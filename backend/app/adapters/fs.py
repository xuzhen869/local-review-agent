"""文档数据源适配器（§4.2）。

- watchdog 事件驱动（子线程回调 → 线程安全投递到事件总线）；
- 快照比对降级：watch 不可用时由调度器定时 sync（轮询），对比 mtime/size/sha1；
- 防抖：create/modify 由总线按路径窗口合并；delete/move 立即投递；
- 忽略：临时文件、隐藏/依赖目录、超限大文件（仅记元信息不读内容）。
"""
from __future__ import annotations

import asyncio
import hashlib
from datetime import datetime
from pathlib import Path
from typing import Any

from watchdog.events import (
    FileCreatedEvent,
    FileDeletedEvent,
    FileModifiedEvent,
    FileMovedEvent,
    FileSystemEventHandler,
)
from watchdog.observers import Observer

from .base import AdapterError, AdapterState, BaseAdapter, RawEvent
from ..bus import EventBus
from ..config import FsSources
from ..db.connection import Database
from ..db.store import get_adapter_state, set_adapter_state
from ..logging_setup import get_logger

log = get_logger("adapter.fs")

_HEAD_CHARS = 2048            # 记录文件内容的最大字符数


class _FsWatchHandler(FileSystemEventHandler):
    """watchdog 事件回调（子线程），过滤后投递到总线。"""

    def __init__(self, adapter: "FsAdapter") -> None:
        self._adapter = adapter

    def on_created(self, event: FileCreatedEvent) -> None:
        if not event.is_directory:
            self._adapter.on_change("create", event.src_path)

    def on_modified(self, event: FileModifiedEvent) -> None:
        if not event.is_directory:
            self._adapter.on_change("modify", event.src_path)

    def on_deleted(self, event: FileDeletedEvent) -> None:
        if not event.is_directory:
            self._adapter.on_change("delete", event.src_path)

    def on_moved(self, event: FileMovedEvent) -> None:
        if not event.is_directory:
            self._adapter.on_change("move", event.dest_path, src_path=event.src_path)


class FsAdapter(BaseAdapter):
    """本地文档目录变更捕获。"""

    name = "fs"

    def __init__(self, dirs: list[str], bus: EventBus, db: Database, cfg: FsSources) -> None:
        super().__init__()
        self._dirs = [str(Path(d).resolve()) for d in dirs]
        self._bus = bus
        self._db = db
        self._cfg = cfg
        self._observer: Observer | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    # ------------------------------------------------------------------
    # 工具
    # ------------------------------------------------------------------
    def _is_ignored(self, path: str) -> bool:
        name = Path(path).name
        if name.startswith("~$") or name.startswith("."):
            return True
        for ext in self._cfg.ignore_exts:
            if path.lower().endswith(ext.lower()):
                return True
        parts = Path(path).parts
        for part in parts:
            if part in self._cfg.ignore_dirs:
                return True
        return False

    def _snapshot_info(self, path: Path) -> tuple[float, int, str] | None:
        """返回 (mtime, size, digest)；超限大文件 digest 用 size 代替（不读内容）。"""
        try:
            st = path.stat()
        except OSError:
            return None
        if st.st_size > self._cfg.max_file_bytes:
            return (st.st_mtime, st.st_size, f"big:{st.st_size}")
        try:
            digest = hashlib.sha1(path.read_bytes()).hexdigest()
        except OSError:
            return None
        return (st.st_mtime, st.st_size, digest)

    def _read_head(self, path: Path) -> str:
        """读文本文件前 N 字符；二进制或超限返回空串。"""
        try:
            if path.stat().st_size > self._cfg.max_file_bytes:
                return ""
            data = path.read_bytes()[:_HEAD_CHARS * 4]
        except OSError:
            return ""
        if b"\x00" in data:
            return ""
        return data.decode("utf-8", errors="replace")

    def _make_event(self, change: str, path: str, *, src_path: str | None = None) -> RawEvent:
        p = Path(path)
        now = datetime.now().astimezone()
        if change == "delete" or change == "move":
            meta: dict[str, Any] = {"change": change, "path": path}
            if src_path:
                meta["src_path"] = src_path
            return RawEvent(
                source=self.name, source_ref=path, ts=now, content="", meta=meta,
                immediate=True, critical=True,
            )
        try:
            st = p.stat()
            size = st.st_size
            mtime = datetime.fromtimestamp(st.st_mtime).astimezone().isoformat(timespec="seconds")
        except OSError:
            size, mtime = 0, ""
        head = self._read_head(p)
        return RawEvent(
            source=self.name, source_ref=path, ts=now, content=head,
            meta={"change": change, "path": path, "ext": p.suffix, "size": size, "mtime": mtime},
            debounce_key=path,  # create/modify 走防抖合并
            critical=True,
        )

    # ------------------------------------------------------------------
    # watchdog 线程回调（由 _FsWatchHandler 调用）
    # ------------------------------------------------------------------
    def on_change(self, change: str, path: str, *, src_path: str | None = None) -> None:
        if self._is_ignored(path):
            return
        raw = self._make_event(change, path, src_path=src_path)
        self._bus.post_threadsafe(raw)

    # ------------------------------------------------------------------
    # BaseAdapter
    # ------------------------------------------------------------------
    async def connect(self) -> None:
        if not self._dirs:
            return
        for d in self._dirs:
            if not Path(d).exists():
                raise AdapterError(f"监控目录不存在: {d}")
        self.state = AdapterState.RUNNING if self.state == AdapterState.IDLE else self.state

    async def sync(self, since: datetime | None = None) -> list[RawEvent]:
        """快照比对全量同步（用于初始建库与 watch 降级轮询）。"""
        events: list[RawEvent] = []
        state = await get_adapter_state(self._db, self.name) or {"cursor": {}}
        old_snapshot = state.get("cursor") or {}
        new_snapshot: dict[str, Any] = {}

        for d in self._dirs:
            root = Path(d)
            try:
                for p in root.rglob("*"):
                    if not p.is_file() or self._is_ignored(str(p)):
                        continue
                    rel = str(p)
                    info = self._snapshot_info(p)
                    if info is None:
                        continue
                    new_snapshot[rel] = list(info)
                    old = old_snapshot.get(rel)
                    if old is None:
                        events.append(self._make_event("create", rel))
                    elif list(old) != list(info):
                        events.append(self._make_event("modify", rel))
            except OSError as exc:
                log.warning("目录遍历失败", extra={"adapter": self.name, "action": "sync"},
                            detail={"dir": d, "error": str(exc)})

        now = datetime.now().astimezone()
        for rel in old_snapshot:
            if rel not in new_snapshot:
                events.append(RawEvent(
                    source=self.name, source_ref=rel, ts=now, content="",
                    meta={"change": "delete", "path": rel},
                    immediate=True, critical=True,
                ))

        await set_adapter_state(self._db, self.name, cursor=new_snapshot,
                                state=self.state.value, last_error=self.last_error)
        if events:
            log.info("fs 快照同步", extra={"adapter": self.name, "action": "sync"},
                     detail={"new_or_changed": len(events)})
        return events

    async def run(self) -> None:
        """启动：初始快照同步 + watchdog 监听（失败降级为调度器轮询）。"""
        self._loop = asyncio.get_running_loop()
        self.state = AdapterState.CONNECTING
        try:
            events = await self.sync(datetime.now())
            for e in events:
                await self._bus.post(e)
            if self._dirs:
                handler = _FsWatchHandler(self)
                self._observer = Observer()
                for d in self._dirs:
                    self._observer.schedule(handler, d, recursive=True)
                self._observer.daemon = True
                self._observer.start()
            self.state = AdapterState.RUNNING
            log.info("fs 适配器运行中", detail={"dirs": self._dirs})
        except Exception as exc:
            self.mark_error(exc)
            log.error("fs watch 启动失败，已降级为轮询快照比对",
                      extra={"adapter": self.name, "action": "run"}, exc_info=exc)

    async def stop(self) -> None:
        if self._observer is not None:
            self._observer.stop()
            self._observer.join(timeout=5)
            self._observer = None
        self.state = AdapterState.PAUSED
        log.info("fs 适配器已停止")
