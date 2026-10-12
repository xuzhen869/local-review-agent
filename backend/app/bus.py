"""事件总线（§9.2）：asyncio.Queue 背压 + 单消费者串行入库 + 防抖合并（§9.5）。

- 适配器（协程/线程）投递事件到队列；
- 单消费者串行做：脱敏 → 构造 EventRecord → 幂等入库（天然规避 SQLite 写竞争）；
- fs 类事件按 debounce_key 做窗口合并；关键源（git/fs）在队列满时阻塞投递不丢弃。
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, Awaitable, Callable

from .adapters.base import RawEvent
from .db.connection import Database
from .db.store import EventRecord, redact_ref, store_event
from .logging_setup import get_logger

log = get_logger("bus")

# 背压时不丢弃的关键源
CRITICAL_SOURCES = frozenset({"git", "fs"})

# 适配器数据参与质量门控的文本长度上限（超大文件跳过门控，控 LLM 成本）
_ADAPTER_GATE_MAX = 6000


class _Debouncer:
    """按 key 的窗口合并：窗口内新事件覆盖旧事件（取最终状态），到期只发一次。"""

    def __init__(self, window_seconds: float, emit: Callable[[RawEvent], Awaitable[None]]) -> None:
        self._window = window_seconds
        self._emit = emit
        self._pending: dict[str, tuple[RawEvent, asyncio.TimerHandle]] = {}
        self._loop = asyncio.get_running_loop()

    def add(self, key: str, raw: RawEvent, *, immediate: bool = False) -> None:
        if immediate:
            self._flush(key)
            self._loop.create_task(self._emit(raw))
            return
        existing = self._pending.get(key)
        if existing is not None:
            existing[0].__dict__.update(raw.__dict__)  # 保留合并后最终状态
            return
        timer = self._loop.call_later(self._window, self._emit_key, key)
        self._pending[key] = (raw, timer)

    def _emit_key(self, key: str) -> None:
        item = self._pending.pop(key, None)
        if item is not None:
            self._loop.create_task(self._emit(item[0]))

    def _flush(self, key: str) -> None:
        item = self._pending.pop(key, None)
        if item is not None:
            item[1].cancel()
            self._loop.create_task(self._emit(item[0]))

    def flush_all(self) -> None:
        for key in list(self._pending):
            self._flush(key)

    @property
    def pending_count(self) -> int:
        return len(self._pending)


class EventBus:
    """全局事件总线。"""

    def __init__(
        self,
        db: Database,
        *,
        queue_maxsize: int = 1000,
        debounce_seconds: float = 5.0,
        llm: Any = None,
    ) -> None:
        self._db = db
        self._llm = llm
        self._queue: asyncio.Queue[RawEvent] = asyncio.Queue(maxsize=queue_maxsize)
        self._debounce_seconds = debounce_seconds
        self._loop: asyncio.AbstractEventLoop | None = None
        self._debouncer: _Debouncer | None = None
        self._consumer_task: asyncio.Task[None] | None = None
        self._dropped_count = 0

    # ------------------------------------------------------------------
    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._debouncer = _Debouncer(self._debounce_seconds, self._process_raw)
        self._consumer_task = asyncio.create_task(self._consumer(), name="bus-consumer")
        log.info("事件总线已启动", detail={"queue_maxsize": self._queue.maxsize,
                                        "debounce_seconds": self._debounce_seconds})

    async def stop(self) -> None:
        if self._debouncer is not None:
            self._debouncer.flush_all()
        if self._consumer_task is not None:
            self._consumer_task.cancel()
            try:
                await self._consumer_task
            except asyncio.CancelledError:
                pass
            self._consumer_task = None
        log.info("事件总线已停止", detail={"dropped": self._dropped_count})

    @property
    def queue_size(self) -> int:
        return self._queue.qsize()

    @property
    def dropped_count(self) -> int:
        return self._dropped_count

    @property
    def pending_debounce(self) -> int:
        return self._debouncer.pending_count if self._debouncer else 0

    # ------------------------------------------------------------------
    # 投递（协程上下文）
    # ------------------------------------------------------------------
    async def post(self, raw: RawEvent) -> None:
        critical = raw.source in CRITICAL_SOURCES
        try:
            self._queue.put_nowait(raw)
        except asyncio.QueueFull:
            if critical:
                await self._queue.put(raw)  # 阻塞投递，关键源不丢
            else:
                self._dropped_count += 1
                log.warning("非关键事件因队列满被丢弃",
                            extra={"adapter": raw.source, "action": "drop"},
                            detail={"source_ref": redact_ref(raw.source_ref),
                                    "dropped_total": self._dropped_count})

    # 投递（线程上下文，如 watchdog 回调线程）
    def post_threadsafe(self, raw: RawEvent) -> None:
        if self._loop is None or self._loop.is_closed():
            return
        self._loop.call_soon_threadsafe(self._schedule_post, raw)

    def _schedule_post(self, raw: RawEvent) -> None:
        asyncio.create_task(self.post(raw))

    # ------------------------------------------------------------------
    # 单消费者：串行归一化 + 入库
    # ------------------------------------------------------------------
    async def _consumer(self) -> None:
        while True:
            raw = await self._queue.get()
            try:
                await self._handle(raw)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.error("事件处理失败", extra={"adapter": raw.source, "action": "consume"},
                          exc_info=exc, detail={"source_ref": redact_ref(raw.source_ref)})
            finally:
                self._queue.task_done()

    async def _handle(self, raw: RawEvent) -> None:
        if self._debouncer is not None and raw.debounce_key is not None:
            self._debouncer.add(raw.debounce_key, raw, immediate=raw.immediate)
            return
        await self._process_raw(raw)

    async def _process_raw(self, raw: RawEvent) -> None:
        # 入库存原文（全库 SQLCipher 加密保护）；脱敏统一在数据出口
        # （query_events 返回 / LLM prompt 构造）执行 —— 保留原文可恢复性。
        rec = EventRecord(
            source=raw.source,
            source_ref=raw.source_ref,
            ts=raw.ts.astimezone().isoformat(timespec="seconds"),
            content=raw.content,
            keywords_json=json.dumps(raw.meta, ensure_ascii=False),
            grade="internal",                          # M1 默认；M3 接入分级
        )
        # 适配器数据质量门控：clear → 直接入库；needs_review → 带标记原样入库
        # （后台自动采集不弹窗打扰，create_pending=False；数据不丢，事件流可见待审标记）
        if (self._llm is not None and raw.content
                and len(raw.content) <= _ADAPTER_GATE_MAX):
            from .agent.router import route_text
            try:
                gate = await route_text(self._db, self._llm, raw.content, raw.source,
                                        create_pending=False)
            except Exception as exc:  # noqa: BLE001
                log.warning("适配器门控异常，按原样入库",
                            detail={"error": str(exc)[:200]})
                gate = {"verdict": "clear"}
            if gate.get("verdict") == "needs_review":
                rec.needs_review = True
                log.info("适配器数据门控标记待审", extra={"adapter": raw.source,
                                                     "action": "gate"},
                         detail={"source_ref": redact_ref(raw.source_ref),
                                 "concern": (gate.get("concern") or "")[:120]})
        inserted, _ = await store_event(self._db, rec)
        if not inserted:
            log.debug("重复事件已忽略", extra={"adapter": raw.source, "action": "dedup"},
                      detail={"source_ref": redact_ref(raw.source_ref)})
