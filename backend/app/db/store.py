"""数据访问：事件幂等入库、适配器游标、审计、secrets（§10）。"""
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .connection import Database
from ..logging_setup import get_logger
from ..security.desensitize import desensitize, desensitize_object

log = get_logger("db.store")


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


def redact_ref(ref: str) -> str:
    """脱敏 source_ref（数据出口用；入库仍存原文，保幂等键可去重）。

    - fs 绝对路径   C:/Users/xu/Desktop/笔记/第一章.txt → 第一章.txt
    - git 绝对路径  C:/Users/xu/Desktop/项目::a1b2c3d → 项目::a1b2c3d（hash 保留）
    - 相对路径 / manual:* 等安全格式取末段，不做额外变换
    """
    if not ref:
        return ref
    if "::" in ref:
        head, sep, tail = ref.partition("::")
        head = head.replace("\\", "/").rstrip("/")
        return head.rsplit("/", 1)[-1] + sep + tail
    return ref.replace("\\", "/").rsplit("/", 1)[-1]


@dataclass
class EventRecord:
    """归一化事件（由 bus 层从 RawEvent 构造，store 只负责落库）。"""
    source: str
    source_ref: str
    ts: str
    content: str
    keywords_json: str = "{}"
    category: str | None = None
    grade: str = "internal"
    needs_review: bool = False
    degraded: bool = False


# ---------------------------------------------------------------------------
# events：幂等入库
# ---------------------------------------------------------------------------
def _content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


async def store_event(db: Database, rec: EventRecord) -> tuple[bool, str]:
    """写入一条事件；依赖 (source, source_ref) 唯一约束实现幂等去重。

    返回 (是否新增, event_id)。
    """
    event_id = uuid.uuid4().hex
    created_at = _now_iso()
    cur = await db.execute(
        """
        INSERT OR IGNORE INTO events
            (id, source, source_ref, ts, category, grade, content,
             content_hash, keywords, needs_review, degraded, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event_id, rec.source, rec.source_ref, rec.ts, rec.category, rec.grade,
            rec.content, _content_hash(rec.content), rec.keywords_json,
            1 if rec.needs_review else 0, 1 if rec.degraded else 0, created_at,
        ),
    )
    inserted = cur.rowcount
    await cur.close()
    await db.commit()
    if inserted:
        log.debug("事件入库", extra={"event_id": event_id, "adapter": rec.source,
                                    "action": "store",
                                    "detail": {"source_ref": redact_ref(rec.source_ref)}})
    return bool(inserted), event_id


async def query_events(
    db: Database,
    *,
    source: str | None = None,
    category: str | None = None,
    ts_from: str | None = None,
    ts_to: str | None = None,
    limit: int = 500,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """查询事件（返回脱敏字段；content 为加密库内的明文语义列，上层展示前需脱敏）。"""
    clauses: list[str] = []
    params: list[Any] = []
    if source:
        clauses.append("source = ?")
        params.append(source)
    if category:
        clauses.append("category = ?")
        params.append(category)
    if ts_from:
        clauses.append("ts >= ?")
        params.append(ts_from)
    if ts_to:
        clauses.append("ts <= ?")
        params.append(ts_to)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    sql = f"SELECT id, source, source_ref, ts, category, grade, content, keywords, needs_review, degraded "
    sql += f"FROM events {where} ORDER BY ts DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])
    rows = await db.fetchall(sql, tuple(params))
    # 统一数据出口脱敏：库内为原文，返回层对 content 脱敏、source_ref 只留末段，
    # 避免绝对路径（含用户名）经 API/LLM 上下文泄漏。
    return [
        {**dict(r), "content": desensitize(r["content"]),
         "source_ref": redact_ref(r["source_ref"])}
        for r in rows
    ]


async def count_events(db: Database, source: str | None = None) -> int:
    if source:
        row = await db.fetchone("SELECT count(*) FROM events WHERE source = ?", (source,))
    else:
        row = await db.fetchone("SELECT count(*) FROM events")
    return int(row[0]) if row else 0


async def delete_event(db: Database, event_id: str) -> bool:
    """删除单条事件（含关联 embeddings 向量块）。返回是否删除成功。

    事件内容向量化进 embeddings（event_id 关联），必须同步清理，
    否则问答 RAG 会检索到已删除事件的残留内容。
    """
    await db.execute("DELETE FROM embeddings WHERE event_id = ?", (event_id,))
    cur = await db.execute("DELETE FROM events WHERE id = ?", (event_id,))
    removed = cur.rowcount
    await cur.close()
    await db.commit()
    return removed > 0


async def delete_events(db: Database, source: str | None = None) -> int:
    """删除全部（或指定来源）事件及其关联 embeddings。返回删除条数。

    与 delete_event 同理级联清理向量块；IN 列表分批执行防过长。
    """
    if source:
        ids = [r[0] for r in await db.fetchall(
            "SELECT id FROM events WHERE source = ?", (source,))]
        cur = await db.execute("DELETE FROM events WHERE source = ?", (source,))
    else:
        ids = [r[0] for r in await db.fetchall("SELECT id FROM events")]
        cur = await db.execute("DELETE FROM events")
    removed = cur.rowcount
    await cur.close()
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        marks = ",".join("?" * len(chunk))
        await db.execute(
            f"DELETE FROM embeddings WHERE event_id IN ({marks})", chunk)
    await db.commit()
    return removed


# ---------------------------------------------------------------------------
# adapters_state：增量游标 / 状态
# ---------------------------------------------------------------------------
async def get_adapter_state(db: Database, adapter: str) -> dict[str, Any] | None:
    row = await db.fetchone(
        "SELECT cursor, state, last_error, updated_at FROM adapters_state WHERE adapter = ?",
        (adapter,),
    )
    if row is None:
        return None
    return {
        "cursor": json.loads(row[0]) if row[0] else {},
        "state": row[1],
        "last_error": row[2],
        "updated_at": row[3],
    }


async def set_adapter_state(
    db: Database, adapter: str, cursor: dict[str, Any] | None = None,
    state: str = "RUNNING", last_error: str | None = None,
) -> None:
    await db.execute(
        """
        INSERT INTO adapters_state (adapter, cursor, state, last_error, updated_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(adapter) DO UPDATE SET
            cursor = excluded.cursor,
            state = excluded.state,
            last_error = excluded.last_error,
            updated_at = excluded.updated_at
        """,
        (adapter, json.dumps(cursor or {}, ensure_ascii=False), state, last_error, _now_iso()),
    )
    await db.commit()


# ---------------------------------------------------------------------------
# audit_logs：审计（M1 记录敏感操作；M3 门控复用）
# ---------------------------------------------------------------------------
async def insert_audit_log(
    db: Database,
    actor: str,
    action: str,
    object_type: str = "",
    object_id: str = "",
    result: str = "ok",
    detail: dict[str, Any] | None = None,
) -> None:
    await db.execute(
        "INSERT INTO audit_logs (ts, actor, action, object_type, object_id, result, detail) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (_now_iso(), actor, action, object_type, object_id, result,
         json.dumps(detail, ensure_ascii=False) if detail else None),
    )
    await db.commit()


async def query_audit_logs(db: Database, limit: int = 200) -> list[dict[str, Any]]:
    rows = await db.fetchall(
        "SELECT ts, actor, action, object_type, object_id, result, detail "
        "FROM audit_logs ORDER BY id DESC LIMIT ?",
        (limit,),
    )
    out = []
    for r in rows:
        item = dict(r)
        try:
            detail = json.loads(item["detail"]) if item["detail"] else None
            item["detail"] = desensitize_object(detail)
        except (ValueError, TypeError):
            item["detail"] = desensitize(str(item["detail"]))
        out.append(item)
    return out


# ---------------------------------------------------------------------------
# secrets：敏感配置（全库加密覆盖）
# ---------------------------------------------------------------------------
async def set_secret(db: Database, key: str, value: str) -> None:
    await db.execute(
        "INSERT INTO secrets (key, value, created_at) VALUES (?, ?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value, _now_iso()),
    )
    await db.commit()


async def get_secret(db: Database, key: str) -> str | None:
    row = await db.fetchone("SELECT value FROM secrets WHERE key = ?", (key,))
    return row[0] if row else None
