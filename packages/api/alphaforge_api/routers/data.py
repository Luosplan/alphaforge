"""数据接口：库状态、增量同步触发、数据源说明。

同步是个长任务（全量 15–25 分钟），所以 POST /data/sync 只负责**发起**
并立即返回，进度靠轮询 /data/status 看。同一时刻只允许一个同步任务，
重复发起会拿到 409 而不是排一队任务把机器压垮。
"""

from __future__ import annotations

import threading

from fastapi import APIRouter, HTTPException, Query, Request

from ..deps import market, ok

router = APIRouter(prefix="/data", tags=["data"])

_SYNC_LOCK = threading.Lock()
_SYNC_STATE: dict = {"running": False, "step": None, "started": None, "result": None, "error": None}


def _run_sync(only: str, workers: int) -> None:
    from alphaforge_engine.market import reset_market

    from alphaforge_data import Repo
    from alphaforge_data.sync import Syncer

    try:
        repo = Repo()
        syncer = Syncer(repo, workers=workers, log=lambda m: _SYNC_STATE.update(step=m))
        _SYNC_STATE["result"] = syncer.run(only)
    except Exception as exc:                              # noqa: BLE001
        _SYNC_STATE["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        _SYNC_STATE["running"] = False
        _SYNC_STATE["step"] = None
        # 数据换了，引擎里的缓存与标的表必须重来
        reset_market()
        try:
            from alphaforge_core.fields import set_industries

            set_industries(Repo().industries())
        except Exception:                                  # noqa: BLE001
            pass


@router.get("/status")
def status(request: Request):
    """库里有多少数据、同步到哪天、最近跑了哪些任务。"""
    data = market().repo.status()
    data["sync"] = {
        "running": _SYNC_STATE["running"],
        "step": _SYNC_STATE["step"],
        "result": _SYNC_STATE["result"],
        "error": _SYNC_STATE["error"],
    }
    return ok(data)


@router.post("/sync")
def sync(
    request: Request,
    only: str = Query("all", description="all / universe / fundamentals / dividend / daily / flow / index / factor"),
    workers: int = Query(10, ge=1, le=20),
):
    """发起同步（后台跑，立即返回）。"""
    global _SYNC_STATE
    if not _SYNC_LOCK.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="已有同步任务在跑")
    try:
        if _SYNC_STATE["running"]:
            raise HTTPException(status_code=409, detail="已有同步任务在跑")
        _SYNC_STATE = {"running": True, "step": "starting", "started": None,
                       "result": None, "error": None}
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
