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


def _amplitude(highs: list[float], lows: list[float], closes: list[float],
               window: int, offset: int = 0) -> float:
    """近 `window` 根 K 线的平均振幅（%）。

    `offset` 用来把窗口整体往前挪，取「不含最近 N 日」的基准段。
    缩量收窄必须这么算：如果分子分母的窗口重叠，比值会被自己的近期数据
    稀释，永远在 100 附近晃，量不出「最近是不是明显收窄了」。
    """
    end = len(closes) - offset
    start = max(end - window, 0)
    vals = []
    for i in range(start, end):
        prev = closes[i - 1] if i > 0 and closes[i - 1] else closes[i]
        if prev > 0:
            vals.append((highs[i] - lows[i]) / prev * 100)
    return sum(vals) / len(vals) if vals else 0.0


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
    # MA10 与 MA60 的兜底方向不同：MA60 数据不足时用全段均值（长期趋势仍成立），
    # 而 MA10 不足 10 天说明是新股，此时 MA10 与 MA20 同值不会制造虚假的
    # 「均线粘合」——直接让粘合度算出来是 0，选股时会被当成「极度粘合」，
    # 所以下面粘合度计算遇到 min<=0 或新股会跳过。
    ma10 = sum(closes[-10:]) / 10 if n >= 10 else ma20
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
    prev_chg = (closes[-2] / closes[-3] - 1) if n >= 3 and closes[-3] else 0.0

    # ---- 蓄势类因子：启动前夜的形态。全都「越小越好」，方向在 fields 里声明
    #
    # 两个窗口都**不含今日**（offset=1 / offset=6）。这是踩过的坑：初版把今日
    # 算进「近 5 日振幅」，于是「缩量收窄」与「今日放量启动」在同一只票上
    # 天然互斥——放量当天振幅必然放大，squeeze 立刻失去信号。实测叠加
    # `volratio ≥ 1.4` 之后全市场只剩 1 只、再叠位置约束就是 0 只，
    # 策略静默地永远选不出票。蓄势描述的是「启动之前」的状态，本来就不该
    # 把启动当天算进去。
    amp5 = _amplitude(highs, lows, closes, 5, offset=1)
    amp_base = _amplitude(highs, lows, closes, 20, offset=6)
    squeeze = round(amp5 / amp_base * 100, 2) if amp_base > 0 else 100.0

    tripod = [ma5, ma10, ma20]
    converge = (round((max(tripod) / min(tripod) - 1) * 100, 2)
                if n >= 20 and min(tripod) > 0 else 0.0)

    high5 = max(highs[-5:]) if highs else closes[-1]

    # 连板数：从最新一根往前数连续涨停的天数。30 天封顶——A 股再长的连板
    # 几乎不存在，而不封顶会让一条脏数据（比如除权日的假涨跌幅）数出几百。
    limit_cnt = 0
    for k in range(min(n - 1, 30)):
        i = n - 1 - k
        prev = closes[i - 1] if i > 0 and closes[i - 1] else 0.0
        if prev > 0 and (closes[i] / prev - 1) >= limit - 0.002:
            limit_cnt += 1
        else:
            break

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
        # ---- 蓄势与回踩
        "squeeze": squeeze,
        "ma_converge": converge,
        "dev_ma5": round((closes[-1] / ma5 - 1) * 100, 2) if ma5 > 0 else 0.0,
        "dev_ma10": round((closes[-1] / ma10 - 1) * 100, 2) if ma10 > 0 else 0.0,
        "pullback5": round((high5 - closes[-1]) / high5 * 100, 2) if high5 > 0 else 0.0,
        # ---- 板块联动：必须等全市场算完才能填，先占位，见 cross_section()
        "ind_limit_cnt": 0.0,
        "ind_limit_prev": 0.0,
        "ind_strength": 0.0,
        "ind_rank_pct": 50.0,
        # ---- 连板数（打板策略的硬门槛）
        "limit_cnt": float(limit_cnt),
        # 中间量，不入库（不在 FACTOR_COLUMNS 里，写库时自然被忽略）：
        # 「同日行业涨停家数」和「昨日行业涨停家数」差一天，就是板块联动
        # 第一天的判据。存下来会被下一个交易日的同步覆盖，没有意义。
        "_prev_limit_up": 1.0 if prev_chg >= limit - 0.002 else 0.0,
    }


def cross_section(rows: list[dict]) -> None:
    """按行业回填截面因子，就地修改 `rows`。

    单票函数算不出「同行业有几家涨停」——那是跨票聚合，必须等全市场
    （或当前批次）都算完。之前把这一步漏掉，`ind_limit_cnt` 恒为 0，
    板块联动策略会永远选不出东西，而且看起来像"今天板块没联动"。

    `ind_rank_pct` 用**组内涨幅排名 ÷ (组内数量-1)**：0 是最强、100 是最弱。
    除数是 n-1 而不是 n，这样单只票独占一个行业时不会除以 0，
    而只剩一只票的行业本来就谈不上"排名"。
    """
    buckets: dict[str, list[dict]] = {}
    for row in rows:
        buckets.setdefault(str(row.get("industry") or "其他"), []).append(row)

    for group in buckets.values():
        total = len(group)
        limit_cnt = sum(1 for r in group if float(r.get("limit_up") or 0) >= 1)
        prev_cnt = sum(1 for r in group if float(r.get("_prev_limit_up") or 0) >= 1)
        strength = (sum(float(r.get("change_pct") or 0.0) for r in group) / total
                    if total else 0.0)
        ranked = sorted(group, key=lambda r: float(r.get("change_pct") or 0.0),
                        reverse=True)
        denom = max(total - 1, 1)
        for rank, row in enumerate(ranked):
            row["ind_limit_cnt"] = float(limit_cnt)
            row["ind_limit_prev"] = float(prev_cnt)
            row["ind_strength"] = round(strength, 2)
            row["ind_rank_pct"] = round(rank / denom * 100, 1)
        for row in group:
            row.pop("_prev_limit_up", None)
