"""公开元数据、启动载荷、导入与健康检查。"""

from __future__ import annotations

from ... import __version__, ai, content, db

from ..core import ApiError, Request, router
from ..helpers import _account_payload, _dates, _entry_payload, _insert_entry, _rows, _session

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
    added = 0
    with _session() as conn:
        conn.execute("BEGIN IMMEDIATE")
        fields = ("occurred_at", "emotion", "severity", "cause", "note", "tz_offset", "intervention_completed", "relief")
        times = [row["occurred_at"] for row in rows]
        wanted = {tuple(row[field] for field in fields) for row in rows}
        existing = {key for row in conn.execute(
            "SELECT " + ", ".join(fields) + " FROM entries INDEXED BY idx_entries_time WHERE user_id = ? AND occurred_at BETWEEN ? AND ?",
            (uid, min(times), max(times)),
        ) if (key := tuple(row)) in wanted}
        for row in rows:
            key = tuple(row[field] for field in fields)
            if key not in existing:
                _insert_entry(row, uid, conn)
                existing.add(key)
                added += 1
        conn.commit()
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
    # False 表示账号语言只是默认值：前端应回落到中文，而不是当作用户的选择。
    payload["lang_set"] = bool(user.get("lang_set")) if user else False
    payload["has_data"] = (
        bool(_rows("SELECT id FROM entries WHERE user_id = ? LIMIT 1", [user["id"]])) if user else False
    )
    payload["ai"] = ai.public_config()
    return payload