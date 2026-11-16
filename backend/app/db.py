"""SQLite 存储层。单用户本地部署，无需外部数据库。"""

from __future__ import annotations

import pathlib
import os
import sqlite3
from datetime import datetime, timezone

DATA_DIR = pathlib.Path(os.environ.get("TETHER_DATA_DIR") or pathlib.Path(__file__).resolve().parent.parent / "data")
DB_PATH = DATA_DIR / "tether.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS entries (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id                INTEGER NOT NULL DEFAULT 0,
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
/* user_id 的索引不写在这里：老库还没有这一列时，
   这句会先于补列执行并直接报错。索引统一在 _migrate() 里补。 */

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- AI 分析结果缓存：指纹相同（同一批记录 + 同一窗口 + 同一模型）直接复用，
-- 免得每次翻到总结页都把同样的数据再算一遍。
CREATE TABLE IF NOT EXISTS ai_cache (
    key        TEXT PRIMARY KEY,
    payload    TEXT NOT NULL,
    model      TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

-- 账号。plan / region / lang 都挂在用户身上，记录按 user_id 隔离。
CREATE TABLE IF NOT EXISTS users (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    username   TEXT NOT NULL UNIQUE COLLATE NOCASE,
    pass_hash  TEXT NOT NULL,
    pass_salt  TEXT NOT NULL,
    plan       TEXT NOT NULL DEFAULT 'free',
    region     TEXT NOT NULL DEFAULT 'CN',
    lang       TEXT NOT NULL DEFAULT 'zh',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    token      TEXT PRIMARY KEY,
    user_id    INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);

-- 兑换码：Pro 只靠码开通，不做支付
CREATE TABLE IF NOT EXISTS redeem_codes (
    code       TEXT PRIMARY KEY,
    plan       TEXT NOT NULL DEFAULT 'pro',
    max_uses   INTEGER NOT NULL DEFAULT 1,
    used       INTEGER NOT NULL DEFAULT 0,
    note       TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS redemptions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    code        TEXT NOT NULL,
    user_id     INTEGER NOT NULL,
    redeemed_at TEXT NOT NULL,
    UNIQUE(code, user_id)
);

-- 缓解转盘的清单：用户自己填「能做点什么」，转盘从里面随机挑一件
CREATE TABLE IF NOT EXISTS wheel_items (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    label      TEXT NOT NULL,
    sort       INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_wheel_user ON wheel_items(user_id);
"""

DEFAULTS = {
    "plan": "free",
    "region": "CN",
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
        _migrate(conn)
        for key, value in DEFAULTS.items():
            conn.execute(
                "INSERT OR IGNORE INTO settings(key, value) VALUES(?, ?)",
                (key, value or now_iso()),
            )
        conn.commit()
    finally:
        conn.close()


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _migrate(conn: sqlite3.Connection) -> None:
    """老库补列。单机版升到多账号时，历史记录落在 user_id=0，对新账号不可见。"""
    if "user_id" not in _columns(conn, "entries"):
        conn.execute("ALTER TABLE entries ADD COLUMN user_id INTEGER NOT NULL DEFAULT 0")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_entries_user ON entries(user_id)")
    if "client_id" not in _columns(conn, "entries"):
        conn.execute("ALTER TABLE entries ADD COLUMN client_id TEXT")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_entries_client ON entries(user_id, client_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_entries_local ON entries(user_id, local_date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_entries_time ON entries(user_id, occurred_at DESC, id DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_entries_rank ON entries(user_id, COALESCE(severity, 0) DESC, occurred_at DESC, id DESC)")


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
