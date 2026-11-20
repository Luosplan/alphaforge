"""公司与板块概况：抽屉里「这家公司干啥的、属于哪个板块」。

三块信息，来源完全不同，所以拼装逻辑放在这里而不是塞进某个 router：

1. **上市板**（沪 / 深 / 创 / 科 / 北）——来自 `symbol.exchange` + `symbol.board`，
   纯本地，零成本。它是「这只票敢不敢用 10% 涨跌停假设」的前提，属性和
   主营业务不是一回事，所以前端也分成两个位置展示。
2. **概念板块**——`sector_member` 本地映射，上游只在同步时拉一次。
3. **F10 文本**（主营业务 / 经营范围 / 公司简介）——只能按票去东财问，
   一次一只。因此落库缓存，超过 `PROFILE_TTL_DAYS` 才回上游。

上游挂掉时不能让抽屉变成错误页：本地那两块照样返回，`stale` 与
`source` 字段告诉前端这份资料是新拉的还是库里的旧货。
"""

from __future__ import annotations

import datetime as dt

PROFILE_TTL_DAYS = 30
# 简介兜底截断：上游少数票的 ORG_PROFILE 能到上万字。抽屉里靠前端
# line-clamp + 展开控制，这里只防「一次响应传几百 KB」的极端值。
PROFILE_SECTIONS_MAX = 1200

# 板块 tag：短到能塞进一张 72px 宽的卡片标题行
BOARD_TAG: dict[str, str] = {
    "科创板": "科",
    "创业板": "创",
    "北交所": "北",
}


def board_tag(code: str, board: str, exchange: str = "") -> str:
    """上市板 → 单字 tag。主板要再分沪深，所以得看交易所。"""
    if board in BOARD_TAG:
        return BOARD_TAG[board]
    ex = (exchange or code.rsplit(".", 1)[-1]).upper()
    if ex == "BJ":
        return "北"
    return "沪" if ex == "SH" else "深"


def board_full(code: str, board: str, exchange: str = "") -> str:
    """完整说法，给抽屉里的标签用。"""
    tag = board_tag(code, board, exchange)
    return {"沪": "上交所主板", "深": "深交所主板", "创": "创业板",
            "科": "科创板", "北": "北交所"}.get(tag, board or "未知")


def company_profile(engine, code: str, *, refresh: bool = False) -> dict:
    """抽屉用的公司概况。永不抛异常——上游不可用时返回本地已有信息。"""
    repo = engine.repo
    sym = repo.symbol(code)
    if not sym:
        return {"code": code, "found": False, "board_tag": "", "board_name": "",
                "concepts": [], "main_business": "", "profile": "",
                "em_industry": "", "csrc": "", "scope": "", "found_date": "",
                "reg_capital": "", "website": "", "source": "none", "stale": False}

    tag = board_tag(code, sym.get("board") or "", sym.get("exchange") or "")
    out = {
        "code": code,
        "found": True,
        "name": sym.get("name") or code,
        "board_tag": tag,
        "board_name": board_full(code, sym.get("board") or "", sym.get("exchange") or ""),
        "industry": sym.get("industry") or "",
        "concepts": repo.concepts_of(code),
        "main_business": "",
        "profile": "",
        "em_industry": "",
        "csrc": "",
        "scope": "",
        "found_date": "",
        "reg_capital": "",
        "website": "",
        "source": "local",
        "stale": False,
        "updated": "",
    }

    cached = repo.profile_get(code)
    need = refresh or not cached
    if cached and not need:
        stamp = str(cached.get("updated") or "")[:10]
        try:
            age = (dt.date.today() - dt.date.fromisoformat(stamp)).days
        except ValueError:
            age = PROFILE_TTL_DAYS + 1
        need = age > PROFILE_TTL_DAYS

    if need:
        try:
            from alphaforge_data.sources.eastmoney import org_profile

            fresh = org_profile(code)
            if fresh.get("main_business") or fresh.get("profile"):
                fresh["source"] = "eastmoney"
                repo.profile_put(code, fresh, dt.datetime.now().isoformat(timespec="seconds"))
                cached = repo.profile_get(code)
            elif cached:
                out["stale"] = True
        except Exception:
            # 上游抖动 / 断网 / 报表改名：用缓存兜底，只标 stale
            out["stale"] = bool(cached)

    if cached:
        out.update({
            "main_business": cached.get("main_business") or "",
            "profile": _trim(cached.get("profile") or ""),
            "em_industry": cached.get("em_industry") or "",
            "csrc": cached.get("csrc") or "",
            "scope": cached.get("scope") or "",
            "found_date": cached.get("found_date") or "",
            "reg_capital": cached.get("reg_capital") or "",
            "website": cached.get("website") or "",
            "source": cached.get("source") or "eastmoney",
            "updated": cached.get("updated") or "",
        })
    return out


def _trim(text: str, limit: int = PROFILE_SECTIONS_MAX) -> str:
    """公司简介动辄两三千字，抽屉里给个开头就够了。按句号断，不留半句话。"""
    body = (text or "").strip()
    if len(body) <= limit:
        return body
    cut = body[:limit]
    for mark in ("。", "；", ";", "."):
        pos = cut.rfind(mark)
        if pos > limit * 0.5:
            return cut[: pos + 1]
    return cut + "…"
