"""持仓、成交流水与账户绩效（M06 只读侧）。

数据全部来自实验室的模拟盘账户：计划卡挂单 → 次日开盘成交 → 账本记股数与成本。
这一层不造数，只做合并、切片和统一响应包装。
"""

from __future__ import annotations

from fastapi import APIRouter, Request

from alphaforge_engine.paper import OPEN_TIME
from alphaforge_engine.portfolio import benchmark_curve, trade_change_pct, trade_stats

from ..deps import accounts, market, ok, portfolio

router = APIRouter(prefix="/portfolio", tags=["portfolio"])


@router.get("/summary")
def summary(request: Request):
    return ok(portfolio())


@router.get("/holdings")
def holdings(request: Request):
    data = portfolio()
    return ok({"holdings": data["holdings"], "summary": data["summary"],
               "industry_alloc": data["industry_alloc"], "accounts": data["accounts"]})


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
    """成交流水：各策略账户的成交合并，新的在前。"""
    rows = []
    engine = market()
    for acc in accounts().values():
        name = acc.get("name") or acc.get("id")
        for t in acc.get("trades") or []:
            rows.append({
                "id": t.get("id"), "code": t["code"], "name": t.get("name") or "",
                "side": t["side"], "price": t["price"], "shares": t["shares"],
                "amount": t["amount"], "fee": t["fee"], "tax": t["tax"],
                "trade_date": t["date"], "note": t.get("reason") or "",
                # 老账本里没有 time 字段；模拟盘的买单只可能是次日开盘成交
                "trade_time": t.get("time") or (OPEN_TIME if t["side"] == "BUY" else ""),
                "change_pct": trade_change_pct(engine, t["code"], t["date"]),
                "strategy": name, "pnl": t.get("pnl"), "pnl_pct": t.get("pnl_pct"),
            })
    rows.sort(key=lambda r: r["trade_date"], reverse=True)
    return ok({"items": rows, "stats": trade_stats(rows)})
