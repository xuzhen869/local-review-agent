"""截图判断 Agent：分析 OCR 文字，生成解决方案 / 要点提炼。

流程：OCR 文字 → 判断 Agent 一次 LLM 调用生成解决方案（JSON {solution}）
→ 前端弹可编辑框确认后入库（source=manual, keywords 记 channel=solution）。

降级策略（不丢功能）：
- 无 LLM Key / 未配置 / 调用失败 / 输出为空 → solution=""、degraded=True，
  前端直接预填 OCR 原文，主人手写方案后仍可入库，功能不依赖 Key。
"""
from __future__ import annotations

from typing import Any

from ..llm.jsonio import chat_json
from ..llm.provider import LLMProvider
from ..logging_setup import get_logger

log = get_logger("agent.solution")

# 单次输入上限：超长 OCR 文本截断，避免超出模型上下文（token 成本可控）
_MAX_INPUT_CHARS = 4000

_SYSTEM = "你善于从屏幕截图识别出的文字内容中，给出可执行的解决方案。"
_USER = """下面是用户从屏幕截图中识别出的文字内容。请分析并给出：
- 如果是报错/问题：给出解决思路与排查步骤；
- 如果是学习/工作内容：提炼核心要点；
- 如果是一般信息：归纳总结。

要求：使用中文，分条清晰列出。只输出一个 JSON 对象，形如
{{"solution": "解决方案正文"}}，不要输出任何解释文字。

截图文字：
{text}"""


async def analyze_solution(llm: LLMProvider | None, text: str) -> dict[str, Any]:
    """分析 OCR 文字生成解决方案。返回 {"solution": str, "degraded": bool}。

    - text 为空 → 直接返回空 solution（前端不弹框，走无文字提示）；
    - llm 未配置/调用失败/输出为空 → degraded=True 降级（solution 为空串）。
    """
    text = (text or "").strip()
    if not text:
        return {"solution": "", "degraded": False}
    if llm is None or not await llm.is_configured():
        log.debug("LLM 未配置，截图方案降级为原文")
        return {"solution": "", "degraded": True}

    messages = [
        {"role": "system", "content": _SYSTEM},
        {"role": "user", "content": _USER.format(text=text[:_MAX_INPUT_CHARS])},
    ]
    try:
        data = await chat_json(llm, messages, temperature=0.2, max_tokens=900)
        if isinstance(data, dict):
            solution = str(data.get("solution") or "").strip()
            if not solution:  # 兼容模型把正文放在其他 key 的情形
                solution = str(data).strip()
        elif isinstance(data, str):  # 兼容 json_mode 失效输出纯文本
            solution = data.strip()
        else:
            solution = ""
        log.info("截图方案生成完成", detail={"solution_len": len(solution),
                                          "degraded": not bool(solution)})
        return {"solution": solution, "degraded": not bool(solution)}
    except Exception as exc:  # noqa: BLE001
        log.warning("截图方案生成失败，降级",
                    detail={"error": f"{type(exc).__name__}: {exc}"})
        return {"solution": "", "degraded": True}
