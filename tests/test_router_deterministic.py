# -*- coding: utf-8 -*-
"""行为测试（第 3 课）：router.py 确定性校验器（纯函数，逻辑对错）。

- 数学等式对/错
- 中文数字归一化（五加五等于十 → 5+5=10）
- 日期有效性（2026-02-31 不存在）
- 纯文本 / should_gate 预筛
"""
from app.agent.router import _deterministic_check, should_gate


class TestDeterministicCheck:
    def test_正确数学等式放行(self) -> None:
        assert _deterministic_check("2 + 3 = 5")["math"] == "skip"

    def test_错误数学等式拦截(self) -> None:
        assert _deterministic_check("1 + 1 = 3")["math"] == "fail"

    def test_内嵌正确等式放行(self) -> None:
        assert _deterministic_check("今天做了 2 + 3 = 5 道题")["math"] == "skip"

    def test_中文正确等式放行(self) -> None:
        assert _deterministic_check("五加五等于十")["math"] == "skip"

    def test_中文错误等式拦截(self) -> None:
        assert _deterministic_check("一加一等于三")["math"] == "fail"

    def test_无效日期拦截(self) -> None:
        assert _deterministic_check("2026-02-31")["math"] == "fail"

    def test_有效日期放行(self) -> None:
        assert _deterministic_check("2026-08-13")["math"] == "skip"

    def test_纯文本不拦截(self) -> None:
        assert _deterministic_check("今天学习了 Python 的基础语法。")["math"] == "skip"


class TestShouldGate:
    def test_太短不门控(self) -> None:
        assert should_gate("hi") is False

    def test_纯日志不门控(self) -> None:
        assert should_gate("2026-08-13 10:00:00 INFO 服务启动完成") is False

    def test_正常内容触发门控(self) -> None:
        # 长度必须 ≥ _MIN_GATE_CHARS(20)，且含非日志行
        assert should_gate("今天学习了很多有用的知识，收获很大，要继续加油！") is True
