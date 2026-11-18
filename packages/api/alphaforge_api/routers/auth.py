"""登录接口：状态、登录、登出、改密。

会话凭证只走 HttpOnly Cookie，接口不返回任何 token —— 前端拿不到也就无从泄露。
`/status` 和 `/login` 是唯一两个不需要登录的接口（见 auth.PUBLIC_PATHS），
其余 `/api` 请求由 main.py 的统一守卫拦下。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from .. import auth
from ..deps import ok

router = APIRouter(prefix="/auth", tags=["auth"])

USERNAME_MAX = 32
PASSWORD_MIN = 6
PASSWORD_MAX = 128


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=USERNAME_MAX)
    password: str = Field(min_length=1, max_length=PASSWORD_MAX)


class PasswordIn(BaseModel):
    old_password: str = Field(min_length=1, max_length=PASSWORD_MAX)
    new_password: str = Field(min_length=PASSWORD_MIN, max_length=PASSWORD_MAX)


def _client(request: Request) -> tuple[str, str]:
    ip = request.client.host if request.client else ""
    return ip, request.headers.get("user-agent", "")


def _set_cookie(response: Response, sid: str) -> None:
    response.set_cookie(
        key=auth.COOKIE_NAME,
        value=sid,
        max_age=auth.SESSION_DAYS * 86400,
        httponly=True,          # 前端 JS 读不到，XSS 也偷不走
        samesite="lax",         # 挡住第三方站点发起的跨站 POST
        secure=False,           # 本地 http 部署；上 https 时置 1 即可
        path="/",
    )


@router.get("/status")
def status(request: Request):
    """登录页与路由守卫的第一问：我登录了吗、要不要强制改密。"""
    info = auth.status_info()
    user = auth.current_user(request.cookies.get(auth.COOKIE_NAME))
    return ok({
        "authenticated": user is not None,
        "username": info["username"],
        "must_change": info["must_change"] and user is not None,
        "session_days": info["session_days"],
        "auth_disabled": info["disabled"],
    })


@router.post("/login")
def login(payload: LoginIn, request: Request, response: Response):
    info = auth.status_info()
    remaining = auth.lock_remaining(payload.username)
    if remaining:
        raise HTTPException(status_code=429, detail=f"尝试过于频繁，请 {remaining} 秒后再试")

    # 用户名不一致时不提前返回，照样走一遍哈希，避免用响应时间试出账号是否存在
    name_ok = payload.username == info["username"]
    pass_ok = auth.verify_password(payload.password)
    if not (name_ok and pass_ok):
        auth.note_failure(payload.username)
        raise HTTPException(status_code=401, detail="用户名或密码错误")

    auth.clear_failures(payload.username)
    ip, ua = _client(request)
    _set_cookie(response, auth.create_session(ip, ua))
    return ok({"username": info["username"], "must_change": info["must_change"]})


@router.post("/logout")
def logout(request: Request, response: Response):
    auth.revoke(request.cookies.get(auth.COOKIE_NAME))
    response.delete_cookie(auth.COOKIE_NAME, path="/")
    return ok({"logged_out": True})


@router.post("/password")
def change_password(payload: PasswordIn, request: Request, response: Response):
    """改密。成功后作废旧会话并给当前浏览器换新的，不至于改完自己也被踢下线。"""
    user = auth.current_user(request.cookies.get(auth.COOKIE_NAME))
    if not user:
        raise HTTPException(status_code=401, detail="未登录或登录已过期")
    if not auth.verify_password(payload.old_password):
        raise HTTPException(status_code=400, detail="原密码不正确")
    if payload.new_password == payload.old_password:
        raise HTTPException(status_code=400, detail="新密码不能与原密码相同")
    if payload.new_password.lower() == user.lower():
        raise HTTPException(status_code=400, detail="新密码不能与登录账号相同")

    auth.set_password(payload.new_password)
    ip, ua = _client(request)
    _set_cookie(response, auth.create_session(ip, ua))
    return ok({"username": user, "must_change": False})
