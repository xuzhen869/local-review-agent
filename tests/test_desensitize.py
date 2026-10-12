# -*- coding: utf-8 -*-
"""PII 脱敏关键路径测试（P0-1 / §6.2）。

脱敏是全通道前置防线：任何 Agent 调 LLM 前统一对动态消息内容脱敏，
兜底防止某个 Agent 漏脱敏导致 PII 出网。本测试锁定 8 类规则 + 结构化递归 +
异常信息 + 敏感命中判定的行为，防止规则顺序/覆盖范围回归。

关键点：RULES 顺序敏感（先长后短），测试用例必须命中唯一规则，避免误判。
"""
from app.security.desensitize import (desensitize, desensitize_exception,
                                      desensitize_object, is_sensitive_hit)


class TestDesensitize:
    """单条规则的逐类脱敏 + 边界。"""

    def test_手机号脱敏(self) -> None:
        assert desensitize("我的手机 13812345678 请存一下") == "我的手机 <手机号> 请存一下"

    def test_身份证脱敏(self) -> None:
        assert desensitize("身份证 110101199003077758") == "身份证 <身份证>"

    def test_银行卡脱敏(self) -> None:
        assert desensitize("卡号 4111111111111111") == "卡号 <银行卡>"

    def test_邮箱脱敏(self) -> None:
        assert desensitize("联系 user.name+tag@sub.example.com") == "联系 <邮箱>"

    def test_ip脱敏(self) -> None:
        assert desensitize("服务器 192.168.1.1 已重启") == "服务器 <IP> 已重启"

    def test_api_key脱敏(self) -> None:
        assert desensitize("sk-abcdefghijklmnop123456") == "<API_KEY>"

    def test_pem私钥脱敏(self) -> None:
        assert desensitize("-----BEGIN PRIVATE KEY-----") == "<私钥>"

    def test_aws_key脱敏(self) -> None:
        assert desensitize("AKIA1234567890ABCDEF") == "<AWS_KEY>"

    def test_混合多规则脱敏(self) -> None:
        text = "手机 13812345678 邮箱 test@example.com 内网 10.0.0.1"
        assert desensitize(text) == "手机 <手机号> 邮箱 <邮箱> 内网 <IP>"

    def test_none归为空串(self) -> None:
        assert desensitize(None) == ""

    def test_空串原样(self) -> None:
        assert desensitize("") == ""

    def test_无pii原样返回(self) -> None:
        assert desensitize("今天学习了 Python 的基础语法。") == "今天学习了 Python 的基础语法。"


class TestSensitiveHit:
    """敏感命中判定：高风险源（剪贴板）前置丢弃。"""

    def test_纯验证码命中(self) -> None:
        assert is_sensitive_hit("123456") is True

    def test_长连续数字命中(self) -> None:
        assert is_sensitive_hit("动态口令 888888 已发送") is True

    def test_api_key命中(self) -> None:
        assert is_sensitive_hit("sk-abcdefghijklmnop123456") is True

    def test_pem命中(self) -> None:
        assert is_sensitive_hit("-----BEGIN CERTIFICATE-----") is True

    def test_正常文本不命中(self) -> None:
        assert is_sensitive_hit("今天学习了新的知识点") is False

    def test_none不命中(self) -> None:
        assert is_sensitive_hit(None) is False


class TestDesensitizeObject:
    """结构化递归脱敏：dict/list 逐项，非文本原样保留。"""

    def test_嵌套dict与list递归(self) -> None:
        obj = {
            "phone": "13812345678",
            "nested": {"email": "a@b.com"},
            "items": ["192.168.1.1", "普通文本", 123],
        }
        assert desensitize_object(obj) == {
            "phone": "<手机号>",
            "nested": {"email": "<邮箱>"},
            "items": ["<IP>", "普通文本", 123],
        }

    def test_纯标量原样(self) -> None:
        assert desensitize_object(42) == 42


class TestDesensitizeException:
    """异常文本可能携带路径/邮箱/连接串，必须脱敏后再记日志。"""

    def test_异常信息脱敏(self) -> None:
        assert desensitize_exception(ValueError("非法邮箱 a@b.com")) == "ValueError: 非法邮箱 <邮箱>"
