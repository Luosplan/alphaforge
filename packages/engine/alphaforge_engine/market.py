"""行情引擎：从本地库读真实行情。

与旧版的根本区别：这里**不再生成任何数据**。价格、成交量、资金流、指数点位
全部来自 `alphaforge_data` 落库的真实数据；取不到就是取不到，返回空并带上
`available: False`，绝不用合成序列顶替。

复权口径（对应 SRS K-12）：
- 库里只存不复权原始价与因子 `adj = 前复权价 / 原始价`；
- 前复权 `qfq = raw × adj`，后复权 `hfq = raw × adj / adj[-1]`，
  两者都由原始价推出，所以三档切换不会出现「同一天三个不同涨跌幅」。
"""

from __future__ import annotations

import datetime as dt
import time

from alphaforge_core.market_session import is_live, label as session_label, session_state
from alphaforge_core.universe import board_key as board_of

HISTORY_DAYS = 520

# 逐笔成交的上游（东财 push2）连发会被拉黑，同一只票 5 秒内复用结果。
# key 是代码，值是天真的 (抓取时刻, 响应)。
_TICK_CACHE: dict[str, tuple[float, dict]] = {}
_TICK_TTL = 5.0

# 实时报价里覆盖「当日状态」的字段。财务类字段（PE/ROE/行业/流通市值）
# 仍然从库里取——它们一天内不会变，没必要每秒重算。
LIVE_FIELDS = ("price", "prev_close", "open", "high", "low", "change",
               "change_pct", "volume", "amount", "ts", "suspended")


class DailySeries:
    """一只票的日线序列。字段与数据层 `Repo.series()` 一一对应。"""

    __slots__ = ("code", "dates", "raw_open", "raw_high", "raw_low", "raw_close",
                 "volume", "amount", "turnover", "adj", "main_net")

    def __init__(self, **kw):
        for key in self.__slots__:
            setattr(self, key, kw[key])

    def __len__(self) -> int:
        return len(self.dates)


def _to_series(data: dict) -> DailySeries:
    return DailySeries(
        code=data["code"], dates=data["dates"], raw_open=data["raw_open"],
        raw_high=data["raw_high"], raw_low=data["raw_low"], raw_close=data["raw_close"],
        volume=data["volume"], amount=data["amount"], turnover=data["turnover"],
        adj=data["adj"], main_net=data["main_net"],
    )


class MarketEngine:
    def __init__(self, repo, history_days: int = HISTORY_DAYS):
        self.repo = repo
        self.history_days = history_days
        self._cache: dict[str, DailySeries | None] = {}
        self._index_cache: dict[str, DailySeries | None] = {}
        self._dates: list[dt.date] | None = None

    # ------------------------------------------------------------------ 基础
    @property
    def dates(self) -> list[dt.date]:
        # 交易日列表在进程生命周期内基本不变，但 portfolio / panorama 会在循环里
        # 反复读它，不缓存就是几千次全表 DISTINCT
        if self._dates is None:
            raw = self.repo.trading_dates(self.history_days)
            self._dates = [dt.date.fromisoformat(d) for d in raw]
        return self._dates

    @property
    def trade_date(self) -> dt.date | None:
        dates = self.dates
        return dates[-1] if dates else None

    def universe(self) -> list[dict]:
        return self.repo.universe()

    def symbol(self, code: str) -> dict:
        return self.repo.symbol(code) or {
            "code": code, "name": code, "exchange": "", "board": "", "industry": "",
            "is_st": False, "float_mv": 0.0, "pe": 0.0, "roe": 0.0, "dividend": 0.0,
        }

    def symbol_valid(self, code: str) -> bool:
        """标的是否真实存在。写自选、下单前都要过这一关。"""
        return self.repo.symbol(code) is not None

    def series(self, code: str) -> DailySeries | None:
        if code not in self._cache:
            data = self.repo.series(code, self.history_days)
            self._cache[code] = _to_series(data) if data else None
        return self._cache[code]

    def index_series(self, code: str = "000300.SH") -> DailySeries | None:
        """指数日线。真实指数只有收盘价，开高低用同值填充（画面积图足够）。"""
        if code in self._index_cache:
            return self._index_cache[code]
        bars = self.repo.index_daily(code, self.history_days)
        if not bars:
            self._index_cache[code] = None
            return None
        closes = [b["close"] for b in bars]
        volume = [b["volume"] for b in bars]
        amount = [b["amount"] for b in bars]
        series = DailySeries(
            code=code,
            dates=[dt.date.fromisoformat(b["date"]) for b in bars],
            raw_open=[b["open"] for b in bars],
            raw_high=[b["high"] for b in bars],
            raw_low=[b["low"] for b in bars],
            raw_close=closes,
            volume=volume,
            amount=amount,
            turnover=[0.0] * len(bars),
            adj=[1.0] * len(bars),
            main_net=[0.0] * len(bars),
        )
        self._index_cache[code] = series
        return series

    # ------------------------------------------------------------------ K 线
    def bars(self, code: str, freq: str = "1d", adjust: str = "qfq",
             limit: int | None = None, *, live: bool = False) -> list[dict]:
        s = self.series(code)
        if not s:
            return []
        if adjust == "qfq":
            factor = s.adj
        elif adjust == "hfq":
            last = s.adj[-1] if s.adj and s.adj[-1] else 1.0
            factor = [a / last for a in s.adj]
        else:
            factor = [1.0] * len(s.dates)

        rows = [
            {
                "date": d.isoformat(),
                "open": round(s.raw_open[i] * factor[i], 2),
                "high": round(s.raw_high[i] * factor[i], 2),
                "low": round(s.raw_low[i] * factor[i], 2),
                "close": round(s.raw_close[i] * factor[i], 2),
                "volume": s.volume[i],
                "amount": s.amount[i],
                "turnover": s.turnover[i],
                "main_net": s.main_net[i],
            }
            for i, d in enumerate(s.dates)
        ]
        # 当日那根还没入库（日线源要等收盘后才发），用实时报价补一根临时的。
        # 只在接口层显式要求时做：选股 / 因子这类批量路径一秒钟调几百次 bars()，
        # 每次再去打一次实时接口会把限速打满。
        if live and freq in ("1d", "1w", "1M"):
            rows = self._append_today(code, rows, s)
        # 当日的临时标记要跟着重采样传下去，否则周 K / 月 K 的最后一根
        # 就不知道自己「还在长」，前端也就不会去追
        provisional = bool(rows and rows[-1].get("provisional"))
        if freq == "60m":
            rows = self._intraday_bars(rows, days=60)
        elif freq != "1d":
            rows = self._resample(rows, freq)
            if provisional and rows:
                rows[-1]["provisional"] = True
        if limit:
            rows = rows[-limit:]
        return rows

    def _append_today(self, code: str, rows: list[dict], series) -> list[dict]:
        """用实时报价给日线补上「今天」这根，收盘前是进行中的，收盘后是终值。

        三个前提，缺一个都不补：
        1. 实时源给得出**交易日期**（`ts` 的日期部分）——不能拿本机日期顶替你，
           周末或数据源停更时会把昨天的收盘价复制成一根新 K 线；
        2. 该日期必须晚于库里最后一根，避免和已入库的当日 K 线重复；
        3. 开盘价与现价都大于 0（停牌 / 集合竞价未成交时不画实体）。
        """
        if not rows:
            return rows
        try:
            from alphaforge_data.live import feed

            hit = (feed().quotes([code]) or {}).get(code)
        except Exception:                                  # noqa: BLE001 - 实时链路不许炸
            return rows
        if not hit:
            return rows

        session = str(hit.get("ts") or "")[:10]
        if not session or session <= rows[-1]["date"]:
            return rows

        price = float(hit.get("price") or 0)
        open_ = float(hit.get("open") or 0)
        if price <= 0 or open_ <= 0:
            return rows

        # 实时价是不复权的，要和已复权的历史价格同尺度：用最后一根的
        # 「复权价 / 原始价」比例换算，等于把今天当作没有除权事件
        raw_last = series.raw_close[-1] if series.raw_close else 0.0
        k = rows[-1]["close"] / raw_last if raw_last else 1.0
        high = max(float(hit.get("high") or 0), price)
        low = min(float(hit.get("low") or price), price)

        rows.append({
            "date": session,
            "open": round(open_ * k, 2),
            "high": round(high * k, 2),
            "low": round(low * k, 2),
            "close": round(price * k, 2),
            "volume": float(hit.get("volume") or 0),
            "amount": float(hit.get("amount") or 0),
            "turnover": 0.0,
            "main_net": 0.0,
            # 前端据此标注「盘中/实时」，也便于排查「这根是不是落库了」
            "provisional": True,
        })
        return rows

    @staticmethod
    def _intraday_bars(rows: list[dict], days: int = 60) -> list[dict]:
        """由日线拆 4 根 60 分钟线。

        真实分钟级历史不在免费源的覆盖范围内，这里按当日振幅**等分**还原出
        4 根 K 线的形状。它只用于看「日内节奏」，不参与任何因子计算——
        因子全部基于日线，所以这条降级不会污染选股结果。
        """
        out: list[dict] = []
        for r in rows[-days:]:
            o, c, h, l = r["open"], r["close"], r["high"], r["low"]
            up = h - max(o, c)
            dn = min(o, c) - l
            steps = [o, o + (c - o) * 0.45, o + (c - o) * 0.8, c]
            vols = [0.32, 0.24, 0.22, 0.22]
            for i in range(4):
                so, sc = steps[i], steps[i + 1]
                sh = min(max(so, sc) + up * (0.55 if i in (0, 2) else 0.25), h)
                sl = max(min(so, sc) - dn * (0.55 if i in (1, 3) else 0.25), l)
                out.append({
                    "date": f"{r['date']} {['10:30', '11:30', '14:00', '15:00'][i]}",
                    "open": round(so, 2),
                    "high": round(sh, 2),
                    "low": round(sl, 2),
                    "close": round(sc, 2),
                    "volume": round(r["volume"] * vols[i], 0),
                    "amount": round(r["amount"] * vols[i], 0),
                    "turnover": round(r["turnover"] * vols[i], 3),
                    "main_net": round(r["main_net"] * vols[i], 0),
                })
        return out

    @staticmethod
    def _resample(rows: list[dict], freq: str) -> list[dict]:
        if freq not in ("1w", "1M"):
            return rows
        out: list[dict] = []
        cur_key = None
        bucket: dict | None = None
        for r in rows:
            d = dt.date.fromisoformat(r["date"])
            key = f"{d.isocalendar()[0]}-{d.isocalendar()[1]}" if freq == "1w" else r["date"][:7]
            if key != cur_key:
                if bucket:
                    out.append(bucket)
                bucket = {**r, "date": r["date"]}
                cur_key = key
            else:
                assert bucket is not None
                bucket["high"] = max(bucket["high"], r["high"])
                bucket["low"] = min(bucket["low"], r["low"])
                bucket["close"] = r["close"]
                bucket["volume"] += r["volume"]
                bucket["amount"] += r["amount"]
                bucket["turnover"] = round(bucket["turnover"] + r["turnover"], 3)
                bucket["main_net"] += r["main_net"]
        if bucket:
            out.append(bucket)
        return out

    # ------------------------------------------------------------------ 分时
    def minute(self, code: str, points: int = 240) -> dict:
        """当日分时。

        数据源顺序：**新浪逐分钟 → 腾讯（已不稳）→ 库里缓存**。

        第一版走的是腾讯，但那个接口现在整片返回空，分时图于是永远停在
        上一次成功抓到的曲线上——这正是「不是实时」的观感来源之一。
        新浪 `CN_MinlineService` 直接给到当前分钟，还附带 `avg_p`
        （真实均价线），比自己拿成交量加权算的近似值更准。

        抓不到时退回 `minute_cache` 并标 `stale`，不伪造数据。
        """
        from alphaforge_data.sources import sina
        from alphaforge_data.sources import tencent as tx

        today = (self.trade_date or dt.date.today()).isoformat()
        live = None
        source = ""
        try:
            line = sina.minline(code)
            if line and line.get("times"):
                live, source = line, "sina"
        except Exception:                                  # noqa: BLE001
            live = None
        if not live:
            try:
                fallback = tx.minute(code)
                if fallback and fallback.get("times"):
                    live, source = fallback, "tencent"
            except Exception:                              # noqa: BLE001
                live = None

        if live and live.get("times"):
            # 新浪分时不给昨收，用实时报价补；补不到就留 0，前端不画基准线
            prev = live.get("prev_close") or 0.0
            if not prev:
                try:
                    rows = self.live_quotes([code])["items"]
                    if rows:
                        prev = rows[0].get("prev_close") or 0.0
                except Exception:                          # noqa: BLE001
                    prev = 0.0
            payload = {
                "code": code,
                "prev_close": prev,
                "times": live["times"],
                "prices": live["prices"],
                "avg": live.get("avg") or self._avg_line(live["prices"], live["volumes"]),
                "volumes": live["volumes"],
                "source": source,
                "stale": False,
            }
            try:
                self.repo.save_minute(code, today, payload)
            except Exception:                              # noqa: BLE001
                pass
            return payload

        cached = self.repo.minute_cache(code)
        if cached:
            return {**cached, "stale": True}
        return {"code": code, "prev_close": 0.0, "times": [], "prices": [],
                "avg": [], "volumes": [], "available": False, "stale": True}

    @staticmethod
    def _avg_line(prices: list[float], volumes: list[float]) -> list[float]:
        cum_amt = cum_vol = 0.0
        out: list[float] = []
        for p, v in zip(prices, volumes):
            cum_amt += p * v
            cum_vol += v
            out.append(round(cum_amt / cum_vol, 3) if cum_vol else p)
        return out

    # ---------------------------------------------------------------- 筹码峰
    def chips(self, code: str, *, days: int = 250, bins: int = 120) -> dict:
        """筹码分布（成本分布）。

        口径说明写在 `alphaforge_core.chips` 里：成交量按当日价格区间摊到
        价格轴上、再按换手率逐日衰减。这里只负责凑料：

        - 价格用**前复权**（`adjust="qfq"`）。用不复权价算，除权那天筹码会
          整体凭空跳一档，峰直接裂成两座；
        - 换手率用 `volume(手) × 100 / float_shares`，流通股本取 `symbol`
          表（东财给的是最新值，历史股本变动会被抹平，这是近似模型的固有误差）；
        - 当日那根用实时价补（`live=True`），不吃实时的话盘中筹码峰永远停在
          昨收，看起来像坏了。
        """
        sym = self.symbol(code) or {}
        float_shares = float(sym.get("float_shares") or 0)
        if float_shares <= 0:
            return {"code": code, "available": False,
                    "reason": "缺少流通股本，无法换算换手率",
                    "levels": [], "peak": 0.0, "avg_cost": 0.0,
                    "profit_ratio": 0.0, "cost90": [0.0, 0.0], "concentration": 0.0}

        rows = self.bars(code, "1d", "qfq", limit=days, live=True)
        if not rows:
            return {"code": code, "available": False, "reason": "无日线数据",
                    "levels": [], "peak": 0.0, "avg_cost": 0.0,
                    "profit_ratio": 0.0, "cost90": [0.0, 0.0], "concentration": 0.0}

        from alphaforge_core.chips import cost_distribution

        current = float(rows[-1]["close"] or 0)
        # 盘中用实时价当现价，否则获利比例会拿「上一分钟那根」的收盘算
        try:
            hit = (self.live_quotes([code]).get("items") or [])[0]
            if float(hit.get("price") or 0) > 0 and rows[-1].get("provisional"):
                current = float(hit["price"])
        except Exception:                                  # noqa: BLE001
            pass

        out = cost_distribution(rows, float_shares, bins=bins, current=current)
        return {
            "code": code,
            "price": round(current, 2),
            "float_shares": round(float_shares / 1e8, 2),   # 亿股，前端标注用
            "days": len(rows),
            "range": [rows[0]["date"], rows[-1]["date"]],
            "source": "local:volume-decay",
            **out,
        }

    # -------------------------------------------------------------- 分时成交
    def ticks(self, code: str, limit: int = 30) -> dict:
        """最近逐笔成交。东财失败时退回「分钟聚合」，并标记 `approx`。

        退回不是造假：分时接口本来就有每分钟的成交量与成交价，聚合成一行
        「14:56　1447.72　4 手」在语义上是成立的，只是笔数变粗、买卖方向
        不可知。前端据此把方向列显示为「—」。
        """
        from alphaforge_data.sources import eastmoney as em

        now = time.time()
        cached = _TICK_CACHE.get(code)
        if cached and now - cached[0] < _TICK_TTL:
            return cached[1]

        payload: dict
        try:
            payload = em.ticks(code, limit)
        except Exception:                                  # noqa: BLE001
            payload = {"items": [], "available": False}

        if payload.get("available"):
            payload["approx"] = False
            payload["source"] = "eastmoney"
        else:
            payload = self._ticks_from_minute(code, limit)

        _TICK_CACHE[code] = (now, payload)
        return payload

    def _ticks_from_minute(self, code: str, limit: int) -> dict:
        try:
            m = self.minute(code)
        except Exception:                                  # noqa: BLE001
            m = {}
        times = m.get("times") or []
        prices = m.get("prices") or []
        vols = m.get("volumes") or []
        items = [
            {
                "time": str(t),
                "price": round(float(p or 0), 2),
                "volume": float(v or 0),
                "amount": round(float(p or 0) * float(v or 0) * 100, 2),
                "side": "mid",
            }
            for t, p, v in list(zip(times, prices, vols))[-limit:]
        ]
        return {
            "items": items,
            "prev_close": float(m.get("prev_close") or 0),
            "available": bool(items),
            "approx": True,
            "source": "minute-aggregate",
        }

    # ------------------------------------------------------------------ 快照
    def quote(self, code: str) -> dict:
        row = self.repo.quote(code)
        if row:
            return row
        sym = self.symbol(code)
        s = self.series(code)
        price = s.raw_close[-1] if s else 0.0
        prev = s.raw_close[-2] if s and len(s.raw_close) > 1 else price
        return {
            "code": code, "name": sym.get("name", ""), "price": price,
            "prev_close": prev, "change": round(price - prev, 2),
            "change_pct": round((price / prev - 1) * 100, 2) if prev else 0.0,
            "open": s.raw_open[-1] if s else 0.0,
            "high": s.raw_high[-1] if s else 0.0,
            "low": s.raw_low[-1] if s else 0.0,
            "volume": s.volume[-1] if s else 0.0,
            "amount": s.amount[-1] if s else 0.0,
            "turnover_rate": s.turnover[-1] if s else 0.0,
            "volratio": 0.0,
            "pe": sym.get("pe") or 0.0,
            "roe": sym.get("roe") or 0.0,
            "industry": sym.get("industry", ""),
            "board": sym.get("board", ""),
            "board_key": board_of(code),
            "is_st": bool(sym.get("is_st")),
            "float_mv": sym.get("float_mv") or 0.0,
            "ts": None,
        }

    def quotes(self, codes: list[str] | None = None) -> list[dict]:
        return self.repo.quotes(codes)

    # ------------------------------------------------------------------ 实时
    def live_quotes(self, codes: list[str]) -> dict:
        """盘中报价：实时价 + 库里的静态字段。

        合并口径很关键——`price/change/high/low/volume/amount` 用行情源的
        实时值，`pe/roe/industry/board/float_mv` 用库里同步时的值。理由是
        后者一天只变一次，实时接口也不提供；混着用不会出现两套口径打架。

        取不到实时值时**不编造**：`live=False`，价格退回库里的收盘价，
        `as_of` 也还是库里的日期。前端据此显示「已收盘」而不是「交易中」。
        """
        from alphaforge_data.live import feed

        want = [c for c in dict.fromkeys(codes) if c]
        if not want:
            return {"items": [], "as_of": "", "session": session_state(dt.datetime.now()),
                    "is_live": False, "session_label": session_label(session_state(dt.datetime.now()))}

        static = {r["code"]: r for r in self.repo.quotes(want)}
        live = feed().quotes(want)

        items: list[dict] = []
        for code in want:
            base = dict(static.get(code) or {})
            base.setdefault("code", code)
            hit = live.get(code)
            if hit:
                for key in LIVE_FIELDS:
                    if key in hit:
                        base[key] = hit[key]
                base["name"] = hit.get("name") or base.get("name") or ""
                base["live"] = not hit.get("stale")
                if hit.get("stale"):
                    base["age"] = hit.get("age")
            else:
                base["live"] = False
            items.append(base)

        as_of = max((i.get("ts") or "" for i in items), default="")
        state = session_state(dt.datetime.now(), as_of[:10] or None)
        return {
            "items": items,
            "as_of": as_of,
            "session": state,
            "session_label": session_label(state),
            "is_live": is_live(state),
            "streaming": any(i.get("live") for i in items),
        }

    def live_board(self, codes: list[str]) -> dict:
        """个股 + 7 大指数一次取完，让前端一轮轮询只打一个请求。"""
        from .panorama import index_book

        stock = self.live_quotes(codes)
        book = index_book(self)
        index_codes = [i["code"] for i in book.list(spark=1)]
        idx = self.live_quotes(index_codes)
        # 指数没有 PE/ROE，合并库里已有的点位信息（成交额、成分涨跌家数）
        merged = {i["code"]: i for i in idx["items"]}
        indices = []
        for item in book.list(spark=20):
            live_item = merged.get(item["code"])
            if live_item and live_item.get("live"):
                item = {**item, "value": live_item["price"],
                        "change": live_item["change"],
                        "change_pct": live_item["change_pct"],
                        "ts": live_item.get("ts")}
            indices.append(item)

        stock["indices"] = indices
        stock["as_of"] = max(stock["as_of"] or "", idx["as_of"] or "")
        return stock

    # ------------------------------------------------------------------ 榜单
    def ranking(self, kind: str = "gainers", limit: int = 10) -> list[dict]:
        rows = self.quotes()
        if kind == "losers":
            rows.sort(key=lambda r: r["change_pct"])
        elif kind == "amount":
            rows.sort(key=lambda r: r["amount"], reverse=True)
        elif kind == "turnover":
            rows.sort(key=lambda r: r["turnover_rate"], reverse=True)
        else:
            rows.sort(key=lambda r: r["change_pct"], reverse=True)
        return rows[:limit]

    # ------------------------------------------------------------------ 市场统计
    def market_overview(self) -> dict:
        from .panorama import index_book, market_temperature

        breadth = self.repo.breadth()
        book = index_book(self)
        hs300 = book.quote("000300.SH")
        temp = market_temperature(self)
        return {
            "trade_date": breadth["trade_date"],
            "up": breadth["up"],
            "down": breadth["down"],
            "flat": breadth["flat"],
            "limit_up": breadth["limit_up"],
            "limit_down": breadth["limit_down"],
            "amount": breadth["amount"],
            "total": breadth["total"],
            "index": {
                "code": "000300.SH",
                "name": "沪深300",
                "value": hs300["value"],
                "change_pct": hs300["change_pct"],
                "change": hs300["change"],
            },
            # 全景条要画迷你走势，spark 必须留着；20 点足够
            "indices": book.list(spark=20),
            "temperature": {
                "score": temp["score"],
                "label": temp["label"],
                "advice": temp["advice"],
                "parts": temp["parts"],
            },
        }

    def sector_flow(self) -> list[dict]:
        return self.repo.industry_flow()

    def stock_flow(self, code: str) -> dict:
        s = self.series(code)
        rows = self.repo.stock_flow(code, 20)
        if not s and not rows:
            return {"code": code, "net_today": 0.0, "net_5d": 0.0, "net_10d": 0.0,
                    "net_20d": 0.0, "ratio": 0.0, "streak": 0, "series": []}
        nets = [r["main_net"] for r in rows] or (s.main_net[-20:] if s else [])
        amount_today = s.amount[-1] if s else 0.0
        return {
            "code": code,
            "net_today": round((nets[-1] if nets else 0.0) / 1e8, 2),
            "net_5d": round(sum(nets[-5:]) / 1e8, 2),
            "net_10d": round(sum(nets[-10:]) / 1e8, 2),
            "net_20d": round(sum(nets[-20:]) / 1e8, 2),
            "ratio": round(nets[-1] / amount_today * 100, 2) if nets and amount_today else 0.0,
            "streak": _streak(nets[-20:]),
            "series": [
                {"date": r["date"], "net": round(r["main_net"] / 1e8, 3)} for r in rows
            ],
        }

    # ------------------------------------------------------------------ 检索
    def search(self, q: str, limit: int = 10) -> list[dict]:
        """代码 / 名称 / 行业 / 拼音首字母。多关键词用空格分隔，需同时命中。"""
        keywords = [k for k in _normalize_query(q).split() if k]
        if not keywords:
            return []
        pool: dict[str, dict] = {}
        for kw in keywords:
            hits = self.repo.search(kw, limit=200)
            if not hits:
                return []
            if not pool:
                pool = {h["code"]: h for h in hits}
            else:
                pool = {c: pool[c] | h for c, h in ((x["code"], x) for x in hits) if c in pool}
        rows = list(pool.values())
        rows.sort(key=lambda r: (len(r["name"]), r["code"]))
        return rows[:limit]

    # ------------------------------------------------------------------ 数据状态
    def status(self) -> dict:
        return self.repo.status()


def _normalize_query(q: str) -> str:
    """全角转半角、去标点、代码补位。"""
    table = {ord("　"): " ", ord("．"): ".", ord("，"): " ", ord("、"): " "}
    text = (q or "").translate(table).strip().lower()
    for ch in "?？!！,，。.、；;：:'\"()（）[]【】":
        text = text.replace(ch, " ")
    return " ".join(text.split())


def _streak(values: list[float]) -> int:
    """连续净流入（正）或净流出（负）的天数。"""
    if not values:
        return 0
    sign = 1 if values[-1] > 0 else -1
    n = 0
    for v in reversed(values):
        if (v > 0) == (sign > 0) and v != 0:
            n += 1
        else:
            break
    return n * sign


def last_trade_date() -> dt.date:
    from .market import get_market

    return get_market().trade_date or dt.date.today()


def get_market() -> MarketEngine:
    """进程级单例。API 层通过 `deps.market()` 拿它。"""
    global _ENGINE
    if _ENGINE is None:
        from alphaforge_data import Repo

        _ENGINE = MarketEngine(Repo())
    return _ENGINE


_ENGINE: MarketEngine | None = None


def reset_market() -> None:
    """数据同步后调用，让缓存与标的表重新加载。"""
    global _ENGINE
    _ENGINE = None
