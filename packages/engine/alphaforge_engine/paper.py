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
from alphaforge_core.universe import pc as price_limit

from .backtest import (DEFAULTS, GAP_MAX_PCT, GAP_MIN_PCT, REGIME_HUDDLE_MAX_RANK,
                       REGIME_HUDDLE_MIN_LIMITS, REGIME_SCALE, TRIM_FRAC, single_cap)
from .gate import blocked_label, board_allow, buyable
from .selector import execute
from .signal import TRIM_PNL_PCT, build_plan

LIVE_CAPITAL = 1_000_000.0
# 开盘那笔的价格是集合竞价出的开盘价，成交时刻就是 09:30。日线给不出分钟级的
# 盘中时刻，止损 / 止盈按触发价成交的那种只能记空，页面上显示「—」。
OPEN_TIME = "09:30"
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
ORDER_HELD = "已持有 · 不动"
ORDER_ADD = "待明日开盘加仓"
ORDER_TRIM = "待明日开盘减仓"
ORDER_NOSLOT = "仅备选 · 仓位已满"
# 持仓数已经超过 `top_n` 时不能再叫「仓位已满」：进化允许把 top_n 往下调
# （只能变集中、不能变多），调小之后账户里可能还留着更多票。这时限的是
# **只数**不是资金，写成「仓位已满」会让用户以为钱不够，实际是等已有票退出。
# 行为上和 NOSLOT 一样不挂单——账户只减不增，直到持仓数回到上限以内。
ORDER_OVER = "已超持仓上限 · 只减不增"
# 弱势（沪深300 在半年线下方）时的委托状态。这一版**不再停手**：
# 只买行业里正在抱团的那几只，且每笔预算乘 `REGIME_SCALE`（0.5）。
# 上一版是「一张都不挂」，依据是 5 年样本上「弱势停手」比「弱势半仓」回撤小；
# 但那条结论针对的是**无差别半仓**。实测 2026-08-17 跌破半年线之后连续三个月
# 零成交、三个账户净值恒等于初始资金——自进化闭环第三环直接空转。
# 现在把「该不该参与」交给行业强度判断（`ind_limit_cnt` / `ind_rank_pct`），
# 而不是交给指数位置一刀切。
ORDER_WEAK = "弱势减半 · 明日开盘建仓"
# 弱势时行业没有抱团迹象 → 这张卡只是观察位。文案要写明「因为行业不抱团」，
# 不能笼统写「择时关闭」——后者会让人以为系统停摆了。
ORDER_NO_HUDDLE = "弱势 · 行业未抱团，不建仓"
# 弱势停手：只有声明了 `weak_participate` 的策略才会走到这一支，其余策略
# 在弱势期本该一张不挂（放开实测年化、回撤都明显变差，见 selector 注释）。
# 文案不能写「择时关闭」——那是全局开关的语气，看起来像整个系统停了；
# 这里是**这条策略**主动不做，另外一条（板块联动首日）照样在打抱团。
ORDER_REGIME = "弱势停手 · 该策略在弱市无 edge"

# 减仓一次卖多少（1/3）与「浮盈多少算该减」（`TRIM_PNL_PCT`）都从上层取：
# 回测和模拟盘必须用同一对数，否则同一只票在实验室里减仓、在回测里继续拿着。
# 见 `backtest.TRIM_FRAC` / `signal.TRIM_PNL_PCT` 的注释。


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
    # 弱市做不做是**策略的性质**，不在 DEFAULTS 里（它不该被当成全局默认值，
    # 更不该进遗传搜索）。缺省 False = 停手，宁可保守。
    base["weak_participate"] = bool(spec.get("weak_participate", False))
    # 开盘跳空闸同理：抓动量的策略不该被高开拦下，缺省开。
    base["gap_gate"] = bool(spec.get("gap_gate", True))
    # 弱转强的两条买点属性：必须高开的区间，和「当日没封板次日走」的验证条件。
    # 它们描述的是「这类买点长什么样」，不是可以搜的参数，所以不进 PARAM_KEYS。
    base["gap_band"] = list(spec["gap_band"]) if spec.get("gap_band") else None
    base["need_limit_close"] = bool(spec.get("need_limit_close"))
    base["limit_close_frac"] = float(spec.get("limit_close_frac") or 1.0)
    # 打板不加仓（见 `backtest.run_backtest` 里的同一条）。波段照常。
    base["allow_add"] = bool(spec.get("allow_add", True))
    # 弱市要不要再要求行业抱团。缺省 True（板块联动型策略的口径），
    # 波段显式声明 False——它靠个股动量，不靠题材。
    base["weak_huddle"] = bool(spec.get("weak_huddle", True))
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
                                regime_scale=_regime_scale(engine, date_s, params),
                                boards=prof.get("boards"))
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
    # `weak_participate` / `gap_gate` / `gap_band` / `need_limit_close` 都是
    # 策略性质，v10 之后才陆续加进 params，老账户落盘的那份里没有。不补的话，
    # 老账户会一直按旧口径跑，而同一条策略新建的账户行为不一样——同一个页面上
    # 并排两个数，谁也不知道差在哪。
    saved = account.get("params") or {}
    spec_raw = account.get("spec") or {}
    for key, default in (("weak_participate", False), ("gap_gate", True),
                         ("gap_band", None), ("need_limit_close", False),
                         ("limit_close_frac", 1.0), ("allow_add", True),
                         ("weak_huddle", True)):
        if key not in saved:
            value = spec_raw.get(key)
            params[key] = default if value is None else value
    positions = [dict(p) for p in account.get("positions") or []]
    trades = list(account.get("trades") or [])
    cash = float(account.get("cash") or 0.0)
    allow = board_allow(prof.get("boards"))
    log: list[str] = []
    seq = len(trades)

    def sell(pos: dict, price: float, reason: str, qty: int | None = None,
             when: str = "") -> None:
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
            "side": "SELL", "date": date_s, "time": when, "price": round(price, 3),
            "shares": shares, "amount": round(amount, 2), "fee": fee["total"],
            "tax": fee["stamp"], "pnl": round(pnl, 2),
            "pnl_pct": round((price / pos["cost"] - 1) * 100, 2) if pos["cost"] else 0.0,
            "reason": reason,
        })
        pos["shares"] -= shares
        log.append(f"卖出 {pos['code']} {shares} 股 @{price:.2f}（{reason}）")

    # ---- 1. 盘中：止损 / 分档止盈 / 时间止损（止损优先，按坏的算）
    #
    # 这一步跑在「撮合」之前，所以天然满足 T+1：这里遍历的是**昨天及更早**
    # 建立的仓位，今天开盘才建的仓要到第 2 步才进 `positions`，今天就碰不到。
    # 顺序不能为了「先买后判」好看而对调——对调之后，早上买进、下午打穿止损
    # 离场这种实盘挂不出卖单的事就会在模拟盘里成立（回测踩过，v11 已修）。
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
            sell(pos, o if o < pos["stop"] else pos["stop"], reason,
                 when=OPEN_TIME if o < pos["stop"] else "")
            continue
        if not pos.get("tp1_done") and hi >= pos["tp1"]:
            half = max(int(pos["shares"] * 0.5 / 100) * 100, 100)
            sell(pos, o if o >= pos["tp1"] else pos["tp1"], "TP1 减半", qty=half,
                 when=OPEN_TIME if o >= pos["tp1"] else "")
            pos["tp1_done"] = True
            # 与回测同口径：TP1 之后剩余仓位止损上移到成本价，见 backtest.py 注释
            pos["stop"] = max(pos["stop"], pos["cost"])
            continue
        if pos.get("tp1_done") and hi >= pos["tp2"]:
            sell(pos, o if o >= pos["tp2"] else pos["tp2"], "TP2 清仓",
                 when=OPEN_TIME if o >= pos["tp2"] else "")
            continue
        held = _held_days(engine, pos.get("open_date") or date_s, date_s)
        if held >= int(params["time_stop"]):
            sell(pos, o, f"时间止损（持有 {held} 日）", when=OPEN_TIME)

    positions = [p for p in positions if p["shares"] > 0]

    # ---- 2. 开盘：成交上一交易日挂出的委托
    # 减仓排在最前面：同一天既有减仓又有加仓时，先卖出再买，否则「现金不够」
    # 会让本来能成交的加仓单被跳过（卖出的钱就在同一次开盘里）。
    orders = list(account.get("orders") or [])
    held_codes = {p["code"] for p in positions}
    pos_by_code = {p["code"]: p for p in positions}
    nav_open = cash + _market_value(positions, feed)
    for order in sorted(orders, key=lambda o: 0 if o.get("kind") == "TRIM" else 1):
        code = order["code"]
        kind = order.get("kind") or "ENTRY"
        if kind == "ENTRY" and code in held_codes:
            continue
        # 购买权限：委托可能是「权限还没关」或「旧版本遗留」时挂上的，
        # 撮合这一步必须再拦一道——否则夜里改了权限，明早照样成交。
        # 减仓不拦：卖出不需要购买权限，拦了只会把仓位锁死。
        if kind != "TRIM" and not buyable(code, allow):
            log.append(f"跳过 {code}：{blocked_label(code)}未开通购买权限，委托作废")
            continue
        bar, prev = feed.bar(code, date_s)
        if not bar:
            continue
        o = float(bar["open"])
        hi = float(bar["high"])
        prev_close = float(prev["close"]) if prev else o
        if o <= 0:
            continue

        if kind == "TRIM":
            pos = pos_by_code.get(code)
            if not pos or pos["shares"] <= 0:
                continue
            # 一字跌停卖不掉，等下一格再说（和止损那一侧同一判断）
            if prev_close > 0 and o <= prev_close * 0.9001 and hi <= o * 1.001:
                log.append(f"跳过 {code}：一字跌停，减仓卖不掉")
                continue
            qty = min(int(order.get("shares") or 0), pos["shares"])
            if qty < 100:
                continue
            sell(pos, o, order.get("reason") or "计划减仓", qty=qty, when=OPEN_TIME)
            pos["trimmed"] = True
            # 不再补一条「减仓 …」：`sell()` 已经写过「卖出 …」，两条连着出现
            # 会让人以为卖了两笔（实测日志里就是这样，一对就多出一笔）。
            if pos["shares"] <= 0:
                positions.remove(pos)
                pos_by_code.pop(code, None)
                held_codes.discard(code)
            continue

        # 一字涨停买不到（加仓同样买不到）
        if prev_close > 0 and o >= prev_close * 1.0999 and hi <= o * 1.001:
            log.append(f"跳过 {code}：开盘一字涨停，买不到")
            continue
        # 开盘校验：开盘价就是 9:25 集合竞价的撮合结果，跳空幅度决定「还值不值得买」。
        # 昨天收盘做的计划，到今天开盘可能已经不成立了：
        #   高开太多 → 情绪已经透支，追进去就是给别人抬轿；
        #   低开太多 → 跳空破位，说明昨天的信号已被证伪。
        # `gap_gate=False` 的策略（趋势波段）跳过这一段：它赚的是动量延续，
        # 高开就是确认。和回测 `_buy_block` 同一组常量，两边不会各说各话。
        gap_pct = (o / prev_close - 1) * 100 if prev_close > 0 else 0.0
        band = params.get("gap_band")
        if band:
            # 弱转强：高开是买点的组成部分，不是风控。区间之外分两种性质——
            # 高开不足说明昨天的弱势没被修复（信号没成立），高开过头已经
            # 是一字板或接近一字（买不到也没溢价），复盘时要分开统计。
            lo, hi = float(band[0]), float(band[1])
            if gap_pct < lo:
                log.append(f"跳过 {code}：高开 {gap_pct:+.1f}%，未达 {lo:g}%，弱势没被修复")
                continue
            if gap_pct > hi:
                log.append(f"跳过 {code}：高开 {gap_pct:+.1f}%，越过 {hi:g}%，情绪已透支")
                continue
        elif params.get("gap_gate", True):
            if gap_pct > GAP_MAX_PCT:
                log.append(f"跳过 {code}：高开 {gap_pct:.1f}%，越过追高上限 {GAP_MAX_PCT:g}%")
                continue
            if gap_pct < GAP_MIN_PCT:
                log.append(f"跳过 {code}：低开 {gap_pct:.1f}%，跳空破位（信号已证伪）")
                continue
        if kind == "ADD":
            pos = pos_by_code.get(code)
            if not pos:
                continue
            # 加仓不改止损/止盈：那两条是按最初的风险位定的，跟着均价动会让
            # 一次加仓把整笔交易的 R 悄悄放大。
            cap_value = nav_open * RISK_LIMITS["max_single_weight"]
            headroom = cap_value - pos["shares"] * o
            want = int(order.get("shares") or 0)
            budget = min(headroom, cash)
            qty = min(want, int(budget / o / 100) * 100)
            while qty >= 100:
                amount = qty * o
                fee = trade_fee(amount, "BUY", prof)
                if amount + fee["total"] <= cash:
                    break
                qty -= 100
            if qty < 100:
                log.append(f"跳过 {code}：现金或单票上限不够，加仓不执行")
                continue
            amount = qty * o
            fee = trade_fee(amount, "BUY", prof)
            cash -= amount + fee["total"]
            total = pos["shares"] + qty
            pos["cost"] = round((pos["cost"] * pos["shares"] + o * qty) / total, 4)
            pos["shares"] = total
            pos["last"] = o
            seq += 1
            trades.append({
                "id": f"P{seq:05d}", "code": code, "name": order.get("name", ""),
                "side": "BUY", "date": date_s, "time": OPEN_TIME, "price": round(o, 3),
                "shares": qty, "amount": round(amount, 2), "fee": fee["total"],
                "tax": 0.0, "pnl": None, "pnl_pct": None,
                "reason": order.get("reason", "计划加仓"),
            })
            log.append(f"加仓 {code} {qty} 股 @{o:.2f}（{order.get('reason', '计划加仓')}）")
            continue

        weight = min(1.0 / int(params["top_n"]), single_cap(int(params["top_n"])))
        # 择时降仓：弱市里每笔预算折半，而不是一张都不买。
        grep = (nav_open - cash) / nav_open if nav_open else 1.0
        if grep > MAX_GROSS:
            log.append("总仓位已到上限，本次委托不执行")
            continue
        # 同 backtest：`or 1.0` 会把 0 当成缺省值，择时空仓永远不生效
        scale = order.get("scale")
        budget = min(nav_open * weight * (1.0 if scale is None else float(scale)), cash)
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
            "side": "BUY", "date": date_s, "time": OPEN_TIME, "price": round(o, 3),
            "shares": shares, "amount": round(amount, 2), "fee": fee["total"],
            "tax": 0.0, "pnl": None, "pnl_pct": None,
            "reason": order.get("reason", "计划建仓"),
        })
        log.append(f"买入 {code} {shares} 股 @{o:.2f}（{order.get('reason', '计划建仓')}）")

    # ---- 3. 收盘：按今日选股生成计划卡与明日委托
    nav = cash + _market_value(positions, feed)
    spec = account.get("spec") or {"id": account["id"], "name": account.get("name", "")}
    plans, new_orders = _next_plans(engine, feed, spec, params, positions, nav, date_s,
                                    regime_scale=_regime_scale(engine, date_s, params),
                                    boards=prof.get("boards"))

    # ---- 3.4 弱转强：买入当日没封板 = 判断错了，次日开盘无条件走
    #
    # 这条不能用止损替代。止损回答「跌到某个价位怎么办」，而这句要的是
    # 「一到收盘就承认看错」——差整整一个隔夜，成本就吃在隔夜跳空上。
    # 它也不会和 T+1 冲突：委托是今天收盘挂的，成交在明天开盘。
    if params.get("need_limit_close"):
        for pos in positions:
            if pos.get("open_date") != date_s:
                continue
            bar, prev = feed.bar(pos["code"], date_s)
            if not bar or not prev or not prev.get("close"):
                continue
            lim = price_limit(pos["code"], bool(engine.symbol(pos["code"]).get("is_st")))
            chg = float(bar["close"]) / float(prev["close"]) - 1
            if chg >= lim - 0.002:
                continue
            frac = min(max(float(params.get("limit_close_frac") or 1.0), 0.0), 1.0)
            qty = int(pos["shares"] * frac / 100) * 100
            if 0 < qty < pos["shares"] * 0.5:
                qty = pos["shares"]
            if qty < 100:
                continue
            new_orders.append({"code": pos["code"], "name": pos.get("name", ""),
                               "kind": "TRIM", "shares": qty,
                               "reason": "未封板离场", "created": date_s,
                               "scale": 1.0})
            pos["exit_next"] = "未封板离场" if qty >= pos["shares"] else "未封板减半"
            log.append(f"{pos['code']} 收盘未封板（{chg * 100:+.1f}%），"
                       f"明日开盘{'清仓' if qty >= pos['shares'] else f'减 {qty} 股'}"
                       f"（弱转强验证失败）")

    # ---- 3.5 收盘快照：把「今天这只票自己赚了多少 / 浮盈多少 / 离止损多远」算出来
    #
    # 放在撮合之后，是因为**今天新买的票也要在内**——用户问的正是「我今天买的那只，
    # 收盘是赚还是亏」。所以口径分两种：
    #   今日新建仓 → 相对买入价（买完到收盘这段）
    #   此前已持有 → 相对昨收（今天这一天的日内涨跌）
    # 两者相加就是今天账户的浮动盈亏，再加上已实现部分就是今日总盈亏，账是平的。
    #
    # 这些数每天只在推进模拟盘时算一次，展示层直接读，不要在页面里重算——
    # 页面拿到的是落盘账户，重算就得再查一次日线，还可能因为数据更新而和账户对不上。
    for pos in positions:
        bar, prev = feed.bar(pos["code"], date_s)
        close = float(bar["close"]) if bar else float(pos.get("cost") or 0)
        prev_close = float(prev["close"]) if prev else close
        cost = float(pos.get("cost") or 0)
        stop = float(pos.get("stop") or 0)
        today_buy = pos.get("open_date") == date_s
        base = cost if today_buy else prev_close
        pos["last"] = close
        pos["today_buy"] = today_buy
        pos["day_pct"] = round((close / base - 1) * 100, 2) if base > 0 else 0.0
        pos["day_pnl"] = round((close - base) * pos["shares"], 2)
        pos["pnl_pct"] = round((close / cost - 1) * 100, 2) if cost > 0 else 0.0
        pos["pnl"] = round((close - cost) * pos["shares"], 2)
        pos["dist_stop_pct"] = round((close / stop - 1) * 100, 2) if stop > 0 else None
        pos["held_days"] = _held_days(engine, pos.get("open_date") or date_s, date_s)

    # ---- 4. 记账
    equity = list(account.get("equity") or [])
    if not equity or equity[-1]["date"] != date_s:
        equity.append({"date": date_s, "value": round(nav, 2)})

    updated = {
        **account,
        # `weak_participate` 必须写回：它是由 `_advance` 从 spec 补出来的，
        # 只放在局部 `params` 里的话，`regime_state` 读账户落盘的 params
        # 永远是 None，页面上会一直显示「弱势停手」——而账户其实在挂单。
        "params": {**(account.get("params") or {}),
                   "weak_participate": bool(params.get("weak_participate")),
                   "gap_gate": bool(params.get("gap_gate", True)),
                   "gap_band": params.get("gap_band"),
                   "need_limit_close": bool(params.get("need_limit_close")),
                   "limit_close_frac": float(params.get("limit_close_frac") or 1.0),
                   "allow_add": bool(params.get("allow_add", True)),
                   "weak_huddle": bool(params.get("weak_huddle", True))},
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


def _held_action(plan: dict, pos: dict, nav: float,
                 regime_scale: float, allow_add: bool = True) -> dict | None:
    """已持仓的票，这张计划卡要不要动手——加仓、减仓，还是什么都不做。

    口径就是计划卡自己说的话，不另立一套规则：
    - **加仓**：同一只票今天又进了 Top-N，说明信号还在，就补到计划仓位
      （`sizing.shares`）。受两条硬约束：单票不超过 15% 净值、现金够付；
      弱势时和建仓同一口径——预算乘 `regime_scale`（0.5），不是不加。
    - **减仓**：浮盈到了 12% 就减 1/3，只减一次。TP1 已经减过半的票照样减：
      三档合计 2/3，剩下的 1/3 还跟得住趋势。此前把 `tp1_done` 也排除在外，
      这条规则几乎永不触发——TP1 在 +2.6ATR 就先成交了，浮盈走不到 12%。
    - 其余情况只标注「已持有 · 不动」。
    """
    price = float(plan.get("ref_price") or 0)
    cost = float(pos.get("cost") or 0)
    shares = int(pos.get("shares") or 0)
    if price <= 0 or cost <= 0 or shares <= 0:
        return None
    pnl_pct = (price / cost - 1) * 100

    if pnl_pct >= TRIM_PNL_PCT and not pos.get("trimmed"):
        qty = int(shares * TRIM_FRAC / 100) * 100
        if qty >= 100:
            return {"kind": "TRIM", "state": ORDER_TRIM, "shares": qty,
                    "reason": f"计划减仓（浮盈 {pnl_pct:.1f}%）"}

    if pnl_pct < TRIM_PNL_PCT and regime_scale > 0 and allow_add:
        target = int((plan.get("sizing") or {}).get("shares") or 0)
        cap_shares = int(nav * RISK_LIMITS["max_single_weight"] / price / 100) * 100
        delta = min(target - shares, cap_shares - shares)
        # 择时降仓时加仓预算同样打折，和建仓一个口径
        delta = int(delta * min(regime_scale, 1.0) / 100) * 100
        if delta >= 100:
            return {"kind": "ADD", "state": ORDER_ADD, "shares": delta,
                    "reason": f"计划加仓（浮盈 {pnl_pct:+.1f}%）"}

    return None


def holding_rows(positions: list[dict], params: dict | None = None,
                 plans: list[dict] | None = None) -> list[dict]:
    """持仓逐票 + 明日动作，供复盘日记的「持仓」段直接渲染。

    为什么要有这个函数：复盘如果只写「今天成交 2 笔、持仓 2 只」，读的人
    根本不知道明天该干什么。用户的原话是「我今天买入了某一只，今天收盘时
    亏了还是赚了，明白这只该怎么操作」——这三件事必须落在同一行里。

    动作口径和 `_held_action` / 撮合里的止损止盈是同一套，不另立规则：
    日报说「明天减仓」而第二天实际什么都没做，那份日报就是在骗人。
    所以这里的文案都带条件——减仓只在「明天它又进了榜单」时才会发生，
    写成「明日减仓」会让人误以为是既定动作。
    """
    params = params or {}
    kinds: dict[str, str] = {}
    for p in plans or []:
        k = p.get("order_kind")
        if k:
            kinds[p.get("code") or ""] = k
    time_stop = int(params.get("time_stop") or DEFAULTS["time_stop"])

    rows: list[dict] = []
    for pos in positions:
        code = pos.get("code") or ""
        shares = int(pos.get("shares") or 0)
        if shares <= 0:
            continue
        cost = float(pos.get("cost") or 0)
        close = float(pos.get("last") or cost)
        stop = float(pos.get("stop") or 0)
        pnl_pct = pos.get("pnl_pct")
        if pnl_pct is None:
            pnl_pct = (close / cost - 1) * 100 if cost > 0 else 0.0
        pnl_pct = float(pnl_pct)
        day_pnl = float(pos.get("day_pnl") or 0.0)
        day_pct = float(pos.get("day_pct") or 0.0)
        dist = pos.get("dist_stop_pct")
        dist = None if dist is None else float(dist)
        held = int(pos.get("held_days") or 0)
        today_buy = bool(pos.get("today_buy"))

        kind = kinds.get(code)
        if pos.get("exit_next"):
            # 已经下了离场单，不再是「可能减仓」而是既定动作，文案要确定
            action, tone = f"{pos['exit_next']}，明日开盘走", "warn"
        elif kind == "TRIM":
            action, tone = "明日开盘减仓 1/3", "warn"
        elif kind == "ADD":
            action, tone = "明日开盘加仓", "brand"
        elif dist is not None and dist <= 2.0:
            action, tone = f"逼近止损（距 {dist:.1f}%），跌破就走", "warn"
        elif today_buy:
            # T+1：今天买的今天不能卖，这一条必须写在明面上，
            # 否则用户会在盘中问「跌了怎么不止损」
            action, tone = "今日建仓，T+1 明日才可卖；按止损位持有", "flat"
        elif pnl_pct >= TRIM_PNL_PCT and not pos.get("trimmed"):
            action = f"浮盈 {pnl_pct:.1f}%，若明日仍在榜单则减 1/3"
            tone = "warn"
        elif held >= time_stop - 1:
            action, tone = f"时间止损倒计时（已持 {held} 日 / 上限 {time_stop} 日）", "warn"
        else:
            action = f"继续持有，止损 {stop:.2f}" if stop > 0 else "继续持有"
            tone = "flat"

        rows.append({
            "code": code, "name": pos.get("name") or "",
            "shares": shares, "cost": round(cost, 3), "close": round(close, 3),
            "day_pnl": round(day_pnl, 2), "day_pct": round(day_pct, 2),
            "pnl": round((close - cost) * shares, 2),
            "pnl_pct": round(pnl_pct, 2),
            "stop": round(stop, 3),
            "dist_stop_pct": None if dist is None else round(dist, 2),
            "held_days": held, "today_buy": today_buy,
            "action": action, "action_tone": tone,
            "tone": "up" if day_pnl > 0 else ("down" if day_pnl < 0 else "flat"),
        })

    # 今天买的排最前（用户最关心），其余按当日影响大小
    rows.sort(key=lambda r: (not r["today_buy"], -abs(r["day_pnl"])))
    return rows


def _next_plans(engine, feed: _Feed, spec: dict, params: dict,
                positions: list[dict], nav: float,
                date_s: str, regime_scale: float = 1.0,
                boards: dict | None = None) -> tuple[list[dict], list[dict]]:
    """按今日收盘选股 → 计划卡 + 明日委托。

    弱势（沪深300 在半年线下方）时按策略分开走：
    - `regime_scale == 0`（未声明 `weak_participate`）：一张不挂，卡片写「弱势停手」；
    - `0 < regime_scale < 1`（板块联动首日）：照常挂单，但①只保留行业里正在
      抱团的候选（`ind_limit_cnt` / `ind_rank_pct`），②每笔预算乘 `regime_scale`。

    上一版这里在弱势时对所有策略一张委托都不挂。好处是熊段不被拖累，代价是
    指数一旦长期站不回去，账户就彻底锁死——实测连续三个月零成交，自进化闭环里
    「模拟成交 → 收盘复盘 → 进化换参」三环全空转。指数位置是个很粗的信号，
    它回答不了「今天有没有能做的票」；行业抱团能。

    `boards` 传购买权限：模拟盘的委托是照这张卡真挂出去的（次日开盘成交），
    所以候选池必须先按权限过一遍——买不了的票进了卡，第二天照样会被成交。
    """
    try:
        selection = execute(engine, spec, top_n=int(params["top_n"]), boards=boards)
    except Exception:                                   # noqa: BLE001 - 选股失败不该拖垮记账
        return [], []
    items = selection.get("items") or []
    if not items:
        return [], []

    held = {p["code"] for p in positions}
    pos_by_code = {p["code"]: p for p in positions}
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
        # 抱团画像跟着卡片走：弱势时「凭什么这只还能建仓」全靠它解释，
        # 前端直接显示「行业 3 家涨停 / 排名前 5%」，不用再查一次因子。
        plan["huddle"] = item.get("huddle") or {}
        # 区间带策略（弱转强）的买点是「次日高开多少」，不是 ATR 建仓区。
        # 卡片上不写清楚，用户会照着 ATR 区间去挂限价，而实际成交看的是开盘价。
        band = params.get("gap_band")
        if band and signal_type == "ENTRY":
            plan["entry_rule"] = f"高开 {band[0]:g}%~{band[1]:g}%"
        plans.append(plan)
        # 委托状态：这张卡到底有没有真的挂单，比「现价落在区间内」有用得多
        if code in held:
            pos = pos_by_code.get(code) or {}
            act = _held_action(plan, pos, nav, regime_scale,
                               allow_add=bool(params.get("allow_add", True)))
            if not act:
                plan["order_state"] = ORDER_HELD
                plan["order_tone"] = "flat"
                continue
            plan["order_kind"] = act["kind"]
            plan["order_shares"] = act["shares"]
            plan["order_state"] = act["state"]
            plan["order_tone"] = "brand"
            orders.append({"code": code, "name": item["name"], "kind": act["kind"],
                           "shares": act["shares"], "reason": act["reason"],
                           "created": date_s, "atr": round(atr, 4),
                           "score": item.get("score"),
                           "scale": round(regime_scale, 2)})
            continue
        if slots <= 0:
            plan["order_state"] = ORDER_NOSLOT if len(held) <= int(params["top_n"]) else ORDER_OVER
            plan["order_tone"] = "flat"
            continue
        # 弱势停手（只有未声明 `weak_participate` 的策略会走到这里）：
        # 一张单都不挂。挂在账上但永远成交不了的委托比明说「不做」更糟。
        if regime_scale <= 0:
            plan["order_state"] = ORDER_REGIME
            plan["order_tone"] = "flat"
            continue
        # 弱势参与（目前只有「板块联动首日」）：再加一道抱团准入——
        # 行业当日涨停 ≥ N 家、或涨幅排名进前 M% 才建仓。
        # 判断口径与回测 `_huddle_ok` 同一组常量，回测调阈值这里自动跟着变。
        # 因子缺行（次新 / 停牌复牌）当「不抱团」：弱市里宁可错过。
        # `weak_huddle=False` 的策略（波段）跳过这一道，只留半仓。
        if regime_scale < 1.0 and params.get("weak_huddle", True):
            huddle = item.get("huddle") or {}
            cnt = float(huddle.get("ind_limit_cnt") or 0)
            # `ind_rank_pct` 的 0 表示行业最强，不能用 `or 100` 兜底
            rank_raw = huddle.get("ind_rank_pct")
            rank = 100.0 if rank_raw is None else float(rank_raw)
            if not (cnt >= REGIME_HUDDLE_MIN_LIMITS or rank <= REGIME_HUDDLE_MAX_RANK):
                plan["order_state"] = ORDER_NO_HUDDLE
                plan["order_tone"] = "flat"
                continue
        plan["order_state"] = ORDER_WEAK if regime_scale < 1.0 else ORDER_WAITING
        plan["order_tone"] = "brand"
        orders.append({"code": code, "name": item["name"], "reason": "计划建仓",
                       "created": date_s, "atr": round(atr, 4),
                       "score": item.get("score"), "scale": round(regime_scale, 2)})

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


_MA_NAME = {5: "周线", 10: "两周线", 20: "月线", 30: "月半线",
            60: "季线", 90: "季度线", 120: "半年线", 200: "年线"}


def regime_state(engine, date_s: str, params: dict) -> dict:
    """择时状态的完整快照，给前端解释「今天为什么只买这么点」。

    只回一个布尔值是不够的：页面上看到的是一排灰色标签，用户没法判断这是
    规则在起作用还是程序坏了。所以把指数点位、均线点位、偏离幅度一起带出去
    ——「沪深300 3984.8 低于半年线 4539.9（−12.2%）」这句话本身就是解释。

    三个字段各管一件事，别混：
    - `weak`：大盘在均线下方，进入「只打抱团 + 半仓」模式；
    - `open_new`：是否**满额**开新仓。弱势时为 False，但**不等于停手**——
      真正决定买不买的是候选所在行业抱不抱团（见 `_next_plans`）；
    - `scale`：弱势时的预算系数，直接取自 `backtest.REGIME_SCALE`。

    `open_new=False` 而账户仍在建仓，是本版和上一版最大的行为差别；
    上一版它是「停手」的意思，前端那句「指数回到均线上方即恢复」也得跟着改。

    取不到指数数据时返回 `enabled=False`：宁可照常交易，
    也不因为缺数据让模拟盘静默停摆。
    """
    ma = int(params.get("market_ma") or 0)
    state = {
        "enabled": False, "ma": ma, "open_new": True, "weak": False, "scale": 1.0,
        "index_close": None, "index_ma": None, "gap_pct": None,
        "as_of": None, "label": "未启用择时", "detail": "不按大盘位置过滤新仓",
    }
    if ma <= 0:
        return state
    try:
        rows = engine.repo.index_daily("000300.SH", ma + 40)
    except Exception:                                    # noqa: BLE001
        return state
    hist = [(b["date"], float(b["close"])) for b in (rows or []) if b.get("close")]
    hist = [r for r in hist if not date_s or r[0] <= date_s]
    if len(hist) < ma:
        return state

    closes = [c for _, c in hist[-ma:]]
    last, avg = closes[-1], sum(closes) / len(closes)
    gap = (last / avg - 1) * 100 if avg else 0.0
    ok = last >= avg
    name = _MA_NAME.get(ma, f"{ma} 日线")
    if ok:
        label = "可开新仓"
    elif params.get("weak_participate"):
        # 两条「弱势参与」路线要分开说：打抱团的只有板块联动型策略，
        # 波段是直接半仓买动量票（`weak_huddle=False`）。都写成「只打抱团」，
        # 用户会以为波段也在按行业涨停筛，然后奇怪为什么它买的票行业没涨停。
        label = (f"弱势 · 只打抱团（{REGIME_SCALE:g} 成仓）"
                 if params.get("weak_huddle", True)
                 else f"弱势 · 半仓参与（{REGIME_SCALE:g} 成仓）")
    else:
        label = "弱势停手 · 该策略在弱市无 edge"
    state.update({
        "enabled": True, "open_new": ok, "weak": not ok,
        "scale": 1.0 if ok else (REGIME_SCALE if params.get("weak_participate") else 0.0),
        "as_of": hist[-1][0],
        "index_close": round(last, 2), "index_ma": round(avg, 2),
        "gap_pct": round(gap, 2),
        "label": label,
        "detail": (f"沪深300 {last:.0f} {'高于' if ok else '低于'}"
                   f"{name} {avg:.0f}（{gap:+.1f}%）"),
    })
    return state


def _regime_allows(engine, date_s: str, params: dict) -> bool:
    """今天该不该开新仓：沪深300 是否在 `market_ma` 日线之上。

    与回测 `backtest._regime_ok` 同一口径。`market_ma=0` 时永远返回 True，
    保持历史行为；取不到指数数据也返回 True——宁可照常交易，也不因为缺数据
    让模拟盘静默停摆。
    """
    return bool(regime_state(engine, date_s, params)["open_new"])


def _regime_scale(engine, date_s: str, params: dict) -> float:
    """择时系数：大盘在均线上 → 1.0（满额），否则看策略自己声明的口径。

    返回 `REGIME_SCALE`（弱市半仓）或 `0.0`（停手）——取决于这个账户所属策略
    有没有声明 `weak_participate`：
    - 板块联动首日：弱市照做，只打抱团、半仓（放开实测更好）；
    - 缩量蓄势 / 趋势波段：弱市停手（放开实测年化掉、回撤大）。

    **这个函数不回答「买哪只」**，只回答「买多少」。买不买由行业抱团决定
    （`_next_plans` 里的 `REGIME_HUDDLE_*`）。分开是因为依据不同：仓位看大盘，
    准入看题材。
    """
    if _regime_allows(engine, date_s, params):
        return 1.0
    return REGIME_SCALE if params.get("weak_participate") else 0.0


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


ORDER_KIND_LABEL = {"ENTRY": "建仓", "ADD": "加仓", "TRIM": "减仓"}


def match_orders(prev_orders: list[dict], prev_plans: list[dict],
                 trades: list[dict], date_s: str,
                 log: list[str] | None = None) -> dict:
    """把「昨天收盘挂出的委托」与「今天实际成交」逐张对起来。

    复盘要回答的第一个问题不是「今天赚了多少」，而是「说的话算不算数」：
    计划卡上写着「待明日开盘成交」，第二天到底成交了没有？没成交是因为
    一字板买不到、现金不够，还是这张委托本来就不该挂？这个数不报出来，
    「计划」和「账户」就成了两套互不相干的叙事——卡上天天有票，账户里
    从来不动，看的人只能猜是规则还是程序。

    成交价与委托价的偏离记成**不利滑点**（正数 = 对你不利）：
    买单成交得比委托价高、卖单成交得比委托价低都算不利。两边同一个符号，
    平均下来才有意义——否则「买单平均 +30bp、卖单平均 +30bp」会被读成
    同一件事，实际一个是成本、一个是收益。
    """
    plan_px: dict[str, float] = {}
    for plan in prev_plans or []:
        code = plan.get("code")
        price = plan.get("ref_price")
        if code and price:
            plan_px[code] = float(price)

    pool = list(trades or [])
    used = [False] * len(pool)
    log_lines = [str(x) for x in (log or [])]
    rows: list[dict] = []
    missed: list[dict] = []
    orders_by_kind: dict[str, int] = {}
    filled_by_kind: dict[str, int] = {}

    for order in prev_orders or []:
        code = order.get("code") or ""
        kind = order.get("kind") or "ENTRY"
        side = "SELL" if kind == "TRIM" else "BUY"
        orders_by_kind[kind] = orders_by_kind.get(kind, 0) + 1
        ref = plan_px.get(code) or 0.0
        # 先按成交原因对号：同一只票今天可能既被止损卖出、又被计划减仓卖出，
        # 随便抓一张 SELL 会把止损算成「计划减仓成交了」。对不上再退回
        # 「同代码同方向」——建仓单可能被一字板跳过，走到这里是撮合成功后
        # 的补记，顺序取第一张就够了。
        tag = ORDER_KIND_LABEL.get(kind, "")
        hit = None
        for i, trade in enumerate(pool):
            if used[i] or trade.get("code") != code or trade.get("side") != side:
                continue
            if tag and tag in str(trade.get("reason") or ""):
                hit = i
                break
        if hit is None:
            for i, trade in enumerate(pool):
                if used[i] or trade.get("code") != code or trade.get("side") != side:
                    continue
                hit = i
                break
        if hit is None:
            reason = next((ln.split("：", 1)[1] for ln in log_lines
                           if code in ln and "跳过" in ln), "")
            item = {"code": code, "name": order.get("name", ""), "kind": kind,
                    "kind_label": ORDER_KIND_LABEL.get(kind, kind),
                    "plan_price": round(ref, 3) or None,
                    "shares": int(order.get("shares") or 0),
                    "reason": reason or "当日无对应成交（委托作废或未到价）"}
            missed.append(item)
            rows.append({**item, "status": "未成交", "fill_price": None,
                         "adverse_bps": None})
            continue
        used[hit] = True
        trade = pool[hit]
        filled_by_kind[kind] = filled_by_kind.get(kind, 0) + 1
        fill = float(trade.get("price") or 0)
        bps = None
        if ref > 0 and fill > 0:
            diff = (fill - ref) / ref * 10000
            bps = round(diff if side == "BUY" else -diff, 1)
        rows.append({"code": code, "name": order.get("name", ""), "kind": kind,
                     "kind_label": ORDER_KIND_LABEL.get(kind, kind),
                     "plan_price": round(ref, 3) or None,
                     "fill_price": round(fill, 3), "shares": int(trade.get("shares") or 0),
                     "adverse_bps": bps, "status": "成交", "reason": ""})

    total = len(prev_orders or [])
    filled = sum(filled_by_kind.values())
    bps_all = [r["adverse_bps"] for r in rows if r["adverse_bps"] is not None]
    return {
        "date": date_s,
        "order_count": total,
        "filled_count": filled,
        "fill_rate": round(filled / total * 100, 1) if total else None,
        "avg_adverse_bps": round(sum(bps_all) / len(bps_all), 1) if bps_all else None,
        "kinds": [{"kind": k, "label": ORDER_KIND_LABEL.get(k, k),
                   "orders": orders_by_kind.get(k, 0),
                   "filled": filled_by_kind.get(k, 0)}
                  for k in ORDER_KIND_LABEL if orders_by_kind.get(k)],
        "rows": rows,
        "missed": missed,
    }


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
                                   "stop", "tp1", "tp2",
                                   # 收盘快照：日报持仓表和前端都用这几个数。
                                   # 这里必须透传，不能让页面拿 `change_pct` 顶替
                                   # 「今日盈亏」——`change_pct` 是按最近两根日线算的，
                                   # 停牌股、隔了周末的账户都会算错。
                                   "day_pnl", "day_pct", "dist_stop_pct",
                                   "held_days", "today_buy", "exit_next") if k in pos},
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
        # 择时状态必须跟着账户一起出去：一单不挂的时候，页面要能说清是规则在
        # 起作用（大盘在均线下方）还是程序没跑（`is_stale`）。
        "regime": regime_state(engine, today_s, account.get("params") or {}),
        "positions": positions,
        "orders": account.get("orders") or [],
        "plans": ensure_order_state(account.get("plans") or [],
                                     account.get("positions") or [],
                                     account.get("params") or {}),
        "equity": (account.get("equity") or [])[-260:],
        "trades": trades[-80:],
        "log": account.get("log") or [],
    }
