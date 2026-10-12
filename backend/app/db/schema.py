"""数据库 schema（§10）。全库加密覆盖，敏感列直接用 TEXT 明文语义列。

M1 实际使用：events / adapters_state / secrets / audit_logs；
其余表按计划预建空表，供 M2/M3 里程碑直接使用。
"""
from __future__ import annotations

from .connection import Database
from ..logging_setup import get_logger

log = get_logger("db.schema")

_SCHEMA_STATEMENTS: list[str] = [
    # ---- events：归一化事件（幂等去重：source+source_ref 唯一）----
    """
    CREATE TABLE IF NOT EXISTS events (
        id            TEXT PRIMARY KEY,
        source        TEXT NOT NULL,
        source_ref    TEXT NOT NULL,
        ts            TEXT NOT NULL,
        category      TEXT,
        grade         TEXT NOT NULL DEFAULT 'internal',
        content       TEXT,
        content_hash  TEXT,
        keywords      TEXT,
        summary       TEXT,
        needs_review  INTEGER DEFAULT 0,
        degraded      INTEGER DEFAULT 0,
        created_at    TEXT NOT NULL,
        UNIQUE(source, source_ref)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts)",
    "CREATE INDEX IF NOT EXISTS idx_events_source ON events(source)",
    "CREATE INDEX IF NOT EXISTS idx_events_category ON events(category)",

    # ---- traces：Agent 决策回放（M2）----
    """
    CREATE TABLE IF NOT EXISTS traces (
        run_id           TEXT PRIMARY KEY,
        event_ids        TEXT NOT NULL,
        steps            TEXT NOT NULL,
        status           TEXT NOT NULL,
        total_cost       TEXT,
        total_latency_ms INTEGER,
        created_at       TEXT NOT NULL
    )
    """,

    # ---- weekly_reports / daily_summaries：报告（M2）----
    """
    CREATE TABLE IF NOT EXISTS weekly_reports (
        id          TEXT PRIMARY KEY,
        week_start  TEXT NOT NULL,
        week_end    TEXT NOT NULL,
        metrics     TEXT NOT NULL,
        timeline    TEXT NOT NULL,
        categories  TEXT NOT NULL,
        learnings   TEXT NOT NULL,
        conclusion  TEXT NOT NULL,
        created_at  TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_weekly_start ON weekly_reports(week_start)",
    """
    CREATE TABLE IF NOT EXISTS daily_summaries (
        id         TEXT PRIMARY KEY,
        date       TEXT NOT NULL,
        summary    TEXT NOT NULL,
        created_at TEXT NOT NULL
    )
    """,

    # ---- audit_logs：审计（M3 门控使用，M1 记录敏感操作）----
    """
    CREATE TABLE IF NOT EXISTS audit_logs (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        ts          TEXT NOT NULL,
        actor       TEXT NOT NULL,
        action      TEXT NOT NULL,
        object_type TEXT,
        object_id   TEXT,
        result      TEXT NOT NULL,
        detail      TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_logs(ts)",

    # ---- approval_requests：门控审批（M3）----
    """
    CREATE TABLE IF NOT EXISTS approval_requests (
        id             TEXT PRIMARY KEY,
        tool           TEXT NOT NULL,
        payload_hash   TEXT NOT NULL,
        payload_summary TEXT NOT NULL,
        status         TEXT NOT NULL,
        requested_at   TEXT NOT NULL,
        decided_at     TEXT,
        decided_by     TEXT
    )
    """,

    # ---- adapters_state：各数据源游标/状态（增量续跑）----
    """
    CREATE TABLE IF NOT EXISTS adapters_state (
        adapter    TEXT PRIMARY KEY,
        cursor     TEXT NOT NULL,
        state      TEXT NOT NULL,
        last_error TEXT,
        updated_at TEXT NOT NULL
    )
    """,

    # ---- secrets：加密敏感配置（全库加密覆盖）----
    """
    CREATE TABLE IF NOT EXISTS secrets (
        key        TEXT PRIMARY KEY,
        value      TEXT NOT NULL,
        created_at TEXT NOT NULL
    )
    """,

    # ---- llm_cache：内容 hash 缓存（M2，控成本）----
    """
    CREATE TABLE IF NOT EXISTS llm_cache (
        key         TEXT PRIMARY KEY,
        model       TEXT NOT NULL,
        response    TEXT NOT NULL,
        cost        TEXT NOT NULL,
        created_at  TEXT NOT NULL
    )
    """,

    # ---- pet_state：宠物喂养（单行，id=1）----
    """
    CREATE TABLE IF NOT EXISTS pet_state (
        id              INTEGER PRIMARY KEY CHECK (id = 1),
        hunger          INTEGER NOT NULL DEFAULT 100,
        food            INTEGER NOT NULL DEFAULT 0,
        updated_at      TEXT NOT NULL,
        last_punch_date TEXT NOT NULL DEFAULT ''
    )
    """,

    # ---- pomodoro_records：番茄钟完成/中断记录（可与周报「专注时段」联动）----
    # phase: focus / short_break / long_break；status: completed / interrupted
    """
    CREATE TABLE IF NOT EXISTS pomodoro_records (
        id      INTEGER PRIMARY KEY AUTOINCREMENT,
        minutes INTEGER NOT NULL,
        phase   TEXT NOT NULL DEFAULT 'focus',
        status  TEXT NOT NULL DEFAULT 'completed',
        ts      TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_pomodoro_ts ON pomodoro_records(ts)",

    # ---- pomodoro_settings：番茄钟配置（单行，id=1）----
    """
    CREATE TABLE IF NOT EXISTS pomodoro_settings (
        id                  INTEGER PRIMARY KEY CHECK (id = 1),
        focus_minutes       INTEGER NOT NULL DEFAULT 25,
        short_break_minutes INTEGER NOT NULL DEFAULT 5,
        long_break_minutes  INTEGER NOT NULL DEFAULT 15,
        laps_per_cycle      INTEGER NOT NULL DEFAULT 4,
        updated_at          TEXT NOT NULL
    )
    """,

    # ---- pomodoro_session：番茄钟进行中状态（单行，id=1；跨重启恢复）----
    """
    CREATE TABLE IF NOT EXISTS pomodoro_session (
        id                INTEGER PRIMARY KEY CHECK (id = 1),
        phase             TEXT NOT NULL DEFAULT 'focus',
        remaining_seconds INTEGER NOT NULL DEFAULT 1500,
        running           INTEGER NOT NULL DEFAULT 0,
        completed_in_cycle INTEGER NOT NULL DEFAULT 0,
        updated_at        TEXT NOT NULL
    )
    """,

    # ---- todos：待办清单（P2）----
    # priority: 高/中/低；due_at: ISO 到期时间（可空）；done: 0/1
    """
    CREATE TABLE IF NOT EXISTS todos (
        id           TEXT PRIMARY KEY,
        text         TEXT NOT NULL,
        done         INTEGER NOT NULL DEFAULT 0,
        priority     TEXT NOT NULL DEFAULT '中',
        due_at       TEXT,
        created_at   TEXT NOT NULL,
        completed_at TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_todos_due ON todos(due_at)",

    # ---- review_cards：学习回顾间隔重复卡片（P3）----
    # ease: SM-2 难度系数；interval_days: 当前间隔；due_date: 下次到期；reps: 复习次数
    """
    CREATE TABLE IF NOT EXISTS review_cards (
        id              TEXT PRIMARY KEY,
        concept         TEXT NOT NULL,
        explanation     TEXT NOT NULL,
        source_event_id TEXT,
        ease            REAL NOT NULL DEFAULT 2.5,
        interval_days   INTEGER NOT NULL DEFAULT 0,
        due_date        TEXT NOT NULL,
        reps            INTEGER NOT NULL DEFAULT 0,
        created_at      TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_cards_due ON review_cards(due_date)",

    # ---- embeddings：RAG 语义检索向量块（问答升级阶段1）----
    # vector: JSON float 数组（BGE-small-zh 384 维）；换模型需按 model 重建索引
    """
    CREATE TABLE IF NOT EXISTS embeddings (
        id         TEXT PRIMARY KEY,
        event_id   TEXT,               -- 关联事件，可空
        card_id    TEXT,               -- 关联复习卡片，可空
        text       TEXT NOT NULL,      -- chunk 原文
        vector     TEXT NOT NULL,      -- JSON float 数组
        model      TEXT NOT NULL,      -- 模型标识
        created_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_emb_event ON embeddings(event_id)",

    # ---- chat_sessions：短期对话缓存（判断 Agent 输入源，不入 RAG）----
    """
    CREATE TABLE IF NOT EXISTS chat_sessions (
        id         TEXT PRIMARY KEY,   -- 会话 id
        turns      TEXT NOT NULL,      -- JSON 数组 [{role, content}]
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,

    # ---- pending_approvals：入库门控 + 沉淀确认的异步交互状态机----
    # kind: review_ingest（入库门控）/ memory_save（沉淀确认）
    # status: pending / approved / rejected / expired
    """
    CREATE TABLE IF NOT EXISTS pending_approvals (
        id         TEXT PRIMARY KEY,
        kind       TEXT NOT NULL,
        status     TEXT NOT NULL,
        payload    TEXT NOT NULL,      -- JSON（原文本/建议/候选卡片）
        source     TEXT NOT NULL,      -- screenshot / knowledge / qa / ...
        created_at TEXT NOT NULL,
        decided_at TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_pending_status ON pending_approvals(status)",
]

# ---- 旧库迁移：v1 pomodoro_records 无 phase/status 列，需 ALTER 补齐 ----
_MIGRATIONS: list[str] = [
    (
        "pomodoro_records",
        "phase",
        "ALTER TABLE pomodoro_records ADD COLUMN phase TEXT NOT NULL DEFAULT 'focus'",
    ),
    (
        "pomodoro_records",
        "status",
        "ALTER TABLE pomodoro_records ADD COLUMN status TEXT NOT NULL DEFAULT 'completed'",
    ),
]


async def init_schema(db: Database) -> None:
    """建表（IF NOT EXISTS，幂等）+ 旧库列迁移。"""
    for stmt in _SCHEMA_STATEMENTS:
        cur = await db.execute(stmt)
        await cur.close()
    await _run_migrations(db)
    await db.commit()
    log.info("数据库 schema 初始化完成")


async def _run_migrations(db: Database) -> None:
    """对已存在的旧表补齐新增列（检测列是否存在，避免重复 ALTER）。"""
    for table, column, alter_stmt in _MIGRATIONS:
        try:
            cur = await db.fetchall(f"PRAGMA table_info({table})")
        except Exception:
            continue
        cols = {str(row[1]) for row in cur}
        if column in cols:
            continue
        cur = await db.execute(alter_stmt)
        await cur.close()
        log.info("schema 迁移", detail={"table": table, "column": column})
