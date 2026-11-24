"""缓解转盘清单。"""

from __future__ import annotations

from ... import content, db

from ..core import ApiError, Request, router
from ..helpers import _clean_text, _rows, _session

# --------------------------------------------------------------------------- #
# 缓解转盘
# --------------------------------------------------------------------------- #


def _wheel_items(user_id: int) -> list[dict]:
    return _rows(
        "SELECT id, label FROM wheel_items WHERE user_id = ? ORDER BY sort, id", [user_id]
    )


def _wheel_seed(user_id: int, lang: str) -> None:
    now = db.now_iso()
    with _session() as conn:
        for index, label in enumerate(content.wheel_defaults(lang)):
            conn.execute(
                "INSERT INTO wheel_items(user_id, label, sort, created_at) VALUES(?, ?, ?, ?)",
                (user_id, label, index, now),
            )
        conn.commit()


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
        if not isinstance(item, (str, dict)):
            raise ApiError(422, "each item must be a string or an object with a string label")
        text = item if isinstance(item, str) else item.get("label")
        if not isinstance(text, str):
            raise ApiError(422, "label must be a string")
        cleaned = _clean_text(text, limit["label"])
        if cleaned:
            labels.append(cleaned)

    if len(labels) < limit["min"]:
        raise ApiError(422, f"至少留 {limit['min']} 项，不然转起来没意义")
    if len(labels) > limit["max"]:
        raise ApiError(422, f"最多 {limit['max']} 项，再多就看不清了")

    user_id = req.user["id"]
    now = db.now_iso()
    with _session() as conn:
        conn.execute("DELETE FROM wheel_items WHERE user_id = ?", (user_id,))
        for index, label in enumerate(labels):
            conn.execute(
                "INSERT INTO wheel_items(user_id, label, sort, created_at) VALUES(?, ?, ?, ?)",
                (user_id, label, index, now),
            )
        conn.commit()
    return {"items": _wheel_items(user_id), "limits": limit}