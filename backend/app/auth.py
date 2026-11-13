"""账号与会话。

密码用 scrypt 存（标准库，不引第三方）。会话是一张表 + 一个 HttpOnly Cookie，
过期时间 30 天；改密码会把该账号的所有会话踢掉，逼其他设备重新登录。
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone

from . import db

SESSION_DAYS = 30
COOKIE_NAME = "tether_sid"

# scrypt 参数：n=2^14 在普通机器上约 40—60ms，够挡住离线爆破又不至于让登录卡顿
SCRYPT_N = 16384
SCRYPT_R = 8
SCRYPT_P = 1


class AuthError(Exception):
    def __init__(self, status: int, message: str):
        self.status = status
        self.message = message
        super().__init__(message)


def hash_password(password: str, salt: str | None = None) -> tuple[str, str]:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"), salt=salt.encode("utf-8"), n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=32
    )
    return digest.hex(), salt


def verify_password(password: str, salt: str, expected: str) -> bool:
    digest, _ = hash_password(password, salt)
    return hmac.compare_digest(digest, expected)


# --------------------------------------------------------------------------- #
# 用户
# --------------------------------------------------------------------------- #


def _row_to_user(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "username": row["username"],
        "plan": row["plan"],
        "region": row["region"],
        "lang": row["lang"],
        "created_at": row["created_at"],
    }


def get_user(user_id: int) -> dict | None:
    conn = db.connect()
    try:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    finally:
        conn.close()
    return _row_to_user(row) if row else None


def get_user_by_name(username: str) -> dict | None:
    conn = db.connect()
    try:
        row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    finally:
        conn.close()
    return _row_to_user(row) if row else None


def create_user(username: str, password: str) -> dict:
    if not (2 <= len(username) <= 24):
        raise AuthError(422, "用户名需要 2—24 个字符")
    if not all(c.isalnum() or c in "_-." for c in username):
        raise AuthError(422, "用户名只能用字母、数字、下划线、短横线和点")
    if len(password) < 6:
        raise AuthError(422, "密码至少 6 位")

    pass_hash, salt = hash_password(password)
    now = db.now_iso()
    conn = db.connect()
    try:
        cur = conn.execute(
            "INSERT INTO users(username, pass_hash, pass_salt, plan, region, lang, created_at, updated_at) "
            "VALUES(?, ?, ?, 'free', ?, ?, ?, ?)",
            (username, pass_hash, salt, db.DEFAULTS["region"], db.DEFAULTS["lang"], now, now),
        )
        conn.commit()
        user_id = int(cur.lastrowid or 0)
    except sqlite3.IntegrityError:
        raise AuthError(409, "这个用户名已经被用了") from None
    finally:
        conn.close()
    return get_user(user_id)


def set_password(user_id: int, new_password: str) -> None:
    if len(new_password) < 6:
        raise AuthError(422, "密码至少 6 位")
    pass_hash, salt = hash_password(new_password)
    conn = db.connect()
    try:
        conn.execute(
            "UPDATE users SET pass_hash = ?, pass_salt = ?, updated_at = ? WHERE id = ?",
            (pass_hash, salt, db.now_iso(), user_id),
        )
        conn.commit()
    finally:
        conn.close()
    # 改密码后其他设备上的会话立即失效
    delete_user_sessions(user_id)


def update_user(user_id: int, **fields) -> dict:
    allowed = {"plan", "region", "lang"}
    sets = [f"{k} = ?" for k in fields if k in allowed]
    values = [v for k, v in fields.items() if k in allowed]
    if sets:
        values.append(db.now_iso())
        values.append(user_id)
        conn = db.connect()
        try:
            conn.execute(f"UPDATE users SET {', '.join(sets)}, updated_at = ? WHERE id = ?", values)
            conn.commit()
        finally:
            conn.close()
    return get_user(user_id)


def authenticate(username: str, password: str) -> dict:
    conn = db.connect()
    try:
        row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    finally:
        conn.close()
    if not row or not verify_password(password, row["pass_salt"], row["pass_hash"]):
        raise AuthError(401, "用户名或密码不对")
    return _row_to_user(row)


# --------------------------------------------------------------------------- #
# 会话
# --------------------------------------------------------------------------- #


def create_session(user_id: int) -> tuple[str, str]:
    token = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    expires = now + timedelta(days=SESSION_DAYS)
    conn = db.connect()
    try:
        conn.execute("DELETE FROM sessions WHERE expires_at < ?", (now.isoformat(timespec="seconds"),))
        conn.execute(
            "INSERT INTO sessions(token, user_id, created_at, expires_at) VALUES(?, ?, ?, ?)",
            (token, user_id, now.isoformat(timespec="seconds"), expires.isoformat(timespec="seconds")),
        )
        conn.commit()
    finally:
        conn.close()
    return token, expires.isoformat(timespec="seconds")


def user_by_token(token: str) -> dict | None:
    if not token:
        return None
    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT u.* FROM sessions s JOIN users u ON u.id = s.user_id "
            "WHERE s.token = ? AND s.expires_at > ?",
            (token, datetime.now(timezone.utc).isoformat(timespec="seconds")),
        ).fetchone()
    finally:
        conn.close()
    return _row_to_user(row) if row else None


def delete_session(token: str) -> None:
    conn = db.connect()
    try:
        conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
        conn.commit()
    finally:
        conn.close()


def delete_user_sessions(user_id: int) -> None:
    conn = db.connect()
    try:
        conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
        conn.commit()
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# 兑换码
# --------------------------------------------------------------------------- #


def redeem(user_id: int, code: str) -> dict:
    """兑换一次。码用完、重复兑换、码不存在都给出明确原因。"""
    code = (code or "").strip().upper()
    if not code:
        raise AuthError(422, "请输入兑换码")

    conn = db.connect()
    try:
        row = conn.execute("SELECT * FROM redeem_codes WHERE code = ?", (code,)).fetchone()
        if not row:
            raise AuthError(404, "这个兑换码不存在")
        already = conn.execute(
            "SELECT 1 FROM redemptions WHERE code = ? AND user_id = ?", (code, user_id)
        ).fetchone()
        if already:
            raise AuthError(409, "这个码你已经兑换过了")
        if row["used"] >= row["max_uses"]:
            raise AuthError(409, "这个兑换码已经用完了")

        now = db.now_iso()
        conn.execute("UPDATE redeem_codes SET used = used + 1 WHERE code = ?", (code,))
        conn.execute(
            "INSERT INTO redemptions(code, user_id, redeemed_at) VALUES(?, ?, ?)",
            (code, user_id, now),
        )
        conn.execute(
            "UPDATE users SET plan = ?, updated_at = ? WHERE id = ?", (row["plan"], now, user_id)
        )
        conn.commit()
        plan = row["plan"]
    finally:
        conn.close()
    return get_user(user_id) | {"redeemed_plan": plan}


def create_code(code: str, plan: str = "pro", max_uses: int = 1, note: str = "") -> str:
    code = code.strip().upper()
    conn = db.connect()
    try:
        conn.execute(
            "INSERT INTO redeem_codes(code, plan, max_uses, used, note, created_at) VALUES(?, ?, ?, 0, ?, ?)",
            (code, plan, max(1, max_uses), note, db.now_iso()),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        raise AuthError(409, f"兑换码 {code} 已存在") from None
    finally:
        conn.close()
    return code


def list_codes() -> list[dict]:
    conn = db.connect()
    try:
        rows = conn.execute("SELECT * FROM redeem_codes ORDER BY created_at DESC").fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]
