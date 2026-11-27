"""复盘经验库：把每天「该抓没抓到」的机会与成交盈亏写成 md，再回灌给模型。

为什么落成 md，而不是继续塞进 `autopilot.json`
----------------------------------------------
复盘日记（`autopilot.json` 的 `reviews`）是**一天一篇的结论**，要读得翻 JSON、
要喂模型得先拼字符串。「这套规则错在哪、改过什么、改对没有」是连续文本，
md 同时满足三件事：人能读、模型能整段喂、git 能 diff。所以落盘。

口径（用户定的）
----------------
「T+1 波动大于 3%」= **当日开盘买入、次日开盘就能卖出时的收益 ≥ 3%**。
两头都用开盘价：本系统的成交价就是次日集合竞价的开盘价（见 `paper` 文件头），
用收盘价衡量会出现「账上说赚、实际卖不到」的差——那种记录总结不出经验，
只会总结出幻觉。

归因只有五类，每类都必须落回一条可验证的事实
--------------------------------------------
    买了        当日有成交
    挂了没成    挂了委托，开盘被闸门挡掉（高开区间 / 一字板 / 现金）
    没挂单      计划卡上有，但没有委托（仓位已满 / 弱势 / 模型否决）
    没进候选    在候选池里但排名不够
    不在池子    当日策略条件根本没过（涨停池 / 连板区间 / 量能）
"为什么没买"必须能指到某一闸门，否则这段文字就是事后编故事。
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from . import llm
from .selector import band_label, limit_band, normalize_spec

ROOT = Path(__file__).resolve().parents[3]
JOURNAL_DIR = Path(os.environ.get("AF_JOURNAL_DIR") or (ROOT / "data" / "journal"))
if not JOURNAL_DIR.is_absolute():
    JOURNAL_DIR = ROOT / JOURNAL_DIR
DAYS_DIR = JOURNAL_DIR / "days"
LESSONS_FILE = JOURNAL_DIR / "LESSONS.md"

# 用户定的阈值：开盘买入、次日开盘可卖时的收益。
MIN_EDGE = 3.0
# 每日候选留多少名。计划卡只挂前 top_n 张，但「第 7 名后来涨了 8%」同样值得记，
# 不然所有漏掉的机会都会被归成「不在池子」，看不出是名额问题还是选股问题。
SCREEN_DEPTH = 20
# 喂给模型的经验上限。一首诗长度的经验它记不住，一份财报长度的它抓不住重点。
CONTEXT_CHARS = 6000
# 账户里留几天的候选/跳过流水：归因只需要「买的那天」和「做计划的那天」。
ACCOUNT_KEEP = 5

_MOVERS_SQL = """
SELECT a.code, a.open * a.adj, b.open * b.adj, s.name, s.industry
FROM daily a
JOIN daily b ON b.code = a.code AND b.date = ?
JOIN symbol s ON s.code = a.code
WHERE a.date = ? AND a.open > 0 AND b.open > 0 AND a.adj > 0 AND b.adj > 0
"""


def _prev_date(engine, date_s: str) -> str | None:
    dates = [d.isoformat() for d in engine.dates]
    if date_s not in dates:
        return None
    i = dates.index(date_s)
    return dates[i - 1] if i > 0 else None


def _fmt(value: float | None, digits: int = 0, sign: bool = False) -> str:
    if value is None:
        return "—"
    text = f"{value:,.{digits}f}"
    return f"+{text}" if sign and value > 0 else text


def _table(headers: list[str], rows: list[list[str]]) -> str:
    head = "| " + " | ".join(headers) + " |"
    rule = "|" + "|".join("---" for _ in headers) + "|"
    body = ["| " + " | ".join(str(c) for c in row) + " |" for row in rows]
    return "\n".join([head, rule, *body])


# ---------------------------------------------------------------- 机会扫描
def movers(engine, date_s: str, min_edge: float = MIN_EDGE) -> list[dict]:
    """「昨天开盘买入、今天开盘能卖出」收益 ≥ min_edge 的票，收益高的在前。"""
    prev = _prev_date(engine, date_s)
    if prev is None:
        return []
    out = []
    for code, buy, sell, name, industry in engine.repo.conn.execute(
            _MOVERS_SQL, (date_s, prev)):
        edge = (sell / buy - 1) * 100
        if edge < min_edge:
            continue
        out.append({"code": code, "name": name, "industry": industry or "",
                    "buy": round(buy, 2), "sell": round(sell, 2),
                    "edge": round(edge, 2)})
    out.sort(key=lambda r: r["edge"], reverse=True)
    return out


def _screen(account: dict, date_s: str) -> dict | None:
    for row in account.get("screens") or []:
        if row.get("date") == date_s:
            return row
    return None


def _skips(account: dict, date_s: str) -> dict[str, dict]:
    for row in account.get("execution") or []:
        if row.get("date") == date_s:
            return {s["code"]: {"bucket": s.get("bucket") or "其他未成交",
                                "reason": s["reason"]}
                    for s in row.get("skips") or []}
    return {}


def _factor_facts(engine, date_s: str) -> dict[str, dict]:
    """那一天每只票的关键事实（连板数、涨幅、成交额）。

    因子表只留最近几天，取不到就给空字典：宁可少写一句解释，
    也不要拿今天的截面去说一个月前那天的事。
    """
    return {row["code"]: row for row in engine.repo.factor_rows(date_s)}


def _pool_detail(fact: dict | None, specs: list[dict]) -> tuple[str, str]:
    """没进候选的票，到底是哪一条条件没过。返回 (归类, 带数字的说明)。

    归类必须短且稳定——它进的是分组键。把「当日 -0.45% 没涨停」这种带涨跌幅的
    句子当键，同一类原因会被拆成几十行，汇总表就废了（实测 227 只漏掉的机会
    被拆成 227 组）。数字只放在说明里。
    """
    if not fact:
        return "条件未通过", "当日策略条件未通过"
    cnt = fact.get("limit_cnt")
    chg = float(fact.get("change_pct") or 0)
    amount = float(fact.get("amount") or 0)          # 因子表里是亿元
    for spec in specs:
        band = limit_band(spec)
        if not (band[0] or band[1]):
            continue
        if not cnt:
            return "没涨停", f"当日 {chg:+.2f}%，没涨停，打板不进射程"
        if amount and amount < 0.5:
            return "成交额不足", f"成交额 {amount:.2f} 亿，不够 5000 万"
        if band[1] and float(cnt) > band[1]:
            return "连板超出区间", f"连板 {int(cnt)}，超出 {band_label(band)}"
        if band[0] and float(cnt) < band[0]:
            return "连板不足下限", f"连板 {int(cnt)}，低于 {band_label(band)}"
    return "动量未进前列", f"当日 {chg:+.2f}%，动量或量能没进前列"


def _attribute(code: str, accounts: dict[str, dict], buy_date: str,
               plan_date: str, facts: dict[str, dict],
               specs: list[dict]) -> dict:
    """这只票昨天为什么没买。返回 {kind, label, bucket, detail}。"""
    for sid, acc in accounts.items():
        name = acc.get("name") or sid
        for trade in acc.get("trades") or []:
            if (trade.get("code") == code and trade.get("side") == "BUY"
                    and trade.get("date") == buy_date):
                return {"kind": "bought", "label": f"{name}已买入",
                        "bucket": "已买入", "detail": f"成交价 {trade['price']:.2f}"}
        skipped = _skips(acc, buy_date).get(code)
        if skipped:
            return {"kind": "blocked", "label": "挂了没成",
                    "bucket": skipped["bucket"], "detail": skipped["reason"]}
        screen = _screen(acc, plan_date)
        if not screen:
            continue
        for plan in screen.get("plans") or []:
            if plan.get("code") != code:
                continue
            if plan.get("llm_reason"):
                return {"kind": "veto", "label": "没挂单", "bucket": "模型否决",
                        "detail": f"模型否决：{plan['llm_reason']}"}
            state = plan.get("order_state") or "计划卡上无委托"
            # 状态是「待明日开盘成交」却没有成交记录、也没有跳过日志，只可能是
            # 旧账户（这一版之前没有执行流水）。归到「挂了没成」，别归到「没挂单」——
            # 后者会让人以为系统那天没出手。
            if state.startswith("待明日开盘"):
                return {"kind": "blocked", "label": "挂了没成",
                        "bucket": "委托未成交",
                        "detail": "挂了委托，开盘没成交（该日没有执行流水）"}
            return {"kind": "noplan", "label": "没挂单",
                    "bucket": state, "detail": state}
        if screen.get("veto"):
            return {"kind": "senti", "label": "策略停手",
                    "bucket": "情绪闸门", "detail": screen["veto"]}
        for i, cand in enumerate(screen.get("candidates") or []):
            if cand.get("code") == code:
                return {"kind": "rank", "label": "没进候选", "bucket": "候选排名不够",
                        "detail": f"候选第 {i + 1} 名，只挂前 {screen.get('top_n')} 名"}
    bucket, detail = _pool_detail(facts.get(code), specs)
    return {"kind": "pool", "label": "不在池子", "bucket": bucket, "detail": detail}


# ---------------------------------------------------------------- 每日文档
def _trade_rows(accounts: dict[str, dict], date_s: str) -> list[dict]:
    rows: list[dict] = []
    for sid, acc in accounts.items():
        params = acc.get("params") or {}
        time_stop = int(params.get("time_stop") or 0)
        for trade in acc.get("trades") or []:
            if trade.get("date") != date_s:
                continue
            rows.append({**trade, "strategy": acc.get("name") or sid,
                         "time_stop": time_stop})
    rows.sort(key=lambda r: (r.get("side") != "SELL", r.get("code") or ""))
    return rows


def build_day(engine, date_s: str, accounts: dict[str, dict]) -> dict:
    """生成某一天的复盘 md。当天没有成交也要写——没有成交本身就是结论。"""
    prev = _prev_date(engine, date_s)
    plan_date = _prev_date(engine, prev) if prev else None
    trades = _trade_rows(accounts, date_s)
    sells = [t for t in trades if t["side"] == "SELL"]
    realized = round(sum(float(t.get("pnl") or 0) for t in sells), 2)
    wins = [t for t in sells if float(t.get("pnl") or 0) > 0]
    losses = [t for t in sells if float(t.get("pnl") or 0) < 0]

    nav = 0.0
    float_pnl = 0.0
    positions = 0
    for acc in accounts.values():
        cash = float(acc.get("cash") or 0)
        for pos in acc.get("positions") or []:
            positions += 1
            cash += float(pos.get("shares") or 0) * float(pos.get("last") or 0)
            float_pnl += float(pos.get("pnl") or 0)
        nav += cash

    edge_rows = movers(engine, date_s)
    caught = [m for m in edge_rows
              if any(t.get("code") == m["code"] for t in trades if t["side"] == "BUY")]
    missed = [m for m in edge_rows if m not in caught]
    # 归因要问「昨天那条策略为什么没选它」，所以取的是**做计划那天**的截面，
    # 不是今天的。今天的连板数说明不了昨天的决定。
    facts = _factor_facts(engine, plan_date) if plan_date else {}
    specs = [normalize_spec(acc.get("spec") or {"id": sid})
             for sid, acc in accounts.items()]
    reasons: dict[str, dict] = {}
    for row in missed:
        got = _attribute(row["code"], accounts, prev, plan_date, facts, specs)
        key = f"{got['kind']}|{got['bucket']}"
        bucket = reasons.setdefault(key, {
            "kind": got["kind"], "label": got["label"], "bucket": got["bucket"],
            "detail": got["detail"], "count": 0, "edges": [], "samples": []})
        bucket["count"] += 1
        bucket["edges"].append(row["edge"])
        if len(bucket["samples"]) < 3:
            bucket["samples"].append(f"{row['name']}（{row['code']}）{row['edge']:+.1f}%")
    for bucket in reasons.values():
        bucket["avg"] = round(sum(bucket["edges"]) / len(bucket["edges"]), 2)

    # 射程内 / 射程外必须分开。「不在池子」里绝大多数是策略定义之外的机会：
    # 打板只做涨停股，一只没涨停的票次日涨 8% 不是「漏掉」，是两套策略的事。
    # 混在一起算抓到率，结论会变成「应该去买没涨停的票」——那是另一种策略，
    # 不是这套规则该改的地方。这一条是这套经验库最容易被带歪的地方。
    reach = [b for b in reasons.values() if b["kind"] != "pool"]
    out_of_range = [b for b in reasons.values() if b["kind"] == "pool"]
    in_range = sum(b["count"] for b in reach)
    out_count = sum(b["count"] for b in out_of_range)
    rate = round(len(caught) / (len(caught) + in_range) * 100) if (caught or in_range) else None

    lines = [f"# {date_s} · 复盘", ""]
    lines.append(f"- 已实现 **{_fmt(realized, 0, True)} 元**"
                 f"（盈 {len(wins)} / 亏 {len(losses)}），浮动盈亏 **{_fmt(float_pnl, 0, True)} 元**")
    lines.append(f"- 组合净值 **{_fmt(nav, 0)} 元**，持仓 {positions} 只")
    lines.append(f"- 开盘买入·次日开盘可卖 ≥{MIN_EDGE:g}% 的机会 **{len(edge_rows)}** 只："
                 f"射程内 **{in_range}** 只、抓到 **{len(caught)}** 只"
                 f"（抓到率 {f'{rate}%' if rate is not None else '—'}），"
                 f"射程外 {out_count} 只")
    lines.append("")

    lines += ["## 一、成交与盈亏", ""]
    if trades:
        rows = []
        for t in trades:
            rows.append([
                t["strategy"], t["code"], t.get("name") or "", t["side"],
                f"{float(t['price']):.2f}", f"{int(t['shares'])}",
                _fmt(float(t.get("pnl") or 0), 0, True) if t["side"] == "SELL" else "—",
                f"{float(t.get('pnl_pct') or 0):+.2f}%" if t["side"] == "SELL" else "—",
                t.get("reason") or "",
            ])
        lines += [_table(["策略", "代码", "名称", "方向", "价格", "股数", "盈亏", "幅度", "原因"],
                         rows), ""]
    else:
        lines += ["今天没有成交。", ""]
    for t in sells:
        verdict = "赚" if float(t.get("pnl") or 0) >= 0 else "亏"
        lines.append(f"- {verdict}：{t.get('name')}（{t['code']}）"
                     f"{float(t.get('pnl_pct') or 0):+.2f}%，"
                     f"持 {t.get('held_days') if t.get('held_days') is not None else '—'} 天，"
                     f"卖出原因「{t.get('reason') or '—'}」")
    if sells:
        lines.append("")

    lines += ["## 二、漏掉的机会", ""]
    reach_rows = [m for m in missed if _attribute(m["code"], accounts, prev,
                                                  plan_date, facts, specs)["kind"] != "pool"]
    if edge_rows:
        if reach_rows:
            rows = []
            for row in reach_rows[:15]:
                got = _attribute(row["code"], accounts, prev, plan_date, facts, specs)
                rows.append([row["name"], row["code"], row["industry"] or "—",
                             f"{row['edge']:+.2f}%", got["label"], got["detail"]])
            lines += [f"射程内漏掉 **{len(reach_rows)}** 只，前 15 只：", ""]
            lines += [_table(["名称", "代码", "行业", "收益", "归类", "昨天怎么回事"], rows), ""]
            if len(reach_rows) > 15:
                lines += [f"其余 {len(reach_rows) - 15} 只见下方归因汇总。", ""]
        else:
            lines += ["射程内没有漏掉的机会。", ""]
        if out_count:
            kinds = "、".join(f"{b['bucket']} {b['count']} 只" for b in
                             sorted(out_of_range, key=lambda b: -b["count"])[:3])
            lines += [f"另有 **{out_count}** 只不在射程（{kinds}）。这些不是这套规则"
                      f"该抓的机会——它们要靠另一类买点才吃得到，记在这里是为了知道"
                      f"钱漏在哪，不是为了改这套规则去追。", ""]
    else:
        lines += ["今天没有满足条件的机会。", ""]

    lines += ["## 三、为什么没买", ""]
    if reasons:
        rows = []
        for bucket in sorted(reasons.values(), key=lambda b: (b["kind"] == "pool", -b["count"])):
            rows.append([bucket["label"], bucket["bucket"], str(bucket["count"]),
                         f"{bucket['avg']:+.2f}%", "、".join(bucket["samples"])])
        lines += [_table(["归类", "原因", "只数", "平均收益", "典型票"], rows), ""]
    else:
        lines += ["没有漏掉的机会，这一栏今天空着。", ""]

    lines += ["## 四、今天的教训", ""]
    lines += _lessons_of_day(reasons, in_range, len(caught), losses, out_count)
    lines.append("")

    markdown = "\n".join(lines).rstrip() + "\n"
    return {
        "date": date_s,
        "markdown": markdown,
        "stats": {"edge": len(edge_rows), "caught": len(caught),
                  "in_range": in_range, "out_of_range": out_count,
                  "reach_rate": rate, "realized": realized,
                  "wins": len(wins), "losses": len(losses),
                  "nav": round(nav, 2)},
    }


def _lessons_of_day(reasons: dict, in_range: int, caught: int,
                    losses: list[dict], out_count: int) -> list[str]:
    """当天的教训：只写有数字支撑的句子，没有就明说没有。

    这一段是给模型读的，不是给人读的摘要——「注意风险」这种话它每篇都会写，
    写进经验库之后会变成一条永远正确、永远没用的规矩。所以每条都必须带
    一个当天的实数，让模型下次能拿它和新情况对比。
    """
    out: list[str] = []
    if caught or in_range:
        rate = caught / (caught + in_range) * 100
        out.append(f"- 射程内抓到率 {rate:.0f}%（{caught}/{caught + in_range}）。"
                   f"{'射程内都抓不到，先查名额和闸门，不是选股。' if rate < 50 else '出手频率跟得上射程内的机会。'}")
    reach = [b for b in reasons.values() if b["kind"] != "pool"]
    if reach:
        top = sorted(reach, key=lambda b: -b["count"])[:3]
        parts = "、".join(
            f"「{b['label']}·{b['bucket']}」{b['count']} 只（平均 {b['avg']:+.2f}%，"
            f"如 {b['detail']}）" for b in top)
        out.append(f"- 射程内漏掉的分布：{parts}。"
                   f"这些是这套规则够得着、却没吃到的钱，改进从这里入手。")
    if out_count:
        out.append(f"- 射程外还有 {out_count} 只达到 {MIN_EDGE:g}%，"
                   f"那是另一套买点的事，不计入本策略的漏掉。")
    if losses:
        worst = min(losses, key=lambda t: float(t.get("pnl") or 0))
        out.append(f"- 今天最大一笔亏损是 {worst.get('name')}（{worst.get('code')}）"
                   f"{float(worst.get('pnl_pct') or 0):+.2f}%，"
                   f"原因是「{worst.get('reason') or '—'}」。")
    if not out:
        out.append("- 今天没有成交也没有漏掉的机会，不做结论。")
    return out


def write_day(engine, date_s: str, accounts: dict[str, dict]) -> dict:
    doc = build_day(engine, date_s, accounts)
    DAYS_DIR.mkdir(parents=True, exist_ok=True)
    path = DAYS_DIR / f"{date_s}.md"
    path.write_text(doc["markdown"], encoding="utf-8")
    doc["file"] = str(path)
    return doc


# ---------------------------------------------------------------- 读档
_HEAD = re.compile(r"^# (.+)$", re.M)


def list_days() -> list[dict]:
    """日期新的在前。只回索引，正文由 `read_day` 按需取。"""
    if not DAYS_DIR.exists():
        return []
    out = []
    for path in sorted(DAYS_DIR.glob("*.md"), reverse=True):
        text = path.read_text(encoding="utf-8", errors="ignore")
        head = _HEAD.search(text)
        facts = [ln[2:].strip() for ln in text.splitlines()
                 if ln.startswith("- ") and "**" in ln][:3]
        out.append({"date": path.stem, "title": head.group(1) if head else path.stem,
                    "facts": facts})
    return out


def read_day(date_s: str) -> str | None:
    path = DAYS_DIR / f"{date_s}.md"
    return path.read_text(encoding="utf-8") if path.exists() else None


def lessons_text() -> str:
    return LESSONS_FILE.read_text(encoding="utf-8") if LESSONS_FILE.exists() else ""


def _recent(limit: int = 3) -> list[str]:
    docs = []
    for row in list_days()[:limit]:
        text = read_day(row["date"]) or ""
        docs.append(f"### {row['date']}\n{text}")
    return docs


def context(max_chars: int = CONTEXT_CHARS) -> str:
    """喂给模型的经验：滚动经验在前，最近几天归因在后，整体截断。"""
    parts = []
    lessons = lessons_text()
    if lessons:
        parts.append(lessons.strip())
    parts.extend(_recent(3))
    text = "\n\n".join(parts).strip()
    return text[:max_chars]


# ---------------------------------------------------------------- 盈亏统计
def trade_stats(accounts: dict[str, dict]) -> dict:
    """全部平仓样本的胜率、盈亏比、按卖出原因与持有天数分组。

    经验库里写的每一句都得能被这张表验证。之前那种「注意追高风险」的总结，
    事后没法反驳，也就没法修正——它永远不会错，也就永远没用。
    """
    rows = [t for acc in accounts.values() for t in acc.get("trades") or []
            if t.get("side") == "SELL"]
    if not rows:
        return {"count": 0, "win": 0, "loss": 0, "win_rate": None,
                "avg_win": None, "avg_loss": None, "expectancy": None,
                "by_reason": [], "by_held": []}
    wins = [float(t.get("pnl_pct") or 0) for t in rows if float(t.get("pnl") or 0) > 0]
    losses = [float(t.get("pnl_pct") or 0) for t in rows if float(t.get("pnl") or 0) <= 0]
    by_reason: dict[str, list[float]] = {}
    by_held: dict[str, list[float]] = {}
    for t in rows:
        pct = float(t.get("pnl_pct") or 0)
        by_reason.setdefault(t.get("reason") or "—", []).append(pct)
        held = t.get("held_days")
        by_held.setdefault("—" if held is None else f"{int(held)} 日", []).append(pct)
    def _group(data: dict[str, list[float]]) -> list[dict]:
        return [{"key": k, "count": len(v), "avg": round(sum(v) / len(v), 2),
                 "win_rate": round(sum(1 for x in v if x > 0) / len(v) * 100, 0)}
                for k, v in sorted(data.items(), key=lambda kv: -len(kv[1]))]
    avg_win = round(sum(wins) / len(wins), 2) if wins else None
    avg_loss = round(sum(losses) / len(losses), 2) if losses else None
    return {
        "count": len(rows), "win": len(wins), "loss": len(losses),
        "win_rate": round(len(wins) / len(rows) * 100, 1),
        "avg_win": avg_win, "avg_loss": avg_loss,
        "expectancy": round((sum(wins) + sum(losses)) / len(rows), 2),
        "by_reason": _group(by_reason), "by_held": _group(by_held),
    }


LESSONS_SYSTEM = (
    "你是 A 股超短交易的复盘教练。给你一个策略账户的真实成交统计和最近几天的"
    "逐日复盘（行情、成交、漏掉的机会、为什么没买），写一份可执行的《经验》。"
    "要求："
    "1. 每条经验必须引用给出的数字（胜率、盈亏比、某类原因漏掉几只、平均收益），"
    "不能出现「注意风险」「保持纪律」这类无法反驳的空话；"
    "2. 最多 8 条，按对账户盈亏的影响从大到小排；"
    "3. 最后单独写「## 下一步改什么」，只允许 1~2 条，必须是能落到参数或规则上的"
    "动作（例如缩短持有上限、给某类原因加一道准入、把某类票放进候选池）；"
    "4. 用 markdown，二级标题分节，不要写客套话。"
)


def build_lessons(accounts: dict[str, dict]) -> str:
    """滚动《经验》：有模型就让模型写，没有就用统计口径写一份能用的。"""
    stats = trade_stats(accounts)
    brief = json.dumps(stats, ensure_ascii=False)
    days = "\n\n".join(_recent(5))
    if llm.enabled():
        reply = llm.chat(
            [{"role": "system", "content": LESSONS_SYSTEM},
             {"role": "user", "content":
              f"【成交统计】\n{brief}\n\n【最近复盘】\n{days[:CONTEXT_CHARS]}"}],
            temperature=0.3, timeout=60)
        # 配了模型但这次失败（限流、超时、key 过期）时**保留上一版**：
        # 规则版没有模型那份信息量，用它覆盖掉一份写得好的经验，
        # 等于一次网络抖动把库里的东西降级了，而用户看不出发生过什么。
        if reply:
            return reply.strip() + "\n"
        if LESSONS_FILE.exists():
            return LESSONS_FILE.read_text(encoding="utf-8")
    return _rule_lessons(stats, days)


def _rule_lessons(stats: dict, days: str) -> str:
    lines = ["# 经验（规则版）", ""]
    if not stats["count"]:
        lines += ["还没有平仓样本，攒够再总结。", ""]
    else:
        lines += [f"- 平仓 {stats['count']} 笔，胜率 {stats['win_rate']}%，"
                  f"平均盈 {stats['avg_win']}% / 平均亏 {stats['avg_loss']}%，"
                  f"期望 {stats['expectancy']}%/笔"]
        for row in stats["by_reason"][:4]:
            lines.append(f"- 卖出原因「{row['key']}」{row['count']} 笔，"
                         f"胜率 {row['win_rate']:.0f}%，平均 {row['avg']:+.2f}%")
        for row in stats["by_held"][:3]:
            lines.append(f"- 持有 {row['key']} 的 {row['count']} 笔，"
                         f"平均 {row['avg']:+.2f}%")
    lines += ["", "## 下一步改什么", "",
              "- 模型没接入，这一节只能靠人看逐日复盘补：从「为什么没买」里"
              "只数最多的那一类开始查。", ""]
    return "\n".join(lines)


def write_lessons(accounts: dict[str, dict]) -> str:
    text = build_lessons(accounts)
    JOURNAL_DIR.mkdir(parents=True, exist_ok=True)
    LESSONS_FILE.write_text(text, encoding="utf-8")
    return text


# ---------------------------------------------------------------- 模型复核
ADVISE_SYSTEM = (
    "你是 A 股超短交易的下单复核人。规则引擎已经按固定条件选出今天的候选，"
    "你不负责选股，只负责用下方《历史复盘经验》判断哪几只**今天不该买**。"
    "只能否决，不能新增，不能改仓位。"
    "只有经验里有明确对应的教训（同类票在类似情形下反复亏、或这类机会"
    "历史上根本抓不到）才否决；没把握就放行。"
    '只输出 JSON：{"verdicts":[{"code":"600000.SH","verdict":"skip|pass","reason":"≤40字，引用经验里的具体依据"}]}'
)


def _candidate_brief(items: list[dict]) -> str:
    """候选表：给模型的每一行都要能对上一条经验，没有指标的行它没法判断。"""
    rows = []
    for it in items:
        huddle = it.get("huddle") or {}
        rows.append(
            f"{it['code']} {it['name']}｜行业 {it.get('industry') or '—'}｜"
            f"今日 {float(it.get('change_pct') or 0):+.2f}%｜"
            f"连板 {int(it.get('limit_cnt') or 0)}｜"
            f"行业涨停 {int(huddle.get('ind_limit_cnt') or 0)} 家｜"
            f"得分 {float(it.get('score') or 0):.1f}")
    return "\n".join(rows)


# 同一天同一批候选只问一次模型。`POST /lab/paper/tick?force=true` 会重算计划卡，
# 不缓存的话点三次就付三次钱，而且三次的裁决还可能不一样——卡片上的理由
# 每次刷新都换一句，人就不知道该信哪条。
_ADVICE: dict[tuple, dict] = {}
_ADVICE_KEEP = 8


def advise(engine, strategy_id: str, spec: dict, items: list[dict],
           date_s: str) -> dict | None:
    """把候选交给模型复核，返回 {verdicts: {code: {...}}, source, at}。

    未接入模型、调用失败、返回不合法时返回 None——调用方照常挂单。
    兜底方向必须是「照常买」：一段网络故障不该让账户当天停摆，
    那会把「模型在把关」变成「模型在当刹车」。
    """
    if not items or not llm.enabled():
        return None
    key = (strategy_id, date_s, tuple(sorted(it["code"] for it in items)))
    if key in _ADVICE:
        return _ADVICE[key]
    ctx = context()
    if not ctx:
        return None
    senti = engine.repo.breadth()
    user = (f"【历史复盘经验】\n{ctx}\n\n"
            f"【策略】{spec.get('name') or strategy_id}：{spec.get('desc') or ''}\n"
            f"【今日市场】上涨 {senti.get('up')} 家、下跌 {senti.get('down')} 家、"
            f"涨停 {senti.get('limit_up')} 家、跌停 {senti.get('limit_down')} 家\n"
            f"【今日候选】\n{_candidate_brief(items)}\n\n"
            f"对每只给出 verdict。")
    got = llm.chat_json([{"role": "system", "content": ADVISE_SYSTEM},
                         {"role": "user", "content": user}],
                        temperature=0.2, timeout=45)
    if not got:
        return None
    verdicts = {}
    for row in got.get("verdicts") or []:
        code = row.get("code")
        if not code:
            continue
        verdicts[code] = {"verdict": "skip" if row.get("verdict") == "skip" else "pass",
                          "reason": str(row.get("reason") or "")[:80]}
    if not verdicts:
        return None
    result = {"date": date_s, "strategy": strategy_id, "source": "llm",
              "model": llm.status().get("model"), "verdicts": verdicts}
    if len(_ADVICE) >= _ADVICE_KEEP:
        _ADVICE.pop(next(iter(_ADVICE)))
    _ADVICE[key] = result
    return result


# ---------------------------------------------------------------- 账户快照
def screen_snapshot(selection: dict, plans: list[dict], top_n: int, *,
                    window: int = ACCOUNT_KEEP) -> dict:
    """把「今天选了谁、谁挂了单」压成一条流水，留给第二天归因。

    落盘而不是需要时重算：归因发生在 T+1 收盘，那时因子表已经换成新的截面，
    昨天的连板数、成交额都查不回来了。少了这一条，「为什么没买」只能靠猜。

    `top_n` 单独传：候选池按 `SCREEN_DEPTH` 放宽到 20 名，但真正挂单只挂
    `params["top_n"]` 张。归因文案里写「只挂前几名」必须是后者，否则
    「候选第 7 名，只挂前 20 名」会自相矛盾。
    """
    return {
        "date": selection.get("as_of"),
        "top_n": int(top_n),
        "funnel": selection.get("funnel") or [],
        "sentiment": selection.get("sentiment") or {},
        "veto": selection.get("sentiment_note") or "",
        "candidates": [
            {"code": it["code"], "name": it.get("name"),
             "industry": it.get("industry"), "score": it.get("score"),
             "limit_cnt": it.get("limit_cnt")}
            for it in (selection.get("items") or [])[:window * 4]
        ],
        "plans": [
            {"code": p.get("code"), "name": p.get("name"),
             "order_state": p.get("order_state"),
             "signal_type": p.get("signal_type"),
             "llm_reason": (p.get("llm") or {}).get("reason")}
            for p in plans
        ],
    }


def push_screen(account: dict, screen: dict) -> list[dict]:
    rows = [r for r in account.get("screens") or [] if r.get("date") != screen["date"]]
    rows.append(screen)
    return rows[-ACCOUNT_KEEP:]
