"""自然语言选股解析器（问财式）。

设计取向：**规则为主、模型兜底**。
- 规则层：同义词表（`core/fields.py`）+ 时序算子别名 + 数字单位换算。
  优点是完全确定、可解释、离线可用；每一条解析结果都能给出「原句片段 → 条件」的对照，
  用户看得见错在哪，也就能删掉某条条件重跑。
- 兜底层：`engine/llm.py`，仅在配置了 `AF_LLM_BASE_URL` / `AF_LLM_API_KEY` 时启用，
  只处理规则层认不出的片段，失败一律退回「未识别」，绝不让整次解析失败。

解析顺序（重要，顺序错了会互相抢）：
1. 口语定量短语（低估值、放量、超跌…）整段替换为等价条件
2. 数量与排序（前 10 只、按主力净流入排序）
3. 逐片段抽取指标 + 比较符 + 阈值（同一片段内可抽出多条）
4. 剩余的行业 / 板块 / 指数成分 / 排除词
"""

from __future__ import annotations

import re

from alphaforge_core.fields import (
    BOARD_VALUES,
    FIELD_MAP,
    INDUSTRY_ALIAS,
    industry_keywords,
    OP_WORDS,
)

from . import llm
from .conditions import normalize as normalize_condition
from .conditions import normalize_all

# ------------------------------------------------------------------ 文本归一化
_FULLWIDTH = {ord(c): ord(c) - 0xFEE0 for c in "０１２３４５６７８９％（）＞＜＝，；："}
_CN_DIGITS = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}


def _cn_number(text: str) -> int | None:
    if not text:
        return None
    if "十" not in text and "百" not in text:
        return _CN_DIGITS.get(text)
    total, section = 0, 0
    for ch in text:
        if ch in _CN_DIGITS:
            section = _CN_DIGITS[ch]
        elif ch == "十":
            total += (section or 1) * 10
            section = 0
        elif ch == "百":
            total += (section or 1) * 100
            section = 0
        else:
            return None
    return total + section


def normalize_text(text: str) -> str:
    out = (text or "").translate(_FULLWIDTH).strip()
    out = out.replace("％", "%").replace("＞", ">").replace("＜", "<").replace("＝", "=")
    # 中文数字只在带量词时替换，避免把「一线」「两只」之外的普通字误伤
    out = re.sub(r"[零一二三四五六七八九十百两]+(?=[只个支名天%倍亿万元 ])",
                 lambda m: str(_cn_number(m.group()) or m.group()), out)
    # 去掉所有空白。用户写「站上 20 日线」「ROE 大于 15」带空格，
    # 而词典里的别名是「站上20日线」，不去空格就是永远匹配不上。
    # 别名词典与行业词也用同一个函数归一，两侧才是同一把尺子。
    return re.sub(r"\s+", "", out)


_SPLIT_RE = re.compile(r"且|并且|而且|同时|以及|还有|,|，|、|;|；|\|")


def split_clauses(text: str) -> list[str]:
    return [c.strip() for c in _SPLIT_RE.split(text) if c and c.strip()]


# ------------------------------------------------------------------ 口语定量短语
# 这类词没有数字，但问财里最高频；映射到固定的等价条件，并在 chips 里还原成条件而不是原词
PHRASES: list[tuple[str, list[dict]]] = [
    ("低估值", [{"field": "pe", "op": "<=", "value": 20}]),
    ("估值低", [{"field": "pe", "op": "<=", "value": 20}]),
    ("高股息", [{"field": "dividend", "op": ">=", "value": 4}]),
    ("高分红", [{"field": "dividend", "op": ">=", "value": 4}]),
    ("放量", [{"field": "volratio", "op": ">=", "value": 1.5}]),
    ("缩量", [{"field": "volratio", "op": "<=", "value": 0.8}]),
    ("超跌", [{"field": "mom20", "op": "<=", "value": -15}]),
    ("低波动", [{"field": "vol20", "op": "<=", "value": 30}]),
    ("低波", [{"field": "vol20", "op": "<=", "value": 30}]),
    ("大盘股", [{"field": "float_mv", "op": ">=", "value": 500}]),
    ("中小盘", [{"field": "float_mv", "op": "<=", "value": 300}]),
    ("小盘股", [{"field": "float_mv", "op": "<=", "value": 200}]),
    ("白马股", [{"field": "roe", "op": ">=", "value": 15}, {"field": "float_mv", "op": ">=", "value": 500}]),
    ("强势股", [{"field": "mom20", "op": ">=", "value": 10}]),
    ("超跌反弹", [{"field": "mom20", "op": "<=", "value": -15}, {"field": "volratio", "op": ">=", "value": 1.2}]),
]

# 排除词：出现即转为不等条件（行业/板块/ST）
EXCLUDE_WORDS = ("排除", "剔除", "不要", "不含", "不考虑", "去掉", "回避")

_SORT_DESC_WORDS = ("从大到小", "由大到小", "从高到低", "由高到低", "降序", "最多", "最高", "最强", "最大", "涨幅")
_SORT_ASC_WORDS = ("从小到大", "由小到大", "从低到高", "由低到高", "升序", "最少", "最低", "最弱", "最小", "跌幅", "便宜")

_LIMIT_RE = re.compile(r"(?:前|取|选出|挑出|TOP|Top|top)\s*(\d+)\s*(?:只|个|支|名)?")
_LIMIT_TAIL_RE = re.compile(r"(\d+)\s*(?:只|个|支|名)(?:股票|标的|个股)?")
_SORT_RE = re.compile(r"(?:按|以|依|根据)([^,，。;；]{1,18}?)(?:排序|排名|排列|降序|升序)")
_SORT_LOOSE_RE = re.compile(r"(?:按|以|依|根据)([^,，。;；]{1,10}?)(?=取|选|挑|前\d|$)")
_SORT_TOP_RE = re.compile(r"(?:涨幅|跌幅|市值|成交额|资金|净流入|量比|股息|估值|roe|ROE|动量)[^,，。;；]{0,6}?(?:最多|最少|最高|最低|最强|最大|最小)")

# 时序算子：「连续 5 天上涨 / 连涨 3 天 / 已连涨 2 日」。
# 必须单独抽：先匹配别名的话，「连涨」会把词吃掉，剩下「3 天」没有比较符，
# 整句就被静默丢掉了。
_UP_DAYS_RE = re.compile(r"(?:连续|连涨|已连涨|连收)(\d+)(?:天|日)?(?:上涨|收阳|阳线|连涨|红)?")


def _strip_excluded(fragment: str) -> tuple[str, list[str]]:
    """把「排除X」「不要X」拆出来。返回（剩余文本, 被排除的名词）。"""
    excluded = []
    text = fragment
    for word in EXCLUDE_WORDS:
        idx = text.find(word)
        while idx >= 0:
            tail = text[idx + len(word):]
            cut = len(tail)
            for sep in _SPLIT_RE.findall(tail):
                pos = tail.find(sep)
                if 0 <= pos < cut:
                    cut = pos
            excluded.append(tail[:cut].strip())
            text = text[:idx] + tail[cut:]
            idx = text.find(word)
    return text, [e for e in excluded if e]


# ------------------------------------------------------------------ 指标匹配
def _alias_index() -> list[tuple[str, str]]:
    """(别名, 字段 key)，按别名长度降序 —— 否则「60日涨幅」会被「涨幅」抢先命中。

    别名必须和用户输入用同一套归一化（去空白、全角转半角、中文数字转阿拉伯），
    否则词典里写「站上20日线」、用户写「站上 20 日线」就永远对不上。
    """
    pairs: list[tuple[str, str]] = []
    for key, field in FIELD_MAP.items():
        for alias in field.aliases:
            pairs.append((normalize_text(alias).lower(), key))
    pairs.sort(key=lambda p: -len(p[0]))
    return pairs


ALIASES = _alias_index()

def _industry_keys() -> tuple[str, ...]:
    """行业词候选。运行期才生成：真实行业名要等数据层注入后才存在。

    与别名同样做一次归一化，保证与 `working`（已去空白的查询串）可比。
    """
    return tuple(normalize_text(w) for w in industry_keywords())


_INDUSTRY_CACHE: dict[str, tuple[str, ...]] = {}


def _industry_terms() -> tuple[str, ...]:
    from alphaforge_core.fields import industry_values

    key = str(len(industry_values()))
    if key not in _INDUSTRY_CACHE:
        _INDUSTRY_CACHE[key] = _industry_keys()
    return _INDUSTRY_CACHE[key]


def _find_alias(text: str, start: int = 0) -> tuple[int, int, str] | None:
    lowered = text.lower()
    best: tuple[int, int, str] | None = None
    for alias, key in ALIASES:
        pos = lowered.find(alias, start)
        if pos < 0:
            continue
        # 取最靠前的一条；同一起点取更长的（ALIASES 已按长度降序）
        if best is None or pos < best[0]:
            best = (pos, pos + len(alias), key)
    return best


_UNIT_TO_BASE = {"%": 1.0, "亿": 1.0, "万": 1e-4, "倍": 1.0, "元": 1.0, "天": 1.0, "": 1.0}
_VALUE_RE = re.compile(r"(-?\d+(?:\.\d+)?)\s*(%|亿|万|倍|元|天)?")


def _parse_operand(segment: str, field_key: str) -> dict | None:
    """从指标后面的片段里抽出比较符与阈值。"""
    field = FIELD_MAP[field_key]
    text = segment.strip()
    if not text:
        if field.kind == "bool":
            return {"field": field_key, "op": "is", "value": True}
        # 裸写的「连续上涨 / 连涨」没有阈值，但连涨的字面含义就是至少两天。
        # 放在这里而不是下面，是因为空片段在前面就返回了。
        if field_key == "up_days":
            return {"field": "up_days", "op": ">=", "value": 2.0}
        return None

    # 区间：10% 到 20% / 10-20亿
    rng = re.search(r"(-?\d+(?:\.\d+)?)\s*(%|亿|万|倍|元|天)?\s*(?:到|至|~|-)\s*(\d+(?:\.\d+)?)\s*(%|亿|万|倍|元|天)?", text)
    if rng and field.kind == "num" and field.ops and "between" in field.ops:
        unit = rng.group(2) or rng.group(4) or field.unit
        scale = _UNIT_TO_BASE.get(unit, 1.0)
        return {"field": field_key, "op": "between",
                "value": [float(rng.group(1)) * scale, float(rng.group(3)) * scale]}

    match = _VALUE_RE.search(text)
    if match:
        value = float(match.group(1))
        unit = match.group(2) or field.unit
        value *= _UNIT_TO_BASE.get(unit, 1.0)
        before = text[: match.start()]
        after = text[match.end():]

        op = None
        for word, symbol in OP_WORDS:
            if word in before:
                op = symbol
                break
        if op is None:
            if re.search(r"以上|及以上|往上|起", after):
                op = ">="
            elif re.search(r"以下|及以下|以内|之内", after):
                op = "<="
            elif re.search(r"左右|附近|上下", after):
                op = ">=" if field.direction > 0 else "<="
        if op is None:
            return None
        return {"field": field_key, "op": op, "value": value}

    # 没有数字：布尔字段直接为真；数值字段的「低/高」交给 PHRASES 处理
    if field.kind == "bool":
        negative = bool(re.search(r"不是|非|没有|排除|剔除|不要", text))
        return {"field": field_key, "op": "is", "value": not negative}

    if field.kind == "num":
        # 「为正 / 为负」：没有阈值但语义明确，等价于 > 0 / < 0。
        # 这类说法在问财里极高频（「主力净流入为正」），当未识别太可惜。
        if re.search(r"为正|是正|正数|正增长|净流入|净增加", text):
            return {"field": field_key, "op": ">", "value": 0.0}
        if re.search(r"为负|是负|负数|负增长|净流出|净减少", text):
            return {"field": field_key, "op": "<", "value": 0.0}
    return None


_FLIP_OPS = {">": "<", ">=": "<=", "<": ">", "<=": ">="}


def _is_negative_alias(alias: str) -> bool:
    """「20日跌幅」要取负号；但「涨跌幅」是中性词，不能翻。"""
    return "跌" in alias and "涨跌" not in alias


def _extract_conditions(clause: str) -> list[dict]:
    conditions: list[dict] = []
    remaining = clause
    guard = 0
    while guard < 8:
        guard += 1
        hit = _find_alias(remaining)
        if hit is None:
            break
        start, end, key = hit
        alias = remaining[start:end]
        nxt = _find_alias(remaining, end)
        seg_end = nxt[0] if nxt else len(remaining)
        operand = _parse_operand(remaining[end:seg_end], key)
        if operand and isinstance(operand.get("value"), (int, float)) and _is_negative_alias(alias):
            # 「跌幅超过 15%」= 区间跌幅 ≤ -15%，与字面比较符相反
            operand["value"] = -float(operand["value"])
            operand["op"] = _FLIP_OPS.get(operand["op"], operand["op"])
        if operand:
            conditions.append(operand)
        remaining = remaining[:start] + " " * (seg_end - start) + remaining[seg_end:]
    return conditions


def _extract_class(text: str) -> list[dict]:
    """行业 / 板块 / 指数成分。"""
    out: list[dict] = []
    for alias in _industry_terms():
        if alias in text:
            out.append({"field": "industry", "op": "=", "value": INDUSTRY_ALIAS[alias]})
            text = text.replace(alias, " ")
            break
    for board in BOARD_VALUES:
        if board in text:
            out.append({"field": "board", "op": "=", "value": board})
            break
    return out


def _detect_excluded(word: str) -> dict | None:
    text = word.strip()
    if not text:
        return None
    if re.search(r"st|ST|风险警示", text):
        return {"field": "is_st", "op": "is", "value": False}
    if text in BOARD_VALUES:
        return {"field": "board", "op": "!=", "value": text}
    if text in INDUSTRY_ALIAS:
        return {"field": "industry", "op": "!=", "value": INDUSTRY_ALIAS[text]}
    for alias in _industry_terms():
        if alias in text:
            return {"field": "industry", "op": "!=", "value": INDUSTRY_ALIAS[alias]}
    return None


def _sort_field(text: str) -> str | None:
    hit = _find_alias(text)
    while hit:
        start, end, key = hit
        field = FIELD_MAP[key]
        if field.kind == "num":
            return key
        hit = _find_alias(text, end)
    return None


# ------------------------------------------------------------------ 主入口
def parse(text: str) -> dict:
    raw = (text or "").strip()
    result = {
        "raw": raw,
        "filters": [],
        "weights": {},
        "sort": None,
        "top_n": None,
        "exclude_st": True,
        "max_per_industry": 2,
        "unresolved": [],
        "summary": "",
        "suggested_name": "",
        "engine": "rules",
    }
    if not raw:
        return result

    working = normalize_text(raw)
    filters: list[dict] = []
    unresolved: list[str] = []

    # 1) 排除了什么
    working, excluded_terms = _strip_excluded(working)
    for term in excluded_terms:
        cond = _detect_excluded(term)
        if cond:
            filters.append(cond)
        elif term:
            unresolved.append(f"排除「{term}」")

    # 2) 数量
    m = _LIMIT_RE.search(working) or _LIMIT_TAIL_RE.search(working)
    implied_sort = None
    if m:
        result["top_n"] = max(1, min(int(m.group(1)), 40))
        # 「主力净流入前 10」里的「前 10」已经隐含了按该指标降序，不必再写「按…排序」
        prefix = working[max(0, m.start() - 8): m.start()]
        key = _sort_field(prefix)
        if key and FIELD_MAP[key].kind == "num":
            implied_sort = key
        working = working[: m.start()] + " " * (m.end() - m.start()) + working[m.end():]

    # 3) 排序
    sort_match = _SORT_RE.search(working)
    sort_text = ""
    if sort_match:
        sort_text = sort_match.group(1)
    elif _SORT_TOP_RE.search(working):
        sort_text = _SORT_TOP_RE.search(working).group(0)
    if not sort_text and implied_sort:
        result["sort"] = {"by": implied_sort, "desc": True, "label": FIELD_MAP[implied_sort].label}
    if sort_text:
        key = _sort_field(sort_text)
        if key:
            desc = not any(w in sort_text for w in _SORT_ASC_WORDS)
            if any(w in sort_text for w in _SORT_DESC_WORDS):
                desc = True
            result["sort"] = {"by": key, "desc": desc, "label": FIELD_MAP[key].label}
            working = working.replace(sort_match.group(0) if sort_match else sort_text, " ")

    # 3.1) 「按涨幅取 10 只」这种省略了「排序/排名」的写法：
    #      按 X 后面直接接「取/选/前」，同样是在说排序。
    #      清词与设值要分开：隐含排序可能已经由第 2 步定好了，
    #      但「按涨幅」这段文本仍留在串里，不清掉就会被当未识别报出去。
    loose = _SORT_LOOSE_RE.search(working)
    if loose:
        key = _sort_field(loose.group(1))
        if key:
            if result["sort"] is None:
                desc = not any(w in loose.group(1) for w in _SORT_ASC_WORDS)
                if any(w in loose.group(1) for w in _SORT_DESC_WORDS):
                    desc = True
                result["sort"] = {"by": key, "desc": desc, "label": FIELD_MAP[key].label}
            working = working.replace(loose.group(0), " " * len(loose.group(0)))

    # 4) 逐片段抽条件
    for clause in split_clauses(working):
        consumed = False

        # 4.1 口语短语（低估值、放量…）
        for phrase, conds in PHRASES:
            if phrase in clause:
                filters.extend(conds)
                clause = clause.replace(phrase, " ")
                consumed = consumed or bool(conds)

        # 4.15 时序算子：连涨 N 天。要抢在别名抽取之前，否则「连涨」被吃掉、
        # 剩下的数字没有比较符，条件就静默丢了。
        ud = _UP_DAYS_RE.search(clause)
        if ud:
            filters.append({"field": "up_days", "op": ">=", "value": float(ud.group(1))})
            clause = clause[: ud.start()] + " " * (ud.end() - ud.start()) + clause[ud.end():]
            consumed = True

        # 4.2 行业 / 板块。必须排在指标之前：一句话里常常两者都有
        #     （「ROE 大于 20% 的白酒」），先抽指标会 `continue` 掉，行业就丢了。
        #     它不消费文本，先跑不会影响后面的指标抽取。
        classes = _extract_class(clause)
        if classes:
            filters.extend(classes)
            consumed = True

        # 4.3 指标 + 阈值
        found = _extract_conditions(clause)
        if found:
            filters.extend(found)
            consumed = True

        # 4.4 仍认不出 → 模型兜底（未配置时直接记未识别）
        if not consumed and clause.strip():
            fallback = llm.parse_fragment(clause.strip())
            if fallback is not None:
                filters.extend(fallback)
                result["engine"] = "rules+llm"
            else:
                unresolved.append(clause.strip())

    # 5) 归一化并生成标签；归一化失败的条目进「未识别」
    normalized: list[dict] = []
    seen: set[tuple] = set()
    for cond in filters:
        try:
            item = normalize_all([cond])[0]
        except ValueError as exc:
            unresolved.append(f"{FIELD_MAP.get(cond.get('field'), None).label if cond.get('field') in FIELD_MAP else cond.get('field')}：{exc}")
            continue
        marker = (item["field"], item["op"], str(item["value"]))
        if marker in seen:
            continue
        seen.add(marker)
        normalized.append(item)

    normalized = _merge_numeric(normalized)
    result["filters"] = normalized
    result["unresolved"] = [u for u in unresolved if _meaningful(u)]
    if any(f["field"] == "is_st" and f["value"] is False for f in normalized):
        result["exclude_st"] = True

    result["summary"] = _summary(normalized, result)
    result["suggested_name"] = _suggest_name(normalized, result)
    return result


_STOPWORDS = ("取", "选", "选出", "挑出", "的", "股票", "个股", "标的", "只", "排名", "排序",
              "一些", "那些", "这些", "请", "帮我", "找出", "筛选", "来看", "看看", "股", "有", "和", "与")


def _meaningful(text: str) -> bool:
    """只剩停用词的片段不算「未识别」，否则「取」这种残渣会被当成解析失败。"""
    rest = text
    for word in _STOPWORDS:
        rest = rest.replace(word, "")
    return bool(rest.strip(" 　,，。、;；"))


def _merge_numeric(items: list[dict]) -> list[dict]:
    """同一数值字段的多条约束合并成一条：下界取最大、上界取最小。

    「白马股」这类短语会自带 roe>=15，用户又写了 roe>15，两条并存会让 chips 看起来重复；
    合并后既去重，也把「10 到 20 倍且低于 15 倍」这类叠加条件收敛成一个区间。
    """
    groups: dict[str, list[dict]] = {}
    order: list[str] = []
    for item in items:
        key = item["field"]
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(item)

    merged: list[dict] = []
    for key in order:
        rows = groups[key]
        field = FIELD_MAP[key]
        if field.kind != "num" or len(rows) == 1:
            merged.extend(rows)
            continue

        lower: tuple[float, str] | None = None
        upper: tuple[float, str] | None = None
        for row in rows:
            op, value = row["op"], row["value"]
            if op == "between":
                candidates = [(value[0], ">="), (value[1], "<=")]
            elif op == "=":
                candidates = [(value, ">="), (value, "<=")]
            elif op in (">", ">="):
                candidates = [(value, op)]
            elif op in ("<", "<="):
                candidates = [(value, op)]
            else:
                candidates = []
            for bound, bound_op in candidates:
                if bound_op.startswith(">"):
                    if lower is None or bound > lower[0] or (bound == lower[0] and bound_op == ">"):
                        lower = (bound, bound_op)
                else:
                    if upper is None or bound < upper[0] or (bound == upper[0] and bound_op == "<"):
                        upper = (bound, bound_op)

        if lower and upper:
            if lower[0] == upper[0]:
                merged.append(normalize_condition({"field": key, "op": "=", "value": lower[0]}))
            else:
                merged.append(normalize_condition(
                    {"field": key, "op": "between", "value": [lower[0], upper[0]]}))
        elif lower:
            merged.append(normalize_condition({"field": key, "op": lower[1], "value": lower[0]}))
        elif upper:
            merged.append(normalize_condition({"field": key, "op": upper[1], "value": upper[0]}))
        else:
            merged.extend(rows)
    return merged


def _summary(filters: list[dict], result: dict) -> str:
    parts = [f["label"] for f in filters]
    text = "、".join(parts) if parts else "无条件（全池）"
    sort = result.get("sort")
    if sort:
        text += f"；按{sort['label']}{'降序' if sort['desc'] else '升序'}"
    if result.get("top_n"):
        text += f"，取前 {result['top_n']} 只"
    return text


def _suggest_name(filters: list[dict], result: dict) -> str:
    labels = [f["label"] for f in filters][:2]
    name = " 且 ".join(labels) if labels else "自定义条件"
    if result.get("sort"):
        name += f" · 按{result['sort']['label']}"
    return name[:24]


# ------------------------------------------------------------------ 示例问句
EXAMPLES: list[str] = [
    "主力净流入前10的创业板股票",
    "ROE大于15%且市盈率低于20的白马股",
    "涨停放量，按成交额排序取前5只",
    "站上20日线、创60日新高，排除ST",
    "高股息低估值的沪深300成分股",
    "连涨3天以上且量比大于1.5",
    "近20日跌幅超过15%的超跌股，按主力净流入排序",
]
