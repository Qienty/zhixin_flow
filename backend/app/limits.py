"""Bounded process-local limits; gateways must share limits across workers."""
from contextlib import contextmanager
import math
import os
import threading
import time


def setting(name, default, low=1, high=3600):
    try:
        return max(low, min(high, int(os.environ.get(name, default))))
    except (TypeError, ValueError):
        return default


class LimitError(Exception):
    """限流。reason 给接口层用来选本地化文案，message 只作兜底。"""

    REASONS = {"in_flight", "cooldown", "capacity", "ip_window", "global_window", "daily"}

    def __init__(self, seconds, message="Too many requests. Please try again later.", reason=""):
        super().__init__(message)
        self.retry_after = max(1, math.ceil(seconds))
        self.reason = reason if reason in self.REASONS else ""


_lock = threading.RLock()
_analysis_active = set()
_analysis_starts = {}
_registrations = {}
_registration_global = []
_daily_usage = {}


def reset():
    """For process/test startup only, never on logout."""
    with _lock:
        _analysis_active.clear()
        _analysis_starts.clear()
        _registrations.clear()
        _registration_global.clear()
        _daily_usage.clear()


def expression_attempt(user_id, plan, tz_offset=0, limit=1):
    """免费账号每天 limit 次「让 AI 帮我理清」；Pro 不占这条额度。

    按客户端本地日期计（同一套 tz_offset 语义），进程内计数，重启清零。
    """
    if plan == "pro":
        return {"limit": None, "used": 0, "remaining": None}
    now = time.time() + tz_offset * 60
    day = time.strftime("%Y-%m-%d", time.gmtime(now))
    with _lock:
        if len(_daily_usage) >= 4096:
            for key in [k for k, (stored, _) in _daily_usage.items() if stored != day]:
                del _daily_usage[key]
        stored, used = _daily_usage.get(user_id, (day, 0))
        if stored != day:
            used = 0
        if used >= limit:
            until_midnight = 86400 - (now % 86400)
            raise LimitError(until_midnight, reason="daily")
        _daily_usage[user_id] = (day, used + 1)
        return {"limit": limit, "used": used + 1, "remaining": max(0, limit - used - 1)}


def expression_refund(user_id, tz_offset=0):
    """模型调用失败时把刚占掉的额度退回去，别让一次失败吃掉当天的机会。"""
    now = time.time() + tz_offset * 60
    day = time.strftime("%Y-%m-%d", time.gmtime(now))
    with _lock:
        stored, used = _daily_usage.get(user_id, (day, 0))
        if stored == day and used > 0:
            _daily_usage[user_id] = (day, used - 1)


@contextmanager
def analysis_slot(user_id):
    interval = setting("TETHER_AI_MIN_INTERVAL", 60)
    capacity = setting("TETHER_AI_MAX_CONCURRENT", 4, high=64)
    now = time.monotonic()
    with _lock:
        for key, started in list(_analysis_starts.items()):
            if now - started >= interval and key not in _analysis_active:
                del _analysis_starts[key]
        if user_id in _analysis_active:
            raise LimitError(5, "An analysis is already running for this account.", reason="in_flight")
        if user_id in _analysis_starts:
            raise LimitError(interval - (now - _analysis_starts[user_id]), reason="cooldown")
        if len(_analysis_active) >= capacity or len(_analysis_starts) >= 4096:
            raise LimitError(5, "Analysis capacity is busy. Please try again later.", reason="capacity")
        _analysis_active.add(user_id)
        _analysis_starts[user_id] = now
    try:
        yield
    finally:
        with _lock:
            _analysis_active.discard(user_id)


def registration_attempt(ip):
    now = time.monotonic()
    window, per_ip = 300, setting("TETHER_REGISTER_LIMIT", 10, high=100)
    with _lock:
        for key, stamps in list(_registrations.items()):
            live = [stamp for stamp in stamps if now - stamp < window]
            if live:
                _registrations[key] = live
            else:
                del _registrations[key]
        _registration_global[:] = [stamp for stamp in _registration_global if now - stamp < 60]
        stamps = _registrations.get(ip, [])
        if len(stamps) >= per_ip:
            raise LimitError(window - (now - stamps[0]), reason="ip_window")
        if len(_registration_global) >= 40 or (ip not in _registrations and len(_registrations) >= 4096):
            raise LimitError(60, reason="global_window")
        _registrations.setdefault(ip, []).append(now)
        _registration_global.append(now)
