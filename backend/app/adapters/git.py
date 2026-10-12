"""Git 数据源适配器（§4.1）。

- 触发：watch 各仓库 `.git` 目录（增量拉取）+ 调度器 15min 兜底轮询；
- 增量：游标 = 已处理最新 HEAD hash；`git log --no-merges` 忽略 merge commit；
- 健壮性：非祖先增量（rebase 历史重写）自动退化为最近 200 条全量；
  单仓库失败隔离，不中断其他仓库。
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from .base import AdapterError, AdapterState, BaseAdapter, RawEvent
from ..bus import EventBus
from ..db.connection import Database
from ..db.store import get_adapter_state, set_adapter_state
from ..logging_setup import get_logger

log = get_logger("adapter.git")

_GIT_COMMIT_FORMAT = "COMMIT%x1f%H%x1f%an%x1f%at%x1f%s%x1e"
_INITIAL_LIMIT = 200          # 首次灌入最近 N 条，避免历史风暴
_WATCH_THROTTLE_SECONDS = 5.0  # .git 目录变化触发增量 sync 的节流
_GIT_TIMEOUT_SECONDS = 60


class _GitWatchHandler(FileSystemEventHandler):
    """监听 .git 目录变化（线程回调），节流触发适配器增量同步。"""

    def __init__(self, adapter: "GitAdapter") -> None:
        self._adapter = adapter
        self._last_trigger = 0.0

    def on_any_event(self, event: Any) -> None:
        now = time.monotonic()
        if now - self._last_trigger < _WATCH_THROTTLE_SECONDS:
            return
        self._last_trigger = now
        self._adapter.on_git_changed()


def _parse_git_log(text: str) -> list[dict[str, Any]]:
    """解析 git log 输出（COMMIT<US>hash<US>author<US>at<US>subject<RS> + name-status 行）。"""
    commits: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for line in text.splitlines():
        if line.startswith("COMMIT"):
            if current is not None:
                commits.append(current)
            parts = line.split("\x1f")
            if len(parts) >= 5:
                current = {
                    "hash": parts[1],
                    "author": parts[2],
                    "ts": int(parts[3]),
                    "message": parts[4].strip(),
                    "files": [],
                }
            else:
                current = None
        elif current is not None and line:
            if "\t" in line:
                status, path = line.split("\t", 1)
                current["files"].append({"status": status.strip(), "path": path.strip()})
    if current is not None:
        commits.append(current)
    return commits


class GitAdapter(BaseAdapter):
    """多仓库 git 提交捕获。"""

    name = "git"

    def __init__(self, repos: list[str], bus: EventBus, db: Database,
                 watch_throttle_seconds: float = _WATCH_THROTTLE_SECONDS) -> None:
        super().__init__()
        self._repos = [str(Path(r).resolve()) for r in repos]
        self._bus = bus
        self._db = db
        self._watch_throttle_seconds = watch_throttle_seconds
        self._loop: asyncio.AbstractEventLoop | None = None
        self._observer: Observer | None = None
        self._sync_lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # 底层 git 调用
    # ------------------------------------------------------------------
    async def _git(self, repo: str, args: list[str]) -> tuple[int, str, str]:
        proc = await asyncio.create_subprocess_exec(
            "git", "-C", repo, *args,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=_GIT_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise AdapterError(f"git 命令超时(>{_GIT_TIMEOUT_SECONDS}s): {args}")
        return proc.returncode, stdout.decode("utf-8", "replace"), stderr.decode("utf-8", "replace")

    # ------------------------------------------------------------------
    # BaseAdapter
    # ------------------------------------------------------------------
    async def connect(self) -> None:
        if not self._repos:
            return
        for repo in self._repos:
            p = Path(repo)
            if not p.exists():
                raise AdapterError(f"仓库路径不存在: {repo}")
            if not (p / ".git").exists():
                raise AdapterError(f"非 git 工作树（缺 .git 目录）: {repo}")
            code, _, err = await self._git(repo, ["rev-parse", "--is-inside-work-tree"])
            if code != 0:
                raise AdapterError(f"git 不可用: {err.strip()[:200]}")
        self.state = AdapterState.RUNNING if self.state == AdapterState.IDLE else self.state

    async def sync(self, since: datetime | None = None) -> list[RawEvent]:
        """增量拉取所有仓库提交。游标持久化，支持重启续跑。"""
        events: list[RawEvent] = []
        state = await get_adapter_state(self._db, self.name) or {"cursor": {}}
        cursor = dict(state.get("cursor") or {})

        for repo in self._repos:
            try:
                code, out, err = await self._git(repo, ["rev-parse", "HEAD"])
                if code != 0 or not out.strip():
                    raise AdapterError(f"无法获取 HEAD（空仓库?）: {err.strip()[:200]}")
                head = out.strip()
                last = cursor.get(repo)

                incremental = False
                if last and last != head:
                    # 校验 last 是否为 head 的祖先（rebase 历史重写时退化全量）
                    anc_code, _, _ = await self._git(repo, ["merge-base", "--is-ancestor", last, head])
                    incremental = anc_code == 0

                if incremental:
                    args = ["log", "--no-merges", f"--format={_GIT_COMMIT_FORMAT}",
                            "--name-status", f"{last}..HEAD"]
                else:
                    args = ["log", "--no-merges", f"--format={_GIT_COMMIT_FORMAT}",
                            "--name-status", f"-{_INITIAL_LIMIT}"]
                code, out, err = await self._git(repo, args)
                if code != 0:
                    # range 无效等 → 再退化为最近全量
                    code, out, err = await self._git(
                        repo, ["log", "--no-merges", f"--format={_GIT_COMMIT_FORMAT}",
                               "--name-status", f"-{_INITIAL_LIMIT}"])
                if code != 0:
                    raise AdapterError(f"git log 失败: {err.strip()[:200]}")

                commits = _parse_git_log(out)
                for c in commits:
                    if not c["hash"]:
                        continue
                    events.append(RawEvent(
                        source=self.name,
                        source_ref=f"{repo}::{c['hash']}",
                        ts=datetime.fromtimestamp(c["ts"]).astimezone(),
                        content=c["message"],
                        meta={"repo": repo, "author": c["author"], "files": c["files"]},
                        critical=True,
                    ))
                cursor[repo] = head
                self.state = AdapterState.RUNNING
                self.last_error = None
                if commits:
                    log.info("git 增量同步", extra={"adapter": self.name, "action": "sync"},
                             detail={"repo": repo, "new_commits": len(commits), "head": head[:12]})
            except Exception as exc:
                self.mark_error(exc)
                log.error("git 同步失败", extra={"adapter": self.name, "action": "sync"},
                          exc_info=exc, detail={"repo": repo})

        await set_adapter_state(self._db, self.name, cursor=cursor,
                                state=self.state.value, last_error=self.last_error)
        return events

    async def run(self) -> None:
        """启动：初始同步 + watchdog 监听各仓库 .git。"""
        self._loop = asyncio.get_running_loop()
        self.state = AdapterState.CONNECTING
        try:
            events = await self.sync(datetime.now())
            for e in events:
                await self._bus.post(e)
            if self._repos:
                handler = _GitWatchHandler(self)
                self._observer = Observer()
                for repo in self._repos:
                    git_dir = Path(repo) / ".git"
                    self._observer.schedule(handler, str(git_dir), recursive=True)
                self._observer.daemon = True
                self._observer.start()
            self.state = AdapterState.RUNNING
            log.info("git 适配器运行中", detail={"repos": self._repos})
        except Exception as exc:
            self.mark_error(exc)
            log.error("git 适配器启动失败（将靠调度器兜底轮询）",
                      extra={"adapter": self.name, "action": "run"}, exc_info=exc)

    async def stop(self) -> None:
        if self._observer is not None:
            self._observer.stop()
            self._observer.join(timeout=5)
            self._observer = None
        self.state = AdapterState.PAUSED
        log.info("git 适配器已停止")

    # ------------------------------------------------------------------
    # 内部：watchdog 回调触发
    # ------------------------------------------------------------------
    def on_git_changed(self) -> None:
        if self._loop is None or self._loop.is_closed():
            return
        self._loop.call_soon_threadsafe(self._schedule_sync)

    def _schedule_sync(self) -> None:
        asyncio.create_task(self._incremental_sync())

    async def _incremental_sync(self) -> None:
        if self._sync_lock.locked():
            return  # 已有同步在进行，跳过本次触发
        async with self._sync_lock:
            events = await self.sync(datetime.now())
            for e in events:
                await self._bus.post(e)
