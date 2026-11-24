"""请求/响应类型、路由表与分发。"""

from __future__ import annotations

import json
import re
import traceback
import urllib.parse

from typing import Any, Callable

from .. import auth, limits

# 没有订阅、没有支付：Pro 只能靠兑换码开通（见 auth.redeem）

# 不需要登录就能访问的接口。登录页要拿 meta 才能渲染情绪与地区；
# 危机资源任何时候都该能打开，不能卡在登录后面。
PUBLIC_PATHS = {"/api/health", "/api/meta", "/api/crisis", "/api/bootstrap"}
WINDOW_DAYS = 14


# --------------------------------------------------------------------------- #
# 基础类型
# --------------------------------------------------------------------------- #


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        self.status = status
        self.message = message
        super().__init__(message)


class Request:
    def __init__(self, method: str, path: str, query: dict[str, list[str]], body: bytes, headers: dict[str, str]):
        self.method = method
        self.path = path
        self.query = query
        self.body = body
        self.headers = headers
        self.params: dict[str, str] = {}
        # 由 dispatch 填：当前登录用户 + 会话 token
        self.user: dict | None = None
        self.sid: str = ""
        self.token: str = ""
        self.client_ip: str = "unknown"

    def q(self, key: str, default: str | None = None) -> str | None:
        values = self.query.get(key)
        return values[0] if values else default

    def qi(self, key: str, default: int, low: int | None = None, high: int | None = None) -> int:
        raw = self.q(key)
        if raw is None:
            return default
        try:
            value = int(raw)
        except ValueError:
            raise ApiError(400, f"{key} must be an integer")
        if (low is not None and value < low) or (high is not None and value > high):
            raise ApiError(400, f"{key} must be between {low} and {high}")
        return value

    def qb(self, key: str, default: bool = False) -> bool:
        raw = self.q(key)
        if raw is None:
            return default
        if raw.lower() not in ("1", "true", "yes", "on", "0", "false", "no", "off"):
            raise ApiError(400, f"{key} must be a boolean")
        return raw.lower() in ("1", "true", "yes", "on")

    @property
    def lang(self) -> str:
        value = (self.q("lang") or "").lower()
        return value if value in ("zh", "en") else "zh"

    def json(self) -> dict:
        if not self.body:
            return {}
        try:
            data = json.loads(self.body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise ApiError(400, "invalid json body")
        if not isinstance(data, dict):
            raise ApiError(400, "json body must be an object")
        return data


class Response:
    def __init__(
        self,
        status: int = 200,
        payload: Any = None,
        raw: bytes | str | None = None,
        headers: dict[str, str] | None = None,
        content_type: str = "application/json; charset=utf-8",
    ):
        self.status = status
        self.payload = payload
        self.raw = raw
        self.headers = headers or {}
        self.content_type = content_type


# --------------------------------------------------------------------------- #
# 路由
# --------------------------------------------------------------------------- #


class Router:
    def __init__(self) -> None:
        self._routes: list[tuple[str, re.Pattern, Callable]] = []

    def route(self, method: str, pattern: str):
        def wrapper(fn: Callable):
            self._routes.append((method.upper(), re.compile(f"^{pattern}$"), fn))
            return fn

        return wrapper

    def match(self, method: str, path: str):
        allowed = []
        for m, rx, fn in self._routes:
            hit = rx.match(path)
            if hit:
                if m == method:
                    return fn, hit.groupdict()
                allowed.append(m)
        if allowed:
            raise ApiError(405, f"method {method} not allowed")
        raise ApiError(404, f"no route for {path}")


router = Router()


# --------------------------------------------------------------------------- #
# 分发
# --------------------------------------------------------------------------- #


def _cookie(req: Request, name: str) -> str:
    raw = req.headers.get("cookie") or ""
    for part in raw.split(";"):
        key, _, value = part.strip().partition("=")
        if key == name:
            return urllib.parse.unquote(value)
    return ""


def _bearer(req: Request) -> str:
    """原生壳用 Bearer token，网页继续用 Cookie。

    两种凭据走同一张 sessions 表：token 就是会话主键，所以退出、改密码、
    过期清理对两边同时生效，不需要第二套状态。
    """
    raw = (req.headers.get("authorization") or "").strip()
    if not raw:
        return ""
    scheme, _, value = raw.partition(" ")
    if scheme.lower() != "bearer":
        return ""
    token = value.strip()
    # token 是 token_urlsafe(32)：只接受这个长度区间，避免把垃圾数据当会话去查。
    return token if 20 <= len(token) <= 128 else ""


# 限流是 AI 功能最常见的可见失败：文案要跟着界面语言走，并明确还要等多久。
LIMIT_MESSAGES = {
    "zh": {
        "in_flight": "这个账号已经有一次分析在进行，等它结束再试。",
        "cooldown": "刚调用过模型，请再等 {s} 秒。",
        "capacity": "当前同时在跑的分析太多，请 {s} 秒后再试。",
        "ip_window": "尝试次数太多，请 {s} 秒后再试。",
        "global_window": "这一分钟注册请求过多，请 {s} 秒后再试。",
        "generic": "请求过于频繁，请 {s} 秒后再试。",
    },
    "en": {
        "in_flight": "An analysis is already running for this account. Try again when it finishes.",
        "cooldown": "The model was just called. Try again in {s} seconds.",
        "capacity": "Too many analyses are running right now. Try again in {s} seconds.",
        "ip_window": "Too many attempts. Try again in {s} seconds.",
        "global_window": "Too many sign-ups this minute. Try again in {s} seconds.",
        "generic": "Too many requests. Try again in {s} seconds.",
    },
}


def _limit_message(req: Request, exc: limits.LimitError) -> str:
    table = LIMIT_MESSAGES.get(getattr(req, "lang", "zh"), LIMIT_MESSAGES["zh"])
    template = table.get(exc.reason or "generic", table["generic"])
    return template.replace("{s}", str(exc.retry_after))


def dispatch(req: Request) -> Response:
    try:
        handler, params = router.match(req.method, req.path)
        # 先把会话解出来：公开接口也想知道「谁在访问」（比如 meta 要回该用户的 plan）
        # 原生壳无法可靠携带跨站 Cookie，所以优先用 Authorization: Bearer；网页仍走 Cookie。
        req.token = _bearer(req)
        req.sid = _cookie(req, auth.COOKIE_NAME)
        req.user = auth.user_by_token(req.token or req.sid)

        public = req.path in PUBLIC_PATHS or req.path.startswith("/api/auth/")
        if not public and not req.user:
            raise ApiError(401, "login required")

        req.params = params
        result = handler(req)
        if isinstance(result, Response):
            return result
        return Response(payload=result)
    except ApiError as exc:
        return Response(status=exc.status, payload={"error": exc.message})
    except limits.LimitError as exc:
        return Response(status=429, payload={"error": _limit_message(req, exc)}, headers={"Retry-After": str(exc.retry_after)})
    except auth.AuthError as exc:
        headers = {"Retry-After": str(exc.retry_after)} if hasattr(exc, "retry_after") else {}
        return Response(status=exc.status, payload={"error": exc.message}, headers=headers)
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        return Response(status=500, payload={"error": "internal server error"})


def parse_query(query_string: str) -> dict[str, list[str]]:
    return urllib.parse.parse_qs(query_string, keep_blank_values=True)