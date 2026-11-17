"""概览看板聚合接口：一次请求返回首屏所需的全部数据。"""

from __future__ import annotations

from fastapi import APIRouter, Request

from alphaforge_core.constants import RISK_LIMITS
from alphaforge_engine.portfolio import benchmark_curve
from alphaforge_engine.signal import active_plans

from ..deps import market, ok, portfolio

router = APIRouter(prefix="/overview", tags=["overview"])


@router.get("")
def overview(request: Request, days: int = 90):
    engine = market()
    pf = portfolio()
    nav = pf["summary"]["nav"]

    full_curve = pf["equity_curve"]
    base = full_curve[0]["value"] if full_curve else 1
    curve = full_curve[-days:]
    bench = benchmark_curve(engine, days)
    bench_base = bench[0]["value"] if bench else 1

    plans = active_plans(engine, nav=nav)
    pending = [p for p in plans if p["status"] == "PENDING"]
    triggered = [p for p in plans if p["status"] == "TRIGGERED"]

    flow = engine.sector_flow()

    # 风控告警：单票 / 行业 / 总仓位
    alerts = []
    if pf["holdings"]:
        top = max(pf["holdings"], key=lambda h: h["weight"])
        if top["weight"] > RISK_LIMITS["max_single_weight"] * 100:
            alerts.append({
                "level": "WARN",
                "title": "单票集中度超限",
                "detail": f"{top['name']} 占比 {top['weight']:.1f}%，上限 {RISK_LIMITS['max_single_weight'] * 100:.0f}%",
            })
    for alloc in pf["industry_alloc"][:1]:
        if alloc["weight"] > RISK_LIMITS["max_industry_weight"] * 100:
            alerts.append({
                "level": "WARN",
                "title": "行业集中度超限",
                "detail": f"{alloc['name']} 占比 {alloc['weight']:.1f}%，上限 {RISK_LIMITS['max_industry_weight'] * 100:.0f}%",
            })
    alerts.append({
        "level": "INFO",
        "title": "待触发计划卡",
        "detail": f"{len(pending)} 张待触发 / {len(triggered)} 张已进入区间",
    })

    return ok({
        "market": engine.market_overview(),
        "account": pf["summary"],
        "performance": pf["performance"],
        "equity": [
            {"date": p["date"], "nav": round(p["value"] / base * 100, 2)}
            for p in curve
        ],
        "benchmark": [{"date": b["date"], "value": round(b["value"] / bench_base * 100, 2)} for b in bench],
        "plans": sorted(plans, key=lambda p: p["confidence"], reverse=True)[:4],
        "plan_stats": {
            "total": len(plans),
            "pending": len(pending),
            "triggered": len(triggered),
        },
        "sector_flow_top": flow[:4],
        "sector_flow_bottom": flow[-2:],
        "industry_alloc": pf["industry_alloc"],
        "holdings": pf["holdings"][:5],
        "alerts": alerts,
        "trade_date": engine.trade_date.isoformat() if engine.trade_date else None,
    })
