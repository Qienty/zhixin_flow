"""注册登录、会话、密码、兑换码与账号设置。"""

from __future__ import annotations

from typing import Any

from ... import auth, content, db, limits

from ..core import ApiError, Request, Response, router
from ..helpers import _account_payload, _clean_password, _clean_text, _session_response

# --------------------------------------------------------------------------- #
# 账号 / 登录
# --------------------------------------------------------------------------- #


@router.route("POST", "/api/auth/register")
def register(req: Request):
    limits.registration_attempt(req.client_ip)
    body = req.json()
    user = auth.create_user(_clean_text(body.get("username"), 24), _clean_password(body.get("password")))
    return _session_response(user, req)


@router.route("POST", "/api/auth/login")
def login(req: Request):
    body = req.json()
    username = _clean_text(body.get("username"), 24)
    password = _clean_password(body.get("password"))
    user = auth.authenticate_limited(req.client_ip, username, password)
    return _session_response(user, req)


@router.route("POST", "/api/auth/logout")
def logout(req: Request):
    # 壳内用 Bearer、网页用 Cookie，退出一律销毁当前这条会话。
    for token in (req.token, req.sid):
        if token:
            auth.delete_session(token)
    resp = Response(payload={"ok": True})
    resp.headers["Set-Cookie"] = f"{auth.COOKIE_NAME}=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0"
    return resp


@router.route("GET", "/api/auth/me")
def whoami(req: Request):
    if not req.user:
        return {"logged_in": False, "account": None}
    return {"logged_in": True, "account": _account_payload(req.user)}


@router.route("POST", "/api/auth/password")
def change_password(req: Request):
    if not req.user:
        raise ApiError(401, "login required")
    body = req.json()
    old = body.get("old_password") or ""
    new = body.get("new_password") or ""
    auth.authenticate_limited(req.client_ip, req.user["username"], _clean_password(old))
    auth.set_password(req.user["id"], new)
    # 改密码会踢掉所有会话，这里立刻补一个新的，免得自己被登出
    return _session_response(req.user, req)


@router.route("POST", "/api/account/redeem")
def redeem_code(req: Request):
    code = req.json().get("code") or ""
    # 非字符串（例如 {"code": 123}）会走到 .strip() 抛 AttributeError，直接 400 挡住
    if not isinstance(code, str):
        raise ApiError(400, "兑换码格式不正确")
    user = auth.redeem(req.user["id"], code)
    return {"account": _account_payload(user), "ok": True}


@router.route("GET", "/api/account")
def get_account(req: Request):
    return _account_payload(req.user)


@router.route("PATCH", "/api/account/settings")
def update_settings(req: Request):
    body = req.json()
    fields: dict[str, Any] = {}
    region = body.get("region")
    if region is not None:
        if region not in content.REGION_KEYS:
            raise ApiError(422, "unknown region")
        fields["region"] = region
    lang = body.get("lang")
    if lang is not None:
        if lang not in ("zh", "en"):
            raise ApiError(422, "lang must be zh or en")
        fields["lang"] = lang
    user = auth.update_user(req.user["id"], **fields)
    return _account_payload(user)


@router.route("GET", "/api/crisis")
def crisis(req: Request):
    fallback = req.user["region"] if req.user else db.DEFAULTS["region"]
    return content.crisis_for(req.q("region") or fallback, req.lang)