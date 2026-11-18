"""SQLite 连接与建表。

为什么是 SQLite 而不是 MySQL：这是单人工作台，读多写少、数据全在一台机器上。
SQLite 的 WAL 模式足以支撑「写同步任务 + 读 API」并发，省掉一整套服务运维。
真正需要换库时，替换本模块的 `connect()` 与 repo 里的几条 SQL 即可，
engine 层看到的是 dict/list，不感知存储形态。
"""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from .schema import DDL, SCHEMA_VERSION

# 默认落在仓库根的 data/ 下，可用 AF_DB 覆盖
_DEFAULT_DB = Path(__file__).resolve().parents[3] / "data" / "alphaforge.db"


def db_path() -> Path:
    raw = os.environ.get("AF_DB", "").strip()
    return Path(raw) if raw else _DEFAULT_DB


def _configure(conn: sqlite3.Connection) -> None:
    conn.row_factory = sqlite3.Row
    # WAL：同步任务写入时不阻塞 API 读；NORMAL 在 WAL 下已足够安全
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA temp_store=MEMORY")
    conn.execute("PRAGMA cache_size=-64000")     # 64MB 页缓存
    conn.execute("PRAGMA mmap_size=268435456")   # 256MB 内存映射，全表扫更快


def connect(path: Path | None = None, *, create: bool = True) -> sqlite3.Connection:
    p = path or db_path()
    if create:
        p.parent.mkdir(parents=True, exist_ok=True)
    elif not p.exists():
        raise FileNotFoundError(f"数据库不存在：{p}")
    conn = sqlite3.connect(str(p), timeout=30, isolation_level=None)
    _configure(conn)
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    for stmt in DDL:
        conn.execute(stmt)
    conn.execute(
        "INSERT INTO meta(key, value) VALUES('schema_version', ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (str(SCHEMA_VERSION),),
    )


@contextmanager
def session(path: Path | None = None):
    """一次操作一个连接。读路径用它，短事务，不用连接池。"""
    conn = connect(path)
    try:
        yield conn
    finally:
        conn.close()


def table_counts(conn: sqlite3.Connection) -> dict[str, int]:
    out: dict[str, int] = {}
    for table in ("symbol", "daily", "flow", "snapshot", "index_daily", "index_member", "factor"):
        try:
            out[table] = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        except sqlite3.OperationalError:
            out[table] = 0
    return out


def get_meta(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row[0] if row else None


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO meta(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )
