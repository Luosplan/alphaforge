"""绩效指标（附录 B 公式实现）。"""

from __future__ import annotations

import math

TRADING_DAYS = 252


def daily_returns(equity: list[float]) -> list[float]:
    out = []
    for i in range(1, len(equity)):
        prev = equity[i - 1]
        out.append((equity[i] - prev) / prev if prev else 0.0)
    return out


def total_return(equity: list[float]) -> float:
    if len(equity) < 2 or not equity[0]:
        return 0.0
    return equity[-1] / equity[0] - 1


def annualized_return(equity: list[float]) -> float:
    if len(equity) < 2 or not equity[0]:
        return 0.0
    days = len(equity) - 1
    if days <= 0:
        return 0.0
    growth = equity[-1] / equity[0]
    if growth <= 0:
        return -1.0
    return growth ** (TRADING_DAYS / days) - 1


def volatility(returns: list[float]) -> float:
    n = len(returns)
    if n < 2:
        return 0.0
    mean = sum(returns) / n
    var = sum((r - mean) ** 2 for r in returns) / (n - 1)
    return math.sqrt(var) * math.sqrt(TRADING_DAYS)


def downside_deviation(returns: list[float]) -> float:
    neg = [r for r in returns if r < 0]
    if not neg:
        return 0.0
    var = sum(r * r for r in neg) / len(neg)
    return math.sqrt(var) * math.sqrt(TRADING_DAYS)


def max_drawdown(equity: list[float]) -> tuple[float, int]:
    """返回（最大回撤绝对值, 回撤持续交易日）。"""
    peak = -math.inf
    peak_idx = 0
    mdd = 0.0
    duration = 0
    best_duration = 0
    for i, v in enumerate(equity):
        if v > peak:
            peak = v
            peak_idx = i
            best_duration = max(best_duration, duration)
            duration = 0
        else:
            dd = 1 - v / peak if peak else 0
            if dd > mdd:
                mdd = dd
            duration = i - peak_idx
    return mdd, max(best_duration, duration)


def sharpe(equity: list[float], rf: float = 0.02) -> float:
    rets = daily_returns(equity)
    vol = volatility(rets)
    if vol == 0:
        return 0.0
    return (annualized_return(equity) - rf) / vol


def sortino(equity: list[float], rf: float = 0.02) -> float:
    rets = daily_returns(equity)
    dd = downside_deviation(rets)
    if dd == 0:
        return 0.0
    return (annualized_return(equity) - rf) / dd


def calmar(equity: list[float]) -> float:
    mdd, _ = max_drawdown(equity)
    if mdd == 0:
        return 0.0
    return annualized_return(equity) / mdd


def summarize(equity: list[float], rf: float = 0.02) -> dict:
    mdd, dur = max_drawdown(equity)
    return {
        "total_return": round(total_return(equity), 4),
        "annual_return": round(annualized_return(equity), 4),
        "volatility": round(volatility(daily_returns(equity)), 4),
        "max_drawdown": round(-mdd, 4),
        "max_drawdown_days": dur,
        "sharpe": round(sharpe(equity, rf), 3),
        "sortino": round(sortino(equity, rf), 3),
        "calmar": round(calmar(equity), 3),
    }


def monthly_matrix(curve: list[dict]) -> list[dict]:
    """curve: [{date: 'YYYY-MM-DD', value: float}] → 月度收益矩阵。"""
    buckets: dict[str, list[float]] = {}
    for row in curve:
        buckets.setdefault(row["date"][:7], []).append(row["value"])
    months = sorted(buckets)
    out = []
    for m in months:
        vals = buckets[m]
        if len(vals) < 2 or not vals[0]:
            out.append({"month": m, "ret": 0.0})
            continue
        out.append({"month": m, "ret": round(vals[-1] / vals[0] - 1, 4)})
    return out
