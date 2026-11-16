"""HTTP API（纯标准库实现，无第三方依赖）。

路由表与上一版 FastAPI 完全一致：
    POST   /api/entries
    GET    /api/entries
    GET    /api/entries/{id}
    PATCH  /api/entries/{id}
    DELETE /api/entries/{id}
    POST   /api/entries/{id}/intervention
    GET    /api/interventions/{emotion}
    GET    /api/stats/basic
    GET    /api/insights
    GET    /api/account
    POST   /api/account/subscription
    PATCH  /api/account/settings
    GET    /api/crisis
    GET    /api/export
    POST   /api/demo/seed
    GET    /api/meta
    GET    /api/health
"""

from __future__ import annotations

import csv
import base64
import io
import json
import re
import traceback
import urllib.parse
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from . import __version__, ai, auth, content, db, insights, seed

# 没有订阅、没有支付：Pro 只能靠兑换码开通（见 auth.redeem）

# 不需要登录就能访问的接口。登录页要拿 meta 才能渲染情绪与地区；
# 危机资源任何时候都该能打开，不能卡在登录后面。
PUBLIC_PATHS = {"/api/health", "/api/meta", "/api/crisis", "/api/bootstrap"}
WINDOW_DAYS = 14


# --------------------------------------------------------------------------- #
# 基础类型
# --------------------------------------------------------------------------- #


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        self.status = status
        self.message = message
        super().__init__(message)


class Request:
    def __init__(self, method: str, path: str, query: dict[str, list[str]], body: bytes, headers: dict[str, str]):
        self.method = method
        self.path = path
        self.query = query
        self.body = body
        self.headers = headers
        self.params: dict[str, str] = {}
        # 由 dispatch 填：当前登录用户 + 会话 token
        self.user: dict | None = None
        self.sid: str = ""
        self.client_ip: str = "unknown"

    def q(self, key: str, default: str | None = None) -> str | None:
        values = self.query.get(key)
        return values[0] if values else default

    def qi(self, key: str, default: int, low: int | None = None, high: int | None = None) -> int:
        raw = self.q(key)
        if raw is None:
            return default
        try:
            value = int(raw)
        except ValueError:
            raise ApiError(400, f"{key} must be an integer")
        if (low is not None and value < low) or (high is not None and value > high):
            raise ApiError(400, f"{key} must be between {low} and {high}")
        return value

    def qb(self, key: str, default: bool = False) -> bool:
        raw = self.q(key)
        if raw is None:
            return default
        if raw.lower() not in ("1", "true", "yes", "on", "0", "false", "no", "off"):
            raise ApiError(400, f"{key} must be a boolean")
        return raw.lower() in ("1", "true", "yes", "on")

    @property
    def lang(self) -> str:
        value = (self.q("lang") or "").lower()
        return value if value in ("zh", "en") else "zh"

    def json(self) -> dict:
        if not self.body:
            return {}
        try:
            data = json.loads(self.body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise ApiError(400, "invalid json body")
        if not isinstance(data, dict):
            raise ApiError(400, "json body must be an object")
        return data


class Response:
    def __init__(
        self,
        status: int = 200,
        payload: Any = None,
        raw: bytes | str | None = None,
        headers: dict[str, str] | None = None,
        content_type: str = "application/json; charset=utf-8",
    ):
        self.status = status
        self.payload = payload
        self.raw = raw
        self.headers = headers or {}
        self.content_type = content_type


# --------------------------------------------------------------------------- #
# 路由
# --------------------------------------------------------------------------- #


class Router:
    def __init__(self) -> None:
        self._routes: list[tuple[str, re.Pattern, Callable]] = []

    def route(self, method: str, pattern: str):
        def wrapper(fn: Callable):
            self._routes.append((method.upper(), re.compile(f"^{pattern}$"), fn))
            return fn

        return wrapper

    def match(self, method: str, path: str):
        allowed = []
        for m, rx, fn in self._routes:
            hit = rx.match(path)
            if hit:
                if m == method:
                    return fn, hit.groupdict()
                allowed.append(m)
        if allowed:
            raise ApiError(405, f"method {method} not allowed")
        raise ApiError(404, f"no route for {path}")


router = Router()


# --------------------------------------------------------------------------- #
# 校验与工具
# --------------------------------------------------------------------------- #


def _connect():
    return db.connect()


def _rows(sql: str, params: list | None = None) -> list[dict]:
    conn = _connect()
    try:
        return [db.row_to_dict(r) for r in conn.execute(sql, params or []).fetchall()]
    finally:
        conn.close()


def _one(entry_id: int, user_id: int) -> dict:
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT * FROM entries WHERE id = ? AND user_id = ?", (entry_id, user_id)
        ).fetchone()
    finally:
        conn.close()
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


# --------------------------------------------------------------------------- #
# 元信息
# --------------------------------------------------------------------------- #


@router.route("GET", "/api/bootstrap")
def bootstrap(req: Request):
    payload = {"logged_in": bool(req.user), "meta": meta(req), "account": None}
    if req.user:
        payload["account"] = _account_payload(req.user)
        payload["home"] = home(req)
    return payload


@router.route("GET", "/api/home")
def home(req: Request):
    since, today = _dates(req)
    uid = req.user["id"]
    watch = _rows("SELECT * FROM entries WHERE user_id = ? AND local_date BETWEEN ? AND ? AND severity >= 4 ORDER BY severity DESC, occurred_at DESC, id DESC LIMIT 3", [uid, since, today])
    recent = _rows("SELECT * FROM entries WHERE user_id = ? ORDER BY occurred_at DESC, id DESC LIMIT 3", [uid])
    total = _rows("SELECT COUNT(*) AS c FROM entries WHERE user_id = ?", [uid])[0]["c"]
    return {"items": watch or recent, "watch": bool(watch), "total": total, "local_today": today}


@router.route("POST", "/api/import")
def import_entries(req: Request):
    raw = req.json().get("entries")
    if not isinstance(raw, list) or not 1 <= len(raw) <= 5000:
        raise ApiError(400, "entries must contain 1–5000 records")
    rows = []
    for entry in raw:
        if not isinstance(entry, dict) or not entry.get("occurred_at"):
            raise ApiError(400, "each entry requires occurred_at")
        rows.append(_entry_payload(entry))
    uid = req.user["id"]
    conn = _connect()
    added = 0
    try:
        conn.execute("BEGIN IMMEDIATE")
        for row in rows:
            existing = conn.execute(
                "SELECT id FROM entries WHERE user_id = ? AND occurred_at = ? AND emotion = ? AND severity IS ? AND cause = ? AND note = ? AND tz_offset = ? AND intervention_completed = ? AND relief IS ? LIMIT 1",
                (uid, row["occurred_at"], row["emotion"], row["severity"], row["cause"], row["note"], row["tz_offset"], row["intervention_completed"], row["relief"]),
            ).fetchone()
            if not existing:
                _insert_entry(row, uid, conn)
                added += 1
        conn.commit()
    finally:
        conn.close()
    return {"imported": added, "skipped": len(rows) - added}


@router.route("GET", "/api/health")
def health(req: Request):
    return {"ok": True, "time": db.now_iso()}


@router.route("GET", "/api/meta")
def meta(req: Request):
    payload = content.meta_payload(req.lang)
    payload["version"] = __version__
    user = req.user
    payload["logged_in"] = bool(user)
    payload["plan"] = user["plan"] if user else "free"
    payload["region"] = user["region"] if user else db.DEFAULTS["region"]
    payload["lang"] = user["lang"] if user else "zh"
    payload["has_data"] = (
        bool(_rows("SELECT id FROM entries WHERE user_id = ? LIMIT 1", [user["id"]])) if user else False
    )
    payload["ai"] = ai.public_config()
    return payload


# --------------------------------------------------------------------------- #
# 记录
# --------------------------------------------------------------------------- #


@router.route("POST", "/api/entries")
def create_entry(req: Request):
    body = req.json()
    uid = req.user["id"]
    row = _entry_payload(body)
    client_id = _clean_text(body.get("client_id"), 64) or None
    conn = _connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        existing = conn.execute("SELECT id FROM entries WHERE user_id = ? AND client_id = ?", (uid, client_id)).fetchone() if client_id else None
        if existing:
            entry_id = existing["id"]
        else:
            entry_id = _insert_entry(row, uid, conn)
            conn.execute("UPDATE entries SET client_id = ? WHERE id = ?", (client_id, entry_id))
        conn.commit()
    finally:
        conn.close()
    return {
        "entry": _one(entry_id, uid),
        "intervention": content.intervention_for(row["emotion"], req.lang),
    }


@router.route("GET", "/api/entries")
def list_entries(req: Request):
    where = ["user_id = ?"]
    params: list = [req.user["id"]]
    emotion = req.q("emotion")
    if emotion and emotion != "all":
        if emotion not in content.EMOTION_KEYS:
            raise ApiError(400, "unknown emotion")
        where.append("emotion = ?")
        params.append(emotion)
    days = req.qi("days", 0, 0, 3650)
    if days:
        since, today = _dates(req, days)
        where.append("local_date BETWEEN ? AND ?")
        params.extend([since, today])
    if req.qb("older"):
        since, _ = _dates(req)
        where.append("local_date < ?")
        params.append(since)
    q = _clean_text(req.q("q"), 500)
    if q:
        where.append("(cause LIKE ? OR note LIKE ?)")
        params.extend([f"%{q}%", f"%{q}%"])
    if req.qb("unfilled"):
        where.append("(cause = '' OR severity IS NULL)")
    sort = req.q("sort", "severity")
    if sort not in ("severity", "time"):
        raise ApiError(400, "sort must be severity or time")
    order = "COALESCE(severity, 0) DESC, occurred_at DESC, id DESC" if sort == "severity" else "occurred_at DESC, id DESC"
    limit = req.qi("limit", 40, 1, 100)
    offset = req.qi("offset", 0, 0, 1000000)
    clause = " AND ".join(where)
    conn = _connect()
    try:
        total = conn.execute(f"SELECT COUNT(*) AS c FROM entries WHERE {clause}", params).fetchone()["c"]
        cursor = req.q("cursor")
        if cursor:
            try:
                decoded = json.loads(base64.urlsafe_b64decode(cursor).decode())
                if decoded["sort"] != sort:
                    raise ValueError()
                anchor = decoded["anchor"]
                if not isinstance(anchor, list) or len(anchor) != (3 if sort == "severity" else 2):
                    raise ValueError()
                if not isinstance(anchor[-2], str) or type(anchor[-1]) is not int:
                    raise ValueError()
                if sort == "severity" and (type(anchor[0]) is not int or not 0 <= anchor[0] <= 5):
                    raise ValueError()
            except (ValueError, KeyError, TypeError, UnicodeError):
                raise ApiError(400, "invalid cursor")
            fields = "(COALESCE(severity, 0), occurred_at, id)" if sort == "severity" else "(occurred_at, id)"
            where.append(f"{fields} < ({', '.join('?' for _ in anchor)})")
            params.extend(anchor)
            offset = 0
        clause = " AND ".join(where)
        rows = conn.execute(f"SELECT * FROM entries WHERE {clause} ORDER BY {order} LIMIT ? OFFSET ?", params + [limit + 1, offset]).fetchall()
    finally:
        conn.close()
    more = len(rows) > limit
    items = [db.row_to_dict(r) for r in rows[:limit]]
    next_cursor = None
    if more:
        last = items[-1]
        anchor = ([last["severity"] or 0] if sort == "severity" else []) + [last["occurred_at"], last["id"]]
        next_cursor = base64.urlsafe_b64encode(json.dumps({"sort": sort, "anchor": anchor}).encode()).decode()
    return {"items": items, "total": total, "next_cursor": next_cursor}


@router.route("DELETE", "/api/entries")
def delete_all_entries(req: Request):
    conn = _connect()
    try:
        # 只清当前账号自己的记录
        deleted = conn.execute("DELETE FROM entries WHERE user_id = ?", (req.user["id"],)).rowcount
        ai.cache_clear(req.user["id"], conn)
        conn.commit()
    finally:
        conn.close()
    return {"ok": True, "deleted": deleted}


@router.route("GET", r"/api/entries/(?P<entry_id>\d+)")
def get_entry(req: Request):
    return _one(int(req.params["entry_id"]), req.user["id"])


@router.route("PATCH", r"/api/entries/(?P<entry_id>\d+)")
def patch_entry(req: Request):
    entry_id = int(req.params["entry_id"])
    uid = req.user["id"]
    _one(entry_id, uid)
    body = req.json()
    fields: dict[str, Any] = {}
    if "severity" in body:
        fields["severity"] = _severity(body["severity"])
    if "cause" in body:
        cause = _clean_text(body["cause"], 500)
        fields["cause"] = cause
        fields["tags"] = ",".join(insights.tag_cause(cause))
    if "note" in body:
        fields["note"] = _clean_text(body["note"], 1000)
    if not fields:
        raise ApiError(400, "nothing to update")
    fields["updated_at"] = db.now_iso()
    sets = ", ".join(f"{k} = ?" for k in fields)
    conn = _connect()
    try:
        conn.execute(
            f"UPDATE entries SET {sets} WHERE id = ? AND user_id = ?",
            list(fields.values()) + [entry_id, uid],
        )
        conn.commit()
    finally:
        conn.close()
    return _one(entry_id, uid)


@router.route("DELETE", r"/api/entries/(?P<entry_id>\d+)")
def delete_entry(req: Request):
    entry_id = int(req.params["entry_id"])
    uid = req.user["id"]
    _one(entry_id, uid)
    conn = _connect()
    try:
        conn.execute("DELETE FROM entries WHERE id = ? AND user_id = ?", (entry_id, uid))
        conn.commit()
    finally:
        conn.close()
    return {"ok": True}


# --------------------------------------------------------------------------- #
# 干预
# --------------------------------------------------------------------------- #


@router.route("GET", r"/api/interventions/(?P<emotion>[a-z]+)")
def get_intervention(req: Request):
    emotion = req.params["emotion"]
    if emotion not in content.EMOTION_KEYS:
        raise ApiError(404, "unknown emotion")
    return content.intervention_for(emotion, req.lang)


@router.route("POST", r"/api/entries/(?P<entry_id>\d+)/intervention")
def record_intervention(req: Request):
    entry_id = int(req.params["entry_id"])
    uid = req.user["id"]
    _one(entry_id, uid)
    body = req.json()
    relief = body.get("relief")
    if relief not in (None, "", "better", "same", "worse"):
        raise ApiError(422, "relief must be better / same / worse")
    conn = _connect()
    try:
        conn.execute(
            "UPDATE entries SET intervention_completed = ?, relief = ?, updated_at = ? "
            "WHERE id = ? AND user_id = ?",
            (1 if body.get("completed", True) else 0, relief or None, db.now_iso(), entry_id, uid),
        )
        conn.commit()
    finally:
        conn.close()
    return _one(entry_id, uid)


# --------------------------------------------------------------------------- #
# 总结
# --------------------------------------------------------------------------- #


@router.route("GET", "/api/stats/basic")
def stats_basic(req: Request):
    _fixed_window(req)
    entries = _window_entries(req)
    payload = insights.build_basic(entries, req.lang)
    payload["window_days"] = WINDOW_DAYS
    daily = {}
    for entry in entries:
        if entry["severity"]:
            daily[entry["local_date"]] = max(daily.get(entry["local_date"], 0), entry["severity"])
    payload["daily_max"] = daily
    return payload


def _window_entries(req: Request) -> list[dict]:
    since, today = _dates(req)
    entries = _rows(
        "SELECT * FROM entries WHERE user_id = ? AND local_date BETWEEN ? AND ? ORDER BY occurred_at",
        [req.user["id"], since, today],
    )
    for entry in entries:
        entry["_window_today"] = today
    return entries


def _rules(req: Request, entries: list[dict], locked: bool) -> dict:
    return insights.build_insights(entries, WINDOW_DAYS, req.lang, locked=locked, today=_dates(req)[1])


def _redact(text: str) -> str:
    """错误详情会下发到前端，先把可能出现的 key 片段抹掉。"""
    return re.sub(r"sk-[A-Za-z0-9_\-]{4,}", "sk-***", text or "")


def _ai_state(entries: list[dict], days: int, lang: str, rules: dict, plan: str, user_id: int) -> dict:
    """这一屏归 AI 还是归规则引擎。

    状态直接决定界面长什么样：
      pro           免费层没有这项
      off           没配 key —— 全站静默用规则版
      insufficient  样本不够，先别硬凑结论
      pending       有数据但还没分析过，等待用户主动点击分析
      ready         正文已由模型改写（这里已经把文案合并进 rules 了）
    """
    cfg = ai.config()
    info: dict[str, Any] = {"enabled": cfg["enabled"], "model": cfg["model"] if cfg["enabled"] else None}

    if plan != "pro":
        return {**info, "status": "pro"}
    if not cfg["enabled"]:
        return {**info, "status": "off"}
    if len(entries) < insights.MIN_SAMPLE:
        return {
            **info,
            "status": "insufficient",
            "needed": insights.MIN_SAMPLE,
            "have": len(entries),
        }

    narrative = ai.cache_get(ai.fingerprint(entries, days, lang, cfg["model"]), user_id)
    if narrative is None:
        return {**info, "status": "pending"}

    ai.merge(rules, narrative)
    return {
        **info,
        "status": "ready",
        "headline": narrative.get("headline") or "",
        "summary": narrative.get("summary") or "",
        "next_step": narrative.get("next_step") or "",
        "focus": narrative.get("focus") or {},
    }


@router.route("GET", "/api/insights")
def get_insights(req: Request):
    _fixed_window(req)
    days = WINDOW_DAYS
    plan = req.user["plan"]
    entries = _window_entries(req)
    payload = _rules(req, entries, locked=(plan != "pro"))
    payload["ai"] = _ai_state(entries, days, req.lang, payload, plan, req.user["id"])
    return payload


@router.route("POST", "/api/insights/analyze")
def analyze_insights(req: Request):
    """让模型重新分析这段时间的记录。

    这是唯一一个会真的阻塞几秒的接口；失败不走 500，而是带着错误状态返回规则版，
    前端因此永远有内容可显示。
    """
    plan = req.user["plan"]
    if plan != "pro":
        raise ApiError(403, "AI 分析属于 Pro 层")

    cfg = ai.config()
    if not cfg["enabled"]:
        raise ApiError(503, "还没有配置 AI key：把 TETHER_AI_API_KEY 写进 .env")

    body = req.json()
    _fixed_window(req)
    if "days" in body and (type(body["days"]) is not int or body["days"] != WINDOW_DAYS):
        raise ApiError(400, "days must be 14")
    days = WINDOW_DAYS
    force = bool(body.get("force"))
    lang = req.lang

    revision = ai.data_revision(req.user["id"])
    entries = _window_entries(req)
    rules = _rules(req, entries, locked=False)

    if len(entries) < insights.MIN_SAMPLE:
        rules["ai"] = {
            "enabled": True,
            "model": cfg["model"],
            "status": "insufficient",
            "needed": insights.MIN_SAMPLE,
            "have": len(entries),
        }
        return rules

    fingerprint = ai.fingerprint(entries, days, lang, cfg["model"])
    narrative = None if force else ai.cache_get(fingerprint, req.user["id"])
    from_cache = narrative is not None
    error = ""

    if narrative is None:
        try:
            narrative = ai.analyze(entries, days, lang, rules)
            if not ai.cache_put(fingerprint, narrative, cfg["model"], req.user["id"], revision):
                raise ApiError(409, "记录已变化，请刷新后重新分析" if lang == "zh" else "Entries changed. Refresh before analyzing again.")
        except ai.AIError as exc:
            error = str(exc)

    if narrative is None:
        rules["ai"] = {
            "enabled": True,
            "model": cfg["model"],
            "status": "error",
            "message": _redact(error)[:200],
        }
        return rules

    ai.merge(rules, narrative)
    rules["ai"] = {
        "enabled": True,
        "model": cfg["model"],
        "status": "ready",
        "headline": narrative.get("headline") or "",
        "summary": narrative.get("summary") or "",
        "next_step": narrative.get("next_step") or "",
        "focus": narrative.get("focus") or {},
        "cached": from_cache,
    }
    return rules


# --------------------------------------------------------------------------- #
# 账号 / 登录
# --------------------------------------------------------------------------- #


def _account_payload(user: dict) -> dict:
    total = _rows("SELECT COUNT(*) AS c FROM entries WHERE user_id = ?", [user["id"]])[0]["c"]
    return {
        "user": {"id": user["id"], "username": user["username"], "created_at": user["created_at"]},
        "plan": user["plan"],
        "pro": user["plan"] == "pro",
        "region": user["region"],
        "lang": user["lang"],
        "entries": total,
        "note": {
            "zh": "Pro 用兑换码开通，没有订阅、也不会扣款。",
            "en": "Pro is unlocked with a redeem code. No subscription, no charges.",
        },
    }


def _session_response(user: dict) -> Response:
    token, _ = auth.create_session(user["id"])
    resp = Response(payload={"account": _account_payload(user)})
    resp.headers["Set-Cookie"] = (
        f"{auth.COOKIE_NAME}={token}; Path=/; HttpOnly; SameSite=Lax; Max-Age={auth.SESSION_DAYS * 86400}"
    )
    return resp


@router.route("POST", "/api/auth/register")
def register(req: Request):
    body = req.json()
    user = auth.create_user(_clean_text(body.get("username"), 24), _clean_password(body.get("password")))
    return _session_response(user)


@router.route("POST", "/api/auth/login")
def login(req: Request):
    body = req.json()
    username = _clean_text(body.get("username"), 24)
    password = _clean_password(body.get("password"))
    user = auth.authenticate_limited(req.client_ip, username, password)
    return _session_response(user)


@router.route("POST", "/api/auth/logout")
def logout(req: Request):
    if req.sid:
        auth.delete_session(req.sid)
    resp = Response(payload={"ok": True})
    resp.headers["Set-Cookie"] = f"{auth.COOKIE_NAME}=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0"
    return resp


@router.route("GET", "/api/auth/me")
def whoami(req: Request):
    if not req.user:
        return {"logged_in": False, "account": None}
    return {"logged_in": True, "account": _account_payload(req.user)}


@router.route("POST", "/api/auth/password")
def change_password(req: Request):
    if not req.user:
        raise ApiError(401, "login required")
    body = req.json()
    old = body.get("old_password") or ""
    new = body.get("new_password") or ""
    auth.authenticate_limited(req.client_ip, req.user["username"], _clean_password(old))
    auth.set_password(req.user["id"], new)
    # 改密码会踢掉所有会话，这里立刻补一个新的，免得自己被登出
    return _session_response(req.user)


@router.route("POST", "/api/account/redeem")
def redeem_code(req: Request):
    code = req.json().get("code") or ""
    user = auth.redeem(req.user["id"], code)
    return {"account": _account_payload(user), "ok": True}


@router.route("GET", "/api/account")
def get_account(req: Request):
    return _account_payload(req.user)


@router.route("PATCH", "/api/account/settings")
def update_settings(req: Request):
    body = req.json()
    fields: dict[str, Any] = {}
    region = body.get("region")
    if region is not None:
        if region not in content.REGION_KEYS:
            raise ApiError(422, "unknown region")
        fields["region"] = region
    lang = body.get("lang")
    if lang is not None:
        if lang not in ("zh", "en"):
            raise ApiError(422, "lang must be zh or en")
        fields["lang"] = lang
    user = auth.update_user(req.user["id"], **fields)
    return _account_payload(user)


@router.route("GET", "/api/crisis")
def crisis(req: Request):
    fallback = req.user["region"] if req.user else db.DEFAULTS["region"]
    return content.crisis_for(req.q("region") or fallback, req.lang)


# --------------------------------------------------------------------------- #
# 缓解转盘
# --------------------------------------------------------------------------- #


def _wheel_items(user_id: int) -> list[dict]:
    return _rows(
        "SELECT id, label FROM wheel_items WHERE user_id = ? ORDER BY sort, id", [user_id]
    )


def _wheel_seed(user_id: int, lang: str) -> None:
    now = db.now_iso()
    conn = _connect()
    try:
        for index, label in enumerate(content.wheel_defaults(lang)):
            conn.execute(
                "INSERT INTO wheel_items(user_id, label, sort, created_at) VALUES(?, ?, ?, ?)",
                (user_id, label, index, now),
            )
        conn.commit()
    finally:
        conn.close()


@router.route("GET", "/api/wheel")
def get_wheel(req: Request):
    user_id = req.user["id"]
    items = _wheel_items(user_id)
    if not items:
        # 第一次进来就给他一份能直接用的清单，别让用户先面对一个空转盘
        _wheel_seed(user_id, req.lang)
        items = _wheel_items(user_id)
    return {"items": items, "limits": content.WHEEL_LIMITS}


@router.route("PUT", "/api/wheel")
def put_wheel(req: Request):
    raw = req.json().get("items")
    if not isinstance(raw, list):
        raise ApiError(422, "items must be a list")

    limit = content.WHEEL_LIMITS
    labels: list[str] = []
    for item in raw:
        text = item if isinstance(item, str) else (item or {}).get("label")
        cleaned = _clean_text(text, limit["label"])
        if cleaned:
            labels.append(cleaned)

    if len(labels) < limit["min"]:
        raise ApiError(422, f"至少留 {limit['min']} 项，不然转起来没意义")
    if len(labels) > limit["max"]:
        raise ApiError(422, f"最多 {limit['max']} 项，再多就看不清了")

    user_id = req.user["id"]
    now = db.now_iso()
    conn = _connect()
    try:
        conn.execute("DELETE FROM wheel_items WHERE user_id = ?", (user_id,))
        for index, label in enumerate(labels):
            conn.execute(
                "INSERT INTO wheel_items(user_id, label, sort, created_at) VALUES(?, ?, ?, ?)",
                (user_id, label, index, now),
            )
        conn.commit()
    finally:
        conn.close()
    return {"items": _wheel_items(user_id), "limits": limit}


# --------------------------------------------------------------------------- #
# 导出
# --------------------------------------------------------------------------- #


def _csv_cell(value: str) -> str:
    return "'" + value if value and (value.lstrip().startswith(("=", "+", "-", "@")) or value.startswith(("\t", "\r", "\n"))) else value


@router.route("GET", "/api/export")
def export(req: Request):
    rows = _rows(
        "SELECT * FROM entries WHERE user_id = ? ORDER BY occurred_at DESC", [req.user["id"]]
    )
    if (req.q("format") or "json") == "csv":
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(
            ["id", "occurred_at", "local_date", "local_hour", "emotion", "severity", "cause", "note", "relief"]
        )
        for r in rows:
            writer.writerow(
                [
                    r["id"],
                    r["occurred_at"],
                    r["local_date"],
                    r["local_hour"],
                    r["emotion"],
                    r["severity"] if r["severity"] is not None else "",
                    _csv_cell(r["cause"]),
                    _csv_cell(r["note"]),
                    r["relief"] or "",
                ]
            )
        return Response(
            raw="\ufeff" + buf.getvalue(),
            headers={"Content-Disposition": 'attachment; filename="tether-entries.csv"'},
            content_type="text/csv; charset=utf-8",
        )
    return Response(
        payload={"exported_at": db.now_iso(), "count": len(rows), "entries": rows},
        headers={"Content-Disposition": 'attachment; filename="tether-entries.json"'},
    )


# --------------------------------------------------------------------------- #
# 演示数据
# --------------------------------------------------------------------------- #


@router.route("POST", "/api/demo/seed")
def demo_seed(req: Request):
    tz_offset = req.qi("tz_offset", 480, -840, 840)
    reset = req.qb("reset", False)
    if reset:
        raise ApiError(400, "演示数据只能追加；清空记录请使用单独的清空功能")
    uid = req.user["id"]
    conn = _connect()
    try:
        for row in seed.build_rows(tz_offset):
            _insert_entry(row, uid, conn)
        conn.commit()
    finally:
        conn.close()
    total = _rows("SELECT COUNT(*) AS c FROM entries WHERE user_id = ?", [uid])[0]["c"]
    return {"seeded": True, "entries": total}


# --------------------------------------------------------------------------- #
# 分发
# --------------------------------------------------------------------------- #


def _cookie(req: Request, name: str) -> str:
    raw = req.headers.get("cookie") or ""
    for part in raw.split(";"):
        key, _, value = part.strip().partition("=")
        if key == name:
            return urllib.parse.unquote(value)
    return ""


def dispatch(req: Request) -> Response:
    try:
        handler, params = router.match(req.method, req.path)
        # 先把会话解出来：公开接口也想知道「谁在访问」（比如 meta 要回该用户的 plan）
        req.sid = _cookie(req, auth.COOKIE_NAME)
        req.user = auth.user_by_token(req.sid)

        public = req.path in PUBLIC_PATHS or req.path.startswith("/api/auth/")
        if not public and not req.user:
            raise ApiError(401, "login required")

        req.params = params
        result = handler(req)
        if isinstance(result, Response):
            return result
        return Response(payload=result)
    except ApiError as exc:
        return Response(status=exc.status, payload={"error": exc.message})
    except auth.AuthError as exc:
        headers = {"Retry-After": str(exc.retry_after)} if hasattr(exc, "retry_after") else {}
        return Response(status=exc.status, payload={"error": exc.message}, headers=headers)
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        return Response(status=500, payload={"error": "internal server error"})


def parse_query(query_string: str) -> dict[str, list[str]]:
    return urllib.parse.parse_qs(query_string, keep_blank_values=True)
