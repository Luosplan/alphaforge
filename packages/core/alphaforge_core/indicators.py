"""技术指标库（纯函数，无第三方依赖）。

所有函数输入 list[float] / list[Bar]，输出与输入等长的 list，
数据不足处填 None，便于直接序列化给前端绘图。
"""

from __future__ import annotations

from typing import Sequence


def sma(values: Sequence[float], n: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    if n <= 0:
        return out
    acc = 0.0
    for i, v in enumerate(values):
        acc += v
        if i >= n:
            acc -= values[i - n]
        if i >= n - 1:
            out[i] = round(acc / n, 4)
    return out


def ema(values: Sequence[float], n: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    if not values or n <= 0:
        return out
    k = 2.0 / (n + 1)
    prev = values[0]
    out[0] = round(prev, 4)
    for i in range(1, len(values)):
        prev = values[i] * k + prev * (1 - k)
        out[i] = round(prev, 4)
    return out


def macd(closes: Sequence[float], fast: int = 12, slow: int = 26, signal: int = 9):
    ef = ema(closes, fast)
    es = ema(closes, slow)
    dif = [round((a or 0) - (b or 0), 4) if a is not None and b is not None else None for a, b in zip(ef, es)]
    valid = [d if d is not None else 0.0 for d in dif]
    dea = ema(valid, signal)
    hist = [
        round((d - e) * 2, 4) if d is not None and e is not None else None
        for d, e in zip(dif, dea)
    ]
    return {"dif": dif, "dea": dea, "hist": hist}


def rsi(closes: Sequence[float], n: int = 14) -> list[float | None]:
    out: list[float | None] = [None] * len(closes)
    if len(closes) <= n:
        return out
    gains = 0.0
    losses = 0.0
    for i in range(1, n + 1):
        d = closes[i] - closes[i - 1]
        gains += max(d, 0.0)
        losses += max(-d, 0.0)
    avg_g = gains / n
    avg_l = losses / n
    out[n] = round(100 * avg_g / (avg_g + avg_l), 2) if (avg_g + avg_l) else 50.0
    for i in range(n + 1, len(closes)):
        d = closes[i] - closes[i - 1]
        avg_g = (avg_g * (n - 1) + max(d, 0.0)) / n
        avg_l = (avg_l * (n - 1) + max(-d, 0.0)) / n
        out[i] = round(100 * avg_g / (avg_g + avg_l), 2) if (avg_g + avg_l) else 50.0
    return out


def kdj(highs, lows, closes, n: int = 9, m1: int = 3, m2: int = 3):
    k_list: list[float | None] = [None] * len(closes)
    d_list: list[float | None] = [None] * len(closes)
    j_list: list[float | None] = [None] * len(closes)
    k_prev = 50.0
    d_prev = 50.0
    for i in range(len(closes)):
        if i < n - 1:
            continue
        hh = max(highs[i - n + 1 : i + 1])
        ll = min(lows[i - n + 1 : i + 1])
        rsv = 50.0 if hh == ll else (closes[i] - ll) / (hh - ll) * 100
        k_prev = (m1 - 1) / m1 * k_prev + rsv / m1
        d_prev = (m2 - 1) / m2 * d_prev + k_prev / m2
        k_list[i] = round(k_prev, 2)
        d_list[i] = round(d_prev, 2)
        j_list[i] = round(3 * k_prev - 2 * d_prev, 2)
    return {"k": k_list, "d": d_list, "j": j_list}


def boll(closes: Sequence[float], n: int = 20, k: float = 2.0):
    mid = sma(closes, n)
    up: list[float | None] = [None] * len(closes)
    low: list[float | None] = [None] * len(closes)
    for i in range(len(closes)):
        if mid[i] is None:
            continue
        window = closes[i - n + 1 : i + 1]
        mean = mid[i] or 0.0
        var = sum((v - mean) ** 2 for v in window) / n
        sd = var ** 0.5
        up[i] = round(mean + k * sd, 4)
        low[i] = round(mean - k * sd, 4)
    return {"mid": mid, "upper": up, "lower": low}


def true_range(highs, lows, closes) -> list[float]:
    out = []
    for i in range(len(closes)):
        if i == 0:
            out.append(highs[i] - lows[i])
        else:
            pc = closes[i - 1]
            out.append(max(highs[i] - lows[i], abs(highs[i] - pc), abs(lows[i] - pc)))
    return out


def atr(highs, lows, closes, n: int = 14) -> list[float | None]:
    tr = true_range(highs, lows, closes)
    out: list[float | None] = [None] * len(closes)
    if len(tr) < n:
        return out
    prev = sum(tr[:n]) / n
    out[n - 1] = round(prev, 4)
    for i in range(n, len(tr)):
        prev = (prev * (n - 1) + tr[i]) / n
        out[i] = round(prev, 4)
    return out


def last_valid(series: Sequence[float | None]) -> float | None:
    for v in reversed(series):
        if v is not None:
            return v
    return None


def compute_all(bars: list[dict], sets: set[str] | None = None) -> dict:
    """输入 bar 列表，返回各指标序列。sets 为 None 时全量计算。"""
    closes = [b["close"] for b in bars]
    highs = [b["high"] for b in bars]
    lows = [b["low"] for b in bars]
    sets = sets or {"MA", "MACD", "KDJ", "RSI", "BOLL", "ATR"}
    out: dict = {}
    if "MA" in sets:
        out["ma5"] = sma(closes, 5)
        out["ma10"] = sma(closes, 10)
        out["ma20"] = sma(closes, 20)
        out["ma60"] = sma(closes, 60)
    if "MACD" in sets:
        out["macd"] = macd(closes)
    if "KDJ" in sets:
        out["kdj"] = kdj(highs, lows, closes)
    if "RSI" in sets:
        out["rsi6"] = rsi(closes, 6)
        out["rsi12"] = rsi(closes, 12)
    if "BOLL" in sets:
        out["boll"] = boll(closes, 20, 2.0)
    if "ATR" in sets:
        out["atr14"] = atr(highs, lows, closes, 14)
    return out
