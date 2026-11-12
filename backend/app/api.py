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
import io
import json
import re
import traceback
import urllib.parse
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from . import ai, content, db, insights, seed

PRICING = {
    "monthly": {"price": "$9", "period": {"zh": "/ 月", "en": "/ month"}},
    "yearly": {"price": "$59", "period": {"zh": "/ 年", "en": "/ year"}, "note": {"zh": "相当于 $4.9/月", "en": "works out to $4.9/mo"}},
    "trial_days": 7,
}


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

    def q(self, key: str, default: str | None = None) -> str | None:
        values = self.query.get(key)
        return values[0] if values else default

    def qi(self, key: str, default: int) -> int:
        raw = self.q(key)
        if raw is None:
            return default
        try:
            return int(raw)
        except ValueError:
            return default

    def qb(self, key: str, default: bool = False) -> bool:
        raw = self.q(key)
        if raw is None:
            return default
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


def _one(entry_id: int) -> dict:
    conn = _connect()
    try:
        row = conn.execute("SELECT * FROM entries WHERE id = ?", (entry_id,)).fetchone()
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
    return str(value).strip()[:limit]


def _severity(value: Any, *, required: bool = False) -> int | None:
    if value is None or value == "":
        if required:
            raise ApiError(422, "severity is required")
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise ApiError(422, "severity must be an integer between 1 and 5")
    if not 1 <= number <= 5:
        raise ApiError(422, "severity must be between 1 and 5")
    return number


def _normalize_time(occurred_at: str | None, tz_offset: int) -> tuple[str, str, int]:
    if occurred_at:
        try:
            dt = datetime.fromisoformat(occurred_at.replace("Z", "+00:00"))
        except ValueError:
            dt = db.now_utc()
    else:
        dt = db.now_utc()
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    local = dt.astimezone(timezone(timedelta(minutes=tz_offset)))
    return (
        dt.astimezone(timezone.utc).isoformat(timespec="seconds"),
        local.strftime("%Y-%m-%d"),
        local.hour,
    )


def _insert_entry(row: dict) -> int:
    now = db.now_iso()
    conn = _connect()
    try:
        cur = conn.execute(
            """
            INSERT INTO entries (emotion, severity, cause, note, occurred_at, local_date,
                                 local_hour, tz_offset, intervention_branch,
                                 intervention_completed, relief, tags, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
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
        conn.commit()
        return int(cur.lastrowid or 0)
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# 元信息
# --------------------------------------------------------------------------- #


@router.route("GET", "/api/health")
def health(req: Request):
    return {"ok": True, "time": db.now_iso()}


@router.route("GET", "/api/meta")
def meta(req: Request):
    payload = content.meta_payload(req.lang)
    payload["pricing"] = PRICING
    payload["plan"] = db.get_setting("plan", "free")
    payload["region"] = db.get_setting("region", "US")
    payload["lang"] = db.get_setting("lang", "zh")
    payload["has_data"] = bool(_rows("SELECT id FROM entries LIMIT 1"))
    payload["ai"] = ai.public_config()
    return payload


# --------------------------------------------------------------------------- #
# 记录
# --------------------------------------------------------------------------- #


@router.route("POST", "/api/entries")
def create_entry(req: Request):
    body = req.json()
    emotion = body.get("emotion")
    if emotion not in content.EMOTION_KEYS:
        raise ApiError(422, "unknown emotion")
    occurred_at, local_date, local_hour = _normalize_time(
        body.get("occurred_at"), int(body.get("tz_offset") or 0)
    )
    entry_id = _insert_entry(
        {
            "emotion": emotion,
            "severity": _severity(body.get("severity")),
            "cause": _clean_text(body.get("cause"), 500),
            "note": _clean_text(body.get("note"), 1000),
            "occurred_at": occurred_at,
            "local_date": local_date,
            "local_hour": local_hour,
            "tz_offset": int(body.get("tz_offset") or 0),
        }
    )
    return {
        "entry": _one(entry_id),
        "intervention": content.intervention_for(emotion, req.lang),
    }


@router.route("GET", "/api/entries")
def list_entries(req: Request):
    where = ["1=1"]
    params: list = []
    emotion = req.q("emotion")
    if emotion and emotion != "all":
        where.append("emotion = ?")
        params.append(emotion)
    days = req.qi("days", 0)
    if days:
        where.append("occurred_at >= ?")
        params.append((db.now_utc() - timedelta(days=days)).isoformat(timespec="seconds"))
    q = (req.q("q") or "").strip()
    if q:
        where.append("(cause LIKE ? OR note LIKE ?)")
        params.extend([f"%{q}%", f"%{q}%"])
    if req.qb("unfilled"):
        where.append("(cause = '' OR severity IS NULL)")
    clause = " AND ".join(where)
    order = (
        "ORDER BY (severity IS NULL), severity DESC, occurred_at DESC"
        if (req.q("sort") or "severity") == "severity"
        else "ORDER BY occurred_at DESC"
    )
    limit = max(1, min(req.qi("limit", 200), 1000))
    offset = max(0, req.qi("offset", 0))
    conn = _connect()
    try:
        total = conn.execute(f"SELECT COUNT(*) AS c FROM entries WHERE {clause}", params).fetchone()["c"]
        rows = conn.execute(
            f"SELECT * FROM entries WHERE {clause} {order} LIMIT ? OFFSET ?",
            params + [limit, offset],
        ).fetchall()
    finally:
        conn.close()
    return {"items": [db.row_to_dict(r) for r in rows], "total": total}


@router.route("DELETE", "/api/entries")
def delete_all_entries(req: Request):
    conn = _connect()
    try:
        deleted = conn.execute("DELETE FROM entries").rowcount
        conn.commit()
    finally:
        conn.close()
    return {"ok": True, "deleted": deleted}


@router.route("GET", r"/api/entries/(?P<entry_id>\d+)")
def get_entry(req: Request):
    return _one(int(req.params["entry_id"]))


@router.route("PATCH", r"/api/entries/(?P<entry_id>\d+)")
def patch_entry(req: Request):
    entry_id = int(req.params["entry_id"])
    _one(entry_id)
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
        conn.execute(f"UPDATE entries SET {sets} WHERE id = ?", list(fields.values()) + [entry_id])
        conn.commit()
    finally:
        conn.close()
    return _one(entry_id)


@router.route("DELETE", r"/api/entries/(?P<entry_id>\d+)")
def delete_entry(req: Request):
    entry_id = int(req.params["entry_id"])
    _one(entry_id)
    conn = _connect()
    try:
        conn.execute("DELETE FROM entries WHERE id = ?", (entry_id,))
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
    _one(entry_id)
    body = req.json()
    relief = body.get("relief")
    if relief not in (None, "", "better", "same", "worse"):
        raise ApiError(422, "relief must be better / same / worse")
    conn = _connect()
    try:
        conn.execute(
            "UPDATE entries SET intervention_completed = ?, relief = ?, updated_at = ? WHERE id = ?",
            (1 if body.get("completed", True) else 0, relief or None, db.now_iso(), entry_id),
        )
        conn.commit()
    finally:
        conn.close()
    return _one(entry_id)


# --------------------------------------------------------------------------- #
# 总结
# --------------------------------------------------------------------------- #


@router.route("GET", "/api/stats/basic")
def stats_basic(req: Request):
    days = max(1, min(req.qi("days", 30), 3650))
    since = (db.now_utc() - timedelta(days=days)).isoformat(timespec="seconds")
    entries = _rows("SELECT * FROM entries WHERE occurred_at >= ? ORDER BY occurred_at", [since])
    payload = insights.build_basic(entries, req.lang)
    payload["window_days"] = days
    return payload


def _window_entries(days: int) -> list[dict]:
    since = (db.now_utc() - timedelta(days=days)).isoformat(timespec="seconds")
    return _rows("SELECT * FROM entries WHERE occurred_at >= ? ORDER BY occurred_at", [since])


def _redact(text: str) -> str:
    """错误详情会下发到前端，先把可能出现的 key 片段抹掉。"""
    return re.sub(r"sk-[A-Za-z0-9_\-]{4,}", "sk-***", text or "")


def _ai_state(entries: list[dict], days: int, lang: str, rules: dict, plan: str) -> dict:
    """这一屏归 AI 还是归规则引擎。

    状态直接决定界面长什么样：
      pro           免费层没有这项
      off           没配 key —— 全站静默用规则版
      insufficient  样本不够，先别硬凑结论
      pending       有数据但还没分析过，前端接着调 /api/insights/analyze
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

    narrative = ai.cache_get(ai.fingerprint(entries, days, lang, cfg["model"]))
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
    days = max(1, min(req.qi("days", 14), 3650))
    plan = db.get_setting("plan", "free")
    entries = _window_entries(days)
    payload = insights.build_insights(entries, days, req.lang, locked=(plan != "pro"))
    payload["ai"] = _ai_state(entries, days, req.lang, payload, plan)
    return payload


@router.route("POST", "/api/insights/analyze")
def analyze_insights(req: Request):
    """让模型重新分析这段时间的记录。

    这是唯一一个会真的阻塞几秒的接口；失败不走 500，而是带着错误状态返回规则版，
    前端因此永远有内容可显示。
    """
    plan = db.get_setting("plan", "free")
    if plan != "pro":
        raise ApiError(403, "AI 分析属于 Pro 层")

    cfg = ai.config()
    if not cfg["enabled"]:
        raise ApiError(503, "还没有配置 AI key：把 TETHER_AI_API_KEY 写进 .env")

    body = req.json()
    try:
        days = int(body.get("days") or 14)
    except (TypeError, ValueError):
        days = 14
    days = max(1, min(days, 3650))
    force = bool(body.get("force"))
    lang = req.lang

    entries = _window_entries(days)
    rules = insights.build_insights(entries, days, lang, locked=False)

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
    narrative = None if force else ai.cache_get(fingerprint)
    from_cache = narrative is not None
    error = ""

    if narrative is None:
        try:
            narrative = ai.analyze(entries, days, lang, rules)
            ai.cache_put(fingerprint, narrative, cfg["model"])
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
# 账户
# --------------------------------------------------------------------------- #


def _account_payload() -> dict:
    plan = db.get_setting("plan", "free")
    total = _rows("SELECT COUNT(*) AS c FROM entries")[0]["c"]
    return {
        "plan": plan,
        "pro": plan == "pro",
        "region": db.get_setting("region", "US"),
        "lang": db.get_setting("lang", "zh"),
        "entries": total,
        "pricing": PRICING,
        "note": {
            "zh": "演示环境：订阅状态仅保存在本地，不涉及真实扣款。",
            "en": "Demo build: subscription state is stored locally. No real payment happens.",
        },
    }


@router.route("GET", "/api/account")
def get_account(req: Request):
    return _account_payload()


@router.route("POST", "/api/account/subscription")
def set_subscription(req: Request):
    plan = req.json().get("plan")
    if plan not in ("free", "pro"):
        raise ApiError(422, "plan must be free or pro")
    db.set_setting("plan", plan)
    return _account_payload()


@router.route("PATCH", "/api/account/settings")
def update_settings(req: Request):
    body = req.json()
    region = body.get("region")
    if region is not None:
        if region not in content.REGION_KEYS:
            raise ApiError(422, "unknown region")
        db.set_setting("region", region)
    lang = body.get("lang")
    if lang is not None:
        if lang not in ("zh", "en"):
            raise ApiError(422, "lang must be zh or en")
        db.set_setting("lang", lang)
    return _account_payload()


@router.route("GET", "/api/crisis")
def crisis(req: Request):
    return content.crisis_for(req.q("region") or "US", req.lang)


# --------------------------------------------------------------------------- #
# 导出
# --------------------------------------------------------------------------- #


@router.route("GET", "/api/export")
def export(req: Request):
    rows = _rows("SELECT * FROM entries ORDER BY occurred_at DESC")
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
                    r["cause"],
                    r["note"],
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
    tz_offset = max(-840, min(req.qi("tz_offset", 480), 840))
    reset = req.qb("reset", True)
    conn = _connect()
    try:
        if reset:
            conn.execute("DELETE FROM entries")
        conn.commit()
    finally:
        conn.close()
    for row in seed.build_rows(tz_offset):
        _insert_entry(row)
    total = _rows("SELECT COUNT(*) AS c FROM entries")[0]["c"]
    return {"seeded": True, "entries": total}


# --------------------------------------------------------------------------- #
# 分发
# --------------------------------------------------------------------------- #


def dispatch(req: Request) -> Response:
    try:
        handler, params = router.match(req.method, req.path)
        req.params = params
        result = handler(req)
        if isinstance(result, Response):
            return result
        return Response(payload=result)
    except ApiError as exc:
        return Response(status=exc.status, payload={"error": exc.message})
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        return Response(status=500, payload={"error": "internal server error"})


def parse_query(query_string: str) -> dict[str, list[str]]:
    return urllib.parse.parse_qs(query_string, keep_blank_values=True)
