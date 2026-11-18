"""新浪财经数据源：全 A 名单 + 快照 + 实时报价 + 分时 + 个股资金流历史。

为什么主力数据源是新浪而不是东财：东财的 `push2` 在连续请求下会把整台机器
拉黑（实测 8 次全 `RemoteDisconnected`，且解封时间不可控），而它的 `datacenter`
又只提供财报类横截面。新浪 `Market_Center` 一次翻页就给完整快照（价格、成交额、
换手率、市盈率、市值），`MoneyFlow` 还给日频资金流历史，两个接口都稳定。

代价是新浪单页硬上限 100 条，5578 只要翻 56 页；这仍然比东财「翻两页被封」
划算得多。

**实时数据走另一条路**：`hq.sinajs.cn` 一次可以问几百个代码（`realtime`），
延迟 1 秒级，是轮询用的接口；`Market_Center` 那套是「全市场横截面」，
翻 56 页要十几秒，只能给日频同步用。两者不要混用。
"""

from __future__ import annotations

import json
import re
import time

from .base import FetchError, fetch

BASE = "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php"
KLINE_URL = ("https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
             "CN_MarketData.getKLineData?symbol={sym}&scale=240&ma=no&datalen={bars}")
# 实时报价：一次请求多个代码，返回 GBK 的 `var hq_str_xxx="...";`
REALTIME_URL = "https://hq.sinajs.cn/list={symbols}"
# 当日分时（逐分钟）：给的是今天从集合竞价开始的整条曲线
MINLINE_URL = ("https://quotes.sina.cn/cn/api/openapi.php/"
               "CN_MinlineService.getMinlineData?symbol={sym}")
# hq.sinajs.cn 从 2022 年起校验 Referer，不带就 403；这不是可选项
HQ_HEADERS = {"Referer": "https://finance.sina.com.cn/"}

# 新浪的节点名：hs_a = 沪深 A 股（含北交所）
NODE_ALL = "hs_a"


def _json(text: str):
    body = text.strip()
    if not body or body in ("null", "[]"):
        return []
    return json.loads(body)


def _sym(code: str) -> str:
    """`600519.SH` → `sh600519`。

    新浪全站都要「交易所前缀 + 纯数字」，写成 `600519sh` 一律静默返回
    `null`（HTTP 200，不报错），是最容易漏掉的一处坑。北交所是 `bj`。
    """
    num, _, suffix = code.partition(".")
    prefix = {"SH": "sh", "SZ": "sz", "BJ": "bj"}.get(suffix.upper(), "sh")
    return f"{prefix}{num}"


def kline(code: str, bars: int = 520) -> list[dict]:
    """日线，**不复权**。

    选新浪做日线主源不是因为它全，而是因为它稳：实测 100 只唯一代码并发
    抓取 100% 成功、5 秒完成，全量 5578 只约 5 分钟。腾讯同类接口在几百次
    请求后开始整片返回 501，靠它扛一万次请求是不可行的。

    代价是新浪只给不复权价，且没有成交额。复权因子由分红送配事件推算
    （见 `sync.py` 的 `_adjust_factors`），成交额由「量 × 均价」估算。
    """
    sym = _sym(code)
    url = KLINE_URL.format(sym=sym, bars=bars)
    try:
        rows = _json(fetch(url, interval=0.1))
    except (FetchError, json.JSONDecodeError):
        return []
    out: list[dict] = []
    for r in rows:
        try:
            out.append({
                "date": r["day"],
                "open": float(r["open"]),
                "high": float(r["high"]),
                "low": float(r["low"]),
                "close": float(r["close"]),
                # 新浪的量是「股」，库里统一用「手」
                "volume": float(r["volume"]) / 100.0,
            })
        except (KeyError, TypeError, ValueError):
            continue
    return out


def index_kline(code: str, bars: int = 520) -> list[dict]:
    """指数日线。与个股走同一个接口，只是代码换成指数代码。

    指数不返回成交额（`volume` 是成分股成交量合计，单位「股」）。下游
    `index_daily.amount` 只被指数走势图用到、且不展示成交额，所以这里
    **不编造**：金额留给调用方填 0。
    """
    sym = _sym(code)
    url = KLINE_URL.format(sym=sym, bars=bars)
    try:
        rows = _json(fetch(url, interval=0.1))
    except (FetchError, json.JSONDecodeError):
        return []
    out: list[dict] = []
    for r in rows:
        try:
            out.append({
                "date": r["day"],
                "open": float(r["open"]),
                "high": float(r["high"]),
                "low": float(r["low"]),
                "close": float(r["close"]),
                "volume": float(r["volume"]) / 100.0,
            })
        except (KeyError, TypeError, ValueError):
            continue
    return out


def count(node: str = NODE_ALL) -> int:
    txt = fetch(f"{BASE}/Market_Center.getHQNodeStockCount?node={node}", interval=0.1)
    return int(txt.strip().strip('"') or 0)


def _page(page: int, node: str = NODE_ALL) -> list[dict]:
    url = (f"{BASE}/Market_Center.getHQNodeData?page={page}&num=100"
           f"&sort=symbol&asc=1&node={node}&symbol=&_s_r_a=page")
    try:
        return _json(fetch(url, interval=0.12))
    except (FetchError, json.JSONDecodeError):
        time.sleep(0.4)
        return _json(fetch(url, interval=0.3, retries=5))


def _f(value, default: float = 0.0) -> float:
    try:
        if value in (None, "", "-"):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def board_of(code6: str) -> tuple[str, str]:
    """代码 → (交易所, 板块)。"""
    if code6.startswith(("688", "689")):
        return "SH", "科创板"
    if code6.startswith(("300", "301")):
        return "SZ", "创业板"
    if code6.startswith(("4", "8")) or code6.startswith("920"):
        return "BJ", "北交所"
    if code6.startswith("6"):
        return "SH", "主板"
    return "SZ", "主板"


def snapshot(node: str = NODE_ALL) -> list[dict]:
    """全 A 快照。逐页拉，翻到空页为止。"""
    total = count(node)
    pages = max((total + 99) // 100, 1)
    rows: list[dict] = []
    for page in range(1, pages + 1):
        batch = _page(page, node)
        if not batch:
            break
        # 末页可能重复上一页，按 symbol 去重
        seen = {r["code"] for r in rows}
        for r in batch:
            code6 = str(r.get("code") or "")
            if len(code6) != 6 or code6 in seen:
                continue
            rows.append(_normalize(r, code6))
        if len(batch) < 100:
            break
    return rows


def _normalize(r: dict, code6: str) -> dict:
    exchange, board = board_of(code6)
    name = str(r.get("name") or "").strip()
    price = _f(r.get("trade"))
    prev = _f(r.get("settlement"))
    volume = _f(r.get("volume")) / 100.0            # 股 → 手
    # 停牌票 trade 为 0，用昨收补，避免市值 / 流通股本算成 0
    ref = price or prev
    return {
        "code": f"{code6}.{exchange}",
        "name": name,
        "exchange": exchange,
        "board": board,
        "is_st": 1 if ("ST" in name.upper() or "退" in name) else 0,
        "price": price,
        "prev_close": prev,
        "change": _f(r.get("pricechange")),
        "change_pct": _f(r.get("changepercent")),
        "open": _f(r.get("open")),
        "high": _f(r.get("high")),
        "low": _f(r.get("low")),
        "volume": volume,
        "amount": _f(r.get("amount")),
        "turnover": _f(r.get("turnoverratio")),
        "pe": _f(r.get("per")),
        "pb": _f(r.get("pb")),
        "total_mv": round(_f(r.get("mktcap")) / 1e4, 4),      # 万元 → 亿元
        "float_mv": round(_f(r.get("nmc")) / 1e4, 4),
        "float_shares": (round(_f(r.get("nmc")) * 1e4 / ref) if ref > 0 else 0.0),
    }


def money_flow(code: str, days: int = 30) -> list[dict]:
    """个股日频资金流。

    新浪这份数据的口径是「超大单 + 大单」，与东财的「主力净流入」一致，
    但只给净额与超大单净额，没有大/中/小的四档拆分，所以 `large_net` 用
    净额减超大单反推，中单/小单留 0 而不去编。
    """
    url = (f"{BASE}/MoneyFlow.ssl_qsfx_zjlrqs?page=1&num={days}"
           f"&sort=opendate&asc=0&daima={_sym(code)}")
    try:
        rows = _json(fetch(url, interval=0.1))
    except (FetchError, json.JSONDecodeError):
        return []
    out: list[dict] = []
    for r in rows:
        date = str(r.get("opendate") or "")
        if len(date) != 10:
            continue
        net = _f(r.get("netamount"))
        xlarge = _f(r.get("r0_net"))
        out.append({
            "date": date,
            "main_net": net,
            "xlarge_net": xlarge,
            "large_net": net - xlarge,
            "mid_net": 0.0,
            "small_net": 0.0,
            "main_ratio": round(_f(r.get("ratioamount")) * 100, 3) if r.get("ratioamount") else None,
        })
    return out


# ---------------------------------------------------------------- 实时报价

def _is_index_sym(sym: str) -> bool:
    """`sh000001` / `sz399001` 是指数，其余是股票。

    判据不是猜的：沪市个股是 6xxxxx / 688xxx / 900xxx，`sh000xxx` 只可能是指数；
    深市指数统一 `399xxx`，而 `sz000xxx` 是个股（平安银行 000001.SZ 就是）。
    """
    s = sym.lower()
    return (s.startswith("sh000") or s.startswith("sz399"))


_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_CLOCK_RE = re.compile(r"^\d{1,2}:\d{2}:\d{2}$")


def _locate_ts(fields: list[str]) -> tuple[str, str]:
    """在不定长的字段里找出「日期 + 时间」这对。

    沪市 33 段、深市指数 32 段、北交所 39 段，用下标或从尾部数都会踩空，
    只能按格式扫一遍。日期后面紧跟的那个字段就是时间。
    """
    for i, value in enumerate(fields):
        if _DATE_RE.match(value):
            clock = fields[i + 1] if i + 1 < len(fields) else ""
            if not _CLOCK_RE.match(clock):
                clock = ""
            return value, clock
    return "", ""


def _hq_rows(text: str) -> dict[str, list[str]]:
    """把 `var hq_str_sh600519="...";` 逐行拆成 {symbol: 字段列表}。"""
    out: dict[str, list[str]] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("var hq_str_"):
            continue
        head, _, payload = line.partition("=")
        symbol = head[len("var hq_str_"):].strip()
        # 停牌 / 代码写错时新浪返回空串，这是正常响应而不是错误
        body = payload.strip().rstrip(";").strip().strip('"').rstrip(",")
        if not body:
            continue
        out[symbol] = body.split(",")
    return out


def _parse_hq(fields: list[str], is_index: bool) -> dict:
    """`hq.sinajs.cn` 的定长字段。

    前 10 段全市场一致（名称、开、昨收、现价、高、低、买一、卖一、成交量、成交额），
    尾部**不一致**：沪市 33 段、深市指数 32 段、北交所 39 段（多出均价、涨跌标志等）。
    所以日期/时间不能按倒数第几位取，得按格式找——否则北交所标的的 `ts`
    会取到 `8300000 B` 这种字段上。

    单位：`volume` 股票给「股」（这里折成手），指数给「手」原样保留；
    `amount` 两种都已经是「元」，与全库口径一致。
    """
    name = fields[0].strip()
    open_ = _f(fields[1])
    prev = _f(fields[2])
    price = _f(fields[3])
    date, clock = _locate_ts(fields)
    ref = price or prev                      # 停牌时 price=0，用昨收兜底
    change = ref - prev if prev > 0 else 0.0
    return {
        "name": name,
        "price": price,
        "prev_close": prev,
        "open": open_,
        "high": _f(fields[4]),
        "low": _f(fields[5]),
        "change": round(change, 4),
        "change_pct": round(change / prev * 100, 4) if prev > 0 else 0.0,
        "volume": _f(fields[8]) if is_index else round(_f(fields[8]) / 100.0, 2),
        "amount": _f(fields[9]),
        "suspended": 1 if price <= 0 else 0,
        "ts": f"{date} {clock}".strip(),
    }


def realtime(codes: list[str], chunk: int = 80) -> dict[str, dict]:
    """批量实时报价。`{code: 报价}`，只包含真的取到的。

    一次请求可以带几百个代码，但把 URL 控制在 ~2KB 内更稳，所以按 80 个一组。
    分组失败不影响其它组：轮询本来就下一轮还会再来。
    """
    want = [c for c in dict.fromkeys(codes) if c]
    out: dict[str, dict] = {}
    for start in range(0, len(want), chunk):
        part = want[start:start + chunk]
        symbols = [_sym(c) for c in part]
        url = REALTIME_URL.format(symbols=",".join(symbols))
        try:
            txt = fetch(url, encoding="gbk", headers=HQ_HEADERS,
                        interval=0.15, retries=2, timeout=8.0)
        except FetchError:
            continue
        rows = _hq_rows(txt)
        for code, symbol in zip(part, symbols):
            fields = rows.get(symbol)
            if not fields or len(fields) < 10:
                continue
            item = _parse_hq(fields, _is_index_sym(symbol))
            item["code"] = code
            out[code] = item
    return out


def minline(code: str) -> dict | None:
    """当日逐分钟分时。

    比腾讯那条分时接口可靠（腾讯现在整片返回空），而且直接给了 `avg_p`
    ——真实均价线，不用拿成交量自己算近似值。

    返回的 `prev_close` 恒为 0：这个接口不提供昨收，由调用方用实时报价补。
    """
    url = MINLINE_URL.format(sym=_sym(code))
    try:
        doc = json.loads(fetch(url, encoding="utf-8", headers=HQ_HEADERS,
                               interval=0.15, retries=2, timeout=8.0))
    except (FetchError, json.JSONDecodeError):
        return None
    rows = ((doc.get("result") or {}).get("data")) or []
    times: list[str] = []
    prices: list[float] = []
    avgs: list[float] = []
    volumes: list[float] = []
    for row in rows:
        clock = str(row.get("m") or "")
        price = _f(row.get("p"))
        if not clock or price <= 0:
            continue
        times.append(clock)
        prices.append(round(price, 3))
        avgs.append(round(_f(row.get("avg_p")) or price, 3))
        volumes.append(round(_f(row.get("v")) / 100.0, 2))
    if not times:
        return None
    return {
        "times": times,
        "prices": prices,
        "avg": avgs,
        "volumes": volumes,
        "prev_close": 0.0,
    }
