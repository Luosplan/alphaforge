"""模型兜底：把规则层认不出的片段交给大模型。

默认**不启用**。只有同时配置了 `AF_LLM_BASE_URL` 与 `AF_LLM_API_KEY`
（OpenAI 兼容的 `/chat/completions`）时才会被调用，且只处理规则层解析失败的片段；
不做整句重写，避免模型把已经解析对的条件搞乱。

没有配置时 `parse_fragment` 直接返回 None，解析器把片段记为「未识别」，
前端会如实展示——宁可说「这句我没懂」，也不要猜一个错条件去选股。
"""

from __future__ import annotations

import datetime as dt
import json
import os
import urllib.error
import urllib.request

from alphaforge_core.fields import FIELDS

TIMEOUT = 8

# 最近一次调用的结果。为什么不只返回 None：`chat()` 失败就降级到规则版，
# 界面上「规则判断」和「配了 key 但 key 是错的」长得一模一样——用户填完
# key 发现结论没变，只会以为功能没生效，实际是 401 被吞掉了。
# 把失败原因留下来，「接入模型」这件事才是可验证的。
_LAST: dict = {"error": None, "error_at": None, "ok_at": None, "reply_at": None}


def _config() -> tuple[str, str, str]:
    base = os.environ.get("AF_LLM_BASE_URL", "").rstrip("/")
    key = os.environ.get("AF_LLM_API_KEY", "")
    model = os.environ.get("AF_LLM_MODEL", "gpt-4o-mini")
    return base, key, model


def _record_ok() -> None:
    _LAST["error"] = None
    _LAST["error_at"] = None
    _LAST["ok_at"] = dt.datetime.now().isoformat(timespec="seconds")


def _record_fail(message: str) -> None:
    _LAST["error"] = message[:400]
    _LAST["error_at"] = dt.datetime.now().isoformat(timespec="seconds")


def status() -> dict:
    """给界面看的接入状态。key 只回末四位，够核对是哪把、又不会泄漏。"""
    base, key, model = _config()
    return {
        "enabled": bool(base and key),
        "base_url": base,
        "model": model,
        "key_tail": key[-4:] if key else "",
        "last_error": _LAST["error"],
        "last_error_at": _LAST["error_at"],
        "last_ok_at": _LAST["ok_at"],
    }


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


# ---------------------------------------------------------------- 通用对话
def chat(messages: list[dict], *, temperature: float = 0.3,
         timeout: int = 40, json_mode: bool = False) -> str | None:
    """OpenAI 兼容的通用对话。未配置或失败返回 None。

    与 `parse_fragment` 分开：那个是选股条件解析，一次几十 token、必须确定；
    这个是盘中分析，要长上下文、允许一点温度，失败时的处理也不同——
    解析失败记「未识别」，分析失败就静默跳过这一轮，不打扰盘中流程。
    """
    if not enabled() or not messages:
        return None
    base, key, model = _config()
    payload: dict = {"model": model, "temperature": temperature,
                     "messages": messages}
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    request = urllib.request.Request(
        f"{base}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = json.loads(response.read().decode("utf-8"))
        content = body["choices"][0]["message"]["content"] or ""
        _record_ok()
        return content.strip()
    except urllib.error.HTTPError as exc:
        # 401/403 是 key 或权限，404 多是 base_url 多了或少了 /v1，
        # 400 常是模型名不对——这三种排查方向完全不同，必须把响应体带出来。
        try:
            detail = exc.read().decode("utf-8", "ignore")[:240]
        except Exception:                                # noqa: BLE001
            detail = ""
        _record_fail(f"HTTP {exc.code} {detail}".strip())
        return None
    except Exception as exc:                             # noqa: BLE001
        _record_fail(f"{type(exc).__name__}: {exc}")
        return None


def chat_json(messages: list[dict], *, temperature: float = 0.3,
              timeout: int = 40) -> dict | None:
    """要一段 JSON 回来。模型爱裹 ```json 围栏，这里统一剥掉再解析。"""
    raw = chat(messages, temperature=temperature, timeout=timeout, json_mode=True)
    if not raw:
        return None
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1] if "\n" in text else text
        text = text.removeprefix("json").strip().removesuffix("```").strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        # 模型偶尔会在 JSON 前后加说明，退一步只截最外层大括号
        lo, hi = text.find("{"), text.rfind("}")
        if lo < 0 or hi <= lo:
            return None
        try:
            parsed = json.loads(text[lo:hi + 1])
        except json.JSONDecodeError:
            return None
    return parsed if isinstance(parsed, dict) else None


def ping() -> dict:
    """发一次最小请求，验 base_url / key / model 三件套到底通不通。

    为什么值得单独做一个：`AF_LLM_BASE_URL` 少写 `/v1`、模型名写成 `gpt-4o`
    （真实名是 `gpt-4o-mini`）、key 复制时带了空格——这三种错都能让整个
    AI 分析静默退回规则版，而页面上只会显示「规则判断」，看不出是哪里错。
    """
    if not enabled():
        missing = []
        base, key, _ = _config()
        if not base:
            missing.append("AF_LLM_BASE_URL")
        if not key:
            missing.append("AF_LLM_API_KEY")
        return {"ok": False, "reply": None, "status": status(),
                "error": "未配置 " + " / ".join(missing)}
    reply = chat(
        [{"role": "user", "content": "只回复两个字：可用"}],
        temperature=0, timeout=25,
    )
    st = status()
    if reply is None:
        return {"ok": False, "reply": None, "status": st,
                "error": st.get("last_error") or "调用失败，原因未知"}
    _LAST["reply_at"] = dt.datetime.now().isoformat(timespec="seconds")
    return {"ok": True, "reply": reply[:80], "status": status(), "error": None}
