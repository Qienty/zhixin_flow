"""表达空间里的两件 AI 事：帮我理清、想想下一步。

两条接口都**不落库**：不入 entries、不进 ai_cache、不写日志，模型只读一次就丢。
免费账号每天各 1 次，Pro 不占这条额度；两者都共用 analyze 的并发与冷却限制。
"""
from __future__ import annotations

from ... import ai, limits
from ..core import ApiError, Request, router
from ..helpers import _redact

EXPRESSION_TEXT_LIMIT = 500  # 与表达空间正文同一条上限
FREE_DAILY_LIMIT = 1


def _expression_text(req: Request) -> str:
    """取正文：类型、长度都按表达空间的规则来。"""
    value = req.json().get("text")
    if not isinstance(value, str):
        raise ApiError(400, "text must be a string")
    text = value.strip()
    if not text:
        raise ApiError(422, "先写一点想说的内容" if req.lang == "zh" else "Write something first")
    if len(text) > EXPRESSION_TEXT_LIMIT:
        raise ApiError(422, "内容最多 500 字" if req.lang == "zh" else "Keep it within 500 characters")
    return text


def _guard(req: Request) -> None:
    if not ai.config()["enabled"]:
        message = "尚未配置 AI 服务" if req.lang == "zh" else "AI service is not configured"
        raise ApiError(503, message)


def _call(req: Request, work):
    """共用：额度 → 并发槽 → 调模型 → 统一错误文案。失败时退还额度。"""
    tz_offset = req.qi("tz_offset", 0, -840, 840)
    quota = limits.expression_attempt(req.user["id"], req.user["plan"], tz_offset, FREE_DAILY_LIMIT)
    with limits.analysis_slot(req.user["id"]):
        try:
            result = work()
        except ai.AIError as exc:
            limits.expression_refund(req.user["id"], tz_offset)
            message = _redact(str(exc))[:200] or ("AI 暂时不可用" if req.lang == "zh" else "AI is temporarily unavailable")
            raise ApiError(502, message) from exc
    result["quota"] = quota
    return result


@router.route("POST", "/api/expression/clarify")
def clarify_expression(req: Request):
    """把用户刚写的一段话整理成：事实、可能的需要、情绪、一个追问。"""
    text = _expression_text(req)
    _guard(req)
    return _call(req, lambda: ai.clarify(text, req.lang))


@router.route("POST", "/api/expression/next-steps")
def next_steps(req: Request):
    """用户说想做点什么，给 1—3 个今天能做完的小步骤。"""
    text = _expression_text(req)
    _guard(req)
    return _call(req, lambda: ai.suggest_steps(text, req.lang))
