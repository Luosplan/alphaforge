"""市场热点：板块强度、龙头、周期相位、炒作主线。

这个模块回答的是「今天在炒什么」，和选股/画像那种「哪只票好」是两回事：
前者看**横截面的共振**，后者看个股的质地。所以它不参与任何打分体系，
也不写库，纯粹是对当日横截面的一次解读。

四个问题，四种算法：

1. **板块** —— 涨幅、涨停家数、资金流、红盘占比、量能、持续性六项各做
   横截面百分位归一后加权。不做「涨幅第一就是最强」，因为单日暴涨的板块
   往往第二天就散伙，持续性单列出来才看得出是不是真行情。
2. **龙头** —— 直接读东财涨停板池，用连板数、封单资金、成交额、首封时间
   打分。这几项本地只能算出「谁涨停了」，算不出「几连板、封了多少钱」，
   而后者才是资金愿意给溢价的原因。
3. **周期** —— 用「位置 + 速度 + 加速度」判定四相位，不用「涨了多少天」
   这种拍脑袋阈值。20 日涨幅是位置，5 日涨幅是速度，两者之差是加速度。
4. **主线** —— 板块名匹配预先维护的题材词根，同一词根被多个板块命中就是
   **共振**。共振数比单一板块热度更能说明「市场在为什么事情买单」。

行业与概念走两条数据路径（见 `Repo.industry_rank` / `Repo.concept_rank`），
但输出同一种形状，前端不必分叉。概念板块刚建库时没有历史，多窗口涨幅
会是 `None`，这里照实传下去，不补 0。
"""

from __future__ import annotations

import statistics
import time

WINDOWS: tuple[int, ...] = (1, 3, 5, 10, 20)

# 涨停池盘中会变（炸板、新封），但变化不频繁。5 分钟够用，也避免
# 用户反复刷页面时把上游打爆。
_POOL_TTL = 300.0
_POOL_CACHE: dict[str, tuple[float, dict]] = {}

# 题材词根表。这不是分类学，是**散户语境**：市场说「今天炒算力」而不是
# 「今天炒通信设备Ⅱ」。所以词根按「炒作故事」组织，一个词根下挂多个
# 同义/近义关键词，命中任何一个就算共振。新增题材时往这里加就行。
THEMES: dict[str, tuple[str, ...]] = {
    "算力": ("算力", "智算", "数据中心", "AIDC", "CPO", "光模块", "液冷",
             "服务器", "铜连接", "PCB", "交换机"),
    "AI应用": ("AI", "人工智能", "大模型", "智能体", "AIGC", "多模态",
               "传媒", "游戏", "短剧"),
    "机器人": ("机器人", "人形", "减速器", "灵巧手", "丝杠", "伺服", "电机"),
    "半导体": ("半导体", "芯片", "存储", "光刻", "先进封装", "晶圆", "EDA",
               "第三代半导体", "碳化硅"),
    "新能源": ("锂电", "固态电池", "储能", "光伏", "风电", "钠电", "氢能",
               "逆变器", "电池"),
    "军工": ("军工", "航天", "卫星", "低空", "无人机", "导弹", "雷达"),
    "医药": ("创新药", "减肥药", "CRO", "医疗器械", "中药", "疫苗", "脑机"),
    "消费": ("白酒", "食品", "饮料", "零售", "免税", "旅游", "纺织", "调味",
             "餐饮", "酒店"),
    "家电": ("家电", "白色家电", "黑色家电", "小家电"),
    "地产链": ("地产", "建材", "家居", "装修", "物业", "水泥", "钢铁"),
    "金融": ("券商", "银行", "保险", "多元金融", "参股"),
    "资源": ("稀土", "黄金", "铜", "铝", "锂矿", "钴", "镍", "煤炭",
             "石油", "天然气", "磷", "氟"),
    "国改": ("国企改革", "并购重组", "中特估", "破净", "市值管理"),
    "汽车": ("汽车", "智能驾驶", "车路云", "一体化压铸", "零部件"),
    "数据": ("数据要素", "信创", "网络安全", "数字经济", "国产软件"),
    "电力": ("电力", "核电", "水电", "电网", "虚拟电厂", "可控核聚变"),
}

PHASES = ("启动", "加速", "高潮", "退潮", "震荡")


# ---------------------------------------------------------------- 小工具
def _pct_rank(values: list[float | None]) -> list[float]:
    """横截面百分位 0–100。`None` 按中位（50）处理，不往下拉。

    不用 min-max 归一：一个极端值会把其余全压到 0 附近，榜单一夜之间
    变成「第一名和其他」。百分位对分布形状不敏感，稳定得多。
    """
    idx = [i for i, v in enumerate(values) if v is not None]
    out = [50.0] * len(values)
    if len(idx) < 2:
        return out
    ordered = sorted(idx, key=lambda i: values[i])          # type: ignore[arg-type,return-value]
    n = len(ordered)
    for pos, i in enumerate(ordered):
        out[i] = pos / (n - 1) * 100
    return out


def _safe(v: object, default: float = 0.0) -> float:
    try:
        return float(v)                                     # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


# 东财的行业名带「Ⅱ」「Ⅲ」后缀（`装修装饰Ⅱ`），涨停池的 `hybk` 不带
# （`装修装饰`）。不归一化的话两边的名字永远对不上，龙头挂不到板块上。
_ROMAN = ("Ⅱ", "Ⅲ", "Ⅰ", "II", "III", "I")


def _bare(name: str) -> str:
    out = (name or "").strip()
    for suf in _ROMAN:
        if out.endswith(suf):
            out = out[: -len(suf)]
            break
    return out.strip()


def _persist(pcts: list[float]) -> dict:
    """连续上涨天数 + 近期阳线占比。"""
    if not pcts:
        return {"streak": 0, "win_rate": 0.0}
    streak = 0
    for v in reversed(pcts):
        if v > 0:
            streak += 1
        else:
            break
    recent = pcts[-5:]
    return {
        "streak": streak,
        "win_rate": round(sum(1 for v in recent if v > 0) / len(recent) * 100, 1),
    }


def _phase(item: dict) -> str:
    """四相位判定：位置 + 速度 + 加速度 + 量能。

    全部用**相对强度**（跑赢同类的幅度）而不是绝对涨跌。这一条很关键：
    2026-11-18 全市场 4132 家下跌，题材板块清一色 -2% ~ -5%，用绝对跌幅
    判的话 143 个题材有 130 多个都是「退潮」，页面变成一片绿——可其中跌
    2% 的那个其实是当天最抗跌的方向。相对强度才分得出谁在退、谁在扛。

    「涨了几天」不是好判据——横盘五天和连涨五天完全不是一回事。用
    「近段相对强度相对长期相对强度的偏离」看加速度，能区分「刚开始」
    和「涨了很久还在涨」。
    """
    p1 = item.get("pct_1d")
    speed = item.get("rel_pct")
    if speed is None:
        speed = _safe(item.get("pct_5d"))
    pos = item.get("rel_20d")
    if pos is None:
        pos = _safe(item.get("pct_20d"))
    acc = speed - pos / 4 if pos else speed
    vol = _safe(item.get("volratio"), 1.0) or 1.0
    streak = item.get("streak") or 0

    if speed < -2.5 or (p1 is not None and p1 < -3 and speed < 0):
        return "退潮"
    if pos >= 8 and speed > 2.5 and vol > 1.15:
        return "高潮"
    if acc > 1.0 and streak >= 3:
        return "加速"
    if pos < 6 and speed > 0.5:
        return "启动"
    return "震荡"


# ---------------------------------------------------------------- 板块
def _score_sectors(items: list[dict], histories: dict[str, list[float]],
                   window: int) -> list[dict]:
    """给一组同类型板块打热度分。`items` 会被就地补充字段。"""
    key = f"pct_{window}d"
    for it in items:
        pcts = histories.get(it["name"]) or histories.get(it.get("code", "")) or []
        it.update(_persist(pcts))
        it["window_pct"] = it.get(key)
        # 概念刚建库时没有 20 日历史，用可得的更长窗口兜底，保证相位不瞎判
        if it.get("pct_20d") is None:
            for n in (10, 5, 3):
                if it.get(f"pct_{n}d") is not None:
                    it["pct_20d"] = it[f"pct_{n}d"]
                    break

    if not items:
        return items

    # 相对强度：板块涨幅减去同类板块的中位数。
    #
    # 这一层不是锦上添花。2026-11-18 那天上涨 1320 家、下跌 4132 家，权重
    # 护盘、小票普跌，题材板块**全部**是负的——第一名「猪肉」也只有 -0.6%。
    # 只看绝对涨幅，用户看到的是「今天没有热点」，但实际上 -0.6% 对 -3% 的
    # 中位数来说是 2.4 个点的逆势超额，那才是热点。所以排序用相对强度，
    # 展示仍然给绝对涨幅（用户对「涨了几个点」有直觉，对超额没有）。
    vals = [it.get("window_pct") for it in items if it.get("window_pct") is not None]
    med = statistics.median(vals) if vals else 0.0
    p20s = [it.get("pct_20d") for it in items if it.get("pct_20d") is not None]
    med20 = statistics.median(p20s) if p20s else 0.0
    # 必须先算完相对强度再判相位——`_phase` 读的就是 `rel_pct` / `rel_20d`，
    # 顺序反了它会拿到 None 退回绝对涨幅，于是普跌日全体判成退潮。
    for it in items:
        wp = it.get("window_pct")
        it["rel_pct"] = round(wp - med, 2) if wp is not None else None
        p20 = it.get("pct_20d")
        it["rel_20d"] = round(p20 - med20, 2) if p20 is not None else None
        it["phase"] = _phase(it)

    # 涨幅必须占主导。第一版给 0.30，结果一个 5 日跌 3.8% 的板块因为涨停
    # 家数多、资金流入大而排到第二——用户问的是「谁最强」，不是「谁最热闹」。
    # 涨停/资金这些是**佐证**，不能反客为主。
    parts = [
        (_pct_rank([it.get("rel_pct") for it in items]), 0.45),
        (_pct_rank([it.get("limit_up") for it in items]), 0.20),
        (_pct_rank([it.get("main_net") for it in items]), 0.15),
        (_pct_rank([it.get("adv_ratio") for it in items]), 0.10),
        (_pct_rank([it.get("volratio") for it in items]), 0.05),
        (_pct_rank([it.get("win_rate") for it in items]), 0.05),
    ]
    for i, it in enumerate(items):
        heat = sum(rank[i] * w for rank, w in parts)
        # 退潮的板块即使数据好看也不该排前面：它代表「行情已经走完」，
        # 追进去是接最后一棒。这里压 12 分，让它掉出前排但不至于消失。
        if it["phase"] == "退潮":
            heat -= 12
        it["heat"] = round(heat, 1)
    return items


def _sectors(engine, window: int) -> dict:
    repo = engine.repo
    ind = repo.industry_rank(WINDOWS)
    con = repo.concept_rank(WINDOWS)

    hist_ind = repo.industry_daily_pct(25)
    # 概念板块的序列按 code 索引（`gn_xxx`），行业按名字索引，所以下面
    # 取历史时先试 name 再试 code
    hist_con = repo.concept_daily_pct(25)

    industry = _score_sectors(ind["items"], hist_ind, window)
    concept = _score_sectors(con["items"], hist_con, window)

    # 两类各自归一化后再合并，避免「行业有涨停家数、概念没有」导致概念整体偏低
    for it in industry:
        it["kind"] = "industry"
    for it in concept:
        it["kind"] = "concept"
    merged = sorted(industry + concept, key=lambda x: x["heat"], reverse=True)
    return {
        "industry": sorted(industry, key=lambda x: x["heat"], reverse=True),
        "concept": sorted(concept, key=lambda x: x["heat"], reverse=True),
        "merged": merged,
        "concept_days": con.get("days", 0),
    }


# ---------------------------------------------------------------- 龙头
def _pool(engine, date: str) -> dict:
    """涨停/跌停池，带缓存与降级。

    缓存 TTL 分两档：**成功 5 分钟，失败 60 秒**。第一版不分档，失败结果
    也被记住 5 分钟，结果是上游一抖动，页面就要空五分钟才恢复——用户只会
    以为功能坏了。
    """
    now = time.time()
    cached = _POOL_CACHE.get(date)
    if cached:
        stamp, payload, ttl = cached
        if now - stamp < ttl:
            return payload

    from alphaforge_data.sources import eastmoney as em

    try:
        up = em.limit_up_pool(date)
    except Exception:                                        # noqa: BLE001
        up = []
    try:
        down = em.limit_down_pool(date)
    except Exception:                                        # noqa: BLE001
        down = []
    payload = {"date": date, "up": up, "down": down, "available": bool(up)}
    _POOL_CACHE[date] = (now, payload, _POOL_TTL if up else 60.0)
    return payload


def _leader_score(row: dict) -> float:
    """龙头分。连板数是硬通货，封单资金次之，首封时间早说明抢筹坚决。"""
    boards = row.get("boards") or 1
    seal = _safe(row.get("seal_fund"))
    amount = _safe(row.get("amount"))
    # 首封越早越好：09:25:00（集合竞价一字）到 14:57 之间线性衰减
    t = str(row.get("first_seal") or "15:00:00")
    try:
        hh, mm = int(t[0:2]), int(t[3:5])
        minutes = hh * 60 + mm
    except (ValueError, IndexError):
        minutes = 900
    speed = max(0.0, min(1.0, (900 - minutes) / 540))
    return (boards * 40
            + min(seal / 1e8, 10) * 2.5
            + min(amount / 1e8, 30) * 0.7
            + speed * 15
            - (row.get("broken") or 0) * 8)


def _local_boards(conn, code: str, date: str) -> int:
    """从日线回溯连板数：连续多少天涨幅达到该板块的涨停幅度。

    阈值用 `pc()` 而不是写死 10%——创业板和科创板是 20%，北交所 30%，
    ST 是 5%。写死的话，一只 10% 的创业板股票会被误判成涨停。
    """
    from alphaforge_core.universe import pc

    row = conn.execute("SELECT is_st FROM symbol WHERE code=?", (code,)).fetchone()
    limit = pc(code, bool(row[0]) if row else False) * 100
    rows = conn.execute(
        "SELECT d.close, p.close FROM daily d "
        "JOIN daily p ON p.code = d.code AND p.date = ("
        "  SELECT MAX(date) FROM daily WHERE code = d.code AND date < d.date) "
        "WHERE d.code = ? AND d.date <= ? AND p.close > 0 "
        "ORDER BY d.date DESC LIMIT 12", (code, date)).fetchall()
    boards = 0
    for close, prev in rows:
        if (close - prev) / prev * 100 >= limit - 0.5:
            boards += 1
        else:
            break
    return boards


def _leaders_local(engine, date: str, limit: int = 8) -> dict:
    """涨停池取不到时的本地兜底。

    `factor.limit_up` 是同步时按各板块规则算好的，可以直接信；连板数用
    日线回溯。**封单资金和炸板次数本地算不出来，就不给**——宁可少两列，
    也不要用别的数字顶上去冒充。
    """
    repo = engine.repo
    rows = repo.conn.execute(
        "SELECT f.code, s.name, f.change_pct, f.amount, f.industry, f.float_mv "
        "FROM factor f JOIN symbol s ON s.code = f.code "
        "WHERE f.trade_date = ? AND f.limit_up = 1", (date,)).fetchall()
    out: list[dict] = []
    for code, name, pct, amount, industry, fmv in rows:
        out.append({
            "code": code, "name": name, "price": 0.0,
            "change_pct": round(float(pct or 0), 2),
            "amount": float(amount or 0) * 1e8,     # factor.amount 是亿元
            "float_mv": round(float(fmv or 0), 2),
            "turnover": 0.0,
            "boards": _local_boards(repo.conn, code, date),
            "first_seal": "", "last_seal": "",
            "seal_fund": 0.0, "broken": 0,
            "industry": industry or "",
        })
    out.sort(key=lambda r: (r["boards"], r["amount"]), reverse=True)
    return {
        "date": date, "total": len(out), "top": out[:limit],
        "by_sector": {}, "broken_rate": 0.0,
        "max_boards": max((r["boards"] for r in out), default=0),
        "available": bool(out), "down_total": 0, "local": True,
    }


def _leaders(engine, sectors: list[dict], date: str, limit: int = 8) -> dict:
    pool = _pool(engine, date)
    rows = pool["up"]
    if not rows:
        # 上游取不到就退回本地算法：涨停名单和连板数本地都有，只是缺
        # 封单资金和炸板次数。这比整块留空有用得多——用户要的是「谁在
        # 领涨」，那部分不依赖上游。
        local = _leaders_local(engine, date, limit)
        if local["top"]:
            return local
        # 形状必须和正常分支一致。少几个 key 不会立刻出错，但前端拿到的
        # `undefined` 会显示成 `—`，看起来像「今天没有最高板」而不是
        # 「取数失败」——两件事在页面上必须能分开。
        return {"date": date, "total": 0, "top": [], "by_sector": {},
                "broken_rate": 0.0, "max_boards": 0, "available": False,
                "down_total": len(pool["down"])}

    scored = sorted(rows, key=_leader_score, reverse=True)
    broken = sum(1 for r in rows if r.get("broken"))
    # 用归一化后的名字建索引，两边才能对上（见 `_bare`）
    bare = {_bare(s["name"]): s["name"] for s in sectors}
    for r in scored:
        r["score"] = round(_leader_score(r), 1)

    # 板块 → 该板块内封板最硬的那只。用涨停池自带的行业名对齐，
    # 概念板块没有成分股映射，不做强行归属（宁可少挂，不要挂错）。
    by_sector: dict[str, list[dict]] = {}
    for r in scored:
        key = bare.get(_bare(r.get("industry") or ""))
        if key:
            by_sector.setdefault(key, []).append(r)

    return {
        "date": date,
        "total": len(rows),
        "top": scored[:limit],
        "by_sector": {k: v[:3] for k, v in by_sector.items()},
        "broken_rate": round(broken / len(rows) * 100, 1),
        "max_boards": max((r.get("boards") or 1) for r in rows),
        "available": True,
        "down_total": len(pool["down"]),
    }


# ---------------------------------------------------------------- 主线
def _themes(sectors: list[dict], limit: int = 8) -> list[dict]:
    """把板块归到题材词根上，按共振板块数与热度合计排序。

    一个板块可能命中多个词根（「光伏储能」同时沾「新能源」和「储能」），
    取**最长关键词**命中的那个，避免短词总是抢走归属。
    """
    buckets: dict[str, dict] = {}
    for it in sectors:
        name = it.get("name") or ""
        best_theme, best_len = None, 0
        for theme, words in THEMES.items():
            for w in words:
                if w and w in name and len(w) > best_len:
                    best_theme, best_len = theme, len(w)
        if not best_theme:
            continue
        b = buckets.setdefault(best_theme, {
            "theme": best_theme, "heat": 0.0, "count": 0,
            "sectors": [], "leaders": [], "best_phase": "震荡",
            "pct": 0.0,
        })
        b["heat"] += it.get("heat") or 0
        b["count"] += 1
        b["pct"] += it.get("window_pct") or 0
        b["sectors"].append({
            "name": name, "kind": it.get("kind"), "heat": it.get("heat"),
            "pct": it.get("window_pct"), "phase": it.get("phase"),
            "lead": it.get("lead"),
        })
        if it.get("lead"):
            b["leaders"].append(it["lead"])

    out = list(buckets.values())
    for b in out:
        b["heat"] = round(b["heat"], 1)
        b["pct"] = round(b["pct"] / b["count"], 2) if b["count"] else 0.0
        b["sectors"].sort(key=lambda x: x["heat"] or 0, reverse=True)
        b["sectors"] = b["sectors"][:5]
        b["leaders"] = b["leaders"][:4]
        phases = [s["phase"] for s in b["sectors"]]
        # 取该题材下最有进攻性的相位（启动/加速优先于震荡）
        for p in ("高潮", "加速", "启动", "震荡", "退潮"):
            if p in phases:
                b["best_phase"] = p
                break
        # 排序不能用「累加热度」：板块多的题材天然占便宜，结果一个整体在跌
        # 的宽泛题材（13 个板块加起来）会压过真正在涨的窄题材。改成
        # 「平均热度 × 共振加成」，共振是加分项不是决定项。
        avg = b["heat"] / b["count"] if b["count"] else 0.0
        b["avg_heat"] = round(avg, 1)
        resonance = 1 + 0.10 * max(0, b["count"] - 1)
        # 涨幅直接参与乘法而不是当布尔开关判。第一版写 `if pct<0: ×0.7`，
        # 结果一个 11 个板块、均跌 4.4% 的宽泛题材靠共振加成照样排到前面——
        # 「跌得多」和「跌得少」被当成了同一件事。现在按跌幅连续打折，
        # 分母取 8 而不是 10：共振带来的加成（每多一个板块 10%）应该小于
        # 跌幅的惩罚，否则「板块多」这一件事就能盖过「在不在涨」。
        trend = max(0.2, 1 + b["pct"] / 8)
        b["score"] = round(avg * resonance * trend, 1)
    out.sort(key=lambda x: x["score"], reverse=True)
    return out[:limit]


# ---------------------------------------------------------------- 对外
def build(engine, window: int = 5, limit: int = 12) -> dict:
    """热点总览。`window` 取 `WINDOWS` 里的值。"""
    if window not in WINDOWS:
        window = 5
    repo = engine.repo
    date = repo.trade_date()
    if not date:
        return {"trade_date": None, "window": window, "sectors": [],
                "industry": [], "concept": [], "themes": [], "leaders": {},
                "advice": "还没有同步数据"}

    pack = _sectors(engine, window)
    leaders = _leaders(engine, pack["merged"], date, limit=8)
    themes = _themes(pack["merged"])

    # 给前排板块挂上它的龙头股
    for it in pack["merged"][:limit * 2]:
        hit = leaders["by_sector"].get(it["name"])
        if hit:
            it["leaders"] = [{"name": r["name"], "code": r["code"],
                              "boards": r["boards"], "seal_fund": r["seal_fund"]}
                             for r in hit]
    breadth = repo.breadth(date)

    return {
        "trade_date": date,
        "window": window,
        "sectors": pack["merged"][:limit],
        "industry": pack["industry"][:limit],
        "concept": pack["concept"][:limit],
        "concept_days": pack["concept_days"],
        "themes": themes,
        "leaders": leaders,
        "breadth": {
            "up": breadth.get("up"), "down": breadth.get("down"),
            "limit_up": breadth.get("limit_up"),
            "limit_down": breadth.get("limit_down"),
            "amount": breadth.get("amount"),
        },
    }
