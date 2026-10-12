"""运行时容器：组装 config / keyring / db / bus / adapters / scheduler。

lifespan 语义：start() 全量失败时抛 RuntimeError 由入口 FATAL 退出；
shutdown() 优雅回收，任何异常不阻断后续关闭。
"""
from __future__ import annotations

import asyncio
import random
from typing import Any

from .adapters.base import BaseAdapter
from .adapters.fs import FsAdapter
from .adapters.git import GitAdapter
from .bus import EventBus
from .config import AppConfig, ConfigError, ConfigManager
from .db.connection import Database, DatabaseError
from .db.schema import init_schema
from .db.store import insert_audit_log
from .llm.provider import LLMProvider
from .logging_setup import get_audit_logger, get_logger, setup_logging
from .scheduler import Scheduler
from .security.keyring import Keyring, MasterKeyError

log = get_logger("runtime")
audit = get_audit_logger()

_CONNECT_RETRIES = 5


class Runtime:
    """本地 Agent 服务的运行时容器。"""

    def __init__(self) -> None:
        self._config_mgr = ConfigManager()
        self.config: AppConfig | None = None
        self._keyring: Keyring | None = None
        self._db: Database | None = None
        self._bus: EventBus | None = None
        self._adapters: dict[str, BaseAdapter] = {}
        self._scheduler: Scheduler | None = None
        self._llm: LLMProvider | None = None
        self._started = False

    # ------------------------------------------------------------------
    async def start(self) -> None:
        """完整启动：配置 → 日志 → 密钥 → 数据库 → 总线 → 适配器 → 调度器。"""
        self.config = self._load_config()
        setup_logging(
            level=self.config.logging.level,
            max_bytes=self.config.logging.max_bytes,
            backup_count=self.config.logging.backup_count,
            retention_days=self.config.logging.retention_days,
        )

        self._keyring = Keyring(
            recovery_iterations=self.config.security.recovery_iterations,
            recovery_memory_mib=self.config.security.recovery_memory_mib,
        )
        try:
            master_key = self._keyring.ensure_master_key()
        except MasterKeyError as exc:
            raise RuntimeError(
                f"主密钥不可用：{exc}。可用恢复口令重建（recover_from_passphrase）。"
            ) from exc

        self._db = Database(
            self.config.db_path,
            master_key,
            busy_timeout_ms=self.config.db.busy_timeout_ms,
            lock_memory=self.config.security.lock_memory,
        )
        try:
            await self._db.connect()
        except DatabaseError as exc:
            raise RuntimeError(str(exc)) from exc
        await init_schema(self._db)

        self._llm = LLMProvider(self._db)
        # 适配器数据入库走质量门控，需把 llm 传入总线（needs_review 只打标记不弹窗）
        self._bus = EventBus(
            self._db,
            debounce_seconds=self.config.sources.fs.debounce_seconds,
            llm=self._llm,
        )
        await self._bus.start()

        self._adapters = self._build_adapters()
        await self._start_adapters()

        self._scheduler = Scheduler(self._adapters, self._bus)
        await self._scheduler.start()

        self._started = True
        assert self._db is not None
        await insert_audit_log(
            self._db, "system", "runtime.start", object_type="app", result="ok",
            detail={"adapters": list(self._adapters),
                    "db": str(self.config.db_path),
                    "has_sources": self.config.has_any_source},
        )
        audit.audit("system", "runtime.start", object_type="app", result="ok",
                    detail={"adapters": list(self._adapters),
                            "db": str(self.config.db_path),
                            "has_sources": self.config.has_any_source})
        log.info("本地 Agent 服务启动完成", detail={
            "adapters": list(self._adapters),
            "db": str(self.config.db_path),
            "master_key_store": "DPAPI",
        })

    async def shutdown(self) -> None:
        if not self._started:
            return
        if self._scheduler is not None:
            await self._scheduler.stop()
        for adapter in self._adapters.values():
            try:
                await adapter.stop()
            except Exception:
                log.warning("适配器关闭异常", extra={"adapter": adapter.name, "action": "stop"})
        if self._bus is not None:
            await self._bus.stop()
        if self._db is not None:
            await insert_audit_log(self._db, "system", "runtime.stop", object_type="app", result="ok")
            await self._db.close()
        audit.audit("system", "runtime.stop", object_type="app", result="ok")
        log.info("本地 Agent 服务已停止")

    # ------------------------------------------------------------------
    # 配置与适配器
    # ------------------------------------------------------------------
    def _load_config(self) -> AppConfig:
        try:
            return self._config_mgr.load()
        except ConfigError as exc:
            raise RuntimeError(f"配置加载失败: {exc}") from exc

    def _build_adapters(self) -> dict[str, BaseAdapter]:
        assert self._bus is not None
        adapters: dict[str, BaseAdapter] = {}
        cfg = self.config
        assert cfg is not None
        if cfg.sources.git.repos:
            adapters["git"] = GitAdapter(cfg.sources.git.repos, self._bus, self._db)
        if cfg.sources.fs.dirs:
            adapters["fs"] = FsAdapter(cfg.sources.fs.dirs, self._bus, self._db, cfg.sources.fs)
        return adapters

    async def _start_adapters(self) -> None:
        for name, adapter in self._adapters.items():
            await self._launch_adapter(adapter)
            if adapter.state.value == "DISABLED":
                log.error("数据源已禁用", extra={"adapter": name, "action": "connect"},
                          detail={"reason": adapter.last_error})
                audit.audit("system", "adapter.disabled", object_type=f"adapter.{name}",
                            result="failed", detail={"reason": adapter.last_error})

    async def _launch_adapter(self, adapter: BaseAdapter) -> None:
        """connect 指数退避重试（含抖动），多次失败置 DISABLED。"""
        for attempt in range(1, _CONNECT_RETRIES + 1):
            try:
                await adapter.connect()
                await adapter.run()
                return
            except Exception as exc:
                adapter.mark_error(exc)
                if attempt < _CONNECT_RETRIES:
                    delay = (2 ** attempt) + random.uniform(0, 1.5)
                    log.warning("数据源连接失败，准备重试",
                                extra={"adapter": adapter.name, "action": "connect"},
                                detail={"attempt": attempt, "retry_in_s": round(delay, 2)})
                    await asyncio.sleep(delay)
                else:
                    adapter.mark_disabled(f"connect 重试 {_CONNECT_RETRIES} 次失败: {exc}")

    # ------------------------------------------------------------------
    # 数据源热重载（配置向导保存后调用）
    # ------------------------------------------------------------------
    async def reload_sources(self, new_config: AppConfig) -> dict[str, str]:
        """按新配置重建适配器（先停旧、再启新）。返回各适配器最终状态。"""
        assert self._bus is not None
        self._config_mgr.save(new_config)
        self.config = new_config

        for name, adapter in self._adapters.items():
            try:
                await adapter.stop()
            except Exception:
                log.warning("热重载：旧适配器停止异常", extra={"adapter": name, "action": "stop"})
        self._adapters.clear()

        if self._scheduler is not None:
            await self._scheduler.stop()
        self._scheduler = Scheduler(self._adapters, self._bus)

        self._adapters = self._build_adapters()
        await self._start_adapters()
        self._scheduler = Scheduler(self._adapters, self._bus)
        await self._scheduler.start()

        status = {name: adapter.state.value for name, adapter in self._adapters.items()}
        audit.audit("user", "sources.reload", object_type="config", result="ok", detail=status)
        log.info("数据源配置热重载完成", detail=status)
        return status

    # ------------------------------------------------------------------
    # 供 API 层读取状态
    # ------------------------------------------------------------------
    def adapter_status(self) -> dict[str, dict[str, Any]]:
        return {
            name: {"state": adapter.state.value, "last_error": adapter.last_error}
            for name, adapter in self._adapters.items()
        }

    @property
    def bus(self) -> EventBus | None:
        return self._bus

    @property
    def db(self) -> Database | None:
        return self._db

    @property
    def keyring(self) -> Keyring | None:
        return self._keyring

    @property
    def llm(self) -> LLMProvider | None:
        return self._llm
