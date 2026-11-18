"""读路径：engine 只看这一层，不碰 SQL、不知道数据来自腾讯还是东财。

存储形态与业务形状在这里做一次转换，好处是换库、加缓存、换数据源都只改这一层；
代价是每个方法都要写一遍 row → dict，但这比让 engine 里散落 SQL 划算得多。

线程模型：FastAPI 的同步端点跑在线程池里，sqlite3 连接不能跨线程复用，
所以按 thread 持有连接（`threading.local`），每个线程一条。
"""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
import threading
from pathlib import Path

from . import db as dbm


class Repo:
    def __init__(self, path: Path | None = None, *, create: bool = True):
        self.path = path or dbm.db_path()
        self._local = threading.local()
        self._universe: list[dict] = []
        self._umap: dict[str, dict] = {}
        self._industries: list[str] = []
        self._membership: dict[str, set[str]] = {}
        self._stamp: tuple | None = None
        if create:
            conn = self.conn
            dbm.init_db(conn)

    # ------------------------------------------------------------------ 连接
    @property
    def conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = dbm.connect(self.path)
            self._local.conn = conn
        return conn

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None

    # ------------------------------------------------------------------ 元信息
    def meta(self, key: str, default: str | None = None) -> str | None:
        return dbm.get_meta(self.conn, key) or default

    def status(self) -> dict:
        counts = dbm.table_counts(self.conn)
        last = self.conn.execute(
            "SELECT step, status, finished, rows, note FROM sync_run "
            "ORDER BY id DESC LIMIT 12"
        ).fetchall()
        return {
            "db_path": str(self.path),
            "db_size_mb": round(self.path.stat().st_size / 1e6, 1) if self.path.exists() else 0.0,
            "tables": counts,
            "trade_date": self.trade_date(),
            "symbols": counts.get("symbol", 0),
            "last_sync": dbm.get_meta(self.conn, "last_sync"),
            "universe_sync": dbm.get_meta(self.conn, "universe_sync"),
            "daily_sync": dbm.get_meta(self.conn, "daily_sync"),
            "factor_sync": dbm.get_meta(self.conn, "factor_sync"),
            "ready": counts.get("factor", 0) > 0,
            "runs": [
                {"step": r[0], "status": r[1], "finished": r[2], "rows": r[3], "note": r[4]}
                for r in last
            ],
        }

    def trade_date(self) -> str | None:
        row = self.conn.execute("SELECT MAX(trade_date) FROM factor").fetchone()
        if row and row[0]:
            return str(row[0])
        row = self.conn.execute("SELECT MAX(date) FROM daily").fetchone()
        return str(row[0]) if row and row[0] else None

    # ------------------------------------------------------------------ 标的
    def universe(self, *, force: bool = False) -> list[dict]:
        stamp = self.conn.execute("SELECT COUNT(*), MAX(code) FROM symbol").fetchone()
        if force or not self._universe or stamp != self._stamp:
            rows = self.conn.execute(
                "SELECT code, name, exchange, board, industry, is_st, listed, "
                "total_mv, float_mv, pe, pb, roe, roe_date, dividend, float_shares, "
                "pinyin, pinyin_full FROM symbol ORDER BY code"
            ).fetchall()
            self._universe = [dict(r) for r in rows]
            for s in self._universe:
                s["is_st"] = bool(s["is_st"])
            self._umap = {s["code"]: s for s in self._universe}
            self._industries = sorted({s["industry"] for s in self._universe if s["industry"]})
            self._stamp = stamp
        return self._universe

    def symbol(self, code: str) -> dict | None:
        self.universe()
        return self._umap.get(code)

    def symbol_map(self) -> dict[str, dict]:
        self.universe()
        return self._umap

    def industries(self) -> list[str]:
        self.universe()
        return self._industries

    def membership(self) -> dict[str, set[str]]:
        if not self._membership:
            marks: dict[str, set[str]] = {}
            for idx, code in self.conn.execute("SELECT index_code, code FROM index_member"):
                marks.setdefault(code, set()).add(idx)
            self._membership = marks
        return self._membership

    def index_constituents(self, index_code: str) -> list[str]:
        rows = self.conn.execute(
            "SELECT code FROM index_member WHERE index_code=? ORDER BY code", (index_code,)
        ).fetchall()
        return [r[0] for r in rows]

    # ------------------------------------------------------------------ 日线
    def trading_dates(self, days: int = 520) -> list[str]:
        rows = self.conn.execute(
            "SELECT DISTINCT date FROM daily ORDER BY date DESC LIMIT ?", (days,)
        ).fetchall()
        return [r[0] for r in reversed(rows)]

    def series(self, code: str, days: int = 520) -> dict | None:
        """一只票的日线序列，按时间正序。不足 20 根视为无数据。"""
        rows = self.conn.execute(
            "SELECT date, open, high, low, close, volume, amount, adj FROM daily "
            "WHERE code=? ORDER BY date DESC LIMIT ?", (code, days)
        ).fetchall()
        if len(rows) < 2:
            return None
        rows = list(reversed(rows))
        sym = self.symbol(code) or {}
        shares = float(sym.get("float_shares") or 0)
        dates = [r[0] for r in rows]
        flows = self._flow_map(code, dates[0])
        out = {
            "code": code,
            "dates": [dt.date.fromisoformat(d) for d in dates],
            "raw_open": [float(r[1]) for r in rows],
            "raw_high": [float(r[2]) for r in rows],
            "raw_low": [float(r[3]) for r in rows],
            "raw_close": [float(r[4]) for r in rows],
            "volume": [float(r[5]) for r in rows],
            "amount": [float(r[6]) for r in rows],
            "adj": [float(r[7]) or 1.0 for r in rows],
            "turnover": [
                round(float(r[5]) * 100 / shares * 100, 3) if shares else 0.0 for r in rows
            ],
            "main_net": [flows.get(d, 0.0) for d in dates],
        }
        return out

    def _flow_map(self, code: str, since: str) -> dict[str, float]:
        rows = self.conn.execute(
            "SELECT date, main_net FROM flow WHERE code=? AND date>=?", (code, since)
        ).fetchall()
        return {r[0]: float(r[1]) for r in rows}

    def series_bulk(self, codes: list[str], days: int = 520) -> dict[str, dict]:
        """批量取序列：一次 SQL 取回多只票，比逐只往返快一个量级。"""
        out: dict[str, dict] = {}
        if not codes:
            return out
        umap = self.symbol_map()
        for i in range(0, len(codes), 400):
            chunk = codes[i: i + 400]
            marks = ",".join("?" * len(chunk))
            rows = self.conn.execute(
                f"SELECT code, date, open, high, low, close, volume, amount, adj FROM daily "
                f"WHERE code IN ({marks}) ORDER BY code, date", chunk
            ).fetchall()
            bucket: dict[str, list] = {}
            for r in rows:
                bucket.setdefault(r[0], []).append(r)
            if not bucket:
                continue
            # 资金流也一次取回：逐只查会变成 5000+ 次往返
            frows = self.conn.execute(
                f"SELECT code, date, main_net FROM flow WHERE code IN ({marks})", chunk
            ).fetchall()
            flow_map: dict[str, dict[str, float]] = {}
            for fr in frows:
                flow_map.setdefault(fr[0], {})[fr[1]] = float(fr[2])
            for code, rs in bucket.items():
                rs = rs[-days:]
                if len(rs) < 2:
                    continue
                sym = umap.get(code) or {}
                shares = float(sym.get("float_shares") or 0)
                dates = [r[1] for r in rs]
                flows = flow_map.get(code, {})
                out[code] = {
                    "code": code,
                    "dates": [dt.date.fromisoformat(d) for d in dates],
                    "raw_open": [float(r[2]) for r in rs],
                    "raw_high": [float(r[3]) for r in rs],
                    "raw_low": [float(r[4]) for r in rs],
                    "raw_close": [float(r[5]) for r in rs],
                    "volume": [float(r[6]) for r in rs],
                    "amount": [float(r[7]) for r in rs],
                    "adj": [float(r[8]) or 1.0 for r in rs],
                    "turnover": [
                        round(float(r[6]) * 100 / shares * 100, 3) if shares else 0.0 for r in rs
                    ],
                    "main_net": [flows.get(d, 0.0) for d in dates],
                }
        return out

    # ------------------------------------------------------------------ 因子
    def factor_rows(self, trade_date: str | None = None) -> list[dict]:
        date = trade_date or self.trade_date()
        if not date:
            return []
        rows = self.conn.execute(
            "SELECT * FROM factor WHERE trade_date=?", (date,)
        ).fetchall()
        return [dict(r) for r in rows]

    def factor_columns(self, trade_date: str | None = None) -> tuple[str | None, dict[str, list]]:
        rows = self.factor_rows(trade_date)
        if not rows:
            return None, {}
        cols: dict[str, list] = {k: [] for k in rows[0]}
        for r in rows:
            for k, v in r.items():
                cols[k].append(bool(v) if k in _BOOL_COLS else v)
        return rows[0].get("trade_date"), cols

    # ------------------------------------------------------------------ 快照
    def quotes(self, codes: list[str] | None = None) -> list[dict]:
        """当前截面。价格类取因子表（含派生列），盘口细节由快照补。

        量比优先用因子表的：快照源（新浪）不提供量比，那一列恒为 NULL，
        而因子表里的量比是拿「当日量 / 5 日均量」真算出来的。
        """
        date = self.trade_date()
        cols = (
            "SELECT f.code, f.price, f.change_pct, f.float_mv, f.amount, f.pe, f.roe, "
            "f.industry, f.board, f.is_st, s.prev_close, s.open, s.high, s.low, "
            "s.volume, s.turnover, f.volratio, s.ts "
            "FROM factor f LEFT JOIN snapshot s ON s.code = f.code "
        )
        params: list = [date]
        if codes:
            marks = ",".join("?" * len(codes))
            sql = cols + f"WHERE f.trade_date=? AND f.code IN ({marks})"
            params.extend(codes)
        else:
            sql = cols + "WHERE f.trade_date=?"
        rows = self.conn.execute(sql, params).fetchall()
        umap = self.symbol_map()
        out = []
        for r in rows:
            sym = umap.get(r[0]) or {}
            price, prev = float(r[1] or 0), float(r[10] or 0)
            out.append({
                "code": r[0],
                "name": sym.get("name", ""),
                "price": round(price, 2),
                "prev_close": round(prev, 2),
                "change": round(price - prev, 2) if prev else 0.0,
                "change_pct": round(float(r[2] or 0), 2),
                "open": round(float(r[11] or 0), 2),
                "high": round(float(r[12] or 0), 2),
                "low": round(float(r[13] or 0), 2),
                "volume": float(r[14] or 0),
                "amount": round(float(r[4] or 0) * 1e8, 0),
                "turnover_rate": round(float(r[15] or 0), 2),
                "volratio": round(float(r[16] or 0), 2),
                "pe": round(float(r[5] or 0), 2),
                "roe": round(float(r[6] or 0), 2),
                "industry": r[7] or sym.get("industry", ""),
                "board": r[8] or sym.get("board", ""),
                "board_key": _board_key(r[0], r[8] or ""),
                "is_st": bool(r[9]),
                "float_mv": round(float(r[3] or 0), 1),
                "ts": r[17],
            })
        return out

    def quote(self, code: str) -> dict | None:
        rows = self.quotes([code])
        return rows[0] if rows else None

    # ------------------------------------------------------------------ 指数
    def index_quotes(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT code, name, price, prev_close, change, change_pct, open, high, low, "
            "volume, amount, ts FROM index_quote"
        ).fetchall()
        return [
            {
                "code": r[0], "name": r[1], "value": round(float(r[2] or 0), 2),
                "prev_close": round(float(r[3] or 0), 2),
                "change": round(float(r[4] or 0), 2),
                "change_pct": round(float(r[5] or 0), 2),
                "open": round(float(r[6] or 0), 2),
                "high": round(float(r[7] or 0), 2),
                "low": round(float(r[8] or 0), 2),
                "volume": float(r[9] or 0),
                "amount": round(float(r[10] or 0) / 1e8, 1),
                "ts": r[11],
            }
            for r in rows
        ]

    def index_quote(self, code: str) -> dict | None:
        return next((q for q in self.index_quotes() if q["code"] == code), None)

    def index_daily(self, code: str, days: int = 250) -> list[dict]:
        rows = self.conn.execute(
            "SELECT date, open, high, low, close, volume, amount FROM index_daily "
            "WHERE code=? ORDER BY date DESC LIMIT ?", (code, days)
        ).fetchall()
        return [
            {
                "date": r[0], "open": round(float(r[1]), 2), "high": round(float(r[2]), 2),
                "low": round(float(r[3]), 2), "close": round(float(r[4]), 2),
                "volume": float(r[5]), "amount": round(float(r[6]), 0),
            }
            for r in reversed(rows)
        ]

    # ------------------------------------------------------------------ 资金流
    def stock_flow(self, code: str, days: int = 20) -> list[dict]:
        rows = self.conn.execute(
            "SELECT date, main_net, small_net, mid_net, large_net, xlarge_net, main_ratio "
            "FROM flow WHERE code=? ORDER BY date DESC LIMIT ?", (code, days)
        ).fetchall()
        return [
            {
                "date": r[0], "main_net": float(r[1]), "small_net": float(r[2]),
                "mid_net": float(r[3]), "large_net": float(r[4]),
                "xlarge_net": float(r[5]),
                "main_ratio": float(r[6]) if r[6] is not None else None,
            }
            for r in reversed(rows)
        ]

    # ------------------------------------------------------------------ 分时缓存
    def minute_cache(self, code: str) -> dict | None:
        row = self.conn.execute(
            "SELECT payload FROM minute_cache WHERE code=? ORDER BY date DESC LIMIT 1", (code,)
        ).fetchone()
        if not row:
            return None
        try:
            return json.loads(row[0])
        except json.JSONDecodeError:
            return None

    def save_minute(self, code: str, date: str, payload: dict) -> None:
        self.conn.execute(
            "INSERT INTO minute_cache(code, date, payload, ts) VALUES(?,?,?,?) "
            "ON CONFLICT(code, date) DO UPDATE SET payload=excluded.payload, ts=excluded.ts",
            (code, date, json.dumps(payload, ensure_ascii=False),
             dt.datetime.now().isoformat(timespec="seconds")),
        )

    # ------------------------------------------------------------------ 市场统计
    def breadth(self, trade_date: str | None = None) -> dict:
        """涨跌家数 / 涨跌停家数 / 总成交额。一次扫描因子表，不碰日线。"""
        from alphaforge_core.universe import pc

        date = trade_date or self.trade_date()
        if not date:
            return {"trade_date": None, "total": 0, "up": 0, "down": 0, "flat": 0,
                    "limit_up": 0, "limit_down": 0, "amount": 0.0}
        rows = self.conn.execute(
            "SELECT code, change_pct, is_st, amount FROM factor WHERE trade_date=?", (date,)
        ).fetchall()
        up = down = limit_up = limit_down = 0
        amount = 0.0
        for code, chg, is_st, amt in rows:
            chg = float(chg or 0)
            amount += float(amt or 0)
            if chg > 0:
                up += 1
            elif chg < 0:
                down += 1
            limit = pc(code, bool(is_st)) * 100
            if chg >= limit - 0.2:
                limit_up += 1
            elif chg <= -limit + 0.2:
                limit_down += 1
        return {
            "trade_date": date,
            "total": len(rows),
            "up": up,
            "down": down,
            "flat": len(rows) - up - down,
            "limit_up": limit_up,
            "limit_down": limit_down,
            "amount": round(amount * 1e8, 0),      # 亿元 → 元
        }

    def amount_history(self, days: int = 21) -> list[tuple[str, float]]:
        """近 N 个交易日的全市场成交额合计（**元**），按日期正序。

        与 `breadth()["amount"]` 同口径。注意因子表里的 `factor.amount`
        是**亿元**——那是给人看的横截面列；跨 API 的金额一律用元，
        由前端负责折算显示单位。
        """
        rows = self.conn.execute(
            "SELECT date, SUM(amount) FROM daily WHERE date IN "
            "(SELECT DISTINCT date FROM daily ORDER BY date DESC LIMIT ?) "
            "GROUP BY date ORDER BY date", (days,)
        ).fetchall()
        return [(r[0], float(r[1] or 0)) for r in rows]

    def ma20_ratio(self, trade_date: str | None = None) -> tuple[int, int]:
        """站上 20 日线的家数与总数。"""
        date = trade_date or self.trade_date()
        if not date:
            return 0, 0
        row = self.conn.execute(
            "SELECT SUM(above_ma20), COUNT(*) FROM factor WHERE trade_date=?", (date,)
        ).fetchone()
        return int(row[0] or 0), int(row[1] or 0)

    def industry_flow(self, trade_date: str | None = None) -> list[dict]:
        """按行业的资金流与涨跌汇总。"""
        date = trade_date or self.trade_date()
        if not date:
            return []
        rows = self.conn.execute(
            "SELECT industry, SUM(net5d), SUM(amount), SUM(main_net), AVG(change_pct), COUNT(*) "
            "FROM factor WHERE trade_date=? GROUP BY industry", (date,)
        ).fetchall()
        out = []
        for ind, net5d, amt, main_net, chg, cnt in rows:
            amt = float(amt or 0)
            net = float(main_net or 0)
            out.append({
                "name": ind or "其他",
                "net_inflow": round(net, 2),
                "net_5d": round(float(net5d or 0), 2),
                "amount": round(amt, 2),
                "net_ratio": round(net / amt * 100, 2) if amt else 0.0,
                "change_pct": round(float(chg or 0), 2),
                "count": int(cnt or 0),
            })
        out.sort(key=lambda r: r["net_inflow"], reverse=True)
        for i, r in enumerate(out, 1):
            r["rank"] = i
        return out

    def index_breadth(self, index_code: str, trade_date: str | None = None) -> dict:
        """指数成分的涨跌统计 + 领涨股。只对入库了成分的指数有效。"""
        date = trade_date or self.trade_date()
        row = self.conn.execute(
            "SELECT COUNT(*) FROM index_member WHERE index_code=?", (index_code,)
        ).fetchone()
        members = int(row[0] or 0)
        if not members or not date:
            return {"members": 0, "up": 0, "down": 0, "flat": 0, "amount": 0.0, "top": None}
        rows = self.conn.execute(
            "SELECT f.code, f.change_pct, f.amount FROM factor f "
            "JOIN index_member m ON m.code = f.code "
            "WHERE m.index_code=? AND f.trade_date=?", (index_code, date)
        ).fetchall()
        umap = self.symbol_map()
        up = down = 0
        amount = 0.0
        best: tuple[float, str] | None = None
        for code, chg, amt in rows:
            chg = float(chg or 0)
            amount += float(amt or 0)
            if chg > 0:
                up += 1
            elif chg < 0:
                down += 1
            if best is None or chg > best[0]:
                best = (chg, code)
        top = None
        if best:
            sym = umap.get(best[1]) or {}
            top = {"code": best[1], "name": sym.get("name", ""),
                   "change_pct": round(best[0], 2)}
        return {
            "members": members, "up": up, "down": down,
            "flat": len(rows) - up - down,
            "amount": round(amount * 1e8, 0), "top": top,
        }

    def index_constituent_quotes(self, index_code: str, limit: int | None = None,
                                 asc: bool = False) -> list[dict]:
        """成分股当前截面。`asc=True` 取跌幅榜。

        必须有这个开关：成分表按涨幅倒序截断时，截出来的只有涨得多的那些，
        「领跌」若从这份列表尾部取，拿到的是「涨得最少的」而不是「跌得最多的」，
        在界面上就是一条看着合理、实则错误的榜单。
        """
        date = self.trade_date()
        if not date:
            return []
        sql = (
            "SELECT f.code, f.price, f.change_pct, f.float_mv, f.industry "
            "FROM factor f JOIN index_member m ON m.code = f.code "
            f"WHERE m.index_code=? AND f.trade_date=? ORDER BY f.change_pct {'ASC' if asc else 'DESC'}"
        )
        params: list = [index_code, date]
        if limit:
            sql += " LIMIT ?"
            params.append(limit)
        rows = self.conn.execute(sql, params).fetchall()
        umap = self.symbol_map()
        return [
            {
                "code": r[0], "name": (umap.get(r[0]) or {}).get("name", ""),
                "industry": r[4] or (umap.get(r[0]) or {}).get("industry", ""),
                "price": round(float(r[1] or 0), 2),
                "change_pct": round(float(r[2] or 0), 2),
                "float_mv": round(float(r[3] or 0), 1),
            }
            for r in rows
        ]

    def search(self, keyword: str, limit: int = 20) -> list[dict]:
        kw = keyword.strip()
        if not kw:
            return []
        like = f"%{kw}%"
        # 价格与涨跌幅必须一起带出来：搜索弹窗要显示现价，而前端只有一个
        # `fmtNum(price)`，字段缺失会直接抛异常把整个弹窗打掉。
        # 用 LEFT JOIN 而不是 INNER JOIN：有 10 只标的（次新 / 已退市）在
        # factor 表里没有行，它们仍然应该被搜到，只是价格显示为「—」。
        rows = self.conn.execute(
            "SELECT s.code, s.name, s.industry, s.board, f.price, f.change_pct "
            "FROM symbol s LEFT JOIN factor f ON f.code = s.code "
            "WHERE s.code LIKE ? OR s.name LIKE ? OR s.industry LIKE ? "
            "OR s.pinyin LIKE ? OR s.pinyin_full LIKE ? "
            "ORDER BY CASE WHEN s.code = ? THEN 0 "
            "              WHEN s.name = ? THEN 1 "
            "              WHEN s.name LIKE ? THEN 2 "
            "              WHEN s.code LIKE ? THEN 3 ELSE 4 END, s.code LIMIT ?",
            (like, like, like, f"{kw.lower()}%", f"{kw.lower()}%",
             kw.upper(), kw, f"{kw}%", f"{kw}%", limit),
        ).fetchall()
        return [
            {
                "code": r[0],
                "name": r[1],
                "industry": r[2],
                "board": r[3],
                "price": r[4],
                "change_pct": r[5],
            }
            for r in rows
        ]


_BOOL_COLS = frozenset({
    "limit_up", "above_ma20", "ma_bull", "macd_gold", "boll_break",
    "new_high20", "new_high60", "is_st",
    "in_hs300", "in_sz50", "in_zz500", "in_sse", "in_szse",
})


def _board_key(code: str, board: str) -> str:
    if board == "创业板":
        return "gem"
    if board == "科创板":
        return "star"
    if board == "北交所":
        return "bse"
    return "sh_main" if code.upper().endswith(".SH") else "sz_main"
