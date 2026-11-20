"""盘中盯盘与收盘复盘：把「选出来的 2~3 只」交给模型持续看，看到收盘。

分工很清楚：
- 这个模块只**组装事实 + 组织语言**，不产生任何买卖动作；
- 行情事实全部来自本地库（快照 / 日线 / 计划卡），模型不许自己编数字；
- 没配置模型时退回规则版（`source="rule"`），结论仍然给出，只是不带推理，
  前端会明确标注——宁可说「这是规则判断」，也不要装作是模型说的。

为什么一次调用分析所有标的而不是一只一次
--------------------------------------
盘中每 5 分钟一轮，一天约 50 轮。若每只票单独调一次，50×5=250 次/天，
成本与延迟都不划算；打包成一次请求后是 50 次/天，且模型能横向比较
「哪只在拖后腿」，这是单票独立分析给不了的信息。
"""

from __future__ import annotations

from . import llm

STANCES = ("持有", "减仓", "观察", "放弃")

SYSTEM_WATCH = (
    "你是一名 A 股盘中盯盘助手。你会收到市场宽度事实、持仓与候选股的行情事实。"
    "任务：对每只标的给出一个操作倾向和一句理由。\n"
    "硬性要求：\n"
    "1. 只能使用我提供的数字，不许推测、不许编造任何价格或指标；\n"
    "2. stance 只能取「持有 / 减仓 / 观察 / 放弃」之一；\n"
    "3. note 不超过 40 字，必须引用一个具体数字（如涨跌幅、距止损、量比）；\n"
    "4. summary 不超过 80 字，说清当前市场处于什么状态、该激进还是该收手；\n"
    "5. 只输出 JSON，形如 "
    '{"mood":"偏强|中性|偏弱","summary":"...",'
    '"items":[{"code":"600000.SH","stance":"持有","note":"..."}]}'
)

SYSTEM_REVIEW = (
    "你是一名 A 股收盘复盘助手。你会收到当日的市场宽度、模拟盘成交与净值变化。"
    "任务：用事实复盘今天做对了什么、做错了什么，以及明天要注意什么。\n"
    "硬性要求：\n"
    "1. 只能引用我给出的数字；\n"
    "2. 不要写「明天必涨」这类预测，只写观察点与纪律；\n"
    "3. 只输出 JSON，形如 "
    '{"headline":"...","points":["...","..."],"tomorrow":["...","..."]}'
    "；points 2~4 条，tomorrow 1~3 条，每条不超过 45 字。"
)


# ---------------------------------------------------------------- 事实组装
def _num(v, default=0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def build_watch_context(*, breadth: dict, index: dict | None,
                        holdings: list[dict], candidates: list[dict],
                        as_of: str) -> dict:
    """把行情事实压成一段模型能读的紧致 JSON。字段名用中文，省 token。"""
    return {
        "时间": as_of,
        "市场": {
            "上涨": breadth.get("up", 0),
            "下跌": breadth.get("down", 0),
            "涨停": breadth.get("limit_up", 0),
            "跌停": breadth.get("limit_down", 0),
            "涨幅中位数": breadth.get("median_pct", 0.0),
            "成交额亿": breadth.get("amount", 0.0),
            "沪深300": (index or {}).get("change_pct"),
        },
        "持仓": [
            {
                "代码": h.get("code"), "名称": h.get("name"),
                "现价": h.get("price"), "涨跌幅": h.get("change_pct"),
                "成本": h.get("cost"), "浮动盈亏": h.get("pnl_pct"),
                "止损价": h.get("stop"),
                "距止损": _stop_gap(h),
                "行业": h.get("industry"),
                "行业昨日涨停": h.get("ind_limit_cnt"),
            }
            for h in holdings
        ],
        "候选": [
            {
                "代码": c.get("code"), "名称": c.get("name"),
                "现价": c.get("live_price") or c.get("ref_price"),
                "涨跌幅": c.get("change_pct"),
                "量比": c.get("volratio"),
                "策略": c.get("strategy_name"),
                "计划区间": _zone(c),
                "委托状态": c.get("order_state"),
                # 板块背景：同一条涨跌幅，在「行业有 3 家涨停」和「行业全绿」
                # 两种环境里的含义完全相反。不给模型这两个数，它只能对着
                # 涨跌幅说废话——这正是「AI 没分析到点上」的成因之一。
                "行业": c.get("industry"),
                "行业昨日涨停": c.get("ind_limit_cnt"),
                "行业内排名分位": c.get("ind_rank_pct"),
            }
            for c in candidates
        ],
    }


def _stop_gap(h: dict) -> float | None:
    price, stop = _num(h.get("price")), _num(h.get("stop"))
    if not price or not stop:
        return None
    return round((stop - price) / price * 100, 2)


def _zone(c: dict) -> str:
    zones = c.get("zones") or {}
    entry = zones.get("entry") or {}
    lo, hi = _num(entry.get("low")), _num(entry.get("high"))
    if not lo or not hi:
        return "—"
    return f"{lo:.2f}~{hi:.2f}"


# ---------------------------------------------------------------- 规则兜底
def rule_watch(context: dict) -> dict:
    """没模型时的判断：只用计划卡里已有的阈值，不发明新规则。

    它替代不了模型，但有它和没它差别很大——没有它，没配 key 的用户
    打开页面只能看到一堆行情数字，看不出「哪只已经该走了」。
    """
    items = []
    for h in context.get("持仓") or []:
        gap = h.get("距止损")
        pnl = _num(h.get("浮动盈亏"))
        chg = _num(h.get("涨跌幅"))
        if gap is not None and gap >= 0:
            stance, note = "放弃", f"已触及止损价，距止损 {gap:+.2f}%"
        elif gap is not None and gap > -1.5:
            stance, note = "减仓", f"距止损只剩 {gap:.2f}%，风险收益比已经不划算"
        elif chg >= 7:
            stance, note = "减仓", f"单日已涨 {chg:.2f}%，冲高回落概率上升"
        elif pnl >= 10:
            stance, note = "减仓", f"浮盈 {pnl:.1f}%，可考虑兑现一半"
        else:
            stance, note = "持有", f"浮动盈亏 {pnl:.1f}%，距止损 {gap if gap is not None else '—'}%"
        items.append({"code": h.get("代码"), "stance": stance, "note": note})

    for c in context.get("候选") or []:
        state = c.get("委托状态") or ""
        if "待明日开盘" in state:
            note = "已挂委托，等次日开盘成交"
            stance = "观察"
        elif "仓位已满" in state:
            note, stance = "仓位已满，仅作备选", "观察"
        else:
            note = f"未挂单：{state or '状态未知'}"
            stance = "观察"
        items.append({"code": c.get("代码"), "stance": stance, "note": note})

    m = context.get("市场") or {}
    up, down = _num(m.get("上涨")), _num(m.get("下跌"))
    ratio = up / (up + down) if (up + down) else 0.5
    mood = "偏强" if ratio >= 0.6 else ("偏弱" if ratio <= 0.4 else "中性")
    return {
        "source": "rule",
        "mood": mood,
        "summary": (f"上涨 {int(up)} 家 / 下跌 {int(down)} 家，"
                    f"涨停 {int(_num(m.get('涨停')))} 家，中位数 {m.get('涨幅中位数')}%。"
                    f"规则判定：{mood}。"),
        "items": items,
    }


# ---------------------------------------------------------------- 主入口
def analyze_watch(context: dict) -> dict:
    """调模型分析；未配置或失败时退回规则版。"""
    fallback = rule_watch(context)
    if not llm.enabled():
        return fallback
    import json as _json

    got = llm.chat_json([
        {"role": "system", "content": SYSTEM_WATCH},
        {"role": "user", "content": _json.dumps(context, ensure_ascii=False)},
    ], temperature=0.2)
    if not got:
        return {**fallback, "note": "模型调用失败，已退回规则判断"}
    items = []
    for row in (got.get("items") or []):
        code = str(row.get("code") or "").strip()
        stance = str(row.get("stance") or "").strip()
        if not code or stance not in STANCES:
            continue
        items.append({"code": code, "stance": stance,
                      "note": str(row.get("note") or "")[:80]})
    if not items:
        return {**fallback, "note": "模型返回里没有可用条目，已退回规则判断"}
    return {
        "source": "llm",
        "mood": str(got.get("mood") or fallback["mood"])[:8],
        "summary": str(got.get("summary") or fallback["summary"])[:200],
        "items": items,
    }


def _norm_code(raw: object) -> str:
    return str(raw or "").strip().upper()


def build_review_context(*, breadth: dict, index: dict | None, account: dict,
                         trades: list[dict], plans: list[dict], as_of: str,
                         plan_stats: dict | None = None) -> dict:
    wins = [t for t in trades if _num(t.get("pnl")) > 0]
    losses = [t for t in trades if _num(t.get("pnl")) < 0]
    ctx = {
        "日期": as_of,
        "市场": {
            "上涨": breadth.get("up", 0), "下跌": breadth.get("down", 0),
            "涨停": breadth.get("limit_up", 0), "跌停": breadth.get("limit_down", 0),
            "涨幅中位数": breadth.get("median_pct", 0.0),
            "沪深300": (index or {}).get("change_pct"),
        },
        "账户": {
            "净值": account.get("nav"),
            "现金": account.get("cash"),
            "持仓数": len(account.get("positions") or []),
            "今日成交": len(trades),
            "盈利笔数": len(wins), "亏损笔数": len(losses),
            "今日盈亏": round(sum(_num(t.get("pnl")) for t in trades), 2),
        },
        "成交明细": [
            {"代码": t.get("code"), "名称": t.get("name"), "方向": t.get("side"),
             "价格": t.get("price"), "数量": t.get("shares"),
             "盈亏": t.get("pnl"), "原因": t.get("reason")}
            for t in trades[:20]
        ],
        "明日计划": [
            {"代码": p.get("code"), "名称": p.get("name"),
             "策略": p.get("strategy_name"), "类型": p.get("signal_label"),
             "委托状态": p.get("order_state")}
            for p in plans[:10]
        ],
    }
    # 计划执行情况：模型写的复盘如果只说「今天涨了多少」，用户没法判断
    # 这套计划卡到底有没有在按说的执行。挂单/成交/滑点三项直接喂进去，
    # 复盘的那句话才可能落在「今天说买 5 只、成交 3 只，两只是一字板」上。
    if plan_stats:
        ctx["计划执行"] = {
            "挂单": plan_stats.get("order_count"),
            "成交": plan_stats.get("filled_count"),
            "执行率": plan_stats.get("fill_rate"),
            "平均不利滑点(bp)": plan_stats.get("avg_adverse_bps"),
            "未成交": [{"代码": m.get("code"), "名称": m.get("name"),
                        "动作": m.get("kind_label"), "原因": m.get("reason")}
                       for m in (plan_stats.get("missed") or [])][:8],
        }
    return ctx


def analyze_review(context: dict) -> dict:
    """收盘复盘。模型失败时给一份纯事实的规则版。"""
    m = context.get("市场") or {}
    a = context.get("账户") or {}
    up, down = _num(m.get("上涨")), _num(m.get("下跌"))
    ratio = up / (up + down) if (up + down) else 0.5
    mood = "偏强" if ratio >= 0.6 else ("偏弱" if ratio <= 0.4 else "中性")
    today_pnl = _num(a.get("今日盈亏"))
    p = context.get("计划执行") or {}
    plan_point = None
    if p.get("挂单"):
        rate = p.get("执行率")
        bps = p.get("平均不利滑点(bp)")
        plan_point = (f"昨日挂单 {int(_num(p.get('挂单')))} 张、成交 "
                      f"{int(_num(p.get('成交')))} 张"
                      + (f"（执行率 {rate:.0f}%）" if rate is not None else "")
                      + (f"，平均不利滑点 {bps:.0f}bp" if bps is not None else ""))
    fallback = {
        "source": "rule",
        "headline": (f"{mood}，涨停 {int(_num(m.get('涨停')))} 家，"
                     f"账户今日 {today_pnl:+.0f} 元"),
        "points": [
            f"上涨 {int(up)} / 下跌 {int(down)}，中位数 {m.get('涨幅中位数')}%",
            f"今日成交 {int(_num(a.get('今日成交')))} 笔，"
            f"盈 {int(_num(a.get('盈利笔数')))} 亏 {int(_num(a.get('亏损笔数')))}",
            f"当前持仓 {int(_num(a.get('持仓数')))} 只，现金 {_num(a.get('现金')):,.0f}",
        ] + ([plan_point] if plan_point else []),
        "tomorrow": [
            "按计划卡执行，不临时加仓",
            "跌破止损的先走，不等反弹",
        ],
    }
    if not llm.enabled():
        return fallback
    import json as _json

    got = llm.chat_json([
        {"role": "system", "content": SYSTEM_REVIEW},
        {"role": "user", "content": _json.dumps(context, ensure_ascii=False)},
    ], temperature=0.3)
    if not got:
        return {**fallback, "note": "模型调用失败，已退回事实版"}
    points = [str(x)[:90] for x in (got.get("points") or []) if str(x).strip()]
    tomorrow = [str(x)[:90] for x in (got.get("tomorrow") or []) if str(x).strip()]
    if not points:
        return {**fallback, "note": "模型返回不可用，已退回事实版"}
    return {
        "source": "llm",
        "headline": str(got.get("headline") or fallback["headline"])[:120],
        "points": points[:4],
        "tomorrow": tomorrow[:3],
    }
