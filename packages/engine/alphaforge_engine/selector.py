"""选股引擎：条件筛选 + 因子加权 + 排序，三件事共用一块列存。

同一份 spec 可以从三个入口来：内置策略（代码里写死）、用户保存的策略（store.json）、
AI 问句解析结果（nlquery）。执行路径只有一条 `execute()`，所以三条入口的口径、
行业分散规则、返回结构完全一致，不会出现「AI 选出来的和策略选出来的不一样」。

spec 结构：
    {
      "id": "multifactor" | "s-xxxx" | "ai",
      "name": "多因子质量价值",
      "weights": {"ep": 0.22, "roe": 0.22},      # 可为空 → 纯条件筛选
      "filters": [{"field": "roe", "op": ">=", "value": 15}],
      "sort": {"by": "main_net", "desc": true},   # 可为空 → 按综合分
      "top_n": 10,
      "max_per_industry": 2,
      "exclude_st": true
    }
"""

from __future__ import annotations

import statistics

from alphaforge_core.fields import FIELD_MAP, WEIGHTABLE_FIELDS
from alphaforge_core.indicators import atr, sma

from .conditions import evaluate, leaves, normalize_all
from .factor_board import get_board
from .gate import allowed_labels, board_allow, buyable
from .market import MarketEngine
from .profile import board_tag

# 内置策略只有两条：打板、波段。
#
# 之前那一堆（缩量蓄势 / 板块联动首日 / 弱转强）的问题不是「不好」，
# 是定位错了：**弱转强不是一条策略，是打板的出手时间**。它描述的是
# 「什么时候该按下去」——次日开盘高开 1%~7% 那个窗口，而不是「买什么」。
# 把它单独做成一条策略，等于把同一个动作拆成两个账户、两套参数、两个
# 进化名额，选出来的票还互相重叠。
#
# 打板：今日涨停池按连板高度取前 3。买的是**已经封住的强**，卖的是次日
#   的溢价。它的风险不在选股，在出手价——所以出手时机用弱转强那套：
#   次日高开 1%~7% 才按，高开不足说明承接没来，高开过头已经一字、
#   买不到也没溢价。当日没封住＝判断错，次日开盘走。
# 波段：中期动量 + 量能放大，持有 3~8 天吃趋势延续。它是另一套完全不同的
#   持仓周期（周级），不该和打板共用一套参数去进化。
#
# 打板不看大盘位置（`market_ma = 0`）。涨停潮本身就是情绪指标，沪深300
#   在半年线下方时照样有连板梯队；拿指数位置去管打板，等于用一个滞后
#   指标去否掉当天最热的那批票。波段相反：它赚的是趋势延续，指数在
#   半年线下方时中期动量本身就在衰减，弱市停手是它 edge 的一部分。
STRATEGIES: list[dict] = [
    {
        "id": "daban",
        "name": "打板",
        "desc": "今日涨停池按连板高度取前 3，次日高开 1%~7% 出手；当日没封住次日走",
        # 权重只做排序，不做准入：买什么由 filters（涨停池）与 sort（连板数）
        # 定义。同板数之间优先给行业里正在抱团的那只——打板能不能成，
        # 题材强度比个股形态更说了算。
        "weights": {"ind_limit_cnt": 0.34, "volratio": 0.26,
                    "ind_strength": 0.22, "ind_rank_pct": 0.18},
        "filters": [
            # 今日涨停池。这是打板的定义，不是筛选条件之一——不选涨停股
            # 就不叫打板。收盘后因子表里 `limit_up` 才是当日最终答案，
            # 盘中看到的涨跌幅不作数（尾盘炸板很常见）。
            {"field": "limit_up", "op": "is", "value": True},
            # 成交额下限 5000 万：没人接的板，次日高开也卖不掉。
            {"field": "amount", "op": ">=", "value": 0.5},
        ],
        # 连板高度优先：首板、二板、三板的风险完全不同，连板数是最该排在前面的
        # 一列。只在涨停池里按连板数降序，同板数再按上面的权重分。
        "sort": {"by": "limit_cnt", "desc": True},
        # 涨停池门槛。与 filters 里的 `limit_up` 是同一件事的两道表达：
        # 选股阶段用它把「涨停股不够 top_n 时凑数」的普通股挡在外面，
        # 回测与模拟盘撮合阶段也读它——三处必须是同一个数。
        "pool_limit_min": 1,
        "top_n": 3,
        "max_per_industry": 2,
        # 持有上限 3 天。打板赚的是封板次日的溢价，第三天还在手里说明
        # 「次日高开」这个前提已经反复落空，继续拿着就是拿着一个普通仓位。
        # 写在这里而不是让它进搜索区间：持有周期是策略定义，不是调参项。
        "time_stop": 3,
        # 不看大盘位置：详见文件头「市场定位」那段。
        "market_ma": 0,
        # 弱市照做。打板赚的是情绪周期，指数在半年线下方时连板梯队照样存在；
        # 让它停手，等于在最需要「只做最强的」的时候把账户锁死。
        "weak_participate": True,
        # 弱转强＝出手时间：次日开盘高开 1%~7% 才按。低开说明昨天的板没人
        # 接，高开越过 7% 已经接近一字，买不到也没溢价。
        "gap_band": [1, 7],
        # 「当日没封住次日走」这条纪律**关着**，不是漏了：T+1 下当天买的当天
        # 卖不掉，「没封板」要到收盘才知道，成交只能等次日开盘——那正是最差
        # 的价位之一。同一份样本上打开它，250 天年化 62.6%→47.4%、
        # 最近 60 天 −46.9%→−66.0%，两个窗口都更差。打板的确认发生在
        # 「明天还敢不敢高开」上，而那一部分已经由 `gap_band` 表达。
        "need_limit_close": False,
        "limit_close_frac": 1.0,
        # 不加仓。打板的持仓只有两种结局：封住了继续拿、没封住次日走。
        # 中途再加一笔，等于把一次已经确认过的下注改成两次没确认的下注。
        "allow_add": False,
    },
    {
        "id": "trend",
        "name": "波段",
        "desc": "中期动量 + 量能放大，持有 3~8 天吃趋势延续（已加位置约束，不接末段）",
        "weights": {"mom20": 0.34, "mom60": 0.26, "dist_high": 0.22,
                    "volratio": 0.12, "vol20": 0.06},
        "filters": [
            # 位置约束：82% 的权重压在「已经涨了多少」上，等于专门挑加速末段。
            # 两条上限让它不再买最后一棒。
            {"field": "mom5", "op": "<=", "value": 12},
            {"field": "mom60", "op": "<=", "value": 45},
            # 只有上面两条时全市场命中 4956 只——那不是选股，是按动量排序，
            # 前 3 名与第 300 名没有本质区别。要求「有量、中期在涨、站在
            # 20 日线上方」之后命中 33 只，出票才真的来自一个可复述的前提。
            {"field": "volratio", "op": ">=", "value": 1.3},
            {"field": "mom20", "op": ">=", "value": 8},
            {"field": "above_ma20", "op": "is", "value": True},
        ],
        "top_n": 3,
        "max_per_industry": 1,
        # 持有 3~8 天。波段吃的是趋势延续，一天不够、两周太久。
        "time_stop": 8,
        # 沪深300 在半年线之上才开新仓。20 日线太短会反复穿越，200 日线太钝。
        "market_ma": 120,
        # 弱市照常参与，但仓位减半。实测：250 天窗口上弱市停手年化 66.4%、
        # 回撤 −23.9%，弱市参与年化 79.4%、回撤 −18.4%——近一年反而是「该做」；
        # 750 天窗口上两者 43.6% / 36.1%，差距收窄到可以接受。而「停手」的代价
        # 是账户在指数长期站不回去时几个月零成交，模拟盘与自进化闭环直接空转。
        "weak_participate": True,
        # 弱市不额外要求「行业抱团」。抱团准入是为「板块联动」那类策略设计的，
        # 波段靠的是个股中期动量：今天选出的抗跌大票（伊利、银行）所在行业
        # 本来就没有涨停，硬套抱团等于在弱市里一只都不买——账户再次锁死。
        "weak_huddle": False,
        # 开盘跳空闸关掉。这条策略赚的是「动量延续」，高开恰恰是动量被确认的
        # 信号；给它设闸，年化 21.83%→12.97%、卡玛 0.92→0.44——那不是风控
        # 更稳，是把最强的那批信号当风险滤掉了。
        "gap_gate": False,
    },
]

STRATEGY_MAP: dict[str, dict] = {s["id"]: s for s in STRATEGIES}

def _zscore(values: list[float]) -> list[float]:
    if len(values) < 2:
        return [0.0] * len(values)
    mean = statistics.fmean(values)
    sd = statistics.pstdev(values)
    if sd == 0:
        return [0.0] * len(values)
    return [max(-3.0, min(3.0, (v - mean) / sd)) for v in values]


def _rank_value(value, desc: bool) -> float:
    """排序用的数值。缺失数据一律排到末尾，不参与比较。

    因子表是按 code 对齐到池子的：池里存在、但因子表当天没有行的票会拿到
    `None`（实测 5580 只标的 vs 5574 行因子，差的几只是停牌或新股）。
    直接 `sort` 会在 `None` 与 `float` 比较时抛 TypeError —— `/api/v1/overview`
    就是这么挂的：整个首屏 500 全白，而不是少几只票。

    `desc=True` 时缺失值给 -inf（沉到末尾），升序时给 +inf（同样沉到末尾）。
    """
    if value is None:
        return float("-inf") if desc else float("inf")
    try:
        return float(value)
    except (TypeError, ValueError):
        return float("-inf") if desc else float("inf")


def _clean_weights(raw: dict | None) -> dict[str, float]:
    """只保留可加权的数值字段，并归一化到 1。"""
    if not raw:
        return {}
    picked = {k: float(v) for k, v in raw.items()
              if k in WEIGHTABLE_FIELDS and isinstance(v, (int, float)) and float(v) > 0}
    total = sum(picked.values())
    if not total:
        return {}
    return {k: round(v / total, 4) for k, v in picked.items()}


def normalize_spec(spec: dict, top_n: int | None = None) -> dict:
    """把任意来源的 spec 规整成执行层要的形状。"""
    base = STRATEGY_MAP.get(spec.get("id", ""), {})
    merged = {**base, **{k: v for k, v in spec.items() if v not in (None, "", {}, [])}}
    return {
        "id": merged.get("id", "custom"),
        "name": merged.get("name", "自定义策略"),
        "desc": merged.get("desc", ""),
        "weights": _clean_weights(merged.get("weights")),
        "filters": normalize_all(merged.get("filters") or []),
        "sort": merged.get("sort") or None,
        # 兜底 3：一条策略一天选 1~3 只才是有意义的决策。显式传 top_n（研究页/接口）
        # 仍然可以放大到 40。
        "top_n": max(1, min(int(top_n or merged.get("top_n") or 3), 40)),
        "max_per_industry": max(1, min(int(merged.get("max_per_industry") or 2), 20)),
        # 两条撮合开关：池门槛（打板用）与择时（趋势用）。
        # 不进 `weights`，也不参与打分，只是往下传给回测/模拟盘。
        "pool_limit_min": max(0, min(int(merged.get("pool_limit_min") or 0), 5)),
        "market_ma": max(0, min(int(merged.get("market_ma") or 0), 250)),
        "exclude_st": bool(merged.get("exclude_st", True)),
        # 买点属性只在撮合层生效，但要跟着 spec 一起带出去：今日选股页
        # 写着「明日高开 1%~7% 出手」，这个数必须和回测/模拟盘读的是同一个，
        # 否则页面上写的买点与账户实际执行的买点是两回事。
        "gap_band": list(merged["gap_band"]) if merged.get("gap_band") else None,
        "gap_gate": bool(merged.get("gap_gate", True)),
        "weak_participate": bool(merged.get("weak_participate", False)),
        "need_limit_close": bool(merged.get("need_limit_close")),
        "limit_close_frac": float(merged.get("limit_close_frac") or 1.0),
        "builtin": spec.get("id", "") in STRATEGY_MAP,
    }


def execute(engine: MarketEngine, spec: dict, top_n: int | None = None,
            boards: dict | None = None) -> dict:
    """跑一遍选股。

    `boards` 是「个人设置 → 购买权限」里的那份字典；给了就只在已开通板块里选。
    过滤落在 `mask` 上而不是重切 `pool`：因子表按池子代码元组做缓存，
    重切池等于每次换权限都要重算一整套因子表（几百只 × 上百列）。
    """
    cfg = normalize_spec(spec, top_n)
    weights = cfg["weights"]
    filters = cfg["filters"]
    allow = board_allow(boards)

    # 条件里显式要求 ST 时不再无条件排除，否则「只看 ST」会永远选不出东西
    explicit_st = any(f["field"] == "is_st" for f in filters)

    # 行业/板块已经被限死时，「每行业最多 N 只」这条分散规则没有意义：
    # 用户说「取 3 只白酒」，诉求就是 3 只白酒，而不是被分散规则砍到 2 只。
    if any(f["field"] in ("industry", "board") and f.get("op") == "=" for f in filters):
        cfg["max_per_industry"] = 10_000

    pool = [
        s for s in engine.universe()
        if not (cfg["exclude_st"] and not explicit_st and s["is_st"])
    ]
    if not pool:
        return _empty(engine, cfg, "股票池为空，先跑一次数据同步")
    board = get_board(engine, pool)
    if not board.ready:
        return _empty(engine, cfg, "因子表还没生成，先跑 sync --only factor")

    mask = [True] * len(pool)
    blocked = 0
    if allow is not None:
        for i, sym in enumerate(pool):
            if not buyable(sym["code"], allow):
                mask[i] = False
                blocked += 1
        if blocked == len(pool):
            return _empty(engine, cfg, "「个人设置 → 购买权限」里没有开通任何板块，"
                                      "开通后才能选股")
    if filters:
        for i in range(len(mask)):
            if mask[i] and not evaluate(filters, lambda k, i=i: board.column(k)[i]):
                mask[i] = False

    # 池门槛（打板用）：只在连板数 ≥ N 的票里选。
    # 必须在「今日选股」这一步就生效，不能只写在回测与下单里——否则页面上
    # 列出的候选是「全市场按因子打分的前几名」（实测包含当天没涨停的票），
    # 而实际挂单时又被这道门槛挡掉，看到的和买到的不是同一批。
    if cfg["pool_limit_min"] > 0:
        need = float(cfg["pool_limit_min"])
        for i, cell in enumerate(board.column("limit_cnt")):
            if mask[i] and (cell is None or float(cell) < need):
                mask[i] = False

    eligible = [(i, sym) for i, sym in enumerate(pool) if mask[i]]

    # 因子打分：Z 值在「排除 ST 后的全池」上算，保证不同条件下的分数可比
    z: dict[str, list[float]] = {}
    if weights:
        for key in weights:
            field = FIELD_MAP[key]
            column = board.column(key)
            z[key] = [v * field.direction for v in _zscore([float(c or 0) for c in column])]

    rows = []
    for i, sym in eligible:
        score = 0.0
        contributions: dict[str, float] = {}
        if weights:
            for key, w in weights.items():
                contributions[key] = z[key][i] * w
            score = sum(contributions.values())
        rows.append({"idx": i, "sym": sym, "score": score, "contrib": contributions})

    sort_cfg = cfg["sort"]
    implied = False
    if sort_cfg and sort_cfg.get("by") in FIELD_MAP:
        key = sort_cfg["by"]
        desc = bool(sort_cfg.get("desc", True))
        column = board.column(key)
        rows.sort(key=lambda r: _rank_value(column[r["idx"]], desc), reverse=desc)
        sort_cfg = {**sort_cfg, "label": FIELD_MAP[key].label}
    elif weights:
        sort_cfg = {"by": "score", "desc": True, "label": "综合得分"}
        rows.sort(key=lambda r: r["score"], reverse=True)
    else:
        # 只筛选不排序时，用当日涨跌幅兜底，并在结果里说明（不静默改变用户预期）
        sort_cfg = {"by": "change_pct", "desc": True, "label": "今日涨跌幅", "implied": True}
        implied = True
        column = board.column("change_pct")
        rows.sort(key=lambda r: _rank_value(column[r["idx"]], True), reverse=True)

    # 行业分散
    picked: list[dict] = []
    industry_count: dict[str, int] = {}
    for row in rows:
        ind = row["sym"]["industry"]
        if industry_count.get(ind, 0) >= cfg["max_per_industry"]:
            continue
        picked.append(row)
        industry_count[ind] = industry_count.get(ind, 0) + 1
        if len(picked) >= cfg["top_n"]:
            break

    items = []
    for rank, row in enumerate(picked, 1):
        sym: dict = row["sym"]
        i = row["idx"]
        bars = engine.bars(sym["code"], limit=120)
        if len(bars) < 2:
            continue
        closes = [b["close"] for b in bars]
        a = atr([b["high"] for b in bars], [b["low"] for b in bars], closes, 14)
        contributions = sorted(row["contrib"].items(), key=lambda kv: kv[1], reverse=True)

        factors = [
            {
                "key": k,
                "label": FIELD_MAP[k].label,
                "raw": round(float(board.column(k)[i] or 0), 4),
                "z": round(z[k][i], 3),
                "weight": weights[k],
                "contribution": round(row["contrib"][k] * 100, 2),
                "desc": FIELD_MAP[k].desc,
            }
            for k in weights
        ]

        # `or 0` 不能省：因子表缺行的票这一列是 None，直接 float() 会抛
        # TypeError，整个选股接口从「少一条理由」变成「500」。
        if weights:
            positive = [
                f"{FIELD_MAP[k].label} {float(board.column(k)[i] or 0):+.2f}（z={z[k][i]:+.2f}）"
                for k, v in contributions[:3] if v > 0
            ]
            negative = [
                f"{FIELD_MAP[k].label} {float(board.column(k)[i] or 0):+.2f}（z={z[k][i]:+.2f}）"
                for k, v in contributions[::-1][:2] if v < 0
            ]
        else:
            positive = [f["label"] for f in leaves(filters)][:3]
            negative = []

        # 命中条件对应的实际值：条件筛选模式下，这比「因子贡献」更有信息量
        hits = [
            {
                "field": f["field"],
                "label": FIELD_MAP[f["field"]].label,
                "op": f["op"],
                "target": f["value"],
                "value": board.column(f["field"])[i],
                "unit": FIELD_MAP[f["field"]].unit,
            }
            for f in leaves(filters)
        ]

        sort_key = sort_cfg["by"]
        sort_value = row["score"] * 100 if sort_key == "score" else float(board.column(sort_key)[i] or 0)

        # 抱团画像：弱势行情里「只打抱团」要靠它判准入（见 `paper._next_plans`），
        # 前端的计划卡也会显示。**不能**用 `or 100` 兜底 `ind_rank_pct`——
        # 0 表示行业涨幅排第一（最强），`0.0 or 100` 会把它翻成最弱。
        rank_raw = board.column("ind_rank_pct")[i]
        huddle = {
            "ind_limit_cnt": float(board.column("ind_limit_cnt")[i] or 0),
            "ind_limit_prev": float(board.column("ind_limit_prev")[i] or 0),
            "ind_rank_pct": 100.0 if rank_raw is None else float(rank_raw),
            "ind_strength": float(board.column("ind_strength")[i] or 0),
            "industry": sym["industry"],
        }

        items.append({
            "rank": rank,
            "code": sym["code"],
            "name": sym["name"],
            "industry": sym["industry"],
            "board": sym["board"],
            "board_tag": board_tag(sym["code"], sym["board"] or "", sym.get("exchange") or ""),
            "score": round(row["score"] * 100, 2),
            "price": closes[-1],
            "change_pct": round((bars[-1]["close"] / bars[-2]["close"] - 1) * 100, 2),
            "atr": next((v for v in reversed(a) if v is not None), 0.0),
            "ma20": next((v for v in reversed(sma(closes, 20)) if v is not None), 0.0),
            "factors": factors,
            "reason": {"positive": positive, "negative": negative},
            "hits": hits,
            "rank_value": round(sort_value, 3),
            "huddle": huddle,
        })

    mode = "hybrid" if (weights and filters) else ("factor" if weights else "filter")
    trade_date = engine.trade_date
    # 池子大小按「可买的」算：写着「全 A 股 5578 只」而实际只在 3200 只里选，
    # 匹配数看着对不上，是这类口径最容易招骂的地方
    pool_size = len(pool) - blocked if allow is not None else len(pool)
    return {
        "strategy_id": cfg["id"],
        "strategy_name": cfg["name"],
        "mode": mode,
        "as_of": trade_date.isoformat() if trade_date else None,
        "universe": (f"全 A 股（{pool_size} 只）" if allow is None
                     else f"可买板块（{pool_size} 只）"),
        "universe_size": pool_size,
        "board_filter": {
            "applied": allow is not None,
            "allowed": allowed_labels(allow),
            "blocked": blocked,
        },
        "top_n": cfg["top_n"],
        "items": items,
        "weights": weights,
        "filters": filters,
        "sort": sort_cfg,
        "matched_count": len(rows),
        "max_per_industry": cfg["max_per_industry"],
        "exclude_st": cfg["exclude_st"],
        "rank_hint": _rank_hint(sort_cfg, implied),
        # 出手时机写在结果里，不让前端按 strategy_id 自己拼：打板是「次日高开
        # 1%~7%」，波段是「次日开盘按建仓区挂单」，两者的差别决定了卡片上
        # 该写哪个价。
        "entry_rule": _entry_rule(cfg),
        "pool_limit_min": cfg["pool_limit_min"],
    }


def _empty(engine: MarketEngine, cfg: dict, message: str) -> dict:
    """数据没准备好时的空结果。不抛异常，让人看清「为什么没有」。"""
    trade_date = engine.trade_date
    return {
        "strategy_id": cfg["id"],
        "strategy_name": cfg["name"],
        "mode": "filter" if not cfg["weights"] else "factor",
        "as_of": trade_date.isoformat() if trade_date else None,
        "universe": "全 A 股（0 只）",
        "universe_size": 0,
        "top_n": cfg["top_n"],
        "items": [],
        "weights": cfg["weights"],
        "filters": cfg["filters"],
        "sort": None,
        "matched_count": 0,
        "max_per_industry": cfg["max_per_industry"],
        "exclude_st": cfg["exclude_st"],
        "board_filter": {"applied": False, "allowed": [], "blocked": 0},
        "rank_hint": message,
        "notice": message,
        "entry_rule": _entry_rule(cfg),
        "pool_limit_min": cfg["pool_limit_min"],
    }


def _entry_rule(cfg: dict) -> str:
    band = cfg.get("gap_band")
    if band:
        return f"次日高开 {band[0]:g}%~{band[1]:g}% 出手，当日没封住次日走"
    return "次日开盘按建仓区挂单"


def _rank_hint(sort_cfg: dict, implied: bool) -> str:
    if sort_cfg["by"] == "score":
        return "按综合得分（因子 Z 值加权 ×100）降序"
    label = FIELD_MAP[sort_cfg["by"]].label if sort_cfg["by"] in FIELD_MAP else sort_cfg.get("label", "")
    direction = "降序" if sort_cfg.get("desc", True) else "升序"
    prefix = "未指定排序，默认按" if implied else "按"
    return f"{prefix}{label}{direction}"


def strategy_list(custom: list[dict] | None = None) -> list[dict]:
    """内置 + 自定义。自定义带上完整 spec，前端编辑器直接可用。

    `top_n` 与两条撮合开关（`pool_limit_min` / `market_ma`）必须从策略定义里读，
    不能写死 10——打板策略的 top_n=3 和「只在涨停池里选」是它成立的前提，
    在这里被抹平成默认值，跑出来的就不是打板了。

    `filters` 同理：弱转强的全部信息量都在条件里，返回空数组会让它在编辑器里
    变成一套「只有权重」的策略，复制一份出来买点悄悄消失。带上归一化后的
    label，前端也不必自己拼中文。
    """
    rows = [
        {
            "id": s["id"],
            "name": s["name"],
            "desc": s["desc"],
            "weights": s["weights"],
            "filters": normalize_all(s.get("filters")),
            # 排序要跟着走：打板是靠「连板数降序」成立的，返回 None 会让编辑器
            # 里那一栏空着，复制一份出来就变成按综合分排——选出来的不再是
            # 连板梯队，而是一池涨停股按行业热度打乱。
            "sort": s.get("sort"),
            "top_n": s.get("top_n", 10),
            "max_per_industry": s.get("max_per_industry", 2),
            "time_stop": s.get("time_stop"),
            "pool_limit_min": s.get("pool_limit_min", 0),
            "market_ma": s.get("market_ma", 0),
            "gap_band": s.get("gap_band"),
            "gap_gate": s.get("gap_gate", True),
            "weak_participate": s.get("weak_participate", False),
            "weak_huddle": s.get("weak_huddle", True),
            "need_limit_close": s.get("need_limit_close", False),
            "exclude_st": True,
            "builtin": True,
        }
        for s in STRATEGIES
    ]
    for item in custom or []:
        rows.append({**item, "builtin": False})
    return rows
