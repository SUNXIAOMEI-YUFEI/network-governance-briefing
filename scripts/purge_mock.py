"""一次性清掉库里遗留的 mock 数据。

判断依据：URL 命中 `app/data/mock_articles.json` 里列出的那批虚构 URL。

用法（一般 CI 里自动跑，命令行也能手动跑）：
    python3 -m scripts.purge_mock
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.config import DB_PATH, MOCK_PATH


def purge() -> int:
    if not MOCK_PATH.exists():
        print("[purge_mock] mock_articles.json 不存在，跳过")
        return 0

    with MOCK_PATH.open("r", encoding="utf-8") as f:
        mock_articles = json.load(f)

    mock_urls = [a["url"] for a in mock_articles if "url" in a]
    if not mock_urls:
        print("[purge_mock] mock 列表为空，跳过")
        return 0

    if not DB_PATH.exists():
        print("[purge_mock] 数据库不存在，跳过")
        return 0

    placeholders = ",".join(["?"] * len(mock_urls))

    with sqlite3.connect(DB_PATH) as conn:
        # 先看要删几条
        count = conn.execute(
            f"SELECT COUNT(*) FROM articles WHERE url IN ({placeholders})",
            mock_urls,
        ).fetchone()[0]

        if count == 0:
            print("[purge_mock] 库里无 mock 残留，跳过")
            return 0

        # 删（clusters 表有 ON DELETE CASCADE，会自动清理）
        conn.execute(
            f"DELETE FROM articles WHERE url IN ({placeholders})",
            mock_urls,
        )
        conn.commit()

    print(f"[purge_mock] 清除 mock 残留：{count} 条")
    return count


if __name__ == "__main__":
    purge()
