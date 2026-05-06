"""把 mock_articles.json 灌进 SQLite。

仅写入「来源信息」，不计算评分（评分由 score.py 负责）。
重复运行幂等：URL 重复时跳过（schema 里 url UNIQUE）。

用法：
    python3 -m app.fetch_mock
"""
import json
import sqlite3

from app.config import DB_PATH, MOCK_PATH, SOURCE_AUTHORITY


def load_mock() -> list[dict]:
    with MOCK_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


def fetch() -> None:
    articles = load_mock()
    inserted = 0
    skipped = 0
    unknown_sources: set[str] = set()

    with sqlite3.connect(DB_PATH) as conn:
        for art in articles:
            src = art["source_name"]
            tier = SOURCE_AUTHORITY.get(src)
            if tier is None:
                unknown_sources.add(src)
                # 找不到映射的信源默认归 D 级，避免阻塞流水线
                tier = "D"

            try:
                conn.execute(
                    """
                    INSERT INTO articles
                        (url, title, summary, source_name, source_tier, published_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        art["url"],
                        art["title"],
                        art.get("summary", ""),
                        src,
                        tier,
                        art["published_at"],
                    ),
                )
                inserted += 1
            except sqlite3.IntegrityError:
                # URL 已存在 → 跳过
                skipped += 1

        conn.commit()

    print(f"[fetch_mock] inserted={inserted}, skipped(dup)={skipped}, total_in_file={len(articles)}")
    if unknown_sources:
        print(f"[fetch_mock] WARN: 这些信源没在 SOURCE_AUTHORITY 里，临时归 D 级 → {unknown_sources}")


if __name__ == "__main__":
    fetch()
