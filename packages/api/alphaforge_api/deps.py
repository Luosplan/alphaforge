"""依赖注入与统一响应包装（SRS 9.1 响应包约定）。"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import Request

from alphaforge_engine.market import get_market
from alphaforge_engine.portfolio import build_live_portfolio
from alphaforge_engine.selector import STRATEGY_MAP
from alphaforge_api.lab_store import get_lab
from alphaforge_api.store import get_store


def market():
    return get_market()


def store():
    return get_store()


def profile() -> dict:
    """当前账户设置（成本口径 + 购买权限）。"""
    return get_store().profile()


def accounts() -> dict[str, dict]:
    """持仓的唯一来源：实验室里当前策略集的模拟盘账户。

    已删除策略的旧账户还躺在 lab.json 里，不按 `STRATEGY_MAP` 过一道，
    页面上会冒出几个永远不动的幽灵持仓。
    """
    return {sid: acc for sid, acc in get_lab().accounts().items() if sid in STRATEGY_MAP}


def portfolio():
    """按各策略账户的真实成交重建组合视图。"""
    return build_live_portfolio(get_market(), accounts())


def ok(data: Any, request: Request | None = None) -> dict:
    return {
        "code": 0,
        "msg": "ok",
        "data": data,
        "traceId": (request.headers.get("x-trace-id") if request else None) or uuid.uuid4().hex[:16],
    }


def fail(code: int, msg: str, data: Any = None) -> dict:
    return {"code": code, "msg": msg, "data": data, "traceId": uuid.uuid4().hex[:16]}
