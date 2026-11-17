"""行情接口：K 线、分时、快照、搜索、排行、市场概览、资金流。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request

from alphaforge_core.indicators import compute_all
from alphaforge_engine.panorama import index_book, market_temperature
from alphaforge_engine.signal import active_plans

from ..deps import market, ok

router = APIRouter(prefix="/market", tags=["market"])


@router.get("/overview")
def market_overview(request: Request):
    return ok(market().market_overview())


@router.get("/quote")
def quote(
    request: Request,
    codes: str | None = Query(default=None, description="逗号分隔代码"),
    code: str | None = Query(default=None, description="单只代码，等价于 codes 只传一个"),
):
    """当前截面。不传代码返回全池（行情列表页用）。

    `code` 是 `codes` 的单只写法。接受它是为了避免「写错参数名 → 静默返回
    5000 多行」这种坑：参数名对不上时框架不会报错，只会当没传。
    """
    raw = codes or code or ""
    code_list = [c for c in raw.split(",") if c]
    return ok(market().quotes(code_list or None))


@router.get("/live")
def live(
    request: Request,
    codes: str = Query("", description="逗号分隔代码，最多 200 只"),
    indices: bool = Query(True, description="是否一并返回 7 大指数实时点位"),
):
    """实时报价（盘中轮询用），3 秒内同一代码共享一次上游请求。

    与 `/quote` 的区别：`/quote` 读库里同步下来的截面（一天一变），
    `/live` 直接向上游问当前价。**两者不互相替代**——`/live` 给价格与
    成交，`/quote` 给 PE / ROE / 行业这些一天只变一次的基本面。

    响应里的 `session` 是服务端按「钟点 + 报价日期」算出的时段，
    `is_live` 为 False 时前端应当停止轮询、退回 `/quote` 的数据。
    """
    code_list = [c for c in codes.split(",") if c][:200]
    engine = market()
    payload = engine.live_board(code_list) if indices else engine.live_quotes(code_list)
    return ok(payload)


@router.get("/bars")
def bars(
    request: Request,
    code: str = Query(..., description="600519.SH"),
    freq: str = Query("1d", pattern="^(1d|1w|1M|60m)$"),
    adjust: str = Query("qfq", pattern="^(qfq|hfq|none)$"),
    limit: int = Query(250, ge=30, le=520),
    indicators: str | None = Query(default="MA,MACD,BOLL", description="逗号分隔"),
):
    engine = market()
    rows = engine.bars(code, freq=freq, adjust=adjust, limit=limit)
    sets = {s.strip().upper() for s in (indicators or "").split(",") if s.strip()}
    ind = compute_all(rows, sets) if sets else {}
    quote_row = engine.quote(code)
    symbol = {"code": code, "name": quote_row["name"], "industry": quote_row["industry"], "board": quote_row["board"]}
    zones = [p for p in active_plans(engine) if p["code"] == code]
    return ok({
        "symbol": symbol,
        "freq": freq,
        "adjust": adjust,
        "bars": rows,
        "indicators": ind,
        "plans": zones,
        "quote": quote_row,
    })


@router.get("/minute")
def minute(request: Request, code: str = Query(...)):
    engine = market()
    data = engine.minute(code)
    # 分时曲线已经是「到当前分钟」的实时值，报价就不能还挂库里的收盘价，
    # 否则同一张图上曲线在 14:11、标价是上周五收盘，自相矛盾
    live_rows = engine.live_quotes([code])["items"]
    data["quote"] = live_rows[0] if live_rows else engine.quote(code)
    return ok(data)


@router.get("/indices")
def indices(request: Request):
    """7 个主要指数：点位、涨跌幅、成交额、成分涨跌家数、迷你走势。"""
    return ok(index_book(market()).list())


@router.get("/indices/{code}")
def index_detail(request: Request, code: str, days: int = Query(120, ge=20, le=250)):
    book = index_book(market())
    if code not in {item["code"] for item in book.list(spark=1)}:
        raise HTTPException(status_code=404, detail="指数不存在")
    return ok(book.detail(code, days=days))


@router.get("/temperature")
def temperature(request: Request):
    """市场温度 0–100：分数、档位、五项分项拆解。"""
    return ok(market_temperature(market()))


@router.get("/search")
def search(request: Request, q: str = Query("", max_length=30), limit: int = Query(10, ge=1, le=30)):
    return ok(market().search(q, limit=limit))


@router.get("/ranking")
def ranking(request: Request, kind: str = Query("gainers", pattern="^(gainers|losers|amount|turnover)$"),
            limit: int = Query(10, ge=1, le=40)):
    return ok(market().ranking(kind, limit))


@router.get("/sector-flow")
def sector_flow(request: Request, limit: int = Query(12, ge=1, le=30)):
    return ok(market().sector_flow()[:limit])


@router.get("/stock-flow")
def stock_flow(request: Request, code: str = Query(..., description="代码")):
    return ok(market().stock_flow(code))


@router.get("/symbols")
def symbols(request: Request):
    """全量标的 + 行业清单。5000+ 条，前端只在需要本地联想时才拉。"""
    engine = market()
    return ok({
        "industries": engine.repo.industries(),
        "total": len(engine.universe()),
        "items": [
            {"code": s["code"], "name": s["name"], "industry": s["industry"], "board": s["board"]}
            for s in engine.universe()
        ],
    })
