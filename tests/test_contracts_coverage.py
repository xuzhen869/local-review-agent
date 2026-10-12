# -*- coding: utf-8 -*-
"""契约覆盖补强（P0-2 / P1-4）。

P0-2 把 4 个顶层模型补全为 ~45 个契约，覆盖全部 API 路由的顶层返回结构。
补契约最容易翻车的地方是「分支可选字段」：LLM 无 key / 空周 / 无记录等降级
分支会缺字段，若契约没给默认值，后端 response_model 会在运行时把合法响应
打成 500。本测试锁定这些关键出口模型的分支字段缺省行为，防止回归。
"""
from shared.contracts import (DueCard, DueCardList, EventItem, EventList,
                              QaAnswer, ReportConclusion, ReportGenerateResult,
                              ReportMetrics, Sources, WeeklyReport)


class TestSources:
    """数据源集合：config.py 的唯一事实源（从 contracts import）。"""

    def test_空Sources全部用默认值(self) -> None:
        s = Sources()
        assert s.git.repos == []
        assert s.fs.dirs == []
        assert s.ics.files == []
        assert s.imap.enabled is False
        assert s.clipboard.enabled is False

    def test_带git仓库构造(self) -> None:
        s = Sources(git={"repos": ["C:/Users/xu/repos/demo"]})
        assert s.git.repos == ["C:/Users/xu/repos/demo"]


class TestEventList:
    """事件出口：脱敏后的结构，可选字段必须有缺省值。"""

    def test_事件条目可选字段缺省(self) -> None:
        e = EventItem(id="1", source="git", source_ref="abc123", ts="2026-08-13T09:00:00")
        assert e.category is None
        assert e.grade == "internal"
        assert e.content == ""
        assert e.needs_review is False
        assert e.degraded is False

    def test_事件列表默认空items(self) -> None:
        lst = EventList(total=0)
        assert lst.items == []


class TestQaAnswer:
    """问答出口：无记录 / 无 key / 失败多分支字段可选。"""

    def test_空回答分支(self) -> None:
        q = QaAnswer()
        assert q.answer is None
        assert q.need_key is False
        assert q.sources == []
        assert q.error is None

    def test_无key分支(self) -> None:
        q = QaAnswer(need_key=True, message="请先录入 DeepSeek key")
        assert q.need_key is True
        assert q.answer is None


class TestWeeklyReport:
    """周报出口：空周（无事件）缺 metrics/conclusion 时契约必须放行。"""

    def test_空周生成结果_metrics与conclusion可为None(self) -> None:
        r = ReportGenerateResult(
            week_start="2026-08-10", week_end="2026-08-16", generated=False,
            reason="本周无事件", degraded=True)
        assert r.metrics is None
        assert r.conclusion is None
        assert r.generated is False

    def test_完整周报结构放行(self) -> None:
        r = WeeklyReport(
            week_start="2026-08-10", week_end="2026-08-16",
            metrics=ReportMetrics(event_count=3),
            conclusion=ReportConclusion(mainline="主线"))
        assert r.metrics.event_count == 3
        assert r.conclusion.mainline == "主线"


class TestDueCardList:
    """学习回顾出口：到期卡片结构。"""

    def test_完整到期卡片(self) -> None:
        card = DueCard(id="c1", concept="SM-2 算法", explanation="四档打分",
                       ease=2.5, interval_days=3, due_date="2026-08-16", reps=1)
        assert card.source_event_id is None
        lst = DueCardList(items=[card])
        assert lst.items[0].concept == "SM-2 算法"
