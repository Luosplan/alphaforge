"""市场全景：真实指数 + 0–100 市场温度 + 全球指数。

旧版指数是「40 只成分按市值加权合成再缩放到锚点」，只能当方向性代理。
现在指数点位直接取真实行情（腾讯指数日线与实时），涨跌、5/20/60 日动量、
成交额都是市场给的值，不再有任何缩放。

成分股只在拿得到官方成分表时才有：沪深300 / 上证50 / 中证500 / 科创50 来自
东财成分表，上证指数按定义就是全部沪市。深证成指与创业板指是抽样指数，
没有官方成分表就不硬凑——少了「领涨股」这一块，总比把整个板块冒充成
100 只成分股要诚实。

全球指数（`GlobalBook`）走另一条链路：不落库，直接问上游并做分钟级缓存。
理由是不该为了 16 个点位每天同步 16 × 520 根日线；而缺的「5/20 日动量」
宁可不出，也不拿 A 股日线的交易日历去凑——各市场休市日不同，凑出来的
区间涨跌幅是错的。
"""

from __future__ import annotations

import statistics
import threading
import time

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

# 全球指数：`lat` / `lon` 是交易所所在城市，前端用来在地图上定位。
# 同一个城市的多个指数共用坐标，由前端按城市堆叠，避免三个纽约点位叠成一个。
# 地区名按「中国香港 / 中国台湾」的国家标准写法，不要省成「香港 / 台湾」。
GLOBAL_DEFS: list[dict] = [
    {"code": "DJIA", "name": "道琼斯", "short": "道琼斯", "region": "美洲", "place": "美国 · 纽约",
     "lat": 40.71, "lon": -74.01},
    {"code": "NDX", "name": "纳斯达克", "short": "纳指", "region": "美洲", "place": "美国 · 纽约",
     "lat": 40.71, "lon": -74.01},
    {"code": "SPX", "name": "标普500", "short": "标普500", "region": "美洲", "place": "美国 · 纽约",
     "lat": 40.71, "lon": -74.01},
    {"code": "FTSE", "name": "英国富时100", "short": "富时100", "region": "欧洲", "place": "英国 · 伦敦",
     "lat": 51.51, "lon": -0.13},
    {"code": "FCHI", "name": "法国CAC40", "short": "CAC40", "region": "欧洲", "place": "法国 · 巴黎",
     "lat": 48.86, "lon": 2.35},
    {"code": "GDAXI", "name": "德国DAX30", "short": "DAX30", "region": "欧洲", "place": "德国 · 法兰克福",
     "lat": 50.11, "lon": 8.68},
    {"code": "RTS", "name": "俄罗斯RTS", "short": "RTS", "region": "欧洲", "place": "俄罗斯 · 莫斯科",
     "lat": 55.76, "lon": 37.62},
    {"code": "N225", "name": "日经225", "short": "日经225", "region": "亚太", "place": "日本 · 东京",
     "lat": 35.68, "lon": 139.69},
    {"code": "KS11", "name": "韩国KOSPI", "short": "KOSPI", "region": "亚太", "place": "韩国 · 首尔",
     "lat": 37.57, "lon": 126.98},
    {"code": "TWII", "name": "台湾加权", "short": "台湾加权", "region": "亚太", "place": "中国台湾 · 台北",
     "lat": 25.03, "lon": 121.56},
    {"code": "HSI", "name": "恒生指数", "short": "恒生", "region": "亚太", "place": "中国香港",
     "lat": 22.32, "lon": 114.17},
    {"code": "HSCEI", "name": "恒生国企", "short": "国企", "region": "亚太", "place": "中国香港",
     "lat": 22.32, "lon": 114.17},
    {"code": "STI", "name": "新加坡海峡时报", "short": "新加坡", "region": "亚太", "place": "新加坡",
     "lat": 1.29, "lon": 103.85},
    {"code": "SENSEX", "name": "印度SENSEX", "short": "SENSEX", "region": "亚太", "place": "印度 · 孟买",
     "lat": 19.08, "lon": 72.88},
    {"code": "AS51", "name": "澳洲标普200", "short": "澳洲200", "region": "亚太", "place": "澳大利亚 · 悉尼",
     "lat": -33.87, "lon": 151.21},
    # 美元指数没有交易所坐标，只作为一张卡片出现，不上地图
    {"code": "UDI", "name": "美元指数", "short": "美元指数", "region": "汇率", "place": "",
     "lat": None, "lon": None},
]

GLOBAL_MAP: dict[str, dict] = {d["code"]: d for d in GLOBAL_DEFS}


class IndexBook:
    def __init__(self, engine: MarketEngine):
        self.engine = engine
        self.repo = engine.repo

    def _values(self, code: str) -> tuple[list[float], list[str]]:
        series = self.engine.index_series(code)
        if not series:
            return [], []
        return series.raw_close, [d.isoformat() for d in series.dates]

    def quote(self, code: str, spark: int = 60, live_hit: dict | None = None) -> dict:
        meta = INDEX_MAP.get(code, {"code": code, "name": code, "kind": "market"})
        live = self.repo.index_quote(code) or {}
        values, dates = self._values(code)

        # 指数日线要等收盘后才入库，库里的序列天然缺「今天」。实时值比序列新时
        # 就把今天并进序列：最新价、涨跌幅、5/20/60 日动量必须基于同一份价格，
        # 否则标题写着今天的点位、曲线却停在昨天，或者反过来。
        hit = live_hit if live_hit is not None else self._live(code)
        day = str(hit.get("ts") or "")[:10]
        price = float(hit.get("price") or 0)
        if price > 0 and day and (not dates or day > dates[-1]):
            values = [*values, price]
            dates = [*dates, day]
            live = {
                **live,
                "ts": hit.get("ts"),
                # 实时接口给的成交额是「元」，与库里的口径一致后再折成亿元
                "amount": round(float(hit.get("amount") or 0) / 1e8, 1) or live.get("amount"),
            }

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

    @staticmethod
    def _live(code: str) -> dict:
        try:
            from alphaforge_data.live import feed

            return (feed().quotes([code]) or {}).get(code) or {}
        except Exception:                                  # noqa: BLE001 - 实时链路不许炸
            return {}

    def list(self, spark: int = 60) -> list[dict]:
        # 7 个指数一次批量取，别在 quote() 里逐个打请求
        try:
            from alphaforge_data.live import feed

            hits = feed().quotes([d["code"] for d in INDEX_DEFS]) or {}
        except Exception:                                  # noqa: BLE001
            hits = {}
        return [
            self.quote(d["code"], spark=spark, live_hit=hits.get(d["code"]) or {})
            for d in INDEX_DEFS
        ]

    def detail(self, code: str, days: int = 120, member_limit: int = 200) -> dict:
        # 实时值取一次，报价与当日那根点共用，避免同一请求里两次口径
        hit = self._live(code)
        quote = self.quote(code, live_hit=hit)
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
        # 指数日线同样要等收盘后才入库，当日的缺口用实时值补一个点，
        # 否则标题写着今天的点位、走势图却停在昨天
        session = str(hit.get("ts") or "")[:10]
        price = float(hit.get("price") or 0)
        if session and price > 0 and (not rows or session > rows[-1]["date"]):
            prev = rows[-1]["value"] if rows else float(hit.get("prev_close") or price)
            rows.append({
                "date": session,
                "value": round(price, 2),
                "change_pct": round((price / prev - 1) * 100, 2) if prev else 0.0,
                "provisional": True,
            })
        members = self.repo.index_constituent_quotes(code, limit=member_limit)        # 领跌单独取一次跌幅榜。成分表按涨幅倒序截断后，尾部只是「涨得少的」，
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


# ------------------------------------------------------------------ 全球指数

class GlobalBook:
    """全球指数：上游直取 + 分钟级缓存，不落库。

    缓存只做「短路」不做「兜底」——但兜底放宽了一档：北京时间白天，16 个
    市场里有 12 个处在收盘或未开盘状态，上游偶发抽风时把上一轮的值标上
    `stale` 返回，比整块面板空着有用。超过 `STALE_TTL` 的旧值不再返回，
    宁可显示「—」也不给一个半小时前的点位冒充现价。
    """

    TTL = 45.0
    STALE_TTL = 600.0

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._rows: list[dict] = []
        self._stamp = 0.0
        self._failures = 0

    def rows(self) -> list[dict]:
        now = time.monotonic()
        with self._lock:
            if self._rows and now - self._stamp < self.TTL:
                return self._rows

        from alphaforge_data.sources import eastmoney

        fetched = eastmoney.global_indices()
        with self._lock:
            if fetched:
                self._rows = fetched
                self._stamp = time.monotonic()
            elif self._rows and time.monotonic() - self._stamp > self.STALE_TTL:
                # 旧到不可信就清掉，让调用方走「无数据」分支
                self._rows = []
            self._failures = 0 if fetched else self._failures + 1
            return self._rows

    def list(self) -> dict:
        """按 `GLOBAL_DEFS` 的顺序输出，上游没给的指数直接缺席。"""
        fetched = {r["code"]: r for r in self.rows()}
        stale = bool(fetched) and self._failures > 0
        items: list[dict] = []
        for meta in GLOBAL_DEFS:
            hit = fetched.get(meta["code"])
            if not hit:
                continue
            items.append({
                "code": meta["code"],
                "name": meta["name"],
                "short": meta["short"],
                "region": meta["region"],
                "place": meta["place"],
                "lat": meta["lat"],
                "lon": meta["lon"],
                "value": hit["value"],
                "prev_close": hit["prev_close"],
                "change": hit["change"],
                "change_pct": hit["change_pct"],
                "ts": hit["ts"],
                "stale": stale,
            })
        as_of = max((i["ts"] for i in items if i["ts"]), default="")
        return {"items": items, "as_of": as_of, "missing": len(GLOBAL_DEFS) - len(items)}


def global_book(engine: MarketEngine) -> GlobalBook:
    book = getattr(engine, "_global_book", None)
    if book is None:
        book = GlobalBook()
        engine._global_book = book
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
