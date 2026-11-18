"""Pydantic DTO —— 同时作为 API 契约（SRS 4.5.3 core/schemas）。"""

from __future__ import annotations

from typing import Any, Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class Envelope(BaseModel, Generic[T]):
    code: int = 0
    msg: str = "ok"
    data: T | None = None
    traceId: str | None = None


class Bar(BaseModel):
    date: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    amount: float


class Quote(BaseModel):
    code: str
    name: str
    price: float
    prev_close: float
    change: float
    change_pct: float
    open: float
    high: float
    low: float
    volume: float
    amount: float
    turnover_rate: float
    industry: str


class Zone(BaseModel):
    low: float
    high: float
    confidence: float | None = None
    method: str | None = None
    condition: str | None = None


class Tranche(BaseModel):
    price: float
    pct: float
    shares: int


class PlanCard(BaseModel):
    plan_id: str
    code: str
    name: str
    strategy_id: str
    strategy_name: str
    signal_type: str
    signal_label: str
    status: str
    status_label: str
    as_of: str
    executable_from: str
    ref_price: float
    zones: dict[str, Any]
    sizing: dict[str, Any]
    rr: float
    confidence: float
    invalidation: list[str]
    time_stop_days: int
    reason: dict[str, list[str]]


class SortSpec(BaseModel):
    by: str = Field(min_length=1, max_length=32, description="字段键，如 main_net")
    desc: bool = True


class SelectRequest(BaseModel):
    strategy: str = "multifactor"
    top_n: int = Field(default=10, ge=1, le=40)
    universe: str = "all"
    exclude_st: bool = True
    # 内联 spec：编辑器里未保存的策略、AI 解析结果都从这里进来
    spec: dict | None = None


class QueryRequest(BaseModel):
    text: str = Field(min_length=1, max_length=120)
    top_n: int | None = Field(default=None, ge=1, le=40)


class StrategyPayload(BaseModel):
    """自定义策略的可编辑字段。id / 时间戳由服务端维护，不接受客户端指定。"""

    name: str = Field(min_length=1, max_length=24)
    desc: str = Field(default="", max_length=120)
    weights: dict[str, float] = Field(default_factory=dict)
    filters: list[dict] = Field(default_factory=list)
    sort: SortSpec | None = None
    top_n: int = Field(default=10, ge=1, le=40)
    max_per_industry: int = Field(default=2, ge=1, le=20)
    exclude_st: bool = True


class WatchAddRequest(BaseModel):
    code: str
    group_id: str = "g1"


class WatchGroupRequest(BaseModel):
    name: str


class RiskCheckRequest(BaseModel):
    code: str
    shares: int
    price: float


class TradeRequest(BaseModel):
    code: str
    side: str = Field(pattern="^(BUY|SELL)$")
    price: float
    shares: int
    trade_date: str | None = None
    note: str | None = None


# ------------------------------------------------------------ 策略实验室
class LabBacktestRequest(BaseModel):
    """单条策略回测。`spec` 可内联一条还没保存的策略（编辑器里试参数用）。"""

    strategy: str = "multifactor"
    spec: dict | None = None
    days: int = Field(default=250, ge=60, le=520)
    capital: float = Field(default=1_000_000, gt=0, le=100_000_000)
    # 覆盖 ATR 档位 / 调仓周期等结构参数，留空用 `backtest.DEFAULTS`
    params: dict | None = None


class LabEvolveRequest(BaseModel):
    strategy: str = "multifactor"
    spec: dict | None = None
    population: int = Field(default=24, ge=6, le=64)
    generations: int = Field(default=6, ge=1, le=20)
    days: int = Field(default=250, ge=60, le=520)
    capital: float = Field(default=1_000_000, gt=0, le=100_000_000)
    max_codes: int = Field(default=600, ge=100, le=1200)
    seed: int = Field(default=20261118, ge=0, le=2**31 - 1)
    # 搜完直接采纳到模拟盘账户（前端默认给用户自己点，不默认自动覆盖）
    apply: bool = False


class LabPaperRequest(BaseModel):
    """推进模拟盘。`strategy` 留空 = 全部策略都推一格。"""

    strategy: str | None = None
    force: bool = False
    capital: float | None = Field(default=None, gt=0, le=100_000_000)
    reset: bool = False
