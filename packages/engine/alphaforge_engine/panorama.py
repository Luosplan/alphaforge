"""市场全景：真实指数 + 0–100 市场温度。

旧版指数是「40 只成分按市值加权合成再缩放到锚点」，只能当方向性代理。
现在指数点位直接取真实行情（腾讯指数日线与实时），涨跌、5/20/60 日动量、
成交额都是市场给的值，不再有任何缩放。

成分股只在拿得到官方成分表时才有：沪深300 / 上证50 / 中证500 / 科创50 来自
东财成分表，上证指数按定义就是全部沪市。深证成指与创业板指是抽样指数，
没有官方成分表就不硬凑——少了「领涨股」这一块，总比把整个板块冒充成
100 只成分股要诚实。
"""

from __future__ import annotations

import statistics

from .market import MarketEngine

INDEX_DEFS: list[dict] = [
    {"code": "000001.SH", "name": "上证指数", "kind": "market"},
    {"code": "399001.SZ", "name": "深证成指", "kind": "market"},
    {"code": "399006.SZ", "name": "创业板指", "kind": "market"},
    {"code": "000688.SH", "name": "科创50", "kind": "market"},
    {"code": "000300.SH", "name": "沪深300", "kind": "broad"},
    {"code": "000016.SH", "name": "上证50", "kind": "broad"},
    {"code": "000905.SH", "name": "中证500", "kind": "broad"},
]

INDEX_MAP: dict[str, dict] = {d["code"]: d for d in INDEX_DEFS}


class IndexBook:
    def __init__(self, engine: MarketEngine):
        self.engine = engine
        self.repo = engine.repo

    def _values(self, code: str) -> tuple[list[float], list[str]]:
        series = self.engine.index_series(code)
        if not series:
            return [], []
        return series.raw_close, [d.isoformat() for d in series.dates]

    def quote(self, code: str, spark: int = 60) -> dict:
        meta = INDEX_MAP.get(code, {"code": code, "name": code, "kind": "market"})
        live = self.repo.index_quote(code) or {}
        values, _ = self._values(code)

        if values:
            last = values[-1]
            prev = values[-2] if len(values) > 1 else last
        else:
            last = float(live.get("value") or 0)
            prev = float(live.get("prev_close") or last)

        def pct(back: int) -> float:
            if len(values) <= back or not values[-1 - back]:
                return 0.0
            return round((last / values[-1 - back] - 1) * 100, 2)

        breadth = self.repo.index_breadth(code)
        change_pct = round((last / prev - 1) * 100, 2) if prev else float(live.get("change_pct") or 0)

        return {
            "code": code,
            "name": meta["name"],
            "kind": meta["kind"],
            "value": round(last, 2),
            "change": round(last - prev, 2) if prev else 0.0,
            "change_pct": change_pct,
            "chg5": pct(5),
            "chg20": pct(20),
            "chg60": pct(60),
            # `repo.index_quote()` 已经把「元」折成「亿元」，这里不再折第二次
            "amount": round(float(live.get("amount") or 0), 1),
            "up": breadth["up"],
            "down": breadth["down"],
            "flat": breadth["flat"],
            "members": breadth["members"],
            "top": breadth["top"],
            # 有没有官方成分表：前端据此决定要不要渲染涨跌家数与领涨股
            "has_constituents": breadth["members"] > 0,
            "spark": values[-spark:] if values else [],
            "ts": live.get("ts"),
        }

    def list(self, spark: int = 60) -> list[dict]:
        return [self.quote(d["code"], spark=spark) for d in INDEX_DEFS]

    def detail(self, code: str, days: int = 120, member_limit: int = 200) -> dict:
        quote = self.quote(code)
        series = self.engine.index_series(code)
        rows: list[dict] = []
        if series:
            closes = series.raw_close
            dates = [d.isoformat() for d in series.dates]
            start = max(len(closes) - days, 0)
            for i in range(start, len(closes)):
                rows.append({
                    "date": dates[i],
                    "value": round(closes[i], 2),
                    "change_pct": round((closes[i] / closes[i - 1] - 1) * 100, 2) if i else 0.0,
                })
        members = self.repo.index_constituent_quotes(code, limit=member_limit)
        # 领跌单独取一次跌幅榜。成分表按涨幅倒序截断后，尾部只是「涨得少的」，
        # 直接拿它当领跌会给出错误榜单。
        laggards = self.repo.index_constituent_quotes(code, limit=12, asc=True)
        quote["series"] = rows
        quote["constituents"] = members
        quote["laggards"] = laggards
        quote["constituent_total"] = quote["members"]
        return quote


def index_book(engine: MarketEngine) -> IndexBook:
    book = getattr(engine, "_index_book", None)
    if book is None:
        book = IndexBook(engine)
        engine._index_book = book
    return book


# ------------------------------------------------------------------ 市场温度
TEMP_BANDS: list[tuple[float, str, str]] = [
    (20, "冰点", "成交萎缩、赚钱效应极差，适合观察不宜追涨"),
    (40, "偏冷", "情绪偏弱，等企稳信号再考虑加仓"),
    (60, "中性", "多空均衡，按计划卡分批执行即可"),
    (78, "偏热", "赚钱效应扩散，注意止盈与仓位上限"),
    (101, "过热", "普涨伴随高波动，右侧追高风险大"),
]


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def market_temperature(engine: MarketEngine) -> dict:
    """0–100 的市场温度 + 分项拆解。

    五项都是「已换算到 0–100 的分数」再加权，所以口径可核对：
    涨跌家数 30%、涨停强度 15%、指数动量 20%、量能 15%、站上 MA20 比例 20%。
    全部输入来自全市场真实数据，样本数是 5000+ 而不是 40 只。
    """
    repo = engine.repo
    breadth = repo.breadth()
    up, down = breadth["up"], breadth["down"]
    total = max(up + down, 1)
    limit_up, limit_down = breadth["limit_up"], breadth["limit_down"]

    hs300 = index_book(engine).quote("000300.SH")
    chg = hs300["change_pct"]

    # 量能：当日成交额相对近 20 日均值。`breadth.amount` 与 `amount_history`
    # 都是「元」，直接相除；不要在这里再乘除 1e8，否则量能分项会落到 0。
    hist = repo.amount_history(21)
    amount_today = breadth["amount"]
    prior = [amt for _, amt in hist[:-1]] if len(hist) > 1 else []
    avg_amount = statistics.fmean(prior) if prior else amount_today
    amount_ratio = amount_today / avg_amount if avg_amount else 1.0

    above, universe = repo.ma20_ratio()
    above_ratio = above / universe * 100 if universe else 0.0

    parts = [
        {"key": "breadth", "label": "涨跌家数", "weight": 0.30,
         "raw": f"{up} 涨 / {down} 跌", "score": round(up / total * 100, 1)},
        {"key": "limit", "label": "涨停强度", "weight": 0.15,
         "raw": f"{limit_up} 涨停 / {limit_down} 跌停",
         # 斜率按 A 股实际分布定：常态净涨停 -30~+80 家，×0.8 后落在 26~100 之间。
         # 早先用 ×8，净涨停 7 家以上就直接顶到 100 分，等于这一项永远满分。
         "score": round(_clamp(50 + (limit_up - limit_down) * 0.8), 1)},
        {"key": "index", "label": "指数动量", "weight": 0.20,
         "raw": f"沪深300 {chg:+.2f}%", "score": round(_clamp(50 + chg * 12), 1)},
        {"key": "amount", "label": "量能", "weight": 0.15,
         "raw": f"成交额 {amount_ratio:.2f}× 20 日均值",
         "score": round(_clamp(50 + (amount_ratio - 1) * 120), 1)},
        {"key": "ma20", "label": "站上 MA20", "weight": 0.20,
         "raw": f"{above}/{universe} 只在 20 日线上方",
         "score": round(above_ratio, 1)},
    ]

    score = round(sum(p["score"] * p["weight"] for p in parts), 1)
    band = next((b for b in TEMP_BANDS if score < b[0]), TEMP_BANDS[-1])
    return {
        "trade_date": breadth["trade_date"],
        "score": score,
        "label": band[1],
        "advice": band[2],
        "parts": parts,
        "up": up,
        "down": down,
        "flat": breadth["flat"],
        "limit_up": limit_up,
        "limit_down": limit_down,
        "amount": breadth["amount"],
        "amount_ratio": round(amount_ratio, 2),
        "above_ma20_ratio": round(above_ratio, 1),
        "universe": universe,
        "bands": [{"max": b[0], "label": b[1], "advice": b[2]} for b in TEMP_BANDS],
    }


def temperature_band(score: float) -> tuple[str, str]:
    for limit, label, advice in TEMP_BANDS:
        if score < limit:
            return label, advice
    return TEMP_BANDS[-1][1], TEMP_BANDS[-1][2]
