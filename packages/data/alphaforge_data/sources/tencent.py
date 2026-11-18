"""腾讯行情源：日线 / 分时 / 实时 / 指数。

选腾讯而不是东财 `push2his` 的原因很实际：后者在本机网络下被 reset，
而腾讯 `ifzq` 稳定、单次可给 800 根日线、且同时提供复权与不复权两套。

三个必须记住的坑：
1. 日线行序是 **[日期, 开, 收, 高, 低, 量]**，不是常规的 OHLC。写错会把
   最高价当成收盘价，且因为数值接近，肉眼很难发现。
2. `fqt` 参数为空串时返回的键是 `day`，`qfq` 时是 `qfqday`，两者不能混取。
3. `web.ifzq.gtimg.cn` 在连续请求几百次后会直接返回 **HTTP 501**——不是
   限速，是反爬网关拒绝，重试也没用。必须换 `proxy.finance.qq.com` 镜像。
"""

from __future__ import annotations

import threading
import time

from .base import BlockedError, FetchError, fetch, fetch_json

KLINE_PATHS = "/ifzqgtimg/appstock/app/fqkline/get?param={param}"
MINUTE_PATHS = "/ifzqgtimg/appstock/app/minute/query?code={sym}"

# 同一套接口挂在两个域名上，但**它们是交替封禁的**：猛打一阵，当前域名开始
# 整片返回 501，而另一个域名此时通常是好的。所以这里不是「主备切换」，
# 而是带冷却的轮换：哪个被拒就把哪个拉黑 COOLDOWN 秒，立刻换下一个。
BASES = (
    "https://web.ifzq.gtimg.cn",
    "https://proxy.finance.qq.com",
)
COOLDOWN = 240.0          # 秒。实测封禁持续几分钟，留足冷却时间
REALTIME = "https://qt.gtimg.cn/q="

_BLOCK: dict[str, float] = {}
_BLOCK_LOCK = threading.Lock()


def _mark_blocked(base: str) -> None:
    with _BLOCK_LOCK:
        _BLOCK[base] = time.monotonic() + COOLDOWN


def _alive() -> list[str]:
    now = time.monotonic()
    with _BLOCK_LOCK:
        ok = [b for b in BASES if _BLOCK.get(b, 0.0) <= now]
        if ok:
            return ok
        # 全在冷却：挑最早解封的那个硬着头皮试，总比整批任务直接失败强
        return sorted(BASES, key=lambda b: _BLOCK.get(b, 0.0))


def _get_json(path: str, *, interval: float = 0.05, retries: int = 2) -> dict:
    """带冷却的域名轮换。被拒立即换域名，不在同一个域名上耗重试。"""
    last: Exception | None = None
    for _ in range(len(BASES) + 1):
        base = _alive()[0]
        try:
            return fetch_json(base + path, interval=interval, retries=retries)
        except BlockedError as exc:
            _mark_blocked(base)
            last = exc
        except (FetchError, ValueError) as exc:
            # 网络抖动 / 坏 JSON：换一个域名再试，但不拉黑
            last = exc
    raise FetchError(f"腾讯行情全部域名失败：{path} :: {last}")


def cooldown_state() -> dict[str, float]:
    """还有多少秒解封，调试与日志用。"""
    now = time.monotonic()
    with _BLOCK_LOCK:
        return {b: round(max(0.0, t - now), 1) for b, t in _BLOCK.items()}


def symbol_of(code: str) -> str:
    """`600519.SH` → `sh600519`。腾讯用小写前缀。"""
    num, _, ex = code.partition(".")
    ex = ex.upper()
    if ex == "SH":
        return f"sh{num}"
    if ex == "SZ":
        return f"sz{num}"
    if ex == "BJ":
        return f"bj{num}"
    # 没有后缀时按代码段推断，北交所 4/8/9 开头
    if num[:1] in ("4", "8", "9") and not num.startswith("920"):
        return f"bj{num}"
    return ("sh" if num[:1] in ("6", "9") else "sz") + num


def code_of(symbol: str) -> str:
    """`sh600519` → `600519.SH`。"""
    num, pre = symbol[2:], symbol[:2].upper()
    return f"{num}.{pre}"


def _bars(node: dict, key: str) -> list[dict]:
    rows = node.get(key) or []
    out: list[dict] = []
    for r in rows:
        if len(r) < 6:
            continue
        try:
            out.append({
                "date": r[0],
                "open": float(r[1]),
                "close": float(r[2]),
                "high": float(r[3]),
                "low": float(r[4]),
                "volume": float(r[5]),      # 手
            })
        except (TypeError, ValueError):
            continue
    return out


def kline(code: str, bars: int = 520, *, adjust: str = "qfq") -> list[dict]:
    """日线。`adjust="qfq"` 取前复权，`""` 取不复权。

    节流放到 0.05s：腾讯对日线的容忍度很高，实测 10 并发下没有出现截断，
    而全量 5578 只 × 2 次请求，节流每降 0.01s 就省 2 分钟。
    """
    sym = symbol_of(code)
    param = f"{sym},day,,,{bars},{adjust}"
    data = _get_json(KLINE_PATHS.format(param=param), interval=0.05)
    node = (data.get("data") or {}).get(sym) or {}
    key = "qfqday" if adjust == "qfq" else "day"
    rows = _bars(node, key)
    if not rows and key != "day":       # 停牌/退市可能只留 day
        rows = _bars(node, "day")
    return rows


def index_kline(code: str, bars: int = 520) -> list[dict]:
    """指数日线：指数只有不复权，`fqt` 留空。"""
    sym = symbol_of(code)
    data = _get_json(KLINE_PATHS.format(param=f"{sym},day,,,{bars},"), interval=0.08)
    node = (data.get("data") or {}).get(sym) or {}
    return _bars(node, "day")


def minute(code: str) -> dict:
    """当日分时：240 个点，含价格 / 累计量 / 均价。"""
    sym = symbol_of(code)
    data = _get_json(MINUTE_PATHS.format(sym=sym), interval=0.1)
    node = ((data.get("data") or {}).get(sym) or {}).get("data") or {}
    raw = node.get("data") or []
    times: list[str] = []
    prices: list[float] = []
    volumes: list[float] = []
    prev = 0.0
    for line in raw:
        parts = line.split(" ")
        if len(parts) < 3:
            continue
        hhmm = parts[0]
        times.append(f"{hhmm[:2]}:{hhmm[2:]}")
        prices.append(float(parts[1]))
        cum = float(parts[2])
        volumes.append(max(cum - prev, 0.0))
        prev = cum
    return {
        "code": code,
        "prev_close": float(node.get("prec") or 0) or (prices[0] if prices else 0.0),
        "date": node.get("date"),
        "times": times,
        "prices": prices,
        "volumes": volumes,
    }


def realtime(codes: list[str]) -> dict[str, dict]:
    """批量实时快照。腾讯单次能吃上百个代码，但这里按 60 个一批控制 URL 长度。"""
    out: dict[str, dict] = {}
    for i in range(0, len(codes), 60):
        chunk = codes[i: i + 60]
        syms = ",".join(symbol_of(c) for c in chunk)
        txt = fetch(REALTIME + syms, encoding="gbk", interval=0.08)
        for line in txt.split(";"):
            if "=" not in line:
                continue
            head, _, body = line.partition("=")
            sym = head.strip().replace("v_", "")
            if not sym:
                continue
            f = body.strip().strip('"').split("~")
            if len(f) < 45 or not f[3]:
                continue
            row = _parse_realtime(f)
            if row:
                out[code_of(sym)] = row
    return out


def _num(parts: list[str], idx: int) -> float:
    try:
        if idx >= len(parts):
            return 0.0
        txt = parts[idx]
        return float(txt) if txt not in ("", "-") else 0.0
    except ValueError:
        return 0.0


def _parse_realtime(f: list[str]) -> dict | None:
    price = _num(f, 3)
    if price <= 0:
        return None
    return {
        "name": f[1],
        "price": price,
        "prev_close": _num(f, 4),
        "open": _num(f, 5),
        "volume": _num(f, 36),          # 手
        "amount": _num(f, 37) * 1e4,    # 万元 → 元
        "change": _num(f, 31),
        "change_pct": _num(f, 32),
        "high": _num(f, 33),
        "low": _num(f, 34),
        "turnover": _num(f, 38),
        "pe": _num(f, 39),
        "float_mv": _num(f, 44),        # 亿元
        "total_mv": _num(f, 45),        # 亿元
        "pb": _num(f, 46),
        "volratio": _num(f, 49),
        "ts": f[30] if len(f) > 30 else "",
    }
