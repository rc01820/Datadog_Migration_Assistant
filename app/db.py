"""SQLite persistence for migration projects and scan history."""
import json
import os
import sqlite3
import threading
from datetime import datetime, timezone

DB_PATH = os.getenv("DMA_DB_PATH", "/data/dma.sqlite3")
_lock = threading.RLock()


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _conn() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    c = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=30)
    c.row_factory = sqlite3.Row
    return c


def init() -> None:
    with _lock, _conn() as c:
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS projects(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                source_tool TEXT NOT NULL,
                source_config TEXT NOT NULL,
                datadog_config TEXT NOT NULL,
                created_at TEXT, updated_at TEXT);
            CREATE TABLE IF NOT EXISTS scans(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                status TEXT, progress INTEGER DEFAULT 0, message TEXT,
                started_at TEXT, finished_at TEXT,
                summary TEXT, results TEXT, warnings TEXT);
            CREATE INDEX IF NOT EXISTS ix_scans_project ON scans(project_id, id);
            """
        )


def execute(sql: str, params: tuple = ()) -> int:
    with _lock, _conn() as c:
        cur = c.execute(sql, params)
        return cur.lastrowid


def query(sql: str, params: tuple = ()) -> list[dict]:
    with _lock, _conn() as c:
        return [dict(r) for r in c.execute(sql, params).fetchall()]


def one(sql: str, params: tuple = ()) -> dict | None:
    rows = query(sql, params)
    return rows[0] if rows else None


def jload(value, default=None):
    if value in (None, ""):
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default
