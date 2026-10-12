# -*- coding: utf-8 -*-
"""契约测试（第 3 课）：验证 shared/contracts.py 围栏有效性。

- 正确结构 → 能解析（放行）
- 缺字段 / 类型错 / 结构错 → 被 ValidationError 拦（围栏工作）
"""
import pytest
from pydantic import ValidationError

from shared.contracts import (PetState, PomodoroConfig, PomodoroSession,
                              PomodoroStats)


class TestPetState:
    def test_正确结构放行(self) -> None:
        s = PetState(hunger=50, hunger_max=100, food=2,
                     punched_today=True, today_pomodoro=3, mood="happy")
        assert s.hunger == 50
        assert s.mood == "happy"

    def test_缺字段被拦(self) -> None:
        with pytest.raises(ValidationError):
            PetState(hunger=50, hunger_max=100, food=2,
                     punched_today=True, today_pomodoro=3)  # 故意缺 mood


class TestPomodoroSession:
    def test_类型错被拦(self) -> None:
        with pytest.raises(ValidationError):
            PomodoroSession(phase="focus", remaining_seconds=1500,
                            running="不是布尔", completed_in_cycle=0,
                            updated_at="2026-08-13T09:00:00")


class TestPomodoroStats:
    @staticmethod
    def _valid() -> dict:
        return {
            "today": {"count": 2, "minutes": 50},
            "week": {"count": 10, "minutes": 250, "week_start": "2026-08-10"},
            "total": {"count": 100, "minutes": 2500},
            "last_14_days": [{"date": "2026-08-13", "count": 2, "minutes": 50}],
            "phase_dist": {"focus": 90, "short_break": 8, "long_break": 2},
            "interrupted": 3,
            "current_streak_days": 5,
        }

    def test_嵌套数组结构放行(self) -> None:
        s = PomodoroStats(**self._valid())
        assert s.today.count == 2
        assert s.week.week_start == "2026-08-10"
        assert s.last_14_days[0].date == "2026-08-13"

    def test_数组项缺date被拦(self) -> None:
        data = self._valid()
        data["last_14_days"] = [{"count": 2, "minutes": 50}]  # 数组项缺 date
        with pytest.raises(ValidationError):
            PomodoroStats(**data)

    def test_嵌套today缺date放行_但count错被拦(self) -> None:
        data = self._valid()
        data["today"] = {"count": "不是数字", "minutes": 50}  # 类型错
        with pytest.raises(ValidationError):
            PomodoroStats(**data)
