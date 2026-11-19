"""策略回测内核：横截面打分选股 + 次日开盘撮合 + ATR 区间止盈止损。

三条硬口径先写在最前面，因为它们决定了这份回测**能被信到什么程度**：

1. **只用价量因子**。库里的 `factor` 表是「当前截面」（5579 行 / 一天），
   `flow` 只有最近三十来天，历史 ROE / PE / 股息率连一天的历史值都没有。
   所以凡是依赖估值、质量的权重，在回测里会被摘掉，剩下的权重重新归一，
   并在报告的 `dropped` 里逐条写明为什么摘。宁可报「这条策略在回测里只剩
   动量」,也不能拿今天的 ROE 去回放过去的行情——那是标准的未来函数。

2. **成交价取信号次日的开盘价**。信号在 T 日收盘后生成，最早 T+1 开盘
   集合竞价成交。用 T 日收盘价成交等于「先知道收盘价再决定买」，
   是最常见也最容易被自己骗过去的一种未来函数。

3. **股票池按最新成交额取前 N**。这是本实现唯一承认的轻微未来函数：用今天的
   流动性挑历史池。为什么不取全市场——5579 只 × 340 天 × 6 个因子的面板，
   单次装载就要几分钟、几百兆内存，遗传搜索跑 144 次直接不可用。
   这个取舍写在报告里，不藏着。

还有几处简化（都写进 `notes` 返回给前端，而不是留在代码注释里）：
- 行业分类、ST 标记用的是**当前**值，不是历史值；
- 现金按「当日可用」处理，没有 T+1 资金交收；
- 同一根 K 线上同时触及止损与止盈时，按**止损**结算（保守）；
- 一字跌停不撮合卖单，一字涨停不撮合买单。
"""

from __future__ import annotations

import math
import statistics
import threading

from alphaforge_core.constants import RISK_LIMITS, normalize_profile, trade_fee
from alphaforge_core.fields import FIELD_MAP
from alphaforge_core.metrics import summarize
from alphaforge_core.universe import pc as price_limit

TRADING_DAYS = 252

# 撮合模型的版本号。**改动任何影响成交的规则都必须加一**：
# 进场是否每天扫、止损优先级、成交价口径、仓位算法……全都算。
# API 层据此把「按旧规则算出来的回测」判为过期，否则界面上会同时挂着
# 两套口径的数字，而且看不出来哪个是哪个（实测：改成每日补仓之后，
# 同一条动量策略从 -12.75% 变成 -40.08%，旧数字留在卡片上会误导人）。
#
# v3：结构参数改为「先读 spec 再让 params 覆盖」。此前一律用 DEFAULTS，
# 于是声明 top_n=10 的策略实际按 8 回测，而进化基线按 10 跑——两个数字
# 并排显示却不可比。这是**成交规则**的变化（直接改变持仓数与单票仓位），
# 必须算新版本。
# v4：TP1 减半后剩余仓位止损上移到成本价（此前止损一动不动）。
# 这直接改变每笔交易的盈亏分布，不是显示层的调整，必须算新版本——
# 否则 v3 存下来的回测结果会和 v4 并排显示却不可比。
# v5：单票上限随持股数放宽（`single_cap`）。持股 < 6 只时单笔能买更多，
# 总仓位、每笔盈亏、回撤全部改变——同样是成交规则变化，必须算新版本。
MODEL_VERSION = 5


# 多取一段做预热：mom60 / dist_high / vol20 在模拟首日就得有值
WARMUP = 90

# 回测可用的因子：key -> (中文名, 方向)。方向与 `alphaforge_core.fields` 一致，
# 1 表示越大越好，-1 表示越小越好；z 值乘上方向之后，加权求和才有意义。
FACTORS: dict[str, tuple[str, int]] = {
    "mom5": ("5 日动量", 1),
    "mom20": ("20 日动量", 1),
    "mom60": ("60 日动量", 1),
    "vol20": ("20 日波动率", -1),
    "dist_high": ("距 52 周高点", -1),
    "volratio": ("量比", 1),
    "mainflow": ("主力净流入占比", 1),
    "main_net": ("主力净流入", 1),
    "limit_cnt": ("连板数", 1),
}

# 只有当前值、没有历史序列的字段。写清原因是这个模块的义务：
# 用户看到「多因子质量价值」在回测里只剩动量，第一反应一定是「坏了」。
NO_HISTORY: dict[str, str] = {
    "roe": "库里只有当前 ROE 截面（symbol 表），无历史序列",
    "ep": "盈利收益率由当前 PE 推出，无历史序列",
    "dividend": "股息率为当前值，无历史序列",
    "pe": "市盈率为当前截面，无历史序列",
    "float_mv": "流通股本为当前值，无历史序列",
    "up_days": "形态类字段，未纳入回测因子集",
    "sort": "排序字段不参与因子加权",
}

# 资金流历史只有三十来天，覆盖率低于这条线就整体摘掉：
# 留着的后果是「90% 的交易日 z 值全是 0」，等于给综合分加了一个常数项，
# 看起来没影响，实际上把别的因子的相对权重悄悄改了。
FLOW_MIN_COVERAGE = 0.5

DEFAULTS: dict = {
    "top_n": 8,
    "max_per_industry": 2,
    "stop_atr": 2.2,      # 止损 = 成交价 − 2.2 × ATR(14)
    "tp1_atr": 2.6,       # 第一目标 = 成交价 + 2.6 × ATR，减半
    "tp2_atr": 5.0,       # 第二目标 = 成交价 + 5.0 × ATR，清剩余
    "time_stop": 20,      # 持有 20 个交易日仍未触发任何一档 → 时间止损
    # TP1 减半后是否把剩余仓位的止损上移到成本价。默认开。
    # 它不是可搜索项（不在 `paper.PARAM_KEYS` / `harness.BOUNDS` 里）——
    # 这是修正一处撮合缺陷，不是又一个待调参数。留这个键是为了 A/B 对照。
    "breakeven_after_tp1": 1,
    # 择时：0 = 一直满仓（历史口径）；20 = 沪深300 在 20 日线之上才开新仓。
    # 同样不进基因组——它管的是「该不该做」而不是「做什么」，和选股参数
    # 混在一起搜，等于让搜索去挑一段行情的运气。
    "market_ma": 0,
    # 选股池硬门槛：连板数 ≥ 该值才进候选。0 = 不设门槛（默认）。
    # 打板类策略靠它把「涨停池之外」的票挡在外面，否则涨停股不够量时会凑数。
    "pool_limit_min": 0,
    # 已废弃：调仓周期。回测现在**每天**扫描空缺仓位，与模拟盘（`paper.py`
    # 的 `_next_plans` 每天跑一次）保持同一套进场规则。保留这个键只是为了让
    # 早先落盘的 params 还能被读进来，不参与撮合、也不再进搜索基因组。
    "rebalance": 1,
    "capital": 1_000_000.0,
}

# 单日成交量占当日成交额的比例上限：模拟「你的单子不至于把票买飞」
MAX_AMOUNT_SHARE = 0.02

# 目标仓位。单票上限与「等权 1/top_n」取小，避免 top_n=3 时一把梭到 33%
MAX_SINGLE = RISK_LIMITS["max_single_weight"]
MAX_GROSS = RISK_LIMITS["max_gross_exposure"]


def single_cap(top_n: int) -> float:
    """单票权重上限，随持股数放宽到「打满总仓位刚好够用」。

    15% 的固定单票上限是给 10 只以上的组合用的。放到 2~3 只的组合上，它会
    变成总仓位的隐形天花板：`top_n=3` 最多只能买到 45%，剩下 55% 永远躺现金——
    这不是「风险控制」，是把策略的收益和回撤同时腰斩，而且「平均仓位 ≥ 50%」
    的采纳门槛从此再也过不去。

    这不是推测，是实测出来的：放开 `top_n` 让进化自己选，它把趋势波段从 3
    只顶到 7 只——7 × 15% ≈ 105% 刚好打满。搜索没在作弊，它在解「怎么才
    能把钱投出去」这道题。真正该修的是单票上限，不是把 top_n 钉死。

    `MAX_GROSS / top_n` 就是「top_n 只等权时刚好用满总仓位」的单票权重；
    持股数 ≥ 6 时它已经 ≤ 15%，与原来的固定上限**完全一致**——所以这个改动
    只影响集中持仓（< 6 只），常规策略的每个数字都不变。
    """
    n = max(1, int(top_n))
    return max(MAX_SINGLE, min(1.0, MAX_GROSS / n))


# --------------------------------------------------------------------- 面板
class Panel:
    """一次装好的回测面板：日期 × 代码 × 价量因子。

    装面板是这个模块唯一花钱的地方（一次批量取数 + 一次因子展开 + 一次截面
    标准化）。之后每跑一个基因组都只是在这块面板上重放交易，
    所以「1 次装面板 + 144 次遗传评估」而不是「144 次查库」。

    数据按「当日 → 代码 → 该票自己的第几根 K 线」两级寻址：
    `bar[d][ci]` 给出下标，`bars[ci]["c"][p]` 给出价格。这样停牌、次新股
    造成的日期缺口不需要用前值填充来糊弄——没有那根 K 线就是没有。
    """

    __slots__ = ("trade_date", "dates", "codes", "names", "industries", "st",
                 "limit", "bars", "bar", "z", "eligible", "coverage",
                 "pool_size", "universe_size", "min_amount", "dropped_flow",
                 "bench")

    def __init__(self) -> None:
        self.trade_date: str = ""
        self.dates: list[str] = []
        self.codes: list[str] = []
        self.names: list[str] = []
        self.industries: list[str] = []
        self.st: list[bool] = []
        self.limit: list[float] = []
        self.bars: list[dict] = []
        self.bar: list[list[int]] = []
        self.z: dict[str, list[list[float]]] = {}
        self.eligible: list[list[bool]] = []
        self.coverage: dict[str, float] = {}
        self.pool_size: int = 0
        self.universe_size: int = 0
        # 沪深300 的 (日期, 收盘) 序列，比 `dates` 多取 60 根做均线预热。
        # 只存价格、不存「是否在均线之上」——均线周期是回测参数，
        # 面板是所有基因组共用的，把判断提前固化进面板会让 market_ma 变成摆设。
        self.bench: list[tuple[str, float]] = []
        self.min_amount: float = 0.0
        self.dropped_flow: list[str] = []

    @property
    def n(self) -> int:
        return len(self.dates)

    @property
    def ncodes(self) -> int:
        return len(self.codes)


_PANEL_CACHE: dict[tuple, Panel] = {}
_PANEL_LOCK = threading.Lock()


def build_panel(engine, days: int = 250, max_codes: int = 600,
                min_amount: float = RISK_LIMITS["min_amount"], force: bool = False) -> Panel:
    """装面板。同一交易日 + 同样参数复用上一次的结果。"""
    trade_date = engine.repo.trade_date() or ""
    key = (trade_date, int(days), int(max_codes), float(min_amount))
    if not force:
        hit = _PANEL_CACHE.get(key)
        if hit is not None:
            return hit

    with _PANEL_LOCK:
        if not force:
            hit = _PANEL_CACHE.get(key)
            if hit is not None:
                return hit
        panel = _build(engine, days, max_codes, min_amount)
        if len(_PANEL_CACHE) > 6:
            _PANEL_CACHE.clear()
        _PANEL_CACHE[key] = panel
        return panel


def clear_panel_cache() -> None:
    with _PANEL_LOCK:
        _PANEL_CACHE.clear()


def slice_panel(panel: Panel, lo: int, hi: int) -> Panel:
    """把面板按**交易日轴**切一段出来，用于样本外验证。

    只切日期轴，`bar` 里的下标不动——`bar[d][ci]` 指向的是 `bars[ci]` 的
    绝对位置，而 `bars` 仍保留完整窗口，所以切片首日的「上一根 K 线」
    依然取得到，涨跌停判断不会因为切了一刀就失真。这一步纯内存操作，
    不碰数据库，几毫秒就完。
    """
    lo = max(0, int(lo))
    hi = min(int(hi), panel.n)
    if hi - lo < 2:
        lo, hi = max(0, min(lo, panel.n - 2)), panel.n
    sub = Panel()
    sub.trade_date = panel.trade_date
    sub.dates = panel.dates[lo:hi]
    sub.codes = panel.codes
    sub.names = panel.names
    sub.industries = panel.industries
    sub.st = panel.st
    sub.limit = panel.limit
    sub.bars = panel.bars                      # 共享，不复制
    sub.bar = panel.bar[lo:hi]
    sub.z = {k: v[lo:hi] for k, v in panel.z.items()}
    sub.eligible = panel.eligible[lo:hi]
    sub.coverage = panel.coverage
    sub.pool_size = panel.pool_size
    sub.universe_size = panel.universe_size
    sub.min_amount = panel.min_amount
    sub.dropped_flow = panel.dropped_flow
    return sub


def _build(engine, days: int, max_codes: int, min_amount: float) -> Panel:
    panel = Panel()
    panel.min_amount = min_amount
    panel.trade_date = engine.repo.trade_date() or ""

    universe = engine.universe()
    panel.universe_size = len(universe)
    if not universe:
        return panel

    # 池子按最新成交额排序取前 N。成交额在 factor 表里是「亿元」口径
    amount_of = {s["code"]: float(s.get("amount") or 0.0) for s in universe}
    ranked = sorted((c for c in amount_of if amount_of[c] > 0),
                    key=lambda c: amount_of[c], reverse=True)
    codes = ranked[:max_codes]
    if not codes:
        codes = [s["code"] for s in universe[:max_codes]]
    panel.pool_size = len(codes)

    sym = {s["code"]: s for s in universe}
    panel.codes = codes
    panel.names = [sym[c].get("name") or c for c in codes]
    panel.industries = [sym[c].get("industry") or "其他" for c in codes]
    panel.st = [bool(sym[c].get("is_st")) for c in codes]
    panel.limit = [price_limit(c, bool(sym[c].get("is_st"))) for c in codes]

    calendar = engine.repo.trading_dates(days + WARMUP)
    if len(calendar) < WARMUP + 20:
        # 数据太短就别硬跑：算出来的指标没有统计意义，不如直接回空
        return panel
    pos = {d: i for i, d in enumerate(calendar)}
    start = max(0, len(calendar) - days)
    panel.dates = calendar[start:]
    n_cal = len(calendar)

    raw = engine.repo.series_bulk(codes, days + WARMUP)

    for ci, code in enumerate(codes):
        s = raw.get(code)
        bars = _code_bars(s, pos, n_cal, panel.limit[ci]) if s else None
        if bars is None:
            panel.bars.append(_empty_bars())
            continue
        # 只留模拟区间内的 K 线，但因子已经在全历史上算完（预热不白拿）
        panel.bars.append(_trim(bars, start))

    # 日期 → 代码 → bar 下标
    panel.bar = [[-1] * len(codes) for _ in range(len(panel.dates))]
    for ci, bars in enumerate(panel.bars):
        for p, d in enumerate(bars["date_idx"]):
            if 0 <= d < len(panel.dates):
                panel.bar[d][ci] = p

    # 当日是否有资格进候选：有 K 线、没有 ST、成交额过门槛
    panel.eligible = []
    for d in range(len(panel.dates)):
        row = []
        for ci in range(len(codes)):
            p = panel.bar[d][ci]
            ok = p >= 0 and not panel.st[ci] and panel.bars[ci]["amt"][p] >= min_amount
            row.append(ok)
        panel.eligible.append(row)

    _cross_section(panel)
    panel.bench = _bench_series(engine, len(panel.dates))
    return panel


def _bench_series(engine, days: int) -> list[tuple[str, float]]:
    """沪深300 的 (日期, 收盘)，比回测区间多取 60 根供均线预热。

    为什么要有这个：`avg_exposure` 一直在 73~80%，意味着策略全年满仓做多。
    基准这一年 −14.8%，满仓多头必然跟着亏——这跟选股好坏无关。得把
    「选股有没有超额」和「该不该满仓」拆开看：multifactor 满仓时超额 +6.6%
    却年化 −8.3%，问题在仓位，不在因子。

    取不到指数时返回空列表，上层据此不做干预，避免因为缺数据把回测变成空仓。
    """
    try:
        rows = engine.repo.index_daily("000300.SH", days + 60)
    except Exception:                                    # noqa: BLE001
        return []
    out = [(b["date"], float(b["close"])) for b in (rows or []) if b.get("close")]
    out.sort(key=lambda r: r[0])
    return out


def _regime_ok(panel, ma: int) -> list[bool] | None:
    """每个交易日「沪深300 是否在 `ma` 日线之上」。`ma<=0` 或没有指数数据返回 None。"""
    if ma <= 0 or not panel.bench:
        return None
    ok_by_date, window, acc = {}, [], 0.0
    for day, px in panel.bench:
        window.append(px)
        acc += px
        if len(window) > ma:
            acc -= window.pop(0)
        ok_by_date[day] = px >= acc / len(window)
    return [ok_by_date.get(d, True) for d in panel.dates]


def _empty_bars() -> dict:
    return {"date_idx": [], "o": [], "h": [], "l": [], "c": [], "amt": [],
            "pct": [], "atr": [], "f": {}}


def _code_bars(s: dict, pos: dict[str, int], n_cal: int,
               limit: float = 0.10) -> dict | None:
    """把一只票的日线摊到公共日历上，算好复权价、涨跌幅、ATR 与因子。

    `limit` 是该票的涨跌停幅度（主板 10% / 创业板·科创 20% / 北交所 30% /
    ST 5%），用来判断涨停与连板。复权价算出来的 pct 有零点几个百分点的偏差，
    所以判涨停用 `pct >= limit * 0.997` 而不是 `>= limit`——不留这点容差，
    除权日附近的涨停会被漏掉，连板数跟着断。
    """
    rows: list[tuple[int, float, float, float, float, float, float]] = []
    for i, day in enumerate(s["dates"]):
        j = pos.get(day.isoformat())
        if j is None:
            continue
        adj = s["adj"][i] or 1.0
        o = float(s["raw_open"][i]) * adj
        h = float(s["raw_high"][i]) * adj
        l = float(s["raw_low"][i]) * adj
        c = float(s["raw_close"][i]) * adj
        if c <= 0:
            continue
        rows.append((j, o, h, l, c, float(s["amount"][i] or 0.0),
                     float(s["main_net"][i] or 0.0)))
    if len(rows) < 25:
        return None

    idx = [r[0] for r in rows]
    o = [r[1] for r in rows]
    h = [r[2] for r in rows]
    l = [r[3] for r in rows]
    c = [r[4] for r in rows]
    amt = [r[5] for r in rows]
    main = [r[6] for r in rows]
    m = len(c)

    pct = [0.0] * m
    for i in range(1, m):
        pct[i] = (c[i] / c[i - 1] - 1) if c[i - 1] else 0.0

    f: dict[str, list[float]] = {}
    f["mom5"] = [_hold(c, i, 5) for i in range(m)]
    f["mom20"] = [_hold(c, i, 20) for i in range(m)]
    f["mom60"] = [_hold(c, i, 60) for i in range(m)]
    f["vol20"] = [_vol(c, i, 20) for i in range(m)]
    f["dist_high"] = [_dist_high(h, c, i) for i in range(m)]
    f["volratio"] = [_volratio(amt, i, 5) for i in range(m)]
    f["mainflow"] = [
        round(main[i] / amt[i] * 100, 4) if amt[i] > 0 else 0.0 for i in range(m)
    ]
    f["main_net"] = [round(main[i] / 1e8, 4) for i in range(m)]
    # 连板数：当日及之前连续涨停的天数。打板策略的唯一硬门槛——
    # 没有它，涨停股不够时策略会退化成「随便买几只普通股」。
    thr = limit * 0.997
    f["limit_cnt"] = [0.0] * m
    run = 0
    for i in range(m):
        run = run + 1 if pct[i] >= thr else 0
        f["limit_cnt"][i] = float(run)

    return {"date_idx": idx, "o": o, "h": h, "l": l, "c": c, "amt": amt,
            "pct": pct, "atr": _atr(h, l, c, 14), "f": f}


def _trim(bars: dict, start: int) -> dict:
    """按公共日历的模拟起点裁掉预热段。"""
    cut = [k for k, d in enumerate(bars["date_idx"]) if d >= start]
    if not cut:
        return _empty_bars()
    lo = cut[0]
    out = {
        "date_idx": [d - start for d in bars["date_idx"][lo:]],
        "o": bars["o"][lo:], "h": bars["h"][lo:], "l": bars["l"][lo:],
        "c": bars["c"][lo:], "amt": bars["amt"][lo:],
        "pct": bars["pct"][lo:], "atr": bars["atr"][lo:],
        "f": {k: v[lo:] for k, v in bars["f"].items()},
    }
    return out


def _hold(c: list[float], i: int, k: int) -> float:
    if i < k or not c[i - k]:
        return 0.0
    return round((c[i] / c[i - k] - 1) * 100, 4)


def _vol(c: list[float], i: int, n: int) -> float:
    if i < n:
        return 0.0
    rets = [c[j] / c[j - 1] - 1 for j in range(i - n + 1, i + 1) if c[j - 1]]
    if len(rets) < 3:
        return 0.0
    return round(statistics.pstdev(rets) * (TRADING_DAYS ** 0.5) * 100, 4)


def _dist_high(h: list[float], c: list[float], i: int) -> float:
    lo = max(0, i - 249)
    peak = max(h[lo:i + 1]) if h[lo:i + 1] else c[i]
    if peak <= 0:
        return 0.0
    return round((1 - c[i] / peak) * 100, 4)


def _volratio(amt: list[float], i: int, n: int) -> float:
    """量比用成交额算而不是成交量：复权会改成交量，成交额不受影响。"""
    if i < n:
        return 1.0
    base = sum(amt[i - n:i]) / n
    return round(amt[i] / base, 4) if base > 0 else 1.0


def _atr(h: list[float], l: list[float], c: list[float], n: int = 14) -> list[float]:
    m = len(c)
    out = [0.0] * m
    if m < n + 1:
        return out
    tr = [h[0] - l[0]]
    for i in range(1, m):
        tr.append(max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1])))
    prev = sum(tr[1:n + 1]) / n
    out[n] = prev
    for i in range(n + 1, m):
        prev = (prev * (n - 1) + tr[i]) / n
        out[i] = prev
    return out


def _cross_section(panel: Panel) -> None:
    """每个交易日、每个因子在**当日可交易池**上的 z 值。

    这一步与权重无关，所以只在装面板时算一次。遗传搜索每换一个基因组
    只做「z × w 求和」，不再重新标准化——144 次评估的速度全靠这个前提。
    """
    n_cal = len(panel.dates)
    for key, (_, direction) in FACTORS.items():
        per_day: list[list[float]] = [[0.0] * panel.ncodes for _ in range(n_cal)]
        hits = 0
        total = 0
        for d in range(n_cal):
            pool = [ci for ci in range(panel.ncodes)
                    if panel.eligible[d][ci] and panel.bar[d][ci] >= 0]
            if len(pool) < 5:
                continue
            vals = []
            for ci in pool:
                p = panel.bar[d][ci]
                vals.append(panel.bars[ci]["f"][key][p])
            total += len(pool)
            hits += sum(1 for v in vals if v != 0.0)
            mean = statistics.fmean(vals)
            sd = statistics.pstdev(vals) if len(vals) > 1 else 0.0
            row = per_day[d]
            if sd <= 0:
                continue
            for ci, v in zip(pool, vals):
                z = (v - mean) / sd
                row[ci] = max(-3.0, min(3.0, z)) * direction
        panel.z[key] = per_day
        panel.coverage[key] = round(hits / total, 4) if total else 0.0


# ----------------------------------------------------------------- 权重口径
def replayable_weights(weights: dict | None) -> tuple[dict[str, float], list[dict]]:
    """把 spec 的权重过滤成可回测的部分，并返回被摘掉的明细。

    摘掉之后必须重新归一：原权重 {vol20:0.34, dividend:0.30, roe:0.16, ep:0.12,
    mom60:0.08} 剩 vol20 与 mom60 两项，归一后是 0.81 / 0.19——如果不归一，
    综合分整体缩水到原来的 42%，那 top_n 的绝对阈值就全乱了。
    """
    raw = {k: float(v) for k, v in (weights or {}).items()
           if isinstance(v, (int, float)) and float(v) > 0}
    kept: dict[str, float] = {}
    dropped: list[dict] = []
    for key, weight in raw.items():
        if key in FACTORS:
            kept[key] = weight
            continue
        dropped.append({
            "key": key,
            # 中文名从字段词典取：前端不必再维护一份「ep 是盈利收益率」的映射
            "label": FIELD_MAP[key].label if key in FIELD_MAP else key,
            "weight": round(weight, 4),
            "reason": NO_HISTORY.get(key, "该字段没有历史序列，无法回放"),
        })
    total = sum(kept.values())
    if not total:
        return {}, dropped
    return {k: round(v / total, 6) for k, v in kept.items()}, dropped


# --------------------------------------------------------------------- 回测
def run_backtest(engine, spec: dict | None = None, panel: Panel | None = None,
                 *, days: int = 250, max_codes: int = 600,
                 capital: float | None = None, params: dict | None = None,
                 profile: dict | None = None) -> dict:
    """跑一遍回测。返回净值曲线、指标、成交流水与期末持仓。

    `params` 覆盖 DEFAULTS 里的调仓与止盈止损参数；遗传搜索就是在动它。
    """
    spec = spec or {}
    cfg = dict(DEFAULTS)
    # 策略自己声明的结构参数先落进 cfg，再让显式 params 覆盖。
    # 不这么做就会和进化基线打架：`harness.base_genome` 取的是
    # `spec.get("top_n") or DEFAULTS["top_n"]`（这条策略声明的是 10），
    # 而这里原来只看 DEFAULTS（8）。结果同一条策略的「回测」和
    # 「进化原始策略」两个数字并排挂在同一个抽屉里，用的却是两套参数。
    for key in ("top_n", "max_per_industry", "pool_limit_min"):
        value = spec.get(key)
        if value is not None:
            cfg[key] = value
    for key, value in (params or {}).items():
        if value is not None:
            cfg[key] = value
    capital = float(capital if capital is not None else cfg["capital"])
    cfg["capital"] = capital
    cfg["top_n"] = max(1, min(int(cfg["top_n"]), 20))
    cfg["max_per_industry"] = max(1, min(int(cfg["max_per_industry"]), 12))
    cfg["rebalance"] = max(1, min(int(cfg["rebalance"]), 20))
    cfg["time_stop"] = max(3, min(int(cfg["time_stop"]), 120))

    if panel is None:
        panel = build_panel(engine, days=days, max_codes=max_codes)

    weights, dropped = replayable_weights(spec.get("weights"))
    flow_note = None
    if "mainflow" in weights and panel.coverage.get("mainflow", 0.0) < FLOW_MIN_COVERAGE:
        flow_note = (f"资金流历史覆盖率仅 {panel.coverage.get('mainflow', 0.0) * 100:.0f}%，"
                     f"已摘掉主力资金类权重")
        dropped.extend([
            {"key": k, "label": FACTORS[k][0], "weight": round(weights.pop(k), 4),
             "reason": flow_note}
            for k in ("mainflow", "main_net") if k in weights
        ])
        total = sum(weights.values())
        weights = {k: round(v / total, 6) for k, v in weights.items()} if total else {}

    notes: list[str] = []
    if not weights:
        # 两种成因要分开说，混成一句会误导：一种是「写了权重但全被摘掉」
        # （纯估值策略），另一种是「压根没写权重」（纯条件筛选策略）。
        # 给兜底而不是空跑，但必须把「这不是你原来那条策略」说出来。
        weights = {"mom20": 1.0}
        if dropped:
            notes.append("该策略的权重全部依赖估值/质量字段，回测退化为单因子「20 日动量」")
        else:
            notes.append("该策略没有因子权重（只写了筛选条件），回测退化为单因子「20 日动量」")
    if flow_note:
        notes.append(flow_note)
    if dropped:
        notes.append("被摘掉的权重：" + "；".join(
            f"{d['key']} {d['weight']:.0%}（{d['reason']}）" for d in dropped))

    # 条件筛选在回测里同样跑不了，但两种原因不能混为一谈：PE / ROE / 股息率
    # 是**没有历史序列**，`above_ma20` 这类则是**算得出但回测不做条件过滤**。
    # 静默忽略最坏——用户会以为「我那条低估值策略回测过了」，
    # 实际回测的是「去掉所有估值条件之后的纯动量」。
    filter_fields = list(dict.fromkeys(
        str(f.get("field")) for f in (spec.get("filters") or [])
        if isinstance(f, dict) and f.get("field")))
    if filter_fields:
        hard = [f for f in filter_fields if f in NO_HISTORY]
        soft = [f for f in filter_fields if f not in NO_HISTORY]
        parts: list[str] = []
        if hard:
            parts.append("、".join(hard) + " 没有历史序列，库里只有当前截面")
        if soft:
            parts.append("、".join(soft) + " 属于条件过滤，回测只按因子综合分排序")
        notes.append(
            f"策略的 {len(filter_fields)} 条筛选条件在回测中未生效："
            + "；".join(parts)
            + "。回测实际跑的是「纯因子打分」版本，不是带条件过滤的那条策略"
        )
    if spec.get("sort"):
        notes.append("策略指定的排序字段在回测中不参与打分，排序一律按因子综合分")

    if panel.n == 0 or panel.ncodes == 0:
        return _empty_report(spec, cfg, "本地库的日线不足以支撑回测，先跑一次数据同步")

    prof = normalize_profile(None, profile)
    bench = _benchmark(engine, panel.dates)
    regime = _regime_ok(panel, int(cfg.get("market_ma") or 0))

    cash = capital
    positions: dict[int, dict] = {}
    trades: list[dict] = []
    equity: list[dict] = []
    pending: list[dict] = []
    turnover_amount = 0.0
    exposure_sum = 0.0
    seq = 0

    for d in range(panel.n):
        date = panel.dates[d]

        # ---- 1. 开盘：先成交上一交易日收盘后挂出的单子
        carry: list[dict] = []
        for order in pending:
            ci = order["ci"]
            p = panel.bar[d][ci]
            if p < 0:
                # 停牌：最多等三个交易日，再等下去这只票的区间早就失效了
                order["miss"] = order.get("miss", 0) + 1
                if order["miss"] < 3:
                    carry.append(order)
                continue
            bars = panel.bars[ci]
            o = bars["o"][p]
            if not _can_buy(bars, p, panel.limit[ci]) or ci in positions:
                continue
            equity_now = cash + _market_value(positions, panel, d)
            if equity_now <= 0:
                continue
            if (equity_now - cash) / equity_now > MAX_GROSS:
                continue
            weight = min(1.0 / cfg["top_n"], single_cap(cfg["top_n"]))
            budget = min(equity_now * weight, cash)
            fill = _slipped(o, "BUY", prof)
            shares = _shares(budget, fill, bars["amt"][p])
            if shares < 100:
                continue
            amount = shares * fill
            fee = trade_fee(amount, "BUY", prof)
            cost = amount + fee["total"]
            if cost > cash:
                continue
            cash -= cost
            atr = bars["atr"][p] or (fill * 0.02)
            positions[ci] = {
                "code": panel.codes[ci], "shares": shares, "cost": fill,
                "d0": d, "stop": fill - cfg["stop_atr"] * atr,
                "tp1": fill + cfg["tp1_atr"] * atr,
                "tp2": fill + cfg["tp2_atr"] * atr,
                "tp1_done": False, "last": fill, "atr": atr,
                "peak": fill,
            }
            seq += 1
            turnover_amount += amount
            trades.append({
                "id": f"B{seq:05d}", "code": panel.codes[ci], "name": panel.names[ci],
                "side": "BUY", "date": date, "price": round(fill, 3),
                "shares": shares, "amount": round(amount, 2),
                "fee": fee["total"], "tax": 0.0, "pnl": None, "pnl_pct": None,
                "hold_days": 0, "reason": order.get("reason", "信号建仓"),
            })
        pending = carry

        # ---- 2. 开盘：时间止损（按开盘价走）
        for ci in list(positions):
            pos = positions[ci]
            p = panel.bar[d][ci]
            if p < 0:
                continue
            if d - pos["d0"] >= cfg["time_stop"]:
                bars = panel.bars[ci]
                if _can_sell(bars, p, panel.limit[ci]):
                    cash, rec = _sell(panel, ci, pos, d, bars["o"][p], prof,
                                      cash, "时间止损", seq)
                    seq += 1
                    turnover_amount += rec["amount"]
                    trades.append(rec)
                    positions.pop(ci, None)

        # ---- 3. 盘中：止损优先于止盈（同一根 K 线两边都碰到时按坏的算）
        for ci in list(positions):
            pos = positions[ci]
            p = panel.bar[d][ci]
            if p < 0:
                continue
            bars = panel.bars[ci]
            o, hi, lo, close = bars["o"][p], bars["h"][p], bars["l"][p], bars["c"][p]
            pos["last"] = close
            pos["peak"] = max(pos["peak"], hi)

            if lo <= pos["stop"] and _can_sell(bars, p, panel.limit[ci]):
                fill = min(pos["stop"], o) if o < pos["stop"] else pos["stop"]
                # TP1 之后止损已经上移到成本价，这笔记成「保本止损」而不是「止损」：
                # 两者的盈亏含义完全不同，混在一起看会误判胜率与盈亏比。
                reason = "保本止损" if pos["tp1_done"] and pos["stop"] >= pos["cost"] else "止损"
                cash, rec = _sell(panel, ci, pos, d, fill, prof, cash, reason, seq)
                seq += 1
                turnover_amount += rec["amount"]
                trades.append(rec)
                positions.pop(ci, None)
                continue

            if not pos["tp1_done"] and hi >= pos["tp1"]:
                fill = o if o >= pos["tp1"] else pos["tp1"]
                part = max(int(pos["shares"] * 0.5 / 100) * 100, 100)
                if part < pos["shares"]:
                    cash, rec = _sell(panel, ci, pos, d, fill, prof, cash, "TP1 减半",
                                      seq, shares=part)
                    seq += 1
                    turnover_amount += rec["amount"]
                    trades.append(rec)
                    pos["shares"] -= part
                    pos["tp1_done"] = True
                    # TP1 减半后剩余仓位止损上移到成本价。不上移的话，「吃了 TP1
                    # 再跌回原点」是 半仓赚 2.6ATR + 半仓亏 2.2ATR = 净赚 0.2ATR，
                    # 而一次纯亏是 −2.2ATR——盈利被削成零头、亏损全额承担，
                    # 盈亏比被锁死在 1 以下（实测 momentum 0.82），胜率 51% 也必亏。
                    # 上移之后同一段行情最差是 +1.3ATR，盈亏比才可能过 1。
                    if cfg.get("breakeven_after_tp1", 1):
                        pos["stop"] = max(pos["stop"], pos["cost"])
                    continue
            if pos["tp1_done"] and hi >= pos["tp2"]:
                fill = o if o >= pos["tp2"] else pos["tp2"]
                cash, rec = _sell(panel, ci, pos, d, fill, prof, cash, "TP2 清仓", seq)
                seq += 1
                turnover_amount += rec["amount"]
                trades.append(rec)
                positions.pop(ci, None)

        # ---- 4. 收盘：生成下一交易日的委托（信号只用当日及之前的数据）
        #
        # 每天扫一次，只补**空出来的仓位**，不为了换股而换股。
        # 初版是「每 rebalance 天才扫一次」，结果账户在两次扫描之间会从
        # 满仓一路衰减到空仓：止损打掉一只就少一只，要等下一次扫描才补。
        # 一条 top_n=7 的策略因此平均仓位只有 25%，回撤被「钱没买满」压到
        # 很小、Calmar 刷到 5 以上——那不是选股好，是账户经常空着。
        # 现在按「持仓上限 − 现有持仓」算空缺，和模拟盘完全一致。
        if d < panel.n - 1:
            # 择时开关：大盘不在均线之上就不开新仓（已有持仓照常按止损/止盈走）。
            # `market_ma=0` 或没有指数数据时 `regime` 为 None，完全不干预。
            regime_ok = regime is None or (d < len(regime) and regime[d])
            slots = max(0, cfg["top_n"] - len(positions)) if regime_ok else 0
            if slots:
                held = set(positions)
                picks = _select(panel, d, weights, cfg, held, slots)
                for ci, score in picks:
                    pending.append({"ci": ci, "score": score, "reason": "信号建仓"})

        # ---- 5. 收盘净值
        value = cash + _market_value(positions, panel, d)
        equity.append({"date": date, "value": round(value, 2),
                       "benchmark": bench.get(date)})
        # 仓位（股票市值 / 总净值）：Calmar 对低仓位有系统性偏袒，
        # 不把这个数报出来，看到「回撤 3%」的人会以为策略很稳，
        # 实际上是常年半仓，另外一半钱什么都没干。
        if value > 0:
            exposure_sum += (value - cash) / value

    return _report(panel, spec, cfg, weights, dropped, notes, equity, trades,
                   positions, cash, capital, turnover_amount, exposure_sum)


# --------------------------------------------------------------- 回测零件
def _slipped(price: float, side: str, prof: dict) -> float:
    bps = float(prof.get("slippage_bps") or 0.0) / 10000.0
    return price * (1 + bps) if side == "BUY" else price * (1 - bps)


def _shares(budget: float, price: float, amount_today: float) -> int:
    if price <= 0 or budget <= 0:
        return 0
    cap = int(amount_today * MAX_AMOUNT_SHARE / price / 100) * 100
    want = int(budget / price / 100) * 100
    return max(0, min(want, cap))


def _gap(bars: dict, p: int) -> float:
    """开盘跳空幅度：开盘价相对上一根 K 线收盘。判断一字板靠它而不是当日涨跌幅。"""
    if p <= 0:
        return 0.0
    prev = bars["c"][p - 1]
    return (bars["o"][p] / prev - 1) if prev else 0.0


def _can_buy(bars: dict, p: int, limit: float) -> bool:
    """开盘一字涨停买不到：跳空到涨停价开盘的票，集合竞价是排不上的。"""
    return _gap(bars, p) < limit - 0.002


def _can_sell(bars: dict, p: int, limit: float) -> bool:
    """开盘一字跌停卖不掉。"""
    return _gap(bars, p) > -(limit - 0.002)


def _market_value(positions: dict, panel: Panel, d: int) -> float:
    value = 0.0
    for ci, pos in positions.items():
        p = panel.bar[d][ci]
        price = panel.bars[ci]["c"][p] if p >= 0 else pos["last"]
        pos["last"] = price
        value += pos["shares"] * price
    return value


def _sell(panel: Panel, ci: int, pos: dict, d: int, price: float,
          prof: dict, cash: float, reason: str, seq: int,
          shares: int | None = None) -> tuple[float, dict]:
    qty = pos["shares"] if shares is None else min(shares, pos["shares"])
    fill = _slipped(price, "SELL", prof)
    amount = qty * fill
    fee = trade_fee(amount, "SELL", prof)
    cash += amount - fee["total"]
    pnl = (fill - pos["cost"]) * qty
    return cash, {
        "id": f"S{seq:05d}", "code": pos["code"], "name": panel.names[ci],
        "side": "SELL", "date": panel.dates[d], "price": round(fill, 3),
        "shares": qty, "amount": round(amount, 2),
        "fee": fee["total"], "tax": fee["stamp"],
        "pnl": round(pnl, 2),
        "pnl_pct": round((fill / pos["cost"] - 1) * 100, 2) if pos["cost"] else 0.0,
        "hold_days": d - pos["d0"], "reason": reason,
    }


def _select(panel: Panel, d: int, weights: dict[str, float], cfg: dict,
            held: set[int], limit: int) -> list[tuple[int, float]]:
    """当日横截面打分 → 行业分散 → 取前 `limit` 个（已持有的不重复买）。

    `cfg["pool_limit_min"]` > 0 时先过一道硬门槛：只在连板数 ≥ 该值的票里选。
    打板策略必须靠这道门槛——单纯把连板数当因子加权，在涨停股少于 top_n 的
    日子会「凑数」买进一串连板数为 0 的普通股，那不是策略跑偏，是根本没在打板。
    """
    if limit <= 0:
        return []
    row_ok = panel.eligible[d]
    zs = {k: panel.z[k][d] for k in weights if k in panel.z}
    if not zs:
        return []
    pool_min = float(cfg.get("pool_limit_min") or 0.0)
    scored: list[tuple[int, float]] = []
    for ci in range(panel.ncodes):
        if not row_ok[ci] or ci in held:
            continue
        if pool_min > 0:
            p = panel.bar[d][ci]
            col = panel.bars[ci]["f"].get("limit_cnt") if p >= 0 else None
            if not col or col[p] < pool_min:
                continue
        score = 0.0
        for key, w in weights.items():
            col = zs.get(key)
            if col is not None:
                score += col[ci] * w
        scored.append((ci, score))
    if not scored:
        return []
    scored.sort(key=lambda r: r[1], reverse=True)

    picked: list[tuple[int, float]] = []
    count: dict[str, int] = {}
    for ci, score in scored:
        ind = panel.industries[ci]
        if count.get(ind, 0) >= cfg["max_per_industry"]:
            continue
        picked.append((ci, score))
        count[ind] = count.get(ind, 0) + 1
        if len(picked) >= limit:
            break
    return picked



def _benchmark(engine, dates: list[str]) -> dict[str, float]:
    """基准：沪深 300 归一化到 100。取不到就返回空，前端不画这条线。"""
    try:
        series = engine.index_series("000300.SH")
    except Exception:                                       # noqa: BLE001
        return {}
    if not series or not series.dates:
        return {}
    by_date = {d.isoformat(): c for d, c in zip(series.dates, series.raw_close)}
    base = next((by_date[d] for d in dates if by_date.get(d)), None)
    if not base:
        return {}
    out: dict[str, float] = {}
    for d in dates:
        v = by_date.get(d)
        if v:
            out[d] = round(v / base * 100, 2)
    return out


def _trade_stats(trades: list[dict]) -> dict:
    closed = [t for t in trades if t["side"] == "SELL" and t.get("pnl") is not None]
    wins = [t for t in closed if t["pnl"] > 0]
    losses = [t for t in closed if t["pnl"] <= 0]
    gross_win = sum(t["pnl"] for t in wins)
    gross_loss = abs(sum(t["pnl"] for t in losses))
    avg_hold = statistics.fmean([t["hold_days"] for t in closed]) if closed else 0.0
    return {
        "trade_count": len(trades),
        "closed_count": len(closed),
        "win_count": len(wins),
        "loss_count": len(losses),
        "win_rate": round(len(wins) / len(closed) * 100, 2) if closed else 0.0,
        "avg_win": round(gross_win / len(wins), 2) if wins else 0.0,
        "avg_loss": round(-gross_loss / len(losses), 2) if losses else 0.0,
        "profit_factor": round(gross_win / gross_loss, 2) if gross_loss else 0.0,
        "avg_hold_days": round(avg_hold, 1),
        "realized_pnl": round(sum(t["pnl"] for t in closed), 2),
        "total_fee": round(sum(t["fee"] for t in trades), 2),
        "total_tax": round(sum(t["tax"] for t in trades), 2),
    }


def _empty_report(spec: dict, cfg: dict, msg: str) -> dict:
    return {
        "strategy_id": spec.get("id") or "custom",
        "strategy_name": spec.get("name") or "自定义策略",
        "period": {"start": None, "end": None, "days": 0},
        "universe": {"pool": 0, "universe_size": 0, "min_amount": 0, "note": msg},
        "params": cfg,
        "weights": {},
        "dropped": [],
        "metrics": _trade_stats([]),
        "equity": [],
        "trades": [],
        "positions": [],
        "notes": [msg],
        "available": False,
    }


def _report(panel: Panel, spec: dict, cfg: dict, weights: dict, dropped: list,
            notes: list[str], equity: list[dict], trades: list[dict],
            positions: dict, cash: float, capital: float,
            turnover_amount: float, exposure_sum: float = 0.0) -> dict:
    values = [e["value"] for e in equity] or [capital]
    perf = summarize(values)
    stats = _trade_stats(trades)
    avg_equity = statistics.fmean(values) if values else capital
    turnover = round(turnover_amount / avg_equity, 2) if avg_equity else 0.0
    avg_exposure = round(exposure_sum / len(equity), 4) if equity else 0.0

    bench = [e["benchmark"] for e in equity if e.get("benchmark")]
    bench_return = round(bench[-1] / 100 - 1, 4) if len(bench) >= 2 else None

    holdings = []
    d_last = panel.n - 1
    for ci, pos in positions.items():
        p = panel.bar[d_last][ci]
        price = panel.bars[ci]["c"][p] if p >= 0 else pos["last"]
        holdings.append({
            "code": pos["code"], "name": panel.names[ci],
            "industry": panel.industries[ci], "shares": pos["shares"],
            "cost": round(pos["cost"], 3), "price": round(price, 3),
            "pnl": round((price - pos["cost"]) * pos["shares"], 2),
            "pnl_pct": round((price / pos["cost"] - 1) * 100, 2) if pos["cost"] else 0.0,
            "stop": round(pos["stop"], 3), "tp1": round(pos["tp1"], 3),
            "tp2": round(pos["tp2"], 3), "tp1_done": pos["tp1_done"],
            "hold_days": d_last - pos["d0"],
        })
    holdings.sort(key=lambda r: r["pnl"], reverse=True)

    notes = list(notes)
    notes.append(f"股票池：按最新成交额取前 {panel.pool_size} 只（全市场 {panel.universe_size} 只）；"
                 f"当日成交额低于 {panel.min_amount / 1e4:.0f} 万的不进候选")
    notes.append("成交价 = 信号次日开盘价（含 5‱ 滑点），不用收盘价成交，避免未来函数")
    notes.append(f"每天扫描空缺仓位并补足到 top_n={cfg['top_n']}，不为了换股而换股："
                 f"已持有的标的不会因为排名下滑被卖，只会被止损/止盈/时间止损带走")
    notes.append("行业分类与 ST 标记为当前值，不是历史值；现金按当日可用处理，未模拟 T+1 交收")
    if avg_exposure < 0.6:
        notes.append(
            f"平均仓位仅 {avg_exposure:.0%}，主要原因有两个：一是 TP1 减半之后腾出来的"
            f"现金不会立刻补新仓，要等某个标的整只退出才有空位（`top_n={cfg['top_n']}`，"
            f"单票上限 {single_cap(cfg['top_n']):.0%}，理论满仓 "
            f"{min(1.0, cfg['top_n'] * single_cap(cfg['top_n'])):.0%}）；"
            f"二是这一段的信号数本来就不够填满仓位。"
            f"回撤浅有一部分来自「钱没买满」，不是选股更稳")

    return {
        "strategy_id": spec.get("id") or "custom",
        "strategy_name": spec.get("name") or "自定义策略",
        "available": True,
        "model_version": MODEL_VERSION,
        "as_of": panel.trade_date,
        "period": {"start": panel.dates[0], "end": panel.dates[-1],
                   "days": panel.n},
        "universe": {
            "pool": panel.pool_size, "universe_size": panel.universe_size,
            "min_amount": panel.min_amount,
            "note": f"按最新成交额取前 {panel.pool_size} 只",
        },
        "params": {
            "top_n": cfg["top_n"], "max_per_industry": cfg["max_per_industry"],
            "stop_atr": cfg["stop_atr"], "tp1_atr": cfg["tp1_atr"],
            "tp2_atr": cfg["tp2_atr"], "time_stop": cfg["time_stop"],
            "rebalance": cfg["rebalance"], "capital": cfg["capital"],
        },
        "weights": weights,
        "dropped": dropped,
        "metrics": {
            **perf,
            **stats,
            "turnover": turnover,
            "avg_exposure": avg_exposure,
            "final_nav": round(values[-1], 2),
            "trade_total": len(trades),
            "benchmark_return": bench_return,
            "excess_return": (round(perf["total_return"] - bench_return, 4)
                              if bench_return is not None else None),
            "fill_rule": "next_open",
        },
        # 曲线超过 260 点就抽稀：前端画的是走势，不是逐日对账
        "equity": _thin(equity, 260),
        "trades": trades[-120:],
        "positions": holdings,
        "notes": notes,
    }


def _thin(rows: list[dict], limit: int) -> list[dict]:
    if len(rows) <= limit:
        return rows
    step = math.ceil(len(rows) / limit)
    out = rows[::step]
    if out[-1] is not rows[-1]:
        out.append(rows[-1])
    return out
