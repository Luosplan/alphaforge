"""策略知识库：把当前生效的那一版策略翻译成「怎么选、什么时候出手、什么时候卖」。

数值一律来自运行中的那一版（账户里已采纳的 spec 与参数），不另立一套口径：
选股直接调 `selector.execute`，止损止盈用与模拟盘同一组 ATR 乘数。
"""

from __future__ import annotations

from fastapi import APIRouter, Request

from alphaforge_core.constants import RISK_LIMITS
from alphaforge_core.fields import FIELD_MAP, OP_LABEL
from alphaforge_engine import backtest as bt
from alphaforge_engine import paper
from alphaforge_engine.conditions import leaves
from alphaforge_engine.selector import execute, strategy_list

from ..deps import market, ok, profile, store
from ..lab_store import get_lab
from . import lab as lab_router

router = APIRouter(prefix="/playbook", tags=["playbook"])


def _param(params: dict, key: str) -> float:
    return float(params.get(key) or bt.DEFAULTS[key])


def _conditions(filters: list[dict]) -> list[dict]:
    out = []
    for f in leaves(filters):
        meta = FIELD_MAP.get(f["field"])
        out.append({
            "field": f["field"],
            "label": meta.label if meta else f["field"],
            "op": "=" if f["op"] == "is" else OP_LABEL.get(f["op"], f["op"]),
            "target": f["value"],
            "unit": meta.unit if meta else "",
        })
    return out


def _factors(weights: dict) -> list[dict]:
    rows = [
        {"key": k, "label": FIELD_MAP[k].label, "weight": round(float(w), 4),
         "desc": FIELD_MAP[k].desc}
        for k, w in weights.items() if k in FIELD_MAP
    ]
    return sorted(rows, key=lambda r: r["weight"], reverse=True)


def _picks(result: dict, params: dict) -> list[dict]:
    stop_atr = _param(params, "stop_atr")
    tp1_atr = _param(params, "tp1_atr")
    tp2_atr = _param(params, "tp2_atr")
    out = []
    for it in result.get("items") or []:
        price = float(it.get("price") or 0)
        atr = float(it.get("atr") or 0)
        out.append({
            "code": it["code"],
            "name": it["name"],
            "industry": it.get("industry"),
            "rank": it.get("rank"),
            "score": it.get("score"),
            "price": price,
            "change_pct": it.get("change_pct"),
            "atr": round(atr, 3),
            "levels": {
                "stop": round(price - stop_atr * atr, 2),
                "tp1": round(price + tp1_atr * atr, 2),
                "tp2": round(price + tp2_atr * atr, 2),
            },
            "hits": [
                {**h, "op": "=" if h["op"] == "is" else OP_LABEL.get(h["op"], h["op"])}
                for h in (it.get("hits") or [])
            ],
            "factors": [
                {**f, "unit": FIELD_MAP[f["key"]].unit if f["key"] in FIELD_MAP else ""}
                for f in (it.get("factors") or [])[:4]
            ],
            "huddle": it.get("huddle") or {},
            "reason": it.get("reason") or {},
        })
    return out


@router.get("")
def playbook(request: Request):
    mkt = market()
    lab = get_lab()
    boards = profile()["boards"]
    strategies = []
    for item in strategy_list(store().strategies()):
        sid = item["id"]
        spec = lab_router._live_spec(sid)
        params = lab_router._live_params(sid)
        result = execute(mkt, spec, boards=boards)
        acc = lab.account(sid) or {}
        evo = lab.evolution(sid) or {}
        # 保本移损这类开关不在 PARAM_KEYS 里（不参与搜索），但确实影响卖出，
        # 只能从账户那份完整 params 里读。
        acc_params = acc.get("params") or {}
        strategies.append({
            "id": sid,
            "name": spec.get("name") or sid,
            # 说明文字跟着代码里的策略定义走：账户那份 spec 是采纳时的快照，
            # 里面的 desc 可能还在讲早已关掉的动作（比如「没封住次日走」）。
            "desc": lab_router._spec_of(sid).get("desc") or "",
            "generation": int(acc.get("generation") or 0),
            "fitness": acc.get("fitness"),
            "based_on": evo.get("as_of"),
            "params": {k: params.get(k) for k in paper.PARAM_KEYS},
            "selection": {
                "as_of": result.get("as_of"),
                "universe": result.get("universe"),
                "universe_size": result.get("universe_size"),
                "board_filter": result.get("board_filter"),
                "funnel": result.get("funnel") or [],
                "conditions": _conditions(result.get("filters") or []),
                "sort": result.get("sort"),
                "top_n": result.get("top_n"),
                "max_per_industry": result.get("max_per_industry"),
                "pool_limit_min": int(result.get("pool_limit_min") or 0),
                "factors": _factors(result.get("weights") or {}),
                "picks": _picks(result, params),
                "notice": result.get("notice") or result.get("rank_hint") or "",
            },
            "entry": {
                "timing": "信号日收盘生成，次日开盘执行",
                "gap_band": spec.get("gap_band"),
                "gap_gate": bool(spec.get("gap_gate", True)),
                "gap_max": bt.GAP_MAX_PCT,
                "gap_min": bt.GAP_MIN_PCT,
                "regime_scale": bt.REGIME_SCALE,
                "market_ma": int(spec.get("market_ma") or 0),
                "weak_participate": bool(spec.get("weak_participate")),
                "weak_huddle": bool(spec.get("weak_huddle", True)),
                "allow_add": bool(spec.get("allow_add", True)),
                "sizing": {
                    "top_n": params.get("top_n"),
                    "max_per_industry": params.get("max_per_industry"),
                    "single_weight": RISK_LIMITS["max_single_weight"],
                    "risk_per_trade": RISK_LIMITS["risk_per_trade"],
                },
            },
            "exit": {
                "stop_atr": _param(params, "stop_atr"),
                "tp1_atr": _param(params, "tp1_atr"),
                "tp2_atr": _param(params, "tp2_atr"),
                "time_stop": int(_param(params, "time_stop")),
                "breakeven_after_tp1": bool(acc_params.get("breakeven_after_tp1")),
                "need_limit_close": bool(spec.get("need_limit_close")),
                "entry_rule": result.get("entry_rule"),
            },
        })
    return ok({"as_of": strategies[0]["selection"]["as_of"] if strategies else None,
               "strategies": strategies})
