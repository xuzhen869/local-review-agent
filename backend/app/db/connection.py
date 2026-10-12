"""数据库连接层：SQLCipher 全库加密 + WAL + 单写锁（§9.4）。

集成方式：monkeypatch `aiosqlite.core.sqlite3` 为 `sqlcipher3.dbapi2`，
使 aiosqlite 所有操作透明走 SQLCipher 驱动（Windows 预编译 wheel 已验证可用）。

关键点：
- 每次连接都必须执行 `PRAGMA key`；连接后立即做一次密钥验证查询，
  密钥错误/不匹配时抛 DatabaseError（引导走恢复口令流程），绝不静默重建空库。
- `cipher_memory_security` 默认关闭；仅当配置开启且实测具备 mlock 权限时启用，
  否则降级并 WARN —— 该 PRAGMA 在无 SeLockMemoryPrivilege 时原生崩溃。
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import aiosqlite
import aiosqlite.core as aiosqlite_core
import sqlcipher3.dbapi2 as sqlcipher_driver  # noqa: F401  # 驱动本体，monkeypatch 使用

# 进程级一次性生效：aiosqlite 的连接工厂切换为 SQLCipher
aiosqlite_core.sqlite3 = sqlcipher_driver

from ..logging_setup import get_logger
from ..security.keyring import can_lock_memory

log = get_logger("db.connection")


class DatabaseError(RuntimeError):
    """数据库初始化 / 密钥解锁失败。"""


class Database:
    """SQLCipher 连接封装：单连接 + WAL + 单写锁。"""

    def __init__(
        self,
        db_path: Path,
        master_key: bytes,
        *,
        busy_timeout_ms: int = 5000,
        lock_memory: bool = False,
    ) -> None:
        self._db_path = Path(db_path)
        self._master_key = master_key
        self._busy_timeout_ms = busy_timeout_ms
        self._lock_memory = lock_memory
        self._conn: aiosqlite.Connection | None = None
        self._write_lock = asyncio.Lock()
        self._closed = False

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------
    async def connect(self) -> None:
        """建立连接并初始化 PRAGMA。可重复调用（幂等）。"""
        if self._conn is not None:
            return
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = await aiosqlite.connect(str(self._db_path), check_same_thread=False)
        self._conn = conn
        try:
            await self._init_pragmas(conn)
            await self._verify_key(conn)
        except Exception:
            await conn.close()
            self._conn = None
            raise

    async def _init_pragmas(self, conn: aiosqlite.Connection) -> None:
        key_hex = self._master_key.hex()
        # key 为 hex 字符串，无 SQL 注入面
        await conn.execute(f"PRAGMA key = '{key_hex}'")
        if self._lock_memory:
            if can_lock_memory():
                await conn.execute("PRAGMA cipher_memory_security = ON")
                log.info("cipher_memory_security 已启用（具备 mlock 权限）")
            else:
                log.warning(
                    "配置 lock_memory=true 但当前进程无 mlock 权限，已降级关闭"
                    "（避免 SQLCipher 原生崩溃）；以管理员运行可启用"
                )
        await conn.execute("PRAGMA cipher_use_hmac = ON")
        await conn.execute("PRAGMA journal_mode=WAL")
        await conn.execute(f"PRAGMA busy_timeout = {self._busy_timeout_ms}")
        # 行工厂设为 Row，保证查询返回可按列名访问的对象（dict(row) 可用）
        conn.row_factory = sqlcipher_driver.Row
        await conn.commit()

    async def _verify_key(self, conn: aiosqlite.Connection) -> None:
        """密钥校验：错误 key 时 SQLCipher 读取 sqlite_master 会失败。"""
        try:
            async with conn.execute("SELECT count(*) FROM sqlite_master") as cur:
                await cur.fetchone()
        except Exception as exc:
            raise DatabaseError(
                "数据库密钥校验失败：主密钥与库不匹配或库已损坏。"
                "请用恢复口令导入（recover_from_passphrase）或检查 data.db。"
            ) from exc

    async def close(self) -> None:
        if self._conn is not None and not self._closed:
            await self._conn.close()
            self._closed = True
            self._conn = None

    @property
    def is_connected(self) -> bool:
        return self._conn is not None and not self._closed

    # ------------------------------------------------------------------
    # 读写原语（外部只通过这些方法访问数据库）
    # ------------------------------------------------------------------
    async def execute(self, sql: str, params: tuple | dict | list = ()) -> Any:
        """写操作：经单写锁串行化（杜绝 SQLITE_BUSY）。返回 cursor（游标未迭代）。"""
        if self._conn is None:
            raise DatabaseError("数据库未连接")
        async with self._write_lock:
            cur = await self._conn.execute(sql, params)
            return cur

    async def executemany(self, sql: str, seq_of_params: list) -> None:
        if self._conn is None:
            raise DatabaseError("数据库未连接")
        async with self._write_lock:
            await self._conn.executemany(sql, seq_of_params)

    async def fetchall(self, sql: str, params: tuple | dict | list = ()) -> list[Any]:
        """读操作：WAL 下并发读不阻塞。"""
        if self._conn is None:
            raise DatabaseError("数据库未连接")
        cur = await self._conn.execute(sql, params)
        rows = await cur.fetchall()
        await cur.close()
        return rows

    async def fetchone(self, sql: str, params: tuple | dict | list = ()) -> Any:
        if self._conn is None:
            raise DatabaseError("数据库未连接")
        cur = await self._conn.execute(sql, params)
        row = await cur.fetchone()
        await cur.close()
        return row

    async def commit(self) -> None:
        if self._conn is None:
            raise DatabaseError("数据库未连接")
        async with self._write_lock:
            await self._conn.commit()
