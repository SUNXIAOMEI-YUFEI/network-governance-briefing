"""初始化 SQLite 数据库（运行一次即可，重复运行幂等）。

用法：
    python3 -m app.init_db
"""
import sqlite3
from app.config import DB_PATH, SCHEMA_PATH


# 幂等补丁：schema 升级时新加的列，要让老库也能追上
MIGRATIONS: list[tuple[str, str, str]] = [
    # (table, column, column_def_including_type_and_default)
    ("articles", "title_cn",            "TEXT"),
    ("articles", "content_type",        "TEXT NOT NULL DEFAULT 'opinion_analysis'"),
    ("articles", "content_type_reason", "TEXT"),
]


def _apply_migrations(conn: sqlite3.Connection) -> None:
    for table, column, coldef in MIGRATIONS:
        # SQLite 没有 IF NOT EXISTS for ADD COLUMN，自己判断
        cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
        if column not in cols:
            try:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coldef}")
                print(f"[init_db] + migrated: {table}.{column}")
            except sqlite3.OperationalError as e:
                print(f"[init_db] ⚠️ migrate {table}.{column} failed: {e}")


def init() -> None:
    schema_sql = SCHEMA_PATH.read_text(encoding="utf-8")
    with sqlite3.connect(DB_PATH) as conn:
        # 1. 应用完整 schema（幂等：CREATE TABLE IF NOT EXISTS）
        conn.executescript(schema_sql)
        # 2. 对已存在的老库做迁移（加缺失的列）
        _apply_migrations(conn)
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
