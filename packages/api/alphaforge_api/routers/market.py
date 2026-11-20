"""行情接口：K 线、分时、快照、搜索、排行、市场概览、资金流。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request

from alphaforge_core.indicators import compute_all
from alphaforge_engine import hotspot as hotspot_engine
from alphaforge_engine.panorama import global_book, index_book, market_temperature
from alphaforge_engine.profile import company_profile
from alphaforge_engine.signal import active_plans

from ..deps import market, ok, store

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
    rows = engine.bars(code, freq=freq, adjust=adjust, limit=limit, live=True)
    sets = {s.strip().upper() for s in (indicators or "").split(",") if s.strip()}
    ind = compute_all(rows, sets) if sets else {}
    # 报价也用实时值：K 线已经补上了今天那根，报价却还挂昨天的收盘价，
    # 同一屏里两个数字会当场打架。实时源取不到才退回库里的值。
    # 以库里的报价为底再盖实时字段：实时接口不提供 industry / board /
    # turnover_rate，整体替换会让这些字段消失。
    base_quote = engine.quote(code)
    live = engine.live_quotes([code]).get("items") or []
    quote_row = {**base_quote, **live[0]} if live else base_quote
    symbol = {"code": code, "name": quote_row["name"], "industry": quote_row["industry"], "board": quote_row["board"]}
    # 计划卡按购买权限过滤：行情页画出来的建仓区，和信号页能看到的必须是同一批，
    # 否则「这里有卡、计划页却没有」会被当成丢数据
    zones = [p for p in active_plans(engine, boards=store().profile()["boards"])["items"]
             if p["code"] == code]
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


@router.get("/chips")
def chips(
    request: Request,
    code: str = Query(..., description="代码"),
    days: int = Query(250, ge=60, le=520, description="参与计算的交易日数"),
    bins: int = Query(120, ge=30, le=300, description="价格轴分箱数"),
):
    """筹码分布（成本分布）。

    **不是真实持仓**——交易所不公开逐户持仓，这是「成交量按价格摊开 + 换手率
    逐日衰减」的近似模型，和通达信/同花顺同源。价格口径为前复权，与 K 线一致。
    """
    return ok(market().chips(code, days=days, bins=bins))


@router.get("/ticks")
def ticks(
    request: Request,
    code: str = Query(..., description="代码"),
    limit: int = Query(30, ge=5, le=200, description="返回最近多少笔"),
):
    """最近逐笔成交。

    上游失败时退回「分钟聚合」，响应里 `approx: true`、`side` 恒为 `mid`，
    前端不要把它当成真实逐笔渲染买卖方向。
    """
    return ok(market().ticks(code, limit=limit))


@router.get("/indices")
def indices(request: Request):
    """7 个主要指数：点位、涨跌幅、成交额、成分涨跌家数、迷你走势。"""
    return ok(index_book(market()).list())


@router.get("/global-indices")
def global_indices(request: Request):
    """全球主要指数：点位、涨跌、所在城市经纬度（前端据此在地图上打点）。

    不落库、不提供 5/20 日动量：各市场休市日不同，用 A 股的交易日历去凑
    区间涨跌幅会算错。要的是「此刻全球在涨还是在跌」，给日涨跌就够了。
    """
    return ok(global_book(market()).list())


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


@router.get("/hotspot")
def hotspot(request: Request,
            window: int = Query(5, description="涨幅窗口：1/3/5/10/20 日"),
            limit: int = Query(12, ge=3, le=40)):
    """今日热点：板块榜、龙头、周期相位、炒作主线。

    概念板块的历史是逐日累积的，刚建库时多窗口涨幅为 `null`（不是 0），
    `concept_days` 会告诉前端实际攒了几天。
    """
    if window not in hotspot_engine.WINDOWS:
        window = 5
    return ok(hotspot_engine.build(market(), window=window, limit=limit))


@router.get("/stock-flow")
def stock_flow(request: Request, code: str = Query(..., description="代码")):
    return ok(market().stock_flow(code))


@router.get("/stock/{code}/profile")
def stock_profile(
    request: Request,
    code: str,
    refresh: bool = Query(False, description="忽略缓存，强制回上游拉一次"),
):
    """公司概况：上市板 / 概念板块 / 主营业务 / 公司简介。

    慢接口——首次访问要回东财拉 F10，之后读本地缓存 30 天。前端在抽屉
    打开时发起，不要放进列表循环里。
    """
    engine = market()
    if not engine.symbol_valid(code):
        raise HTTPException(status_code=404, detail=f"标的 {code} 不存在")
    return ok(company_profile(engine, code, refresh=refresh))


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
