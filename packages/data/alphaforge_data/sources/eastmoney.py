"""东方财富 `datacenter` 数据源：财报字段 / 分红 / 指数成分 / 全球指数。

主力通道是 `datacenter-web.eastmoney.com`——这条通道在本机稳定。
`push2` / `push2his` 在连续请求下会整机拉黑（实测 8 连全断），所以 A 股
快照与资金流改由新浪承担。

**唯一例外是 `global_indices()`**：全球 16 个指数只有 `push2` 的
`ulist.np` 能一次问完（腾讯 `qt.gtimg.cn` 只认美股与港股，日经/德法英
一律返回空串）。它是「一个请求换 16 个市场」，加上上游 TTL 缓存后
实际频率是分钟级，与遍历 5578 只票的同步流量不是一个量级，因此不受
上面那条限制。真要高频轮询时请把 TTL 调大，而不是压缩这个请求。

行业字段藏在业绩报表的 `BOARD_NAME` 里（东财二级行业，如「白酒Ⅱ」「光伏设备」），
顺手一起取回，省一次全市场请求。
"""

from __future__ import annotations

import datetime as dt
from urllib.parse import quote

from .base import FetchError, fetch_json

DATACENTER = "https://datacenter-web.eastmoney.com/api/data/v1/get"

# 东财指数成分表的 TYPE 枚举。1/2/3/4 已按成分内容核对过：
# 1 = 沪深300（主板+创业板混合 300 只）、2 = 上证50、3 = 中证500、
# 4 = 科创50（50 只全是 688）。深证成指与创业板指不在这张表里，
# 与其拿「全板块」冒充成分股，不如让它们只显示指数自身的真实点位。
INDEX_TYPES: dict[str, int] = {
    "000300.SH": 1,
    "000016.SH": 2,
    "000905.SH": 3,
    "000688.SH": 4,
}


def _q(value: object) -> str:
    """datacenter 的 filter 值要带单引号，整体再做一次 URL 编码。"""
    return quote(str(value), safe="")


def _dc(report: str, params: dict, *, page_size: int = 500,
        max_pages: int | None = None) -> list[dict]:
    """通用翻页。实测单页上限 500，给 1000 也只返回 500。

    `max_pages` 不是可选项而是安全阀：`page_size=1` 配一个五万页的报表时，
    按 `len(batch) < page_size` 永远不会提前退出，会老老实实翻五万次。
    """
    rows: list[dict] = []
    page = 1
    while True:
        merged = {
            "reportName": report,
            "columns": "ALL",
            "pageNumber": page,
            "pageSize": page_size,
            "source": "WEB",
            "client": "WEB",
            **params,
        }
        qs = "&".join(f"{k}={v}" for k, v in merged.items())
        data = fetch_json(f"{DATACENTER}?{qs}", interval=0.14)
        result = data.get("result") or {}
        batch = result.get("data") or []
        if not batch:
            break
        rows.extend(batch)
        pages = int(result.get("pages") or 0)
        if max_pages is not None and page >= max_pages:
            break
        if page >= pages or len(batch) < page_size:
            break
        page += 1
    return rows


def latest_report_date() -> str | None:
    rows = _dc("RPT_LICO_FN_CPD", {
        "filter": _q('(SECURITY_TYPE_CODE="058001001")'),
        "columns": "SECURITY_CODE,REPORTDATE",
        "sortColumns": "REPORTDATE",
        "sortTypes": "-1",
    }, page_size=1, max_pages=1)
    raw = str(rows[0].get("REPORTDATE") or "") if rows else ""
    return raw[:10] or None


def fundamentals(report_date: str | None = None) -> dict[str, dict]:
    """全市场 ROE（加权）+ 东财二级行业。返回 code6 → {roe, roe_date, industry}。

    两次取数，口径各自取最合适的那一版：

    - **ROE 只取年报**。`WEIGHTAVG_ROE` 是年初至今的累计值，三季报的 ROE
      不是年化 ROE。拿它当「ROE 大于 15%」去筛，等于一季度就要求「三个月
      赚 15%」，与用户说的「年 ROE」根本不是一回事。实测三季报口径下
      全市场只有零星几只银行过 15%，而它们的年报 ROE 都在 15% 上下。
    - **行业取最新报告期**。行业分类跟报告期无关，用最新的那份覆盖面最大；
      只取年报的话，当年新上市的票会没有行业。
    """
    annual = report_date or latest_annual_date()
    latest = latest_report_date()
    if not annual and not latest:
        return {}

    out: dict[str, dict] = {}

    if latest:
        for r in _dc("RPT_LICO_FN_CPD", {
            "filter": _q(f"(REPORTDATE='{latest}')"),
            "columns": "SECURITY_CODE,BOARD_NAME",
            "sortColumns": "SECURITY_CODE",
            "sortTypes": "1",
        }):
            code = str(r.get("SECURITY_CODE") or "")
            if len(code) != 6:
                continue
            industry = str(r.get("BOARD_NAME") or "").strip()
            out[code] = {
                "roe": None,
                "roe_date": None,
                "industry": industry if industry and industry != "-" else None,
            }

    if annual:
        for r in _dc("RPT_LICO_FN_CPD", {
            "filter": _q(f"(REPORTDATE='{annual}')"),
            "columns": "SECURITY_CODE,WEIGHTAVG_ROE,BOARD_NAME",
            "sortColumns": "SECURITY_CODE",
            "sortTypes": "1",
        }):
            code = str(r.get("SECURITY_CODE") or "")
            if len(code) != 6:
                continue
            roe = r.get("WEIGHTAVG_ROE")
            try:
                roe_val = float(roe) if roe is not None else None
            except (TypeError, ValueError):
                roe_val = None
            item = out.setdefault(code, {"roe": None, "roe_date": None, "industry": None})
            item["roe"] = roe_val
            item["roe_date"] = annual
            if not item["industry"]:
                industry = str(r.get("BOARD_NAME") or "").strip()
                item["industry"] = industry if industry and industry != "-" else None

    return out


def latest_annual_date() -> str | None:
    """最近一个已披露完的年报报告期（`YYYY-12-31`）。

    用日历规则往前推，再探一次确认有数据：A 股年报 4 月 30 日前披露完，
    所以 4 月 30 日之后用「去年」，之前用「前年」。
    """
    today = dt.date.today()
    start = 1 if (today.month, today.day) >= (5, 1) else 2
    for back in range(start, start + 3):
        cand = f"{today.year - back}-12-31"
        rows = _dc("RPT_LICO_FN_CPD", {
            "filter": _q(f"(REPORTDATE='{cand}')"),
            "columns": "SECURITY_CODE",
            "sortColumns": "SECURITY_CODE",
            "sortTypes": "1",
        }, page_size=1, max_pages=1)
        if rows:
            return cand
    return None


def bonus_events(since: str) -> dict[str, list[dict]]:
    """分红送配事件。**这是复权因子的唯一依据**。

    每行代表一次除权除息：
    - `PRETAX_BONUS_RMB` 每 10 股派息（元）→ 每股派息 = 值 / 10
    - `IT_RATIO` 每 10 股送转合计（股）→ 股本扩张比例 = 值 / 10
    - `EX_DIVIDEND_DATE` 除权除息日

    同一只票在同一天可能有多行（送股一行、派息一行），按日期合并。
    """
    rows = _dc("RPT_SHAREBONUS_DET", {
        "filter": _q(f"(EX_DIVIDEND_DATE>='{since}')"),
        "columns": "SECURITY_CODE,EX_DIVIDEND_DATE,PRETAX_BONUS_RMB,IT_RATIO,BONUS_IT_RATIO",
        "sortColumns": "EX_DIVIDEND_DATE",
        "sortTypes": "1",
    }, page_size=500)
    merged: dict[tuple[str, str], dict] = {}
    for r in rows:
        code = str(r.get("SECURITY_CODE") or "")
        raw_date = str(r.get("EX_DIVIDEND_DATE") or "")[:10]
        if len(code) != 6 or len(raw_date) != 10:
            continue
        key = (code, raw_date)
        item = merged.setdefault(key, {"date": raw_date, "dps": 0.0, "ratio": 0.0})
        bonus = r.get("PRETAX_BONUS_RMB")
        ratio = r.get("IT_RATIO")
        if ratio is None:
            ratio = r.get("BONUS_IT_RATIO")
        try:
            if bonus is not None:
                item["dps"] += float(bonus) / 10.0
            if ratio is not None:
                item["ratio"] += float(ratio) / 10.0
        except (TypeError, ValueError):
            continue

    out: dict[str, list[dict]] = {}
    for (code, _), item in sorted(merged.items(), key=lambda kv: kv[1]["date"]):
        out.setdefault(code, []).append(item)
    return out


def dividends(since: str) -> dict[str, float]:
    """近 12 个月已实施的每股现金分红合计（元/股），用于算股息率。"""
    out: dict[str, float] = {}
    for code, events in bonus_events(since).items():
        total = sum(e["dps"] for e in events)
        if total > 0:
            out[code] = total
    return out


def index_members(index_code: str) -> list[str]:
    """指数成分股 code6 列表。没有官方成分表的指数返回空，调用方不要硬凑。"""
    type_id = INDEX_TYPES.get(index_code)
    if type_id is None:
        return []
    rows = _dc("RPT_INDEX_TS_COMPONENT", {
        "filter": _q(f'(TYPE="{type_id}")'),
        "columns": "SECURITY_CODE",
    }, page_size=500)
    return [str(r["SECURITY_CODE"]) for r in rows if r.get("SECURITY_CODE")]


# ---------------------------------------------------------------- 公司概况
#
# F10 的 `RPT_F10_BASIC_ORGINFO` 走的是同一个 datacenter 通道，但必须带
# `source=F10&client=PC`——换成 `WEB` 会返回空 `result`，而不是报错。
#
# 一次只认一只票（`filter=SECUCODE`），没有批量口径。所以这个方法天生是
# 「按需触发 + 落库缓存」的用法，不要拿去遍历全市场。
_EMPTY_PROFILE = {
    "name": "", "em_industry": "", "csrc": "", "main_business": "",
    "profile": "", "scope": "", "found_date": "", "reg_capital": "", "website": "",
}


def org_profile(code: str) -> dict:
    """公司概况：主营业务 / 经营范围 / 公司简介 / 产业链归属。

    `code` 形如 `603885.SH`。上游没这只票时返回空值字典——调用方拿不到
    东西时的正确处理是「显示本地已有的行业与概念」，而不是把抽屉搞成错误页。
    """
    qs = "&".join([
        "reportName=RPT_F10_BASIC_ORGINFO",
        f"filter={_q(f'((SECUCODE=\"{code}\"))')}",
        "columns=ALL",
        "pageNumber=1",
        "pageSize=1",
        "source=F10",
        "client=PC",
    ])
    data = fetch_json(f"{DATACENTER}?{qs}")
    rows = (data.get("result") or {}).get("data") or []
    if not rows:
        return dict(_EMPTY_PROFILE)
    r = rows[0]
    return {
        "name": str(r.get("SECURITY_NAME_ABBR") or ""),
        "em_industry": str(r.get("EM2016") or ""),
        "csrc": str(r.get("INDUSTRYCSRC1") or ""),
        "main_business": str(r.get("MAIN_BUSINESS") or ""),
        "profile": str(r.get("ORG_PROFILE") or ""),
        "scope": str(r.get("BUSINESS_SCOPE") or ""),
        "found_date": str(r.get("FOUND_DATE") or "")[:10],
        "reg_capital": str(r.get("REG_CAPITAL") or ""),
        "website": str(r.get("ORG_WEB") or ""),
    }


def raw_report(report: str, filter_expr: str, *, page_size: int = 5) -> dict:
    """调试用：直接看某个报表的原始结构，字段变更时先跑它。"""
    qs = "&".join([
        f"reportName={report}",
        f"filter={_q(filter_expr)}",
        "columns=ALL",
        "pageNumber=1",
        f"pageSize={page_size}",
        "source=WEB",
        "client=WEB",
    ])
    return fetch_json(f"{DATACENTER}?{qs}")


# ---------------------------------------------------------------- 全球指数

PUSH2 = "https://push2.eastmoney.com/api/qt/ulist.np/get"
# 东财网页端的公共 ut 令牌，不带会返回空 diff。它不是密钥，值长期不变。
PUSH2_UT = "fa5fd1943c7b386f172d6893dbfba10b"

# `100.` 前缀是东财的「全球指数」市场号。这份清单与网页端「全球指数」
# 页面一致，改代码前先在浏览器里核对一遍 `f12` 字段。
GLOBAL_SECIDS: tuple[str, ...] = (
    "100.DJIA",     # 道琼斯工业平均
    "100.NDX",      # 纳斯达克综合
    "100.SPX",      # 标普 500
    "100.HSI",      # 恒生指数
    "100.HSCEI",    # 恒生中国企业指数
    "100.N225",     # 日经 225
    "100.KS11",     # 韩国 KOSPI
    "100.TWII",     # 台湾加权
    "100.STI",      # 新加坡海峡时报
    "100.SENSEX",   # 印度孟买 SENSEX
    "100.AS51",     # 澳大利亚标普 200
    "100.GDAXI",    # 德国 DAX 30
    "100.FTSE",     # 英国富时 100
    "100.FCHI",     # 法国 CAC 40
    "100.RTS",      # 俄罗斯 RTS
    "100.UDI",      # 美元指数
)

_GLOBAL_FIELDS = "f1,f2,f3,f4,f12,f13,f14,f18,f124"


def global_indices() -> list[dict]:
    """全球主要指数现价，一次请求问完。取不到返回空列表。

    东财的数值都是放大过的整数，倍数写在 `f1`（小数位数）里——道琼斯给
    5075942 配 `f1=2`，除以 100 才是 50759.42。**不要写死 100**：不同板块
    的 `f1` 不一样，写死会把点位算错一到两位。

    `f124` 是这条行情的时间戳（秒）。美股盘后会停在上一个收盘时刻，所以
    它是「数据的时刻」而不是「现在」，前端标注时按数据时刻写。
    """
    url = (f"{PUSH2}?secids={','.join(GLOBAL_SECIDS)}"
           f"&fields={_GLOBAL_FIELDS}&ut={PUSH2_UT}&fltt=1&invt=2")
    try:
        doc = fetch_json(url, interval=0.2, retries=2, timeout=8.0)
    except FetchError:
        return []

    diff = ((doc.get("data") or {}).get("diff")) or []
    out: list[dict] = []
    for row in diff:
        try:
            scale = 10 ** int(row.get("f1") or 2)
            value = float(row["f2"]) / scale
            prev = float(row["f18"]) / scale
            stamp = int(row.get("f124") or 0)
        except (KeyError, TypeError, ValueError):
            continue
        if value <= 0:
            continue
        out.append({
            "code": str(row.get("f12") or ""),
            "name": str(row.get("f14") or "").strip(),
            "value": round(value, 2),
            "prev_close": round(prev, 2),
            "change": round(float(row.get("f4") or 0) / scale, 2),
            "change_pct": round(float(row.get("f3") or 0) / scale, 2),
            "ts": (dt.datetime.fromtimestamp(stamp).strftime("%Y-%m-%d %H:%M")
                   if stamp else ""),
        })
    return out


# ---------------------------------------------------------------- 分笔成交
#
# 逐笔成交只有 `push2` 的 `stock/details/get` 给得干净（东财网页端右侧那列
# 「分时成交」就是它）。模块开头那条「push2 连发会被拉黑」的限制在这里仍然
# 成立，所以调用方必须加缓存：一次只问一只票、同一只票 5 秒内复用结果。
# 这不是批量接口，正常使用频率是「用户切一次股票问一次」，打不满限速。

DETAILS_URL = "https://push2.eastmoney.com/api/qt/stock/details/get"


def _secid(code: str) -> str:
    """`600519.SH` → `1.600519`。0 = 深市，1 = 沪市。"""
    num = code.split(".")[0]
    suffix = code.split(".")[-1].upper() if "." in code else ""
    if suffix:
        market = "1" if suffix == "SH" else "0"
    else:
        market = "1" if num[0] in "659" else "0"
    return f"{market}.{num}"


def ticks(code: str, limit: int = 30) -> dict:
    """最近若干笔成交。

    返回 `{items:[{time, price, volume, amount, side}], prev_close, available}`。
    `volume` 单位「手」（东财原始口径），`amount` 元。

    `side` 取东财的 `f55`：1 = 买盘、2 = 卖盘，其余（集合竞价撮合出来的）
    记为 `mid`。注意它是**主动方**而不是涨跌方向——一笔卖盘打在买一上，
    价格未必跌。前端别按它上色为涨跌。
    """
    limit = max(1, min(int(limit or 30), 200))
    # 多取一些再过滤：15:05 之后的成交是盘后固定价格交易（科创板/北交所），
    # 价格恒等于收盘价、每笔 1 手，混在「分时成交」里看着像死盘
    url = (f"{DETAILS_URL}?secid={_secid(code)}"
           f"&fields1=f1,f2,f3,f4&fields2=f51,f52,f53,f54,f55"
           f"&pos=-{min(limit * 3, 200)}&ut={PUSH2_UT}")
    try:
        doc = fetch_json(url, interval=0.2, retries=1, timeout=8.0)
    except FetchError:
        return {"items": [], "prev_close": 0.0, "available": False}

    data = doc.get("data") or {}
    prev = float(data.get("prePrice") or 0) or 0.0
    items: list[dict] = []
    for raw in data.get("details") or []:
        parts = str(raw).split(",")
        if len(parts) < 5:
            continue
        try:
            clock, price, vol = parts[0], float(parts[1]), float(parts[2])
        except ValueError:
            continue
        if not ("09:15:00" <= clock <= "15:00:59"):
            continue
        flag = parts[4].strip()
        side = "buy" if flag == "1" else ("sell" if flag == "2" else "mid")
        items.append({
            "time": clock,
            "price": round(price, 2),
            "volume": vol,
            "amount": round(price * vol * 100, 2),
            "side": side,
        })
    items = items[-limit:]
    return {"items": items, "prev_close": prev, "available": bool(items)}


# ------------------------------------------------------------------ 涨停板池
#
# 这是「龙头」唯一靠谱的来源。本地能判出「谁涨停了」，但判不出「几连板」
# 「封单多大」「炸过几次」——这些只有东财的涨停板池给。字段口径已核对：
#
# - `p` 是价格 **×1000**（7370 → 7.37 元）。不是东财行情接口惯用的 ×100，
#   用错会把价格放大十倍，而且看起来还挺像真的。
# - `tshare` 名字是「总股本」，实际给的是**总市值（元）**，别拿它当股本用。
# - `fbt` / `lbt` 是 HHMMSS 整数（92500 = 09:25:00）。
# - `zttj` = `{days, ct}`，`days` 天里涨停 `ct` 次——比连板数更能看出成色。
#
# 与 `push2his` 不同，这个接口没有出现过整机拉黑，一天问一次是安全的。

ZT_URL = "https://push2ex.eastmoney.com/getTopicZTPool"
DT_URL = "https://push2ex.eastmoney.com/getTopicDTPool"
_POOL_UT = "7eea3edcaed734bea9cbfc24409ed989"


def _pool(url: str, date: str, page_size: int = 200,
          sort: str = "fbt%3Aasc") -> list[dict]:
    """涨停池按「首次封板时间」排，跌停池没有 `fbt` 字段，要按封单资金排。"""
    out: list[dict] = []
    page = 0
    while page < 8:                      # 安全阀：极端行情也就 200 多只
        q = (f"{url}?ut={_POOL_UT}&dpt=wz.ztzt&Pageindex={page}"
             f"&pagesize={page_size}&sort={sort}&date={date}")
        try:
            doc = fetch_json(q, interval=0.3, retries=1, timeout=10.0)
        except FetchError:
            break
        data = doc.get("data") or {}
        rows = data.get("pool") or []
        if not rows:
            break
        out.extend(rows)
        if len(out) >= int(data.get("tc") or 0):
            break
        page += 1
    return out


def _num(raw: object) -> float:
    """安全转 float。东财对缺失字段给 `"-"` 或 `None`，不能直接 float()。"""
    if raw is None or raw == "" or raw == "-":
        return 0.0
    try:
        return float(raw)                # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0


def _clock(raw: object) -> str:
    """92500 → '09:25:00'。"""
    try:
        n = int(raw)                     # type: ignore[arg-type]
    except (TypeError, ValueError):
        return ""
    s = f"{n:06d}"
    return f"{s[0:2]}:{s[2:4]}:{s[4:6]}"


def limit_up_pool(date: str) -> list[dict]:
    """某日涨停板池。`date` 形如 `2026-11-18` 或 `20261118`。

    返回 `[{code, name, price, change_pct, amount, float_mv, turnover,
    boards, first_seal, last_seal, seal_fund, broken, industry, stat_days,
    stat_count}]`。`boards` 就是连板数，1 = 首板。
    """
    day = str(date).replace("-", "")
    out: list[dict] = []
    for r in _pool(ZT_URL, day):
        try:
            code = str(r.get("c") or "")
            if not code:
                continue
            stat = r.get("zttj") or {}
            out.append({
                "code": f"{code}.{'SH' if int(r.get('m') or 0) == 1 else 'SZ'}",
                "name": str(r.get("n") or "").strip(),
                "price": round(_num(r.get("p")) / 1000, 2),
                "change_pct": round(_num(r.get("zdp")), 2),
                "amount": _num(r.get("amount")),
                "float_mv": round(_num(r.get("ltsz")) / 1e8, 2),
                "turnover": round(_num(r.get("hs")), 2),
                "boards": int(_num(r.get("lbc"))) or 1,
                "first_seal": _clock(r.get("fbt")),
                "last_seal": _clock(r.get("lbt")),
                "seal_fund": _num(r.get("fund")),
                "broken": int(_num(r.get("zbc"))),
                "industry": str(r.get("hybk") or "").strip(),
                "stat_days": int(_num(stat.get("days"))),
                "stat_count": int(_num(stat.get("ct"))),
            })
        except (TypeError, ValueError):
            continue
    return out


def limit_down_pool(date: str) -> list[dict]:
    """某日跌停板池，字段比涨停少（没有连板数），够用即可。"""
    day = str(date).replace("-", "")
    out: list[dict] = []
    for r in _pool(DT_URL, day, sort="fund%3Aasc"):
        try:
            code = str(r.get("c") or "")
            if not code:
                continue
            out.append({
                "code": f"{code}.{'SH' if int(r.get('m') or 0) == 1 else 'SZ'}",
                "name": str(r.get("n") or "").strip(),
                "price": round(_num(r.get("p")) / 1000, 2),
                "change_pct": round(_num(r.get("zdp")), 2),
                "amount": _num(r.get("amount")),
                "float_mv": round(_num(r.get("ltsz")) / 1e8, 2),
                "turnover": round(_num(r.get("hs")), 2),
                "seal_fund": _num(r.get("fund")),
                "last_seal": _clock(r.get("lbt")),
                "industry": str(r.get("hybk") or "").strip(),
                "days": int(_num(r.get("days"))),
            })
        except (TypeError, ValueError):
            continue
    return out
