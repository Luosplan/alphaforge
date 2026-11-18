"""数据接口：库状态、增量同步触发、数据源说明。

同步是个长任务，`POST /data/sync` 只负责**发起**并立即返回，进度靠轮询
`/data/status` 看。同一时刻只允许一个同步任务，重复发起会拿到 409 而不是
排一队任务把机器压垮。

进度分两层：`overall`（0–100，含步骤权重）给总进度条，`done/total`
（如 3210/5578 只）说明具体在抓第几只，`eta` 是按当前速度外推的剩余秒数。
只有 `step` 一个字符串的话，用户只能看到「日线」两个字，十几分钟里
不知道它是在跑还是在卡着。
"""

from __future__ import annotations

import datetime as dt
import threading
import time

from fastapi import APIRouter, HTTPException, Query, Request

from ..deps import market, ok

router = APIRouter(prefix="/data", tags=["data"])

_SYNC_LOCK = threading.Lock()
_SYNC_STATE: dict = {
    "running": False,
    "only": None,
    "workers": None,
    # 当前步骤与细分进度
    "step": None,
    "message": None,
    "done": 0,
    "total": 0,
    "percent": 0.0,
    # 总进度
    "overall": 0.0,
    "steps_done": 0,
    "steps_total": 0,
    # 时间
    "started_at": None,
    "finished_at": None,
    "elapsed": 0.0,
    "eta": None,
    "result": None,
    "error": None,
}

STEP_LABELS = {
    "universe": "标的名单",
    "fundamentals": "ROE / 行业",
    "bonus": "分红送配",
    "daily": "日线",
    "flow": "资金流",
    "index": "指数",
    "factor": "因子",
    "starting": "启动中",
}


def _reset_state(only: str, workers: int) -> None:
    """原地清空而不是换一个新 dict：后台线程持有的是同一个引用。"""
    _SYNC_STATE.update({
        "running": True, "only": only, "workers": workers,
        "step": "starting", "message": "启动中", "done": 0, "total": 0,
        "percent": 0.0, "overall": 0.0, "steps_done": 0, "steps_total": 0,
        "started_at": dt.datetime.now().isoformat(timespec="seconds"),
        "finished_at": None, "elapsed": 0.0, "eta": None,
        "result": None, "error": None,
    })


def _run_sync(only: str, workers: int) -> None:
    from alphaforge_engine.market import reset_market

    from alphaforge_data import Repo
    from alphaforge_data.sync import Syncer

    t0 = time.monotonic()

    def on_progress(p: dict) -> None:
        elapsed = round(time.monotonic() - t0, 1)
        overall = float(p.get("overall") or 0.0)
        # ETA 只在「样本足够」时给：分母太小、或者刚跑完几步很便宜的步骤时，
        # 按整体比例外推会算出离谱的数字（3 分钟实际只要 1 分钟）。
        # 单步任务（只补日线）看自身进度就够准；全量任务要等过几步再报。
        steps_total = int(p.get("steps_total") or 0)
        enough = (steps_total <= 1 and overall >= 10) or (
            steps_total > 1 and int(p.get("steps_done") or 0) >= 3 and overall >= 10
        )
        eta = round(elapsed / overall * (100 - overall), 1) if enough else None
        _SYNC_STATE.update({
            "step": p.get("step"),
            "label": STEP_LABELS.get(str(p.get("step")), p.get("step")),
            "message": p.get("message"),
            "done": int(p.get("done") or 0),
            "total": int(p.get("total") or 0),
            "percent": float(p.get("percent") or 0.0),
            "overall": overall,
            "steps_done": int(p.get("steps_done") or 0),
            "steps_total": int(p.get("steps_total") or 0),
            "elapsed": elapsed,
            "eta": eta,
        })

    try:
        repo = Repo()
        syncer = Syncer(repo, workers=workers, log=lambda m: _SYNC_STATE.update(message=m),
                        progress=on_progress)
        _SYNC_STATE["result"] = syncer.run(only)
    except Exception as exc:                              # noqa: BLE001
        _SYNC_STATE["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        _SYNC_STATE["running"] = False
        _SYNC_STATE["step"] = None
        _SYNC_STATE["finished_at"] = dt.datetime.now().isoformat(timespec="seconds")
        _SYNC_STATE["elapsed"] = round(time.monotonic() - t0, 1)
        _SYNC_STATE["eta"] = 0
        _SYNC_STATE["overall"] = 100.0 if not _SYNC_STATE.get("error") else _SYNC_STATE["overall"]
        # 数据换了，引擎里的缓存与标的表必须重来
        reset_market()
        try:
            from alphaforge_core.fields import set_industries

            set_industries(Repo().industries())
        except Exception:                                  # noqa: BLE001
            pass


@router.get("/status")
def status(request: Request):
    """库里有多少数据、同步到哪天、最近跑了哪些任务，以及正在跑的进度。"""
    data = market().repo.status()
    data["sync"] = dict(_SYNC_STATE)
    return ok(data)


@router.post("/sync")
def sync(
    request: Request,
    only: str = Query("all", description="all / universe / fundamentals / bonus / daily / flow / index / factor"),
    workers: int = Query(16, ge=1, le=32),
):
    """发起同步（后台跑，立即返回）。进度看 GET /data/status 的 sync 字段。"""
    if not _SYNC_LOCK.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="已有同步任务在跑")
    try:
        if _SYNC_STATE["running"]:
            raise HTTPException(status_code=409, detail="已有同步任务在跑")
        _reset_state(only, workers)
        thread = threading.Thread(target=_run_sync, args=(only, workers), daemon=True)
        thread.start()
    finally:
        _SYNC_LOCK.release()
    return ok({"accepted": True, "only": only, "workers": workers})


@router.get("/sources")
def sources(request: Request):
    """数据来源与口径说明。界面上「数据来源」那一栏直接读这里。"""
    return ok({
        "sources": [
            {"name": "新浪财经", "role": "全 A 名单 / 横截面快照 / 个股与指数日线 / 个股资金流",
             "host": "vip.stock.finance.sina.com.cn, money.finance.sina.com.cn"},
            {"name": "新浪行情", "role": "实时报价（个股 + 指数，盘中 3 秒） / 当日分时（20 秒）",
             "host": "hq.sinajs.cn, quotes.sina.cn"},
            {"name": "东方财富", "role": "ROE / 二级行业分类 / 分红送配 / 指数成分",
             "host": "datacenter-web.eastmoney.com"},
        ],
        "notes": [
            "日线为不复权原始价，复权因子由分红送配精确推算："
            "除权参考价 =（除权前收盘 − 每股派息）÷（1 + 送转比例）。",
            "日线历史成交额为「成交量 × 均价」推算值，与真实值偏差通常 1%–3%；最新交易日用快照真实值覆盖。",
            "主力净流入口径为新浪「超大单 + 大单」净额；大单由净额减超大单反推，中单 / 小单不编造，留 0。",
            "指数日线的成交额源不提供，恒为 0，界面不展示该项，不拿「量 × 点数」凑数。",
            "深证成指 / 创业板指没有官方成分表，只展示指数自身走势，不用板块范围冒充成分股。",
            "分时数据在打开个股时实时抓取并缓存，断网回放时显示最近一次缓存的曲线并标记为缓存。",
            "实时只覆盖「当前标的 + 自选 + 7 大指数」与分时曲线；涨跌家数、涨停家数、"
            "市场温度、涨跌幅榜需要全市场同一时刻截面，仍按同步批次口径（一天一变）。",
            "实时源取不到时界面退回上一次成功的价并标注延迟，超过 2 分钟则显示「—」，不把旧价当作实时。",
        ],
    })
