"""区间信号与交易计划卡（SRS M03 ★核心差异）。

区间生成采用 ATR 波动率法 + 通道法的合成口径，输出结构严格对齐 SRS 5.3 核心数据契约。
"""

from __future__ import annotations

import datetime as dt
import math
import zlib

from alphaforge_core.constants import RISK_LIMITS, SIGNAL_LABEL, SignalType
from alphaforge_core.indicators import atr, boll

from .market import MarketEngine
from .profile import board_tag

STRATEGY_NAMES = {
    "multifactor": "多因子质量价值",
    "momentum": "动量趋势突破",
    "flow": "主力资金跟随",
    "lowvol": "低波红利",
}


def _plan_id(code: str, as_of: str, salt: str) -> str:
    h = zlib.crc32(f"{code}{as_of}{salt}".encode()) % 10000
    return f"PLAN-{as_of.replace('-', '')}-{code.split('.')[0]}-{h:04d}"


def build_plan(engine: MarketEngine, code: str, nav: float = 1_000_000,
               strategy_id: str = "multifactor", signal_type: str = "ENTRY",
               salt: str = "", strategy_name: str | None = None) -> dict:
    sym = engine.symbol(code)
    bars = engine.bars(code, limit=180)
    closes = [b["close"] for b in bars]
    highs = [b["high"] for b in bars]
    lows = [b["low"] for b in bars]
    ref = closes[-1]
    a = next((v for v in reversed(atr(highs, lows, closes, 14)) if v is not None), ref * 0.02)
    a = a or ref * 0.02
    b = boll(closes, 20, 2.0)
    upper = next((v for v in reversed(b["upper"]) if v is not None), ref * 1.05)
    lower = next((v for v in reversed(b["lower"]) if v is not None), ref * 0.95)

    # 建仓区：以参考价为中心、ATR 偏置；受布林带上下轨约束
    entry_low = max(ref - 0.55 * a, lower)
    entry_high = min(ref + 0.15 * a, max(upper, ref + 0.15 * a))
    entry_mid = (entry_low + entry_high) / 2

    stop_high = entry_low - 1.15 * a
    stop_low = stop_high - 0.5 * a
    stop_mid = (stop_high + stop_low) / 2

    tp1_low = entry_high + 2.2 * a
    tp1_high = tp1_low + 1.0 * a
    tp2_low = entry_high + 4.4 * a
    tp2_high = tp2_low + 1.4 * a
    tp_mid = (tp1_low + tp1_high + tp2_low + tp2_high) / 4

    add_low = entry_low - 0.85 * a
    add_high = entry_low - 0.35 * a
    watch_low = entry_low - 2.6 * a
    watch_high = entry_high + 2.2 * a

    risk_per_share = max(entry_mid - stop_mid, 0.01)
    risk_budget = nav * RISK_LIMITS["risk_per_trade"]
    shares = math.floor(risk_budget / risk_per_share / 100) * 100
    shares = max(shares, 100)
    weight = shares * entry_mid / nav * 100

    rr = round((tp_mid - entry_mid) / risk_per_share, 2)

    # 置信度：动量 + 资金 + 波动适配度（0-100）
    mom20 = ref / closes[-21] - 1 if len(closes) > 21 else 0
    flow = engine.series(code).main_net[-1] / max(engine.series(code).amount[-1], 1)
    vol_pen = min(abs(a / ref - 0.022) * 900, 18)
    confidence = 62 + max(min(mom20 * 160, 14), -12) + max(min(flow * 120, 12), -8) - vol_pen
    confidence = round(max(38, min(93, confidence)), 1)

    tranche_prices = [round(entry_high - i * (entry_high - entry_low) / 3, 2) for i in range(3)]
    split = [0.4, 0.3, 0.3]

    flow_5d = sum(engine.series(code).main_net[-5:]) / 1e8
    ma20_val = sum(closes[-20:]) / 20
    ma_gap = (ref / ma20_val - 1) * 100
    band_gap = (ref / upper - 1) * 100
    high60 = max(closes[-60:])
    reason_pos = []
    reason_neg = []

    (reason_pos if mom20 > 0 else reason_neg).append(
        f"20 日动量 {mom20 * 100:+.1f}%"
    )
    (reason_pos if flow > 0 else reason_neg).append(
        f"主力净流入占比 {flow * 100:+.1f}%（5 日累计 {flow_5d:+.2f} 亿）"
    )
    (reason_pos if ma_gap > 0 else reason_neg).append(
        f"现价相对 20 日线 {ma_gap:+.1f}%"
    )
    if band_gap > -3:
        reason_neg.append(f"贴近布林上轨（差 {abs(band_gap):.1f}%），短线空间受限")
    else:
        reason_pos.append(f"距布林上轨仍有 {abs(band_gap):.1f}% 空间")
    if ref / high60 > 0.97:
        reason_neg.append("逼近 60 日高点，追高风险")
    if (sym.get("pe") or 0) > 40:
        reason_neg.append(f"估值偏高（PE {sym.get('pe')}）")
    if not reason_neg:
        reason_neg.append("暂无显著负向因子")

    as_of = engine.trade_date.isoformat()
    executable_from = (engine.trade_date + dt.timedelta(days=3)).isoformat()

    return {
        "plan_id": _plan_id(code, as_of, salt or strategy_id),
        "code": code,
        "name": sym["name"],
        "industry": sym["industry"],
        "board": sym.get("board") or "",
        "board_tag": board_tag(code, sym.get("board") or "", sym.get("exchange") or ""),
        "strategy_id": strategy_id,
        "strategy_name": strategy_name or STRATEGY_NAMES.get(strategy_id, strategy_id),
        "signal_type": signal_type,
        "signal_label": SIGNAL_LABEL.get(SignalType(signal_type), signal_type),
        "status": "PENDING",
        "status_label": "待触发",
        "as_of": as_of,
        "executable_from": executable_from,
        "ref_price": round(ref, 2),
        "zones": {
            "watch": {"low": round(watch_low, 2), "high": round(watch_high, 2)},
            "entry": {"low": round(entry_low, 2), "high": round(entry_high, 2),
                      "confidence": confidence, "method": "ATR+CHANNEL"},
            "add": {"low": round(add_low, 2), "high": round(add_high, 2), "condition": "回踩 20 日线不破"},
            "target": [
                {"level": "TP1", "low": round(tp1_low, 2), "high": round(tp1_high, 2), "ratio": 0.5},
                {"level": "TP2", "low": round(tp2_low, 2), "high": round(tp2_high, 2), "ratio": 0.5},
            ],
            "stop": {"low": round(stop_low, 2), "high": round(stop_high, 2)},
        },
        "sizing": {
            "risk_per_trade_pct": RISK_LIMITS["risk_per_trade"] * 100,
            "shares": shares,
            "weight_pct": round(weight, 2),
            "max_loss": round(shares * risk_per_share, 0),
            "tranches": [
                {"price": tranche_prices[i], "pct": split[i], "shares": int(shares * split[i] // 100 * 100)}
                for i in range(3)
            ],
        },
        "rr": rr,
        "confidence": confidence,
        "invalidation": [
            f"收盘跌破 {round(stop_low, 2)} 且成交量放大 1.5 倍",
            f"所属行业（{sym['industry']}）指数跌破 60 日线",
            f"{executable_from} 起 15 个交易日内未触及建仓区",
        ],
        "time_stop_days": 15,
        "reason": {"positive": reason_pos, "negative": reason_neg},
    }


# 浮盈到这个比例就只给「减仓」不再给「加仓」：利润已经兑现一部分，
# 再往同一标的上加仓等于放大单票敞口，与风控的单票上限是打架的。
TRIM_PNL_PCT = 12.0


# 计划卡默认策略的尝试顺序：从信号最强的往下退。
# 缩量蓄势最严（弱势行情可能当天一只都没有），强势回踩次之，趋势波段兜底。
# 兜底这一层不能省——`multifactor` 在策略精简时就被删掉了，而这里原本
# `STRATEGY_MAP.get("multifactor")` 恒为 None，于是落到一个没有 weights
# 的空壳 spec，走「纯条件筛选」分支，排序时撞上 None 直接 500。
PLAN_FALLBACK: tuple[str, ...] = ("squeeze", "pullback", "trend")


def plan_candidates(engine: MarketEngine, spec: dict | None = None, top_n: int = 6) -> dict:
    """计划卡的候选池来自选股引擎，而不是代码里写死的名单。

    选股结果本身已经是「因子打分 + 行业分散 + ST 过滤」的产物，拿它当输入，
    卡片才会随行情和策略变化；写死名单的问题是行情早就换了，每天推的还是
    同一批票，看起来像功能坏了。

    没指定策略时按 `PLAN_FALLBACK` 依次试，第一个凑出候选的就用：
    缩量蓄势在普跌日可能一只都选不出，此时若直接返回空，首屏计划卡整片空白，
    用户会以为功能坏了。
    """
    from .selector import STRATEGY_MAP, execute

    if spec:
        return execute(engine, spec, top_n=top_n)

    last: dict | None = None
    for sid in PLAN_FALLBACK:
        base = STRATEGY_MAP.get(sid)
        if not base:
            continue
        got = execute(engine, base, top_n=top_n)
        last = got
        if got.get("items"):
            return got
    # 三条策略都选不出票（极端普跌或因子表未就绪）：把最后一次结果返回，
    # 它的 notice / rank_hint 会说清为什么空，比抛异常或返回 None 有用。
    return last or execute(engine, {"id": "plan", "name": "今日计划"}, top_n=top_n)


def active_plans(engine: MarketEngine, nav: float = 1_000_000,
                 holdings: list[dict] | None = None, spec: dict | None = None,
                 top_n: int = 6) -> dict:
    """当日计划卡：候选取自选股 Top-N，动作由持仓状态推导。

    返回 `{"items": [...], "source": {...}}`。source 记录「这批卡是从哪个策略、
    多大的股票池、多少只匹配里选出来的」，前端据此把来源写在标题边上——
    只说「今日计划卡」而不交代来源，用户没法判断它该不该信。
    """
    selection = plan_candidates(engine, spec, top_n)
    held = {h["code"]: h for h in (holdings or [])}

    plans: list[dict] = []
    for item in selection.get("items") or []:
        code = item["code"]
        hold = held.get(code)
        if hold is None:
            signal_type = "ENTRY"
        elif float(hold.get("pnl_pct") or 0) >= TRIM_PNL_PCT:
            signal_type = "TRIM"
        else:
            signal_type = "ADD"

        plan = build_plan(
            engine, code, nav=nav,
            strategy_id=selection.get("strategy_id") or "multifactor",
            strategy_name=selection.get("strategy_name"),
            signal_type=signal_type,
            salt=signal_type,
        )
        # 状态推导：现价进入建仓区 → 已触发
        entry = plan["zones"]["entry"]
        if entry["low"] <= plan["ref_price"] <= entry["high"]:
            plan["status"] = "TRIGGERED"
            plan["status_label"] = "已触发"
        # 选股排名带进卡片：第 3 名选出来的和第 20 名不是一回事
        plan["rank"] = item.get("rank")
        plan["score"] = item.get("score")
        plans.append(plan)

    return {
        "items": plans,
        "source": {
            "strategy_id": selection.get("strategy_id"),
            "strategy_name": selection.get("strategy_name"),
            "as_of": selection.get("as_of"),
            "universe": selection.get("universe"),
            "universe_size": selection.get("universe_size"),
            "matched_count": selection.get("matched_count"),
            "top_n": top_n,
            "notice": selection.get("notice"),
        },
    }
