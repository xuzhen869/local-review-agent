"""调度器（§4/§9）：定时兜底轮询，弥补事件监听的漏网。

- git：每 15min 增量同步（watch 之外的第二道保险）；
- fs：按 poll_interval_seconds 轮询，但仅在 watch 不可用（state != RUNNING）时
  执行全量快照比对，避免 watch 正常时重复扫描。
"""
from __future__ import annotations

import asyncio
import random
from datetime import datetime
from typing import Any

from .adapters.base import AdapterState, BaseAdapter
from .bus import EventBus
from .logging_setup import get_logger

log = get_logger("scheduler")

GIT_POLL_INTERVAL_SECONDS = 900
_DEFAULT_FS_POLL = 300


class Scheduler:
    def __init__(self, adapters: dict[str, BaseAdapter], bus: EventBus) -> None:
        self._adapters = adapters
        self._bus = bus
        self._tasks: list[asyncio.Task[Any]] = []

    async def start(self) -> None:
        git = self._adapters.get("git")
        if git and git.enabled and git._repos:  # type: ignore[attr-defined]
            self._tasks.append(asyncio.create_task(
                self._poll_loop(git, GIT_POLL_INTERVAL_SECONDS),
                name="sched-git",
            ))
        fs = self._adapters.get("fs")
        if fs and fs.enabled and fs._dirs:  # type: ignore[attr-defined]
            interval = int(getattr(fs, "_cfg", None).poll_interval_seconds
                           if hasattr(fs, "_cfg") and fs._cfg else _DEFAULT_FS_POLL)
            self._tasks.append(asyncio.create_task(
                self._poll_loop(fs, interval, only_when_watch_down=True),
                name="sched-fs",
            ))
        if self._tasks:
            log.info("调度器已启动", detail={"jobs": [t.get_name() for t in self._tasks]})

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._tasks.clear()

    async def _poll_loop(self, adapter: BaseAdapter, interval: int,
                         only_when_watch_down: bool = False) -> None:
        while True:
            await asyncio.sleep(interval)
            try:
                if only_when_watch_down and adapter.state == AdapterState.RUNNING:
                    continue  # watch 正常，不重复全量扫描
                events = await adapter.sync(datetime.now())
                for e in events:
                    await self._bus.post(e)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.error("定时同步失败", extra={"adapter": adapter.name, "action": "poll"},
                          exc_info=exc, detail={"next_retry_s": interval + random.randint(0, 30)})
