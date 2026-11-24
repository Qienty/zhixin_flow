"""情绪记录的增删改查与列表筛选。"""

from __future__ import annotations

import base64
import json

from typing import Any

from ... import ai, content, db, insights

from ..core import ApiError, Request, router
from ..helpers import _clean_text, _dates, _entry_payload, _insert_entry, _one, _session, _severity

# --------------------------------------------------------------------------- #
# 记录
# --------------------------------------------------------------------------- #


@router.route("POST", "/api/entries")
def create_entry(req: Request):
    body = req.json()
    uid = req.user["id"]
    row = _entry_payload(body)
    client_id = _clean_text(body.get("client_id"), 64) or None
    with _session() as conn:
        conn.execute("BEGIN IMMEDIATE")
        existing = conn.execute("SELECT id FROM entries WHERE user_id = ? AND client_id = ?", (uid, client_id)).fetchone() if client_id else None
        if existing:
            entry_id = existing["id"]
        else:
            entry_id = _insert_entry(row, uid, conn)
            conn.execute("UPDATE entries SET client_id = ? WHERE id = ?", (client_id, entry_id))
        conn.commit()
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
    with _session() as conn:
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
    with _session() as conn:
        # 只清当前账号自己的记录
        deleted = conn.execute("DELETE FROM entries WHERE user_id = ?", (req.user["id"],)).rowcount
        ai.cache_clear(req.user["id"], conn)
        conn.commit()
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
    with _session() as conn:
        conn.execute(
            f"UPDATE entries SET {sets} WHERE id = ? AND user_id = ?",
            list(fields.values()) + [entry_id, uid],
        )
        conn.commit()
    return _one(entry_id, uid)


@router.route("DELETE", r"/api/entries/(?P<entry_id>\d+)")
def delete_entry(req: Request):
    entry_id = int(req.params["entry_id"])
    uid = req.user["id"]
    _one(entry_id, uid)
    with _session() as conn:
        conn.execute("DELETE FROM entries WHERE id = ? AND user_id = ?", (entry_id, uid))
        conn.commit()
    return {"ok": True}