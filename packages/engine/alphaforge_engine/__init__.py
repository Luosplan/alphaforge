"""AlphaForge 计算引擎层。

只依赖 alphaforge_core，不依赖 api / web（SRS 4.5.4 架构硬约束）。
所有模块为纯函数或纯内存计算，可脱离 Web 进程被批处理任务直接调用。
"""

from .market import MarketEngine, get_market
from .portfolio import build_live_portfolio, check_plan, trade_stats
from .nlquery import EXAMPLES, parse
from .panorama import index_book, market_temperature
from .selector import execute, normalize_spec, strategy_list
from .signal import active_plans, build_plan

__all__ = [
    "MarketEngine",
    "get_market",
    "build_live_portfolio",
    "check_plan",
    "trade_stats",
    "execute",
    "normalize_spec",
    "strategy_list",
    "parse",
    "EXAMPLES",
    "index_book",
    "market_temperature",
    "active_plans",
    "build_plan",
]
