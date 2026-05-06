"""用户校准反馈验证脚本：
把用户给出的"目标分"和新版 prompt v1.4 的实际输出对比。

跑完看：
  1. 用户判断为 veto 的样本，新 prompt 是否也 veto
  2. 用户给低分的营销邮件，新 prompt 是否也识别
  3. 用户给高分的核心议题，新 prompt 是否也给高分
"""
from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

env_file = PROJECT_ROOT / ".env"
if env_file.exists():
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

from app.score import Article, OpenAICompatScorer
from app.config import DB_PATH, TIER_TO_SCORE


# 用户手工校准的目标分（从对话中整理）
# 用标题关键词匹配数据库里的文章
USER_CALIBRATION = [
    # (标题匹配关键词, 用户目标分, 备注)
    ("ACSC issues guidance on cybersecurity risks of agentic AI", 83, "Agent 相关，对齐"),
    ("White House Framework Signals", 60, "像是讲座推广"),
    ("Fine-Tuning Foundation Models", 65, "技术偏，监管关心度低"),
    ("Friendly AI chatbots", 72, "算法极化相关，但有点学术"),  # 推测值
    ("C-22", 40, "不知道在讲啥"),
    ("Character.AI", 68, "未成年人，老生常谈"),
    ("FTC", 30, "反垄断非 AI，不想要"),  # 该 veto
    ("Trump", 82, "美国 AI 治理风向"),
    ("CAISI", 87, "NIST + 前沿治理"),
    ("CMA", 0, "和 AI 无关，应 veto"),
    ("surveillance pricing", 0, "和 AI 无关"),
    ("CNIL", 30, "无具体事实"),
    ("satellite", 0, "非网络治理"),
]


def find_article(conn: sqlite3.Connection, keyword: str):
    row = conn.execute(
        """
        SELECT * FROM articles
        WHERE title LIKE ? OR summary LIKE ? OR title_cn LIKE ?
        ORDER BY total_score DESC LIMIT 1
        """,
        (f"%{keyword}%", f"%{keyword}%", f"%{keyword}%"),
    ).fetchone()
    return row


def main() -> None:
    scorer = OpenAICompatScorer(double_pass=True)
    print(f"[check] model={scorer.cfg.model}")
    print(f"{'='*110}")
    print(f"{'文章关键词':30s} | {'用户分':>6} | {'LLM 现分':>8} | {'新 prompt 分':>10} | {'新 veto':>7} | 结论")
    print(f"{'='*110}")

    hits = 0
    total = 0

    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        for keyword, user_score, note in USER_CALIBRATION:
            r = find_article(conn, keyword)
            if not r:
                print(f"{keyword[:30]:30s} | 未找到文章")
                continue

            a = Article(
                id=r["id"], url=r["url"], title=r["title"], summary=r["summary"],
                source_name=r["source_name"], source_tier=r["source_tier"],
                published_at=r["published_at"],
            )
            old_total = r["total_score"]
            old_veto = r["veto"] or "-"

            try:
                result = scorer.score(a)
            except Exception as e:  # noqa
                print(f"{keyword[:30]:30s} | {user_score:>6} | {old_total:>8} | ERROR: {e}")
                continue

            tier_score = TIER_TO_SCORE.get(a.source_tier, 0)
            new_total = 0 if result.veto else (
                result.score_a + result.score_b + tier_score + result.score_e + result.score_f
            )

            # 判断是否符合用户期望
            if user_score == 0:
                # 用户想 veto，新评也应 veto 或很低
                ok = result.veto is not None or new_total < 30
            elif user_score >= 70:
                ok = new_total >= 60
            elif user_score >= 40:
                ok = 35 <= new_total <= 80
            else:
                ok = new_total <= 50

            flag = "✅" if ok else "❌"
            if ok:
                hits += 1
            total += 1

            print(f"{keyword[:30]:30s} | {user_score:>6} | {old_total:>8} | {new_total:>10} | {(result.veto or '-'):>7} | {flag} {note}")

    print(f"{'='*110}")
    print(f"\n总体一致率：{hits}/{total} = {hits/total*100:.0f}%")
    print("（一致 = 新评分方向和用户判断吻合，不要求数值完全一致）")


if __name__ == "__main__":
    main()
