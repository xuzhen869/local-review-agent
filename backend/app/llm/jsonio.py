"""LLM JSON 输出解析与重试工具：供各 JSON Agent 复用。

- extract_json: 剥离 markdown 代码块 → json.JSONDecoder.raw_decode 精确取首个合法
  JSON（dict/list），替代正则裸抓（正文含 } / 模型带解释文字均健壮）；
- chat_json: json_mode 调用 + 首次解析失败时附错误信息自动重试一次，
  仍失败返回 None（由调用方降级）。
"""
from __future__ import annotations

import json
import re
from typing import Any

from .provider import LLMProvider

_CODE_BLOCK_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def extract_json(text: str) -> Any | None:
    """从 LLM 输出中提取首个合法 JSON 对象/数组；失败返回 None。"""
    if not text:
        return None
    s = text.strip()
    # 1) 全文直解（最常见：json_mode 下模型直接输出纯 JSON）
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        pass
    # 2) 剥离 markdown 代码块（json/```json 包裹）
    for blk in _CODE_BLOCK_RE.findall(s):
        try:
            return json.loads(blk.strip())
        except json.JSONDecodeError:
            continue
    # 3) raw_decode 精确取首个合法 JSON 片段（免疫前后解释文字 / 正文含 }）
    dec = json.JSONDecoder()
    for i, ch in enumerate(s):
        if ch not in "{[":  # 只从 { 或 [ 处尝试
            continue
        try:
            obj, _end = dec.raw_decode(s, i)
            return obj
        except json.JSONDecodeError:
            continue
    return None


async def chat_json(llm: LLMProvider, messages: list[dict[str, str]], *,
                    temperature: float = 0.1, max_tokens: int = 800) -> Any | None:
    """json_mode 调用并解析；首次解析失败带错误回传重试一次；仍失败返回 None。

    正常路径零额外调用；重试仅发生在模型输出非法 JSON 时，成本可控。
    """
    text = await llm.chat(messages, temperature=temperature,
                          max_tokens=max_tokens, json_mode=True)
    parsed = extract_json(text)
    if parsed is not None:
        return parsed
    # 重试：把上次原始输出回传，让模型自我修复（常见于字段漏填/多余解释/字段类型错）
    retry_messages = list(messages) + [
        {"role": "assistant", "content": text},
        {"role": "user", "content": "上次输出不是合法且完整的 JSON。请重新只输出严格合法、"
                                    "完整的 JSON（不要 markdown 代码块、不要任何解释文字）。"},
    ]
    try:
        text2 = await llm.chat(retry_messages, temperature=temperature,
                               max_tokens=max_tokens, json_mode=True)
        return extract_json(text2)
    except Exception:  # noqa: BLE001
        return None
