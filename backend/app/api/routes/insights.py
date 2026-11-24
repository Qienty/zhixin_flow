"""统计、规则归因与 AI 分析。"""

from __future__ import annotations


from typing import Any

from ... import ai, insights, limits

from ..core import ApiError, Request, WINDOW_DAYS, router
from ..helpers import _dates, _fixed_window, _rows, _redact

# --------------------------------------------------------------------------- #
# 总结
# --------------------------------------------------------------------------- #


@router.route("GET", "/api/stats/basic")
def stats_basic(req: Request):
    _fixed_window(req)
    entries = _window_entries(req)
    payload = insights.build_basic(entries, req.lang)
    payload["window_days"] = WINDOW_DAYS
    daily = {}
    for entry in entries:
        if entry["severity"]:
            daily[entry["local_date"]] = max(daily.get(entry["local_date"], 0), entry["severity"])
    payload["daily_max"] = daily
    return payload


def _window_entries(req: Request) -> list[dict]:
    since, today = _dates(req)
    entries = _rows(
        "SELECT * FROM entries WHERE user_id = ? AND local_date BETWEEN ? AND ? ORDER BY occurred_at",
        [req.user["id"], since, today],
    )
    for entry in entries:
        entry["_window_today"] = today
    return entries


def _rules(req: Request, entries: list[dict], locked: bool) -> dict:
    return insights.build_insights(entries, WINDOW_DAYS, req.lang, locked=locked, today=_dates(req)[1])


def _ai_state(entries: list[dict], days: int, lang: str, rules: dict, plan: str, user_id: int) -> dict:
    """这一屏归 AI 还是归规则引擎。

    状态直接决定界面长什么样：
      pro           免费层没有这项
      off           没配 key —— 全站静默用规则版
      insufficient  样本不够，先别硬凑结论
      pending       有数据但还没分析过，等待用户主动点击分析
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

    narrative = ai.cache_get(ai.fingerprint(entries, days, lang, cfg["model"]), user_id)
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
        "reminders": narrative.get("reminders") or [],
    }


@router.route("GET", "/api/insights")
def get_insights(req: Request):
    _fixed_window(req)
    days = WINDOW_DAYS
    plan = req.user["plan"]
    entries = _window_entries(req)
    payload = _rules(req, entries, locked=(plan != "pro"))
    payload["ai"] = _ai_state(entries, days, req.lang, payload, plan, req.user["id"])
    return payload


@router.route("POST", "/api/insights/analyze")
def analyze_insights(req: Request):
    """让模型重新分析这段时间的记录。

    这是唯一一个会真的阻塞几秒的接口；失败不走 500，而是带着错误状态返回规则版，
    前端因此永远有内容可显示。
    """
    plan = req.user["plan"]
    if plan != "pro":
        raise ApiError(403, "AI 分析属于 Pro 层")

    cfg = ai.config()
    if not cfg["enabled"]:
        raise ApiError(503, "还没有配置 AI key：把 TETHER_AI_API_KEY 写进 .env")

    body = req.json()
    _fixed_window(req)
    if "days" in body and (type(body["days"]) is not int or body["days"] != WINDOW_DAYS):
        raise ApiError(400, "days must be 14")
    days = WINDOW_DAYS
    force = bool(body.get("force"))
    lang = req.lang

    revision = ai.data_revision(req.user["id"])
    entries = _window_entries(req)
    rules = _rules(req, entries, locked=False)

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
    narrative = None if force else ai.cache_get(fingerprint, req.user["id"])
    from_cache = narrative is not None
    error = ""

    if narrative is None:
        try:
            with limits.analysis_slot(req.user["id"]):
                narrative = ai.analyze(entries, days, lang, rules)
                if not ai.cache_put(fingerprint, narrative, cfg["model"], req.user["id"], revision):
                    raise ApiError(409, "记录已变化，请刷新后重新分析" if lang == "zh" else "Entries changed. Refresh before analyzing again.")
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
        "reminders": narrative.get("reminders") or [],
    }
    return rules