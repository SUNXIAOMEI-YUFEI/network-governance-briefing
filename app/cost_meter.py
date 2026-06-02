"""LLM 调用成本计量。

设计目标：
1. **零侵入**：通过模块级单例 `meter` 旁路收集，不破坏 chat_completion 的 str 返回签名
2. **多模型多阶段**：按 (model, stage) 双键累加，方便排查「哪个模型 / 哪个步骤」吃掉钱
3. **线程安全**：score.py 用 ThreadPoolExecutor 并发评分，必须用 Lock 保护累加
4. **持久化简单**：JSONL 追加写入 data/cost_log.jsonl，每天一行，scripts/cost_summary.py 读它做汇总

单价表说明（CNY / 1M tokens）：
- DeepSeek 官方定价（2026 年实测）：
  - deepseek-chat (V3.x)：input cache_miss ¥1，input cache_hit ¥0.5，output ¥2
  - deepseek-v4-pro (推理模型)：input cache_miss ¥3，input cache_hit ¥1.5，output ¥6
  - 其他兜底单价用 chat 的 ×3（保守估算，宁可高估让用户警觉）
- 其他厂商（Claude / GPT-4o）走中转站时按转换汇率折算，用户大概率不会切

历史背景：
- 2026-05-22 ~ 2026-05-31，因 GitHub Secret 误把 LLM_MODEL 设为 deepseek-v4-pro
  + double_pass 默认开 + 一次 rescore_all，10 天烧掉 ¥91（预期 ¥3）
- 这个模块就是从那次事故里长出来的——下次再有任何"突然烧钱"，
  ci_run 跑完会立刻打印「[cost] 本次 ≈ ¥X.XX」让用户当天就能察觉
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path


# ============================================================
# 单价表（CNY / 1 token）
# ============================================================
# 注意：单位是 ¥/token，不是 ¥/1k 或 ¥/1M
# 例如 deepseek-chat output 是 ¥2/1M tokens = ¥0.000002/token
LLM_PRICING: dict[str, dict[str, float]] = {
    # DeepSeek 官方
    "deepseek-chat": {
        "input_cache_miss": 1.0 / 1_000_000,
        "input_cache_hit":  0.5 / 1_000_000,
        "output":           2.0 / 1_000_000,
    },
    "deepseek-v3":  {  # 别名
        "input_cache_miss": 1.0 / 1_000_000,
        "input_cache_hit":  0.5 / 1_000_000,
        "output":           2.0 / 1_000_000,
    },
    "deepseek-v4-flash":  {  # = chat
        "input_cache_miss": 1.0 / 1_000_000,
        "input_cache_hit":  0.5 / 1_000_000,
        "output":           2.0 / 1_000_000,
    },
    "deepseek-reasoner": {
        "input_cache_miss": 3.0 / 1_000_000,
        "input_cache_hit":  1.5 / 1_000_000,
        "output":           6.0 / 1_000_000,
    },
    "deepseek-v4-pro": {
        "input_cache_miss": 3.0 / 1_000_000,
        "input_cache_hit":  1.5 / 1_000_000,
        "output":           6.0 / 1_000_000,
    },
}

# 未知模型兜底（按 deepseek-chat ×3 保守估算）
_FALLBACK_PRICING = {
    "input_cache_miss": 3.0 / 1_000_000,
    "input_cache_hit":  1.5 / 1_000_000,
    "output":           6.0 / 1_000_000,
}


def _price_of(model: str) -> dict[str, float]:
    """按 model 名查单价；找不到走兜底。匹配方式：完全等于 OR model 包含 key（前缀匹配）。"""
    if model in LLM_PRICING:
        return LLM_PRICING[model]
    # 模糊匹配（如 "deepseek-chat-v3.2" 匹配 "deepseek-chat"）
    lower = model.lower()
    for k, v in LLM_PRICING.items():
        if k in lower or lower.startswith(k):
            return v
    return _FALLBACK_PRICING


# ============================================================
# 成本计量器
# ============================================================

class CostMeter:
    """全局成本计量单例。线程安全。

    关键 API：
    - record(model, usage, stage)  每次 chat_completion 拿到 usage 时调一次
    - summary() → dict             汇总当前累加结果
    - write_jsonl(path)            追加一行到 cost_log.jsonl
    - reset()                      单测用 / ci_run 开头清零
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # by_model_stage: {(model, stage): {"calls":..., "input_tok":..., "output_tok":..., "cny":...}}
        self._by_model_stage: dict[tuple[str, str], dict[str, float]] = {}

    def record(self, model: str, usage: dict | None, stage: str = "llm") -> None:
        """记录一次 LLM 调用。

        usage 是 OpenAI 兼容响应里的 `usage` 字段，结构常见两种：
        - 标准：{"prompt_tokens": N, "completion_tokens": N, "total_tokens": N}
        - DeepSeek 扩展：{"prompt_cache_hit_tokens": ..., "prompt_cache_miss_tokens": ...,
                          "prompt_tokens": ..., "completion_tokens": ...}
        没传或字段缺失时按 0 处理（不拖垮主流程）。
        """
        if not usage or not isinstance(usage, dict):
            return

        prompt_tok = int(usage.get("prompt_tokens", 0) or 0)
        completion_tok = int(usage.get("completion_tokens", 0) or 0)

        # DeepSeek 扩展：精确区分 cache hit/miss（如果提供了）
        cache_hit = int(usage.get("prompt_cache_hit_tokens", 0) or 0)
        cache_miss = int(usage.get("prompt_cache_miss_tokens", 0) or 0)
        # 如果厂商没给细分字段，把 prompt_tok 全算 cache_miss（保守估算）
        if cache_hit == 0 and cache_miss == 0:
            cache_miss = prompt_tok

        price = _price_of(model)
        cny = (
            cache_miss * price.get("input_cache_miss", 0)
            + cache_hit * price.get("input_cache_hit", 0)
            + completion_tok * price.get("output", 0)
        )

        key = (model, stage)
        with self._lock:
            entry = self._by_model_stage.setdefault(
                key,
                {"calls": 0, "input_cache_miss_tok": 0, "input_cache_hit_tok": 0,
                 "output_tok": 0, "cny": 0.0},
            )
            entry["calls"] += 1
            entry["input_cache_miss_tok"] += cache_miss
            entry["input_cache_hit_tok"] += cache_hit
            entry["output_tok"] += completion_tok
            entry["cny"] += cny

    def summary(self) -> dict:
        """返回 {"total_cny", "by_model", "by_stage", "by_model_stage"}。"""
        with self._lock:
            total_cny = sum(e["cny"] for e in self._by_model_stage.values())
            total_calls = sum(int(e["calls"]) for e in self._by_model_stage.values())

            by_model: dict[str, dict] = {}
            by_stage: dict[str, dict] = {}
            by_model_stage: dict[str, dict] = {}

            for (model, stage), entry in self._by_model_stage.items():
                # by_model
                m = by_model.setdefault(model, {"calls": 0, "cny": 0.0})
                m["calls"] += int(entry["calls"])
                m["cny"] += entry["cny"]
                # by_stage
                s = by_stage.setdefault(stage, {"calls": 0, "cny": 0.0})
                s["calls"] += int(entry["calls"])
                s["cny"] += entry["cny"]
                # by_model_stage（详细）
                key = f"{model} · {stage}"
                by_model_stage[key] = {
                    "calls": int(entry["calls"]),
                    "input_cache_miss_tok": int(entry["input_cache_miss_tok"]),
                    "input_cache_hit_tok": int(entry["input_cache_hit_tok"]),
                    "output_tok": int(entry["output_tok"]),
                    "cny": round(entry["cny"], 4),
                }

            return {
                "total_cny": round(total_cny, 4),
                "total_calls": total_calls,
                "by_model": {k: {"calls": v["calls"], "cny": round(v["cny"], 4)}
                             for k, v in by_model.items()},
                "by_stage": {k: {"calls": v["calls"], "cny": round(v["cny"], 4)}
                             for k, v in by_stage.items()},
                "by_model_stage": by_model_stage,
            }

    def write_jsonl(self, path: Path) -> None:
        """追加一行到 cost_log.jsonl。失败不抛异常（成本日志不能拖垮主流程）。"""
        try:
            payload = {
                "ts": datetime.now(timezone.utc).isoformat(),
                **self.summary(),
            }
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(payload, ensure_ascii=False) + "\n")
        except Exception as e:  # noqa
            print(f"[cost] ⚠️ 写 cost_log.jsonl 失败（不影响主流程）：{e}")

    def print_summary(self) -> None:
        """打印汇总到 stdout。每次 ci_run 收尾调一次。"""
        s = self.summary()
        if s["total_calls"] == 0:
            print("[cost] 本次未发生 LLM 调用")
            return
        print(f"[cost] 本次共 {s['total_calls']} 次调用 ≈ ¥{s['total_cny']:.4f}")
        if s["by_model"]:
            for m, v in s["by_model"].items():
                print(f"[cost]   按模型: {m:<20s} {v['calls']:>4d} 次 ≈ ¥{v['cny']:.4f}")
        if s["by_stage"]:
            for st, v in s["by_stage"].items():
                print(f"[cost]   按阶段: {st:<20s} {v['calls']:>4d} 次 ≈ ¥{v['cny']:.4f}")

    def reset(self) -> None:
        with self._lock:
            self._by_model_stage.clear()


# 模块级单例
meter = CostMeter()
