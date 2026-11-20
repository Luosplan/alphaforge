"""自动驾驶：盘中盯盘 → 收盘复盘 → 进化 → 次日计划，一天一轮。

一个后台守护线程，每 30 秒看一次表，只在两个时点做事：

- **盘中**（交易日 9:30–11:30 / 13:00–15:00）：每 5 分钟刷一次实时快照，
  对「持仓 + 今日候选」做一轮盯盘分析。盘中不做任何交易动作。
- **收盘**（15:05 之后，每天一次）：同步当日日线 → 推进模拟盘（成交昨日委托、
  生成次日计划）→ 生成复盘 → 对每条策略跑一遍进化 → 落盘。

为什么进化放在收盘而不是半夜
----------------------------
进化要吃「截至今天的完整日线」。收盘后数据一到就能跑，跑完的参数正好用来
生成次日计划——顺序不能反，否则明天用的是用前天数据搜出来的参数。
实测两条策略各 24×6=144 次评估、每次 250 天回放，总共约 60 秒，
在这个时间点完全不必异步。

为什么每天进化不会把参数调坏
----------------------------
搜索本身每次都会给出「比基线分高」的基因组（它就是训练集上挑最好的），
所以**分数不能作为采纳依据**。真正的闸门是 `harness.adopt_verdict`：
样本外 edge > 0.2 且连续两次一致才换参数。天天跑、极少换，才是安全的用法。
"""

from __future__ import annotations

import datetime as dt
import json
import os
import threading
import time
from pathlib import Path

from alphaforge_engine import aiwatch, harness, paper

ROOT = Path(__file__).resolve().parents[3]
STATE_FILE = Path(os.environ.get("AF_AUTOPILOT_PATH") or (ROOT / "data" / "autopilot.json"))

TICK_SECONDS = 30          # 巡检间隔
INTRADAY_SECONDS = 300     # 盘中分析间隔（用户选的是 5 分钟）
EOD_HOUR, EOD_MINUTE = 15, 5

# 盘中时段（含端点）。A 股 11:30–13:00 休市，这一段不跑。
SESSIONS = [((9, 30), (11, 30)), ((13, 0), (15, 0))]


def _now() -> dt.datetime:
    return dt.datetime.now()


def _default_state() -> dict:
    return {
        "enabled": False,
        "started_at": None,
        "last_tick": None,
        "last_intraday": None,
        "last_eod_date": None,
        "running": None,            # 当前正在做什么，给前端显示
        "watch": None,              # 最近一轮盘中分析
        "review": None,             # 最近一次收盘复盘
        "eod": None,                # 最近一次收盘任务的结果
        "history": [],              # 盘中分析留最近 60 条
        "error": None,
    }


def _elapsed_ratio(now: dt.datetime) -> float:
    """当日已交易时间占 240 分钟的比例。

    量比要用它折算：新浪快照只给当日累计成交量，直接除以 5 日均量，
    早盘会显示成 0.1 这种假「缩量」——不是没量，是还没交易完。
    """
    minutes = 0.0
    for (sh, sm), (eh, em) in SESSIONS:
        start = now.replace(hour=sh, minute=sm, second=0, microsecond=0)
        end = now.replace(hour=eh, minute=em, second=0, microsecond=0)
        if now <= start:
            continue
        minutes += min((now - start).total_seconds() / 60,
                       (end - start).total_seconds() / 60)
    return max(minutes, 1.0) / 240.0


def _fill_volratio(mkt, rows: list[dict], now: dt.datetime) -> None:
    """给少数几只票补量比。只算被监控的那几只——全市场算一遍代价不值。"""
    ratio = _elapsed_ratio(now)
    for row in rows:
        code = row.get("code")
        if not code:
            continue
        try:
            series = mkt.repo.series(code, 7)
        except Exception:                                # noqa: BLE001
            continue
        vols = [float(v or 0) for v in (series or {}).get("volume") or []][-6:-1]
        vols = [v for v in vols if v > 0]
        today = float(row.get("volume") or 0)
        if not vols or not today:
            continue
        avg = sum(vols) / len(vols)
        if avg > 0:
            row["volratio"] = round(today / ratio / avg, 2)


def _fill_industry(mkt, rows: list[dict]) -> None:
    """给持仓与候选补上行业联动背景（截至上一交易日收盘）。

    为什么这条对超短特别重要：同样是「今天涨 8%」，如果同行业里有 2 家
    涨停，那是板块启动的跟风位；如果同行业全绿，那是逆势挣扎。
    模型看不到这个差别，就只能对着涨跌幅说废话。
    """
    if not rows:
        return
    try:
        from alphaforge_engine.factor_board import get_board
        board = get_board(mkt)
        idx = {c: i for i, c in enumerate(board.codes)}
        cols = {k: board.column(k)
                for k in ("ind_limit_cnt", "ind_rank_pct", "ind_strength")}
    except Exception:                                    # noqa: BLE001
        return
    for row in rows:
        i = idx.get(row.get("code"))
        if i is None:
            continue
        for key, col in cols.items():
            if col and i < len(col):
                row[key] = col[i]


class Autopilot:
    def __init__(self, path: Path = STATE_FILE):
        self.path = path
        self._lock = threading.RLock()
        self.state = self._load()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    # ---------------------------------------------------------------- 状态
    def _load(self) -> dict:
        base = _default_state()
        if self.path.exists():
            try:
                base.update(json.loads(self.path.read_text(encoding="utf-8")))
            except (json.JSONDecodeError, OSError):
                pass
        return base

    def _save(self) -> None:
        with self._lock:
            self.state["updated_at"] = _now().isoformat(timespec="seconds")
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.state, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, self.path)

    def status(self) -> dict:
        with self._lock:
            return json.loads(json.dumps(self.state))

    # ---------------------------------------------------------------- 生命周期
    def start(self) -> dict:
        with self._lock:
            self.state["enabled"] = True
            if not self.state.get("started_at"):
                self.state["started_at"] = _now().isoformat(timespec="seconds")
        self._save()
        if self._thread is None or not self._thread.is_alive():
            self._stop.clear()
            self._thread = threading.Thread(target=self._loop, name="autopilot",
                                            daemon=True)
            self._thread.start()
        return self.status()

    def stop(self) -> dict:
        with self._lock:
            self.state["enabled"] = False
            self.state["running"] = None
        self._save()
        self._stop.set()
        return self.status()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                if self.state.get("enabled"):
                    self.tick()
            except Exception as exc:                      # noqa: BLE001
                with self._lock:
                    self.state["error"] = f"{type(exc).__name__}: {exc}"
                self._save()
            self._stop.wait(TICK_SECONDS)

    # ---------------------------------------------------------------- 主循环
    def tick(self) -> None:
        now = _now()
        with self._lock:
            self.state["last_tick"] = now.isoformat(timespec="seconds")
        if now.weekday() >= 5:                 # 周末不做事
            return
        if self._in_session(now):
            self._maybe_intraday(now)
        elif self._after_close(now):
            self._maybe_eod(now)

    @staticmethod
    def _in_session(now: dt.datetime) -> bool:
        cur = (now.hour, now.minute)
        return any(start <= cur <= end for start, end in SESSIONS)

    @staticmethod
    def _after_close(now: dt.datetime) -> bool:
        return (now.hour, now.minute) >= (EOD_HOUR, EOD_MINUTE)

    def _maybe_intraday(self, now: dt.datetime) -> None:
        last = self.state.get("last_intraday")
        if last:
            try:
                gap = (now - dt.datetime.fromisoformat(last)).total_seconds()
                if gap < INTRADAY_SECONDS:
                    return
            except ValueError:
                pass
        self.run_intraday()

    def _maybe_eod(self, now: dt.datetime) -> None:
        if self.state.get("last_eod_date") == now.date().isoformat():
            return
        self.run_eod()

    # ---------------------------------------------------------------- 对象
    @staticmethod
    def _strategy_ids() -> list[str]:
        """盯盘/推进/进化的对象：**所有已开立的账户**。

        之前写死 `("trend", "limitup")`，新增的策略即使开了户也永远不会被
        盯盘、不会被推进、不会进化——用户看到的就是「新策略是死的」。
        改成读账户表之后，开一个账户就自动进入这条流水线。
        """
        try:
            from .lab_store import get_lab
            return list((get_lab().state.get("accounts") or {}).keys())
        except Exception:                                # noqa: BLE001
            return ["trend", "limitup"]

    # ---------------------------------------------------------------- 盘中
    def run_intraday(self) -> dict:
        """刷快照 → 组装事实 → 分析 → 落一条。盘中不产生任何交易动作。"""
        from alphaforge_engine.market import get_market
        from alphaforge_data.sync import Syncer
        from .lab_store import get_lab

        now = _now()
        with self._lock:
            self.state["running"] = "盘中盯盘"
        self._save()
        try:
            syncer = Syncer(get_market().repo)
            written = syncer.refresh_snapshot()
            mkt = get_market()
            breadth = mkt.repo.market_breadth()
            index = mkt.repo.index_quote("000300.SH")

            lab = get_lab()
            holdings: list[dict] = []
            candidates: list[dict] = []
            # 择时状态取第一个已启动账户的口径带到页面上。各策略的 `market_ma`
            # 目前统一由 DEFAULTS 给（120），取谁都一样；将来若按策略分开放，
            # 这里应该改成按策略分组展示，而不是随便挑一个糊过去。
            regime: dict | None = None
            for sid in self._strategy_ids():
                acc = lab.account(sid)
                if not acc:
                    continue
                snap = paper.snapshot(mkt, acc)
                if regime is None:
                    regime = snap.get("regime")
                for pos in snap.get("positions") or []:
                    holdings.append({**pos, "strategy": sid})
                for plan in snap.get("plans") or []:
                    candidates.append({**plan, "strategy_name": plan.get("strategy_name")
                                       or sid})

            # 计划卡里的价格是「生成那天」的收盘价，盘中要看的是「此刻」的。
            # 用快照覆盖，同时补上计划卡本身没有的涨跌幅与量比——
            # 少了这两个字段，盘中分析等于拿昨天的行情说今天的话。
            quotes = mkt.repo.snapshot_quotes(
                [x.get("code") for x in holdings + candidates if x.get("code")])
            for row in holdings:
                q = quotes.get(row.get("code"))
                if q:
                    row["price"] = q["price"]
                    row["change_pct"] = q["change_pct"]
            for row in candidates:
                q = quotes.get(row.get("code"))
                if q:
                    row["live_price"] = q["price"]
                    row["change_pct"] = q["change_pct"]
                    row["volratio"] = q["volratio"]
            for row in holdings + candidates:
                row["volume"] = (quotes.get(row.get("code")) or {}).get("volume")
            _fill_volratio(mkt, holdings + candidates, now)
            _fill_industry(mkt, holdings + candidates)

            ctx = aiwatch.build_watch_context(
                breadth=breadth, index=index, holdings=holdings,
                candidates=candidates,
                as_of=now.strftime("%Y-%m-%d %H:%M"))
            result = aiwatch.analyze_watch(ctx)
            doc = {
                "at": now.isoformat(timespec="seconds"),
                "as_of": ctx["时间"],
                "snapshot_rows": written,
                "fresh": breadth.get("fresh"),
                "market": ctx["市场"],
                "breadth": breadth,
                "index": index,
                "analysis": result,
                # 存原始对象而不是 `ctx` 里的中文键版本：`ctx` 是给模型看的
                # （中文键更省 token、模型也更稳），前端读的是这些原始字段。
                # 之前直接把中文键版本存进来，前端按 `code/price` 取全是 undefined。
                "holdings": holdings,
                "candidates": candidates,
                "regime": regime,
            }
            with self._lock:
                self.state["watch"] = doc
                self.state["last_intraday"] = now.isoformat(timespec="seconds")
                hist = list(self.state.get("history") or [])
                hist.append({"at": doc["at"], "mood": result.get("mood"),
                             "summary": result.get("summary"),
                             "source": result.get("source")})
                self.state["history"] = hist[-60:]
                self.state["error"] = None
            self._save()
            return doc
        finally:
            with self._lock:
                self.state["running"] = None
            self._save()

    # ---------------------------------------------------------------- 收盘
    def eod_gate(self, force: bool = False) -> None:
        """收盘任务的前置检查。不通过就抛 `RuntimeError`。

        单独抽出来是给 HTTP 层用的：补跑收盘要跑几分钟，接口不能同步等，
        只能「先检查、再丢线程」。检查留在 `run_eod` 里，接口就没法在
        起线程之前把错误回给用户。
        """
        now = _now()
        if not force and now.weekday() < 5 and not self._after_close(now):
            raise RuntimeError(
                f"现在 {now:%H:%M} 还没收盘，收盘任务会把盘中数据写进日线。"
                "确实要提前补跑请传 force=true。")

    def run_eod(self, force: bool = False) -> dict:
        """同步日线 → 推进模拟盘 → 复盘 → 进化 → 落盘。

        `force=False` 时拒绝在收盘前跑：`step_daily` 会把当时那一刻的行情
        当成当日收盘写进日线，`paper.step` 再拿它去成交——盘中手动补跑一次，
        账户就会带着半天的数据往前推一天，而且事后无法区分哪笔是脏的。
        """
        from alphaforge_engine.market import get_market
        from alphaforge_data.sync import Syncer
        from .deps import store
        from .lab_store import get_lab

        now = _now()
        today = now.date().isoformat()
        self.eod_gate(force)
        with self._lock:
            self.state["running"] = "收盘任务"
        self._save()
        steps: list[dict] = []
        try:
            mkt = get_market()
            syncer = Syncer(mkt.repo)
            steps.append({"step": "刷新快照", "rows": syncer.refresh_snapshot()})
            try:
                steps.append({"step": "同步日线", "rows": syncer.step_daily()})
                syncer.step_factor()
                steps.append({"step": "重算因子", "rows": 0})
            except Exception as exc:                     # noqa: BLE001
                steps.append({"step": "同步日线", "error": f"{type(exc).__name__}: {exc}"})

            mkt = get_market()
            lab = get_lab()
            profile = store().profile()
            from .routers import lab as lab_router   # 延迟导入：lab 会反过来 import 本模块
            advanced: dict[str, dict] = {}
            for sid in self._strategy_ids():
                # 走 `_account` 而不是直接读账户：它负责把「从未成交过的空账户」
                # 的 spec 刷回当前策略定义。trend 账户曾经采纳过一次进化，
                # 参数被推到 top_n=7 / 行业上限 4 / 时间止损 30 天，一笔没成交
                # 却永久冻结——那不是超短，是另一条策略。
                try:
                    spec = lab_router._spec_of(sid)
                    acc = lab_router._account(sid, spec)
                except Exception:                        # noqa: BLE001
                    acc = lab.account(sid)
                if not acc:
                    continue
                # 不加 force：`step` 自带「今天已推进过就跳过」的守卫，
                # 收盘任务重跑一次不该把账户往前推两天。
                acc, info = paper.step(mkt, acc, profile)
                lab.put_account(acc)
                advanced[sid] = info
            steps.append({"step": "推进模拟盘", "accounts": list(advanced)})

            # ---- 复盘：成交只取今天的
            breadth = mkt.repo.market_breadth()
            index = mkt.repo.index_quote("000300.SH")
            merged = {"nav": 0.0, "cash": 0.0, "positions": [], "trades": [],
                      "plans": []}
            for sid in advanced:
                acc = lab.account(sid) or {}
                snap = paper.snapshot(mkt, acc)
                merged["nav"] += float(snap["summary"]["nav"])
                merged["cash"] += float(acc.get("cash") or 0)
                merged["positions"].extend(snap.get("positions") or [])
                merged["trades"].extend(
                    [t for t in (acc.get("trades") or []) if t.get("date") ==
                     mkt.trade_date.isoformat()] if mkt.trade_date else [])
                merged["plans"].extend(snap.get("plans") or [])
            review_ctx = aiwatch.build_review_context(
                breadth=breadth, index=index, account=merged,
                trades=merged["trades"], plans=merged["plans"],
                as_of=today)
            review = aiwatch.analyze_review(review_ctx)
            steps.append({"step": "收盘复盘", "source": review.get("source")})

            # ---- 进化：每个账户各跑一轮，采纳与否由门槛决定
            evolved = []
            for sid in self._strategy_ids():
                got = lab_router.run_evolve_blocking(sid, apply=True)
                evolved.append({"strategy": sid, **{k: got.get(k) for k in
                                                    ("action", "reason", "edge", "applied")}})
            steps.append({"step": "进化", "result": evolved})

            with self._lock:
                self.state["review"] = {"at": now.isoformat(timespec="seconds"),
                                        "as_of": today, "text": review,
                                        "market": review_ctx["市场"],
                                        "account": review_ctx["账户"]}
                self.state["eod"] = {"at": now.isoformat(timespec="seconds"),
                                     "date": today, "steps": steps, "evolved": evolved}
                self.state["last_eod_date"] = today
                self.state["error"] = None
            self._save()
            return self.state["eod"]
        except Exception as exc:                         # noqa: BLE001
            with self._lock:
                self.state["error"] = f"{type(exc).__name__}: {exc}"
                self.state["eod"] = {"at": now.isoformat(timespec="seconds"),
                                     "date": today, "steps": steps,
                                     "error": self.state["error"]}
            self._save()
            raise
        finally:
            with self._lock:
                self.state["running"] = None
            self._save()

    def reset_eod(self) -> dict:
        """清掉「今天已跑过收盘」的标记，用于手动补跑。"""
        with self._lock:
            self.state["last_eod_date"] = None
        self._save()
        return self.status()


_pilot: Autopilot | None = None


def get_pilot() -> Autopilot:
    global _pilot
    if _pilot is None:
        _pilot = Autopilot()
    return _pilot
