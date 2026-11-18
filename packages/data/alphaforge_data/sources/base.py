"""HTTP 抓取底座：节流、重试、解压、编码兜底。

两个数据源都有反爬：东财 `push2` 在连续请求下会直接 `RemoteDisconnected`，
腾讯在短时间内要同一个票的大量历史时会返回截断的结果。所以限速与重试
不是「锦上添花」，是这个数据层能不能跑完 5000+ 标的的前提。

节流做成**按主机的令牌桶**而不是全局 sleep：东财与腾讯互不影响，
两边并行时才不会互相拖慢。
"""

from __future__ import annotations

import gzip
import io
import json
import os
import random
import threading
import time
import urllib.error
import urllib.request
import zlib

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

DEFAULT_HEADERS = {
    "User-Agent": UA,
    "Accept": "*/*",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Accept-Encoding": "gzip, deflate",
    "Connection": "close",
}


class FetchError(RuntimeError):
    pass


class BlockedError(FetchError):
    """域名被反爬网关拒绝（501/403/429）。

    这类失败**重试没有意义**：腾讯会连续几千次返回 501，直到冷却结束。
    单独一个异常类型，好让上层立刻换域名而不是在同一个域名上耗满重试次数。
    """


class _HostGate:
    """按主机限速：**令牌桶**，允许 `burst` 个请求同时在飞。

    旧实现是「两次请求之间必须间隔 interval」，等价于把并发压成 1：
    `workers=12` 与 `workers=2` 的实测吞吐完全一样（都是 10 只/秒），
    多开的线程全在 `wait()` 里排队，白白占着内存。

    令牌桶保持「长期平均速率 = 1/interval」这个约束不变（不会比原来更凶地
    持续刷同一个域名），只是允许短时间内有 `burst` 个请求并发飞行，
    于是吞吐从 1/interval 变成 burst/interval。默认 burst 取 4，
    新浪实测 40 请求/秒仍在限流阈值内；被拒时上层会自动降档（见 sync.py）。
    """

    def __init__(self, interval: float, burst: int = 1):
        self.interval = max(interval, 0.001)
        self.burst = max(1, int(burst))
        # 稳态速率 = 并发窗口 / 单请求最小间隔：既保留「相邻两次请求至少间隔
        # interval」的节奏，又让 burst 个请求能同时在飞
        self.rate = self.burst / self.interval
        self.capacity = float(self.burst)
        self._tokens = self.capacity
        self._stamp = time.monotonic()
        self._lock = threading.Lock()

    def wait(self) -> None:
        while True:
            with self._lock:
                now = time.monotonic()
                self._tokens = min(self.capacity, self._tokens + (now - self._stamp) * self.rate)
                self._stamp = now
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return
                delay = (1.0 - self._tokens) / self.rate
            time.sleep(min(delay, 0.25))

    def retune(self, burst: int) -> None:
        """限速撞墙后降档：把并发窗口收窄，速率保持不变。"""
        with self._lock:
            self.burst = max(1, int(burst))
            self.capacity = float(self.burst)
            self._tokens = min(self._tokens, self.capacity)


_GATES: dict[str, _HostGate] = {}
_GATE_LOCK = threading.Lock()

# 单域名并发窗口。默认 6：实测 1500 只样本 51 只/秒、100% 成功，
# 长期平均速率仍是「每个 in-flight 间隔 interval」，不是无节制猛刷。
# 撞到反爬网关会自动减半（见 fetch），也可用 AF_FETCH_BURST 固定。
FETCH_BURST = max(1, int(os.environ.get("AF_FETCH_BURST") or 6))


def gate(host: str, interval: float) -> _HostGate:
    with _GATE_LOCK:
        g = _GATES.get(host)
        if g is None:
            g = _HostGate(interval, FETCH_BURST)
            _GATES[host] = g
        return g


def set_burst(burst: int) -> None:
    """全局限速降档（撞到反爬网关时调用）。"""
    global FETCH_BURST
    FETCH_BURST = max(1, int(burst))
    with _GATE_LOCK:
        for g in _GATES.values():
            g.retune(FETCH_BURST)


def _decode(raw: bytes, encoding: str | None) -> str:
    if encoding:
        return raw.decode(encoding, "replace")
    for enc in ("utf-8", "gbk", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def fetch(
    url: str,
    *,
    encoding: str | None = None,
    timeout: float = 20.0,
    retries: int = 4,
    interval: float = 0.12,
    headers: dict[str, str] | None = None,
) -> str:
    """取文本。失败按指数退避重试；4xx 不重试（重试也没用）。"""
    host = url.split("/")[2] if "//" in url else url
    gate(host, interval).wait()

    merged = {**DEFAULT_HEADERS, **(headers or {})}
    last: Exception | None = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=merged)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
                ctype = (resp.headers.get("Content-Encoding") or "").lower()
            if "gzip" in ctype:
                raw = gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
            elif "deflate" in ctype:
                raw = zlib.decompress(raw, -zlib.MAX_WBITS)
            return _decode(raw, encoding)
        except urllib.error.HTTPError as exc:
            last = exc
            if exc.code in (403, 429, 501):
                # 反爬网关的拒绝：立刻上抛，让调用方换域名。
                # 同时把并发窗口收窄一半——撞墙说明当前的 burst 已经越界，
                # 不降档的话后面一万次请求会一直撞同一堵墙。
                if FETCH_BURST > 1:
                    set_burst(FETCH_BURST // 2)
                raise BlockedError(f"HTTP {exc.code} {url}") from exc
            if 400 <= exc.code < 500:
                raise FetchError(f"HTTP {exc.code} {url}") from exc
        except Exception as exc:      # noqa: BLE001 - 网络层什么都能抛
            last = exc
        # 0.4 / 0.8 / 1.6 ... 加抖动，避免多线程同步重试再次撞墙
        time.sleep(0.4 * (2 ** attempt) + random.random() * 0.15)

    raise FetchError(f"取数失败（{retries} 次）：{url} :: {last}")


def fetch_json(url: str, **kwargs) -> dict:
    txt = fetch(url, **kwargs)
    return loads(txt)


def loads(txt: str) -> dict:
    """兼容 jsonp 包装：`cb({...})` → `{...}`。"""
    body = txt.strip()
    if not body or body[0] not in "[{":
        lo, hi = body.find("("), body.rfind(")")
        if lo >= 0 and hi > lo:
            body = body[lo + 1: hi]
    return json.loads(body)
