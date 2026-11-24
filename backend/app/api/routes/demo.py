"""演示数据。"""

from __future__ import annotations

from ... import seed

from ..core import ApiError, Request, router
from ..helpers import _insert_entry, _rows, _session

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
    with _session() as conn:
        for row in seed.build_rows(tz_offset):
            _insert_entry(row, uid, conn)
        conn.commit()
    total = _rows("SELECT COUNT(*) AS c FROM entries WHERE user_id = ?", [uid])[0]["c"]
    return {"seeded": True, "entries": total}