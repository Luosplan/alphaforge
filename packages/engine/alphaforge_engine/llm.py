"""模型兜底：把规则层认不出的片段交给大模型。

默认**不启用**。只有同时配置了 `AF_LLM_BASE_URL` 与 `AF_LLM_API_KEY`
（OpenAI 兼容的 `/chat/completions`）时才会被调用，且只处理规则层解析失败的片段；
不做整句重写，避免模型把已经解析对的条件搞乱。

没有配置时 `parse_fragment` 直接返回 None，解析器把片段记为「未识别」，
前端会如实展示——宁可说「这句我没懂」，也不要猜一个错条件去选股。
"""

from __future__ import annotations

import json
import os
import urllib.request

from alphaforge_core.fields import FIELDS

TIMEOUT = 8


def _config() -> tuple[str, str, str]:
    base = os.environ.get("AF_LLM_BASE_URL", "").rstrip("/")
    key = os.environ.get("AF_LLM_API_KEY", "")
    model = os.environ.get("AF_LLM_MODEL", "gpt-4o-mini")
    return base, key, model


def enabled() -> bool:
    base, key, _ = _config()
    return bool(base and key)


def _field_brief() -> str:
    rows = []
    for f in FIELDS:
        if f.kind == "cat":
            rows.append(f"{f.key}({f.label},取值如 {f.aliases[0]})")
        elif f.kind == "bool":
            rows.append(f"{f.key}({f.label},布尔)")
        else:
            rows.append(f"{f.key}({f.label},单位{f.unit or '无'})")
    return "；".join(rows)


SYSTEM_PROMPT = (
    "你是 A 股选股条件解析器。把用户片段转成 JSON 数组，"
    '元素形如 {"field":"roe","op":">=","value":15}。'
    "op 只能取 > >= < <= = between；布尔字段用 op=is、value=true/false；"
    "between 的 value 为 [下限, 上限]。只输出 JSON，不要解释。"
)


def parse_fragment(text: str) -> list[dict] | None:
    """返回条件数组；未启用、请求失败或返回不合法时返回 None。"""
    if not enabled() or not text:
        return None
    base, key, model = _config()
    payload = {
        "model": model,
        "temperature": 0,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"可用字段：{_field_brief()}\n片段：{text}"},
        ],
    }
    request = urllib.request.Request(
        f"{base}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            body = json.loads(response.read().decode("utf-8"))
        content = body["choices"][0]["message"]["content"]
        content = content.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        parsed = json.loads(content)
        if isinstance(parsed, dict):
            parsed = [parsed]
        if not isinstance(parsed, list):
            return None
        return [item for item in parsed if isinstance(item, dict) and item.get("field")]
    except Exception:
        # 兜底失败不影响主流程：调用方会把片段记为「未识别」
        return None
