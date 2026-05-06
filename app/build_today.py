"""出 today.json：v1.1 双栏布局的最终产物。

输出结构（供前端 v2/index.html 直接消费）：

{
  "snapshot_at": "2026-05-06T11:00:00",
  "tabs": {
    "24h":  {"facts": {"top3":[...], "pool":[...]}, "opinions": {"top3":[...], "pool":[...]}},
    "72h":  {...}, "120h": {...}, "360h": {...}
  },
  "clusters": [
    {"fingerprint": "...", "main": {...article...}, "main_is_fact": true,
     "related_facts": [...], "related_opinions": [...]}
  ],
  "stats": {"total": N, "by_type": {...}, "veto": M}
}
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.config import ARCHIVE_DIR, DB_PATH, TODAY_JSON

FACT_TYPES = ("fact_legislative", "fact_enforcement", "fact_official_doc")
TIME_WINDOWS = {
    "24h": 24,
    "72h": 72,
    "120h": 120,
    "360h": 360,
}
TOP_N = 3
POOL_PER_COLUMN = 8  # 左右各 8 条情报池


def _row_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    # anxiety_hits 是 JSON 字符串，反序列化
    if d.get("anxiety_hits"):
        try:
            d["anxiety_hits"] = json.loads(d["anxiety_hits"])
        except (json.JSONDecodeError, TypeError):
            d["anxiety_hits"] = []
    else:
        d["anxiety_hits"] = []
    return d


def _query_window(
    conn: sqlite3.Connection,
    *,
    since_utc: datetime,
    column_kind: str,
    limit: int,
) -> list[dict]:
    """查某个时间窗 + 某栏（facts/opinions）的文章，按总分降序。"""
    if column_kind == "facts":
        type_clause = f"content_type IN ({','.join(['?'] * len(FACT_TYPES))})"
        params: list = list(FACT_TYPES)
    else:
        type_clause = "content_type = ?"
        params = ["opinion_analysis"]

    sql = f"""
        SELECT id, url, title, title_cn, summary, source_name, source_tier, published_at,
               score_a, score_b, score_c, score_d, score_e, score_f, total_score,
               fingerprint, anxiety_hits, maturity_stage, content_type, reason
        FROM articles
        WHERE veto IS NULL
          AND total_score > 0
          AND published_at >= ?
          AND {type_clause}
        ORDER BY total_score DESC, published_at DESC
        LIMIT ?
    """
    params = [since_utc.isoformat()] + params + [limit]
    rows = conn.execute(sql, params).fetchall()
    return [_row_to_dict(r) for r in rows]


def _build_clusters(conn: sqlite3.Connection) -> list[dict]:
    """整理出多篇文章的聚类视图（单文章议题不展示，节省界面空间）。"""
    cluster_rows = conn.execute(
        """
        SELECT c.fingerprint, c.main_article_id, c.main_is_fact, c.article_count
        FROM clusters c
        WHERE c.article_count > 1
        ORDER BY c.article_count DESC
        """
    ).fetchall()

    out = []
    for cr in cluster_rows:
        fp = cr["fingerprint"]
        main_row = conn.execute(
            """
            SELECT id, url, title, title_cn, summary, source_name, source_tier, published_at,
                   total_score, anxiety_hits, maturity_stage, content_type, reason
            FROM articles WHERE id = ?
            """,
            (cr["main_article_id"],),
        ).fetchone()
        if not main_row:
            continue

        related_rows = conn.execute(
            """
            SELECT id, url, title, title_cn, source_name, source_tier, published_at,
                   total_score, content_type
            FROM articles
            WHERE fingerprint = ? AND id != ? AND veto IS NULL
            ORDER BY
                CASE WHEN content_type LIKE 'fact_%%' THEN 0 ELSE 1 END,
                total_score DESC
            """,
            (fp, cr["main_article_id"]),
        ).fetchall()

        related = [dict(r) for r in related_rows]
        related_facts = [r for r in related if r["content_type"] in FACT_TYPES]
        related_opinions = [r for r in related if r["content_type"] == "opinion_analysis"]

        out.append({
            "fingerprint": fp,
            "article_count": cr["article_count"],
            "main_is_fact": bool(cr["main_is_fact"]),
            "main": _row_to_dict(main_row),
            "related_facts": related_facts,
            "related_opinions": related_opinions,
        })
    return out


def _stats(conn: sqlite3.Connection) -> dict:
    total = conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
    veto_n = conn.execute("SELECT COUNT(*) FROM articles WHERE veto IS NOT NULL").fetchone()[0]
    rows = conn.execute(
        """
        SELECT content_type, COUNT(*) AS n FROM articles WHERE veto IS NULL
        GROUP BY content_type
        """
    ).fetchall()
    by_type = {r["content_type"]: r["n"] for r in rows}
    return {"total": total, "by_type": by_type, "veto": veto_n}


def _feed_health(conn: sqlite3.Connection) -> list[dict]:
    """信源健康度：每个 feed 当前状态 + 近 7 天成功率。

    返回按状态排序（🔴 红的排最上面，方便一眼看到故障源）：
        [
          {"source_name","feed_url","source_tier",
           "last_attempt_at","last_success_at","last_error",
           "last_article_count","consecutive_fails",
           "recent_7d_success","recent_7d_total","status"}
        ]
    status ∈ "ok" | "warn" | "dead" | "unknown"
        dead     : 连续失败 >= 3 次
        warn     : 连续失败 1-2 次 或 近 7 天成功率 < 70%
        ok       : 其余有成功记录
        unknown  : 没有任何历史（新加的 feed，还没被跑过）
    """
    # feed_health 主表不存在的话（老数据库），直接返回空
    exists = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='feed_health'"
    ).fetchone()
    if not exists:
        return []

    from datetime import datetime as _dt, timedelta as _td, timezone as _tz
    cutoff_7d = (_dt.now(_tz.utc) - _td(days=7)).isoformat()

    rows = conn.execute(
        """
        SELECT source_name, feed_url, source_tier,
               last_attempt_at, last_success_at, last_error,
               last_article_count, consecutive_fails
        FROM feed_health
        """
    ).fetchall()

    out: list[dict] = []
    for r in rows:
        d = dict(r)
        # 近 7 天成功次数 / 总次数
        agg = conn.execute(
            """
            SELECT
              SUM(CASE WHEN success=1 THEN 1 ELSE 0 END) AS succ,
              COUNT(*) AS total
            FROM feed_health_log
            WHERE source_name = ? AND attempted_at >= ?
            """,
            (d["source_name"], cutoff_7d),
        ).fetchone()
        succ_7d = int(agg["succ"] or 0)
        total_7d = int(agg["total"] or 0)
        d["recent_7d_success"] = succ_7d
        d["recent_7d_total"] = total_7d

        # 状态判定
        fails = d.get("consecutive_fails") or 0
        has_success_ever = bool(d.get("last_success_at"))
        if fails >= 3:
            status = "dead"
        elif fails >= 1:
            status = "warn"
        elif total_7d > 0 and succ_7d / total_7d < 0.7:
            status = "warn"
        elif has_success_ever:
            status = "ok"
        else:
            status = "unknown"
        d["status"] = status
        out.append(d)

    # 排序：dead > warn > unknown > ok；每组内按 last_attempt_at 倒序
    status_order = {"dead": 0, "warn": 1, "unknown": 2, "ok": 3}
    out.sort(key=lambda x: x.get("last_attempt_at") or "", reverse=True)
    out.sort(key=lambda x: status_order.get(x["status"], 9))
    return out


def build(*, snapshot_now: datetime | None = None) -> dict:
    now = snapshot_now or datetime.now(timezone.utc)

    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row

        tabs: dict[str, dict] = {}
        for tab_name, hours in TIME_WINDOWS.items():
            since = now - timedelta(hours=hours)
            facts_all = _query_window(
                conn, since_utc=since, column_kind="facts", limit=TOP_N + POOL_PER_COLUMN,
            )
            opinions_all = _query_window(
                conn, since_utc=since, column_kind="opinions", limit=TOP_N + POOL_PER_COLUMN,
            )
            tabs[tab_name] = {
                "facts": {
                    "top3": facts_all[:TOP_N],
                    "pool": facts_all[TOP_N:],
                },
                "opinions": {
                    "top3": opinions_all[:TOP_N],
                    "pool": opinions_all[TOP_N:],
                },
            }

        clusters = _build_clusters(conn)
        stats = _stats(conn)
        feed_health = _feed_health(conn)

    payload = {
        "snapshot_at": now.isoformat(),
        "schema_version": "v1.2",
        "tabs": tabs,
        "clusters": clusters,
        "stats": stats,
        "feed_health": feed_health,
    }
    return payload


def write(payload: dict) -> Path:
    TODAY_JSON.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    # 顺手归档一份
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    archive_path = ARCHIVE_DIR / f"{today_str}.json"
    archive_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return TODAY_JSON


def _print_summary(payload: dict) -> None:
    print(f"[build_today] snapshot_at={payload['snapshot_at']}, schema={payload['schema_version']}")
    print(f"[build_today] stats={payload['stats']}")
    for tab, data in payload["tabs"].items():
        f_top = len(data["facts"]["top3"])
        f_pool = len(data["facts"]["pool"])
        o_top = len(data["opinions"]["top3"])
        o_pool = len(data["opinions"]["pool"])
        print(f"  {tab}: facts top={f_top} pool={f_pool} | opinions top={o_top} pool={o_pool}")
    print(f"[build_today] multi-article clusters: {len(payload['clusters'])}")
    fh = payload.get("feed_health") or []
    if fh:
        counts = {"ok": 0, "warn": 0, "dead": 0, "unknown": 0}
        for f in fh:
            counts[f.get("status", "unknown")] = counts.get(f.get("status", "unknown"), 0) + 1
        print(f"[build_today] feed_health: 🟢 ok={counts['ok']} "
              f"🟡 warn={counts['warn']} 🔴 dead={counts['dead']} ❔ unknown={counts['unknown']}")


if __name__ == "__main__":
    payload = build()
    write(payload)
    _print_summary(payload)
    print(f"[build_today] → {TODAY_JSON}")
