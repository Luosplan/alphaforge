"""筹码分布（成本分布）。

股市里说的「筹码峰」不是真实持仓数据——交易所也不公开逐户持仓。
市面软件（通达信 / 同花顺）用的是同一套近似模型：**把每天的成交量按
当日价格区间摊到价格轴上，再用换手率逐日衰减旧筹码**。换手越充分，
旧筹码被洗掉得越多，峰就越往新价格搬家。

本模块就是这个模型的最小实现，纯函数、无外部依赖：

    对每根 K 线（从旧到新）
        t = 成交量(股) / 流通股本        # 当日换手率
        chips *= (1 - t)                 # 旧筹码衰减
        chips += t * 三角分布(low, high, 峰值)

三角分布而非均匀分布，是因为当日成交价集中在均价附近、两端稀疏，
摊成矩形会让峰形失真（看起来像一块砖，不像峰）。

输出给前端的是一条条 (price, weight)，weight 已归一化成占比，
前端拿它画横向柱状图即是筹码峰。
"""

from __future__ import annotations

from typing import Sequence


def _triangular(lo: float, hi: float, peak: float, edges: Sequence[float]) -> list[float]:
    """把一份权重按三角分布摊到价格网格上（返回未归一化的权重）。"""
    if hi - lo <= 1e-9:
        peak = lo
    peak = min(max(peak, lo), hi)
    out: list[float] = []
    left = peak - lo
    right = hi - peak
    for p in edges:
        if p < lo or p > hi:
            out.append(0.0)
        elif p <= peak:
            out.append((p - lo) / left if left > 1e-9 else 1.0)
        else:
            out.append((hi - p) / right if right > 1e-9 else 1.0)
    return out


def cost_distribution(
    bars: Sequence[dict],
    float_shares: float,
    *,
    bins: int = 120,
    decay: float = 1.0,
    current: float | None = None,
) -> dict:
    """算筹码分布。

    Args:
        bars: 日线，按日期升序，键为 low/high/close/volume（volume 单位「手」）。
            应当传**复权后**的价格，否则除权那天筹码会凭空跳一档。
        float_shares: 流通股本（股）。<= 0 时无法算换手率，直接返回不可用。
        bins: 价格轴分箱数。
        decay: 衰减系数。1.0 = 完全按换手率衰减（标准模型）；
            调小则旧筹码留存更久，峰更钝。
        current: 现价，用于算获利比例；不传则取最后一根收盘。

    Returns:
        {available, levels:[{price, weight}], peak, avg_cost, profit_ratio,
         cost90:[low, high], concentration}
    """
    rows = [b for b in bars if b.get("high") and b.get("low")]
    cur = current if current else (rows[-1]["close"] if rows else 0.0)
    if not rows or float_shares <= 0:
        return {"available": False, "levels": [], "peak": 0.0, "avg_cost": 0.0,
                "profit_ratio": 0.0, "cost90": [0.0, 0.0], "concentration": 0.0}

    lo = min(b["low"] for b in rows)
    hi = max(b["high"] for b in rows)
    if hi <= lo:
        hi = lo + 0.01
    step = (hi - lo) / bins
    edges = [lo + step * (i + 0.5) for i in range(bins)]

    chips = [0.0] * bins
    for b in rows:
        vol = float(b.get("volume") or 0.0) * 100.0          # 手 → 股
        t = vol / float_shares if float_shares else 0.0
        # 一天换手 100% 意味着筹码全换过一遍；除权/新股可能超过，截断
        t = min(max(t, 0.0), 1.0) * decay
        if t <= 0:
            continue
        keep = 1.0 - t
        if keep > 0:
            chips = [c * keep for c in chips]
        b_lo = max(b["low"], lo)
        b_hi = min(b["high"], hi)
        peak = (b["high"] + b["low"] + b["close"]) / 3.0
        shape = _triangular(b_lo, b_hi, peak, edges)
        total = sum(shape)
        if total <= 0:
            continue
        scale = t / total
        chips = [c + s * scale for c, s in zip(chips, shape)]

    # 分箱是离散的：某天成交密集的价位被切成相邻两箱后，轮廓会出现锯齿。
    # 做两遍 [1,2,1] 平滑，峰形才连成一片——平滑的是「箱化噪声」，
    # 不是原始成交信息，所以后面的统计量也用平滑后的分布算，两处口径一致。
    if bins >= 8:
        for _ in range(2):
            smoothed = chips[:]
            for i in range(1, bins - 1):
                smoothed[i] = (chips[i - 1] + 2 * chips[i] + chips[i + 1]) / 4
            chips = smoothed

    total_w = sum(chips)
    if total_w <= 0:
        return {"available": False, "levels": [], "peak": 0.0, "avg_cost": 0.0,
                "profit_ratio": 0.0, "cost90": [0.0, 0.0], "concentration": 0.0}

    # 合并相邻的零值箱，避免前端画出一堆 0 高度的空柱（视觉上像断档）
    levels = [
        {"price": round(edges[i], 2), "weight": round(chips[i] / total_w * 100, 3)}
        for i in range(bins)
        if chips[i] / total_w * 100 >= 0.01
    ]
    avg_cost = sum(edges[i] * chips[i] for i in range(bins)) / total_w
    peak_idx = max(range(bins), key=lambda i: chips[i])

    below = sum(chips[i] for i in range(bins) if edges[i] <= cur)
    profit_ratio = below / total_w * 100.0

    acc = 0.0
    low5 = high95 = edges[0]
    for i in range(bins):
        acc += chips[i] / total_w
        if acc <= 0.05:
            low5 = edges[i]
        if acc <= 0.95:
            high95 = edges[i]

    return {
        "available": True,
        "levels": levels,
        "peak": round(edges[peak_idx], 2),
        "peak_weight": round(chips[peak_idx] / total_w * 100, 2),
        "avg_cost": round(avg_cost, 2),
        "profit_ratio": round(profit_ratio, 2),
        "cost90": [round(low5, 2), round(high95, 2)],
        # 集中度：90% 筹码挤在多窄的区间里，越小越集中（<10% 通常算高度集中）
        "concentration": round((high95 - low5) / avg_cost * 100, 2) if avg_cost else 0.0,
        "bars_used": len(rows),
    }
