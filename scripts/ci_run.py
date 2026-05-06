"""CI 环境下的每日流水线：fetch → score → cluster → build_today。

在 GitHub Actions 里跑，依赖以下环境变量（走 GitHub Secrets 注入）：
    LLM_API_KEY       必需（DeepSeek / OpenRouter 等）
    LLM_BASE_URL      可选，默认 https://api.deepseek.com/v1
    LLM_MODEL         可选，默认 deepseek-chat
    LLM_CONCURRENCY   可选，默认 4

用法：
    python3 -m scripts.ci_run           # 默认抓过去 24h
    python3 -m scripts.ci_run --hours 72

输出：
    data/briefing.db    SQLite（如果要做历史回看，push 到 git；也可以 gitignore）
    data/today.json     今日产物（必须 push 到 git，前端读这个）
    data/archive/...    历史快照
"""
from __future__ import annotations

import argparse
import os
import sys
import traceback
from pathlib import Path

# 让模块路径工作
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app import init_db  # noqa: E402
from app.config import DB_PATH  # noqa: E402


def _step(title: str) -> None:
    print("\n" + "=" * 72)
    print(f"  {title}")
    print("=" * 72)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hours", type=int, default=24,
                        help="抓取窗口（小时），默认 24")
    parser.add_argument("--skip-fetch", action="store_true",
                        help="调试用：跳过抓取，只重跑 score+cluster+build")
    parser.add_argument("--use-mock", action="store_true",
                        help="用 mock 数据（CI 冒烟测试用）")
    args = parser.parse_args()

    # ---- 校验环境变量 ----
    if not args.use_mock and not os.environ.get("LLM_API_KEY"):
        print("❌ 未配置 LLM_API_KEY 环境变量（请检查 GitHub Secrets）")
        return 1

    # ---- 1. 初始化 DB（幂等）----
    _step("Step 1 · 初始化数据库")
    init_db.init()

    # ---- 2. 抓取 ----
    if args.skip_fetch:
        print("\n[ci] 跳过 fetch 阶段")
    elif args.use_mock:
        _step("Step 2 · 灌 mock 数据")
        from app import fetch_mock
        fetch_mock.fetch()
    else:
        _step(f"Step 2 · 抓 RSS（过去 {args.hours} 小时）")
        from app import fetch
        try:
            fetch.fetch_all(hours=args.hours)
        except Exception:
            print("[ci] ⚠️ fetch 遇到异常，但流水线继续（可能部分源不可达）")
            traceback.print_exc()

    # ---- 检查库里有多少未评分文章 ----
    import sqlite3
    with sqlite3.connect(DB_PATH) as conn:
        pending = conn.execute(
            "SELECT COUNT(*) FROM articles WHERE total_score IS NULL"
        ).fetchone()[0]
        total = conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
    print(f"\n[ci] articles 表：总 {total} 条 · 待评分 {pending} 条")

    # ---- 3. 评分 ----
    _step("Step 3 · LLM 评分")
    from app.score import MockScorer, OpenAICompatScorer, run as score_run
    scorer = MockScorer() if args.use_mock else OpenAICompatScorer()
    concurrency = getattr(scorer, "cfg", None)
    concurrency_n = int(os.environ.get("LLM_CONCURRENCY", "4")) if not args.use_mock else 1

    try:
        score_run(scorer, rescore_all=False, concurrency=concurrency_n)
    except Exception:
        print("[ci] ⚠️ 评分遇到异常，但流水线继续")
        traceback.print_exc()

    # ---- 4. 聚类 ----
    _step("Step 4 · 议题聚类 + D 维度回填")
    from app import cluster
    cluster.cluster()

    # ---- 5. 出 today.json ----
    _step("Step 5 · 生成 today.json")
    from app import build_today
    payload = build_today.build()
    build_today.write(payload)
    build_today._print_summary(payload)

    print("\n" + "=" * 72)
    print("  ✅ 流水线完成")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    sys.exit(main())
