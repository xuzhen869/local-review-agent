"""短期对话缓存 + 挂起确认状态机（问答升级）。

- 短期缓存（chat_sessions）：会话级对话轮次，判断 Agent 的输入源；**不入 RAG、不参与长期检索**；
- 挂起确认（pending_approvals）：入库门控（review_ingest）与沉淀确认（memory_save）的异步状态机；
- 过期：pending 超过 7 天未处理 → expired（**不入库**，主人拍板 2026-08-09）。
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta
from typing import Any

from ..db.connection import Database
from ..logging_setup import get_logger

log = get_logger("agent.memory")

_PENDING_EXPIRE_DAYS = 7           # pending 未处理过期天数
_SESSION_MAX_AGE_HOURS = 24        # 短期会话缓存保留时长
_SESSION_MAX_TURNS = 50            # 单会话轮次上限（防无限增长）
_DECISIONS = ("approve_modified", "approve_raw", "approve", "reject")
_KINDS = ("review_ingest", "memory_save")
# 决策动作 → 最终状态（status 统一存过去分词，保证 pending_list(status=...) 可过滤）
_STATUS_MAP = {"approve": "approved", "approve_modified": "approved_modified",
               "approve_raw": "approved_raw", "reject": "rejected"}


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# 短期对话缓存
# ---------------------------------------------------------------------------
async def session_append(db: Database, session_id: str | None, role: str,
                         content: str) -> str:
    """追加一轮对话到短期缓存；无 session_id 则创建新会话。返回 session_id。

    role 仅允许 user/assistant，content 为空则忽略。超出轮次上限丢弃最旧轮。
    """
    if role not in ("user", "assistant"):
        raise ValueError(f"role 必须是 user/assistant，收到 {role!r}")
    content = (content or "").strip()
    if not content:
        raise ValueError("对话内容不能为空")
    sid = session_id or uuid.uuid4().hex
    turns = await session_recent(db, sid, _SESSION_MAX_TURNS + 1)
    turns = turns[-(_SESSION_MAX_TURNS - 1):] if len(turns) > _SESSION_MAX_TURNS else turns
    turns.append({"role": role, "content": content[:2000]})
    await db.execute(
        "INSERT INTO chat_sessions (id, turns, created_at, updated_at) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(id) DO UPDATE SET turns = excluded.turns, updated_at = excluded.updated_at",
        (sid, json.dumps(turns, ensure_ascii=False), _now_iso(), _now_iso()))
    await db.commit()
    return sid


async def session_recent(db: Database, session_id: str | None,
                         limit: int = 20) -> list[dict[str, str]]:
    """取会话最近 N 轮对话（{role, content}）。无会话/坏数据返回空列表。"""
    if not session_id:
        return []
    row = await db.fetchone("SELECT turns FROM chat_sessions WHERE id = ?", (session_id,))
    if row is None:
        return []
    try:
        turns = json.loads(row[0])
    except (TypeError, json.JSONDecodeError):
        return []
    if not isinstance(turns, list):
        return []
    clean = [t for t in turns if isinstance(t, dict) and t.get("role") in ("user", "assistant")
             and isinstance(t.get("content"), str) and t["content"].strip()]
    return clean[-limit:]


async def session_cleanup(db: Database) -> int:
    """清理超过 _SESSION_MAX_AGE_HOURS 未更新的会话。返回清理数。"""
    cutoff = (datetime.now().astimezone()
              - timedelta(hours=_SESSION_MAX_AGE_HOURS)).isoformat(timespec="seconds")
    cur = await db.execute("DELETE FROM chat_sessions WHERE updated_at < ?", (cutoff,))
    removed = cur.rowcount or 0
    await cur.close()
    await db.commit()
    if removed:
        log.info("短期会话缓存清理", detail={"removed": removed})
    return removed


# ---------------------------------------------------------------------------
# 挂起确认状态机
# ---------------------------------------------------------------------------
async def pending_create(db: Database, kind: str, payload: dict[str, Any],
                         source: str) -> str:
    """创建挂起确认项，返回 pending_id。"""
    if kind not in _KINDS:
        raise ValueError(f"kind 必须是 {', '.join(_KINDS)}")
    pid = uuid.uuid4().hex
    await db.execute(
        "INSERT INTO pending_approvals (id, kind, status, payload, source, created_at, decided_at) "
        "VALUES (?, ?, 'pending', ?, ?, ?, NULL)",
        (pid, kind, json.dumps(payload, ensure_ascii=False), source[:50], _now_iso()))
    await db.commit()
    return pid


async def pending_decide(db: Database, pending_id: str, action: str) -> dict[str, Any]:
    """处理挂起项：更新状态并返回 {id, kind, action, payload}。非 pending 状态拒绝。

    action：review_ingest → approve_modified / approve_raw / reject；
            memory_save → approve / reject。
    实际业务写入（入库/写卡片）由调用方根据返回的 kind/action/payload 执行。
    """
    if action not in _DECISIONS:
        raise ValueError(f"action 必须是 {', '.join(_DECISIONS)}")
    row = await db.fetchone(
        "SELECT id, kind, status, payload FROM pending_approvals WHERE id = ?", (pending_id,))
    if row is None:
        raise ValueError("pending 项不存在")
    pid, kind, status, payload_raw = row
    if status != "pending":
        raise ValueError(f"pending 项已处理（status={status}），不可重复决策")
    new_status = _STATUS_MAP[action]
    await db.execute(
        "UPDATE pending_approvals SET status = ?, decided_at = ? WHERE id = ?",
        (new_status, _now_iso(), pid))
    await db.commit()
    try:
        payload = json.loads(payload_raw)
    except (TypeError, json.JSONDecodeError):
        payload = {}
    log.info("pending 决策", detail={"pending_id": pid, "kind": kind, "action": action,
                                    "status": new_status})
    return {"id": pid, "kind": kind, "action": action, "status": new_status,
            "payload": payload}


async def pending_list(db: Database, status: str | None = None,
                       kind: str | None = None) -> list[dict[str, Any]]:
    """查询挂起项（可按 status/kind 过滤，新→旧）。"""
    sql = ("SELECT id, kind, status, payload, source, created_at, decided_at "
           "FROM pending_approvals WHERE 1=1")
    args: list[str] = []
    if status:
        sql += " AND status = ?"
        args.append(status)
    if kind:
        sql += " AND kind = ?"
        args.append(kind)
    sql += " ORDER BY created_at DESC"
    rows = await db.fetchall(sql, tuple(args))
    out: list[dict[str, Any]] = []
    for r in rows:
        try:
            payload = json.loads(r[3])
        except (TypeError, json.JSONDecodeError):
            payload = {}
        out.append({"id": r[0], "kind": r[1], "status": r[2], "payload": payload,
                    "source": r[4], "created_at": r[5], "decided_at": r[6]})
    return out


async def pending_expire(db: Database, max_age_days: int = _PENDING_EXPIRE_DAYS) -> int:
    """将超过 max_age_days 未处理的 pending 置为 expired（不入库）。返回过期数。"""
    cutoff = (datetime.now().astimezone() - timedelta(days=max_age_days)).isoformat(timespec="seconds")
    cur = await db.execute(
        "UPDATE pending_approvals SET status = 'expired', decided_at = ? "
        "WHERE status = 'pending' AND created_at < ?",
        (_now_iso(), cutoff))
    expired = cur.rowcount or 0
    await cur.close()
    await db.commit()
    if expired:
        log.info("pending 过期清理", detail={"expired": expired, "max_age_days": max_age_days})
    return expired
