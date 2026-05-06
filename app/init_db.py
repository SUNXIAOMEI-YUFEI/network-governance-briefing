"""初始化 SQLite 数据库（运行一次即可，重复运行幂等）。

用法：
    python3 -m app.init_db
"""
import sqlite3
from app.config import DB_PATH, SCHEMA_PATH


def init() -> None:
    schema_sql = SCHEMA_PATH.read_text(encoding="utf-8")
    with sqlite3.connect(DB_PATH) as conn:
        conn.executescript(schema_sql)
        conn.commit()
    print(f"[init_db] schema applied → {DB_PATH}")
    # 列出表
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
    print(f"[init_db] tables: {[r[0] for r in rows]}")


if __name__ == "__main__":
    init()
