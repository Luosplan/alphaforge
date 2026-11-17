"""领域枚举与默认参数（对应 SRS 5.x / 8.x）。"""

from __future__ import annotations

from enum import Enum


class SignalType(str, Enum):
    ENTRY = "ENTRY"      # 建仓
    ADD = "ADD"          # 加仓
    TRIM = "TRIM"        # 减仓
    EXIT = "EXIT"        # 清仓
    WATCH = "WATCH"      # 观察


SIGNAL_LABEL = {
    SignalType.ENTRY: "建仓",
    SignalType.ADD: "加仓",
    SignalType.TRIM: "减仓",
    SignalType.EXIT: "清仓",
    SignalType.WATCH: "观察",
}


class PlanStatus(str, Enum):
    PENDING = "PENDING"          # 待触发
    TRIGGERED = "TRIGGERED"      # 已触发
    HOLDING = "HOLDING"          # 持有
    CLOSED = "CLOSED"            # 已了结
    EXPIRED = "EXPIRED"          # 已失效


PLAN_STATUS_LABEL = {
    PlanStatus.PENDING: "待触发",
    PlanStatus.TRIGGERED: "已触发",
    PlanStatus.HOLDING: "持有",
    PlanStatus.CLOSED: "已了结",
    PlanStatus.EXPIRED: "已失效",
}


class RiskLevel(str, Enum):
    INFO = "INFO"
    WARN = "WARN"
    BLOCK = "BLOCK"


class Freq(str, Enum):
    D1 = "1d"
    W1 = "1w"
    M1 = "1M"
    M60 = "60m"


# 风控默认参数（SRS M09）
RISK_LIMITS = {
    "max_single_weight": 0.15,     # 单票上限
    "max_industry_weight": 0.30,   # 单行业上限
    "max_gross_exposure": 0.90,    # 总仓位上限
    "risk_per_trade": 0.01,        # 单笔风险 R
    "min_amount": 5_000_000,       # 日均成交额门槛（元）
}

# 交易成本默认（SRS FR-BT-07）
COST_CONFIG = {
    "commission_rate": 0.00025,
    "commission_min": 5.0,
    "stamp_tax_rate": 0.0005,   # 仅卖出
    "transfer_fee_rate": 0.00001,
    "slippage_bps": 5,
}

ALLOWED_ADJUST = {"qfq", "hfq", "none"}


# ------------------------------------------------------------ 个人设置（账户）
# 一个账户三项设置：身份、成本口径、可买板块。成本项与 COST_CONFIG 同名，
# 因此 `{**COST_CONFIG, **profile}` 就是本次计算真正生效的口径。
BOARD_META: list[dict] = [
    {"key": "sh_main", "label": "沪市主板", "hint": "600 / 601 / 603 / 605"},
    {"key": "sz_main", "label": "深市主板", "hint": "000 / 001 / 002 / 003"},
    {"key": "gem", "label": "创业板", "hint": "300 / 301 · 需 2 年交易经验"},
    {"key": "star", "label": "科创板", "hint": "688 / 689 · 需 50 万资产 + 2 年经验"},
    {"key": "bse", "label": "北交所", "hint": "43 / 83 / 87 / 92 · 需单独开通"},
]

DEFAULT_PROFILE: dict = {
    "account_name": "A 股主账户",
    **COST_CONFIG,
    # 科创 / 北交所实盘需单独开通，演示账户默认按「已开通沪深全部、未开通北交所」给出
    "boards": {"sh_main": True, "sz_main": True, "gem": True, "star": True, "bse": False},
}

# 合法区间：路由层用 pydantic 拦截越界，这里是绕过 HTTP 直接调用时的兜底
PROFILE_RANGES: dict[str, tuple[float, float]] = {
    "commission_rate": (0.0, 0.003),
    "commission_min": (0.0, 50.0),
    "stamp_tax_rate": (0.0, 0.002),
    "transfer_fee_rate": (0.0, 0.001),
    "slippage_bps": (0.0, 200.0),
}


def normalize_profile(patch: dict | None, base: dict | None = None) -> dict:
    """合并并夹取个人设置。缺失项回落到默认值，未知键直接丢弃。"""
    current = {**DEFAULT_PROFILE, **(base or {})}
    current["boards"] = {**DEFAULT_PROFILE["boards"], **(base or {}).get("boards", {})}

    for key, value in (patch or {}).items():
        if key == "boards":
            if not isinstance(value, dict):
                continue
            for board, allowed in value.items():
                if board in current["boards"]:
                    current["boards"][board] = bool(allowed)
            continue
        if key == "account_name":
            name = str(value or "").strip()
            if name:
                current["account_name"] = name[:24]
            continue
        if key in PROFILE_RANGES:
            try:
                num = float(value)
            except (TypeError, ValueError):
                continue
            low, high = PROFILE_RANGES[key]
            current[key] = round(min(max(num, low), high), 8)

    return current


def board_allowed(code_key: str, profile: dict | None = None) -> bool:
    boards = normalize_profile(None, profile)["boards"]
    return bool(boards.get(code_key, True))


def trade_fee(amount: float, side: str, profile: dict | None = None) -> dict:
    """按当前成本口径算一笔成交的费用（不含滑点，滑点只影响回测成交价）。

    佣金双向收取且受最低佣金约束；印花税仅卖出；过户费双向。
    """
    cfg = normalize_profile(None, profile)
    amount = max(float(amount or 0.0), 0.0)
    commission = max(amount * cfg["commission_rate"], cfg["commission_min"]) if amount > 0 else 0.0
    stamp = amount * cfg["stamp_tax_rate"] if side.upper() == "SELL" else 0.0
    transfer = amount * cfg["transfer_fee_rate"]
    total = commission + stamp + transfer
    return {
        "amount": round(amount, 2),
        "commission": round(commission, 2),
        "stamp": round(stamp, 2),
        "transfer": round(transfer, 2),
        "total": round(total, 2),
        "rate": round(total / amount * 10000, 2) if amount else 0.0,   # 万分之
    }


def pos_color(value: float) -> str:
    """涨红跌绿（A 股习惯）。"""
    if value > 0:
        return "up"
    if value < 0:
        return "down"
    return "flat"
