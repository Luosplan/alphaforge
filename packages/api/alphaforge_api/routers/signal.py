"""交易计划卡与风控校验（M03 / M09）。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request

from alphaforge_core.schemas import RiskCheckRequest
from alphaforge_engine.portfolio import check_plan
from alphaforge_engine.signal import active_plans, build_plan

from ..deps import market, ok, portfolio, profile

router = APIRouter(prefix="/signal", tags=["signal"])


@router.get("/plans")
def plans(request: Request, status: str | None = Query(default=None)):
    data = active_plans(market(), nav=portfolio()["summary"]["nav"])
    if status:
        data = [p for p in data if p["status"] == status]
    stats = {
        "total": len(data),
        "pending": sum(1 for p in data if p["status"] == "PENDING"),
        "triggered": sum(1 for p in data if p["status"] == "TRIGGERED"),
        "avg_confidence": round(sum(p["confidence"] for p in data) / len(data), 1) if data else 0,
        "avg_rr": round(sum(p["rr"] for p in data) / len(data), 2) if data else 0,
    }
    return ok({"items": data, "stats": stats})


@router.get("/plans/{plan_id}")
def plan_detail(request: Request, plan_id: str):
    data = active_plans(market(), nav=portfolio()["summary"]["nav"])
    for p in data:
        if p["plan_id"] == plan_id:
            bar_rows = market().bars(p["code"], limit=120)
            return ok({"plan": p, "bars": bar_rows})
    raise HTTPException(status_code=404, detail="计划卡不存在")


@router.post("/plans/preview")
def plan_preview(request: Request, code: str, strategy: str = "multifactor", signal_type: str = "ENTRY"):
    nav = portfolio()["summary"]["nav"]
    return ok(build_plan(market(), code, nav=nav, strategy_id=strategy, signal_type=signal_type))


@router.post("/risk/check")
def risk_check(request: Request, payload: RiskCheckRequest):
    return ok(check_plan(market(), portfolio(), payload.code, payload.shares, payload.price, profile()))
