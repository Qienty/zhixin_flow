"""自助练习：目录、开始、保存、汇总与每周证据卡。"""

from __future__ import annotations

import json
import re

from datetime import datetime, timedelta

from ... import db, practices

from ..core import ApiError, Request, Response, WINDOW_DAYS, router
from ..helpers import _clean_text, _dates, _normalize_time, _offset, _session

# --------------------------------------------------------------------------- #
# 自助练习（独立会话，不写入 AI 分析材料）
# --------------------------------------------------------------------------- #


def _practice_score(value):
    if value is None:
        return None
    if type(value) is not int or not 1 <= value <= 5:
        raise ApiError(400, "score must be an integer between 1 and 5 or null")
    return value


def _practice_key(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{8,80}", value):
        raise ApiError(400, "a stable request id of 8–80 letters, digits, _ or - is required")
    return value


def _practice_one(conn, session_id, user_id):
    if not 1 <= session_id <= 2**63 - 1:
        raise ApiError(404, "practice session not found")
    row = conn.execute("SELECT * FROM practice_sessions WHERE id = ? AND user_id = ?",
                       (session_id, user_id)).fetchone()
    if row is None:
        raise ApiError(404, "practice session not found")
    return dict(row)


def _practice_payload(row, lang, detail=True):
    data = dict(row)
    snapshot = json.loads(data.pop("content_snapshot"))
    data["practice"] = practices.localize(snapshot, lang)
    data["responses"] = json.loads(data["responses"])
    for key in ("user_id", "client_id", "last_request_id"):
        data.pop(key, None)
    if not detail:
        data["practice"].pop("steps", None)
        data.pop("responses", None)
        data.pop("note", None)
    return data


@router.route("GET", "/api/practices")
def practice_catalog(req: Request):
    return {"items": practices.catalog(req.lang)}


@router.route("POST", "/api/practice-sessions")
def start_practice(req: Request):
    body = req.json()
    if set(body) - {"practice_id", "client_id", "entry_id", "before_score", "tz_offset"}:
        raise ApiError(400, "unknown practice field")
    practice_id = body.get("practice_id")
    if not isinstance(practice_id, str) or practice_id not in practices.PRACTICES:
        raise ApiError(400, "unknown practice")
    client_id = _practice_key(body.get("client_id"))
    score = _practice_score(body.get("before_score"))
    offset = _offset(body.get("tz_offset"))
    entry_id = body.get("entry_id")
    if entry_id is not None and (type(entry_id) is not int or not 1 <= entry_id <= 2**63 - 1):
        raise ApiError(400, "invalid entry id")
    with _session() as conn:
        conn.execute("BEGIN IMMEDIATE")
        existing = conn.execute("SELECT * FROM practice_sessions WHERE user_id = ? AND client_id = ?",
                                (req.user["id"], client_id)).fetchone()
        if existing:
            if (existing["practice_id"] != practice_id or existing["before_score"] != score
                    or existing["tz_offset"] != offset
                    or (existing["entry_id"] is not None and existing["entry_id"] != entry_id)):
                raise ApiError(409, "request id already used for a different practice")
            return _practice_payload(existing, req.lang)
        if entry_id is not None and not conn.execute(
                "SELECT id FROM entries WHERE id = ? AND user_id = ?", (entry_id, req.user["id"])).fetchone():
            raise ApiError(404, "entry not found")
        snapshot = practices.PRACTICES[practice_id]
        started_at, local_date, _ = _normalize_time(None, offset)
        cur = conn.execute(
            """INSERT INTO practice_sessions
               (user_id, client_id, practice_id, content_version, content_snapshot, entry_id,
                before_score, started_at, local_date, tz_offset, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (req.user["id"], client_id, practice_id, snapshot["version"],
             json.dumps(snapshot, ensure_ascii=False), entry_id, score, started_at, local_date, offset, started_at))
        result = _practice_payload(_practice_one(conn, cur.lastrowid, req.user["id"]), req.lang)
        conn.commit()
        return Response(status=201, payload=result)


@router.route("GET", "/api/practice-sessions")
def list_practice_sessions(req: Request):
    limit = req.qi("limit", 20, 1, 50)
    cursor = req.qi("cursor", 0, 1, 2**63 - 1)
    entry_id = req.qi("entry_id", 0, 1, 2**63 - 1)
    where, params = "user_id = ?", [req.user["id"]]
    if entry_id:
        where += " AND entry_id = ?"
        params.append(entry_id)
    with _session() as conn:
        total = conn.execute(f"SELECT COUNT(*) FROM practice_sessions WHERE {where}", params).fetchone()[0]
        if cursor:
            where += " AND id < ?"
            params.append(cursor)
        rows = conn.execute(f"SELECT * FROM practice_sessions WHERE {where} ORDER BY id DESC LIMIT ?",
                            [*params, limit + 1]).fetchall()
        return {"items": [_practice_payload(row, req.lang, False) for row in rows[:limit]],
                "total": total, "next_cursor": rows[limit - 1]["id"] if len(rows) > limit else None}


@router.route("GET", "/api/practice-summary")
def practice_summary(req: Request):
    since, today = _dates(req)
    with _session() as conn:
        rows = conn.execute(
            "SELECT status, before_score, after_score, relief FROM practice_sessions "
            "WHERE user_id = ? AND local_date BETWEEN ? AND ?", (req.user["id"], since, today)).fetchall()
        completed = [r for r in rows if r["status"] == "completed"]
        paired = [r for r in completed if r["before_score"] is not None and r["after_score"] is not None]
        return {"days": WINDOW_DAYS, "since": since, "until": today, "completed": len(completed),
                "stopped": sum(r["status"] == "stopped" for r in rows),
                "paired": len(paired),
                "before_average": round(sum(r["before_score"] for r in paired) / len(paired), 1) if paired else None,
                "after_average": round(sum(r["after_score"] for r in paired) / len(paired), 1) if paired else None,
                "feedback": {key: sum(r["relief"] == key for r in completed) for key in ("better", "same", "worse")}}


@router.route("GET", "/api/weekly-card")
def weekly_card(req: Request):
    """这一周能核对的证据：记录、强度、练习前后评分，以及与前一周的对照。

    纯聚合，不调用模型：数字由代码算，文案由前端按当前语言拼。
    练习的 before/after 评分在这里第一次进入主流程，而不只是练习页的历史。
    """
    days = 7
    since, today = _dates(req, days)
    start = datetime.fromisoformat(since).date()
    prev_since = (start - timedelta(days=days)).isoformat()
    prev_until = (start - timedelta(days=1)).isoformat()
    uid = req.user["id"]
    with _session() as conn:
        current = conn.execute(
            "SELECT COUNT(*) AS count, "
            "COALESCE(SUM(CASE WHEN severity >= 4 THEN 1 ELSE 0 END), 0) AS severe, "
            "COUNT(DISTINCT local_date) AS days "
            "FROM entries WHERE user_id = ? AND local_date BETWEEN ? AND ?",
            (uid, since, today)).fetchone()
        previous = conn.execute(
            "SELECT COUNT(*) AS count, COALESCE(SUM(CASE WHEN severity >= 4 THEN 1 ELSE 0 END), 0) AS severe "
            "FROM entries WHERE user_id = ? AND local_date BETWEEN ? AND ?",
            (uid, prev_since, prev_until)).fetchone()
        top = conn.execute(
            "SELECT emotion, COUNT(*) AS c FROM entries WHERE user_id = ? AND local_date BETWEEN ? AND ? "
            "GROUP BY emotion ORDER BY c DESC, emotion LIMIT 1",
            (uid, since, today)).fetchone()
        rows = conn.execute(
            "SELECT status, before_score, after_score, relief FROM practice_sessions "
            "WHERE user_id = ? AND local_date BETWEEN ? AND ?",
            (uid, since, today)).fetchall()
    completed = [row for row in rows if row["status"] == "completed"]
    paired = [row for row in completed if row["before_score"] is not None and row["after_score"] is not None]
    return {
        "days": days,
        "since": since,
        "until": today,
        "entries": {
            "count": current["count"],
            "severe": current["severe"],
            "days": current["days"],
            "top_emotion": top["emotion"] if top else None,
        },
        "previous": {"count": previous["count"], "severe": previous["severe"], "since": prev_since, "until": prev_until},
        "practices": {
            "completed": len(completed),
            "paired": len(paired),
            "before_average": round(sum(r["before_score"] for r in paired) / len(paired), 1) if paired else None,
            "after_average": round(sum(r["after_score"] for r in paired) / len(paired), 1) if paired else None,
            "drops": sum(1 for r in paired if r["after_score"] < r["before_score"]),
            "better": sum(r["relief"] == "better" for r in completed),
            "same": sum(r["relief"] == "same" for r in completed),
            "worse": sum(r["relief"] == "worse" for r in completed),
        },
    }


@router.route("GET", r"/api/practice-sessions/(?P<session_id>\d+)")
def get_practice_session(req: Request):
    with _session() as conn:
        return _practice_payload(_practice_one(conn, int(req.params["session_id"]), req.user["id"]), req.lang)


@router.route("PATCH", r"/api/practice-sessions/(?P<session_id>\d+)")
def update_practice_session(req: Request):
    body = req.json()
    if set(body) - {"request_id", "revision", "step_index", "status", "responses", "after_score", "relief", "note"}:
        raise ApiError(400, "unknown practice field")
    request_id = _practice_key(body.get("request_id"))
    if type(body.get("revision")) is not int or body["revision"] < 0:
        raise ApiError(400, "revision is required")
    session_id, uid = int(req.params["session_id"]), req.user["id"]
    with _session() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = _practice_one(conn, session_id, uid)
        # A lost response may be retried with the same request id after commit.
        if row["last_request_id"] == request_id:
            return _practice_payload(row, req.lang)
        if row["revision"] != body["revision"] or row["status"] != "in_progress":
            raise ApiError(409, "practice changed; reopen the saved session")
        definition = practices.localize(json.loads(row["content_snapshot"]), req.lang)
        step_index = body.get("step_index", row["step_index"])
        if type(step_index) is not int or not row["step_index"] <= step_index <= min(row["step_index"] + 1, len(definition["steps"])):
            raise ApiError(400, "invalid step progress")
        status = body.get("status", row["status"])
        if status not in ("in_progress", "completed", "stopped"):
            raise ApiError(400, "invalid practice status")
        if status == "completed" and step_index != len(definition["steps"]):
            raise ApiError(400, "finish the steps before completing a practice")
        responses = body.get("responses", json.loads(row["responses"]))
        allowed = {s["id"] for s in definition["steps"] if s.get("prompt")}
        if not isinstance(responses, dict) or set(responses) - allowed:
            raise ApiError(400, "invalid practice responses")
        responses = {key: _clean_text(value, 500) for key, value in responses.items()}
        after_score = _practice_score(body.get("after_score", row["after_score"]))
        relief = body.get("relief", row["relief"])
        if relief not in (None, "better", "same", "worse"):
            raise ApiError(400, "invalid practice feedback")
        note = _clean_text(body.get("note", row["note"]), 500)
        now = db.now_iso()
        conn.execute(
            """UPDATE practice_sessions SET status = ?, step_index = ?, responses = ?, after_score = ?,
               relief = ?, note = ?, finished_at = ?, updated_at = ?, revision = revision + 1, last_request_id = ?
               WHERE id = ? AND user_id = ?""",
            (status, step_index, json.dumps(responses, ensure_ascii=False), after_score, relief, note,
             now if status != "in_progress" else None, now, request_id, session_id, uid))
        result = _practice_payload(_practice_one(conn, session_id, uid), req.lang)
        conn.commit()
        return result


@router.route("DELETE", r"/api/practice-sessions/(?P<session_id>\d+)")
def delete_practice_session(req: Request):
    with _session() as conn:
        _practice_one(conn, int(req.params["session_id"]), req.user["id"])
        conn.execute("DELETE FROM practice_sessions WHERE id = ? AND user_id = ?",
                     (int(req.params["session_id"]), req.user["id"]))
        conn.commit()
        return {"deleted": True}


@router.route("GET", "/api/practice-export")
def export_practice_sessions(req: Request):
    with _session() as conn:
        rows = conn.execute("SELECT * FROM practice_sessions WHERE user_id = ? ORDER BY id DESC",
                            (req.user["id"],)).fetchall()
        return Response(payload={"exported_at": db.now_iso(), "count": len(rows),
                                 "practice_sessions": [_practice_payload(row, req.lang) for row in rows]},
                        headers={"Content-Disposition": 'attachment; filename="zhixinyuanliu-practices.json"'})
