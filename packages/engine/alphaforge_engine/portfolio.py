"""持仓、账户绩效与风控校验（SRS M06 / M09 的只读侧实现）。

持仓的唯一来源是实验室的模拟盘账户：计划卡说要买、次日开盘成交、账本记下
股数与成本，这里只把各策略账户合并成一张总表和一条净值曲线。没有种子持仓——
页面上每一行都能在成交流水里找到对应的那笔买单。
"""

from __future__ import annotations

from alphaforge_core.constants import BOARD_META, RISK_LIMITS, RiskLevel, board_allowed, trade_fee
from alphaforge_core.metrics import summarize
from alphaforge_core.universe import board_key

from .market import MarketEngine
from .paper import ORDER_KIND_LABEL


def build_live_portfolio(engine: MarketEngine, accounts: dict[str, dict]) -> dict:
    """合并各策略账户 → 持仓总表 + 净值曲线 + 绩效。

    同一只票被两条策略同时持有时合成一行：并排两行同一个代码，权重各自只占
    自己账户的比例，加起来才对得上这张总表。
    """
    today = engine.trade_date.isoformat() if engine.trade_date else ""
    cash = 0.0
    capital = 0.0
    merged: dict[str, dict] = {}
    pending: list[dict] = []

    for account in accounts.values():
        cash += float(account.get("cash") or 0.0)
        capital += float(account.get("capital") or 0.0)
        label = account.get("name") or account.get("id") or ""
        # 挂单还没成交的委托：持仓页要能看见「计划卡说要买、但钱还没花出去」这一段，
        # 否则今天挂的卡明天才成交，页面上一整天都像没反应。
        for order in account.get("orders") or []:
            kind = order.get("kind") or "ENTRY"
            pending.append({
                "code": order["code"], "name": order.get("name") or "",
                "kind": kind, "kind_label": ORDER_KIND_LABEL.get(kind, kind),
                "shares": int(order.get("shares") or 0),
                "reason": order.get("reason") or "",
                "strategy": label, "created": order.get("created") or "",
            })
        for pos in account.get("positions") or []:
            shares = int(pos.get("shares") or 0)
            if shares <= 0:
                continue
            bars = engine.bars(pos["code"], limit=2)
            price = float(bars[-1]["close"])
            prev = float(bars[-2]["close"]) if len(bars) > 1 else price
            cost = float(pos.get("cost") or 0.0)
            row = merged.get(pos["code"])
            if row is None:
                meta = engine.symbol(pos["code"])
                row = merged[pos["code"]] = {
                    "code": pos["code"], "name": meta["name"], "industry": meta["industry"],
                    "shares": 0, "sellable": 0, "cost_amount": 0.0, "market_value": 0.0,
                    "day_pnl": 0.0, "price": price, "prev": prev, "stop": 0.0,
                    "open_date": "", "strategies": [],
                }
            row["shares"] += shares
            row["cost_amount"] += cost * shares
            row["market_value"] += price * shares
            # 今天开盘建的仓，当日盈亏要跟成本比；此前建的跟昨收比
            row["day_pnl"] += (price - (cost if pos.get("open_date") == today else prev)) * shares
            if pos.get("open_date") != today:
                row["sellable"] += shares
            if pos.get("stop"):
                row["stop"] = max(row["stop"], float(pos["stop"]))
            day = pos.get("open_date") or ""
            if day and (not row["open_date"] or day < row["open_date"]):
                row["open_date"] = day
            if label not in row["strategies"]:
                row["strategies"].append(label)

    market_value = sum(r["market_value"] for r in merged.values())
    nav = market_value + cash
    rows = []
    for r in merged.values():
        cost = r.pop("cost_amount") / r["shares"]
        price, prev = r["price"], r["prev"]
        rows.append({
            "code": r["code"], "name": r["name"], "industry": r["industry"],
            "strategy": " / ".join(r["strategies"]),
            "shares": r["shares"], "sellable": r["sellable"], "cost": round(cost, 3),
            "price": price, "change_pct": round((price / prev - 1) * 100, 2) if prev else 0.0,
            "market_value": round(r["market_value"], 2),
            "pnl": round((price - cost) * r["shares"], 2),
            "pnl_pct": round((price / cost - 1) * 100, 2) if cost else 0.0,
            "day_pnl": round(r["day_pnl"], 2),
            "weight": round(r["market_value"] / nav * 100, 2) if nav else 0.0,
            "stop": round(r["stop"], 3),
            "dist_stop_pct": round((price / r["stop"] - 1) * 100, 2) if r["stop"] else None,
            "open_date": r["open_date"],
        })
    rows.sort(key=lambda r: r["market_value"], reverse=True)

    ind: dict[str, float] = {}
    for r in rows:
        ind[r["industry"]] = ind.get(r["industry"], 0.0) + r["market_value"]
    industry_alloc = [
        {"name": k, "value": round(v, 2), "weight": round(v / nav * 100, 2)}
        for k, v in sorted(ind.items(), key=lambda kv: kv[1], reverse=True)
    ]

    day_pnl = sum(r["day_pnl"] for r in rows)
    total_pnl = nav - capital
    curve = merged_equity(accounts)
    # 盘中打开页面时账户还没推进到今天，曲线最后一点停在昨收，而上面的净值
    # 已经是实时价——两个数对不上会像是算错了，所以补一个当日的盯市点。
    if today and (not curve or curve[-1]["date"] < today):
        curve.append({"date": today, "value": round(nav, 2)})

    return {
        "summary": {
            "nav": round(nav, 2),
            "capital": round(capital, 2),
            "market_value": round(market_value, 2),
            "cash": round(cash, 2),
            "position_ratio": round(market_value / nav * 100, 2) if nav else 0,
            "day_pnl": round(day_pnl, 2),
            "day_pnl_pct": round(day_pnl / (nav - day_pnl) * 100, 2) if nav - day_pnl else 0,
            "total_pnl": round(total_pnl, 2),
            "total_pnl_pct": round(total_pnl / capital * 100, 2) if capital else 0,
            "holdings_count": len(rows),
        },
        "holdings": rows,
        "pending": pending,
        "industry_alloc": industry_alloc,
        "equity_curve": curve,
        "performance": summarize([p["value"] for p in curve]),
        "accounts": [account_brief(engine, a) for a in accounts.values()],
    }


def account_brief(engine: MarketEngine, account: dict) -> dict:
    """单账户一行的现状，给页面标清这张总表由哪几本账合起来。"""
    positions = [p for p in account.get("positions") or [] if int(p.get("shares") or 0) > 0]
    market_value = sum(
        float(engine.bars(p["code"], limit=1)[-1]["close"]) * int(p["shares"]) for p in positions
    )
    cash = float(account.get("cash") or 0.0)
    nav = market_value + cash
    capital = float(account.get("capital") or 0.0)
    return {
        "id": account.get("id"),
        "name": account.get("name") or account.get("id"),
        "last_run": account.get("last_run"),
        "nav": round(nav, 2),
        "cash": round(cash, 2),
        "market_value": round(market_value, 2),
        "pnl": round(nav - capital, 2),
        "pnl_pct": round((nav / capital - 1) * 100, 2) if capital else 0.0,
        "positions": len(positions),
    }


def merged_equity(accounts: dict[str, dict]) -> list[dict]:
    """把各账户的净值曲线按日期对齐相加。

    账户起始日不同时按「前值结转」补：某本账当天还没数据，就用它上一格的净值，
    而不是当成 0——把缺格当 0 会让总净值在某一天凭空腰斩。
    """
    series = [{r["date"]: float(r["value"]) for r in (a.get("equity") or [])}
              for a in accounts.values()]
    out = []
    for date in sorted({d for s in series for d in s}):
        total = 0.0
        for s in series:
            marks = [d for d in s if d <= date]
            if marks:
                total += s[max(marks)]
        out.append({"date": date, "value": round(total, 2)})
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


def trade_change_pct(engine: MarketEngine, code: str, trade_date: str) -> float | None:
    """成交那一格的涨幅：模拟盘按开盘价成交，所以是开盘价相对昨收。

    用开盘而不是收盘，是因为收盘价在 15:00 才成立——拿它当「买入当时的涨幅」，
    等于用还没发生的价格去解释 09:30 的那笔单。
    """
    rows = engine.bars(code)
    for i, bar in enumerate(rows):
        if bar["date"] != trade_date:
            continue
        if not i:
            return None
        prev = rows[i - 1]["close"]
        return round((bar["open"] / prev - 1) * 100, 2) if prev else None
    return None


# --------------------------------------------------------------------- 风控
def check_plan(engine: MarketEngine, positions: dict, code: str, shares: int, price: float,
               profile: dict | None = None) -> dict:
    """事前风控校验（FR-RMS-01/02/03/04/08）。positions 为 build_live_portfolio 结果。

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


def trade_stats(trades: list[dict]) -> dict:
    total_fee = sum(t["fee"] for t in trades)
    total_tax = sum(t["tax"] for t in trades)
    # 简易配对盈亏：按 (账户, 代码) FIFO。跨账户混一个簿子会把 A 策略的卖出
    # 配到 B 策略的买入上，两边的成本口径本来就不一样。
    books: dict[tuple[str | None, str], list[tuple[float, int]]] = {}
    wins = 0
    losses = 0
    profit = 0.0
    gross_win = 0.0
    for t in sorted(trades, key=lambda r: r["trade_date"]):
        book = (t.get("strategy"), t["code"])
        if t["side"] == "BUY":
            books.setdefault(book, []).append((t["price"], t["shares"]))
            continue
        remain = t["shares"]
        cost = 0.0
        while remain > 0 and books.get(book):
            p, q = books[book][0]
            take = min(q, remain)
            cost += p * take
            remain -= take
            if q == take:
                books[book].pop(0)
            else:
                books[book][0] = (p, q - take)
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
