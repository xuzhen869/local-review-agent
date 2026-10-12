"""PII 脱敏层：全通道前置脱敏。

原则（§6.2）：
1. 调 LLM 前必脱敏；
2. 展示层 / HTML 导出 / 日志 / 审计 全部使用脱敏后内容；
3. 原始内容仅本地全库加密存储。

本模块为纯函数，不依赖任何业务模块，供 logging / db / api 共用。
"""
from __future__ import annotations

import re
from typing import Any

# 正则顺序敏感：先长后短，避免短规则先命中破坏长匹配
RULES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\b1[3-9]\d{9}\b"), "<手机号>"),                          # 大陆手机号
    (re.compile(r"\b\d{17}[\dXx]\b"), "<身份证>"),                          # 18 位身份证
    (re.compile(r"\b(?:4\d{3}|5[1-5]\d{2}|6(?:011|5\d{2})|7\d{3})\d{12}\b"), "<银行卡>"),  # 银行卡
    (re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b"), "<邮箱>"),            # 邮箱
    (re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b"), "<IP>"),                   # IPv4
    (re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"), "<API_KEY>"),                # OpenAI 风格密钥
    (re.compile(r"-----BEGIN[^-]*-----"), "<私钥>"),                        # PEM 私钥头
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "<AWS_KEY>"),                     # AWS Access Key
]

# 命中即应整体丢弃的条目（剪贴板过滤等），独立于脱敏规则
SENSITIVE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"^\s*\d{4,8}\s*$"),                     # 纯 4-8 位数字（验证码/密码）
    re.compile(r"\b\d{6,}\b"),                          # 连续长数字（验证码/动态口令）
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"-----BEGIN[^-]*-----"),
]


def desensitize(text: str | None) -> str:
    """对任意文本做正则预脱敏。None 归一为空串。"""
    if not text:
        return ""
    for pattern, repl in RULES:
        text = pattern.sub(repl, text)
    return text


def is_sensitive_hit(text: str | None) -> bool:
    """命中任一敏感模式（剪贴板等高风险源前置丢弃判定）。"""
    if not text:
        return False
    for pattern in SENSITIVE_PATTERNS:
        if pattern.search(text):
            return True
    return False


def _desensitize_value(value: Any) -> Any:
    """递归脱敏：str → 脱敏；dict/list → 逐项；其余原样。"""
    if isinstance(value, str):
        return desensitize(value)
    if isinstance(value, dict):
        return {k: _desensitize_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_desensitize_value(v) for v in value]
    return value


def desensitize_object(obj: Any) -> Any:
    """结构化对象（dict/list）整体脱敏，用于日志 detail / 接口响应。"""
    return _desensitize_value(obj)


def desensitize_exception(exc: BaseException) -> str:
    """异常信息脱敏：异常文本可能携带路径 / 邮箱 / 连接串，必须脱敏后再记日志。"""
    text = f"{type(exc).__name__}: {exc}"
    return desensitize(text)
