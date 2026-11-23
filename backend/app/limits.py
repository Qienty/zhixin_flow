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

    REASONS = {"in_flight", "cooldown", "capacity", "ip_window", "global_window"}

    def __init__(self, seconds, message="Too many requests. Please try again later.", reason=""):
        super().__init__(message)
        self.retry_after = max(1, math.ceil(seconds))
        self.reason = reason if reason in self.REASONS else ""


_lock = threading.RLock()
_analysis_active = set()
_analysis_starts = {}
_registrations = {}
_registration_global = []


def reset():
    """For process/test startup only, never on logout."""
    with _lock:
        _analysis_active.clear()
        _analysis_starts.clear()
        _registrations.clear()
        _registration_global.clear()


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
