"""议题聚类 + D 维度回填。

实现 scoring_spec_v1.md §4.1（v1.0）+ §4.3（v1.1 patch）+ fingerprint 归并：

0. **fingerprint 归并**（v1.1 增量）：LLM 给的 fingerprint 同议题不同角度可能跑偏
   （如 EU-GPAI-CodeOfPractice vs EU-GPAI-Code-Critique），先做 token 归一化 + Jaccard
   相似度归并，把跑偏的指纹改写到组内规范值。
1. 按归并后的 fingerprint 聚类（已被 veto 的文章不参与）
2. 选主条：fact_* 中评分最高 1 条；该议题完全无 fact 才退而取 opinion 最高分
3. 回填 D 维度：D = min(N×3, 15)，N = 同 fingerprint 文章总数
4. 重新计算 total_score（D 改了之后）
5. clusters 表写入 (fingerprint, main_article_id, main_is_fact, article_count)
"""
from __future__ import annotations

import sqlite3

from app.config import DB_PATH
from app.fingerprint_merge import merge_fingerprints

FACT_TYPES = ("fact_legislative", "fact_enforcement", "fact_official_doc")


def _normalize_fingerprints(conn: sqlite3.Connection) -> int:
    """把跑偏的 fingerprint 归并到组内规范值。

    Returns:
        被归并改写的文章数（不含 fingerprint 没动的）。
    """
    rows = conn.execute(
        "SELECT id, fingerprint FROM articles WHERE veto IS NULL AND fingerprint IS NOT NULL"
    ).fetchall()
    if not rows:
        return 0

    fps = [r[1] for r in rows]
    canonical_map = merge_fingerprints(fps)

    rewritten = 0
    log_lines: list[str] = []
    for art_id, fp in rows:
        canonical = canonical_map.get(fp, fp)
        if canonical != fp:
            conn.execute(
                "UPDATE articles SET fingerprint = ? WHERE id = ?",
                (canonical, art_id),
            )
            rewritten += 1
            log_lines.append(f"  - id={art_id}: {fp} → {canonical}")

    if log_lines:
        print(f"[cluster] fingerprint 归并 {rewritten} 篇：")
        for line in log_lines:
            print(line)
    else:
        print("[cluster] fingerprint 归并：0 篇（所有 LLM 输出已对齐）")

    return rewritten


def cluster() -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row

        # 0. fingerprint 归并（先跑，让后面的桶分对）
        _normalize_fingerprints(conn)

        # 清空旧聚类（每次跑都重算，幂等）
        conn.execute("DELETE FROM clusters")

        # 拿所有"未被 veto"且有 fingerprint 的文章
        rows = conn.execute(
            """
            SELECT id, fingerprint, content_type, total_score
            FROM articles
            WHERE veto IS NULL AND fingerprint IS NOT NULL
            """
        ).fetchall()

        # 按 fingerprint 分桶
        buckets: dict[str, list[sqlite3.Row]] = {}
        for r in rows:
            buckets.setdefault(r["fingerprint"], []).append(r)

        cluster_count = 0
        multi_count = 0
        d_updates = 0

        for fp, articles in buckets.items():
            n = len(articles)

            # 1. 选主条
            facts = [a for a in articles if a["content_type"] in FACT_TYPES]
            if facts:
                main = max(facts, key=lambda a: a["total_score"] or 0)
                main_is_fact = 1
            else:
                main = max(articles, key=lambda a: a["total_score"] or 0)
                main_is_fact = 0

            conn.execute(
                """
                INSERT INTO clusters
                    (fingerprint, main_article_id, main_is_fact, article_count)
                VALUES (?, ?, ?, ?)
                """,
                (fp, main["id"], main_is_fact, n),
            )
            cluster_count += 1
            if n > 1:
                multi_count += 1

            # 2. 回填 D 维度（同议题所有文章）
            d_score = min(n * 3, 15)
            for a in articles:
                # 重算 total
                row = conn.execute(
                    "SELECT score_a, score_b, score_c, score_e, score_f, veto FROM articles WHERE id = ?",
                    (a["id"],),
                ).fetchone()
                if row["veto"]:
                    new_total = 0
                else:
                    new_total = (
                        (row["score_a"] or 0)
                        + (row["score_b"] or 0)
                        + (row["score_c"] or 0)
                        + d_score
                        + (row["score_e"] or 0)
                        + (row["score_f"] or 0)
                    )
                conn.execute(
                    "UPDATE articles SET score_d = ?, total_score = ? WHERE id = ?",
                    (d_score, new_total, a["id"]),
                )
                d_updates += 1

        conn.commit()

    print(
        f"[cluster] clusters={cluster_count}, multi-article clusters={multi_count}, "
        f"D 维度回填={d_updates} 篇"
    )


if __name__ == "__main__":
    cluster()
