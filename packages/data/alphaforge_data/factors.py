"""因子宽表计算：把一只票的日线序列压成一行 34 个因子。

放在数据层而不是引擎层，是因为计算发生在**同步时**：5000+ 只票的 MACD/BOLL
要在一次盘后任务里算完，选股请求只读结果表。放引擎层就变成每次请求重算一遍，
那是几十秒级的延迟，交互上不可接受。

计算逻辑与旧版 `engine/factor_board.py` 逐列对齐，口径不变；差别只在数据来源
从合成序列换成了库里的真实日线。
"""

from __future__ import annotations

import statistics

from alphaforge_core.indicators import boll, macd


def _up_days(closes: list[float]) -> int:
    n = 0
    for i in range(len(closes) - 1, 0, -1):
        if closes[i] > closes[i - 1]:
            n += 1
        else:
            break
    return n


def _max_drawdown(closes: list[float]) -> float:
    if not closes:
        return 0.0
    peak = closes[0]
    worst = 0.0
    for c in closes:
        peak = max(peak, c)
        worst = max(worst, (peak - c) / peak * 100 if peak else 0.0)
    return worst


def _cross_up(fast: list[float | None], slow: list[float | None], lookback: int = 3) -> bool:
    for i in range(max(len(fast) - lookback, 1), len(fast)):
        a0, b0 = fast[i - 1], slow[i - 1]
        a1, b1 = fast[i], slow[i]
        if None in (a0, b0, a1, b1):
            continue
        if a0 <= b0 and a1 > b1:      # type: ignore[operator]
            return True
    return False


def price_limit(sym: dict) -> float:
    if sym.get("is_st"):
        return 0.05
    if sym.get("board") in ("创业板", "科创板"):
        return 0.20
    if sym.get("board") == "北交所":
        return 0.30
    return 0.10


def compute(sym: dict, series: dict, memberships: set[str]) -> dict | None:
    """一只票 → 一行因子。数据不足（上市不足 20 日）返回 None。"""
    closes: list[float] = series["raw_close"]
    highs: list[float] = series["raw_high"]
    lows: list[float] = series["raw_low"]
    volumes: list[float] = series["volume"]
    amounts: list[float] = series["amount"]
    mains: list[float] = series["main_net"]

    n = len(closes)
    if n < 21:
        return None

    def hold(k: int) -> float:
        return (closes[-1] / closes[-1 - k] - 1) * 100 if n > k and closes[-1 - k] else 0.0

    ma20 = sum(closes[-20:]) / 20
    ma5 = sum(closes[-5:]) / 5
    ma60 = sum(closes[-60:]) / 60 if n >= 60 else sum(closes) / n
    recent = closes[-21:]
    rets = [recent[i] / recent[i - 1] - 1 for i in range(1, len(recent)) if recent[i - 1]]
    macd_line = macd(closes)
    boll_up = boll(closes)["upper"]

    pe = sym.get("pe")
    amount = amounts[-1] if amounts else 0.0
    avg5 = sum(volumes[-6:-1]) / 5 if len(volumes) > 6 else (volumes[-1] if volumes else 0.0)
    limit = price_limit(sym)
    chg = (closes[-1] / closes[-2] - 1) if n >= 2 and closes[-2] else 0.0

    return {
        "price": round(closes[-1], 3),
        "change_pct": round(chg * 100, 2),
        "float_mv": round(float(sym.get("float_mv") or 0), 2),
        "amount": round(amount / 1e8, 4),
        "pe": round(pe, 2) if pe else 0.0,
        "roe": round(float(sym.get("roe") or 0), 2),
        "dividend": round(float(sym.get("dividend") or 0), 2),
        "ep": round(100.0 / pe, 3) if pe else 0.0,
        "mom5": round(hold(5), 2),
        "mom20": round(hold(20), 2),
        "mom60": round(hold(60), 2),
        "up_days": _up_days(closes),
        "dist_high": round((1 - closes[-1] / max(max(highs[-250:]), 1e-9)) * 100, 2),
        "vol20": round(statistics.pstdev(rets) * (252 ** 0.5) * 100, 2) if len(rets) > 2 else 0.0,
        "max_dd60": round(_max_drawdown(closes[-60:]), 2),
        "main_net": round((mains[-1] if mains else 0.0) / 1e8, 4),
        "mainflow": round((mains[-1] / amount * 100) if mains and amount else 0.0, 2),
        "net5d": round(sum(mains[-5:]) / 1e8, 4),
        "volratio": round(volumes[-1] / avg5, 2) if avg5 else 1.0,
        "limit_up": 1 if chg >= limit - 0.002 else 0,
        "above_ma20": 1 if closes[-1] > ma20 else 0,
        "ma_bull": 1 if ma5 > ma20 > ma60 else 0,
        "macd_gold": 1 if _cross_up(macd_line["dif"], macd_line["dea"], 3) else 0,
        "boll_break": 1 if boll_up[-1] is not None and closes[-1] > boll_up[-1] else 0,  # type: ignore[operator]
        "new_high20": 1 if closes[-1] >= max(closes[-20:]) else 0,
        "new_high60": 1 if n >= 60 and closes[-1] >= max(closes[-60:]) else 0,
        "is_st": 1 if sym.get("is_st") else 0,
        "industry": sym.get("industry") or "其他",
        "board": sym.get("board") or "主板",
        "in_hs300": 1 if "000300.SH" in memberships else 0,
        "in_sz50": 1 if "000016.SH" in memberships else 0,
        "in_zz500": 1 if "000905.SH" in memberships else 0,
        "in_sse": 1 if sym.get("exchange") == "SH" else 0,
        "in_szse": 1 if sym.get("exchange") == "SZ" else 0,
    }
