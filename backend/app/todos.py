"""待办清单服务（P2）：CRUD + 到期查询（供健康提醒轮询）。

- 优先级：高/中/低；due_at 为 ISO 时间串（可空）；
- 幂等更新（PUT 只改传入字段）；删除硬删。
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any

from .db.connection import Database
from .logging_setup import get_logger
from .security.desensitize import desensitize

log = get_logger("todos")

_PRIORITIES = {"高", "中", "低"}


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _row_to_dict(row) -> dict[str, Any]:
    """Row → dict（数据出口：text 脱敏）。行工厂为 sqlite3.Row，支持按列名访问。"""
    return {
        "id": row["id"], "text": desensitize(row["text"]), "done": bool(row["done"]),
        "priority": row["priority"], "due_at": row["due_at"],
        "created_at": row["created_at"], "completed_at": row["completed_at"],
    }


class TodoError(RuntimeError):
    """待办参数错误。"""


class TodoService:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def list(self, include_done: bool = True) -> list[dict[str, Any]]:
        if include_done:
            rows = await self._db.fetchall(
                "SELECT id, text, done, priority, due_at, created_at, completed_at "
                "FROM todos ORDER BY done ASC, due_at IS NULL, due_at ASC, created_at DESC")
        else:
            rows = await self._db.fetchall(
                "SELECT id, text, done, priority, due_at, created_at, completed_at "
                "FROM todos WHERE done = 0 ORDER BY due_at IS NULL, due_at ASC, created_at DESC")
        return [_row_to_dict(r) for r in rows] if rows else []

    async def create(self, text: str, priority: str = "中",
                     due_at: str | None = None) -> dict[str, Any]:
        text = (text or "").strip()
        if not text:
            raise TodoError("待办内容不能为空")
        if priority not in _PRIORITIES:
            priority = "中"
        todo_id = uuid.uuid4().hex
        await self._db.execute(
            "INSERT INTO todos (id, text, done, priority, due_at, created_at, completed_at) "
            "VALUES (?, ?, 0, ?, ?, ?, NULL)",
            (todo_id, text, priority, due_at or None, _now_iso()),
        )
        await self._db.commit()
        return await self._get(todo_id)

    async def update(self, todo_id: str, *, done: bool | None = None,
                     text: str | None = None, priority: str | None = None,
                     due_at: str | None = None) -> dict[str, Any]:
        row = await self._db.fetchone("SELECT * FROM todos WHERE id = ?", (todo_id,))
        if row is None:
            raise TodoError("待办不存在")
        # ⚠ 必须用原始行（未脱敏）做写回：_row_to_dict 的 text 已脱敏，若用它回写
        # 会把「联系 <手机号>」等脱敏文本写进数据库，永久破坏原始内容（只应存在加密库）。
        new_text = row["text"] if text is None else (text or "").strip()
        if not new_text:
            raise TodoError("待办内容不能为空")
        new_prio = (row["priority"] if priority is None
                    else (priority if priority in _PRIORITIES else row["priority"]))
        new_done = bool(row["done"]) if done is None else bool(done)
        new_due = row["due_at"] if due_at is None else (due_at or None)
        completed_at = row["completed_at"]
        if new_done and not row["done"]:
            completed_at = _now_iso()
        elif not new_done:
            completed_at = None
        await self._db.execute(
            "UPDATE todos SET text = ?, done = ?, priority = ?, due_at = ?, completed_at = ? "
            "WHERE id = ?",
            (new_text, 1 if new_done else 0, new_prio, new_due, completed_at, todo_id),
        )
        await self._db.commit()
        return await self._get(todo_id)

    async def delete(self, todo_id: str) -> bool:
        cur = await self._db.execute("DELETE FROM todos WHERE id = ?", (todo_id,))
        await self._db.commit()
        return bool(cur.rowcount)

    async def due_soon(self, minutes: int = 60) -> list[dict[str, Any]]:
        """未完成且 60 分钟内到期（含已过期）的待办，供到期提醒。"""
        now = datetime.now().astimezone()
        end = (now + timedelta(minutes=minutes)).isoformat(timespec="seconds")
        rows = await self._db.fetchall(
            "SELECT id, text, done, priority, due_at, created_at, completed_at "
            "FROM todos WHERE done = 0 AND due_at IS NOT NULL AND due_at <= ? "
            "ORDER BY due_at ASC", (end,),
        )
        return [_row_to_dict(r) for r in rows] if rows else []

    async def _get(self, todo_id: str) -> dict[str, Any]:
        row = await self._db.fetchone("SELECT * FROM todos WHERE id = ?", (todo_id,))
        if row is None:
            raise TodoError("待办不存在")
        return _row_to_dict(row)
