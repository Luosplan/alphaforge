"""列存因子板：从同步时算好的因子宽表按列取用。

旧版是「每次请求现场把整个池子算一遍」——40 只票没问题，5000+ 只就是
几十秒的延迟。现在 34 列因子在 `alphaforge_data` 的 `factor` 表里已经是
算好的结果，这里只负责**按池子顺序对齐**成列数组，选股与条件筛选共用同一份。

对齐这一步不能省：因子表按 code 排序，而池子可能是「创业板」这样的子集，
下标必须与池子一一对应，否则 `board.column(k)[i]` 会张冠李戴。
"""

from __future__ import annotations

from alphaforge_core.fields import FIELD_MAP

from .market import MarketEngine


class FactorBoard:
    def __init__(self, engine: MarketEngine, pool: list[dict] | None = None,
                 trade_date: str | None = None):
        self.engine = engine
        self.pool = pool if pool is not None else engine.universe()
        self.codes = [s["code"] for s in self.pool]
        date, cols = engine.repo.factor_columns(trade_date)
        self.trade_date = date
        self.columns = _align(cols, self.codes)

    def column(self, key: str) -> list:
        return self.columns.get(key, [])

    @property
    def ready(self) -> bool:
        return bool(self.columns)


def _align(cols: dict[str, list], codes: list[str]) -> dict[str, list]:
    """把按 code 排好序的列，重排成池子顺序。池外代码补 None。"""
    if not cols or "code" not in cols:
        return {key: [] for key in FIELD_MAP}
    order = {code: i for i, code in enumerate(cols["code"])}
    picked = [order.get(c) for c in codes]
    out: dict[str, list] = {}
    for key, values in cols.items():
        if key in ("trade_date",):
            continue
        out[key] = [values[i] if i is not None else None for i in picked]
    # 因子表里没有的字段（理论上不会有）补空列，避免下游 KeyError
    for key in FIELD_MAP:
        out.setdefault(key, [None] * len(codes))
    return out


_BOARD_CACHE: dict[tuple, FactorBoard] = {}


def get_board(engine: MarketEngine, pool: list[dict] | None = None) -> FactorBoard:
    pool = pool if pool is not None else engine.universe()
    # 缓存键必须能唯一标识池子。早先只用了「长度 + 首尾代码」，两个数量相同、
    # 首尾相同的不同子集（比如按行业切出来的两块）会互相顶掉，导致选股结果
    # 出现别家的股票。直接用代码元组做键，5578 个指针而已。
    key = (engine.repo.trade_date(), tuple(s["code"] for s in pool))
    board = _BOARD_CACHE.get(key)
    if board is None:
        board = FactorBoard(engine, pool)
        if len(_BOARD_CACHE) > 4:
            _BOARD_CACHE.clear()
        _BOARD_CACHE[key] = board
    return board


def clear_board_cache() -> None:
    _BOARD_CACHE.clear()
