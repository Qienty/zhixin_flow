"""短引导内容、引导结果、就诊摘要与模型自检。"""

from __future__ import annotations

from ... import ai, content, db, handoff, limits

from ..core import ApiError, Request, router
from ..helpers import _one, _redact, _session

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
    with _session() as conn:
        conn.execute(
            "UPDATE entries SET intervention_completed = ?, relief = ?, updated_at = ? "
            "WHERE id = ? AND user_id = ?",
            (1 if body.get("completed", True) else 0, relief or None, db.now_iso(), entry_id, uid),
        )
        conn.commit()
    return _one(entry_id, uid)


@router.route("GET", "/api/handoff")
def handoff_summary(req: Request):
    """就诊摘要：把一段时间的记录压成一页纸，给咨询师或医生看。

    规则计算，不调用模型：免费、可离线、每一句都能回到原始记录核对。
    用户可以选择窗口（14 / 30 / 90 天），内容只属于当前账号。
    """
    days = req.qi("days", 30, 1, 3650)
    if days not in handoff.WINDOWS:
        days = min(handoff.WINDOWS, key=lambda value: abs(value - days))
    tz_offset = req.qi("tz_offset", 480, -840, 840)
    with _session() as conn:
        return handoff.build(conn, req.user["id"], days, req.lang, tz_offset)


@router.route("POST", "/api/ai/selftest")
def ai_selftest(req: Request):
    """部署/演示用的自检：一次最小模型调用，返回耗时与模型名。

    和其他模型调用共用同一套并发与冷却限制，避免它变成免费打模型的通道。
    """
    cfg = ai.config()
    if not cfg["enabled"]:
        message = "尚未配置 AI 服务" if req.lang == "zh" else "AI service is not configured"
        raise ApiError(503, message)
    with limits.analysis_slot(req.user["id"]):
        try:
            return ai.selftest()
        except ai.AIError as exc:
            message = _redact(str(exc))[:200] or ("AI 暂时不可用" if req.lang == "zh" else "AI is temporarily unavailable")
            raise ApiError(502, message) from exc