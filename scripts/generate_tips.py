#!/usr/bin/env python3
"""工作台 v1 · 小 tips 生成 CLI 入口（本地调试用）。

用法：
    # 用 reasoner（默认）+ 4 条预设样本 + 一段补充
    python3 scripts/generate_tips.py

    # 指定 article id
    python3 scripts/generate_tips.py --ids 1075 1118 1120 1158

    # 用 chat 快速模式
    python3 scripts/generate_tips.py --model chat

    # 加用户补充
    python3 scripts/generate_tips.py --note "今天讨论提到 GPAI Code 与西班牙立法的协调问题"

    # 不调 Tavily（debug 时省钱）
    python3 scripts/generate_tips.py --no-search

输出：
- 屏幕直接打印生成的洞察
- 写一份 markdown 备份到 data/tips_poc/cli_YYYYMMDD_HHMM.md（含搜索日志）
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.llm_client import load_env  # noqa: E402
from app.tips_generator import (  # noqa: E402
    MODEL_CHOICES,
    generate_tip,
    load_articles_from_db,
)


# 预设样本（与 tips_poc.py 同一组）
DEFAULT_IDS = [1075, 1118, 1120, 1158]

DEFAULT_NOTE = """\
最近一周看下来，欧盟 AI Act 落地的实际节奏明显比纸面要慢——西班牙这次推出本国转化立法，姿态摆得很高，
但和 GPAI Code 的关系怎么协调还没看清楚；同时 FPF 这种智库开始把 PETs（隐私增强技术）和跨境数据流动挂钩，
明显是为下一轮 AI Act + GDPR 双重合规找出路。"""


OUT_DIR = PROJECT_ROOT / "data" / "tips_poc"


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--ids", nargs="+", type=int, default=DEFAULT_IDS,
                   help="勾选的 article id 列表（默认 4 条 PoC 样本）")
    p.add_argument("--note", type=str, default=DEFAULT_NOTE,
                   help="用户补充（默认用 PoC 那段）；传 '' 表示无补充")
    p.add_argument("--model", choices=list(MODEL_CHOICES.keys()), default="reasoner",
                   help="模型选择：reasoner（默认）/ chat（快速模式）")
    p.add_argument("--no-search", action="store_true",
                   help="不调 Tavily（debug 用，省钱）")
    p.add_argument("--no-save", action="store_true",
                   help="不写 markdown 备份")
    args = p.parse_args()

    load_env(PROJECT_ROOT / ".env")

    print("=" * 60)
    print(f"工作台 v1 · 小 tips 生成（CLI）")
    print("=" * 60)
    print(f"  文章 ID：{args.ids}")
    print(f"  模型：{args.model} ({MODEL_CHOICES[args.model]['model']})")
    print(f"  用户补充：{'有' if args.note.strip() else '无'}")
    print(f"  搜索：{'关闭' if args.no_search else '开启（每条调 Tavily）'}")
    print()

    # 1. 加载文章
    articles = load_articles_from_db(args.ids)
    if not articles:
        print(f"❌ 数据库里找不到 ID = {args.ids} 的文章")
        return 1
    print(f"  ✓ 从 DB 加载了 {len(articles)} 条文章：")
    for a in articles:
        print(f"    [{a.id}] {a.source_name}：{a.title[:60]}")
    print()

    # 2. 调生成
    print("  ⏳ 正在生成（搜索 + LLM）...")
    result = generate_tip(
        articles=articles,
        user_note=args.note,
        model_choice=args.model,
        do_search=not args.no_search,
    )

    # 3. 输出
    print("\n" + "=" * 60)
    if result.error:
        print(f"❌ 失败：{result.error}")
        print(f"  耗时：{result.elapsed_s:.1f}s")
        return 1

    print(f"✅ 生成成功（{result.elapsed_s:.1f}s · {result.output_chars} 字）")
    print(f"  模型：{result.model}")
    print(f"  搜索：{result.n_searches}/{result.n_articles} 条成功")
    print(f"  prompt 长度：{result.prompt_chars} 字")
    print("=" * 60)
    print()
    print("--- 生成的洞察 ---")
    print()
    print(result.tip_markdown)
    print()
    print("--- 搜索日志 ---")
    for log in result.search_log:
        flag = "✓" if log["ok"] else "✗"
        if log["ok"]:
            print(f"  {flag} [{log['article_id']}] {log['n_results']} 条结果，{log['elapsed_ms']} ms")
        else:
            print(f"  {flag} [{log['article_id']}] 失败：{log['error']}")

    # 4. 备份 markdown
    if not args.no_save:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).astimezone()
        out_file = OUT_DIR / f"cli_{ts.strftime('%Y%m%d_%H%M')}.md"

        lines = [
            f"# 小 tips · CLI 生成 · {ts.strftime('%Y-%m-%d %H:%M %Z')}\n",
            f"> 模型：`{result.model}`  ·  耗时：{result.elapsed_s:.1f}s  ·  {result.output_chars} 字\n",
            f"> 搜索：{result.n_searches}/{result.n_articles} 条成功\n",
            f"> 用户补充：{'有' if result.user_note_used else '无'}\n",
            "\n## 洞察正文\n",
            result.tip_markdown,
            "\n\n## 喂入的素材（参考）\n",
        ]
        for i, a in enumerate(articles, 1):
            lines.append(f"\n### 素材 {i} · [{a.id}] {a.source_name}\n")
            lines.append(f"- 标题：{a.title}\n")
            lines.append(f"- 链接：{a.url}\n")
        if args.note.strip():
            lines.append(f"\n## 用户补充\n\n{args.note}\n")

        lines.append("\n## 搜索日志\n\n")
        for log in result.search_log:
            flag = "✓" if log["ok"] else "✗"
            lines.append(f"- {flag} [{log['article_id']}] {log['n_results']} 条 · {log['elapsed_ms']}ms"
                        + (f" · {log['error']}" if log.get("error") else "") + "\n")

        out_file.write_text("\n".join(lines), encoding="utf-8")
        print(f"\n💾 已备份到：{out_file.relative_to(PROJECT_ROOT)}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
