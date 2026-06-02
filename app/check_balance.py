"""DeepSeek API 余额查询 + 守门。

设计目标：在流水线开跑前先查余额，< 阈值（默认 ¥5）直接 sys.exit(1) 让 workflow 红叉。

历史背景：
- 2026-05-24 余额耗光后，所有 LLM 调用 402，但 score.py / fetch.py 的 try/except
  把异常吞了，流水线"绿绿地"跑完，commit 一份内容空的 today.json。
- 这种静默失败让用户 20 天才发现网页空白。
- 这个守门是「先失败响应，再容错」原则的直接体现：余额低于阈值就直接红叉，
  workflow 显著告警，用户当天就能察觉。

DeepSeek 余额接口：GET /user/balance
返回示例：
{
    "is_available": true,
    "balance_infos": [
        {"currency": "CNY", "total_balance": "12.34", "granted_balance": "0.00",
         "topped_up_balance": "12.34"}
    ]
}

用法：
    python -m app.check_balance                # 默认阈值 ¥5
    python -m app.check_balance --threshold 10 # 自定义阈值
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

# 自动加载 .env（命令行直跑时也能拿到 LLM_API_KEY）
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
from app.llm_client import load_env  # noqa: E402

load_env(PROJECT_ROOT / ".env")


def fetch_balance(api_key: str, base_url: str, timeout_sec: int = 15) -> tuple[float, str]:
    """查询余额，返回 (CNY 余额浮点数, 原始 JSON 字符串)。失败抛 RuntimeError。

    base_url 应不带末尾斜杠，例如 https://api.deepseek.com/v1
    余额接口实际路径是 /user/balance，注意它**不**在 /v1 下：
        正确：https://api.deepseek.com/user/balance
        错误：https://api.deepseek.com/v1/user/balance
    所以这里要把 /v1 剥掉。
    """
    base = base_url.rstrip("/")
    if base.endswith("/v1"):
        base = base[:-3]
    url = f"{base}/user/balance"

    req = urllib.request.Request(
        url,
        method="GET",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
            body = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        err_body = ""
        try:
            err_body = e.read().decode("utf-8", errors="replace")[:400]
        except Exception:
            pass
        raise RuntimeError(f"HTTP {e.code}: {err_body}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"Network error: {e.reason}") from e

    try:
        obj = json.loads(body)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"响应不是 JSON: {body[:400]}") from e

    # 解析余额（CNY 优先；找不到 CNY 取第一条）
    infos = obj.get("balance_infos") or []
    if not infos:
        raise RuntimeError(f"响应无 balance_infos: {body[:400]}")
    cny_info = next((i for i in infos if i.get("currency") == "CNY"), infos[0])
    raw_balance = cny_info.get("total_balance") or "0"
    try:
        balance = float(raw_balance)
    except (TypeError, ValueError):
        balance = 0.0

    return balance, body


def check_or_exit(threshold: float = 5.0) -> float:
    """流水线开头调一次。余额 < threshold 直接 sys.exit(1)，否则返回当前余额。

    被多次调用安全（无副作用）。环境变量缺失会打印诊断信息再退出。
    """
    api_key = os.environ.get("LLM_API_KEY", "")
    if not api_key:
        print("[balance] ⚠️ 未配置 LLM_API_KEY，跳过余额检查")
        return -1.0

    base_url = os.environ.get("LLM_BASE_URL", "https://api.deepseek.com/v1")
    # 只对 DeepSeek 端点做余额检查，其他厂商暂不支持（也不阻塞）
    if "deepseek.com" not in base_url:
        print(f"[balance] 当前 base_url={base_url} 非 DeepSeek，跳过余额检查")
        return -1.0

    print(f"[balance] 查询 DeepSeek 余额（阈值 ¥{threshold}）...")
    try:
        balance, _ = fetch_balance(api_key, base_url)
    except Exception as e:
        # 查询失败本身不阻塞流水线（DeepSeek 偶尔抽风），但要打印警告
        print(f"[balance] ⚠️ 查询失败（流水线继续，但要警惕）：{e}")
        return -1.0

    print(f"[balance] 当前余额 ¥{balance:.2f}")

    if balance < threshold:
        print(f"\n❌ 余额 ¥{balance:.2f} < 阈值 ¥{threshold:.2f}")
        print("❌ 拒绝运行流水线，避免静默 402 失败再次发生")
        print("❌ 请前往 https://platform.deepseek.com 充值后重试")
        sys.exit(1)

    return balance


def main() -> int:
    parser = argparse.ArgumentParser(description="DeepSeek 余额检查")
    parser.add_argument("--threshold", type=float, default=5.0,
                        help="低于此值直接 fail（默认 ¥5）")
    args = parser.parse_args()

    check_or_exit(threshold=args.threshold)
    return 0


if __name__ == "__main__":
    sys.exit(main())
