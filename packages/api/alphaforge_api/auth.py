"""单用户登录：密码哈希、会话签发与接口守卫。

**为什么是 Cookie 会话而不是 JWT**
这个工作台的唯一客户端就是浏览器（含同源 Vite 代理）。JWT 的卖点是无状态、跨服务，
但它把凭证交给 JS 保管，一个 XSS 或误贴日志就漏了；HttpOnly Cookie 前端读不到，
前端也就不需要「取 token → 塞 header → 刷新 → 续签」这一整套。
代价是必须有服务端会话表——对单用户工具来说就是 JSON 里的一小段数组。

**为什么密码单独放 data/auth.json**
`store.json` 是业务数据（自选 / 持仓 / 流水），会被频繁读写、也会被用户随手备份和分享；
凭证混在里面，分享一份 store.json 就等于分享密码。拆成独立文件还有个好用的副作用：
**删掉 data/auth.json 就恢复出厂密码**，不需要登进去改。

**为什么不引入 passlib / bcrypt**
标准库 `hashlib.pbkdf2_hmac` 就够：单用户、每秒最多几次校验，200k 轮 SHA-256 约 60ms，
暴力破解的瓶颈在服务端限速而不在哈希强度。多一个带 C 扩展的依赖，
会让「纯标准库、镜像 180MB」这条边界破掉（见 ADR 0001）。
"""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import json
import os
import secrets
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]

COOKIE_NAME = "af_session"
# 默认账号：内置 admin/admin，登录后强制改密（must_change 标记，改完才放行）
SEED_USER = "admin"
SEED_PASSWORD = "admin"
ITERATIONS = 200_000
SESSION_DAYS = int(os.environ.get("AF_AUTH_SESSION_DAYS", "7") or 7)
MAX_SESSIONS = 20

# 这些路径不需要登录：健康探针、登录本身、以及只读的接口文档。
# 文档留在白名单里是为了 Swagger 能正常加载页面——它发起的实际请求照样要过守卫。
PUBLIC_PATHS = {
    "/api/health",
    "/api/docs",
    "/api/openapi.json",
    "/api/redoc",
    "/api/v1/sys/health",
    "/api/v1/auth/status",
    "/api/v1/auth/login",
}

# 登录失败限速：10 分钟内错 5 次，锁 5 分钟。个人工具的合理防御强度，
# 又不至于自己在手机上试错两次就被关在门外。
FAIL_WINDOW = 600.0
FAIL_LIMIT = 5
LOCK_SECONDS = 300.0

_lock = threading.RLock()
_fails: dict[str, list[float]] = {}


class AuthLocked(Exception):
    """连续失败触发的临时锁定。"""

    def __init__(self, remaining: int):
        super().__init__(f"尝试过于频繁，请 {remaining} 秒后再试")
        self.remaining = remaining


# --------------------------------------------------------------------- 存储
def auth_path() -> Path:
    raw = os.environ.get("AF_AUTH_PATH", "").strip()
    p = Path(raw) if raw else ROOT / "data" / "auth.json"
    return p if p.is_absolute() else ROOT / p


def auth_disabled() -> bool:
    """本地工装（布局审计、截图脚本）没有登录态时用它跳过守卫。"""
    return os.environ.get("AF_AUTH_DISABLED", "").strip().lower() in {"1", "true", "yes"}


def _hash(password: str, salt: str, iterations: int = ITERATIONS) -> str:
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), bytes.fromhex(salt), iterations
    )
    return digest.hex()


def _blank() -> dict[str, Any]:
    salt = secrets.token_hex(16)
    return {
        "username": SEED_USER,
        "salt": salt,
        "hash": _hash(SEED_PASSWORD, salt),
        "iterations": ITERATIONS,
        "must_change": True,
        "updated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "sessions": [],
    }


def _read() -> dict[str, Any]:
    path = auth_path()
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                state = json.load(f)
            if state.get("hash") and state.get("salt"):
                state.setdefault("sessions", [])
                state.setdefault("must_change", False)
                return state
        except (json.JSONDecodeError, OSError):
            pass
    state = _blank()
    _write(state)
    return state


def _write(state: dict[str, Any]) -> None:
    path = auth_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _prune(sessions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    now = time.time()
    alive = [s for s in sessions if float(s.get("exp", 0)) > now]
    # 只留最近登录的若干条：会话多了没有意义，反而把文件撑大
    alive.sort(key=lambda s: float(s.get("exp", 0)), reverse=True)
    return alive[:MAX_SESSIONS]


# --------------------------------------------------------------------- 密码
def status_info() -> dict[str, Any]:
    state = _read()
    return {
        "username": state["username"],
        "must_change": bool(state.get("must_change", False)),
        "session_days": SESSION_DAYS,
        "disabled": auth_disabled(),
    }


def verify_password(password: str) -> bool:
    state = _read()
    salt = state["salt"]
    iterations = int(state.get("iterations", ITERATIONS))
    expected = state["hash"]
    actual = _hash(password, salt, iterations)
    # compare_digest 走常数时间比较，避免用响应时间试出前几位
    return hmac.compare_digest(actual, expected)


def set_password(new_password: str, *, must_change: bool = False) -> None:
    with _lock:
        state = _read()
        salt = secrets.token_hex(16)
        state["salt"] = salt
        state["hash"] = _hash(new_password, salt)
        state["iterations"] = ITERATIONS
        state["must_change"] = must_change
        state["updated_at"] = dt.datetime.now().isoformat(timespec="seconds")
        # 改密后踢掉其它会话，只保留调用方当前这条（由 router 重新签发更好理解）
        state["sessions"] = []
        _write(state)


# --------------------------------------------------------------------- 限速
def lock_remaining(username: str) -> int:
    with _lock:
        marks = _fails.get(username, [])
        now = time.time()
        recent = [t for t in marks if now - t < FAIL_WINDOW]
        _fails[username] = recent
        if len(recent) < FAIL_LIMIT:
            return 0
        # 第 5 次失败起锁 LOCK_SECONDS，锁定期内不再累加
        return max(0, int(LOCK_SECONDS - (now - recent[-1])))


def note_failure(username: str) -> None:
    with _lock:
        _fails.setdefault(username, []).append(time.time())


def clear_failures(username: str) -> None:
    with _lock:
        _fails.pop(username, None)


# --------------------------------------------------------------------- 会话
def create_session(ip: str = "", ua: str = "") -> str:
    with _lock:
        state = _read()
        sid = secrets.token_urlsafe(32)
        exp = time.time() + SESSION_DAYS * 86400
        sessions = _prune(state.get("sessions", []))
        sessions.append({
            "sid": sid,
            "exp": exp,
            "created_at": dt.datetime.now().isoformat(timespec="seconds"),
            "ip": ip,
            "ua": ua[:120],
        })
        state["sessions"] = sessions
        _write(state)
        return sid


def session_user(sid: str | None) -> str | None:
    if not sid:
        return None
    state = _read()
    now = time.time()
    for s in state.get("sessions", []):
        if s.get("sid") == sid and float(s.get("exp", 0)) > now:
            return state["username"]
    return None


def revoke(sid: str | None) -> None:
    if not sid:
        return
    with _lock:
        state = _read()
        state["sessions"] = [s for s in state.get("sessions", []) if s.get("sid") != sid]
        _write(state)


def revoke_all() -> None:
    with _lock:
        state = _read()
        state["sessions"] = []
        _write(state)


def sessions() -> list[dict[str, Any]]:
    """当前有效会话（只回登录时间与来源，不回 sid）。"""
    state = _read()
    now = time.time()
    rows = []
    for s in _prune(state.get("sessions", [])):
        if float(s.get("exp", 0)) <= now:
            continue
        rows.append({
            "created_at": s.get("created_at"),
            "ip": s.get("ip", ""),
            "ua": s.get("ua", ""),
            "expires_at": dt.datetime.fromtimestamp(float(s.get("exp", 0))).isoformat(timespec="seconds"),
        })
    return rows


# --------------------------------------------------------------------- 守卫
def is_public(path: str) -> bool:
    if path in PUBLIC_PATHS:
        return True
    # 静态资源与 SPA 落地页不拦：它们只是外壳，真正的数据都要过 /api
    return not path.startswith("/api")


def current_user(cookie: str | None) -> str | None:
    """请求侧唯一的判定入口：有有效会话就回用户名。"""
    if auth_disabled():
        return status_info()["username"]
    return session_user(cookie)
