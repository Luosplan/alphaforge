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

from .conditions import ConditionError, evaluate, normalize, normalize_all
from .gate import board_allow, buyable

# 减仓阈值与模拟盘共用一处定义。写在 `signal.py` 里是因为「浮盈多少算该减」
# 首先是**计划卡**的语言（卡片上那句「减仓」就是它触发的），回测与模拟盘
# 只是照着卡执行。两个引擎各抄一个数，某天只改一边，同一只票在实验室里
# 会「减仓」、在回测里继续拿着——这种分叉比阈值本身取多少重要得多。
from .signal import TRIM_PNL_PCT

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
# v6：条件过滤（filters）真正生效。此前回测只是**提示一句「条件未生效」**
# 然后按纯因子打分跑，于是「带 6 条硬过滤的埋伏策略」在回测里跑的其实是
# 「没有一条过滤的动量策略」——回测好看、实盘选不出票的根源就在这里。
# 这是选股口径的变化，不是显示层的调整。
# v7：三处口径同时改，旧数字全部不可比：
#     ① 股票池改为「区间日均成交额」（原来按最新成交额取，含前视偏差）；
#     ② 择时层正式生效（沪深300 在 120 日线下方不开新仓）；
#     ③ `market_ma` 改为读 spec，与模拟盘/今日选股统一。
#     ①让收益普遍下降、②让回撤普遍收窄，两者方向相反、幅度都不小，
#     混在一起看只会得到一堆互相矛盾的结论。必须换版本号，让旧存档作废。
# v8：已持仓的票也按计划卡执行「加仓 / 减仓」。
# 此前只有「建仓」一种动作：已持有的票即便再次进入 Top-N 也只是躺着，
# 浮盈 30% 也不会减一股。这一版把 `paper._held_action` 的口径搬进来——
# 同一只票再进 Top-N 就补到计划仓位（受单票上限与现金约束），浮盈到
# 12% 就减 1/3（TP1 已减半的不再减）。
# 它直接改变持仓结构、平均仓位、换手与每一笔盈亏，必须算新版本：
# v7 存下来的回测与 v8 并排显示会让人以为策略没变而成绩变了。
# v9：删掉两条没有 edge 的策略（强势回踩 / 打板），趋势波段补上量能与中期动量
# 门槛（命中数 4956 → 33），并把默认出票上限从 8 收到 3。
# 选股口径变了，回测与模拟盘的结果都不可比，必须换版本号。
# v10：择时从「跌破半年线就停手」改成「弱势只打抱团 + 半仓」，并给建仓加开盘
# 跳空校验（高开 >5% 不追、低开 >6% 不接，按策略可关）。上一版在指数长期
# 站不回去时会把账户彻底锁死——实测三个月零成交，自进化闭环直接空转。
# 这一版同时改变建仓机会与每笔仓位大小，历史数字全部不可比。
# v11：建仓当日**不许卖**（T+1）。上一版回测在第 3 步就跑止损，而建仓发生
# 在第 2 步——「早上买进、下午打穿止损离场」在回测里成立，实盘挂不出那张
# 卖单。模拟盘因为「先止损、后撮合」的顺序天然合规，于是同一个策略在两边
# 走出了不同的账。补上这一条，回测的亏损尾部和换手都会变，必须再换版本号。
# v12：新增「弱转强」买点（v11 及以前的策略集合里没有这一类），并给建仓加
# 「跳空区间带」与「当日未封板次日离场」两条按策略生效的规则；`factor` 表
# 新增 `failed_limit`（烂板）一列。选股口径变了，旧回测全部不可比。
# v13：策略集收敛为「打板 + 波段」两条，弱转强不再是独立策略，而是打板的
# 出手时机。打板改成从**今日涨停池**按连板数取前 3，不再用「昨日烂板/放量
# 滞涨」定义买点——那是低吸的语义，不是打板。同时打板不再受大盘位置约束
# （market_ma=0），出手沿用高开 1%~7% 区间与「当日不封板次日走」的纪律。
# 选股池与撮合条件同时变，v12 及以前的存档与进化结论全部不可比。
MODEL_VERSION = 13


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
    # 埋伏型因子。前六个是「已经涨了多少」，这三个是「蓄势到什么程度」——
    # 只有后者能把买点往前挪。放进 FACTORS 是为了让遗传搜索也能调它们的
    # 权重，否则进化永远只能在追涨的方向上打转。
    "squeeze": ("缩量收窄", -1),
    "ma_converge": ("均线粘合", -1),
    "pullback5": ("距 5 日高回撤", -1),
    # 板块联动。`ind_limit_prev` 不进这里：它只用来判断「昨天还没联动」，
    # 是布尔语义的开关，当成连续因子加权会把「刚启动」和「已经涨三天」混在一起。
    "ind_limit_cnt": ("同行业涨停家数", 1),
    "ind_strength": ("行业当日均涨", 1),
    "ind_rank_pct": ("行业内排名分位", -1),
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
    # 出票上限。一条策略一天 1~3 只：再多就不是「选出来的」，
    # 而是「排在前面的」——名次靠后的那几个，胜率和前几名不是一个东西。
    "top_n": 3,
    "max_per_industry": 2,
    "stop_atr": 2.2,      # 止损 = 成交价 − 2.2 × ATR(14)
    "tp1_atr": 2.6,       # 第一目标 = 成交价 + 2.6 × ATR，减半
    "tp2_atr": 5.0,       # 第二目标 = 成交价 + 5.0 × ATR，清剩余
    "time_stop": 20,      # 持有 20 个交易日仍未触发任何一档 → 时间止损
    # TP1 减半后是否把剩余仓位的止损上移到成本价。默认开。
    # 它不是可搜索项（不在 `paper.PARAM_KEYS` / `harness.BOUNDS` 里）——
    # 这是修正一处撮合缺陷，不是又一个待调参数。留这个键是为了 A/B 对照。
    "breakeven_after_tp1": 1,
    # 择时：0 = 一直满仓；120 = 沪深300 在半年线之上才开新仓。
    #
    # 默认从 0 改成 120 是有实测依据的，不是拍的：5 年样本（2021-09~2026-11，
    # 沪深300 −17.7%）上，同一批策略不开择时全都在熊段亏光；加上「跌破半年线
    # 不再开新仓」之后，缩量蓄势与趋势波段在三个子区间（两熊一牛）全部为正。
    # 20/60 日线太短会反复穿越，200 日线太钝，120 是中间的稳妥值。
    #
    # 它不进基因组——择时管的是「该不该做」而不是「做什么」，和选股参数一起搜
    # 等于让搜索去挑一段行情的运气。
    "market_ma": 120,
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

# 弱势（沪深300 在半年线下方）时的建仓系数。0.5 = 半仓，0 = 完全不开新仓。
#
# 历史：这里先是 0.5（无差别半仓）→ 实测更差（熊段半仓照样吃满跌幅，
# 回撤 -40%+）→ 改成 0（完全停手），回撤普降 10~18 个点。
#
# 但 0 有个死锁：指数一旦跌破半年线并长期站不回去，账户就**一笔都不会成交**。
# 实测 2026-08-17 跌破之后三个月零成交，三个模拟账户净值恒等于初始资金——
# 没有成交就没有复盘素材，自进化闭环的第三环直接空转。
#
# 现在回到 0.5，但**不是**简单回退：弱势时再加一道「抱团准入」
# （见 REGIME_HUDDLE_*）。旧结论针对的是「无差别半仓」，那确实吃满跌幅；
# 现在只买行业里正在抱团的那几只，等于把「该不该参与」交给题材强度判断，
# 而不是交给指数位置一刀切。这个组合是重新跑过 1260 天验证的。
REGIME_SCALE = 0.5

# 弱势时的抱团准入：行业当日涨停家数 ≥ 该值，或行业涨幅排名进前 N%。
#
# 用「或」不用「且」：抱团有两种形态——一种是行业里出现涨停潮（题材爆了），
# 另一种是行业整体走强但还没出涨停（资金在持续流入）。只认前一种会漏掉
# 后者，只认后一种会把刚启动的题材判成「排名还不够靠前」。
# 数据来自 `factor` 表的 ind_limit_cnt / ind_rank_pct，回测面板里装了同一份，
# 所以这条规则在回测和模拟盘上是同一段判断。
#
# 门槛必须够严：弱市里「有点热度」的行业满地都是，松一档就等于把跟风票
# 全放进来。1260 天同口径实测（sector，v10）：2 家/20% 卡玛 0.50，
# 3 家/8% 仍在 0.6 以下，5 家/5% 才是 1.17。宁可空仓，不捡跟风。
REGIME_HUDDLE_MIN_LIMITS = 5
REGIME_HUDDLE_MAX_RANK = 5.0

# 开盘校验：跳空幅度 = 开盘价 / 昨收 − 1。
#
# 开盘价就是 9:25 集合竞价的撮合结果，跳空幅度已经把竞价信息全含在里面了——
# 不需要另接竞价明细（历史也拿不到，接了就会变成「回测验不了、只有模拟盘在跑」
# 的两套口径）。这两个数管的是「选出来的票，第二天开盘还值不值得买」：
#   - 高开超过 GAP_MAX_PCT：情绪已经透支，追进去是给别人抬轿，放弃；
#   - 低开超过 GAP_MIN_PCT：跳空破位，说明昨天的选股信号已被证伪，不接；
#   - 两者之间：正常成交，低开反而给了更低的成本。
#
# 阈值取 5/6 而不是 3/4：1260 天样本上（同口径、只开主板）3/4 对「缩量蓄势」
# 的卡玛是 0.71，5/6 是 0.81，无闸是 0.79——闸太紧会把「小幅高开但仍在启动位」
# 的好票一起挡掉，只剩下一堆弱信号。闸的作用是拦极端，不是拦波动。
GAP_MAX_PCT = 5.0
GAP_MIN_PCT = -6.0

# 一次减仓卖多少：1/3。取 1/3 而不是 1/2 是因为 TP1 已经会减半，两档加起来
# 最多砍掉 2/3，剩下的仓位还跟得住趋势。模拟盘从这里取，不另存一份。
TRIM_FRAC = 1 / 3

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
                min_amount: float = RISK_LIMITS["min_amount"], force: bool = False,
                boards: dict | None = None) -> Panel:
    """装面板。同一交易日 + 同样参数复用上一次的结果。

    `boards` 是购买权限：回测必须和模拟盘用同一个池子，否则「回测里能买的」
    和「账户里能买的」是两批票，两条净值不可比。
    """
    trade_date = engine.repo.trade_date() or ""
    allow = board_allow(boards)
    key = (trade_date, int(days), int(max_codes), float(min_amount),
           tuple(sorted(allow.items())) if allow is not None else None)
    if not force:
        hit = _PANEL_CACHE.get(key)
        if hit is not None:
            return hit

    with _PANEL_LOCK:
        if not force:
            hit = _PANEL_CACHE.get(key)
            if hit is not None:
                return hit
        panel = _build(engine, days, max_codes, min_amount, allow)
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
    # 基准必须一起带过去：`_regime_ok` 靠它判断牛熊，不复制的话切片面板里
    # `panel.bench` 是空列表，择时静默失效——样本外验证跑出来的就成了
    # 「没有择时」的成绩，和全样本口径不一致。
    sub.bench = panel.bench
    return sub


def _build(engine, days: int, max_codes: int, min_amount: float,
           allow: dict[str, bool] | None = None) -> Panel:
    panel = Panel()
    panel.min_amount = min_amount
    panel.trade_date = engine.repo.trade_date() or ""

    universe = engine.universe()
    if allow is not None:
        # 「全市场 5578 只」在只买主板时是假的，按可买的算才对得上选股页的数字
        universe = [s for s in universe if buyable(s["code"], allow)]
    panel.universe_size = len(universe)
    if not universe:
        return panel

    calendar = engine.repo.trading_dates(days + WARMUP)
    if len(calendar) < WARMUP + 20:
        # 数据太短就别硬跑：算出来的指标没有统计意义，不如直接回空
        return panel
    pos = {d: i for i, d in enumerate(calendar)}
    start = max(0, len(calendar) - days)
    panel.dates = calendar[start:]
    n_cal = len(calendar)

    if allow is None:
        codes = _pool_codes(engine, panel.dates[0], max_codes)
    else:
        # SQL 的 LIMIT 排在筛板块之前，直接筛完只剩一半池子（主板约占六成），
        # 所以先多取几倍再截到 max_codes——池子大小不变，才和不过滤时可比
        raw = _pool_codes(engine, panel.dates[0], max_codes * 3)
        codes = [c for c in raw if buyable(c, allow)][:max_codes]
    panel.pool_size = len(codes)

    sym = {s["code"]: s for s in universe}
    panel.codes = codes
    panel.names = [sym[c].get("name") or c for c in codes]
    panel.industries = [sym[c].get("industry") or "其他" for c in codes]
    panel.st = [bool(sym[c].get("is_st")) for c in codes]
    panel.limit = [price_limit(c, bool(sym[c].get("is_st"))) for c in codes]

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

    # 先补行业联动（跨票聚合），再算 z 值——顺序不能反：z 值要标准化
    # 的就是联动因子本身，先算 z 会把它们当成恒为 0 的常数。
    _fill_industry(panel)
    _cross_section(panel)
    panel.bench = _bench_series(engine, len(panel.dates))
    return panel


def _pool_codes(engine, since: str, max_codes: int) -> list[str]:
    """历史股票池：**区间内日均成交额**前 N。

    老口径是「按最新成交额取前 N」，等于用今天的信息决定五年前能买什么：
    最近被炒起来、成交额冲上来的票会自动进池，池子整体被前视偏差抬高，
    回测自然好看、实盘必然落差。改成区间日均额后，这层偏差基本消掉。

    严格口径应该是「当日成交额排名」，但那样每天都要在全 A 里重排，
    面板要装 5000 多只票的历史，内存撑不住。日均额是折中：
    池子固定，逐日的流动性门槛仍由 `eligible`（amt >= min_amount）把守。
    """
    try:
        rows = engine.repo.conn.execute(
            "SELECT code, AVG(amount) AS a FROM daily "
            "WHERE date >= ? AND amount > 0 GROUP BY code "
            "HAVING a > 0 ORDER BY a DESC LIMIT ?",
            (since, int(max_codes)),
        ).fetchall()
    except Exception:                                       # noqa: BLE001
        rows = []
    if rows:
        return [r[0] for r in rows]
    # 兜底：daily 还没同步出历史时，退回最新快照排序（成交额是「亿元」口径）
    amount_of = {s["code"]: float(s.get("amount") or 0.0) for s in engine.universe()}
    ranked = sorted((c for c in amount_of if amount_of[c] > 0),
                    key=lambda c: amount_of[c], reverse=True)
    return ranked[:max_codes] or [s["code"] for s in engine.universe()[:max_codes]]


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
    # 蓄势与回踩：口径与 `alphaforge_data.factors.compute` 逐列对齐。
    # 两处必须同步改——选股读的是库里那份，回测算的是这里这份，
    # 一旦口径分叉，同一条策略在「今日选股」和「回测」里会是两只票。
    f["squeeze"] = [_squeeze(h, l, c, i) for i in range(m)]
    f["ma_converge"] = [_converge(c, i) for i in range(m)]
    f["dev_ma5"] = [_dev_ma(c, i, 5) for i in range(m)]
    f["dev_ma10"] = [_dev_ma(c, i, 10) for i in range(m)]
    f["pullback5"] = [_pullback(h, c, i, 5) for i in range(m)]
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


def _ma(c: list[float], i: int, n: int) -> float:
    if i + 1 < n:
        return 0.0
    window = c[i - n + 1:i + 1]
    return sum(window) / n if window else 0.0


def _amp_mean(h: list[float], l: list[float], c: list[float],
              i: int, window: int, offset: int = 0) -> float:
    end = i + 1 - offset
    start = max(end - window, 0)
    vals = []
    for j in range(start, end):
        prev = c[j - 1] if j > 0 and c[j - 1] else c[j]
        if prev > 0:
            vals.append((h[j] - l[j]) / prev * 100)
    return sum(vals) / len(vals) if vals else 0.0


def _squeeze(h: list[float], l: list[float], c: list[float], i: int) -> float:
    """近 5 日振幅 ÷ 前 20 日振幅 ×100。

    分子分母都**不含当日**（offset=1 / 6）：蓄势描述的是「启动之前」的状态，
    把启动当天算进去的话，放量突破当天振幅放大，squeeze 立刻失去信号，
    与 `volratio ≥ 1.4` 天然互斥（实测叠加后全市场只剩 1 只）。
    """
    if i < 26:
        return 100.0
    base = _amp_mean(h, l, c, i, 20, offset=6)
    return round(_amp_mean(h, l, c, i, 5, offset=1) / base * 100, 4) if base > 0 else 100.0


def _converge(c: list[float], i: int) -> float:
    """MA5/MA10/MA20 的最大间距占价格比例。低于 3 表示三线粘合。"""
    if i < 19:
        return 0.0
    tripod = [_ma(c, i, n) for n in (5, 10, 20)]
    low = min(tripod)
    return round((max(tripod) / low - 1) * 100, 4) if low > 0 else 0.0


def _dev_ma(c: list[float], i: int, n: int) -> float:
    ma = _ma(c, i, n)
    return round((c[i] / ma - 1) * 100, 4) if ma > 0 else 0.0


def _pullback(h: list[float], c: list[float], i: int, n: int) -> float:
    peak = max(h[max(0, i - n + 1):i + 1]) if h else c[i]
    return round((peak - c[i]) / peak * 100, 4) if peak > 0 else 0.0


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


_INDUSTRY_KEYS: tuple[str, ...] = (
    "ind_limit_cnt", "ind_limit_prev", "ind_strength", "ind_rank_pct",
)


def _fill_industry(panel: Panel) -> None:
    """按「交易日 × 行业」回填联动因子，就地写进各票的因子列。

    为什么单独一遍而不是放进 `_code_bars`：那个函数只看得见一只票，
    而「同行业有几家涨停」是跨票聚合。之前漏了这一步，`ind_limit_cnt`
    在回测里恒为 0，板块联动策略会静默地永远选不出东西。

    口径限制：这里只看得见回测池（默认成交额前 600 只），不是全市场。
    冷门行业的涨停会被漏掉，算出来的联动强度偏低——这是**保守偏差**：
    漏掉的联动只会少给买点，不会凭空造出买点。实盘选股读的是 `factor`
    表，那份是全市场聚合的，不受此影响。
    """
    n_cal = len(panel.dates)
    nc = panel.ncodes
    if not n_cal or not nc:
        return

    for ci in range(nc):
        bars = panel.bars[ci]
        m = len(bars["c"])
        for key in _INDUSTRY_KEYS:
            bars["f"][key] = [0.0] * m

    has = [[False] * nc for _ in range(n_cal)]
    pct_at = [[0.0] * nc for _ in range(n_cal)]
    limit_at = [[False] * nc for _ in range(n_cal)]
    for ci in range(nc):
        bars = panel.bars[ci]
        thr = panel.limit[ci] * 0.997
        for p, d in enumerate(bars["date_idx"]):
            if 0 <= d < n_cal:
                has[d][ci] = True
                pct_at[d][ci] = bars["pct"][p]
                limit_at[d][ci] = bars["pct"][p] >= thr

    by_ind: dict[str, list[int]] = {}
    for ci, ind in enumerate(panel.industries):
        by_ind.setdefault(ind, []).append(ci)

    for members in by_ind.values():
        prev_cnt = 0
        for d in range(n_cal):
            live = [ci for ci in members if has[d][ci]]
            if not live:
                continue
            cnt = sum(1 for ci in live if limit_at[d][ci])
            strength = sum(pct_at[d][ci] for ci in live) / len(live) * 100
            ranked = sorted(live, key=lambda ci: pct_at[d][ci], reverse=True)
            denom = max(len(live) - 1, 1)
            for rank, ci in enumerate(ranked):
                p = panel.bar[d][ci]
                if p < 0:
                    continue
                f = panel.bars[ci]["f"]
                f["ind_limit_cnt"][p] = float(cnt)
                f["ind_limit_prev"][p] = float(prev_cnt)
                f["ind_strength"][p] = round(strength, 3)
                f["ind_rank_pct"][p] = round(rank / denom * 100, 2)
            prev_cnt = cnt


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
    #
    # `market_ma` 同理必须读 spec：模拟盘（`paper.empty_account`）与「今日选股」
    # 都是按 spec 里的值走的，只有回测走 DEFAULTS，就会出现「回测里空仓躲过的
    # 熊段，实盘照样满仓挨打」——这正是用户抱怨过的「回测和实盘两张皮」。
    # 三处读同一个值，回测数字才对实盘有参考意义。
    # 风控三档 + 持有上限也必须从 spec 读。此前只读了 top_n 一类结构参数，
    # 于是 squeeze 的 stop_atr 1.5 / time_stop 6 在回测里全是 DEFAULTS 的
    # 2.2 / 20——模拟盘按 6 天走、回测按 20 天走，同一条策略两本账。
    for key in ("top_n", "max_per_industry", "pool_limit_min", "market_ma",
                "stop_atr", "tp1_atr", "tp2_atr", "time_stop"):
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
    # 弱势是否参与由策略自己声明（见 `selector.STRATEGIES` 里各条的注释）。
    # 不进 cfg 也不进基因组：它是策略的**性质**，不是可搜参数——
    # 让遗传搜索去挑「弱市该不该做」，等于让它挑这段行情是牛是熊。
    weak_participate = bool(spec.get("weak_participate"))
    # 开盘跳空闸开不开，同样是策略性质：抓动量的那条不该被高开拦下。
    # 缺省 True——「拦极端跳空」是更安全的默认值，要关必须显式声明。
    cfg["gap_gate"] = bool(spec.get("gap_gate", True))
    # 弱转强要求「必须高开」：区间带是买点的组成部分，不是风控。它替代默认
    # 闸而不是叠加——默认闸允许低开到 −6%，那正好把「弱势没被修复」当成机会。
    band = spec.get("gap_band")
    cfg["gap_band"] = (float(band[0]), float(band[1])) if band else None
    # 弱转强的验证条件：买入当日必须封板，没封住就是判断错了，次日开盘走。
    # 这条不能靠止损兜——止损是「跌到某个价位才反应」，而这里要的是
    # 「一到收盘就承认看错」，晚了整整一天，成本差在隔夜。
    # `limit_close_frac` 决定走多少：1.0 清仓，0.5 只走一半。
    cfg["need_limit_close"] = bool(spec.get("need_limit_close"))
    cfg["limit_close_frac"] = float(spec.get("limit_close_frac") or 1.0)
    # 已持仓要不要加仓。打板不加：它的持仓只有「封住了继续拿 / 没封住次日走」
    # 两种结局，中途往里加钱等于把一次确认过的下注改成两次没确认的下注。
    # 波段照常加（信号还在就补到计划仓位）。
    cfg["allow_add"] = bool(spec.get("allow_add", True))
    # 弱市里要不要再要求「行业抱团」。板块联动型策略靠题材抱团成立，
    # 波段靠个股中期动量——给它套抱团准入，弱市里会一只都选不出。
    cfg["weak_huddle"] = bool(spec.get("weak_huddle", True))

    if panel is None:
        panel = build_panel(engine, days=days, max_codes=max_codes,
                            boards=normalize_profile(None, profile)["boards"])

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

    # 条件筛选：v6 起真正参与回测。分两类处理——
    # 1. `NO_HISTORY` 里的字段（PE / ROE / 股息率）库里只有当前截面，
    #    没有历史序列可回放，只能摘掉，并在 notes 里说明摘了哪些；
    # 2. 其余能算出历史序列的字段（含新增的蓄势 / 板块联动因子）正常生效。
    # 以前是一律跳过，于是「带 6 条硬过滤的埋伏策略」在回测里跑的其实是
    # 「没有一条过滤的纯动量策略」——回测好看、实盘选不出票，根源就在这里。
    cfg["filters"] = []
    dropped_filters: list[str] = []
    for cond in (spec.get("filters") or []):
        if not isinstance(cond, dict):
            continue
        field = str(cond.get("field") or "").strip()
        if not field:
            continue
        if field in NO_HISTORY:
            dropped_filters.append(field)
            continue
        try:
            cfg["filters"].append(normalize(cond))
        except ConditionError:
            dropped_filters.append(field)
    if dropped_filters:
        notes.append(
            "以下筛选条件在回测中被摘掉（库里只有当前截面，没有历史序列可回放）："
            + "、".join(dict.fromkeys(dropped_filters))
            + "；其余条件已生效"
        )
    elif cfg["filters"]:
        notes.append(f"{len(cfg['filters'])} 条筛选条件已在回测中生效，"
                     f"与「今日选股」走同一套条件")
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
    # 弱势期被「抱团准入」挡掉的候选数。不报出来，用户只能看到「弱势时交易变少」，
    # 分不清是我们主动只做抱团，还是策略本来就没选出东西。
    weak_skipped = 0
    # 开盘校验挡掉的建仓数，按原因分开记——「高开不追」和「低开不接」是两种性质
    gap_skipped: dict[str, int] = {}

    for d in range(panel.n):
        date = panel.dates[d]

        # ---- 1. 开盘：先成交上一交易日收盘后挂出的单子
        # 减仓排最前面：同一天既有减仓又有加仓时先卖后买，否则「现金不够」
        # 会让本来能成交的加仓单被跳过——卖出的钱就在同一次开盘里。
        carry: list[dict] = []
        for order in sorted(pending, key=lambda o: 0 if o.get("kind") == "TRIM" else 1):
            ci = order["ci"]
            kind = order.get("kind") or "ENTRY"
            p = panel.bar[d][ci]
            if p < 0:
                # 停牌：最多等三个交易日，再等下去这只票的区间早就失效了
                order["miss"] = order.get("miss", 0) + 1
                if order["miss"] < 3:
                    carry.append(order)
                continue
            bars = panel.bars[ci]
            o = bars["o"][p]

            if kind == "TRIM":
                pos = positions.get(ci)
                # 一字跌停卖不掉，等下一格再说（和止损那一侧同一判断）。
                # 减仓不看购买权限：卖出本来就不需要权限，拦了只会把仓位锁死。
                if pos is None or not _can_sell(bars, p, panel.limit[ci]):
                    continue
                qty = min(int(order.get("shares") or 0), pos["shares"])
                if qty < 100:
                    continue
                cash, rec = _sell(panel, ci, pos, d, o, prof, cash,
                                  order.get("reason") or "计划减仓", seq, shares=qty)
                seq += 1
                turnover_amount += rec["amount"]
                trades.append(rec)
                pos["shares"] -= qty
                pos["trimmed"] = True
                if pos["shares"] <= 0:
                    positions.pop(ci, None)
                continue

            if kind == "ADD":
                pos = positions.get(ci)
                if pos is None or not _can_buy(bars, p, panel.limit[ci],
                                               cfg.get("gap_gate", True)):
                    continue
                equity_now = cash + _market_value(positions, panel, d)
                if equity_now <= 0:
                    continue
                fill = _slipped(o, "BUY", prof)
                # 加仓的两条硬约束：单票不超过上限（按**当前**市值算，所以
                # 涨上去的票能加的更少）、现金够付。不再重算 ATR 区间——
                # 止损/止盈是按最初的风险位定的，跟着均价动会让一次加仓
                # 把整笔交易的 R 悄悄放大。
                headroom = equity_now * single_cap(cfg["top_n"]) - pos["shares"] * fill
                want = int(order.get("shares") or 0)
                budget = min(headroom, cash)
                shares = _shares(budget, fill, bars["amt"][p]) if budget > 0 else 0
                shares = min(shares, want)
                while shares >= 100:
                    amount = shares * fill
                    fee = trade_fee(amount, "BUY", prof)
                    if amount + fee["total"] <= cash:
                        break
                    shares -= 100
                if shares < 100:
                    continue
                amount = shares * fill
                fee = trade_fee(amount, "BUY", prof)
                cash -= amount + fee["total"]
                total_shares = pos["shares"] + shares
                pos["cost"] = (pos["cost"] * pos["shares"] + fill * shares) / total_shares
                pos["shares"] = total_shares
                pos["last"] = fill
                seq += 1
                turnover_amount += amount
                trades.append({
                    "id": f"B{seq:05d}", "code": panel.codes[ci], "name": panel.names[ci],
                    "side": "BUY", "date": date, "price": round(fill, 3),
                    "shares": shares, "amount": round(amount, 2),
                    "fee": fee["total"], "tax": 0.0, "pnl": None, "pnl_pct": None,
                    "hold_days": d - pos["d0"],
                    "reason": order.get("reason", "计划加仓"),
                })
                continue

            if ci in positions:
                continue
            blocked = _buy_block(bars, p, panel.limit[ci],
                                 cfg.get("gap_gate", True), cfg.get("gap_band"))
            if blocked:
                gap_skipped[blocked[0]] = gap_skipped.get(blocked[0], 0) + 1
                continue
            equity_now = cash + _market_value(positions, panel, d)
            if equity_now <= 0:
                continue
            if (equity_now - cash) / equity_now > MAX_GROSS:
                continue
            weight = min(1.0 / cfg["top_n"], single_cap(cfg["top_n"]))
            # 择时降仓：大盘弱时每笔预算折半，而不是完全不开新仓。
            # 不能用 `or 1.0`：择时把 scale 压到 0 时，`0.0 or 1.0` 会变回满仓，
            # 「弱势空仓」这条规则就静默失效了（踩过一次）。
            scale = order.get("scale")
            budget = min(equity_now * weight * (1.0 if scale is None else float(scale)), cash)
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

            # T+1：今天开盘才建的仓，今天不能卖——这是 A 股的交收规则，不是
            # 风控偏好。少了这一条，回测里「早上买进、下午打穿止损离场」是
            # 可以发生的，而实盘根本挂不出这张卖单；结果是回测把当天的最坏
            # 情况截断在止损位、模拟盘又从来碰不到它，两边一起高估。
            # 放在更新 last / peak 之后：价格照记，只是不许卖。
            if pos.get("d0") == d:
                continue

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

        # ---- 3.5 弱转强：买入当日没封板 = 判断错了，次日开盘无条件走
        #
        # 这条和止损不是一回事。止损回答「跌到某个价位怎么办」，它回答
        # 「收盘就承认看错」——差一个隔夜，成本就吃在跳空上。写在这一步而不是
        # 塞进 `_held_action`：后者只在票还留在 Top-N 里时才被调用，
        # 「没封板」的票第二天的排名大概率掉出去，正好最需要它的时候不在。
        if cfg.get("need_limit_close"):
            for ci, pos in positions.items():
                if pos.get("d0") != d:
                    continue
                p = panel.bar[d][ci]
                if p < 0:
                    continue                       # 停牌：没有收盘价，明天再说
                if panel.bars[ci]["pct"][p] >= panel.limit[ci] - 0.002:
                    continue
                pending = [o for o in pending if o["ci"] != ci]
                frac = min(max(float(cfg.get("limit_close_frac") or 1.0), 0.0), 1.0)
                qty = int(pos["shares"] * frac / 100) * 100
                if 0 < qty < pos["shares"] * 0.5:
                    qty = pos["shares"]          # 不足半仓的零头一律清掉，别留 100 股
                if qty < 100:
                    continue
                pending.append({"ci": ci, "kind": "TRIM", "shares": qty,
                                "reason": "未封板离场"})

        # ---- 4. 收盘：生成下一交易日的委托（信号只用当日及之前的数据）
        #
        # 每天扫一次，只补**空出来的仓位**，不为了换股而换股。
        # 初版是「每 rebalance 天才扫一次」，结果账户在两次扫描之间会从
        # 满仓一路衰减到空仓：止损打掉一只就少一只，要等下一次扫描才补。
        # 一条 top_n=7 的策略因此平均仓位只有 25%，回撤被「钱没买满」压到
        # 很小、Calmar 刷到 5 以上——那不是选股好，是账户经常空着。
        # 现在按「持仓上限 − 现有持仓」算空缺，和模拟盘完全一致。
        if d < panel.n - 1:
            # 择时开关：大盘不在均线之上时**不开新仓**（REGIME_SCALE=0），
            # 已持仓仍按止损/止盈/减仓走完。系数统一放在 `REGIME_SCALE` 一处，
            # 模拟盘读同一个常量，两边不会各说各话。
            regime_ok = regime is None or (d < len(regime) and regime[d])
            # 弱势时按策略分开处理，不是一刀切：
            # - 声明了 `weak_participate` 的策略（目前只有「板块联动首日」）
            #   照常做，但只打抱团 + 半仓；
            # - 其余策略 scale=0 → 预算 0 股，自然一张单都不挂。
            #
            # 凭什么这么分：1260 天样本上放开弱势，板块联动 17.31%→18.81%、
            # 卡玛 0.67→0.69（变好）；缩量蓄势 15.50%→8.50%、卡玛 0.79→0.37；
            # 趋势波段 21.83%→16.04%、回撤 −23.62%→−37.34%（都变差）。
            # 「弱市里该不该做」取决于策略赚的是哪一种钱，不是一个统一答案。
            weak_on = (not regime_ok) and weak_participate
            scale = 1.0 if regime_ok else (REGIME_SCALE if weak_on else 0.0)
            # 每天扫一遍 Top-N，**已持有的票也在里面**：它今天又排进来了，说明
            # 信号还在，该补到计划仓位；浮盈到位了，该减一股。此前 `_select`
            # 把持仓排除在外，账户里的票一旦买进就再也不动，加仓减仓全看
            # 止损止盈偶然触发——计划卡上写着「加仓」，回测里根本没有这个动作。
            nav_now = cash + _market_value(positions, panel, d)
            slots = max(0, cfg["top_n"] - len(positions))
            for ci, score in _select(panel, d, weights, cfg, cfg["top_n"]):
                pos = positions.get(ci)
                if pos is not None:
                    act = _held_action(panel, ci, pos, d, nav_now, scale, cfg)
                    if act:
                        pending.append({"ci": ci, "kind": act["kind"],
                                        "shares": act["shares"],
                                        "reason": act["reason"], "scale": scale})
                    continue
                if slots <= 0:
                    continue
                # 弱势只打抱团：行业里没有涨停潮、涨幅排名也不靠前的票，
                # 在大盘跌破半年线期间不进候选。这不是「停手」，是「只做最硬的」。
                # 声明了 `weak_huddle=False` 的策略跳过这一道，只留半仓。
                if weak_on and cfg.get("weak_huddle", True) and not _huddle_ok(panel, ci, d):
                    weak_skipped += 1
                    continue
                pending.append({"ci": ci, "score": score, "reason": "信号建仓",
                                "scale": scale})
                slots -= 1

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
                   positions, cash, capital, turnover_amount, exposure_sum,
                   weak_skipped, gap_skipped)


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


def _buy_block(bars: dict, p: int, limit: float,
               gap_gate: bool = True,
               band: tuple[float, float] | None = None) -> tuple[str, str] | None:
    """开盘买不了的原因，返回 (类别, 说明)；能买返回 None。

    「一字涨停」和「跳空超出容忍」是两件不同的事，必须分开说：
    前者是**买不到**（市场不给机会），后者是**不该买**（我们自己放弃）。
    复盘时这两种要分别统计——把「我们主动放弃」混进「没成交」，
    会让人误以为执行率差是运气问题，实际是规则在挑价格。

    `gap_gate=False` 的策略跳过跳空判断（目前只有「趋势波段」）：它赚的是
    「动量延续」，高开恰恰是动量被确认的信号。同一份样本上给它设闸，
    年化从 21.83% 掉到 12.97%——那不是「风控更稳」，是把最强的那批信号
    当成风险滤掉了。一字涨停那一条不跟着关：那是买不到，不是要不要买。

    `band` 是「必须高开」的区间（百分比，如 (1, 7)），弱转强专用。它替代
    默认的追高/破位闸，因为这类买点的前提就是高开：不跳空说明昨天的弱势
    没有被修复，跳太多则已经是一字板、买不到也没溢价。区间之外分两种说法——
    高开不足算「没确认」，高开过头算「已透支」，不能报成同一件事。
    """
    gap = _gap(bars, p)
    if gap >= limit - 0.002:
        return ("limit", "一字涨停买不到")
    if band is not None:
        lo, hi = band
        if gap < lo / 100:
            return ("gap_weak", f"高开 {gap * 100:+.1f}%，未达 {lo:g}%，弱势没被修复")
        if gap > hi / 100:
            return ("gap_high", f"高开 {gap * 100:+.1f}%，越过 {hi:g}%，情绪已透支")
        return None
    if not gap_gate:
        return None
    if gap > GAP_MAX_PCT / 100:
        return ("gap_high", f"高开 {gap * 100:.1f}%，越过追高上限 {GAP_MAX_PCT:g}%")
    if gap < GAP_MIN_PCT / 100:
        return ("gap_low", f"低开 {gap * 100:.1f}%，跳空破位")
    return None


def _can_buy(bars: dict, p: int, limit: float, gap_gate: bool = True,
             band: tuple[float, float] | None = None) -> bool:
    """开盘能不能买：不是一字涨停，且跳空落在策略要求的区间里。"""
    return _buy_block(bars, p, limit, gap_gate, band) is None


def _huddle_ok(panel: Panel, ci: int, d: int) -> bool:
    """弱势时这只票所处的行业够不够「抱团」——够才允许开新仓。

    只在弱势分支里调用：强势时全市场都能买，不该为每只候选多算这几次查表。

    两个口径取「或」——
    - 行业当日涨停 ≥ `REGIME_HUDDLE_MIN_LIMITS` 家：题材已经点着了；
    - 行业涨幅排名进前 `REGIME_HUDDLE_MAX_RANK`%：资金持续流入但还没出涨停。
    指数跌破半年线时，正是这两类票在逆势上涨；只按指数位置一刀切，
    等于把「弱市里唯一能做的那批票」也一起枪毙了。

    门槛是**从宽到严试出来的**：2 家 / 前 20% 等于没筛（sector 回撤反而
    扩大到 −41.63%）；3 家 / 前 8% 还是漏进来一批跟风的（−41.63 → 仍在
    −30% 以上）。收到 5 家 / 前 5% 才真正只留下抱团主线，sector 从
    17.31%/Calmar 0.67 变成 26.82%/1.17。宁可错过，不要在弱市里捡跟风票。
    """
    p = panel.bar[d][ci]
    if p < 0:
        return False
    f = panel.bars[ci].get("f") or {}
    try:
        cnt = float(f["ind_limit_cnt"][p] or 0)
        # `ind_rank_pct` 的 0 表示行业涨幅第一，不能用 `or 100` 兜底——
        # 那会把最强的行业翻成最弱，弱市里恰好把该买的票滤掉（踩过）
        rank_raw = f["ind_rank_pct"][p]
        rank = 100.0 if rank_raw is None else float(rank_raw)
    except (KeyError, IndexError):
        # 因子缺行（次新股 / 停牌复牌）当「不抱团」处理：弱市里宁可错过
        return False
    return cnt >= REGIME_HUDDLE_MIN_LIMITS or rank <= REGIME_HUDDLE_MAX_RANK


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


def _filter_cell(panel: Panel, ci: int, p: int, field: str):
    """某票某日某字段的可回测值。取不到返回 None（条件判 False）。

    布尔形态字段在这里现算而不是预先存列：它们只在条件里出现，
    占一整列（每票每天一个浮点）只为判真假不划算，而且多一列就多一处
    与 `alphaforge_data.factors` 对齐的责任。
    """
    if field == "industry":
        return panel.industries[ci]
    bars = panel.bars[ci]
    if field == "change_pct":
        return round(bars["pct"][p] * 100, 4)
    if field == "price":
        return bars["c"][p]
    if field == "amount":
        return round(bars["amt"][p] / 1e8, 4) if bars["amt"][p] else 0.0
    if field == "limit_up":
        return bars["pct"][p] >= panel.limit[ci] * 0.997
    if field == "failed_limit":
        # 与 `alphaforge_data.factors.compute` 同一口径：摸到涨停、没封住
        if p < 1 or not bars["c"][p - 1]:
            return False
        lim = panel.limit[ci]
        touched = bars["h"][p] / bars["c"][p - 1] - 1 >= lim - 0.002
        sealed = bars["pct"][p] >= lim - 0.002
        return bool(touched and not sealed)
    if field == "above_ma20":
        ma = _ma(bars["c"], p, 20)
        return bool(ma > 0 and bars["c"][p] > ma)
    if field == "ma_bull":
        ma5, ma20, ma60 = (_ma(bars["c"], p, n) for n in (5, 20, 60))
        return bool(ma60 > 0 and ma5 > ma20 > ma60)
    if field == "new_high20":
        win = bars["c"][max(0, p - 19):p + 1]
        return bool(win and bars["c"][p] >= max(win))
    if field == "new_high60":
        win = bars["c"][max(0, p - 59):p + 1]
        return bool(win and bars["c"][p] >= max(win))
    col = bars["f"].get(field)
    if col is None or p >= len(col):
        return None
    return col[p]


def _held_action(panel: Panel, ci: int, pos: dict, d: int, nav: float,
                 scale: float, cfg: dict) -> dict | None:
    """已持仓的票，今天的计划卡要不要动手——加仓、减仓，还是什么都不做。

    与 `paper._held_action` **同一套规则**，只是把「计划卡的 sizing.shares」
    换成等价的「净值 × 单票权重 ÷ 现价」：模拟盘读的是卡片上写好的股数，
    回测里没有卡片，就直接算同一个数。两边的阈值（`TRIM_PNL_PCT`、`TRIM_FRAC`）、
    上限（`single_cap`）、择时系数（`scale`）全部共用常量，改一处两边一起变。

    - **减仓**：浮盈到 12% 减 1/3（只减一次）。TP1 已经减过半的票**照样减**：
      三档合计 1/2 + 1/6 = 2/3，剩下的 1/3 还跟得住趋势。此前把 `tp1_done` 也
      排除掉，结果是这条规则几乎永不触发——TP1 在 +2.6ATR（多数票 +5% 上下）
      就先成交了，浮盈根本没机会走到 12%，「减仓」在成交记录里查无此事。
    - **加仓**：同一只票今天又进了 Top-N（信号还在），补到计划仓位；
      受单票上限与现金约束（现金在撮合那一刻才判，这里只算股数）。
      择时关闭时 scale=0，与建仓一样不加——新敞口该被拦。
    - 其余情况不动，等止损 / 止盈 / 时间止损带走。
    """
    bars = panel.bars[ci]
    p = panel.bar[d][ci]
    if p < 0:
        return None
    # 建仓当日不加减：不加仓是因为计划仓位在开盘那一刻已经买满，
    # 不减仓是因为 T+1 卖不出去（没封板的票由第 3.5 步挂次日离场单）。
    if cfg.get("need_limit_close") and pos.get("d0") == d:
        return None
    price = bars["c"][p]
    cost = float(pos.get("cost") or 0.0)
    shares = int(pos.get("shares") or 0)
    if price <= 0 or cost <= 0 or shares <= 0:
        return None
    pnl_pct = (price / cost - 1) * 100

    if pnl_pct >= TRIM_PNL_PCT and not pos.get("trimmed"):
        qty = int(shares * TRIM_FRAC / 100) * 100
        if qty >= 100:
            return {"kind": "TRIM", "shares": qty,
                    "reason": f"计划减仓（浮盈 {pnl_pct:.1f}%）"}

    if pnl_pct < TRIM_PNL_PCT and scale > 0 and cfg.get("allow_add", True):
        top_n = max(1, int(cfg["top_n"]))
        weight = min(1.0 / top_n, single_cap(top_n))
        target = int(nav * weight / price / 100) * 100
        cap_shares = int(nav * single_cap(top_n) / price / 100) * 100
        delta = min(target - shares, cap_shares - shares)
        # 择时降仓时加仓预算同样打折，和建仓一个口径
        delta = int(delta * min(float(scale), 1.0) / 100) * 100
        if delta >= 100:
            return {"kind": "ADD", "shares": delta,
                    "reason": f"计划加仓（浮盈 {pnl_pct:+.1f}%）"}

    return None


def _select(panel: Panel, d: int, weights: dict[str, float], cfg: dict,
            limit: int, held: set[int] | None = None) -> list[tuple[int, float]]:
    """当日横截面打分 → 条件过滤 → 行业分散 → 取前 `limit` 个。

    `held` 传「不要重复买的票」。回测在收盘那一刻传空——已持有的票也要留在
    候选里，它今天还在 Top-N 里就说明信号还在，据此决定加仓还是减仓
    （见 `_held_action`）；只在撮合时按 kind 区分动作。

    `cfg["pool_limit_min"]` > 0 时先过一道硬门槛：只在连板数 ≥ 该值的票里选。
    打板策略必须靠这道门槛——单纯把连板数当因子加权，在涨停股少于 top_n 的
    日子会「凑数」买进一串连板数为 0 的普通股，那不是策略跑偏，是根本没在打板。

    `cfg["filters"]` 是 v6 新增的**真正生效**的条件过滤。埋伏型策略的买点
    几乎全部由条件定义（如「回踩 5 日线不破」「行业至少 2 家涨停」），
    只靠因子加权表达不了「区间」和「开关」这两种语义。
    """
    if limit <= 0:
        return []
    held = held or set()
    row_ok = panel.eligible[d]
    zs = {k: panel.z[k][d] for k in weights if k in panel.z}
    filters = cfg.get("filters") or []
    if not zs and not filters:
        return []
    pool_min = float(cfg.get("pool_limit_min") or 0.0)
    scored: list[tuple[int, float]] = []
    for ci in range(panel.ncodes):
        if not row_ok[ci] or ci in held:
            continue
        p = panel.bar[d][ci]
        if p < 0:
            continue
        if pool_min > 0:
            col = panel.bars[ci]["f"].get("limit_cnt")
            if not col or col[p] < pool_min:
                continue
        if filters and not evaluate(filters,
                                    lambda k, ci=ci, p=p: _filter_cell(panel, ci, p, k)):
            continue
        score = 0.0
        for key, w in weights.items():
            col = zs.get(key)
            if col is not None:
                score += col[ci] * w
        scored.append((ci, score))
    if not scored:
        return []
    if zs:
        scored.sort(key=lambda r: r[1], reverse=True)
    else:
        # 纯条件筛选（没有因子权重）：与 `selector.execute()` 同口径，
        # 按当日涨幅降序兜底，不按代码顺序随便抓几只。
        scored.sort(key=lambda r: panel.bars[r[0]]["pct"][panel.bar[d][r[0]]],
                    reverse=True)

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
            turnover_amount: float, exposure_sum: float = 0.0,
            weak_skipped: int = 0, gap_skipped: dict | None = None) -> dict:
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
    notes.append(f"股票池：按区间日均成交额取前 {panel.pool_size} 只（全市场 {panel.universe_size} 只）；"
                 f"当日成交额低于 {panel.min_amount / 1e4:.0f} 万的不进候选")
    notes.append("成交价 = 信号次日开盘价（含 5‱ 滑点），不用收盘价成交，避免未来函数")
    # 择时必须在备注里说清楚：仓位常年 35~45% 会让人以为是信号不够，
    # 实际是「弱势只做抱团 + 半仓」，两种情况的风险完全不同。
    ma_cfg = int(cfg.get("market_ma") or 0)
    if ma_cfg > 0 and spec.get("weak_participate"):
        huddle = cfg.get("weak_huddle", True)
        notes.append(
            f"择时：沪深300 在 {ma_cfg} 日线下方时**不停手**，改为"
            + ("「只打抱团 + 半仓」——候选所在行业当日涨停 ≥ "
               f"{REGIME_HUDDLE_MIN_LIMITS} 家、或涨幅排名进前 "
               f"{REGIME_HUDDLE_MAX_RANK:.0f}% 才建仓，预算乘 {REGIME_SCALE:g}。"
               if huddle else f"半仓（预算乘 {REGIME_SCALE:g}），不加行业抱团门槛。")
            + "与模拟盘同一口径。"
            + (f"本期因此放弃 {weak_skipped} 个候选。" if weak_skipped else ""))
    elif ma_cfg > 0:
        notes.append(
            f"择时：沪深300 在 {ma_cfg} 日线下方时不开新仓（已持仓仍按止损/止盈走完）。"
            f"这条策略在弱市里没有 edge——同一份 1260 天样本上放开弱势，"
            f"年化与回撤都明显变差（见 `selector.STRATEGIES` 里该条的注释）。"
            f"与模拟盘同一口径。")
    else:
        notes.append("未启用择时：任何行情都满仓参与，熊段回撤会显著放大")
    if gap_skipped:
        _gap_label = {"limit": "一字涨停买不到", "gap_high": "高开不追", "gap_low": "低开不接",
                      "gap_weak": "高开不足（弱势没被修复）"}
        if spec.get("gap_band"):
            lo, hi = spec["gap_band"]
            _band_note = f"。买点要求高开落在 {lo:g}%~{hi:g}% 之间"
        elif not spec.get("gap_gate", True):
            _band_note = ("。这条策略不设跳空闸（它赚的就是启动首日的动量，"
                          "高开正是信号本身），只拦一字涨停")
        else:
            _band_note = (f"。高开超过 {GAP_MAX_PCT:g}% 视为情绪透支放弃，"
                          f"低开超过 {abs(GAP_MIN_PCT):g}% 视为信号证伪放弃")
        notes.append(
            "开盘校验（成交价是次日开盘，即集合竞价结果）："
            + "、".join(f"{_gap_label[k]} {v} 次" for k, v in gap_skipped.items() if v)
            + _band_note)
    notes.append(f"每天扫描空缺仓位并补足到 top_n={cfg['top_n']}，不为了换股而换股："
                 f"已持有的标的不会因为排名下滑被卖，只会被止损/止盈/时间止损带走")
    notes.append(f"已持仓的票同样按计划卡执行：再进 Top-N 且浮盈未到 {TRIM_PNL_PCT:g}% 时"
                 f"补到计划仓位（受单票上限 {single_cap(cfg['top_n']):.0%} 与现金约束），"
                 f"浮盈达到 {TRIM_PNL_PCT:g}% 减 {TRIM_FRAC:.0%}"
                 f"（TP1 已减半的不再减）。与模拟盘同一套规则")
    # 卖出侧的 T+1 是模拟了的（建仓当日锁仓，见第 3 步的守卫）；没模拟的是
    # 资金交收——A 股卖出当天钱就能再买，这一点与实盘一致，所以不算乐观偏差。
    notes.append("行业分类与 ST 标记为当前值，不是历史值；建仓当日不可卖（T+1），"
                 "现金按当日可用处理")
    if avg_exposure < 0.6:
        _regime_why = ""
        if int(cfg.get("market_ma") or 0) > 0:
            _regime_why = (f"① 弱势期「只打抱团 + 半仓」（系数 {REGIME_SCALE:g}），熊段仓位天然被压下来；"
                           if spec.get("weak_participate") else
                           "① 弱势期直接停手（这条策略在弱市里没有 edge），熊段仓位归零；")
        notes.append(
            f"平均仓位仅 {avg_exposure:.0%}，三个原因按影响从大到小："
            + _regime_why
            + f"② TP1 减半之后腾出来的现金不会立刻补新仓，要等某个标的整只退出"
              f"才有空位（top_n={cfg['top_n']}，单票上限 {single_cap(cfg['top_n']):.0%}，"
              f"理论满仓 {min(1.0, cfg['top_n'] * single_cap(cfg['top_n'])):.0%}）；"
              f"③ 这一段的信号数本来就不够填满仓位。"
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
            "note": f"按区间日均成交额取前 {panel.pool_size} 只",
        },
        "params": {
            "top_n": cfg["top_n"], "max_per_industry": cfg["max_per_industry"],
            "stop_atr": cfg["stop_atr"], "tp1_atr": cfg["tp1_atr"],
            "tp2_atr": cfg["tp2_atr"], "time_stop": cfg["time_stop"],
            "rebalance": cfg["rebalance"], "capital": cfg["capital"],
            # 择时口径必须一起回传：只看收益数字看不出「这 40% 仓位是择时压出来的」，
            # 同样的收益在满仓和空仓切换下是完全不同的两件事。
            "market_ma": cfg.get("market_ma") or 0,
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
