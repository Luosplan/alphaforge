"""购买权限的选池门槛：选股、计划卡、模拟盘、回测共用同一份判断。

背景：`个人设置 → 购买权限` 原本只在**下单**那一刻生效（`store.board_gate`、
`portfolio.check_plan`）。于是选股页和计划卡里照样出现科创板、北交所的票，
用户看到的是「系统在推荐我根本买不了的东西」，还已经挂成了委托。

这里把同一份 `boards` 字典提前到「建候选池」这一步。语义只有一个：
**能不能买，决定能不能进候选池**——不是选出来再标灰，而是压根不参与打分，
否则 Top-N 的名额会被不可买的票占掉，剩下的才是可买的。

`None` 表示调用方没提供权限口径（如纯研究、内部工具），此时不过滤。
"""

from __future__ import annotations

from alphaforge_core.constants import BOARD_META
from alphaforge_core.universe import board_key

_BOARD_LABEL = {b["key"]: b["label"] for b in BOARD_META}


def board_allow(boards: dict | None) -> dict[str, bool] | None:
    """规范化权限字典；`None` / 非字典一律当「不设限」。"""
    if not isinstance(boards, dict):
        return None
    return {k: bool(v) for k, v in boards.items()}


def buyable(code: str, allow: dict[str, bool] | None) -> bool:
    """这个代码在不在已开通的板块里。`allow` 已经过 `board_allow` 规范化。"""
    if allow is None:
        return True
    return bool(allow.get(board_key(code), True))


def allowed_labels(allow: dict[str, bool] | None) -> list[str]:
    """已开通板块的中文名，按 BOARD_META 的顺序——给接口和页面直接显示。"""
    if allow is None:
        return []
    return [b["label"] for b in BOARD_META if allow.get(b["key"])]


def blocked_label(code: str) -> str:
    """被拦下的标的该提示哪个板块名。"""
    return _BOARD_LABEL.get(board_key(code), board_key(code))
