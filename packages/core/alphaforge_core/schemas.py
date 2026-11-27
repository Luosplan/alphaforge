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
    # 不传就听策略自己声明的 `top_n`。写死 10 的时候，内置策略里声明的
    # `top_n=3` 会被这个默认值顶掉——「策略说最多 3 只」在接口这一层就失效了，
    # 页面上看到 10 只、计划卡里却是 3 只，两处对不上。
    top_n: int | None = Field(default=None, ge=1, le=40)
    universe: str = "all"
    exclude_st: bool = True
    # 只看已开通购买的板块（个人设置 → 购买权限）。默认开——选股页是拿来选
    # 能买的票的；关掉是为了纯研究（比如看科创板今天有没有机会）。
    only_allowed: bool = True
    # 内联 spec：编辑器里未保存的策略、AI 解析结果都从这里进来
    spec: dict | None = None


class QueryRequest(BaseModel):
    text: str = Field(min_length=1, max_length=120)
    top_n: int | None = Field(default=None, ge=1, le=40)
    only_allowed: bool = True


class StrategyPayload(BaseModel):
    """自定义策略的可编辑字段。id / 时间戳由服务端维护，不接受客户端指定。"""

    name: str = Field(min_length=1, max_length=24)
    desc: str = Field(default="", max_length=120)
    weights: dict[str, float] = Field(default_factory=dict)
    filters: list[dict] = Field(default_factory=list)
    sort: SortSpec | None = None
    top_n: int = Field(default=3, ge=1, le=40)
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


# ------------------------------------------------------------ 策略实验室
class LabBacktestRequest(BaseModel):
    """单条策略回测。`spec` 可内联一条还没保存的策略（编辑器里试参数用）。"""

    strategy: str = "multifactor"
    spec: dict | None = None
    # 默认 3 年。250 天只能覆盖单一市场状态，牛熊一轮都走不完，
    # 在上头挑参数等于对着一段行情过拟合。
    days: int = Field(default=750, ge=60, le=1260)
    capital: float = Field(default=1_000_000, gt=0, le=100_000_000)
    # 覆盖 ATR 档位 / 调仓周期等结构参数，留空用 `backtest.DEFAULTS`
    params: dict | None = None


class LabEvolveRequest(BaseModel):
    strategy: str = "multifactor"
    spec: dict | None = None
    population: int = Field(default=24, ge=6, le=64)
    generations: int = Field(default=6, ge=1, le=20)
    # 进化是「每天跑一轮」的东西，不是「攒够两年数据再跑一次」。
    # 默认窗口 60 天（约三个月）：持有 1~6 天的超短策略在这个尺度上
    # 才谈得上「当前市场风格」，再长就是拿几个季度前的行情调今天的参数。
    # 上限仍放开到 5 年，做长周期对照时手动调大。
    days: int = Field(default=60, ge=20, le=1260)
    capital: float = Field(default=1_000_000, gt=0, le=100_000_000)
    max_codes: int = Field(default=600, ge=100, le=1500)
    seed: int = Field(default=20261118, ge=0, le=2**31 - 1)
    # 搜完直接采纳到模拟盘账户（前端默认给用户自己点，不默认自动覆盖）
    apply: bool = False


class LabOrderRequest(BaseModel):
    """实验室左栏的战法顺序。整份覆盖，不做增量——拖一次就把当前排序列出来。"""

    order: list[str] = Field(default_factory=list)


class LabPaperRequest(BaseModel):
    """推进模拟盘。`strategy` 留空 = 全部策略都推一格。"""

    strategy: str | None = None
    force: bool = False
    capital: float | None = Field(default=None, gt=0, le=100_000_000)
    reset: bool = False
