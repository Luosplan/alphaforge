"""依赖注入与统一响应包装（SRS 9.1 响应包约定）。"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import Request

from alphaforge_engine.market import get_market
from alphaforge_engine.portfolio import build_portfolio
from alphaforge_api.store import get_store


def market():
    return get_market()


def store():
    return get_store()


def profile() -> dict:
    """当前账户设置（成本口径 + 购买权限）。"""
    return get_store().profile()


def portfolio():
    """按当前落库持仓重建组合视图。"""
    return build_portfolio(get_market(), get_store().holdings(), get_store().cash())


def ok(data: Any, request: Request | None = None) -> dict:
    return {
        "code": 0,
        "msg": "ok",
        "data": data,
        "traceId": (request.headers.get("x-trace-id") if request else None) or uuid.uuid4().hex[:16],
    }


def fail(code: int, msg: str, data: Any = None) -> dict:
    return {"code": code, "msg": msg, "data": data, "traceId": uuid.uuid4().hex[:16]}
