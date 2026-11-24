"""跨路由共享的校验、序列化与数据库辅助函数。"""

from __future__ import annotations

import re

from datetime import datetime, timedelta, timezone

from typing import Any

from .. import auth, content, db, insights

from .core import ApiError, Request, Response, WINDOW_DAYS, _bearer

# --------------------------------------------------------------------------- #
# 校验与工具
# --------------------------------------------------------------------------- #


_session = db.session


def _connect():
    """只在「调用方已经持有连接」的分支里使用（见 _insert_entry）。"""
    return db.connect()


def _rows(sql: str, params: list | None = None) -> list[dict]:
    with _session() as conn:
        return [db.row_to_dict(r) for r in conn.execute(sql, params or []).fetchall()]


def _one(entry_id: int, user_id: int) -> dict:
    with _session() as conn:
        row = conn.execute(
            "SELECT * FROM entries WHERE id = ? AND user_id = ?", (entry_id, user_id)
        ).fetchone()
    if not row:
        raise ApiError(404, "entry not found")
    return db.row_to_dict(row)


def _branch(emotion: str) -> str:
    return (content.EMOTION_MAP.get(emotion) or {}).get("branch", "none")


def _clean_text(value: Any, limit: int) -> str:
    if value is None:
        return ""
    if not isinstance(value, str) or len(value) > limit:
        raise ApiError(400, f"text must be a string of at most {limit} characters")
    return value.strip()


def _clean_password(value: Any) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= 128:
        raise ApiError(400, "password must contain 1–128 characters")
    return value


def _offset(value: Any) -> int:
    if type(value) is not int or not -840 <= value <= 840:
        raise ApiError(400, "tz_offset is required: integer minutes between -840 and 840")
    return value


def _dates(req: Request, days: int = WINDOW_DAYS) -> tuple[str, str]:
    offset = req.qi("tz_offset", 0, -840, 840)
    today = db.now_utc().astimezone(timezone(timedelta(minutes=offset))).date()
    return (today - timedelta(days=days - 1)).isoformat(), today.isoformat()


def _fixed_window(req: Request) -> None:
    req.qi("days", WINDOW_DAYS, WINDOW_DAYS, WINDOW_DAYS)


def _severity(value: Any, *, required: bool = False) -> int | None:
    if value is None or value == "":
        if required:
            raise ApiError(422, "severity is required")
        return None
    if type(value) is not int:
        raise ApiError(400, "severity must be an integer between 1 and 5")
    number = value
    if not 1 <= number <= 5:
        raise ApiError(422, "severity must be between 1 and 5")
    return number


def _normalize_time(occurred_at: str | None, tz_offset: int) -> tuple[str, str, int]:
    if occurred_at:
        try:
            dt = datetime.fromisoformat(occurred_at.replace("Z", "+00:00"))
        except (ValueError, TypeError, AttributeError):
            raise ApiError(400, "occurred_at must be an ISO 8601 timestamp with timezone")
    else:
        dt = db.now_utc()
    if dt.tzinfo is None:
        raise ApiError(400, "occurred_at must include a timezone")
    local = dt.astimezone(timezone(timedelta(minutes=tz_offset)))
    return (
        dt.astimezone(timezone.utc).isoformat(timespec="seconds"),
        local.strftime("%Y-%m-%d"),
        local.hour,
    )


def _insert_entry(row: dict, user_id: int, connection=None) -> int:
    now = db.now_iso()
    conn = connection or _connect()
    try:
        cur = conn.execute(
            """
            INSERT INTO entries (user_id, emotion, severity, cause, note, occurred_at, local_date,
                                 local_hour, tz_offset, intervention_branch,
                                 intervention_completed, relief, tags, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                row["emotion"],
                row["severity"],
                row["cause"],
                row["note"],
                row["occurred_at"],
                row["local_date"],
                row["local_hour"],
                row["tz_offset"],
                _branch(row["emotion"]),
                row.get("intervention_completed", 0),
                row.get("relief"),
                ",".join(insights.tag_cause(row["cause"])),
                now,
                now,
            ),
        )
        if connection is None:
            conn.commit()
        return int(cur.lastrowid or 0)
    finally:
        if connection is None:
            conn.close()


def _entry_payload(body: dict) -> dict:
    emotion = body.get("emotion")
    if not isinstance(emotion, str) or emotion not in content.EMOTION_KEYS:
        raise ApiError(400, "unknown emotion")
    offset = _offset(body.get("tz_offset"))
    occurred, local_date, local_hour = _normalize_time(body.get("occurred_at"), offset)
    completed = body.get("intervention_completed", False)
    relief = body.get("relief") or None
    if type(completed) is not bool or relief not in (None, "better", "same", "worse"):
        raise ApiError(400, "invalid intervention feedback")
    return {"emotion": emotion, "severity": _severity(body.get("severity")),
            "cause": _clean_text(body.get("cause"), 500), "note": _clean_text(body.get("note"), 1000),
            "occurred_at": occurred, "local_date": local_date, "local_hour": local_hour,
            "tz_offset": offset, "intervention_completed": completed, "relief": relief}


def _account_payload(user: dict) -> dict:
    total = _rows("SELECT COUNT(*) AS c FROM entries WHERE user_id = ?", [user["id"]])[0]["c"]
    return {
        "user": {"id": user["id"], "username": user["username"], "created_at": user["created_at"]},
        "plan": user["plan"],
        "pro": user["plan"] == "pro",
        "region": user["region"],
        "lang": user["lang"],
        "lang_set": bool(user.get("lang_set")),
        "entries": total,
        "note": {
            "zh": "Pro 用兑换码开通，没有订阅、也不会扣款。",
            "en": "Pro is unlocked with a redeem code. No subscription, no charges.",
        },
    }


def _session_response(user: dict, req: Request | None = None) -> Response:
    """登录成功：网页拿到 Cookie，壳内另外拿到 Bearer token。

    token 只回给请求方，不写进日志；网页端继续用 HttpOnly Cookie（JS 读不到，
    XSS 也偷不走），只有壳内才把 token 放进安全存储。
    """
    token, _ = auth.create_session(user["id"])
    payload = {"account": _account_payload(user)}
    if req is not None and _wants_token(req):
        payload["token"] = token
        payload["token_type"] = "Bearer"
        payload["expires_in"] = auth.SESSION_DAYS * 86400
    resp = Response(payload=payload)
    resp.headers["Set-Cookie"] = (
        f"{auth.COOKIE_NAME}={token}; Path=/; HttpOnly; SameSite=Lax; Max-Age={auth.SESSION_DAYS * 86400}"
    )
    return resp


def _wants_token(req: Request) -> bool:
    """只有显式声明的客户端才发 token，避免网页端把会话凭据交给 JS。"""
    if _bearer(req):
        return True
    return (req.headers.get("x-tether-token-auth") or "").strip().lower() in ("1", "true", "bearer")


# --------------------------------------------------------------------------- #
# 导出用的小工具
# --------------------------------------------------------------------------- #


def _csv_cell(value: str) -> str:
    """CSV 公式注入防护：以 = + - @ 或控制字符开头的内容前面补一个单引号。"""
    return "'" + value if value and (value.lstrip().startswith(("=", "+", "-", "@")) or value.startswith(("\t", "\r", "\n"))) else value

# --------------------------------------------------------------------------- #
# 错误文案脱敏
# --------------------------------------------------------------------------- #


def _redact(text: str) -> str:
    """错误详情会下发到前端，先把可能出现的 key 片段抹掉。"""
    return re.sub(r"sk-[A-Za-z0-9_\-]{4,}", "sk-***", text or "")