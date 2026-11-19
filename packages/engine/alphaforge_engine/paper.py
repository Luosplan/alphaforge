"""模拟盘：每个策略一个独立账户，从今天起前向推进。

和回测的分工
------------
回测回答「这条策略在历史上是什么水平」，模拟盘回答「它现在真的在做什么」。
两者共用同一套区间规则（ATR 定标尺 + 三档了结），但**资金曲线不共享**：
回测用面板一次性重放，模拟盘只吃真实落库的下一根 K 线，一天一格。

为什么成交价取「次日开盘」
--------------------------
模拟盘在收盘后跑（日线要等收盘才落库），此时当日的开盘价早就过去了。
如果按当日收盘价成交，等于「收盘后拿到信号，还能按收盘价买到」——回测里
这叫未来函数，在模拟盘里叫自欺。所以流程是：

    第 T 日收盘 → 生成计划卡与待成交委托
    第 T+1 日开盘 → 按开盘价成交（一字涨停买不到，一字跌停卖不掉）
    第 T+1 日盘中 → ATR 止损 / 分档止盈 / 时间止损

程序停几天再打开也不会错位：`step()` 会按交易日历把落下的那几天逐格补齐，
而不是把三天的委托堆到同一天开盘成交。上限 30 格——再久就该重建账户，
而不是让一个半年前的委托在今天成交。
"""

from __future__ import annotations

from alphaforge_core.constants import RISK_LIMITS, normalize_profile, trade_fee

from .backtest import DEFAULTS, single_cap
from .selector import execute
from .signal import build_plan

LIVE_CAPITAL = 1_000_000.0
# 单票上限统一走 `backtest.single_cap(top_n)`：模拟盘和回测必须用同一个数，
# 否则同一笔委托在两个页面上是两个仓位。这里不再单独持有 15% 的常量。
MAX_GROSS = RISK_LIMITS["max_gross_exposure"]
MAX_CATCHUP = 30          # 一次最多补几个交易日

# 与进化基因组一一对应（`harness.BOUNDS` 的键）。`rebalance` 已经不在里面：
# 回测与模拟盘都改成「每天补空缺仓位」，调仓周期不再是可调项。
PARAM_KEYS = ("top_n", "max_per_industry", "stop_atr", "tp1_atr",
              "tp2_atr", "time_stop")

# 计划卡的「委托」状态。这三句话回答的是同一个问题：这张卡到底有没有真挂单。
# 不用 `live_status` 顶替——那是「现价落在建仓区哪一段」，当日新卡恒为区间内，
# 跟买没买着没有关系。
ORDER_WAITING = "待明日开盘成交"
ORDER_HELD = "已持有 · 加仓不自动执行"
ORDER_NOSLOT = "仅备选 · 仓位已满"
# 持仓数已经超过 `top_n` 时不能再叫「仓位已满」：进化允许把 top_n 往下调
# （只能变集中、不能变多），调小之后账户里可能还留着更多票。这时限的是
# **只数**不是资金，写成「仓位已满」会让用户以为钱不够，实际是等已有票退出。
# 行为上和 NOSLOT 一样不挂单——账户只减不增，直到持仓数回到上限以内。
ORDER_OVER = "已超持仓上限 · 只减不增"
ORDER_REGIME = "择时关闭 · 大盘在均线下方"


def empty_account(strategy_id: str, name: str, spec: dict,
                  params: dict | None = None,
                  capital: float = LIVE_CAPITAL) -> dict:
    """开一个新的空账户。`spec` 是当前生效的策略（进化之后会被替换）。

    结构参数的优先级是 spec → params：策略自己写了 top_n / 每行业上限就先照它来，
    再让调用方显式传的 params 覆盖。`harness.base_genome` 取进化起点时也是
    `spec.get("top_n") or DEFAULTS[...]`，回测侧同口径——三处必须读同一套数，
    否则同一条策略在抽屉里会并排放着两个用不同 top_n 跑出来的数字。
    """
    base = {k: DEFAULTS[k] for k in PARAM_KEYS}
    # 撮合/风控开关不进基因组（PARAM_KEYS 只放遗传搜索要动的东西），但要
    # 跟着账户一起落盘，否则模拟盘读不到、和回测口径分叉。
    for key in ("market_ma", "breakeven_after_tp1"):
        value = spec.get(key)
        base[key] = DEFAULTS[key] if value is None else value
    for key in PARAM_KEYS:
        value = spec.get(key)
        if value is not None:
            base[key] = value
    return {
        "id": strategy_id,
        "name": name,
        "strategy_id": strategy_id,
        "spec": spec,
        "params": {**base, **(params or {})},
        "capital": round(float(capital), 2),
        "cash": round(float(capital), 2),
        "positions": [],
        "orders": [],
        "trades": [],
        "equity": [],
        "plans": [],
        "last_run": None,
        "generation": 0,
        "started_at": None,
        "log": [],
    }


class _Feed:
    """按代码缓存最近若干根不复权日线。

    用不复权价记账：模拟盘是「真的买了多少股、每股多少钱」，
    前复权价只是画图和算因子的中间量，拿它当成交价会让成本价对不上交割。
    """

    def __init__(self, engine, limit: int = 90):
        self.engine = engine
        self.limit = limit
        self._cache: dict[str, list[dict]] = {}

    def rows(self, code: str) -> list[dict]:
        if code not in self._cache:
            try:
                self._cache[code] = self.engine.bars(code, "1d", "none",
                                                     limit=self.limit) or []
            except Exception:                            # noqa: BLE001
                self._cache[code] = []
        return self._cache[code]

    def bar(self, code: str, date_s: str) -> tuple[dict | None, dict | None]:
        """返回 (当日 K 线, 上一根 K 线)。当日停牌 → (None, None)。"""
        rows = self.rows(code)
        for i in range(len(rows) - 1, -1, -1):
            if rows[i]["date"] == date_s:
                return rows[i], (rows[i - 1] if i else None)
        return None, None

    def last_close(self, code: str, fallback: float = 0.0) -> float:
        rows = self.rows(code)
        return float(rows[-1]["close"]) if rows else fallback


# ------------------------------------------------------------------ 一步
def step(engine, account: dict, profile: dict | None = None,
         *, force: bool = False) -> tuple[dict, dict]:
    """把账户推到最新交易日。返回 (新账户, 本次动作摘要)。"""
    today = engine.trade_date
    if today is None:
        return account, {"skipped": True, "reason": "本地库还没有交易日数据"}
    today_s = today.isoformat()
    last = account.get("last_run")

    if not force and last == today_s:
        return account, {"skipped": True,
                         "reason": f"{today_s} 已推进过，模拟盘一天只前进一天"}
    if last and today_s < last:
        return account, {"skipped": True, "reason": "本地库交易日早于模拟盘进度"}

    calendar = [d.isoformat() for d in engine.dates]
    prof = normalize_profile(None, profile)
    feed = _Feed(engine)

    # force 且今天已经推进过：只重算计划卡与委托。
    # 不能再走一遍 _advance —— 那会把今天的止损/止盈按同一根 K 线再执行一次，
    # 持仓被卖两遍。换参数之后想看新计划卡，走这条路径。
    if last == today_s and force:
        account, info = _refresh_plans(engine, feed, account, prof, today_s)
        return account, {"skipped": False, "refreshed": True, "steps": [info],
                         "dates": [today_s], "nav": info["nav"]}

    todo = [today_s] if not last else [d for d in calendar if d > last]
    if not todo:
        return account, {"skipped": True, "reason": "没有新的交易日"}
    todo = todo[-MAX_CATCHUP:]

    steps: list[dict] = []
    for date_s in todo:
        account, info = _advance(engine, feed, account, prof, date_s)
        steps.append(info)
    return account, {"skipped": False, "steps": steps, "dates": todo,
                     "nav": steps[-1]["nav"] if steps else None}


def _refresh_plans(engine, feed: _Feed, account: dict, prof: dict,
                   date_s: str) -> tuple[dict, dict]:
    """按当日收盘重算计划卡与明日委托，不重复撮合。"""
    params = {**DEFAULTS, **(account.get("params") or {})}
    positions = [dict(p) for p in account.get("positions") or []]
    cash = float(account.get("cash") or 0.0)
    nav = cash + _market_value(positions, feed)
    spec = account.get("spec") or {"id": account["id"], "name": account.get("name", "")}
    plans, orders = _next_plans(engine, feed, spec, params, positions, nav, date_s,
                                regime_ok=_regime_allows(engine, date_s, params))
    equity = list(account.get("equity") or [])
    if equity:
        equity[-1] = {"date": date_s, "value": round(nav, 2)}
    updated = {**account, "plans": plans, "orders": orders, "equity": equity,
               "cash": round(cash, 2), "positions": positions}
    return updated, {
        "date": date_s, "nav": round(nav, 2), "log": [],
        "positions": len(positions), "orders": len(orders),
    }


def _advance(engine, feed: _Feed, account: dict, prof: dict, date_s: str) -> tuple[dict, dict]:
    params = {**DEFAULTS, **(account.get("params") or {})}
    positions = [dict(p) for p in account.get("positions") or []]
    trades = list(account.get("trades") or [])
    cash = float(account.get("cash") or 0.0)
    log: list[str] = []
    seq = len(trades)

    def sell(pos: dict, price: float, reason: str, qty: int | None = None) -> None:
        nonlocal cash, seq
        shares = pos["shares"] if qty is None else min(qty, pos["shares"])
        if shares <= 0 or price <= 0:
            return
        amount = shares * price
        fee = trade_fee(amount, "SELL", prof)
        cash += amount - fee["total"]
        pnl = (price - pos["cost"]) * shares
        seq += 1
        trades.append({
            "id": f"P{seq:05d}", "code": pos["code"], "name": pos.get("name", ""),
            "side": "SELL", "date": date_s, "price": round(price, 3),
            "shares": shares, "amount": round(amount, 2), "fee": fee["total"],
            "tax": fee["stamp"], "pnl": round(pnl, 2),
            "pnl_pct": round((price / pos["cost"] - 1) * 100, 2) if pos["cost"] else 0.0,
            "reason": reason,
        })
        pos["shares"] -= shares
        log.append(f"卖出 {pos['code']} {shares} 股 @{price:.2f}（{reason}）")

    # ---- 1. 盘中：止损 / 分档止盈 / 时间止损（止损优先，按坏的算）
    for pos in list(positions):
        bar, prev = feed.bar(pos["code"], date_s)
        if not bar:
            continue                                   # 停牌：什么都不做
        o, hi, lo = float(bar["open"]), float(bar["high"]), float(bar["low"])
        prev_close = float(prev["close"]) if prev else o
        # 一字跌停：全天只有一个价，卖不掉
        stuck_down = prev_close > 0 and o <= prev_close * 0.9001 and hi <= o * 1.001
        if o > 0:
            pos["last"] = float(bar["close"])

        if lo <= pos["stop"] and not stuck_down:
            reason = ("保本止损" if pos.get("tp1_done") and pos["stop"] >= pos["cost"]
                      else "止损")
            sell(pos, o if o < pos["stop"] else pos["stop"], reason)
            continue
        if not pos.get("tp1_done") and hi >= pos["tp1"]:
            half = max(int(pos["shares"] * 0.5 / 100) * 100, 100)
            sell(pos, o if o >= pos["tp1"] else pos["tp1"], "TP1 减半", qty=half)
            pos["tp1_done"] = True
            # 与回测同口径：TP1 之后剩余仓位止损上移到成本价，见 backtest.py 注释
            pos["stop"] = max(pos["stop"], pos["cost"])
            continue
        if pos.get("tp1_done") and hi >= pos["tp2"]:
            sell(pos, o if o >= pos["tp2"] else pos["tp2"], "TP2 清仓")
            continue
        held = _held_days(engine, pos.get("open_date") or date_s, date_s)
        if held >= int(params["time_stop"]):
            sell(pos, o, f"时间止损（持有 {held} 日）")

    positions = [p for p in positions if p["shares"] > 0]

    # ---- 2. 开盘：成交上一交易日挂出的委托
    orders = list(account.get("orders") or [])
    held_codes = {p["code"] for p in positions}
    nav_open = cash + _market_value(positions, feed)
    for order in orders:
        code = order["code"]
        if code in held_codes:
            continue
        bar, prev = feed.bar(code, date_s)
        if not bar:
            continue
        o = float(bar["open"])
        hi = float(bar["high"])
        prev_close = float(prev["close"]) if prev else o
        if prev_close > 0 and o >= prev_close * 1.0999 and hi <= o * 1.001:
            log.append(f"跳过 {code}：开盘一字涨停，买不到")
            continue
        if o <= 0:
            continue
        weight = min(1.0 / int(params["top_n"]), single_cap(int(params["top_n"])))
        grep = (nav_open - cash) / nav_open if nav_open else 1.0
        if grep > MAX_GROSS:
            log.append("总仓位已到上限，本次委托不执行")
            continue
        budget = min(nav_open * weight, cash)
        shares = int(budget / o / 100) * 100
        while shares >= 100:
            amount = shares * o
            fee = trade_fee(amount, "BUY", prof)
            if amount + fee["total"] <= cash:
                break
            shares -= 100
        if shares < 100:
            continue
        amount = shares * o
        fee = trade_fee(amount, "BUY", prof)
        cash -= amount + fee["total"]
        atr = float(order.get("atr") or o * 0.02)
        positions.append({
            "code": code, "name": order.get("name", ""), "shares": shares,
            "cost": round(o, 4), "open_date": date_s,
            "stop": round(o - float(params["stop_atr"]) * atr, 3),
            "tp1": round(o + float(params["tp1_atr"]) * atr, 3),
            "tp2": round(o + float(params["tp2_atr"]) * atr, 3),
            "tp1_done": False, "last": o,
        })
        held_codes.add(code)
        seq += 1
        trades.append({
            "id": f"P{seq:05d}", "code": code, "name": order.get("name", ""),
            "side": "BUY", "date": date_s, "price": round(o, 3),
            "shares": shares, "amount": round(amount, 2), "fee": fee["total"],
            "tax": 0.0, "pnl": None, "pnl_pct": None,
            "reason": order.get("reason", "计划建仓"),
        })
        log.append(f"买入 {code} {shares} 股 @{o:.2f}（{order.get('reason', '计划建仓')}）")

    # ---- 3. 收盘：按今日选股生成计划卡与明日委托
    nav = cash + _market_value(positions, feed)
    spec = account.get("spec") or {"id": account["id"], "name": account.get("name", "")}
    plans, new_orders = _next_plans(engine, feed, spec, params, positions, nav, date_s,
                                    regime_ok=_regime_allows(engine, date_s, params))

    # ---- 4. 记账
    equity = list(account.get("equity") or [])
    if not equity or equity[-1]["date"] != date_s:
        equity.append({"date": date_s, "value": round(nav, 2)})

    updated = {
        **account,
        "cash": round(cash, 2),
        "positions": positions,
        "orders": new_orders,
        "trades": trades,
        "equity": equity[-400:],
        "plans": plans,
        "last_run": date_s,
        "started_at": account.get("started_at") or date_s,
        "log": (log or []) + list(account.get("log") or []),
    }
    updated["log"] = updated["log"][:60]
    return updated, {
        "date": date_s, "nav": round(nav, 2), "log": log,
        "positions": len(positions), "orders": len(new_orders),
    }


def _next_plans(engine, feed: _Feed, spec: dict, params: dict,
                positions: list[dict], nav: float,
                date_s: str, regime_ok: bool = True) -> tuple[list[dict], list[dict]]:
    """按今日收盘选股 → 计划卡 + 明日委托。

    `regime_ok=False`（大盘在均线下方）时仍然出卡——用户需要知道「今天不买是
    因为大盘」，而不是打开页面发现一片空白——但一张委托都不挂。
    """
    try:
        selection = execute(engine, spec, top_n=int(params["top_n"]))
    except Exception:                                   # noqa: BLE001 - 选股失败不该拖垮记账
        return [], []
    items = selection.get("items") or []
    if not items:
        return [], []

    held = {p["code"] for p in positions}
    slots = max(int(params["top_n"]) - len(held), 0)
    plans: list[dict] = []
    orders: list[dict] = []
    for item in items:
        if len(plans) >= int(params["top_n"]):
            break
        code = item["code"]
        signal_type = "ADD" if code in held else "ENTRY"
        try:
            plan = build_plan(engine, code, nav=nav,
                              strategy_id=selection.get("strategy_id") or "custom",
                              strategy_name=selection.get("strategy_name"),
                              signal_type=signal_type, salt=signal_type)
        except Exception:                               # noqa: BLE001
            continue
        entry_low = plan["zones"]["entry"]["low"]
        stop_high = plan["zones"]["stop"]["high"]
        # build_plan 的止损上沿 = 建仓下沿 − 1.15×ATR，这里反解出 ATR，
        # 免得为了一个数再查一次日线
        atr = max((entry_low - stop_high) / 1.15, entry_low * 0.005)
        plan["atr"] = round(atr, 4)
        plan["plan_date"] = date_s
        plans.append(plan)
        # 委托状态：这张卡到底有没有真的挂单，比「现价落在区间内」有用得多
        if not regime_ok:
            plan["order_state"] = ORDER_REGIME
            plan["order_tone"] = "flat"
            continue
        if code in held:
            plan["order_state"] = ORDER_HELD
            plan["order_tone"] = "flat"
            continue
        if slots <= 0:
            plan["order_state"] = ORDER_NOSLOT if len(held) <= int(params["top_n"]) else ORDER_OVER
            plan["order_tone"] = "flat"
            continue
        plan["order_state"] = ORDER_WAITING
        plan["order_tone"] = "brand"
        orders.append({"code": code, "name": item["name"], "reason": "计划建仓",
                       "created": date_s, "atr": round(atr, 4),
                       "score": item.get("score")})

        slots -= 1

    _mark_plans(plans, feed, date_s)
    return plans, orders


def _mark_plans(plans: list[dict], feed: _Feed, date_s: str) -> None:
    """给计划卡标上「今天走到哪一步」。

    注意 `build_plan` 的建仓区是**以参考价为中心**生成的
    （下沿 ref − 0.55ATR，上沿 ref + 0.15ATR），所以当日新生成的卡里，
    现价必然落在建仓区内——`live_status` 只能当扫描用的色块，不能当作
    「该不该动手」的判断。

    前端因此以 `order_state`（有没有真的挂委托）为主，`live_status` 只在
    「停牌 / 已跌破止损区」这两种真正需要停下来看的情况下才顶到前面。
    真正逐票变化的风险指标是 `stop_distance_pct`：距止损还有几个点。
    """
    for plan in plans:
        bar, _ = feed.bar(plan["code"], date_s)
        if not bar:
            plan["live_status"] = "停牌"
            plan["live_price"] = None
            plan["stop_distance_pct"] = None
            continue
        price = float(bar["close"])
        stop = plan["zones"]["stop"]["high"]
        plan["live_price"] = round(price, 2)
        plan["stop_distance_pct"] = round((stop - price) / price * 100, 2) if price else None
        entry = plan["zones"]["entry"]
        if price <= stop:
            plan["live_status"] = "已跌破止损区"
        elif price < entry["low"]:
            plan["live_status"] = "等待回踩建仓区"
        elif price <= entry["high"]:
            plan["live_status"] = "已进入建仓区"
        else:
            plan["live_status"] = "已冲高，等回调"


def _held_days(engine, open_date: str, date_s: str) -> int:
    return sum(1 for d in engine.dates if open_date < d.isoformat() <= date_s)


def _regime_allows(engine, date_s: str, params: dict) -> bool:
    """今天该不该开新仓：沪深300 是否在 `market_ma` 日线之上。

    与回测 `backtest._regime_ok` 同一口径。`market_ma=0`（默认）时永远返回 True，
    保持历史行为；取不到指数数据也返回 True——宁可照常交易，也不因为缺数据
    让模拟盘静默停摆。
    """
    ma = int(params.get("market_ma") or 0)
    if ma <= 0:
        return True
    try:
        rows = engine.repo.index_daily("000300.SH", ma + 40)
    except Exception:                                    # noqa: BLE001
        return True
    hist = [(b["date"], float(b["close"])) for b in (rows or []) if b.get("close")]
    hist = [r for r in hist if r[0] <= date_s]
    if len(hist) < ma:
        return True
    closes = [c for _, c in hist[-ma:]]
    return closes[-1] >= sum(closes) / len(closes)


def ensure_order_state(plans: list[dict], positions: list[dict],
                        params: dict) -> list[dict]:
    """给还没有 `order_state` 的旧计划卡补一次。

    页面读的是落盘的账户，老数据里没这个字段；不补的话状态列会退回
    `live_status`，用户看到的还是那句话——要等下一次「推进模拟盘」才变。
    同一天的 plan 与 order 是一起生成的、成交发生在下一交易日，所以事后
    按「当前持仓 + 剩余槽位」重推一遍，结果与当时挂单的事实一致。
    """
    if not plans or all("order_state" in p for p in plans):
        return plans
    held = {p["code"] for p in positions}
    cap = int(params.get("top_n") or 0)
    slots = max(cap - len(held), 0)
    for plan in plans:
        if "order_state" in plan:
            continue
        if plan["code"] in held:
            plan["order_state"] = ORDER_HELD
            plan["order_tone"] = "flat"
        elif slots <= 0:
            plan["order_state"] = ORDER_NOSLOT if len(held) <= cap else ORDER_OVER
            plan["order_tone"] = "flat"
        else:
            plan["order_state"] = ORDER_WAITING
            plan["order_tone"] = "brand"
            slots -= 1
    return plans


def _market_value(positions: list[dict], feed: _Feed) -> float:
    value = 0.0
    for pos in positions:
        price = feed.last_close(pos["code"], float(pos.get("last") or pos["cost"]))
        pos["last"] = price
        value += pos["shares"] * price
    return value


# ---------------------------------------------------------------- 账户视图
def snapshot(engine, account: dict) -> dict:
    """给前端的账户视图：净值拆解、持仓浮盈、成交统计。"""
    today_s = engine.trade_date.isoformat() if engine.trade_date else ""
    feed = _Feed(engine, limit=4)
    positions = []
    market_value = 0.0
    cash = float(account.get("cash") or 0.0)
    for pos in account.get("positions") or []:
        rows = feed.rows(pos["code"])
        price = float(rows[-1]["close"]) if rows else float(pos.get("last") or pos["cost"])
        prev = float(rows[-2]["close"]) if len(rows) > 1 else price
        mv = price * pos["shares"]
        market_value += mv
        positions.append({
            **{k: pos[k] for k in ("code", "shares", "cost", "open_date",
                                   "stop", "tp1", "tp2") if k in pos},
            "name": pos.get("name", ""),
            "price": round(price, 2),
            "change_pct": round((price / prev - 1) * 100, 2) if prev else 0.0,
            "market_value": round(mv, 2),
            "pnl": round((price - pos["cost"]) * pos["shares"], 2),
            "pnl_pct": round((price / pos["cost"] - 1) * 100, 2) if pos["cost"] else 0.0,
            "tp1_done": bool(pos.get("tp1_done")),
        })
    positions.sort(key=lambda r: r["market_value"], reverse=True)

    nav = market_value + cash
    capital = float(account.get("capital") or LIVE_CAPITAL)
    trades = account.get("trades") or []
    closed = [t for t in trades if t["side"] == "SELL" and t.get("pnl") is not None]
    wins = [t for t in closed if t["pnl"] > 0]
    stats = {
        "trade_count": len(trades),
        "closed_count": len(closed),
        "win_rate": round(len(wins) / len(closed) * 100, 2) if closed else 0.0,
        "realized_pnl": round(sum(t["pnl"] for t in closed), 2),
        "total_fee": round(sum(t["fee"] for t in trades), 2),
    }

    return {
        "id": account.get("id"),
        "name": account.get("name"),
        "strategy_id": account.get("strategy_id"),
        "generation": account.get("generation", 0),
        "params": account.get("params"),
        "last_run": account.get("last_run"),
        "started_at": account.get("started_at"),
        "is_stale": bool(account.get("last_run")) and account.get("last_run") != today_s,
        "summary": {
            "nav": round(nav, 2),
            "capital": round(capital, 2),
            "cash": round(cash, 2),
            "market_value": round(market_value, 2),
            "pnl": round(nav - capital, 2),
            "pnl_pct": round((nav / capital - 1) * 100, 2) if capital else 0.0,
            "position_ratio": round(market_value / nav * 100, 2) if nav else 0.0,
            "holdings_count": len(positions),
        },
        "statistics": stats,
        "positions": positions,
        "orders": account.get("orders") or [],
        "plans": ensure_order_state(account.get("plans") or [],
                                     account.get("positions") or [],
                                     account.get("params") or {}),
        "equity": (account.get("equity") or [])[-260:],
        "trades": trades[-80:],
        "log": account.get("log") or [],
    }
