"""持仓、账户绩效与风控校验（SRS M06 / M09 的只读侧实现）。"""

from __future__ import annotations

import datetime as dt

from alphaforge_core.constants import BOARD_META, RISK_LIMITS, RiskLevel, board_allowed, trade_fee
from alphaforge_core.metrics import summarize
from alphaforge_core.universe import board_key

from .market import MarketEngine

# 种子持仓：code -> (数量, 成本价, 建仓日)
SEED_HOLDINGS: list[tuple[str, int, float, str]] = [
    ("600519.SH", 200, 1620.50, "2026-06-18"),
    ("300750.SZ", 500, 232.40, "2026-08-05"),
    ("600036.SH", 3000, 36.80, "2026-05-12"),
    ("002594.SZ", 800, 288.60, "2026-09-02"),
    ("601899.SH", 8000, 16.90, "2026-07-21"),
    ("600900.SH", 5000, 28.10, "2026-04-09"),
]
SEED_CASH = 186_420.0


def build_portfolio(engine: MarketEngine, holdings: list[dict] | None = None,
                    cash: float | None = None) -> dict:
    holdings = holdings or [
        {"code": c, "shares": q, "cost": p, "open_date": d} for c, q, p, d in SEED_HOLDINGS
    ]
    cash = SEED_CASH if cash is None else cash

    rows = []
    market_value = 0.0
    day_pnl = 0.0
    total_cost = 0.0
    for h in holdings:
        bars = engine.bars(h["code"], limit=2)
        price = bars[-1]["close"]
        prev = bars[-2]["close"]
        mv = price * h["shares"]
        pnl = (price - h["cost"]) * h["shares"]
        market_value += mv
        day_pnl += (price - prev) * h["shares"]
        total_cost += h["cost"] * h["shares"]
        rows.append({
            "code": h["code"],
            "name": engine.symbol(h["code"])["name"],
            "industry": engine.symbol(h["code"])["industry"],
            "shares": h["shares"],
            "sellable": max(h["shares"] - (h["shares"] if h["open_date"] == engine.dates[-1].isoformat() else 0), 0),
            "cost": round(h["cost"], 3),
            "price": price,
            "change_pct": round((price / prev - 1) * 100, 2),
            "market_value": round(mv, 2),
            "pnl": round(pnl, 2),
            "pnl_pct": round((price / h["cost"] - 1) * 100, 2),
            "open_date": h["open_date"],
        })

    nav = market_value + cash
    for r in rows:
        r["weight"] = round(r["market_value"] / nav * 100, 2)
    rows.sort(key=lambda r: r["market_value"], reverse=True)

    # 行业分布
    ind: dict[str, float] = {}
    for r in rows:
        ind[r["industry"]] = ind.get(r["industry"], 0.0) + r["market_value"]
    industry_alloc = [
        {"name": k, "value": round(v, 2), "weight": round(v / nav * 100, 2)}
        for k, v in sorted(ind.items(), key=lambda kv: kv[1], reverse=True)
    ]

    curve = equity_curve(engine, holdings, cash, days=len(engine.dates))
    perf = summarize([p["value"] for p in curve])

    return {
        "summary": {
            "nav": round(nav, 2),
            "market_value": round(market_value, 2),
            "cash": round(cash, 2),
            "position_ratio": round(market_value / nav * 100, 2) if nav else 0,
            "day_pnl": round(day_pnl, 2),
            "day_pnl_pct": round(day_pnl / (nav - day_pnl) * 100, 2) if nav - day_pnl else 0,
            "total_pnl": round(market_value - total_cost, 2),
            "total_pnl_pct": round((market_value - total_cost) / total_cost * 100, 2) if total_cost else 0,
            "holdings_count": len(rows),
        },
        "holdings": rows,
        "industry_alloc": industry_alloc,
        "equity_curve": curve,
        "performance": perf,
    }


def equity_curve(engine: MarketEngine, holdings: list[dict], cash: float, days: int = 180) -> list[dict]:
    """按建仓日回放账户净值：建仓前该笔资金按成本计入现金等价物，
    使区间收益率与「当前市值 − 持仓成本」口径一致（SRS NFR-D-02 对账一致性）。"""
    series = [engine.bars(h["code"], limit=days) for h in holdings]
    n = min(len(s) for s in series) if series else 0
    out = []
    for i in range(n):
        date = series[0][i]["date"]
        value = cash
        for s, h in zip(series, holdings):
            if date >= h.get("open_date", ""):
                value += s[i]["close"] * h["shares"]
            else:
                value += h["cost"] * h["shares"]
        out.append({"date": date, "value": round(value, 2)})
    return out


def benchmark_curve(engine: MarketEngine, days: int = 180) -> list[dict]:
    """基准：全样本等权合成指数，归一化到 100 便于与净值曲线同图对比。"""
    idx = engine.index_series()
    n = min(days, len(idx.raw_close))
    base = idx.raw_close[-n]
    return [
        {"date": idx.dates[-n + i].isoformat(), "value": round(idx.raw_close[-n + i] / base * 100, 2)}
        for i in range(n)
    ]


# --------------------------------------------------------------------- 风控
def check_plan(engine: MarketEngine, positions: dict, code: str, shares: int, price: float,
               profile: dict | None = None) -> dict:
    """事前风控校验（FR-RMS-01/02/03/04/08）。positions 为 build_portfolio 结果。

    profile 来自「个人设置」：购买权限在这里做硬拦截，费用按账户成本口径预估。
    """
    nav = positions["summary"]["nav"]
    amount = shares * price
    industry = engine.symbol(code)["industry"]
    board = board_key(code)
    allowed = board_allowed(board, profile)

    industry_mv = sum(h["market_value"] for h in positions["holdings"] if h["industry"] == industry)
    new_single = amount / nav if nav else 1
    new_total = (positions["summary"]["market_value"] + amount) / nav if nav else 1
    new_industry = (industry_mv + amount) / nav if nav else 1

    events = []

    def add(rule: str, level: str, detail: str, action: str):
        events.append({"rule": rule, "level": level, "detail": detail, "action": action})

    if not allowed:
        label = next((b["label"] for b in BOARD_META if b["key"] == board), board)
        add("购买权限", RiskLevel.BLOCK,
            f"{label}未在「个人设置 → 购买权限」中开通，本账户不可买入",
            "开通该板块权限，或换一个已开通板块的标的")
    if new_single > RISK_LIMITS["max_single_weight"]:
        add("单票上限", RiskLevel.WARN, f"成交后单票占比 {new_single * 100:.1f}%，超过上限 {RISK_LIMITS['max_single_weight'] * 100:.0f}%", "建议按上限降权")
    if new_industry > RISK_LIMITS["max_industry_weight"]:
        add("行业集中度", RiskLevel.WARN, f"{industry} 行业占比 {new_industry * 100:.1f}%，超过上限 {RISK_LIMITS['max_industry_weight'] * 100:.0f}%", "拒绝或替换同行业标的")
    if new_total > RISK_LIMITS["max_gross_exposure"]:
        add("总仓位上限", RiskLevel.BLOCK, f"建仓后总仓位 {new_total * 100:.1f}%，超过上限 {RISK_LIMITS['max_gross_exposure'] * 100:.0f}%", "禁止新开仓")
    if amount > nav * 0.5:
        add("单笔金额异常", RiskLevel.BLOCK, f"单笔金额 {amount:,.0f} 元占净值 {amount / nav * 100:.0f}%", "拒绝执行")

    passed = not any(e["level"] == RiskLevel.BLOCK for e in events)
    return {
        "passed": passed,
        "nav": round(nav, 2),
        "amount": round(amount, 2),
        "single_weight_after": round(new_single * 100, 2),
        "industry_weight_after": round(new_industry * 100, 2),
        "gross_after": round(new_total * 100, 2),
        "board": board,
        "board_allowed": allowed,
        "fee": trade_fee(amount, "BUY", profile),
        "events": events or [{"rule": "全部通过", "level": RiskLevel.INFO, "detail": "未触发任何限额", "action": "可执行"}],
        "limits": RISK_LIMITS,
    }


def seed_trades(engine: MarketEngine) -> list[dict]:
    """种子成交流水：建仓单 + 若干已了结交易。"""
    rows = []
    seq = 0
    for code, qty, cost, day in SEED_HOLDINGS:
        seq += 1
        rows.append({
            "id": f"T{seq:04d}",
            "code": code,
            "name": engine.symbol(code)["name"],
            "side": "BUY",
            "price": cost,
            "shares": qty,
            "amount": round(cost * qty, 2),
            "fee": max(5.0, round(cost * qty * 0.00025, 2)),
            "tax": 0.0,
            "trade_date": day,
            "note": "计划内建仓",
        })
    closed = [
        ("601012.SH", "BUY", 19.20, 3000, "2026-05-08", "计划内建仓"),
        ("601012.SH", "SELL", 18.05, 3000, "2026-06-11", "跌破止损，纪律离场"),
        ("002415.SZ", "BUY", 33.40, 2000, "2026-06-02", "计划内建仓"),
        ("002415.SZ", "SELL", 36.80, 2000, "2026-07-30", "触及 TP1 减半后了结"),
        ("600030.SH", "BUY", 28.90, 4000, "2026-07-14", "追高建仓"),
        ("600030.SH", "SELL", 27.10, 4000, "2026-08-18", "扛单后割肉"),
        ("603259.SH", "BUY", 55.20, 1200, "2026-08-25", "计划内建仓"),
        ("603259.SH", "SELL", 61.40, 1200, "2026-09-26", "触及 TP2"),
        ("000651.SZ", "BUY", 43.10, 3000, "2026-09-10", "计划内建仓"),
        ("000651.SZ", "SELL", 41.60, 3000, "2026-10-09", "时间止损"),
    ]
    for code, side, price, qty, day, note in closed:
        seq += 1
        amount = price * qty
        rows.append({
            "id": f"T{seq:04d}",
            "code": code,
            "name": engine.symbol(code)["name"],
            "side": side,
            "price": price,
            "shares": qty,
            "amount": round(amount, 2),
            "fee": max(5.0, round(amount * 0.00025, 2)),
            "tax": round(amount * 0.0005, 2) if side == "SELL" else 0.0,
            "trade_date": day,
            "note": note,
        })
    rows.sort(key=lambda r: r["trade_date"], reverse=True)
    return rows


def trade_stats(trades: list[dict]) -> dict:
    total_fee = sum(t["fee"] for t in trades)
    total_tax = sum(t["tax"] for t in trades)
    # 简易配对盈亏：按 code 的 BUY/SELL 顺序 FIFO
    books: dict[str, list[tuple[float, int]]] = {}
    wins = 0
    losses = 0
    profit = 0.0
    gross_win = 0.0
    for t in sorted(trades, key=lambda r: r["trade_date"]):
        if t["side"] == "BUY":
            books.setdefault(t["code"], []).append((t["price"], t["shares"]))
            continue
        remain = t["shares"]
        cost = 0.0
        while remain > 0 and books.get(t["code"]):
            p, q = books[t["code"]][0]
            take = min(q, remain)
            cost += p * take
            remain -= take
            if q == take:
                books[t["code"]].pop(0)
            else:
                books[t["code"]][0] = (p, q - take)
        if remain == 0:
            pnl = t["price"] * t["shares"] - cost
            profit += pnl
            if pnl >= 0:
                wins += 1
                gross_win += pnl
            else:
                losses += 1
    total = wins + losses
    return {
        "trade_count": len(trades),
        "closed_count": total,
        "win_rate": round(wins / total * 100, 1) if total else 0.0,
        "realized_pnl": round(profit, 2),
        "total_fee": round(total_fee, 2),
        "total_tax": round(total_tax, 2),
        "cost_ratio": round((total_fee + total_tax) / gross_win * 100, 2) if gross_win else 0.0,
    }
