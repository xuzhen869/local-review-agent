"""LLM 统一 Provider：DeepSeek（OpenAI 兼容协议）。

- key 从 secrets 加密表读取，不明文落盘；
- 超时（connect 10s / 总 60s）+ 指数退避重试 3 次；
- 401 明确报「key 无效」；429/5xx 退避重试；其余状态码直接抛错。
"""
from __future__ import annotations

import asyncio
from typing import Any

import httpx

from ..db.store import get_secret, set_secret
from ..logging_setup import get_logger
from ..security.desensitize import desensitize

log = get_logger("llm.provider")

DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-chat"
KEY_SECRET = "deepseek_api_key"

_RETRIES = 3


class LLMError(RuntimeError):
    """LLM 调用失败（未配置 / key 无效 / 超时 / 服务错误）。"""


class LLMProvider:
    """多厂商统一层的最小可用实现（当前绑定 DeepSeek）。"""

    def __init__(self, db: Any, model: str = DEFAULT_MODEL,
                 base_url: str = DEEPSEEK_BASE_URL) -> None:
        self._db = db
        self._model = model
        self._base_url = base_url.rstrip("/")

    # ------------------------------------------------------------------
    async def is_configured(self) -> bool:
        key = await get_secret(self._db, KEY_SECRET)
        return bool(key and key.strip())

    async def save_api_key(self, key: str) -> None:
        key = key.strip()
        if not key:
            raise LLMError("API Key 不能为空")
        await set_secret(self._db, KEY_SECRET, key)
        log.info("DeepSeek API Key 已录入 secrets（加密存储）")

    async def _get_api_key(self) -> str:
        key = await get_secret(self._db, KEY_SECRET)
        if not key or not key.strip():
            raise LLMError("未配置 DeepSeek API Key（可在宠物「今日周报」或后端设置中录入）")
        return key.strip()

    # ------------------------------------------------------------------
    async def chat(self, messages: list[dict[str, str]], *,
                   temperature: float = 0.3, max_tokens: int = 2000,
                   json_mode: bool = False) -> str:
        """流式输出拼接为纯文本。失败抛 LLMError。

        json_mode=True 时启用 DeepSeek JSON 输出模式（response_format=json_object），
        模型强制输出合法 JSON；要求 messages 中出现「json」字样
        （各 JSON Agent 的 system prompt 均已包含，满足官方约束）。
        """
        key = await self._get_api_key()
        headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
        # 全通道前置脱敏（§6.2）：任何 Agent 调 LLM 前，统一对动态消息内容脱敏，
        # 兜底防止某个 Agent 漏脱敏导致 PII 出网。system 为本地硬编码模板，不脱敏。
        safe_messages = [
            {**m, "content": desensitize(m.get("content", ""))}
            if m.get("role") in ("user", "assistant") else m
            for m in messages
        ]
        body = {
            "model": self._model,
            "messages": safe_messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        url = f"{self._base_url}/chat/completions"

        timeout = httpx.Timeout(60.0, connect=10.0)
        last_error: Exception | None = None
        for attempt in range(1, _RETRIES + 1):
            try:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    resp = await client.post(url, headers=headers, json=body)
                if resp.status_code == 401:
                    raise LLMError("DeepSeek API Key 无效，请重新录入")
                if resp.status_code == 429 or resp.status_code >= 500:
                    if attempt < _RETRIES:
                        delay = 2 ** attempt
                        log.warning("LLM 限流/服务错误，准备重试",
                                    detail={"attempt": attempt, "status": resp.status_code,
                                            "retry_in_s": delay})
                        await asyncio.sleep(delay)
                        continue
                    raise LLMError(f"LLM 服务错误 HTTP {resp.status_code}: {resp.text[:200]}")
                resp.raise_for_status()
                data = resp.json()
                content = data["choices"][0]["message"]["content"]
                usage = data.get("usage") or {}
                log.info("LLM 调用完成", detail={
                    "model": self._model,
                    "prompt_tokens": usage.get("prompt_tokens", 0),
                    "completion_tokens": usage.get("completion_tokens", 0),
                })
                return content
            except LLMError:
                raise
            except httpx.TimeoutException as exc:
                last_error = exc
                log.warning("LLM 超时", detail={"attempt": attempt})
                if attempt < _RETRIES:
                    await asyncio.sleep(2 ** attempt)
            except httpx.HTTPStatusError as exc:
                raise LLMError(f"LLM HTTP {exc.response.status_code}: {exc.response.text[:200]}") from exc
            except Exception as exc:  # 网络层等
                last_error = exc
                if attempt < _RETRIES:
                    await asyncio.sleep(2 ** attempt)
        raise LLMError(f"LLM 调用失败（已重试 {_RETRIES} 次）: {last_error}")
