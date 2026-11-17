"""标的元数据的**类型与纯函数**。

这里刻意不再放数据。原来那份 40 只的 `SYMBOLS` 是演示种子，价格与财务字段
都是合成的；换成真实数据后，标的表由 `alphaforge_data` 从新浪/东财落库，
`core` 只保留「不依赖数据也能算」的规则：代码 → 交易所、板块、涨跌停幅度。

这样分层才立得住：`core` 不会为了拿一只票的名称反向依赖数据库。
"""

from __future__ import annotations

from typing import TypedDict


class SymbolSeed(TypedDict, total=False):
    code: str
    name: str
    exchange: str
    board: str
    industry: str
    is_st: bool
    listed: str | None
    total_mv: float      # 总市值（亿元）
    float_mv: float      # 流通市值（亿元）
    pe: float
    pb: float
    roe: float
    roe_date: str | None
    dividend: float      # 股息率 %


# 购买权限按板块授权：沪深主板 / 创业板 / 科创板 / 北交所
BOARD_VALUES: tuple[str, ...] = ("主板", "创业板", "科创板", "北交所")


def exchange_of(code: str) -> str:
    """代码 → 交易所。无后缀时按代码段推断。"""
    num, _, ex = code.partition(".")
    ex = ex.upper()
    if ex in ("SH", "SZ", "BJ"):
        return ex
    if num.startswith(("688", "689", "60")):
        return "SH"
    if num.startswith(("4", "8", "920")):
        return "BJ"
    return "SZ"


def board_of(code: str) -> str:
    """代码 → 板块。代码段是唯一可信依据，不必等元数据落库。"""
    num = code.partition(".")[0]
    if num.startswith(("688", "689")):
        return "科创板"
    if num.startswith(("300", "301")):
        return "创业板"
    if num.startswith(("4", "8")) or num.startswith("920"):
        return "北交所"
    return "主板"


def board_key(code: str) -> str:
    """板块键：购买权限与费用口径都按它分档。"""
    return {
        "创业板": "gem",
        "科创板": "star",
        "北交所": "bse",
    }.get(board_of(code), "sh_main" if exchange_of(code) == "SH" else "sz_main")


def pc(code: str, is_st: bool = False) -> float:
    """涨跌停板幅度。ST 一律 5%，双创 20%，北交所 30%，其余 10%。"""
    if is_st:
        return 0.05
    return {
        "创业板": 0.20,
        "科创板": 0.20,
        "北交所": 0.30,
    }.get(board_of(code), 0.10)
