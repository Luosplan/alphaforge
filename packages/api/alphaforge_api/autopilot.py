"""实时盯盘：盘中盯盘 → 收盘复盘 → 进化 → 次日计划，一天一轮。

一个后台守护线程，每 30 秒看一次表，只在两个时点做事：

- **盘中**（交易日 9:30–11:30 / 13:00–15:00）：每 5 分钟刷一次实时快照，
  对「持仓 + 今日候选」做一轮盯盘分析。盘中不做任何交易动作。
- **收盘**（15:05 之后，每天一次）：同步当日日线 → 推进模拟盘（成交昨日委托、
  生成次日计划）→ 生成复盘 → 对每条策略跑一遍进化 → 落盘。

为什么进化放在收盘而不是半夜
----------------------------
进化要吃「截至今天的完整日线」。收盘后数据一到就能跑，跑完的参数正好用来
生成次日计划——顺序不能反，否则明天用的是用前天数据搜出来的参数。
窗口 60 天 × 24×6=144 次评估，两条策略几十秒内跑完，这个时间点完全不必异步。

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

from alphaforge_core.universe import pc as price_limit
from alphaforge_engine import aiwatch, harness, paper

ROOT = Path(__file__).resolve().parents[3]
STATE_FILE = Path(os.environ.get("AF_AUTOPILOT_PATH") or (ROOT / "data" / "autopilot.json"))

TICK_SECONDS = 30          # 巡检间隔
INTRADAY_SECONDS = 300     # 盘中分析间隔（用户选的是 5 分钟）
EOD_HOUR, EOD_MINUTE = 15, 5

# 收盘任务同一天最多自动重试几次。失败多半是数据源抖动，重试一两次就能过；
# 真有更严重的问题（源改版、库锁死）时反复重试只会把后台线程占满。
EOD_MAX_RETRY = 3

# 「日线源还没更新」时的退避间隔。15:05 那一次经常拿不到当天日线（源要晚十几分钟），
# 隔一会儿再试比直接放弃一整天划算；但也不能让巡检每 30 秒重跑一遍同步。
EOD_SKIP_BACKOFF = dt.timedelta(minutes=20)

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
        "review": None,             # 最近一次收盘复盘（= reviews[0] 的快捷方式）
        # 复盘日记：每个交易日一篇，新的在前。`review` 只留最新一条，
        # 而「复盘日记」页要看的是连续几十天怎么想、怎么改。
        "reviews": [],
        "eod": None,                # 最近一次收盘任务的结果
        # 收盘任务今天连续失败了几次。不设上限的后果是实测过的：新浪快照接口
        # 偶发 connection refused，巡检每 30 秒重来一遍、每遍跑几分钟，
        # 整个后台线程被占满，盘中分析也跟着停摆，页面上「收盘任务」转圈不停。
        "eod_retry": {"date": None, "count": 0},
        "history": [],              # 盘中分析留最近 60 条
        # 每日「计划 vs 实际」的执行质量，留最近 60 天。它不只是给人看的：
        # 进化采纳参数前要读它（`harness.paper_verdict`）——回测假设的三件事
        # （挂了就能成交、成交价≈开盘价、停牌照跑）只在模拟盘里被真正检验过。
        "plan_history": [],
        "plan_health": None,
        "error": None,
    }


# 执行质量看最近多少个**有挂单**的交易日。20 个：超短的一天挂 3~5 张，
# 20 天就有 60~100 张，足够看出成交率和滑点是不是系统性偏坏；
# 再拉长会掺进几个月前已经换过参数的市场。
HEALTH_DAYS = 20

# 复盘日记留多少天。一年约 244 个交易日，一篇 3~6KB，180 篇也就 1MB——
# 足够回看「这套规则在一年里怎么变、当时为什么变」，又不至于把状态文件撑大。
REVIEW_KEEP_DAYS = 180


def _plan_health(history: list[dict]) -> dict:
    """把每日对账滚成一份执行质量：成交率、滑点、样本天数。

    只认 `order_count > 0` 的日子。择时关闭时每天都是「挂 0 张、成交 0 张」，
    把它们算进平均会把成交率稀释成一个没有意义的小数——那不是执行差，
    是规则主动不开仓。
    """
    rows = [r for r in (history or []) if int(r.get("order_count") or 0) > 0]
    rows = rows[-HEALTH_DAYS:]
    if not rows:
        return {"days": 0, "orders": 0, "filled": 0, "fill_rate": None,
                "avg_adverse_bps": None, "from": None, "to": None,
                "label": "还没有成交样本", "tone": "flat"}
    orders = sum(int(r.get("order_count") or 0) for r in rows)
    filled = sum(int(r.get("filled_count") or 0) for r in rows)
    # 滑点按**挂单张数**加权，不是按天平均：挂 1 张的那天和挂 5 张的那天
    # 权重一样，会把一两张极端成交的滑点放大成整段时间的印象。
    bps_num = sum(float(r.get("avg_adverse_bps") or 0) * int(r.get("order_count") or 0)
                  for r in rows if r.get("avg_adverse_bps") is not None)
    bps_den = sum(int(r.get("order_count") or 0)
                  for r in rows if r.get("avg_adverse_bps") is not None)
    rate = round(filled / orders * 100, 1) if orders else None
    bps = round(bps_num / bps_den, 1) if bps_den else None
    if orders < 8:
        label, tone = f"样本 {orders} 张，尚不足以下结论", "flat"
    elif (rate is not None and rate < 50) or (bps is not None and bps > 80):
        label, tone = "执行质量偏差（成交率低或滑点大）", "warn"
    else:
        # 用 brand 而不是 up：红色在这套配色里只代表「涨」，一个中性的
        # 健康状态标成红的，会被读成「出问题了」。
        label, tone = "执行质量正常", "brand"
    return {"days": len(rows), "orders": orders, "filled": filled,
            "fill_rate": rate, "avg_adverse_bps": bps,
            "from": rows[0].get("date"), "to": rows[-1].get("date"),
            "label": label, "tone": tone}


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
        # `reviews` 是后加的字段：老状态文件里只有当天那一篇 `review`。
        # 直接置空会让复盘日记一上线就「一篇都没有」，但那天明明写过——
        # 所以把旧的那篇搬进列表，往后每跑一次 EOD 再往前接一篇。
        if not base.get("reviews") and isinstance(base.get("review"), dict):
            old = base["review"]
            if old.get("as_of"):
                base["reviews"] = [old]
        # `running` 是「进程内正在干什么」，不是一个该被持久化的状态：
        # 进程重启（开发时的 --reload 尤其常见）会把正在跑的收盘任务直接
        # 打断，`finally` 来不及执行，于是 `running` 永远停在「收盘任务」，
        # 页面上转圈转到用户来问。新进程刚起来时它一定是空的。
        base["running"] = None
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
        # 上一次在「日线源还没更新」处空转了：隔 20 分钟再试，别每 30 秒重跑一遍同步
        skip_at = self.state.get("eod_skip_at")
        if skip_at and (now - dt.datetime.fromisoformat(skip_at)) < EOD_SKIP_BACKOFF:
            return
        self.run_eod()

    # ---------------------------------------------------------------- 对象
    @staticmethod
    def _strategy_ids() -> list[str]:
        """盯盘/推进/进化的对象：**当前策略集里的账户**。

        之前写死 `("trend", "limitup")`（两条都已删除），新增的策略即使开了户也永远不会被
        盯盘、不会被推进、不会进化——用户看到的就是「新策略是死的」。
        改成读账户表之后，开一个账户就自动进入这条流水线。

        还要再按当前策略集过一道：已删除策略（缩量蓄势 / 板块联动 / 弱转强）
        的旧账户留在 lab.json 里，如果照样推进，页面上会多出三个永远不动的
        账户，进化也会把预算花在已经不存在的策略上。
        """
        from alphaforge_engine.selector import STRATEGY_MAP

        try:
            from .lab_store import get_lab
            ids = list((get_lab().state.get("accounts") or {}).keys())
        except Exception:                                # noqa: BLE001
            ids = list(STRATEGY_MAP)
        live = [sid for sid in ids if sid in STRATEGY_MAP]
        return live or list(STRATEGY_MAP)

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
            syncer = Syncer(get_market().repo, workers=24)
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

        单独抽出来是给 HTTP 层用的：生成复盘日记要跑几分钟，接口不能同步等，
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
        from alphaforge_engine.market import get_market, reset_market
        from alphaforge_data.sync import Syncer
        from .deps import store
        from .lab_store import get_lab

        now = _now()
        today = now.date().isoformat()
        self.eod_gate(force)
        with self._lock:
            self.state["running"] = "生成复盘日记"
        self._save()
        steps: list[dict] = []
        try:
            mkt = get_market()
            syncer = Syncer(mkt.repo, workers=24)
            # 快照只服务「盘中看当前价」，收盘链路真正要用的是日线。新浪这个
            # 接口偶发 connection refused（实测 2026-11-20 15:07 那次就是），
            # 它挂在最外层时一次抖动就能把推进、复盘、进化全炸掉——当天
            # 复盘日记直接空一天，正是用户最先发现的那种「日报没了」。
            try:
                steps.append({"step": "刷新快照", "rows": syncer.refresh_snapshot()})
            except Exception as exc:                     # noqa: BLE001
                steps.append({"step": "刷新快照",
                              "error": f"{type(exc).__name__}: {exc}"})
            # 收盘链路原来只跑「日线 + 因子」：资金流和指数都不更新，于是
            # ① 资金类因子用的是昨天的数；② 择时看的是昨天的沪深300 ——
            # 两个都会让「明天买什么」和「今天收盘的事实」对不上。
            # 每一步单独 try：资金流被限流不该连带把复盘和进化一起跳过。
            for label, call in (("同步日线", syncer.step_daily),
                                ("同步资金流", syncer.step_flow),
                                ("同步指数", syncer.step_index)):
                t0 = time.time()
                try:
                    rows = call()
                    steps.append({"step": label, "rows": rows,
                                  "seconds": round(time.time() - t0, 1)})
                except Exception as exc:                 # noqa: BLE001
                    steps.append({"step": label,
                                  "error": f"{type(exc).__name__}: {exc}"})
            try:
                syncer.step_factor()
                steps.append({"step": "重算因子", "rows": 0})
            except Exception as exc:                     # noqa: BLE001
                steps.append({"step": "重算因子", "error": f"{type(exc).__name__}: {exc}"})

            # 同步刚把今天的日线写进库，但进程里那个 MarketEngine 还缓存着**同步前**
            # 的交易日列表（`dates` 是惰性缓存）。不重建就会拿昨天的最后交易日
            # 去比今天，守卫每天必然判成「非交易日」——实测 2026-11-26：日线
            # 15:30 已入库，这里读到的还是 11-25，收盘任务跳过，复盘日记一篇没生成。
            reset_market()
            mkt = get_market()
            # 今天到底有没有**新的交易日**？没有就别往下走。
            #
            # 这条守卫是必须的：节假日（周一到周五但休市）和「日线源还没更新」
            # 这两种情况下，`trade_date` 仍停在上一个交易日，而下面会拿昨天的
            # 委托去对今天的成交——一张都没成交，于是那一天就被记成
            # 「挂 5 张、成交 0 张、执行率 0%」。这个假数字会直接进入
            # 执行质量（甚至否决进化采纳），比不跑更糟。
            trade_day = mkt.trade_date.isoformat() if mkt.trade_date else None
            if trade_day != today:
                reason = (f"本地最新交易日是 {trade_day or '（无数据）'}，不是今天 {today}"
                          "——日线源还没更新，或今天休市")
                retry = dict(self.state.get("eod_retry") or {})
                if retry.get("date") != today:
                    retry = {"date": today, "count": 0}
                retry["count"] = int(retry.get("count") or 0) + 1
                give_up = retry["count"] >= EOD_MAX_RETRY
                if give_up:
                    reason += (f"。已自动重试 {retry['count']} 次，今天不再自动跑；"
                               "数据到齐后点「生成复盘日记」")
                steps.append({"step": "跳过收盘任务", "reason": reason})
                with self._lock:
                    self.state["eod_retry"] = retry
                    self.state["eod_skip_at"] = now.isoformat(timespec="seconds")
                    self.state["eod"] = {"at": now.isoformat(timespec="seconds"),
                                         "date": today, "steps": steps,
                                         "skipped": True, "reason": reason,
                                         "retry": retry["count"]}
                    # 只有用尽重试才标记「今天跑过」。否则 15:05 数据的源还没出完
                    # 就永久放弃这一天的复盘——20 分钟后巡检会再试一次。
                    if give_up:
                        self.state["last_eod_date"] = today
                    self.state["error"] = None
                self._save()
                return self.state["eod"]

            lab = get_lab()
            profile = store().profile()
            from .routers import lab as lab_router   # 延迟导入：lab 会反过来 import 本模块
            advanced: dict[str, dict] = {}
            prev_snap: dict[str, dict] = {}
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
                # 推进之前先把「昨天挂的单」抄下来：`paper.step` 会用今天收盘
                # 生成的委托覆盖 `orders`，跑完再读就只剩明天的单，
                # 「昨天说买什么、今天成交没有」永远算不出来。
                prev_snap[sid] = {"orders": list(acc.get("orders") or []),
                                  "plans": list(acc.get("plans") or [])}
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
            # 持仓逐票：三个账户各有自己的 params（time_stop 不同），所以动作
            # 必须按账户分别推，不能合并后再套一份参数——那样时间止损会算错。
            holdings: list[dict] = []
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
                for row in paper.holding_rows(acc.get("positions") or [],
                                              acc.get("params") or {},
                                              acc.get("plans") or []):
                    row["strategy_id"] = sid
                    row["strategy_name"] = acc.get("name") or sid
                    holdings.append(row)
            # 计划执行率要在**推进之后**算：成交是今天开盘那一格发生的，
            # 挂在昨天的委托上。这一步只统计，不改任何参数——参数只由
            # 进化 + 样本外门槛动，复盘改参数等于用一天的噪声调模型。
            plan_stats = _plan_review(mkt, lab, advanced, prev_snap, today)
            steps.append({"step": "计划执行复盘",
                          "orders": plan_stats["order_count"],
                          "filled": plan_stats["filled_count"]})
            # 滚进执行质量历史：同一天重跑收盘时**替换**而不是追加，否则补跑
            # 一次，20 天样本里就有两天各算了两遍，成交率会被自己的重跑污染。
            hist = [r for r in (self.state.get("plan_history") or [])
                    if r.get("date") != today]
            hist.append({k: plan_stats.get(k) for k in
                         ("date", "order_count", "filled_count", "fill_rate",
                          "avg_adverse_bps")}
                        | {"seal_rate": (plan_stats.get("seal") or {}).get("rate")})
            health = _plan_health(hist)
            steps.append({"step": "执行质量", **{k: health[k] for k in
                                              ("days", "orders", "fill_rate",
                                               "avg_adverse_bps", "label")}})
            review_ctx = aiwatch.build_review_context(
                breadth=breadth, index=index, account=merged,
                trades=merged["trades"], plans=merged["plans"],
                as_of=today, plan_stats=plan_stats, holdings=holdings)
            review = aiwatch.analyze_review(review_ctx)
            steps.append({"step": "收盘复盘", "source": review.get("source")})

            # ---- 进化：每个账户各跑一轮，采纳与否由门槛决定
            evolved = []
            for sid in self._strategy_ids():
                got = lab_router.run_evolve_blocking(sid, apply=True)
                evolved.append({"strategy": sid, **{k: got.get(k) for k in
                                                    ("action", "reason", "edge", "applied")}})
            steps.append({"step": "进化", "result": evolved})

            entry = {"at": now.isoformat(timespec="seconds"),
                     "as_of": today, "text": review,
                     "market": review_ctx["市场"],
                     "account": review_ctx["账户"],
                     # 持仓逐票：今天买的那只收盘赚了还是亏了、明天怎么操作。
                     # 复盘只说「成交 2 笔」等于什么都没说，这一段才是能用的部分。
                     "holdings": holdings,
                     # 计划 vs 实际：挂单/成交/滑点/未成交原因，
                     # 前端在复盘面板里单独一张小表，按策略拆开
                     "plan": plan_stats}
            # 同一天补跑要替换而不是追加：否则补跑三次，「复盘日记」里
            # 同一天就有三篇，还都长得差不多。
            reviews = [r for r in (self.state.get("reviews") or [])
                       if r.get("as_of") != today]
            reviews.insert(0, entry)

            with self._lock:
                self.state["review"] = entry
                self.state["reviews"] = reviews[:REVIEW_KEEP_DAYS]
                self.state["plan_history"] = hist[-60:]
                self.state["plan_health"] = health
                self.state["eod"] = {"at": now.isoformat(timespec="seconds"),
                                     "date": today, "steps": steps, "evolved": evolved}
                self.state["last_eod_date"] = today
                self.state["eod_retry"] = {"date": None, "count": 0}
                self.state["eod_skip_at"] = None
                self.state["error"] = None
            self._save()
            return self.state["eod"]
        except Exception as exc:                         # noqa: BLE001
            retry = dict(self.state.get("eod_retry") or {})
            if retry.get("date") != today:
                retry = {"date": today, "count": 0}
            retry["count"] = int(retry.get("count") or 0) + 1
            exhausted = retry["count"] >= EOD_MAX_RETRY
            with self._lock:
                self.state["eod_retry"] = retry
                self.state["eod_skip_at"] = None
                self.state["error"] = f"{type(exc).__name__}: {exc}"
                if exhausted:
                    # 用尽自动重试后必须把 last_eod_date 补上，否则巡检会继续
                    # 每 30 秒硬撞一次；同时留一句话告诉用户该怎么手动补。
                    steps.append({
                        "step": "自动重试已用尽",
                        "reason": f"今天已连续失败 {retry['count']} 次，不再自动重试。"
                                  "处理完数据源问题后，点页面上的「生成复盘日记」手动跑一次"})
                    self.state["last_eod_date"] = today
                self.state["eod"] = {"at": now.isoformat(timespec="seconds"),
                                     "date": today, "steps": steps,
                                     "retry": retry["count"],
                                     "error": self.state["error"]}
            self._save()
            raise
        finally:
            with self._lock:
                self.state["running"] = None
            self._save()

    def reset_eod(self) -> dict:
        """清掉「今天已跑过收盘」的标记，用于手动重跑。"""
        with self._lock:
            self.state["last_eod_date"] = None
            # 手动补跑是用户明确要求的，重试计数跟着清零，否则下一次
            # 自动重试还没开始就已经被判成「用尽」。
            self.state["eod_retry"] = {"date": None, "count": 0}
            self.state["eod_skip_at"] = None
        self._save()
        return self.status()


_pilot: Autopilot | None = None


# 退出原因的展示顺序：先讲主动兑现的（计划减仓 / 止盈），再讲被动离场的
# （止损 / 时间止损）。按出现次数排会把「止损 22 次」顶到最前面，
# 复盘读起来像在宣布一天都在割肉，而实际动作结构是止盈为主。
_EXIT_ORDER = ("计划减仓", "未封板离场", "TP2 清仓", "TP1 减半", "保本止损", "止损", "时间止损")


def _sealed_today(mkt, code: str, today_s: str) -> bool:
    """今天收盘有没有封住涨停。弱转强的买点是否成立，收盘那一刻就有答案。"""
    rows = mkt.bars(code, "1d", "none", limit=3) or []
    if len(rows) < 2 or rows[-1]["date"] != today_s:
        return False
    prev = float(rows[-2]["close"] or 0)
    if prev <= 0:
        return False
    limit = price_limit(code, bool(mkt.symbol(code).get("is_st")))
    return float(rows[-1]["close"]) / prev - 1 >= limit - 0.002


def _plan_review(mkt, lab, advanced: dict[str, dict], prev_snap: dict[str, dict],
                 today_s: str) -> dict:
    """今日「计划 vs 实际」：挂了几张、成交几张、滑点多少、没成交为什么。

    用户关心的是「这套计划卡到底有没有在按说的执行」。策略赚不赚钱看回测，
    计划可不可信只看这个执行率——两者混在一起谈，等于用收益给执行质量背书。
    """
    totals = {"order_count": 0, "filled_count": 0}
    kinds: dict[str, dict] = {}
    rows: list[dict] = []
    missed: list[dict] = []
    by_strategy: list[dict] = []
    exits: dict[str, dict] = {}
    bps_all: list[float] = []
    # 弱转强族：今天出手几只、收盘封住几只。这是这条买点上唯一「当天收盘
    # 就能验证」的指标——止损、止盈都要等几天才知道答案，而它不用。
    # 封板率决定「必须当日封板否则次日走」那条硬规则该不该打开：
    # 1260 天样本上它是 16%，打开硬规则年化从 13.92% 掉到 −1.82%；
    # 强势期会明显回升，所以每天记一笔，等它稳定在高位再谈开关。
    seal = {"entries": 0, "sealed": 0, "codes": []}

    for sid, info in advanced.items():
        acc = lab.account(sid) or {}
        name = acc.get("name") or sid
        snap_prev = prev_snap.get(sid) or {}
        today_trades = [t for t in (acc.get("trades") or [])
                        if t.get("date") == today_s]
        got = paper.match_orders(snap_prev.get("orders") or [],
                                 snap_prev.get("plans") or [],
                                 today_trades, today_s,
                                 log=(info or {}).get("log") or [])
        totals["order_count"] += got["order_count"]
        totals["filled_count"] += got["filled_count"]
        for k in got["kinds"]:
            slot = kinds.setdefault(k["kind"], {"kind": k["kind"], "label": k["label"],
                                                "orders": 0, "filled": 0})
            slot["orders"] += k["orders"]
            slot["filled"] += k["filled"]
        for row in got["rows"]:
            rows.append({**row, "strategy_id": sid, "strategy_name": name})
            if row["adverse_bps"] is not None:
                bps_all.append(row["adverse_bps"])
        for m in got["missed"]:
            missed.append({**m, "strategy_name": name})

        # 今日已了结的交易按退出原因归堆：这才是「哪种动作真的在赚钱」
        closed = [t for t in today_trades if t.get("pnl") is not None]
        for t in closed:
            key = str(t.get("reason") or "其他").split("（")[0].strip() or "其他"
            slot = exits.setdefault(key, {"reason": key, "count": 0, "wins": 0,
                                          "pnl": 0.0, "pnl_pct_sum": 0.0})
            slot["count"] += 1
            slot["wins"] += 1 if float(t.get("pnl") or 0) > 0 else 0
            slot["pnl"] += float(t.get("pnl") or 0)
            slot["pnl_pct_sum"] += float(t.get("pnl_pct") or 0)

        if (acc.get("spec") or {}).get("gap_band"):
            for t in today_trades:
                if t.get("side") != "BUY":
                    continue
                seal["entries"] += 1
                if _sealed_today(mkt, t["code"], today_s):
                    seal["sealed"] += 1
                    seal["codes"].append(t["code"])

        by_strategy.append({
            "strategy_id": sid, "strategy_name": name,
            "orders": got["order_count"], "filled": got["filled_count"],
            "fill_rate": got["fill_rate"],
            "trades": len(today_trades),
            "realized_pnl": round(sum(float(t.get("pnl") or 0) for t in closed), 2),
        })

    exit_rows = []
    for key in _EXIT_ORDER + tuple(k for k in exits if k not in _EXIT_ORDER):
        slot = exits.get(key)
        if not slot:
            continue
        exit_rows.append({
            "reason": key, "count": slot["count"],
            "win_rate": round(slot["wins"] / slot["count"] * 100, 1),
            "pnl": round(slot["pnl"], 2),
            "avg_pnl_pct": round(slot["pnl_pct_sum"] / slot["count"], 2),
        })

    total, filled = totals["order_count"], totals["filled_count"]
    return {
        "date": today_s,
        "order_count": total,
        "filled_count": filled,
        "fill_rate": round(filled / total * 100, 1) if total else None,
        "avg_adverse_bps": round(sum(bps_all) / len(bps_all), 1) if bps_all else None,
        "seal": {
            "entries": seal["entries"],
            "sealed": seal["sealed"],
            "rate": (round(seal["sealed"] / seal["entries"] * 100, 1)
                     if seal["entries"] else None),
            "codes": seal["codes"][:8],
        },
        "kinds": list(kinds.values()),
        "rows": rows[:40],
        "missed": missed[:20],
        "by_exit": exit_rows,
        "by_strategy": by_strategy,
    }


def get_pilot() -> Autopilot:
    global _pilot
    if _pilot is None:
        _pilot = Autopilot()
    return _pilot
