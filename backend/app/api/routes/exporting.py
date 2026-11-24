"""JSON / CSV 导出。"""

from __future__ import annotations

import csv
import io

from ... import db

from ..core import Request, Response, router
from ..helpers import _csv_cell, _rows


# --------------------------------------------------------------------------- #
# 导出
# --------------------------------------------------------------------------- #


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
            headers={"Content-Disposition": 'attachment; filename="zhixinyuanliu-entries.csv"'},
            content_type="text/csv; charset=utf-8",
        )
    return Response(
        payload={"exported_at": db.now_iso(), "count": len(rows), "entries": rows},
        headers={"Content-Disposition": 'attachment; filename="zhixinyuanliu-entries.json"'},
    )