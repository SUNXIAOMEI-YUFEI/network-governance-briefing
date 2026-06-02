"""chat-vs-pro 质量 A/B 验证模块（v1.6，2026-05-31）。

设计目的
========
2026-05-22 GitHub Secrets 里的 LLM_MODEL 被偷偷设成 deepseek-v4-pro（推理模型，
比 chat 贵 3 倍），10 天烧掉 ¥91。修复时把 LLM_MODEL 锁回 deepseek-chat，但用户
担心评分质量下降。这个模块就是来回答「chat 模型够不够用」这个问题——用数据，
不靠拍脑袋。

工作机制
========
1. 流水线每天跑完 score 阶段后，从「今日新评分文章」随机抽 30 篇
2. 把这 30 篇用 chat 和 pro 各跑一次单评（不写库，只记日志）
3. 写到 data/quality_ab/YYYYMMDD.jsonl，每行一篇
4. 用户跑 `python -m scripts.quality_report` 看汇总

启用窗口
========
- 默认启用 7 天（env `AB_VALIDATION_DAYS`）
- 首次跑时在 data/.ab_started_at 记录起始日，过 7 天自动 noop
- 用户随时可设 AB_VALIDATION_DAYS=0 立刻关闭

成本控制（⚠️ 2026-06-02 紧急修订）
==================================
原预估（错误，已废弃）：
- pro ≈ ¥0.013 × 30 = ¥0.39/天
- 总 ≈ ¥0.52/天 × 7 天 ≈ ¥3.6

实测真实成本（2026-06-02 事故后）：
- deepseek-reasoner 是**推理模型**，单次输出包含巨长的"思考链"
  （reasoning_content 字段，30,000-60,000 tokens，是 chat 的 100-200 倍）
- 实测单次 reasoner 调用 ≈ ¥0.30-0.50（按 ¥6/1M output 单价）
- 30 篇 × ¥0.40 = **¥12/天**，是原预估的 30 倍
- 6/1 当天因流水线被重复触发 4 次 + AB 首次启用：单天烧 ¥52.71

紧急关闭措施
===========
- daily.yml 里 AB_VALIDATION_DAYS 已改为 "0"，立刻 noop
- 如未来要重启 A/B 验证，必须：
  1. 在调用 reasoner 前 max_tokens=2000 截断（chat_completion 已支持）
  2. 但截断会破坏 reasoner 的推理质量，可能让 A/B 比较失真
  3. 或换用更便宜的 deepseek-chat-v3.2（同档位但非推理模型）做对照
  4. 实测 1 天再决定要不要扩大窗口

抽样稳定性
==========
用 `random.Random(YYYYMMDD)` 做 seeded random，同一天多次跑流水线（cron-job.org
可能误触发跑两次）会抽到同一组样本，避免双倍烧钱。
"""
from __future__ import annotations

import json
import os
import random
import sqlite3
from datetime import datetime, date, timezone
from pathlib import Path

from app.config import DATA_DIR, DB_PATH


AB_DIR = DATA_DIR / "quality_ab"
AB_STARTED_MARKER = DATA_DIR / ".ab_started_at"

# 这里硬编码 pro 模型名（需要 deepseek 后台支持的模型 id）
# 如果未来 DeepSeek 改名，改这里即可
PRO_MODEL_NAME = "deepseek-reasoner"
CHAT_MODEL_NAME = "deepseek-chat"


def _today_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d")


def _read_started_date() -> date | None:
    """读 .ab_started_at；不存在 / 不可解析返回 None。"""
    if not AB_STARTED_MARKER.exists():
        return None
    try:
        s = AB_STARTED_MARKER.read_text(encoding="utf-8").strip()
        return datetime.strptime(s, "%Y-%m-%d").date()
    except Exception:
        return None


def _write_started_date(d: date) -> None:
    try:
        AB_STARTED_MARKER.parent.mkdir(parents=True, exist_ok=True)
        AB_STARTED_MARKER.write_text(d.strftime("%Y-%m-%d"), encoding="utf-8")
    except Exception as e:  # noqa
        print(f"[ab] ⚠️ 写 .ab_started_at 失败（不影响主流程）：{e}")


def is_validation_active() -> tuple[bool, str]:
    """检查 A/B 验证是否在启用窗口内。

    返回 (is_active, reason)。reason 是给日志用的人话说明。
    """
    days_str = os.environ.get("AB_VALIDATION_DAYS", "7").strip()
    try:
        days = int(days_str)
    except ValueError:
        days = 7
    if days <= 0:
        return False, f"AB_VALIDATION_DAYS={days} 已关闭"

    started = _read_started_date()
    today = datetime.now(timezone.utc).date()
    if started is None:
        # 首次跑，记下起始日，本次启用
        _write_started_date(today)
        return True, f"首次启用 A/B（窗口 {days} 天，从 {today} 起）"

    elapsed = (today - started).days
    if elapsed >= days:
        return False, f"A/B 已过期（启用 {elapsed} 天 ≥ 配置 {days} 天）"
    return True, f"A/B 启用中（已过 {elapsed}/{days} 天）"


def _pick_today_article_ids(sample_size: int = 30) -> list[int]:
    """从今天新评分的文章里随机抽 sample_size 篇。

    "今天" 定义：score_d 仍可能是 0（聚类后回填），所以用 fetched_at 而非 published_at；
    且必须 total_score IS NOT NULL（已经被 score 阶段处理过）。
    """
    seed = _today_str()
    rng = random.Random(seed)

    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute(
            "SELECT id FROM articles "
            "WHERE total_score IS NOT NULL "
            "  AND fetched_at >= datetime('now', '-30 hours') "
            "ORDER BY id"
        ).fetchall()
    ids = [r[0] for r in rows]
    if len(ids) <= sample_size:
        return ids
    return rng.sample(ids, sample_size)


def _load_article(conn: sqlite3.Connection, article_id: int):
    """读一篇文章的核心字段，用于走 _single_call。"""
    from app.score import Article
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT id, url, title, summary, source_name, source_tier, published_at "
        "FROM articles WHERE id = ?",
        (article_id,),
    ).fetchone()
    if not row:
        return None
    return Article(
        id=row["id"],
        url=row["url"],
        title=row["title"] or "",
        summary=row["summary"] or "",
        source_name=row["source_name"] or "",
        source_tier=row["source_tier"] or "C",
        published_at=row["published_at"] or "",
    )


def run_validation(sample_size: int = 30) -> dict:
    """跑一次 A/B 验证。返回简要 stat dict（供 ci_run 打印）。

    如果 A/B 不在启用窗口内，直接 noop 返回 `{"skipped": True, "reason": ...}`。
    任何失败都 swallow（不影响主流程），但会在返回 dict 里报错。
    """
    active, reason = is_validation_active()
    print(f"[ab] {reason}")
    if not active:
        return {"skipped": True, "reason": reason}

    try:
        from app.score import OpenAICompatScorer
        scorer = OpenAICompatScorer(double_pass_threshold=11)  # 强制单评（A/B 不需要双评）
    except Exception as e:
        return {"skipped": True, "reason": f"无法创建 scorer：{e}"}

    article_ids = _pick_today_article_ids(sample_size=sample_size)
    if not article_ids:
        print("[ab] 今日无新评分文章，跳过 A/B")
        return {"skipped": True, "reason": "no new scored articles today"}

    print(f"[ab] 抽样 {len(article_ids)} 篇，用 chat 和 pro 各评一次（不写库）")

    out_path = AB_DIR / f"{_today_str()}.jsonl"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    chat_scores: list[int] = []
    pro_scores: list[int] = []
    diffs: list[int] = []
    success = 0
    failed = 0

    with sqlite3.connect(DB_PATH) as conn:
        for aid in article_ids:
            article = _load_article(conn, aid)
            if not article:
                continue

            chat_total = None
            pro_total = None
            chat_dim = None
            pro_dim = None
            err: list[str] = []

            # chat 评分
            try:
                r_chat = scorer._single_call(
                    article, temperature=0.0,
                    stage="ab_validation_chat",
                    model_override=CHAT_MODEL_NAME,
                )
                chat_total = (0 if r_chat.veto
                              else r_chat.score_a + r_chat.score_b
                                   + r_chat.score_e + r_chat.score_f)
                chat_dim = {
                    "A": r_chat.score_a, "B": r_chat.score_b,
                    "E": r_chat.score_e, "F": r_chat.score_f,
                    "veto": r_chat.veto,
                }
            except Exception as e:  # noqa
                err.append(f"chat: {e}")

            # pro 评分
            try:
                r_pro = scorer._single_call(
                    article, temperature=0.0,
                    stage="ab_validation_pro",
                    model_override=PRO_MODEL_NAME,
                )
                pro_total = (0 if r_pro.veto
                             else r_pro.score_a + r_pro.score_b
                                  + r_pro.score_e + r_pro.score_f)
                pro_dim = {
                    "A": r_pro.score_a, "B": r_pro.score_b,
                    "E": r_pro.score_e, "F": r_pro.score_f,
                    "veto": r_pro.veto,
                }
            except Exception as e:  # noqa
                err.append(f"pro: {e}")

            if chat_total is not None and pro_total is not None:
                success += 1
                chat_scores.append(chat_total)
                pro_scores.append(pro_total)
                diffs.append(abs(chat_total - pro_total))
            else:
                failed += 1

            entry = {
                "ts": datetime.now(timezone.utc).isoformat(),
                "article_id": aid,
                "title": article.title[:120],
                "source": article.source_name,
                "chat_score": chat_total,
                "chat_dim": chat_dim,
                "pro_score": pro_total,
                "pro_dim": pro_dim,
                "score_diff": (
                    abs(chat_total - pro_total)
                    if chat_total is not None and pro_total is not None else None
                ),
                "errors": err or None,
            }
            try:
                with out_path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            except Exception as e:  # noqa
                print(f"[ab] ⚠️ 写 {out_path.name} 失败：{e}")

    stat = {
        "skipped": False,
        "sampled": len(article_ids),
        "success": success,
        "failed": failed,
        "chat_avg": round(sum(chat_scores) / len(chat_scores), 2) if chat_scores else None,
        "pro_avg": round(sum(pro_scores) / len(pro_scores), 2) if pro_scores else None,
        "diff_avg": round(sum(diffs) / len(diffs), 2) if diffs else None,
        "log_file": str(out_path),
    }
    print(
        f"[ab] 完成 · success={success} failed={failed} "
        f"chat_avg={stat['chat_avg']} pro_avg={stat['pro_avg']} "
        f"diff_avg={stat['diff_avg']}"
    )
    return stat


def run_validation_safe(sample_size: int = 30) -> dict:
    """run_validation 的异常吞咽包装。给 ci_run 用，确保 A/B 不能拖垮主流程。"""
    try:
        return run_validation(sample_size=sample_size)
    except Exception as e:  # noqa
        import traceback
        print(f"[ab] ⚠️ A/B 验证遇到异常（不影响主流程）：{e}")
        traceback.print_exc()
        return {"skipped": True, "reason": f"unexpected: {e}"}
