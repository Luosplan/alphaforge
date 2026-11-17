"""选股接口（M02）：内置策略 / 自定义策略 / 自然语言问股。

三个入口共用 `selector.execute()`，只在「spec 从哪来」上有差别：
- 内置：id 命中 `STRATEGIES`
- 自定义：id 命中 store.json 里的 strategies
- AI：`nlquery.parse()` 把句子变成 spec

`/parse` 与 `/query` 分开是为了让前端先展示解析结果、用户确认后再执行——
问财最容易招骂的地方就是「自作主张跑出一个奇怪结果」，把中间状态摊开比猜得准更重要。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from alphaforge_core.fields import field_meta
from alphaforge_core.schemas import QueryRequest, SelectRequest, StrategyPayload
from alphaforge_engine.nlquery import EXAMPLES, parse
from alphaforge_engine.selector import STRATEGY_MAP, execute, strategy_list

from ..deps import market, ok, store

router = APIRouter(prefix="/select", tags=["select"])


@router.get("/strategies")
def strategies(request: Request):
    return ok(strategy_list(store().strategies()))


@router.get("/fields")
def fields(request: Request):
    """条件构造器用的字段元数据：分组、单位、可用比较符、同义词。"""
    return ok(field_meta())


@router.get("/examples")
def examples(request: Request):
    return ok(EXAMPLES)


@router.post("/strategies")
def create_strategy(request: Request, payload: StrategyPayload):
    data = payload.model_dump()
    data["sort"] = payload.sort.model_dump() if payload.sort else None
    return ok(store().save_strategy(data))


@router.put("/strategies/{strategy_id}")
def update_strategy(request: Request, strategy_id: str, payload: StrategyPayload):
    if strategy_id in STRATEGY_MAP:
        raise HTTPException(status_code=400, detail="内置策略不可修改，请先复制为自定义策略")
    data = payload.model_dump()
    data["sort"] = payload.sort.model_dump() if payload.sort else None
    try:
        return ok(store().save_strategy(data, strategy_id=strategy_id))
    except KeyError:
        raise HTTPException(status_code=404, detail="策略不存在")


@router.delete("/strategies/{strategy_id}")
def delete_strategy(request: Request, strategy_id: str):
    try:
        store().delete_strategy(strategy_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="策略不存在")
    return ok({"deleted": strategy_id})


@router.post("/parse")
def parse_query(request: Request, payload: QueryRequest):
    """只解析不执行：前端先展示条件 chips，用户确认后再跑。"""
    parsed = parse(payload.text)
    if payload.top_n:
        parsed["top_n"] = payload.top_n
    return ok(parsed)


@router.post("/run")
def run(request: Request, payload: SelectRequest):
    if payload.spec:
        spec = {**payload.spec, "id": payload.spec.get("id", "inline")}
        return ok(execute(market(), spec, top_n=payload.top_n))

    strategy_id = payload.strategy
    if strategy_id in STRATEGY_MAP:
        spec = {**STRATEGY_MAP[strategy_id], "id": strategy_id}
        return ok(execute(market(), spec, top_n=payload.top_n))

    for item in store().strategies():
        if item["id"] == strategy_id:
            return ok(execute(market(), item, top_n=payload.top_n))
    raise HTTPException(status_code=404, detail="策略不存在")


@router.post("/query")
def query(request: Request, payload: QueryRequest):
    """问财式：一句话 → 解析 → 立即执行，返回条件与结果一起。"""
    parsed = parse(payload.text)
    spec = {
        "id": "ai",
        "name": parsed["suggested_name"] or "AI 问股",
        "weights": parsed["weights"],
        "filters": parsed["filters"],
        "sort": parsed["sort"],
        "top_n": payload.top_n or parsed["top_n"] or 10,
        "max_per_industry": parsed["max_per_industry"],
        "exclude_st": parsed["exclude_st"],
    }
    result = execute(market(), spec, top_n=payload.top_n or parsed["top_n"])
    result["parsed"] = parsed
    return ok(result)
