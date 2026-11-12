"""SQLite 存储层。单用户本地部署，无需外部数据库。"""

from __future__ import annotations

import pathlib
import sqlite3
from datetime import datetime, timezone

DATA_DIR = pathlib.Path(__file__).resolve().parent.parent / "data"
DB_PATH = DATA_DIR / "tether.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS entries (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    emotion                TEXT    NOT NULL,
    severity               INTEGER,
    cause                  TEXT    NOT NULL DEFAULT '',
    note                   TEXT    NOT NULL DEFAULT '',
    occurred_at            TEXT    NOT NULL,
    local_date             TEXT    NOT NULL,
    local_hour             INTEGER NOT NULL,
    tz_offset              INTEGER NOT NULL DEFAULT 0,
    intervention_branch    TEXT,
    intervention_completed INTEGER NOT NULL DEFAULT 0,
    relief                 TEXT,
    tags                   TEXT    NOT NULL DEFAULT '',
    created_at             TEXT    NOT NULL,
    updated_at             TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_entries_occurred ON entries(occurred_at);
CREATE INDEX IF NOT EXISTS idx_entries_severity ON entries(severity);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

DEFAULTS = {
    "plan": "free",
    "region": "US",
    "lang": "zh",
    "installed_at": "",
}


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def now_iso() -> str:
    return now_utc().isoformat(timespec="seconds")


def connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db() -> None:
    conn = connect()
    try:
        conn.executescript(SCHEMA)
        for key, value in DEFAULTS.items():
            conn.execute(
                "INSERT OR IGNORE INTO settings(key, value) VALUES(?, ?)",
                (key, value or now_iso()),
            )
        conn.commit()
    finally:
        conn.close()


def get_setting(key: str, default: str = "") -> str:
    conn = connect()
    try:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default
    finally:
        conn.close()


def set_setting(key: str, value: str) -> None:
    conn = connect()
    try:
        conn.execute(
            "INSERT INTO settings(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        conn.commit()
    finally:
        conn.close()


def row_to_dict(row: sqlite3.Row) -> dict:
    data = dict(row)
    data["tags"] = [t for t in (data.get("tags") or "").split(",") if t]
    data["intervention_completed"] = bool(data.get("intervention_completed"))
    return data
