"""A 股交易时段判定（纯函数，不碰时钟也不碰数据）。

为什么放在 `core`：这是规则，不是 I/O。给它传入「现在几点」和「报价日期」，
它只负责回答「此刻该不该轮询、该显示实时还是收盘」。这样它可以脱离
网络和数据库单测，也符合分层约束里「core 不做 I/O」那条。

时段边界按沪深两市统一口径（含北交所）：

    09:15-09:25  集合竞价（有价格但会跳，不该当作成交价展示）
    09:30-11:30  上午连续竞价
    13:00-15:00  下午连续竞价
    11:30-13:00  午间休市
    15:00 之后   已收盘

**节假日不查日历**，用报价自带的时间戳反推：如果按钟点算「现在应该在盘中」，
但报价的日期不是今天，那今天就是休市。这比维护一张节假日表可靠——
表会过期，行情时间戳不会。
"""

from __future__ import annotations

import datetime as dt

# 状态取值：前端只在 「open / auction」 时轮询
PRE = "pre"
AUCTION = "auction"
OPEN = "open"
BREAK = "break"
CLOSED = "closed"
WEEKEND = "weekend"
HOLIDAY = "holiday"

_LABELS = {
    PRE: "未开盘",
    AUCTION: "集合竞价",
    OPEN: "交易中",
    BREAK: "午间休市",
    CLOSED: "已收盘",
    WEEKEND: "周末休市",
    HOLIDAY: "今日休市",
}

_AM_OPEN = dt.time(9, 30)
_AM_CLOSE = dt.time(11, 30)
_PM_OPEN = dt.time(13, 0)
_PM_CLOSE = dt.time(15, 0)
_AUCTION_START = dt.time(9, 15)


def session_state(now: dt.datetime, quote_date: str | None = None) -> str:
    """当前处于哪个时段。

    `quote_date` 是行情源返回的日期（`YYYY-MM-DD`）。传 `None` 表示
    「还没有报价，只按钟点判断」。
    """
    if now.weekday() >= 5:
        return WEEKEND
    clock = now.time()
    if clock < _AUCTION_START:
        return PRE
    if clock < _AM_OPEN:
        return AUCTION

    in_am = _AM_OPEN <= clock <= _AM_CLOSE
    in_pm = _PM_OPEN <= clock <= _PM_CLOSE
    if in_am or in_pm:
        # 钟点在盘中，但行情还停在历史日期 → 今天不开市（节假日）
        if quote_date and quote_date != now.date().isoformat():
            return HOLIDAY
        return OPEN
    if _AM_CLOSE < clock < _PM_OPEN:
        if quote_date and quote_date != now.date().isoformat():
            return HOLIDAY
        return BREAK
    return CLOSED


def is_live(state: str) -> bool:
    """这个状态下值得轮询吗。午休与收盘都不需要。"""
    return state in (OPEN, AUCTION)


def label(state: str) -> str:
    return _LABELS.get(state, state)
