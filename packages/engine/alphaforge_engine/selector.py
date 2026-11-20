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

from .conditions import matches, normalize_all
from .factor_board import get_board
from .gate import allowed_labels, board_allow, buyable
from .market import MarketEngine
from .profile import board_tag

# 内置策略。
#
# 2026-11 之前的六条全部是「追涨型」：打分权重压在 mom20 / mom60 / dist_high
# 上，选出来的必然是**已经涨透**的票（实测选出 5 日 +52%、20 日 +60% 的标的）。
# 对超短来说这是最坏的位置——买在加速末段，赚的是最后一棒，亏起来最快。
#
# 现在按「买点位置」分两条：
#   埋伏组  squeeze / sector —— 用蓄势、板块联动定义买点，目标是买在启动前或
#           启动第一根，而不是跟在第三根后面；
#   延续组  trend —— 保留趋势延续，但补上量与位置约束（mom5 ≤ 12、
#           mom60 ≤ 45、volratio ≥ 1.3、mom20 ≥ 8、站稳 20 日线）。
#
# 2026-11 删掉两条：**强势回踩** 与 **打板**。5 年回测（1260 天 / 池 600）上
#   强势回踩 −2.0% 年化、回撤 −33.4%，打板 +0.6%（几乎为零）、回撤 −49.1%，
#   卡玛分别是 −0.06 与 0.01——不是「稍差」，是没有 edge，却每条每天还要
#   占 3 张计划卡、一个模拟账户和一个进化名额。策略多不等于机会多，
#   摊薄的是注意力和样本。
#
# 同一轮还收紧了 trend：它原来只有 2 条过滤，全市场 4956 只命中——那不是
# 「选股」而是「按动量排序」。加上量能与中期动量两条门槛后命中 33 只，
# 出票才真的来自一个前提（有量、在涨、还在 20 日线上方）。
#
# 埋伏组的条件全部由 `filters` 表达，不靠因子加权：像「三线粘合」
# 「行业昨天还没联动」这类语义是**区间**和**开关**，加权表达不了。
# 这依赖 backtest v6 起让 filters 真正生效——此前回测会静默跳过所有条件，
# 于是埋伏策略在回测里跑的是「无条件的纯动量」，回测与实盘两张皮。
STRATEGIES: list[dict] = [
    {
        "id": "squeeze",
        "name": "缩量蓄势",
        "desc": "振幅收窄 + 三线粘合，今日放量站上 5 日线时介入，买在启动第一根",
        "weights": {"squeeze": 0.30, "ma_converge": 0.25,
                    "volratio": 0.25, "ind_strength": 0.20},
        "filters": [
            # 蓄势：近期振幅收窄到基准的 82% 以内，且三线粘合在 4.5% 以内
            {"field": "squeeze", "op": "<=", "value": 82},
            {"field": "ma_converge", "op": "<=", "value": 4.5},
            # 启动确认：放量 + 上涨 + 站上 5 日线，三个都要有，
            # 只要缺一个就不是「启动」而是「还没动」或「假突破」
            {"field": "volratio", "op": ">=", "value": 1.4},
            {"field": "change_pct", "op": ">=", "value": 2},
            {"field": "dev_ma5", "op": ">=", "value": -0.5},
            # 位置约束：60 日涨幅限制在 -12%~+28%。上界砍掉已经涨透的，
            # 下界砍掉还在下跌趋势里的——缩量横盘在下跌中继里长得一模一样。
            {"field": "mom60", "op": "between", "value": [-12, 28]},
        ],
        "top_n": 3,
        "max_per_industry": 1,
        # 择时口径：沪深300 在 120 日（半年线）上方才开新仓。五条策略统一取
        # `backtest.DEFAULTS["market_ma"]` 的 120，不逐条调：
        # 5 年样本上确实存在「每条策略各自的最优均线」（板块联动 90、打板 200
        # 的单段收益都明显更好），但那是拿同一份数据挑参数，样本外会还回去。
        # 统一 120 的跨策略最差子区间是 −5.1%，本身已是五个候选值里最好的。
        "market_ma": 120,
        # 弱势时不参与。这条策略是「逆势埋伏」——缩量横盘 + 放量启动，
        # 它赚的是板块还没被发现的窗口。指数在半年线下方时，这种窗口的
        # 失败率显著上升：放开后 1260 天回测年化 15.50%→8.50%、卡玛 0.79→0.37。
        # 让它参与不是「多抓机会」，是「多亏钱」。
        "weak_participate": False,
        "time_stop": 6,
        "stop_atr": 1.5,
        "tp1_atr": 1.8,
        "tp2_atr": 3.5,
    },
    {
        "id": "sector",
        "name": "板块联动首日",
        "desc": "行业刚出现 2 家以上涨停的当天，买板块内还没涨停的次强票",
        "weights": {"ind_limit_cnt": 0.30, "ind_strength": 0.25,
                    "ind_rank_pct": 0.25, "volratio": 0.20},
        "filters": [
            # 今天是联动的第一天：昨天最多 1 家涨停，今天 ≥ 2 家
            {"field": "ind_limit_cnt", "op": ">=", "value": 2},
            {"field": "ind_limit_prev", "op": "<=", "value": 1},
            # 自己还没封板，才有空间；已经涨停的买不到也没溢价
            {"field": "limit_up", "op": "is", "value": False},
            {"field": "change_pct", "op": ">=", "value": 3},
            # 板块内排名前 30%，是次强跟风票，不是趴着不动的边缘票
            {"field": "ind_rank_pct", "op": "<=", "value": 30},
            # 前期不能已经暴涨，避免去接高位板块的最后一棒
            {"field": "mom20", "op": "<=", "value": 30},
        ],
        "top_n": 3,
        "max_per_industry": 2,
        "market_ma": 120,
        # 弱势（沪深300 在半年线下方）仍照常参与，只打抱团 + 半仓。
        #
        # 这个标记不是拍脑袋加的：这条策略本身选的就是「行业刚出 ≥2 家涨停的
        # 首日」，天生依赖题材抱团。1260 天回测上，让它在弱势期参与反而更好
        # （年化 17.31%→18.81%，卡玛 0.67→0.69，回撤 −25.86%→−27.27%）。
        # 另外两条策略的道理相反，见各自的注释。
        "weak_participate": True,
        "time_stop": 4,
        "stop_atr": 1.4,
        "tp1_atr": 1.6,
        "tp2_atr": 3.0,
    },
    {
        "id": "trend",
        "name": "趋势波段",
        "desc": "中期动量 + 量能放大，持有 3~8 天吃趋势延续（已加位置约束，不接末段）",
        "weights": {"mom20": 0.34, "mom60": 0.26, "dist_high": 0.22,
                    "volratio": 0.12, "vol20": 0.06},
        "filters": [
            # 位置约束：82% 的权重压在「已经涨了多少」上，等于专门挑加速末段。
            # 两条上限让它不再买最后一棒。
            {"field": "mom5", "op": "<=", "value": 12},
            {"field": "mom60", "op": "<=", "value": 45},
            # 2026-11 补的三条。原来只有上面两条，全市场命中 4956 只——
            # 那不是选股，是按动量排序，前 3 名与第 300 名没有本质区别。
            # 现在要求「有量、中期在涨、站在 20 日线上方」，命中 33 只，
            # 出票才真的来自一个可复述的前提。
            {"field": "volratio", "op": ">=", "value": 1.3},
            {"field": "mom20", "op": ">=", "value": 8},
            {"field": "above_ma20", "op": "is", "value": True},
        ],
        "top_n": 3,
        "max_per_industry": 1,
        # 原先是 20 日线。20 日线太短：2026-11 沪深300 一跌破，趋势账户就整两天
        # 一张委托都不挂，看起来像「模拟盘坏了」。改成 120 日与其余策略统一口径，
        # 也顺带避开了短均线来回穿越带来的反复开关。
        "market_ma": 120,
        # 弱势时不参与。趋势策略的前提是「趋势还在」，指数在半年线下方时
        # 中期动量本身就在衰减：放开后 1260 天回测年化 21.83%→16.04%、
        # 回撤 −23.62%→−37.34%。这条策略是三条里最适合「不做」的那条。
        "weak_participate": False,
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
    for cond in filters:
        column = board.column(cond["field"])
        for i, cell in enumerate(column):
            if mask[i] and not matches(cond, cell):
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
            positive = [f["label"] for f in filters][:3]
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
            for f in filters
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
    }


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
    """
    rows = [
        {
            "id": s["id"],
            "name": s["name"],
            "desc": s["desc"],
            "weights": s["weights"],
            "filters": [],
            "sort": None,
            "top_n": s.get("top_n", 10),
            "max_per_industry": s.get("max_per_industry", 2),
            "pool_limit_min": s.get("pool_limit_min", 0),
            "market_ma": s.get("market_ma", 0),
            "exclude_st": True,
            "builtin": True,
        }
        for s in STRATEGIES
    ]
    for item in custom or []:
        rows.append({**item, "builtin": False})
    return rows
