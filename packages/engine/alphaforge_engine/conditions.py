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

# 条件组：一条条件里再套一组条件，命中任意 / 全部即通过。
# 弱转强的「弱」天然是或的关系——烂板是一种弱，放量滞涨是另一种弱，
# 它们互不包含，用一层平铺的 AND 表达不出来。没有这两个节点，策略只能
# 二选一，等于把一半的弱转强机会丢掉；而「放量滞涨」自己又是两条 AND。
ANY_FIELD = "any"
ALL_FIELD = "all"
_GROUP_JOIN = {ANY_FIELD: " 或 ", ALL_FIELD: " 且 "}


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
    if key in _GROUP_JOIN:
        subs = [normalize(c) for c in (condition.get("value") or [])]
        if not subs:
            raise ConditionError(f"「{_GROUP_JOIN[key].strip()}」组里至少要有一条子条件")
        # 嵌套组加括号：`烂板 或 量比≥2 且 涨幅≤6` 读起来会被当成三步并列，
        # 而「且」本来只约束后两条
        parts = [f"（{s['label']}）" if s["field"] in _GROUP_JOIN else s["label"] for s in subs]
        return {"field": key, "op": key, "value": subs, "label": _GROUP_JOIN[key].join(parts)}
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


def leaves(conditions: list[dict]) -> list[dict]:
    """把条件树摊平成叶子条件，供「命中条件」这类要回填具体字段值的地方用。

    组节点自己没有字段可取——页面上要展示的是「烂板 = 是」而不是
    「any = [...]」，所以展示层一律用叶子。
    """
    out: list[dict] = []
    for cond in conditions:
        if cond.get("field") in _GROUP_JOIN:
            out.extend(leaves(cond["value"]))
        else:
            out.append(cond)
    return out


def evaluate(conditions: list[dict], value_of) -> bool:
    """一组条件按 AND 求值；组节点按自己的语义递归。

    `value_of(field)` 由调用方决定怎么取值：选股走因子宽表的列，
    回测走面板里现算的当日值——两个入口取数方式不同，规则必须只有一份。
    """
    for cond in conditions:
        field = cond.get("field")
        if field in _GROUP_JOIN:
            hit = [evaluate([c], value_of) for c in cond["value"]]
            if not (any(hit) if field == ANY_FIELD else all(hit)):
                return False
        elif not matches(cond, value_of(field)):
            return False
    return True
