"""条件（filter）归一化与求值。

一条条件就是 `{field, op, value}`，例如：
    {"field": "roe", "op": ">=", "value": 15}
    {"field": "limit_up", "op": "is", "value": True}
    {"field": "pe", "op": "between", "value": [10, 20]}
    {"field": "industry", "op": "!=", "value": "银行"}

归一化的意义：自然语言解析、前端条件构造器、持久化的策略**共用同一套结构**，
标签文案也在这里生成——否则三个入口各写一份中文，迟早对不上。
"""

from __future__ import annotations

from alphaforge_core.fields import FIELD_MAP, OP_LABEL, Field

BOOL_YES = ("是", "true", "1", "y", "yes", "有", "存在")


class ConditionError(ValueError):
    pass


def _fmt_number(value: float) -> str:
    if value is None:
        return "-"
    if abs(value - round(value)) < 1e-9:
        return str(int(round(value)))
    return f"{value:.2f}".rstrip("0").rstrip(".")


def _label(field: Field, op: str, value) -> str:
    if field.kind == "bool":
        flag = bool(value)
        if field.unit == "" and flag:
            return field.label
        return field.label if flag else f"非 {field.label}"
    if field.kind == "cat":
        return f"{field.label} {OP_LABEL.get(op, op)} {value}"
    unit = field.unit
    if op == "between":
        low, high = value
        return f"{field.label} {_fmt_number(low)} ~ {_fmt_number(high)}{unit}"
    return f"{field.label} {OP_LABEL.get(op, op)} {_fmt_number(value)}{unit}"


def normalize(condition: dict) -> dict:
    """校验并规整一条条件，返回带 label 的副本。非法输入抛 ConditionError。"""
    key = str(condition.get("field", "")).strip()
    field = FIELD_MAP.get(key)
    if field is None:
        raise ConditionError(f"未知字段：{key or '（空）'}")

    op = str(condition.get("op", ">=")).strip()
    if field.kind == "bool":
        raw = condition.get("value", True)
        if isinstance(raw, str):
            value = raw.strip().lower() in BOOL_YES
        else:
            value = bool(raw)
        op = "is"
        return {"field": key, "op": op, "value": value, "label": _label(field, op, value)}

    if op not in field.ops:
        raise ConditionError(f"{field.label} 不支持比较符 {op}")

    raw = condition.get("value")

    if field.kind == "cat":
        text = str(raw or "").strip()
        if not text:
            raise ConditionError(f"{field.label} 缺少取值")
        if key == "industry":
            from alphaforge_core.fields import resolve_industry

            # 口语落地到真实行业名，「白酒」→「白酒Ⅱ」；解析不出来就原样保留，
            # 让它筛不出东西也比偷偷换成另一个行业强
            text = resolve_industry(text) or text
        return {"field": key, "op": op, "value": text, "label": _label(field, op, text)}

    try:
        if op == "between":
            low, high = raw[0], raw[1]
        else:
            low, high = raw, None
        first = float(low)
        second = float(high) if op == "between" else None
    except (TypeError, ValueError, IndexError):
        raise ConditionError(f"{field.label} 的阈值必须是数字")

    if op == "between":
        low_v, high_v = sorted((first, second))
        if low_v == high_v:
            op = ">="
            return {"field": key, "op": op, "value": low_v, "label": _label(field, op, low_v)}
        return {"field": key, "op": op, "value": [low_v, high_v],
                "label": _label(field, op, [low_v, high_v])}

    return {"field": key, "op": op, "value": round(first, 4), "label": _label(field, op, first)}


def normalize_all(conditions: list[dict] | None) -> list[dict]:
    out = []
    for cond in conditions or []:
        out.append(normalize(cond))
    return out


def matches(condition: dict, cell) -> bool:
    """按已归一化的条件判断单个值是否通过。"""
    op = condition["op"]
    target = condition["value"]
    if op == "is":
        return bool(cell) == bool(target)
    if op == "=":
        return cell == target
    if op == "!=":
        return cell != target
    if cell is None:
        return False
    if op == "between":
        return target[0] <= cell <= target[1]
    if op == ">":
        return cell > target
    if op == ">=":
        return cell >= target
    if op == "<":
        return cell < target
    if op == "<=":
        return cell <= target
    return False
