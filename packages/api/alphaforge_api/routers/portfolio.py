"""持仓、成交流水与账户绩效（M06 只读侧 + 手工记账）。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from alphaforge_core.schemas import TradeRequest
from alphaforge_engine.portfolio import benchmark_curve, seed_trades, trade_stats

from ..deps import market, ok, portfolio, store

router = APIRouter(prefix="/portfolio", tags=["portfolio"])


@router.get("/summary")
def summary(request: Request):
    return ok(portfolio())


@router.get("/holdings")
def holdings(request: Request):
    data = portfolio()
    return ok({"holdings": data["holdings"], "summary": data["summary"], "industry_alloc": data["industry_alloc"]})


@router.get("/equity")
def equity(request: Request, days: int = 180):
    data = portfolio()
    full_curve = data["equity_curve"]
    base = full_curve[0]["value"] if full_curve else 1
    curve = full_curve[-days:]
    bench = benchmark_curve(market(), days)
    bench_base = bench[0]["value"] if bench else 1
    return ok({
        "curve": [{"date": p["date"], "value": round(p["value"], 2),
                   "nav": round(p["value"] / base * 100, 2)} for p in curve],
        "benchmark": [{"date": b["date"], "value": round(b["value"] / bench_base * 100, 2)} for b in bench],
        "performance": data["performance"],
    })


@router.get("/trades")
def trades(request: Request):
    rows = store().manual_trades() + seed_trades(market())
    rows.sort(key=lambda r: r["trade_date"], reverse=True)
    return ok({"items": rows, "stats": trade_stats(rows)})


@router.post("/trades")
def create_trade(request: Request, payload: TradeRequest):
    try:
        record = store().add_trade(payload.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return ok(record)
