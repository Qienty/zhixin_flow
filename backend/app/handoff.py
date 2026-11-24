"""就诊摘要：把一段时间的记录压成「专业人士 30 秒能读完」的一页纸。

和导出 CSV 的区别：导出的是一堆原始行，谁都得自己翻；这里只给结论性的事实
（频率、时段、强度、触发场景、练习前后变化、几条原话），并明确声明这不是
诊断、不是评估，只是用户自己记录的事实整理。

全部由规则计算，不调用模型：这样它免费、可离线、可核对。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from . import db, insights, practices

# 可选窗口：一次就诊通常带 2—4 周材料，90 天用于复诊或换医生
WINDOWS = (14, 30, 90)
MAX_HIGHLIGHTS = 4
CAUSE_CHARS = 40


def _label(emotion: str, lang: str) -> str:
    from .content import EMOTION_MAP

    meta = EMOTION_MAP.get(emotion) or {}
    table = meta.get(lang) or meta.get("zh") or {}
    return table.get("label") or emotion


def _hour_bands(entries: list[dict], lang: str) -> list[dict]:
    counts = {}
    for entry in entries:
        key = insights.slot_of(entry["local_hour"])
        counts[key] = counts.get(key, 0) + 1
    bands = []
    for key, _, _, short, rng in insights.SLOTS:
        count = counts.get(key, 0)
        if not count:
            continue
        bands.append(
            {
                "key": key,
                "label": short.get(lang) or short["zh"],
                "range": rng.get(lang) or rng["zh"],
                "count": count,
            }
        )
    bands.sort(key=lambda item: -item["count"])
    return bands


def _practice_block(conn, user_id: int, since: str, today: str, lang: str) -> dict:
    rows = conn.execute(
        "SELECT status, before_score, after_score, relief FROM practice_sessions "
        "WHERE user_id = ? AND local_date BETWEEN ? AND ?",
        (user_id, since, today),
    ).fetchall()
    completed = [row for row in rows if row["status"] == "completed"]
    paired = [row for row in completed if row["before_score"] is not None and row["after_score"] is not None]
    names = []
    if completed:
        rows_named = conn.execute(
            "SELECT practice_id, COUNT(*) AS c FROM practice_sessions "
            "WHERE user_id = ? AND local_date BETWEEN ? AND ? AND status = 'completed' "
            "GROUP BY practice_id ORDER BY c DESC",
            (user_id, since, today),
        ).fetchall()
        for row in rows_named:
            meta = practices.PRACTICES.get(row["practice_id"])
            if not meta:
                continue
            table = meta.get(lang) or meta.get("zh") or {}
            names.append({"title": table.get("title") or row["practice_id"], "count": row["c"]})
    return {
        "completed": len(completed),
        "paired": len(paired),
        "before_average": round(sum(r["before_score"] for r in paired) / len(paired), 1) if paired else None,
        "after_average": round(sum(r["after_score"] for r in paired) / len(paired), 1) if paired else None,
        "drops": sum(1 for r in paired if r["after_score"] < r["before_score"]),
        "feedback": {key: sum(r["relief"] == key for r in completed) for key in ("better", "same", "worse")},
        "titles": names[:3],
    }


def build(conn, user_id: int, days: int, lang: str, tz_offset: int) -> dict:
    offset = timezone(timedelta(minutes=tz_offset))
    today_date = db.now_utc().astimezone(offset).date()
    since = (today_date - timedelta(days=days - 1)).isoformat()
    today = today_date.isoformat()
    week_start = (today_date - timedelta(days=6)).isoformat()
    prev_start = (today_date - timedelta(days=13)).isoformat()
    prev_end = (today_date - timedelta(days=7)).isoformat()

    entries = [
        dict(row)
        for row in conn.execute(
            "SELECT * FROM entries WHERE user_id = ? AND local_date BETWEEN ? AND ? ORDER BY occurred_at",
            (user_id, since, today),
        ).fetchall()
    ]
    basic = insights.build_basic(entries, lang)

    counts = conn.execute(
        "SELECT COUNT(*) AS count, COALESCE(SUM(CASE WHEN severity >= 4 THEN 1 ELSE 0 END), 0) AS severe "
        "FROM entries WHERE user_id = ? AND local_date BETWEEN ? AND ?",
        (user_id, week_start, today),
    ).fetchone()
    previous = conn.execute(
        "SELECT COUNT(*) AS count, COALESCE(SUM(CASE WHEN severity >= 4 THEN 1 ELSE 0 END), 0) AS severe "
        "FROM entries WHERE user_id = ? AND local_date BETWEEN ? AND ?",
        (user_id, prev_start, prev_end),
    ).fetchone()

    highlights = []
    for entry in sorted(entries, key=lambda e: (-(e.get("severity") or 0), e["occurred_at"]))[:MAX_HIGHLIGHTS]:
        highlights.append(
            {
                "date": entry["local_date"],
                "emotion": _label(entry["emotion"], lang),
                "severity": entry.get("severity"),
                "cause": (entry.get("cause") or "").strip()[:CAUSE_CHARS],
            }
        )

    return {
        "days": days,
        "since": since,
        "until": today,
        "generated_at": db.now_iso(),
        "entries": basic,
        "hours": _hour_bands(entries, lang),
        "week": {
            "entries": counts["count"],
            "severe": counts["severe"],
            "previous_entries": previous["count"],
            "previous_severe": previous["severe"],
        },
        "practices": _practice_block(conn, user_id, since, today, lang),
        "highlights": highlights,
        "notes": {
            "zh": "这是你自己记录的整理，不是诊断、不是评估，也不能替代专业判断。可以按你的意愿删掉任何部分再带去。",
            "en": "This is a summary of your own records. It is not a diagnosis or an assessment and cannot replace professional judgement. Remove anything you do not want to share.",
        }.get(lang)
        or "",
    }
