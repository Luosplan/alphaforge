"""交易计划卡与风控校验（M03 / M09）。

计划卡的候选来自选股引擎（`signal.plan_candidates()`），不是代码里写死的名单；
「移除」只记在本地 store 的 `dismissed_plans` 里，JSON 原子写入，可撤回。

路由顺序有讲究：`/plans/dismissed` 必须排在 `/plans/{plan_id}` 之前，否则
`dismissed` 会被当成一个 plan_id 匹配走。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request

from alphaforge_core.schemas import RiskCheckRequest
from alphaforge_engine.portfolio import check_plan
from alphaforge_engine.signal import active_plans, build_plan

from ..deps import market, ok, portfolio, profile, store

router = APIRouter(prefix="/signal", tags=["signal"])


def _board() -> dict:
    """当日计划卡 + 候选来源。各路由共用，避免每个请求都重跑一遍选股。

    `boards` 必须传：计划卡是要下单的东西，未开通板块的票挂上去只会变成
    「明天开盘自动买进一只根本不能买的票」。
    """
    pf = portfolio()
    return active_plans(market(), nav=pf["summary"]["nav"], holdings=pf["holdings"],
                        boards=profile()["boards"])


@router.get("/plans")
def plans(request: Request, status: str | None = Query(default=None)):
    board = _board()
    hidden = {r["plan_id"] for r in store().dismissed_plans()}
    data = [p for p in board["items"] if p["plan_id"] not in hidden]
    if status:
        data = [p for p in data if p["status"] == status]
    stats = {
        "total": len(data),
        "pending": sum(1 for p in data if p["status"] == "PENDING"),
        "triggered": sum(1 for p in data if p["status"] == "TRIGGERED"),
        "avg_confidence": round(sum(p["confidence"] for p in data) / len(data), 1) if data else 0,
        "avg_rr": round(sum(p["rr"] for p in data) / len(data), 2) if data else 0,
        "dismissed": len(hidden),
    }
    return ok({"items": data, "stats": stats, "source": board["source"]})


@router.get("/plans/dismissed")
def dismissed_plans(request: Request):
    """已移除的计划卡：只回快照，不带区间——恢复时按 plan_id 重算，不存过期点位。"""
    items = store().dismissed_plans()
    items.sort(key=lambda r: r.get("removed_at") or "", reverse=True)
    return ok({"items": items, "total": len(items)})


@router.delete("/plans/dismissed")
def clear_dismissed_plans(request: Request):
    return ok({"cleared": store().clear_dismissed_plans()})


@router.delete("/plans/{plan_id}")
def dismiss_plan(request: Request, plan_id: str):
    board = _board()
    plan = next((p for p in board["items"] if p["plan_id"] == plan_id), None)
    if plan is None:
        raise HTTPException(
            status_code=404,
            detail="计划卡不存在：行情刷新后 plan_id 会变，请刷新列表后重试",
        )
    return ok(store().dismiss_plan(plan))


@router.post("/plans/{plan_id}/restore")
def restore_plan(request: Request, plan_id: str):
    try:
        store().restore_plan(plan_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="该计划卡不在已移除列表里")
    return ok({"restored": plan_id})


@router.get("/plans/{plan_id}")
def plan_detail(request: Request, plan_id: str):
    board = _board()
    for p in board["items"]:
        if p["plan_id"] == plan_id:
            bar_rows = market().bars(p["code"], limit=120, live=True)
            return ok({"plan": p, "bars": bar_rows})
    raise HTTPException(status_code=404, detail="计划卡不存在")


@router.post("/plans/preview")
def plan_preview(request: Request, code: str, strategy: str = "multifactor", signal_type: str = "ENTRY"):
    nav = portfolio()["summary"]["nav"]
    return ok(build_plan(market(), code, nav=nav, strategy_id=strategy, signal_type=signal_type))


@router.post("/risk/check")
def risk_check(request: Request, payload: RiskCheckRequest):
    return ok(check_plan(market(), portfolio(), payload.code, payload.shares, payload.price, profile()))
