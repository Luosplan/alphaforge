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
from .market import MarketEngine

# 内置策略：只给了权重，条件留空。用户可「复制为自定义」再加条件。
#
# 只留两条，理由不是「少好看」，而是**方向不同、不能混在一起评**：
#   trend    —— 波段：顺着中期趋势持有三五天，赚的是延续；
#   limitup  —— 打板：从昨日涨停池里选，赚的是次日溢价，亏起来也最狠。
# 之前那四条（多因子质量价值 / 动量趋势突破 / 主力资金跟随 / 低波红利）
# 都在 2025-11~2026-11 这段回测里亏钱（−7% ~ −45%，基准 −14.8%），
# 而且彼此高度重合——同涨同跌，分散不了。与其留六条曲线互相解释，
# 不如留两条能把「趋势」和「情绪」分开看的。
STRATEGIES: list[dict] = [
    {
        "id": "trend",
        "name": "趋势波段",
        "desc": "中期动量 + 量能放大 + 贴近 52 周高点，持有 3~8 天吃趋势延续",
        "weights": {"mom20": 0.34, "mom60": 0.26, "dist_high": 0.22,
                    "volratio": 0.12, "vol20": 0.06},
        "filters": [],
        "top_n": 3,
        "max_per_industry": 1,
        # 大盘在 20 日线下方不做新仓——实测这条对趋势类策略是净收益，
        # 而且它管的是「该不该做」，不参与选股打分。
        "market_ma": 20,
    },
    {
        "id": "limitup",
        "name": "打板",
        "desc": "昨日涨停池里选，按连板数与封成色排序，赚次日溢价",
        "weights": {"limit_cnt": 0.55, "volratio": 0.25, "mom5": 0.20},
        "filters": [],
        "top_n": 3,
        "max_per_industry": 1,
        # 硬门槛：连板数 ≥ 1，只在昨日涨停的票里选。
        # 没有它，涨停家数少于 top_n 的日子会补进普通股，那不是打板。
        "pool_limit_min": 1,
        # 打板不进择时：涨停潮本来就是情绪最热的时候，
        # 用大盘均线挡它，等于在情绪起点把它关掉。
        "market_ma": 0,
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
        "top_n": max(1, min(int(top_n or merged.get("top_n") or 10), 40)),
        "max_per_industry": max(1, min(int(merged.get("max_per_industry") or 2), 20)),
        # 两条撮合开关：池门槛（打板用）与择时（趋势用）。
        # 不进 `weights`，也不参与打分，只是往下传给回测/模拟盘。
        "pool_limit_min": max(0, min(int(merged.get("pool_limit_min") or 0), 5)),
        "market_ma": max(0, min(int(merged.get("market_ma") or 0), 250)),
        "exclude_st": bool(merged.get("exclude_st", True)),
        "builtin": spec.get("id", "") in STRATEGY_MAP,
    }


def execute(engine: MarketEngine, spec: dict, top_n: int | None = None) -> dict:
    cfg = normalize_spec(spec, top_n)
    weights = cfg["weights"]
    filters = cfg["filters"]

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
    for cond in filters:
        column = board.column(cond["field"])
        for i, cell in enumerate(column):
            if mask[i] and not matches(cond, cell):
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
        rows.sort(key=lambda r: board.column(key)[r["idx"]], reverse=desc)
        sort_cfg = {**sort_cfg, "label": FIELD_MAP[key].label}
    elif weights:
        sort_cfg = {"by": "score", "desc": True, "label": "综合得分"}
        rows.sort(key=lambda r: r["score"], reverse=True)
    else:
        # 只筛选不排序时，用当日涨跌幅兜底，并在结果里说明（不静默改变用户预期）
        sort_cfg = {"by": "change_pct", "desc": True, "label": "今日涨跌幅", "implied": True}
        implied = True
        rows.sort(key=lambda r: board.column("change_pct")[r["idx"]], reverse=True)

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

        if weights:
            positive = [
                f"{FIELD_MAP[k].label} {float(board.column(k)[i]):+.2f}（z={z[k][i]:+.2f}）"
                for k, v in contributions[:3] if v > 0
            ]
            negative = [
                f"{FIELD_MAP[k].label} {float(board.column(k)[i]):+.2f}（z={z[k][i]:+.2f}）"
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

        items.append({
            "rank": rank,
            "code": sym["code"],
            "name": sym["name"],
            "industry": sym["industry"],
            "board": sym["board"],
            "score": round(row["score"] * 100, 2),
            "price": closes[-1],
            "change_pct": round((bars[-1]["close"] / bars[-2]["close"] - 1) * 100, 2),
            "atr": next((v for v in reversed(a) if v is not None), 0.0),
            "ma20": next((v for v in reversed(sma(closes, 20)) if v is not None), 0.0),
            "factors": factors,
            "reason": {"positive": positive, "negative": negative},
            "hits": hits,
            "rank_value": round(sort_value, 3),
        })

    mode = "hybrid" if (weights and filters) else ("factor" if weights else "filter")
    trade_date = engine.trade_date
    return {
        "strategy_id": cfg["id"],
        "strategy_name": cfg["name"],
        "mode": mode,
        "as_of": trade_date.isoformat() if trade_date else None,
        "universe": f"全 A 股（{len(pool)} 只）",
        "universe_size": len(pool),
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
