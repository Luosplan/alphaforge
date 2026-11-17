"""自选股分组与增删（FR-QTE-11）。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from alphaforge_core.schemas import WatchAddRequest, WatchGroupRequest

from ..deps import market, ok, store

router = APIRouter(prefix="/watchlist", tags=["watchlist"])


def _decorate(groups: list[dict]) -> list[dict]:
    # 只取分组里出现的代码，别为了一只自选股把 5000+ 只全捞一遍
    codes = sorted({c for g in groups for c in g["codes"]})
    quotes = {q["code"]: q for q in market().quotes(codes)}
    out = []
    for g in groups:
        rows = [quotes[c] for c in g["codes"] if c in quotes]
        avg = round(sum(r["change_pct"] for r in rows) / len(rows), 2) if rows else 0.0
        out.append({**g, "count": len(rows), "avg_change_pct": avg, "items": rows})
    return out


@router.get("")
def list_groups(request: Request):
    return ok(_decorate(store().groups()))


@router.post("/items")
def add_item(request: Request, payload: WatchAddRequest):
    if not market().symbol_valid(payload.code):
        raise HTTPException(status_code=400, detail="标的不存在")
    try:
        store().add_code(payload.code, payload.group_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="分组不存在")
    return ok({"added": payload.code, "group_id": payload.group_id})


@router.delete("/items")
def remove_item(request: Request, code: str, group_id: str = "g1"):
    try:
        store().remove_code(code, group_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="记录不存在")
    return ok({"removed": code, "group_id": group_id})


@router.post("/groups")
def create_group(request: Request, payload: WatchGroupRequest):
    return ok(store().create_group(payload.name))


@router.delete("/groups/{group_id}")
def delete_group(request: Request, group_id: str):
    store().delete_group(group_id)
    return ok({"deleted": group_id})
