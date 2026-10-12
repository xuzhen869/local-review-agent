# -*- coding: utf-8 -*-
"""llm_cache TTL 判定（P2-6）：缓存新鲜度纯函数测试。

_cached_llm 命中缓存前先过 _cache_fresh 判定：过期 / 损坏 / 缺失 / 时钟回拨
一律重算，避免旧 LLM 响应被无限复用（成本与新鲜度的平衡点）。
"""
from datetime import datetime, timedelta

from app.agent.router import _cache_fresh

_TTL = 30 * 24 * 3600


class TestCacheFresh:
    def test_新鲜缓存放行(self) -> None:
        created = (datetime.now().astimezone() - timedelta(days=1)).isoformat(timespec="seconds")
        assert _cache_fresh(created, _TTL) is True

    def test_过期缓存拦截(self) -> None:
        created = (datetime.now().astimezone() - timedelta(days=31)).isoformat(timespec="seconds")
        assert _cache_fresh(created, _TTL) is False

    def test_none拦截(self) -> None:
        assert _cache_fresh(None, _TTL) is False

    def test_解析失败拦截(self) -> None:
        assert _cache_fresh("不是日期", _TTL) is False

    def test_未来时间拦截_时钟回拨(self) -> None:
        created = (datetime.now().astimezone() + timedelta(days=1)).isoformat(timespec="seconds")
        assert _cache_fresh(created, _TTL) is False
