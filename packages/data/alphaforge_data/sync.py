"""全量 / 增量同步编排。

链路顺序不能乱：股息率要用快照里的现价，因子要用日线 + 资金流 + 成分，
所以是 `universe → fundamentals → dividend → daily → flow → index → factor`。

并发只发生在**取数**阶段：多线程各自发请求，结果回到主线程统一落库。
SQLite 在 WAL 下多写者会互相等锁，把写集中到单线程反而更快。

两个必须说清楚的口径：

1. **复权因子由分红送配推算**。新浪日线只给不复权价，而腾讯的前复权接口
   在几百次请求后就整片 501，靠它扛一万次请求不可行。所以前复权因子
   `adj` 由 `RPT_SHAREBONUS_DET` 的除权事件精确复算：
   `除权参考价 = (除权前收盘 - 每股派息) / (1 + 送转比例)`，
   该日之前的所有价格乘以 `参考价 / 前收盘`。这是教科书口径，不是近似。
2. **历史成交额是估算值**。免费源里只有新浪快照给当日真实成交额，没有
   历史。日线 `amount` 按 `量 × 均价`（均价取开高低收四价均值）估算，
   与真实值通常差 1%–3%，只影响「成交额」字段与资金流占比，不影响
   价格、涨跌幅、动量、波动等价格类因子。最新交易日用快照真实值覆盖。
"""

from __future__ import annotations

import bisect
import datetime as dt
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, Iterable

from . import db as dbm
from . import factors as factor_calc
from .repo import Repo
from .schema import FACTOR_COLUMNS
from .sources import eastmoney as em
from .sources import sina
from .sources import tencent as tx

INDEX_DEFS: list[dict] = [
    {"code": "000001.SH", "name": "上证指数", "kind": "market"},
    {"code": "399001.SZ", "name": "深证成指", "kind": "market"},
    {"code": "399006.SZ", "name": "创业板指", "kind": "market"},
    {"code": "000688.SH", "name": "科创50", "kind": "market"},
    {"code": "000300.SH", "name": "沪深300", "kind": "broad"},
    {"code": "000016.SH", "name": "上证50", "kind": "broad"},
    {"code": "000905.SH", "name": "中证500", "kind": "broad"},
]

# 成分来源。拿不到官方成分表的指数留空，前端据此只展示指数自身走势，
# 不用「全板块」冒充「成分股」。
INDEX_MEMBERS_FROM: dict[str, str] = {
    "000300.SH": "eastmoney",
    "000016.SH": "eastmoney",
    "000905.SH": "eastmoney",
    "000688.SH": "eastmoney",
    "000001.SH": "exchange:SH",   # 上证指数按定义就是全部沪市
}

Log = Callable[[str], None]
Progress = Callable[[dict], None]

# 各步骤的相对耗时权重（实测口径：日线/资金流各抓 5000+ 只，其余是一次或几次请求）。
# 只用于折算总进度与 ETA，不影响执行顺序。
STEP_WEIGHT: dict[str, float] = {
    "universe": 1,
    "fundamentals": 1,
    "bonus": 1,
    "daily": 8,
    "flow": 5,
    "index": 1,
    "factor": 3,
    "sector": 1,
}

_SUFFIX = {"6": "SH", "0": "SZ", "3": "SZ", "4": "BJ", "8": "BJ", "9": "BJ"}


def _suffix_of(code6: str) -> str:
    if code6.startswith(("688", "689", "60")):
        return "SH"
    if code6.startswith(("4", "8", "920")):
        return "BJ"
    return _SUFFIX.get(code6[:1], "SZ")


def _est_amount(bar: dict) -> float:
    """成交额估算：量（手）× 100 × 均价。见模块头部的口径说明。"""
    avg = (bar["open"] + bar["high"] + bar["low"] + bar["close"]) / 4
    return round(bar["volume"] * 100 * avg, 2)


class Syncer:
    def __init__(
        self,
        repo: Repo | None = None,
        *,
        workers: int = 8,
        bars: int = 520,
        flow_days: int = 30,
        log: Log | None = None,
        progress: Progress | None = None,
    ):
        self.repo = repo or Repo()
        self.workers = max(1, workers)
        self.bars = bars
        self.flow_days = flow_days
        self._log = log or (lambda msg: None)
        self._progress = progress or (lambda p: None)
        # 除权事件：日线复权与股息率共用这一份，`step_bonus` 填
        self._bonus: dict[str, list[dict]] = {}
        # 当前进度上下文：本步之前累计权重 / 本步权重 / 总权重，用于折算总进度
        self._ctx: tuple[float, float, float] = (0.0, 1.0, 1.0)
        # 步骤位置（第几步 / 共几步），只用于显示「第 4/7 步」
        self._pos: tuple[int, int] = (0, 1)

    def log(self, msg: str) -> None:
        self._log(msg)

    def emit(self, step: str, done: int, total: int, **extra) -> None:
        """上报进度：细分进度（done/total）+ 折算后的总进度（overall）。

        总进度按**权重**折算而不是按步骤数平均：日线要抓 5580 只、因子要扫
        全表，universe 只有一次请求，七步等权会让进度条在第一步就跳 14%，
        而真正的长任务刚开始。权重写在 `STEP_WEIGHT`。
        """
        base, weight, total_w = self._ctx
        idx, steps = self._pos
        total = max(int(total), 1)
        frac = min(max(done / total, 0.0), 1.0)
        overall = round(min(((base + frac * weight) / max(total_w, 0.001)) * 100, 100), 1)
        self._progress({
            "step": step,
            "done": int(done),
            "total": int(total),
            "percent": round(frac * 100, 1),
            "overall": overall,
            "steps_done": idx,
            "steps_total": steps,
            **extra,
        })

    # ------------------------------------------------------------------ 日志
    def _run_row(self, step: str) -> int:
        cur = self.repo.conn.execute(
            "INSERT INTO sync_run(step, started, status) VALUES(?,?, 'running')",
            (step, dt.datetime.now().isoformat(timespec="seconds")),
        )
        return int(cur.lastrowid or 0)

    def _finish(self, run_id: int, rows: int, status: str = "ok", note: str = "") -> None:
        self.repo.conn.execute(
            "UPDATE sync_run SET finished=?, status=?, rows=?, note=? WHERE id=?",
            (dt.datetime.now().isoformat(timespec="seconds"), status, rows, note, run_id),
        )

    # ------------------------------------------------------------------ 入口
    def run(self, steps: Iterable[str] | str | None = None, *, full: bool = False) -> dict:
        """按步骤同步。`full=True` 时 daily 走全量重拉（补历史用）。

        增量模式只补最近 12 根，对「把历史往前延到 5 年」这件事完全无效——
        库里已有的票永远不会被要求更早的数据。所以拉长窗口必须显式全量。
        """
        order = ["universe", "fundamentals", "bonus", "daily", "flow", "index",
                 "factor", "sector"]
        wanted = order if not steps or steps == "all" else (
            [steps] if isinstance(steps, str) else [s for s in order if s in set(steps)]
        )
        # 上一次被强杀（Ctrl-C / taskkill）会留下 status='running' 的僵尸行，
        # 不清掉的话「最近同步」里永远挂着一个跑不完的任务。
        self.repo.conn.execute(
            "UPDATE sync_run SET status='aborted', note='进程中断' "
            "WHERE status='running' AND finished IS NULL"
        )
        done: dict[str, dict] = {}
        weights = [STEP_WEIGHT.get(s, 2) for s in wanted]
        total_w = float(sum(weights) or 1)
        for idx, step in enumerate(wanted):
            self._ctx = (float(sum(weights[:idx])), float(weights[idx]), total_w)
            self._pos = (idx, len(wanted))
            t0 = time.time()
            self.emit(step, 0, 1, message=f"开始 {step}")
            run_id = self._run_row(step)
            try:
                kwargs = {"run_id": run_id}
                if step == "daily" and full:
                    kwargs["full"] = True
                rows = getattr(self, f"step_{step}")(**kwargs)
                self.emit(step, 1, 1, message=f"{step} 完成")
                secs = round(time.time() - t0, 1)
                done[step] = {"rows": rows, "seconds": secs}
                self._finish(run_id, rows)
                self.log(f"[{step}] {rows} 行 / {secs}s")
            except Exception as exc:                       # noqa: BLE001
                self._finish(run_id, 0, "fail", f"{type(exc).__name__}: {exc}")
                self.log(f"[{step}] 失败：{type(exc).__name__}: {exc}")
                done[step] = {"rows": 0, "error": str(exc)}
                break
        dbm.set_meta(self.repo.conn, "last_sync", dt.datetime.now().isoformat(timespec="seconds"))
        self.repo.universe(force=True)
        self.repo._membership = {}
        return done

    # ------------------------------------------------------------------ 标的
    def step_universe(self, run_id: int = 0) -> int:
        rows = sina.snapshot()
        if not rows:
            raise RuntimeError("新浪全 A 快照返回空，检查网络或接口变更")
        conn = self.repo.conn
        now = dt.datetime.now().isoformat(timespec="seconds")
        conn.execute("BEGIN")
        for r in rows:
            conn.execute(
                "INSERT INTO symbol(code, name, exchange, board, is_st, total_mv, float_mv, "
                "float_shares, pe, pb) VALUES(?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(code) DO UPDATE SET name=excluded.name, exchange=excluded.exchange, "
                "board=excluded.board, is_st=excluded.is_st, total_mv=excluded.total_mv, "
                "float_mv=excluded.float_mv, float_shares=excluded.float_shares, "
                "pe=excluded.pe, pb=excluded.pb",
                (r["code"], r["name"], r["exchange"], r["board"], r["is_st"],
                 r["total_mv"], r["float_mv"], r["float_shares"], r["pe"], r["pb"]),
            )
            conn.execute(
                "INSERT INTO snapshot(code, price, prev_close, open, high, low, change, "
                "change_pct, volume, amount, turnover, total_mv, float_mv, ts) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(code) DO UPDATE SET price=excluded.price, "
                "prev_close=excluded.prev_close, open=excluded.open, high=excluded.high, "
                "low=excluded.low, change=excluded.change, change_pct=excluded.change_pct, "
                "volume=excluded.volume, amount=excluded.amount, turnover=excluded.turnover, "
                "total_mv=excluded.total_mv, float_mv=excluded.float_mv, ts=excluded.ts",
                (r["code"], r["price"], r["prev_close"], r["open"], r["high"], r["low"],
                 r["change"], r["change_pct"], r["volume"], r["amount"], r["turnover"],
                 r["total_mv"], r["float_mv"], now),
            )
        conn.execute("COMMIT")
        self._fill_pinyin(rows)
        dbm.set_meta(conn, "universe_sync", now)
        return len(rows)

    def refresh_snapshot(self) -> int:
        """只刷实时快照，不动 `symbol` 与拼音。

        盘中每 5 分钟轮询用这个：`step_universe` 会连带重算 5000+ 只票的拼音
        （5000 次 UPDATE + 事务），跑一遍好几秒，而盘中只想要「此刻的价格」。
        返回写入行数；上游返回空时返回 0 而不是抛错——盘中一次失败不该
        让调度器整轮挂掉。
        """
        rows = sina.snapshot()
        if not rows:
            return 0
        conn = self.repo.conn
        now = dt.datetime.now().isoformat(timespec="seconds")
        conn.execute("BEGIN")
        for r in rows:
            conn.execute(
                "INSERT INTO snapshot(code, price, prev_close, open, high, low, change, "
                "change_pct, volume, amount, turnover, total_mv, float_mv, ts) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(code) DO UPDATE SET price=excluded.price, "
                "prev_close=excluded.prev_close, open=excluded.open, high=excluded.high, "
                "low=excluded.low, change=excluded.change, change_pct=excluded.change_pct, "
                "volume=excluded.volume, amount=excluded.amount, turnover=excluded.turnover, "
                "total_mv=excluded.total_mv, float_mv=excluded.float_mv, ts=excluded.ts",
                (r["code"], r["price"], r["prev_close"], r["open"], r["high"], r["low"],
                 r["change"], r["change_pct"], r["volume"], r["amount"], r["turnover"],
                 r["total_mv"], r["float_mv"], now),
            )
        conn.execute("COMMIT")
        return len(rows)

    def _fill_pinyin(self, rows: list[dict]) -> None:
        py = _pinyin_table()
        conn = self.repo.conn
        conn.execute("BEGIN")
        for r in rows:
            initials, full = py(r["name"])
            if initials or full:
                conn.execute(
                    "UPDATE symbol SET pinyin=?, pinyin_full=? WHERE code=?",
                    (initials, full, r["code"]),
                )
        conn.execute("COMMIT")

    # ------------------------------------------------------------------ 财报字段
    def step_fundamentals(self, run_id: int = 0) -> int:
        """ROE（最新**年报**口径）+ 东财二级行业，两次请求覆盖全市场。

        这里不能用 `COALESCE` 保留旧值：口径从「三季报年初至今」切到「年报」，
        缺年报的票必须显式写成 NULL，否则界面上会留着一个口径对不上的旧数字。
        NULL 表示「没有年报 ROE」，比一个错的数更诚实。
        """
        data = em.fundamentals()
        if not data:
            raise RuntimeError("东财业绩报表返回空")
        conn = self.repo.conn
        conn.execute("BEGIN")
        n = 0
        for code6, item in data.items():
            code = f"{code6}.{_suffix_of(code6)}"
            cur = conn.execute(
                "UPDATE symbol SET roe=?, roe_date=?, industry=COALESCE(?, industry) "
                "WHERE code=?",
                (item["roe"], item["roe_date"], item["industry"], code),
            )
            n += cur.rowcount or 0
        conn.execute("COMMIT")
        # 东财行业没覆盖到的（新股 / 停牌），给一个明确兜底而不是空串
        conn.execute("UPDATE symbol SET industry='其他' WHERE industry IS NULL OR industry=''")
        return n

    # ------------------------------------------------------------------ 分红
    def bonus_since(self) -> str:
        """分红回看窗口的起点。**必须覆盖日线窗口**。

        `_adjust_factors` 只认拿到的除权事件：漏掉某一次分红，除权日之前的
        价格就少乘一个因子，K 线上会凭空多出一根假跌——回测里那就是一根
        无法解释的 -X%，而且恰好落在除权日。所以窗口按 `self.bars` 反推
        （一年约 230 个交易日），再留一年缓冲给跨年分红。
        """
        years = self.bars / 230.0 + 1.0
        return (dt.date.today() - dt.timedelta(days=int(365 * years))).isoformat()

    def step_bonus(self, run_id: int = 0) -> int:
        """分红送配事件。同时产出两样东西：股息率、前复权因子。

        一次请求覆盖全市场，比按股票逐只查便宜三个数量级；页数随窗口线性增长，
        默认 520 根日线约 30 页，拉到 5 年约 60 页。
        """
        since = self.bonus_since()
        events = em.bonus_events(since)
        self._bonus = events
        if not events:
            return 0
        ttm_since = (dt.date.today() - dt.timedelta(days=365)).isoformat()
        conn = self.repo.conn
        rows = conn.execute("SELECT code, price, prev_close FROM snapshot").fetchall()
        conn.execute("BEGIN")
        n = 0
        for code, price, prev in rows:
            evs = events.get(code.split(".")[0]) or []
            ref = float(price or prev or 0)
            ttm = sum(e["dps"] for e in evs if e["date"] >= ttm_since)
            if ttm > 0 and ref > 0:
                conn.execute(
                    "UPDATE symbol SET dividend=? WHERE code=?",
                    (round(ttm / ref * 100, 2), code),
                )
                n += 1
        conn.execute("UPDATE symbol SET dividend=0 WHERE dividend IS NULL")
        conn.execute("COMMIT")
        return n

    # ------------------------------------------------------------------ 日线
    def _fetch_daily(self, code: str, incremental: bool) -> tuple[str, list[dict]]:
        """一只票的日线：新浪不复权原始价 + 分红事件推算的前复权因子。"""
        want = 12 if incremental else self.bars
        bars = sina.kline(code, want)
        if not bars:
            return code, []
        adj = _adjust_factors(bars, self._bonus.get(code.split(".")[0], []))
        rows = [
            {**b, "adj": adj[i], "amount": _est_amount(b)}
            for i, b in enumerate(bars)
        ]
        return code, rows

    def _needs_rebuild(self, code: str, rows: list[dict]) -> bool:
        """增量窗口里出现除权事件时，必须整段重建——因为除权会改写**全部**
        历史复权因子，只补最近几天会让新旧因子在时间轴上断层。"""
        if not rows:
            return False
        first = min(r["date"] for r in rows)
        for ev in self._bonus.get(code.split(".")[0], []):
            if ev["date"] >= first:
                return True
        return False

    def _flush_daily(self, batch: list[tuple[str, list[dict]]]) -> int:
        conn = self.repo.conn
        conn.execute("BEGIN")
        n = 0
        for code, rows in batch:
            if not rows:
                continue
            conn.executemany(
                "INSERT INTO daily(code, date, open, high, low, close, volume, amount, adj) "
                "VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(code, date) DO UPDATE SET "
                "open=excluded.open, high=excluded.high, low=excluded.low, "
                "close=excluded.close, volume=excluded.volume, amount=excluded.amount, "
                "adj=excluded.adj",
                [(code, r["date"], r["open"], r["high"], r["low"], r["close"],
                  r["volume"], r["amount"], r["adj"]) for r in rows],
            )
            n += len(rows)
        conn.execute("COMMIT")
        return n

    def _settled(self, meta_key: str, table: str = "daily") -> str:
        """上次同步是否已经把**最后一根 K 线**拿全了。

        判断要用「数据里的最后一天」而不是「同步时间」：快照表里的 ts 是抓取
        时刻，拿它当交易日会把「今天同步过」误当成「今天的数据已经有了」。
        规则是——上次同步晚于最后那根 K 线当天的收盘（15:00），且同步发生在
        今天之内。加「今天之内」是为了不把隔夜旧数据当成终值：明天再跑必须
        重拉，否则新出的那根 K 线永远补不上。

        返回最后那个交易日的日期串（不成立时空串）。
        """
        conn = self.repo.conn
        row = conn.execute(f"SELECT MAX(date) FROM {table}").fetchone()
        last_date = (row[0] if row else "") or ""
        synced_at = dbm.get_meta(conn, meta_key) or ""
        if not last_date or not synced_at:
            return ""
        today = dt.date.today().isoformat()
        if synced_at[:10] != today:
            return ""
        # 同步时刻必须晚于「最后一根 K 线那天」的收盘
        if synced_at[:10] < last_date:
            return ""
        if synced_at[:10] == last_date and int(synced_at[11:13] or 0) < 15:
            return ""
        return last_date

    def _skip_fresh(self, codes: list[str], incremental: bool) -> set[str]:
        """收盘后已经落过终值的票，重跑时不必再抓。

        只在增量模式下生效（`full=True` 是要强制重拉全部历史），且三个条件
        同时满足才跳：上次同步发生在同一交易日的 15:00 之后、该票日线最后
        一天就是这个交易日、近 30 天没有除权事件（有的话得整段重建复权因子）。
        """
        if not incremental:
            return set()
        trade_date = self._settled("daily_sync")
        if not trade_date:
            return set()
        recent = (dt.date.today() - dt.timedelta(days=30)).isoformat()
        conn = self.repo.conn
        last = {r[0]: r[1] for r in conn.execute("SELECT code, MAX(date) FROM daily GROUP BY code")}
        out: set[str] = set()
        for code in codes:
            if last.get(code) != trade_date:
                continue
            if any(ev["date"] >= recent for ev in self._bonus.get(code.split(".")[0], [])):
                continue
            out.add(code)
        return out

    def step_daily(self, run_id: int = 0, *, full: bool = False,
                   codes: list[str] | None = None, limit: int | None = None,
                   rounds: int = 3) -> int:
        codes = codes or [s["code"] for s in self.repo.universe()]
        if limit:
            codes = codes[:limit]
        if not self._bonus:
            # 单独跑 --only daily 时没有 bonus 缓存，就地补一次，否则复权因子全为 1
            # 这步要抓 30 页，先报一句，否则界面上会静默半分钟
            self.emit("daily", 0, len(codes), message="取除权事件（复权因子要用）")
            since = self.bonus_since()
            try:
                self._bonus = em.bonus_events(since)
            except Exception:                              # noqa: BLE001
                self._bonus = {}
        incremental = not full
        # 库里还没有的票必须整段拉，否则增量模式只给它 12 根，画不出图也进不了因子表
        have = {
            r[0] for r in self.repo.conn.execute("SELECT DISTINCT code FROM daily").fetchall()
        }
        written = 0
        rebuild: list[str] = []
        pending = list(codes)
        total = len(pending)

        # 收盘后再跑一次的话，库里最后一天已经是当日终值，这些票重抓等于白跑。
        # 判断标准不看「今天有没有同步过」，而看「上次同步是否发生在收盘后」——
        # 早盘 9:31 同步过的票，15:00 之后再跑一遍必须重新拉，否则留下的
        # 是一根只有半天的 K 线。
        skipped = self._skip_fresh(pending, incremental)
        if skipped:
            pending = [c for c in pending if c not in skipped]
            self.log(f"  {len(skipped)} 只已是收盘后数据，跳过")
        # 先把分母告诉界面：前端一进来就能画「0 / 5580」，而不是空条
        self.emit(
            "daily", len(skipped), total,
            message=(f"跳过 {len(skipped)} 只（已是当日终值）" if skipped else "开始抓日线"),
        )

        # 日线主源新浪偶尔也会连不上（并发高了被限流），一轮必然有漏网的。
        # 与其在抓取时死等，不如把失败的票收起来，隔几秒再补几轮。
        for attempt in range(1, rounds + 1):
            if not pending:
                break
            batch: list[tuple[str, list[dict]]] = []
            failed: list[str] = []
            errors: list[str] = []
            ok_n = 0

            def fetch_one(c: str) -> tuple[str, list[dict], bool]:
                """返回 (code, rows, 是否走的增量)。

                增量与否必须逐只判断：库里没有的票要整段拉 520 根，
                而「除权后需整段重建」这条规则只对**只补了 12 根**的票成立。
                早先这里错用了整批的 `incremental` 标志，导致全量拉的 520 根
                一撞上两年前的分红事件就被误判成需重建，既不落盘也不计失败。
                """
                inc = incremental and c in have
                _, rows = self._fetch_daily(c, inc)
                return c, rows, inc

            with ThreadPoolExecutor(max_workers=self.workers) as pool:
                futures = {pool.submit(fetch_one, c): c for c in pending}
                for i, fut in enumerate(as_completed(futures), 1):
                    code = futures[fut]
                    try:
                        _, rows, inc = fut.result()
                    except Exception as exc:               # noqa: BLE001 - 单票失败跳过
                        rows, inc = [], False
                        reason = f"{type(exc).__name__}: {exc}"[:120]
                        if len(errors) < 5:
                            errors.append(f"{code} {reason}")
                    if not rows:
                        failed.append(code)
                        continue
                    if inc and self._needs_rebuild(code, rows):
                        rebuild.append(code)
                        continue
                    batch.append((code, rows))
                    ok_n += 1
                    if len(batch) >= 120:
                        written += self._flush_daily(batch)
                        batch.clear()
                    if i % 25 == 0:
                        self.emit("daily", len(skipped) + i, total, message=f"日线 第 {attempt} 轮")
                    if i % 50 == 0:
                        self.log(
                            f"  日线 {i}/{len(pending)}（第 {attempt} 轮）"
                            f" · 成功 {ok_n} 只 · 待重建 {len(rebuild)} 只 · 失败 {len(failed)} 只"
                        )
                if batch:
                    written += self._flush_daily(batch)

            self.log(
                f"  日线第 {attempt} 轮完成：成功 {ok_n} 只 · 待重建 {len(rebuild)} 只"
                f" · 失败 {len(failed)} 只（共 {total} 只）"
            )
            for line in errors:
                self.log(f"    失败样例 {line}")
            pending = failed
            if pending and attempt < rounds:
                pause = 5.0 * attempt
                self.log(f"  停 {pause:.0f}s 让被限速的通道缓过来，再补抓 {len(pending)} 只")
                time.sleep(pause)

        if pending and len(pending) > total * 0.5:
            raise RuntimeError(f"日线抓取大面积失败：{len(pending)}/{total} 只没取到，检查数据源")
        if pending:
            self.log(f"  仍有 {len(pending)} 只缺日线（多为停牌/退市），已跳过")

        if rebuild:
            self.log(f"  检测到 {len(rebuild)} 只除权，整段重建")
            for i in range(0, len(rebuild), 120):
                chunk = rebuild[i: i + 120]
                with ThreadPoolExecutor(max_workers=self.workers) as pool:
                    results = list(pool.map(lambda c: self._fetch_daily(c, False), chunk))
                written += self._flush_daily([(c, rows) for c, rows in results])
                # 重建阶段占总数的比例很小，进度条停在总量分母上不往回跳，
                # 具体在重建第几只用 message 说明
                self.emit("daily", total, total, message=f"除权重建 {min(i + 120, len(rebuild))}/{len(rebuild)}")

        # 最新交易日的成交额改用快照的真实值（`_est_amount` 是估算，
        # 快照给的是交易所口径的真实成交额）。
        #
        # **必须校验日期**。原来只写了 `WHERE date=(SELECT MAX(date) FROM daily)`，
        # 隐含假设「跑同步时最新日线就是今天」。盘中跑同步时这个假设不成立：
        # 今天是 11-19 但最新日线还是 11-18，于是把今天开盘 8 分钟的成交额
        # 写进了昨天的日线——涨跌家数没变，只有成交额悄悄缩水到七分之一，
        # 极难发现（实测把 13833 亿写成 1943 亿）。
        # 加一条「快照日期 == 日线日期」才更新，盘中跑就保留估算值。
        conn = self.repo.conn
        conn.execute(
            "UPDATE daily SET amount=COALESCE((SELECT s.amount FROM snapshot s "
            "WHERE s.code=daily.code AND s.amount>0), amount) "
            "WHERE date=(SELECT MAX(date) FROM daily) "
            "  AND date=(SELECT substr(MAX(ts), 1, 10) FROM snapshot)"
        )
        dbm.set_meta(conn, "daily_sync", dt.datetime.now().isoformat(timespec="seconds"))
        self.emit("daily", total, total, message="日线完成")
        return written

    # ------------------------------------------------------------------ 资金流
    def step_flow(self, run_id: int = 0, *, codes: list[str] | None = None,
                  limit: int | None = None) -> int:
        codes = codes or [s["code"] for s in self.repo.universe()]
        if limit:
            codes = codes[:limit]
        conn = self.repo.conn
        written = 0

        # 资金流也是一天一变：收盘后已经落过的票重跑时跳过，避免重复抓
        settled = self._settled("flow_sync")
        if settled:
            done_codes = {
                r[0] for r in conn.execute("SELECT code FROM flow WHERE date=?", (settled,))
            }
            skip = [c for c in codes if c in done_codes]
            if skip:
                codes = [c for c in codes if c not in done_codes]
                self.log(f"  {len(skip)} 只资金流已是收盘后数据，跳过")
        if not codes:
            self.emit("flow", 1, 1, message="资金流已是当日终值")
            return written

        def flush(batch: list[tuple[str, list[dict]]]) -> None:
            nonlocal written
            conn.execute("BEGIN")
            n = 0
            for code, rows in batch:
                if not rows:
                    continue
                conn.executemany(
                    "INSERT INTO flow(code, date, main_net, small_net, mid_net, large_net, "
                    "xlarge_net, main_ratio) VALUES(?,?,?,?,?,?,?,?) "
                    "ON CONFLICT(code, date) DO UPDATE SET main_net=excluded.main_net, "
                    "small_net=excluded.small_net, mid_net=excluded.mid_net, "
                    "large_net=excluded.large_net, xlarge_net=excluded.xlarge_net, "
                    "main_ratio=excluded.main_ratio",
                    [(code, r["date"], r["main_net"], r["small_net"], r["mid_net"],
                      r["large_net"], r["xlarge_net"], r["main_ratio"]) for r in rows],
                )
                n += len(rows)
            conn.execute("COMMIT")
            written += n

        batch: list[tuple[str, list[dict]]] = []
        empty = 0
        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            futures = {pool.submit(sina.money_flow, c, self.flow_days): c for c in codes}
            for i, fut in enumerate(as_completed(futures), 1):
                code = futures[fut]
                try:
                    rows = fut.result()
                except Exception:                          # noqa: BLE001
                    rows = []
                if not rows:
                    empty += 1
                batch.append((code, rows))
                if len(batch) >= 150:
                    flush(batch)
                    batch.clear()
                    self.log(f"  资金流 {i}/{len(codes)}")
                if i % 25 == 0:
                    self.emit("flow", i, len(codes), message="资金流")
            if batch:
                flush(batch)
        if empty > len(codes) * 0.5:
            raise RuntimeError(f"资金流抓取大面积失败：{empty}/{len(codes)} 只返回空，检查数据源")
        dbm.set_meta(conn, "flow_sync", dt.datetime.now().isoformat(timespec="seconds"))
        self.emit("flow", len(codes), len(codes), message="资金流完成")
        return written

    # ------------------------------------------------------------------ 指数
    def step_index(self, run_id: int = 0) -> int:
        conn = self.repo.conn
        codes = [d["code"] for d in INDEX_DEFS]
        try:
            live = tx.realtime(codes)
        except Exception:                                  # noqa: BLE001 - 实时报价失败不影响历史
            live = {}
        now = dt.datetime.now().isoformat(timespec="seconds")
        written = 0

        for d in INDEX_DEFS:
            code = d["code"]
            bars = sina.index_kline(code, self.bars)
            conn.execute("BEGIN")
            if bars:
                conn.executemany(
                    "INSERT INTO index_daily(code, date, open, high, low, close, volume, amount) "
                    "VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(code, date) DO UPDATE SET "
                    "open=excluded.open, high=excluded.high, low=excluded.low, "
                    "close=excluded.close, volume=excluded.volume, amount=excluded.amount",
                    # 新浪不返回指数成交额，这里如实写 0，不拿「量×点数」编一个出来
                    [(code, b["date"], b["open"], b["high"], b["low"], b["close"],
                      b["volume"], 0.0) for b in bars],
                )
                written += len(bars)
            q = live.get(code)
            if q:
                conn.execute(
                    "INSERT INTO index_quote(code, name, price, prev_close, change, "
                    "change_pct, open, high, low, volume, amount, ts) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?) "
                    "ON CONFLICT(code) DO UPDATE SET name=excluded.name, price=excluded.price, "
                    "prev_close=excluded.prev_close, change=excluded.change, "
                    "change_pct=excluded.change_pct, open=excluded.open, high=excluded.high, "
                    "low=excluded.low, volume=excluded.volume, amount=excluded.amount, "
                    "ts=excluded.ts",
                    (code, d["name"], q.get("price", 0), q.get("prev_close", 0),
                     q.get("change", 0), q.get("change_pct", 0), q.get("open", 0),
                     q.get("high", 0), q.get("low", 0), q.get("volume", 0),
                     q.get("amount", 0), q.get("ts") or now),
                )
            conn.execute("COMMIT")

            source = INDEX_MEMBERS_FROM.get(code)
            members: list[str] = []
            if source == "eastmoney":
                try:
                    members = [f"{c}.{_suffix_of(c)}" for c in em.index_members(code)]
                except Exception:                          # noqa: BLE001
                    members = []
            elif source and source.startswith("exchange:"):
                ex = source.split(":", 1)[1]
                members = [s["code"] for s in self.repo.universe() if s["exchange"] == ex]
            if members:
                conn.execute("BEGIN")
                conn.execute("DELETE FROM index_member WHERE index_code=?", (code,))
                conn.executemany(
                    "INSERT OR IGNORE INTO index_member(index_code, code) VALUES(?,?)",
                    [(code, c) for c in members],
                )
                conn.execute("COMMIT")
        return written

    # ------------------------------------------------------------------ 因子
    # ------------------------------------------------------------------ 板块
    def step_sector(self, run_id: int = 0) -> int:
        """概念板块目录 + 成分股映射。

        **不抓行情**，只抓「有哪些概念」和「每个概念由哪些票组成」。拿到映射
        之后，板块的当日表现和多周期涨幅全部由 `Repo` 用本地 604 天日线现算
        ——这比拉板块 K 线稳得多，也不会碰到 `push2his` 的整机限频。

        成分股映射变化很慢（一个概念几个月才调整几只票），所以只在明显缺失
        时补：成员数不到应有七成的才重抓。否则每天白跑 250 次请求。
        """
        from concurrent.futures import ThreadPoolExecutor

        from .sources import sina as sina_src

        try:
            cats = sina_src.concept_list()
        except Exception as exc:                       # noqa: BLE001
            self.log(f"[sector] 概念目录取数失败：{type(exc).__name__}: {exc}")
            return 0
        if not cats:
            self.log("[sector] 概念目录返回空，本次跳过")
            return 0

        conn = self.repo.conn
        stamp = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conn.executemany(
            "INSERT INTO sector(code, name, kind, updated) VALUES(?,?, 'concept', ?) "
            "ON CONFLICT(code) DO UPDATE SET name=excluded.name, "
            "kind='concept', updated=excluded.updated",
            [(c["code"], c["name"], stamp) for c in cats])
        conn.commit()

        have = {code: n for code, n in conn.execute(
            "SELECT sector_code, COUNT(*) FROM sector_member GROUP BY sector_code")}
        todo = [c for c in cats
                if have.get(c["code"], 0) < max(2, int(c["count"] * 0.7))]
        if not todo:
            self.log(f"[sector] {len(cats)} 个概念，成分股映射已完整")
            return len(cats)

        self.emit("sector", 0, len(todo), message=f"抓 {len(todo)} 个概念成分股")

        def work(cat: dict) -> tuple[str, list[str]]:
            try:
                return cat["code"], sina_src.concept_members(cat["code"])
            except Exception:                          # noqa: BLE001
                return cat["code"], []

        added = 0
        with ThreadPoolExecutor(max_workers=max(1, self.workers)) as pool:
            # 迭代在主线程，连接不会跨线程——Repo 是 thread-local 连接，
            # 一旦让 worker 直接写库就会踩到「SQLite objects created in a
            # thread can only be used in that same thread」。
            for i, (code, members) in enumerate(pool.map(work, todo), 1):
                if members:
                    conn.execute("DELETE FROM sector_member WHERE sector_code=?", (code,))
                    conn.executemany(
                        "INSERT OR IGNORE INTO sector_member(sector_code, code) "
                        "VALUES(?,?)", [(code, m) for m in members])
                    added += len(members)
                if i % 20 == 0:
                    conn.commit()
                    self.emit("sector", i, len(todo), message=f"{i}/{len(todo)} 个概念")
        conn.commit()
        self.repo._sector_stamp = None                  # 让下一次读重新建索引
        self.log(f"[sector] {len(cats)} 个概念，补 {len(todo)} 个成员映射，共 {added} 条")
        return added

    def step_factor(self, run_id: int = 0, *, trade_date: str | None = None) -> int:
        conn = self.repo.conn
        date = trade_date or conn.execute("SELECT MAX(date) FROM daily").fetchone()[0]
        if not date:
            raise RuntimeError("日线为空，先同步 daily")
        universe = self.repo.universe()
        memberships = self.repo.membership()
        cols = ", ".join(FACTOR_COLUMNS)
        marks = ", ".join("?" * len(FACTOR_COLUMNS))
        conn.execute("DELETE FROM factor WHERE trade_date=?", (date,))

        # 必须**先算完全市场再落库**：板块联动因子是跨票聚合（同行业涨停家数），
        # 边算边写的话每个批次只看得见自己那 200 只，行业涨停数永远在 0~1 家，
        # 板块联动策略会静默地永远选不出东西。
        rows: list[dict] = []
        codes: list[str] = []
        for i in range(0, len(universe), 200):
            chunk = universe[i: i + 200]
            series = self.repo.series_bulk([s["code"] for s in chunk], days=520)
            for sym in chunk:
                s = series.get(sym["code"])
                if not s:
                    continue
                row = factor_calc.compute(sym, s, memberships.get(sym["code"], set()))
                if not row:
                    continue
                rows.append(row)
                codes.append(sym["code"])
            self.log(f"  因子计算 {min(i + 200, len(universe))}/{len(universe)}")

        factor_calc.cross_section(rows)

        payload = [(date, code, *[row[c] for c in FACTOR_COLUMNS])
                   for code, row in zip(codes, rows)]
        written = 0
        for i in range(0, len(payload), 1000):
            conn.execute("BEGIN")
            conn.executemany(
                f"INSERT OR REPLACE INTO factor(trade_date, code, {cols}) "
                f"VALUES(?, ?, {marks})",
                payload[i: i + 1000],
            )
            conn.execute("COMMIT")
            written += len(payload[i: i + 1000])

        dbm.set_meta(conn, "factor_sync", dt.datetime.now().isoformat(timespec="seconds"))
        dbm.set_meta(conn, "trade_date", date)
        return written


def _adjust_factors(bars: list[dict], events: list[dict]) -> list[float]:
    """由除权事件推算前复权因子。

    口径：`除权参考价 = (除权前收盘 - 每股派息) / (1 + 送转比例)`，
    除权日**之前**的价格乘以 `参考价 / 前收盘`。多个事件连乘。

    为什么不用「除权日之后的价格反推」：送转会让股数变化，只有按前收盘
    重算参考价才能同时处理「纯派息」「纯送转」「派息 + 送转」三种情形，
    这是交易所的官方算法，也是所有行情软件一致的口径。
    """
    n = len(bars)
    adj = [1.0] * n
    if not events or n < 2:
        return adj
    dates = [b["date"] for b in bars]
    # 事件 → 序列中的分界下标（该下标及之后是除权后的价格）
    boundaries: dict[int, list[float]] = {}
    for ev in events:
        k = bisect.bisect_left(dates, ev["date"])
        if k <= 0 or k >= n:
            continue
        prev = bars[k - 1]["close"]
        ref = (prev - ev["dps"]) / (1.0 + ev["ratio"])
        if prev > 0 and ref > 0:
            boundaries.setdefault(k, []).append(ref / prev)

    factor = 1.0
    for i in range(n - 1, -1, -1):
        adj[i] = round(factor, 6)
        for ratio in boundaries.get(i, ()):
            factor *= ratio
    return adj


def _pinyin_table() -> Callable[[str], tuple[str | None, str | None]]:
    """拼音检索列。装了 pypinyin 才算，没装就留空——不为一个检索功能塞 8MB 词库。"""
    try:
        from pypinyin import Style, lazy_pinyin      # type: ignore[import-not-found]
    except Exception:                                 # noqa: BLE001
        return lambda name: (None, None)

    def build(name: str) -> tuple[str | None, str | None]:
        try:
            initials = "".join(lazy_pinyin(name, style=Style.FIRST_LETTER))
            full = "".join(lazy_pinyin(name))
        except Exception:                             # noqa: BLE001
            return None, None
        return initials.lower() or None, full.lower() or None

    return build
