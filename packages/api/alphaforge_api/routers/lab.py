"""策略实验室（M10）：策略回测、遗传自进化、模拟盘、全策略计划卡。

这一层只做三件事：**取 spec、调引擎、落盘**。所有数值口径都在 `engine` 里，
路由层不重新算一遍——否则「后端算的胜率」和「前端看到的胜率」迟早会打架。

长任务（进化）沿用 `routers/data.py` 的套路：`POST` 只负责发起并立即返回，
进度靠轮询 `/lab/evolve/status`。同一时刻只允许一个进化任务，
重复发起拿到 409 而不是排一队把 CPU 压满。
"""

from __future__ import annotations

import datetime as dt
import threading
import time

from fastapi import APIRouter, HTTPException, Request

from alphaforge_core.schemas import LabBacktestRequest, LabEvolveRequest, LabPaperRequest
from alphaforge_engine import backtest as bt
from alphaforge_engine import harness, paper
from alphaforge_engine.selector import STRATEGY_MAP, normalize_spec, strategy_list

from ..deps import market, ok, store
from ..lab_store import get_lab

router = APIRouter(prefix="/lab", tags=["lab"])


# ------------------------------------------------------------------ 取 spec
def _spec_of(strategy: str, inline: dict | None = None) -> dict:
    """取一条策略的 spec，并保证 `top_n` / `max_per_industry` 一定有值。

    `STRATEGY_MAP` 里的内置策略只写了 `max_per_industry`，`top_n` 是靠
    `normalize_spec` 补的默认 10——而策略列表页展示的正是补齐后的值。
    直接返回原始字典的话，`spec["top_n"]` 是缺失的，回测会退回
    `backtest.DEFAULTS` 的 8：界面写 10、实际按 8 跑，而且看不出来。
    """
    if inline:
        base = {**inline, "id": inline.get("id") or "inline",
                "name": inline.get("name") or "内联策略"}
    elif strategy in STRATEGY_MAP:
        base = {**STRATEGY_MAP[strategy], "id": strategy}
    else:
        base = None
        for item in store().strategies():
            if item["id"] == strategy:
                base = item
                break
        if base is None:
            raise HTTPException(status_code=404, detail="策略不存在")
    norm = normalize_spec(base)
    for key in ("top_n", "max_per_industry"):
        base[key] = norm[key]
    return base


def _roster() -> list[dict]:
    return strategy_list(store().strategies())


def _account(strategy: str, spec: dict, capital: float | None = None) -> dict:
    """取账户；没有就开一个。

    spec 的刷新规则：账户**从未进化过**才跟着源策略走。一旦采纳过进化结果，
    账户里的 spec 就是用户认可的那一版，不能被「策略库里那条原始策略」
    每次请求都覆盖掉——否则进化白跑，而且没有任何提示。
    """
    lab = get_lab()
    acc = lab.account(strategy)
    if acc is None:
        acc = paper.empty_account(strategy, spec.get("name") or strategy, spec,
                                  capital=capital or paper.LIVE_CAPITAL)
        lab.put_account(acc)
        return acc
    if int(acc.get("generation") or 0) == 0:
        acc["spec"] = spec
        acc["name"] = spec.get("name") or acc["name"]
        # 结构参数也跟随 spec 刷新，但**只在账户还空着的时候**：已经按旧
        # top_n 建过仓，中途换参数会让计划卡与账本对不上。账户一旦有成交，
        # 参数就冻在那一刻；generation>0 的账户是用户采纳过的版本，一律不碰。
        if not (acc.get("trades") or acc.get("positions")):
            params = dict(acc.get("params") or {})
            for key in paper.PARAM_KEYS:
                if spec.get(key) is not None:
                    params[key] = spec[key]
            acc["params"] = params
    return acc


def _thin(rows: list, limit: int) -> list:
    if len(rows) <= limit:
        return rows
    step = max(1, len(rows) // limit)
    out = rows[::step]
    if out and out[-1] is not rows[-1]:
        out.append(rows[-1])
    return out


def _saved_backtest(strategy_id: str) -> dict | None:
    """取存档回测，但**只认当前撮合模型算出来的**。

    规则的任何改动（进场是否每天扫、止损优先级、仓位算法）都会让旧数字
    失去可比性，而界面上又看不出哪个是旧口径。实测：改成每日补仓之后，
    同一条动量策略从 -12.75% 变成 -40.08%——两个数字同时挂在卡片上，
    比「没有数字」危险得多。所以版本对不上就当没跑过，让用户重跑。
    """
    rep = get_lab().backtest(strategy_id)
    if not rep:
        return None
    if rep.get("model_version") != bt.MODEL_VERSION:
        return None
    return rep


def _brief_backtest(rep: dict | None) -> dict | None:
    if not rep:
        return None
    return {
        "available": rep.get("available"),
        "as_of": rep.get("as_of"),
        "saved_at": rep.get("saved_at"),
        "period": rep.get("period"),
        "params": rep.get("params"),
        "weights": rep.get("weights"),
        "dropped": rep.get("dropped") or [],
        "notes": rep.get("notes") or [],
        "metrics": rep.get("metrics"),
        "equity": _thin(rep.get("equity") or [], 60),
    }


# ------------------------------------------------------------------ 总览
@router.get("/overview")
def overview(request: Request):
    """所有策略一格：回测水平 + 模拟盘现状 + 今日计划卡。"""
    mkt = market()
    lab = get_lab()
    accounts = lab.accounts()
    evolutions = lab.state.get("evolutions", {})

    rows: list[dict] = []
    plans: list[dict] = []
    nav_total = 0.0
    pnl_total = 0.0
    win_rates: list[float] = []
    calmars: list[float] = []

    for item in _roster():
        sid = item["id"]
        acc = accounts.get(sid)
        snap = paper.snapshot(mkt, acc) if acc else None
        rep = _brief_backtest(_saved_backtest(sid))
        evo = evolutions.get(sid)

        if rep and rep.get("available"):
            m = rep["metrics"] or {}
            if m.get("closed_count"):
                win_rates.append(float(m.get("win_rate") or 0.0))
            calmars.append(float(m.get("calmar") or 0.0))
        if snap:
            nav_total += float(snap["summary"]["nav"])
            pnl_total += float(snap["summary"]["pnl"])
            for plan in snap["plans"]:
                plans.append({
                    **plan,
                    "strategy_id": sid,
                    "strategy_name": item["name"],
                    "strategy_generation": snap.get("generation", 0),
                })

        rows.append({
            "id": sid,
            "name": item["name"],
            "desc": item.get("desc") or "",
            "builtin": bool(item.get("builtin")),
            "spec": {k: item.get(k) for k in
                     ("weights", "filters", "sort", "top_n", "max_per_industry",
                      "exclude_st")},
            "generation": int(acc.get("generation") or 0) if acc else 0,
            "started": bool(acc),
            "paper": {
                "summary": snap["summary"], "statistics": snap["statistics"],
                "last_run": snap["last_run"], "is_stale": snap["is_stale"],
                "plans": len(snap["plans"]), "orders": len(snap["orders"]),
            } if snap else None,
            "backtest": rep,
            "evolution": {
                "score": (evo or {}).get("best", {}).get("score"),
                "baseline": (evo or {}).get("baseline", {}).get("score"),
                "elapsed": (evo or {}).get("elapsed"),
                "saved_at": (evo or {}).get("saved_at"),
                "history": _thin((evo or {}).get("history") or [], 20),
                # 样本外验证：没参与搜索的那段交易日，进化后还打不打得赢原始策略
                "holdout": (evo or {}).get("holdout"),
                # 该不该真换参数：edge + 连续两次一致 + 风控门槛
                "adoption": (evo or {}).get("adoption"),
            } if evo else None,
        })

    return ok({
        "as_of": mkt.trade_date.isoformat() if mkt.trade_date else None,
        "ready": bool(mkt.trade_date),
        "stats": {
            "strategies": len(rows),
            "started": sum(1 for r in rows if r["started"]),
            "avg_win_rate": round(sum(win_rates) / len(win_rates), 2) if win_rates else None,
            "best_calmar": round(max(calmars), 3) if calmars else None,
            "paper_nav": round(nav_total, 2),
            "paper_pnl": round(pnl_total, 2),
            "plan_count": len(plans),
        },
        "strategies": rows,
        "plans": plans,
        "notes": [
            "回测只用价量因子：库里的 ROE / PE / 股息率只有当前截面，没有历史序列",
            "成交价取信号次日开盘价；行业与 ST 标记为当前值，不是历史值",
            "模拟盘每策略独立 100 万，按 T+1 开盘成交，一天推进一格",
        ],
    })


# ------------------------------------------------------------------ 单策略
@router.get("/strategies/{strategy_id}")
def strategy_detail(request: Request, strategy_id: str):
    mkt = market()
    lab = get_lab()
    spec = _spec_of(strategy_id)
    acc = lab.account(strategy_id)
    return ok({
        "id": strategy_id,
        "name": spec.get("name") or strategy_id,
        "spec": spec,
        "paper": paper.snapshot(mkt, acc) if acc else None,
        "backtest": _saved_backtest(strategy_id),
        "evolution": lab.evolution(strategy_id),
    })


# ------------------------------------------------------------------ 回测
@router.post("/backtest")
def run_backtest(request: Request, payload: LabBacktestRequest):
    """跑一遍回测并落盘。同步执行：250 天 / 600 只大约 0.1 秒，不值得异步。"""
    mkt = market()
    spec = _spec_of(payload.strategy, payload.spec)
    sid = spec["id"]
    report = bt.run_backtest(
        mkt, spec, days=payload.days, capital=payload.capital,
        params=payload.params, profile=store().profile(),
    )
    get_lab().put_backtest(sid, report)
    return ok(report)


@router.get("/backtest/{strategy_id}")
def get_backtest(request: Request, strategy_id: str):
    rep = _saved_backtest(strategy_id)
    if not rep:
        raise HTTPException(status_code=404, detail="还没有跑过回测（或旧存档已按新规则作废）")
    return ok(rep)


# ------------------------------------------------------------------ 进化
_EVOLVE_LOCK = threading.Lock()
_EVOLVE: dict = {
    "running": False, "strategy": None, "name": None,
    "generation": 0, "of": 0, "done": 0, "total": 0, "percent": 0.0,
    "best": None, "started_at": None, "finished_at": None,
    "elapsed": 0.0, "result": None, "error": None,
    # 请求了 apply 但被采纳门槛挡下时，把原因留在状态里给前端显示——
    # 否则用户只看到「没换上」却不知道是 edge 不够还是风控不合格。
    "apply_blocked": None,
}


def _apply_evolution(strategy_id: str, result: dict) -> dict:
    """把最优基因组写进账户的 spec 与结构参数。持仓不动，只影响下一次选股。"""
    lab = get_lab()
    acc = lab.account(strategy_id)
    if acc is None:
        spec = result["best"]["spec"]
        acc = paper.empty_account(strategy_id, spec.get("name") or strategy_id, spec)
    acc["spec"] = result["best"]["spec"]
    acc["params"] = {**(acc.get("params") or {}),
                     **{k: result["best"]["genome"][k] for k in paper.PARAM_KEYS}}
    acc["generation"] = int(acc.get("generation") or 0) + 1
    acc["fitness"] = result["best"]["score"]
    lab.put_account(acc)
    return acc


def _run_evolve(payload: dict) -> None:
    t0 = time.monotonic()
    sid = payload["strategy_id"]

    def on_progress(p: dict) -> None:
        _EVOLVE.update({
            "generation": p["generation"], "of": p["of"],
            "done": p["done"], "total": p["total"],
            "percent": p["percent"], "best": p["best"],
            "elapsed": round(time.monotonic() - t0, 1),
            "eta": round((time.monotonic() - t0) / max(p["percent"], 1) * (100 - p["percent"]), 1)
            if p["percent"] else None,
        })

    try:
        result = harness.evolve(
            market(), payload["spec"],
            population=payload["population"], generations=payload["generations"],
            days=payload["days"], max_codes=payload["max_codes"],
            capital=payload["capital"], seed=payload["seed"],
            profile=store().profile(), progress=on_progress,
        )
        _EVOLVE["result"] = result
        if result.get("available"):
            # 采纳与否要在落盘**之前**算：put_evolution 会把上一条覆盖掉，
            # 之后就再也拿不到「上一次的样本外结论」来做一致性判断了。
            prev_raw = get_lab().evolution(sid) or {}
            # 跨版本的上一次当不存在：撮合规则或搜索区间变了之后，上一条的
            # 「win/edge」是在另一套规则下得出的，拿它做「连续两次一致」的确认
            # 等于用旧尺子量新布。与回测的 `_saved_backtest` 同一套处理，
            # 连 `adoption_runs` 的历史也一起归零——那条曲线的每一格都该同口径。
            same_version = prev_raw.get("model_version") == bt.MODEL_VERSION
            prev = prev_raw if same_version else None
            result["adoption"] = harness.adopt_verdict(result, prev)
            runs = list(prev_raw.get("adoption_runs") or []) if same_version else []
            runs.append({
                "at": dt.datetime.now().isoformat(timespec="seconds"),
                "action": result["adoption"]["action"],
                "level": result["holdout"].get("level") if result.get("holdout") else None,
                "edge": result["adoption"]["edge"],
            })
            result["adoption_runs"] = runs[-8:]
            get_lab().put_evolution(sid, result)
            # `apply` 只在门槛通过时才真的换参数：以前是「跑完就换上」，
            # 等于把每一次拟合噪声的结果都当成改进。
            if payload.get("apply") and result["adoption"]["action"] == "adopt":
                _apply_evolution(sid, result)
                _EVOLVE["applied"] = True
            elif payload.get("apply"):
                _EVOLVE["apply_blocked"] = result["adoption"]["reason"]
    except Exception as exc:                            # noqa: BLE001
        _EVOLVE["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        _EVOLVE["running"] = False
        _EVOLVE["finished_at"] = dt.datetime.now().isoformat(timespec="seconds")
        _EVOLVE["elapsed"] = round(time.monotonic() - t0, 1)
        _EVOLVE["percent"] = 100.0 if not _EVOLVE.get("error") else _EVOLVE["percent"]
        _EVOLVE["eta"] = 0


def _begin_evolve(sid: str, spec: dict, *, population: int, generations: int,
                  days: int, max_codes: int, capital: float, seed: int,
                  apply: bool) -> dict:
    """把状态机置成「正在跑」并组装 `_run_evolve` 的参数。HTTP 与收盘任务共用。"""
    _EVOLVE.update({
        "running": True, "strategy": sid, "name": spec.get("name") or sid,
        "generation": 0, "of": generations, "done": 0,
        "total": population * generations,
        "percent": 0.0, "best": None, "applied": False,
        "started_at": dt.datetime.now().isoformat(timespec="seconds"),
        "finished_at": None, "elapsed": 0.0, "eta": None,
        "result": None, "error": None, "apply_blocked": None,
    })
    return {
        "strategy_id": sid, "spec": spec,
        "population": population, "generations": generations,
        "days": days, "max_codes": max_codes,
        "capital": capital, "seed": seed, "apply": apply,
    }


def run_evolve_blocking(strategy_id: str, *, apply: bool = True) -> dict:
    """同步跑一轮进化，返回采纳结论。给收盘自动驾驶调用。

    与 HTTP 入口共用 `_EVOLVE_LOCK`：两边同时跑会各搜一遍、互相覆盖结果，
    进度条也会来回跳。拿不到锁就跳过这一轮——**收盘任务不该因为
    「用户刚好手点了一次进化」而整体失败**，明天还有一轮。
    """
    if not _EVOLVE_LOCK.acquire(blocking=False):
        return {"action": "busy", "reason": "已有进化任务在跑，本轮跳过",
                "edge": None, "applied": False}
    try:
        spec = _spec_of(strategy_id)
        args = _begin_evolve(spec["id"], spec, population=24, generations=6,
                             days=250, max_codes=600, capital=1_000_000,
                             seed=20261118, apply=apply)
        _run_evolve(args)
        res = _EVOLVE.get("result") or {}
        adoption = res.get("adoption") or {}
        return {
            "action": adoption.get("action") or "hold",
            "reason": adoption.get("reason") or _EVOLVE.get("error") or "",
            "edge": adoption.get("edge"),
            "applied": bool(_EVOLVE.get("applied")),
        }
    finally:
        _EVOLVE_LOCK.release()


@router.post("/evolve")
def evolve(request: Request, payload: LabEvolveRequest):
    """发起遗传搜索（后台跑，立即返回）。进度看 GET /lab/evolve/status。"""
    if not _EVOLVE_LOCK.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="已有进化任务在跑")
    try:
        if _EVOLVE["running"]:
            raise HTTPException(status_code=409, detail="已有进化任务在跑")
        spec = _spec_of(payload.strategy, payload.spec)
        sid = spec["id"]
        args = _begin_evolve(
            sid, spec, population=payload.population, generations=payload.generations,
            days=payload.days, max_codes=payload.max_codes,
            capital=payload.capital, seed=payload.seed, apply=payload.apply)
        threading.Thread(target=_run_evolve, args=(args,), daemon=True).start()
    finally:
        _EVOLVE_LOCK.release()
    return ok({"accepted": True, "strategy": sid,
               "evaluations": args["population"] * args["generations"]})


@router.get("/evolve/status")
def evolve_status(request: Request):
    return ok(dict(_EVOLVE))


@router.post("/evolve/apply")
def evolve_apply(request: Request, payload: LabPaperRequest):
    """把已搜到的最优基因组采纳到账户（不重跑搜索）。"""
    lab = get_lab()
    sid = payload.strategy
    if not sid:
        raise HTTPException(status_code=400, detail="缺少 strategy")
    result = lab.evolution(sid)
    if not result or not result.get("best"):
        raise HTTPException(status_code=404, detail="这条策略还没有可用的进化结果")
    acc = _apply_evolution(sid, result)
    return ok({"applied": sid, "generation": acc["generation"],
               "score": acc.get("fitness")})


# ---------------------------------------------------------------- 模拟盘
@router.post("/paper/tick")
def paper_tick(request: Request, payload: LabPaperRequest):
    """推进模拟盘：每个策略各前进一格（缺失的交易日会逐格补齐）。"""
    mkt = market()
    lab = get_lab()
    profile = store().profile()
    targets = [payload.strategy] if payload.strategy else [r["id"] for r in _roster()]
    out: dict[str, dict] = {}
    for sid in targets:
        if payload.reset:
            lab.drop_account(sid)
        spec = _spec_of(sid)
        acc = _account(sid, spec, payload.capital)
        acc, info = paper.step(mkt, acc, profile, force=payload.force)
        lab.put_account(acc)
        out[sid] = info
    return ok({
        "as_of": mkt.trade_date.isoformat() if mkt.trade_date else None,
        "results": out,
        "reset": bool(payload.reset),
    })


@router.post("/paper/reset")
def paper_reset(request: Request, payload: LabPaperRequest):
    """清空模拟盘账户。`strategy` 留空 = 全部清空。"""
    lab = get_lab()
    if payload.strategy:
        existed = lab.drop_account(payload.strategy)
        return ok({"cleared": 1 if existed else 0, "strategy": payload.strategy})
    return ok({"cleared": lab.reset()})


@router.get("/plans")
def all_plans(request: Request):
    """所有策略今日的计划卡，按策略分组。等价于 overview.plans 的轻量版。"""
    mkt = market()
    lab = get_lab()
    accounts = lab.accounts()
    groups = []
    for item in _roster():
        acc = accounts.get(item["id"])
        if not acc:
            continue
        plans = paper.ensure_order_state(acc.get("plans") or [],
                                         acc.get("positions") or [],
                                         acc.get("params") or {})
        groups.append({
            "strategy_id": item["id"],
            "strategy_name": item["name"],
            "generation": int(acc.get("generation") or 0),
            "last_run": acc.get("last_run"),
            "items": plans,
        })
    return ok({
        "as_of": mkt.trade_date.isoformat() if mkt.trade_date else None,
        "groups": groups,
        "total": sum(len(g["items"]) for g in groups),
    })


# ---------------------------------------------------------------- 自动驾驶
@router.get("/autopilot")
def autopilot_status(request: Request):
    """盘中盯盘 + 收盘复盘的最新状态。前端 30 秒轮询一次。"""
    from ..autopilot import get_pilot
    from alphaforge_engine import llm

    pilot = get_pilot()
    return ok({**pilot.status(), "llm_enabled": llm.enabled(), "llm": llm.status()})


@router.post("/llm/test")
def llm_test(request: Request):
    """验一下模型接入通不通，返回失败原因而不是静默降级。

    页面上「规则判断」有两种可能：没配 key，或者配了但调不通。不把失败原因
    透出来，用户填完 key 发现结论没变只会以为功能坏了。
    """
    from alphaforge_engine import llm

    result = llm.ping()
    return ok(result)


@router.post("/autopilot/toggle")
def autopilot_toggle(request: Request, payload: dict | None = None):
    """开/关自动驾驶。开了之后进程活着就一直在跑，重启后自动恢复。"""
    from ..autopilot import get_pilot

    pilot = get_pilot()
    want = bool((payload or {}).get("enabled", True))
    pilot.start() if want else pilot.stop()
    return ok(pilot.status())


@router.post("/autopilot/run")
def autopilot_run(request: Request, payload: dict | None = None):
    """手动触发一轮：`mode=intraday` 立刻盯一次盘，`mode=eod` 补跑收盘任务。

    收盘任务会同步日线 + 进化，几十秒起步，所以放在后台线程里跑，
    接口立刻返回——前端看 GET /lab/autopilot 的 running 字段。
    """
    from ..autopilot import get_pilot

    pilot = get_pilot()
    mode = str((payload or {}).get("mode") or "intraday")
    if mode == "eod":
        force = bool((payload or {}).get("force"))
        try:
            pilot.eod_gate(force)
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        pilot.reset_eod()
        # 补跑要同步日线 + 跑两轮进化，几十秒到几分钟，不能同步等
        threading.Thread(target=pilot.run_eod, kwargs={"force": force},
                         daemon=True).start()
        return ok({"accepted": True, "mode": "eod"})
    doc = pilot.run_intraday()
    return ok({"accepted": True, "mode": "intraday", "watch": doc})


@router.get("/autopilot/history")
def autopilot_history(request: Request, limit: int = 30):
    """盘中分析历史，最新的在前。"""
    from ..autopilot import get_pilot

    rows = list(get_pilot().status().get("history") or [])
    return ok({"items": list(reversed(rows))[:max(1, min(limit, 60))]})
