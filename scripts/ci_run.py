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
    parser.add_argument("--rescore-all", action="store_true",
                        help="重新评分全部文章（升级 prompt / 修 bug 时用）")
    args = parser.parse_args()

    # ---- 校验环境变量 ----
    if not args.use_mock and not os.environ.get("LLM_API_KEY"):
        print("❌ 未配置 LLM_API_KEY 环境变量（请检查 GitHub Secrets）")
        return 1

    # ---- 0. 余额预检（真数据流水线开跑前）----
    # 历史教训：2026-05-24 余额耗光后，所有 LLM 调用 402，try/except 静默吞了 20 天
    # 现在：余额 < ¥5 直接 sys.exit(1)，让 workflow 红叉，避免静默
    if not args.use_mock:
        _step("Step 0 · DeepSeek 余额预检")
        try:
            from app import check_balance
            check_balance.check_or_exit(threshold=5.0)
        except SystemExit:
            raise  # 余额不足时 check_or_exit 会 sys.exit(1)，让它继续退出
        except Exception as e:
            print(f"[ci] ⚠️ 余额预检失败（流水线继续，但要警惕）：{e}")

    # ---- 1. 初始化 DB（幂等）----
    _step("Step 1 · 初始化数据库")
    init_db.init()

    # ---- 1.5 清掉可能残留的 mock 数据（首次部署后的一次性清理）----
    if not args.use_mock:
        _step("Step 1.5 · 清除 mock 残留")
        from scripts import purge_mock
        purge_mock.purge()

        # ---- 1.6 mock 残留保险丝 ----
        # 特征：真实 LLM 评分 reason 必然带 "A1=" 公式；mock 的 reason 是人工拼的
        # 标签格式 "[官方文件] 源 · 阶段 · 关切"。purge_mock 跑完后还能检测到 →
        # 说明存在未知污染路径，立即停流水线，避免 mock 污染 today.json。
        import sqlite3
        with sqlite3.connect(DB_PATH) as conn:
            rows = conn.execute(
                "SELECT id, source_name, title, reason FROM articles "
                "WHERE reason LIKE '[%' AND reason NOT LIKE '%A1=%' LIMIT 10"
            ).fetchall()
        if rows:
            print("\n❌ 检测到 mock / 僵尸数据残留（purge_mock 未能清除）：")
            for (aid, src, title, reason) in rows:
                print(f"   id={aid} src={src}")
                print(f"     title : {(title or '')[:80]}")
                print(f"     reason: {(reason or '')[:100]}")
            print("\n提示：扩展 scripts/purge_mock.py 的匹配规则，或检查是否有")
            print("      额外脚本 / 迁移把 mock 写回了 articles 表。")
            raise RuntimeError("mock data detected after purge — pipeline aborted")
        print("[ci] ✅ mock 残留体检通过")

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
    if args.use_mock:
        scorer = MockScorer()
    else:
        # 智能双评（v1.6）：通过 LLM_DOUBLE_PASS_THRESHOLD env 调，默认 7
        # 旧 LLM_DOUBLE_PASS=0/1 也兼容
        thr_env = os.environ.get("LLM_DOUBLE_PASS_THRESHOLD")
        if thr_env is not None:
            try:
                threshold = int(thr_env)
            except ValueError:
                threshold = 7
        else:
            old_dp = os.environ.get("LLM_DOUBLE_PASS")
            if old_dp == "0":
                threshold = 11
            elif old_dp == "1":
                threshold = 0
            else:
                threshold = 7
        scorer = OpenAICompatScorer(double_pass_threshold=threshold)
        if threshold >= 11:
            dp_flag = "全部单评"
        elif threshold <= 0:
            dp_flag = "全部双评"
        else:
            dp_flag = f"智能双评（≥{threshold} 分二评）"
        print(f"[ci] scorer 模式：{dp_flag} · model={scorer.cfg.model}")

    concurrency_n = int(os.environ.get("LLM_CONCURRENCY", "4")) if not args.use_mock else 1

    # 注意：这里不再 try/except 静默吞异常（历史教训）
    # score_run 内部如果失败率 ≥50% 会主动 raise，让 workflow 红叉
    # 单条文章失败仍由 score_run 内部记录到 errors 计数，不会拖垮整批
    score_run(scorer, rescore_all=args.rescore_all, concurrency=concurrency_n)

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

    # ---- 6. 写 last_run.json（前端用来检测"是否漏跑"）----
    # mock 模式（push 触发的冒烟测试）不写，否则会让前端误以为线上是新鲜数据
    if not args.use_mock:
        _step("Step 6 · 更新 last_run.json")
        import json
        from datetime import datetime, timezone
        last_run_path = PROJECT_ROOT / "data" / "last_run.json"
        # 顺手统计本次跑的产物，便于前端展示 / 排障
        articles_24h = 0
        latest_pub = None  # v1.6: 库里最新文章的 published_at（前端真实新鲜度判断用）
        latest_scored_pub = None
        try:
            with sqlite3.connect(DB_PATH) as conn:
                articles_24h = conn.execute(
                    "SELECT COUNT(*) FROM articles "
                    "WHERE fetched_at >= datetime('now', '-24 hours')"
                ).fetchone()[0]
                latest_pub = conn.execute(
                    "SELECT MAX(published_at) FROM articles"
                ).fetchone()[0]
                # 进一步：库里最新「已评分」文章 → 这才是前端真正能看到的最新数据
                latest_scored_pub = conn.execute(
                    "SELECT MAX(published_at) FROM articles "
                    "WHERE total_score IS NOT NULL"
                ).fetchone()[0]
        except Exception:
            pass
        last_run_data = {
            "last_run_at": datetime.now(timezone.utc).isoformat(),
            "hours_window": args.hours,
            "articles_fetched_24h": articles_24h,
            "rescored_all": bool(args.rescore_all),
            # v1.6: 前端用 latest_article_published_at 判断「数据陈旧」而非 last_run_at
            # 历史教训：last_run_at 只反映"流水线跑过"，但 LLM 没钱时也会更新这个时间，
            # 让"18h 红条告警"永远不弹。改用 latest_article_published_at（已评分）就准了
            "latest_article_published_at": latest_scored_pub or latest_pub,
        }
        last_run_path.write_text(
            json.dumps(last_run_data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"[ci] last_run.json 已更新: {last_run_data['last_run_at']}")
        print(f"[ci]   latest_article_published_at: {last_run_data['latest_article_published_at']}")

    # ---- 7. 成本汇总（每次跑都打印，无论 mock 还是真数据）----
    # 注：旧版 v1.6 这里曾有"质量 A/B 验证"步骤——
    # 6/1 单天烧 ¥52（reasoner 推理模型思考链超长，单次成本被低估 100 倍），
    # 已彻底移除该步骤。如未来想重启 chat-vs-pro 对照，必须先解决 reasoner 输出截断问题。
    _step("Step 7 · 成本汇总")
    try:
        from app.cost_meter import meter
        meter.print_summary()
        if not args.use_mock:
            meter.write_jsonl(PROJECT_ROOT / "data" / "cost_log.jsonl")
    except Exception as e:  # noqa
        print(f"[ci] ⚠️ 成本汇总失败（不影响主流程）：{e}")

    print("\n" + "=" * 72)
    print("  ✅ 流水线完成")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    sys.exit(main())
