"""实时报价的短 TTL 缓存。

为什么需要这一层：一次刷新里前端要同时拿「当前票 + 自选 N 只 + 7 个指数」，
而且可能开着多个标签页。如果每个调用点各自打网络，3 秒一轮就会变成
一串重复请求；新浪那边限速一旦被触发，实时性反而没了。

缓存**按代码**存而不是按请求存：不同页面要的代码集合不一样，
按请求做 key 会互相顶掉，每次都要全量重取。

两条设计约束：

1. TTL 只做「短路」，不做「兜底」。过期就重取，取不到才退回旧值并标记
   `stale`——盘中断网时界面显示的是「上一次成功的价 + 旧时间戳」，
   而不是一个看起来新鲜的错数字。
2. 上游失败不抛异常。轮询是持续行为，这一轮失败下一轮自然补上；
   抛异常只会让前端把整个报价区打红。
"""

from __future__ import annotations

import threading
import time

from .sources import sina


class LiveFeed:
    def __init__(self, ttl: float = 3.0, stale_ttl: float = 120.0):
        self.ttl = ttl
        # 超过这个年龄的旧值不再拿来充数，宁可显示「—」
        self.stale_ttl = stale_ttl
        self._cache: dict[str, tuple[float, dict]] = {}
        self._lock = threading.Lock()
        # 统计，便于 /data/status 观察是否在被限速
        self.fetches = 0
        self.failures = 0

    def quotes(self, codes: list[str]) -> dict[str, dict]:
        want = [c for c in dict.fromkeys(codes) if c]
        if not want:
            return {}

        # 1. 先捞还在 TTL 内的
        fresh: dict[str, dict] = {}
        now = time.monotonic()
        with self._lock:
            for code in want:
                hit = self._cache.get(code)
                if hit and now - hit[0] < self.ttl:
                    fresh[code] = dict(hit[1])

        # 2. 缺的才去问上游
        missing = [c for c in want if c not in fresh]
        if missing:
            try:
                self.fetches += 1
                fetched = sina.realtime(missing)
            except Exception:                        # noqa: BLE001 - 实时链路不许炸
                self.failures += 1
                fetched = {}
            if fetched:
                stamp = time.monotonic()
                with self._lock:
                    for code, item in fetched.items():
                        self._cache[code] = (stamp, item)
                # 必须同步回填 fresh，否则下面「没取到」的分支会把这批
                # 刚拿到的最新价当成旧值、还算出负的 age
                for code, item in fetched.items():
                    if code in want:
                        fresh[code] = dict(item)

        # 3. 仍然缺的，退回最近一次成功值并按年龄标记
        now = time.monotonic()
        with self._lock:
            for code in want:
                if code in fresh:
                    continue
                hit = self._cache.get(code)
                if not hit:
                    continue
                age = now - hit[0]
                if age > self.stale_ttl:
                    continue
                old = dict(hit[1])
                old["stale"] = True
                old["age"] = round(age, 1)
                fresh[code] = old

        return fresh

    def stats(self) -> dict:
        return {"fetches": self.fetches, "failures": self.failures,
                "cached": len(self._cache), "ttl": self.ttl}


_FEED: LiveFeed | None = None
_FEED_LOCK = threading.Lock()


def feed() -> LiveFeed:
    """进程级单例。多实例会让 TTL 形同虚设。"""
    global _FEED
    with _FEED_LOCK:
        if _FEED is None:
            _FEED = LiveFeed()
        return _FEED
