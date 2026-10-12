"""前后端共享契约（第 1+2 课）：API 返回结构的唯一事实源。

- 后端：路由挂对应模型的 response_model，返回结构缺字段/类型错 → 当场报错；
- 前端：api_client 返回对应类型，不再 `Any` 全靠猜；
- 本文件是唯一权威：改契约只改这里，两端同时生效（禁止另存拷贝）。

覆盖范围：
  1. 番茄钟 / 宠物状态（petstate.py 真实返回）；
  2. 数据源配置 Sources（config.py 的唯一事实源，config 从这里 import）；
  3. 全部 HTTP API 路由的顶层返回结构（main.py 每个路由一个 response_model）。
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


# ===========================================================================
# 番茄钟 / 宠物状态（petstate.py 真实返回）
# ===========================================================================
class PomodoroConfig(BaseModel):
    """GET/POST /api/pomodoro/config —— 番茄钟配置（petstate.py:171）。"""
    focus_minutes: int
    short_break_minutes: int
    long_break_minutes: int
    laps_per_cycle: int


class PomodoroSession(BaseModel):
    """GET /api/pomodoro/session —— 进行中的番茄状态（petstate.py:219）。"""
    phase: str
    remaining_seconds: int
    running: bool
    completed_in_cycle: int
    updated_at: str


class PetState(BaseModel):
    """GET /api/pet/state —— 宠物实时状态（petstate.py:89）。"""
    hunger: int
    hunger_max: int
    food: int
    punched_today: bool
    today_pomodoro: int
    mood: str


class PomodoroCount(BaseModel):
    """嵌套：仅计数+分钟（today / total 没有 date 字段）。"""
    count: int
    minutes: int


class PomodoroDay(BaseModel):
    """嵌套：带日期的单日统计（last_14_days 数组里的一项）。"""
    date: str
    count: int
    minutes: int


class PomodoroStatsWeek(BaseModel):
    """嵌套：本周统计（比 PomodoroDay 多一个 week_start）。"""
    count: int
    minutes: int
    week_start: str


class PhaseDist(BaseModel):
    """嵌套：阶段分布。"""
    focus: int
    short_break: int
    long_break: int


class PomodoroStats(BaseModel):
    """GET /api/pomodoro/stats —— 汇总统计（petstate.py:354，嵌套 + 数组）。"""
    today: PomodoroCount
    week: PomodoroStatsWeek
    total: PomodoroCount
    last_14_days: list[PomodoroDay]
    phase_dist: PhaseDist
    interrupted: int
    current_streak_days: int


# ===========================================================================
# 数据源配置（唯一事实源：config.py 从这里 import，main.py 挂 response_model）
# ===========================================================================
class GitSources(BaseModel):
    """git 数据源：仓库绝对路径列表。"""
    repos: list[str] = Field(default_factory=list)


class FsSources(BaseModel):
    """文档数据源：监控目录列表。"""
    dirs: list[str] = Field(default_factory=list)
    debounce_seconds: float = Field(default=5.0, ge=0.5, le=120)
    poll_interval_seconds: int = Field(default=300, ge=30)
    max_file_bytes: int = Field(default=5 * 1024 * 1024, ge=0)
    ignore_exts: list[str] = Field(
        default_factory=lambda: [".tmp", ".swp", ".lock", ".partial"])
    ignore_dirs: list[str] = Field(
        default_factory=lambda: [".git", ".venv", "node_modules", "__pycache__",
                                 "venv", "dist", "build"])


class IcsSources(BaseModel):
    """日历数据源（M2 预留）。"""
    files: list[str] = Field(default_factory=list)
    urls: list[str] = Field(default_factory=list)


class ImapSources(BaseModel):
    """邮件数据源（M2 预留；密码入 secrets 表，禁止入 config.yaml）。"""
    enabled: bool = False
    server: str = ""
    port: int = Field(default=993, ge=1, le=65535)
    username: str = ""
    ssl: bool = True
    poll_interval_seconds: int = Field(default=300, ge=60)


class ClipboardSources(BaseModel):
    """剪贴板数据源（默认关闭，高风险源）。"""
    enabled: bool = False
    poll_interval_seconds: float = Field(default=2.0, ge=0.5)
    daily_limit: int = Field(default=200, ge=0)


class Sources(BaseModel):
    """数据源集合（GET /api/sources 返回体，POST 提交体复用 config）。"""
    git: GitSources = Field(default_factory=GitSources)
    fs: FsSources = Field(default_factory=FsSources)
    ics: IcsSources = Field(default_factory=IcsSources)
    imap: ImapSources = Field(default_factory=ImapSources)
    clipboard: ClipboardSources = Field(default_factory=ClipboardSources)


class SourcesUpdateResult(BaseModel):
    """POST /api/sources —— 保存结果（status 为各适配器最终状态 {name: state}）。"""
    status: dict[str, str] = Field(default_factory=dict)
    sources: Sources = Field(default_factory=Sources)


# ===========================================================================
# 运维探针 / 事件查询 / 审计
# ===========================================================================
class Health(BaseModel):
    """GET /api/health —— 运维探针。"""
    status: str
    db_connected: bool
    adapters: dict[str, dict[str, Any]] = Field(default_factory=dict)
    queue_size: int = 0
    dropped_count: int = 0
    event_count: int = 0
    data_dir_basename: str = ""
    disk_free_bytes: int = 0
    recovery_backup_exists: bool = False
    has_sources: bool = False


class EventItem(BaseModel):
    """事件条目（query_events 已脱敏 content / redact source_ref 后的出口结构）。"""
    id: str
    source: str
    source_ref: str
    ts: str
    category: str | None = None
    grade: str = "internal"
    content: str = ""
    keywords: str | None = None
    needs_review: bool = False
    degraded: bool = False


class EventList(BaseModel):
    """GET /api/events —— 事件分页列表。"""
    total: int
    items: list[EventItem] = Field(default_factory=list)


class DeletedCount(BaseModel):
    """DELETE /api/events —— 删除全部事件条数。"""
    deleted: int


class DeletedBool(BaseModel):
    """DELETE /api/events/{id} —— 删除单条事件结果。"""
    deleted: bool


class AuditItem(BaseModel):
    """审计日志条目（detail 已脱敏）。"""
    ts: str
    actor: str
    action: str
    object_type: str | None = None
    object_id: str | None = None
    result: str = "ok"
    detail: Any = None


class AuditList(BaseModel):
    """GET /api/audit —— 审计列表。"""
    total: int
    items: list[AuditItem] = Field(default_factory=list)


# ===========================================================================
# 事件分类（P1）
# ===========================================================================
class ClassifyResult(BaseModel):
    """POST /api/classify —— 批量打标统计（空批次无 degraded 字段）。"""
    processed: int = 0
    classified: int = 0
    rule_fallback: int = 0
    failed: int = 0
    degraded: bool = False


# ===========================================================================
# 周报（P1）
# ===========================================================================
class ReportMetrics(BaseModel):
    """周报指标（report.py:_agg_metrics）。by_* 为动态键 dict。"""
    event_count: int = 0
    by_source: dict[str, int] = Field(default_factory=dict)
    by_category: dict[str, int] = Field(default_factory=dict)
    commit_count: int = 0
    pomodoro_count: int = 0
    pomodoro_minutes: int = 0


class ReportItem(BaseModel):
    """周报结论里带溯源的一条（成就/阻塞/下周计划）。"""
    text: str = ""
    source_event_ids: list[str] = Field(default_factory=list)


class ReportConclusion(BaseModel):
    """周报 LLM 结论（report.py:_parse_conclusion）。"""
    mainline: str = ""
    achievements: list[ReportItem] = Field(default_factory=list)
    blockers: list[ReportItem] = Field(default_factory=list)
    next_week: list[ReportItem] = Field(default_factory=list)


class TimelineItem(BaseModel):
    """时间线里的一天中的一条事件（report.py:_agg_timeline）。"""
    id: str
    ts: str
    source: str
    category: str | None = None
    content: str = ""


class WeeklyReport(BaseModel):
    """GET /api/report/weekly —— 已落库的周报全量。"""
    week_start: str
    week_end: str
    metrics: ReportMetrics
    timeline: dict[str, list[TimelineItem]] = Field(default_factory=dict)
    categories: dict[str, int] = Field(default_factory=dict)
    learnings: Any = None
    conclusion: ReportConclusion = Field(default_factory=ReportConclusion)


class ReportGenerateResult(BaseModel):
    """POST /api/report/weekly —— 生成结果（空周缺 metrics/conclusion）。"""
    week_start: str
    week_end: str
    generated: bool
    reason: str | None = None
    metrics: ReportMetrics | None = None
    conclusion: ReportConclusion | None = None
    degraded: bool = False


class ReportListItem(BaseModel):
    """GET /api/reports —— 周报列表项。"""
    week_start: str
    week_end: str
    metrics: ReportMetrics
    conclusion: ReportConclusion = Field(default_factory=ReportConclusion)
    created_at: str = ""


class ReportList(BaseModel):
    """GET /api/reports —— 周报列表。"""
    total: int
    items: list[ReportListItem] = Field(default_factory=list)


# ===========================================================================
# 问答（P1）
# ===========================================================================
class QaSource(BaseModel):
    """问答溯源条目。"""
    id: str
    ts: str = ""
    source: str
    category: str | None = None
    content: str = ""


class WebSource(BaseModel):
    """联网搜索溯源条目。"""
    title: str = ""
    url: str = ""
    snippet: str = ""
    source: str = "web"


class QaAnswer(BaseModel):
    """POST /api/qa/ask —— 问答结果（无记录/无 key/失败分支字段可选）。"""
    answer: str | None = None
    sources: list[QaSource] = Field(default_factory=list)
    events_count: int = 0
    need_key: bool = False
    session_id: str = ""
    web_sources: list[WebSource] = Field(default_factory=list)
    semantic_used: bool = False
    message: str | None = None
    error: str | None = None


class ReindexResult(BaseModel):
    """POST /api/qa/reindex —— 向量索引重建结果。"""
    indexed: int = 0
    events: int = 0
    need_model: bool = False
    message: str | None = None


# ===========================================================================
# 沉淀判断 / 挂起确认（阶段 3）
# ===========================================================================
class Concept(BaseModel):
    """候选复习概念（判断 Agent 输出 / pending payload）。"""
    concept: str
    explanation: str


class JudgeResult(BaseModel):
    """POST /api/qa/judge —— 沉淀判断结果。"""
    has_memory: bool = False
    need_key: bool = False
    pending_id: str | None = None
    concepts: list[Concept] = Field(default_factory=list)
    reason: str = ""
    reused: bool = False
    message: str | None = None
    error: str | None = None


class PendingItem(BaseModel):
    """GET /api/pending —— 挂起确认项。"""
    id: str
    kind: str
    status: str
    payload: dict[str, Any] = Field(default_factory=dict)
    source: str = ""
    created_at: str = ""
    decided_at: str | None = None


class PendingList(BaseModel):
    """GET /api/pending —— 挂起确认列表。"""
    items: list[PendingItem] = Field(default_factory=list)
    count: int = 0


class PendingDecideResult(BaseModel):
    """POST /api/pending/decide —— 决策结果（approve 系列追加写入统计）。"""
    id: str
    kind: str
    action: str
    status: str
    payload: dict[str, Any] = Field(default_factory=dict)
    inserted: int | None = None
    indexed: int | None = None
    event_id: str | None = None


# ===========================================================================
# 宠物对话 / 待办（P2）
# ===========================================================================
class PetChatReply(BaseModel):
    """POST /api/pet/chat —— 宠物对话回复。"""
    reply: str
    need_key: bool = False


class TodoItem(BaseModel):
    """待办条目（text 已脱敏）。"""
    id: str
    text: str
    done: bool
    priority: str = "中"
    due_at: str | None = None
    created_at: str
    completed_at: str | None = None


class TodoList(BaseModel):
    """GET /api/todos、/api/todos/due —— 待办列表。"""
    items: list[TodoItem] = Field(default_factory=list)


class OkResult(BaseModel):
    """通用「成功」响应（如 DELETE /api/todos/{id}）。"""
    ok: bool = True


# ===========================================================================
# 学习回顾间隔重复（P3）
# ===========================================================================
class LearningExtractResult(BaseModel):
    """POST /api/learning/extract —— 知识卡片提取结果。"""
    extracted: int = 0
    found: int = 0
    need_key: bool = False
    message: str | None = None
    error: str | None = None


class DueCard(BaseModel):
    """GET /api/learning/due —— 到期复习卡片。"""
    id: str
    concept: str
    explanation: str
    source_event_id: str | None = None
    ease: float
    interval_days: int
    due_date: str
    reps: int


class DueCardList(BaseModel):
    """GET /api/learning/due —— 到期卡片列表。"""
    items: list[DueCard] = Field(default_factory=list)


class ReviewResult(BaseModel):
    """POST /api/learning/review/{id} —— SM-2 复习更新结果。"""
    card_id: str
    grade: str
    grade_label: str
    ease: float
    interval_days: int
    reps: int
    due_date: str


# ===========================================================================
# 截图捕获 / 手动录入
# ===========================================================================
class CaptureScreenshotResult(BaseModel):
    """POST /api/capture/screenshot —— 截图 + OCR + 提纯 + 入库（三分支）。"""
    id: str = ""
    ocr_text: str = ""
    purified_text: str = ""
    path: str = ""
    path_rel: str = ""
    inserted: bool = False
    ocr_available: bool = False
    gated: bool = False
    verdict: str = "skip"
    degraded: bool = False
    pending_id: str | None = None
    concern: str | None = None
    message: str | None = None


class CaptureAnalyzeResult(BaseModel):
    """POST /api/capture/analyze —— 一键截图 + 方案生成。"""
    path_rel: str
    ocr_text: str = ""
    solution: str = ""
    degraded: bool = False
    ocr_available: bool = False


class CaptureSolutionResult(BaseModel):
    """POST /api/capture/solution —— 方案确认入库。"""
    id: str
    inserted: bool
    mode: str = "raw"


class IngestResult(BaseModel):
    """POST /api/capture/knowledge、/api/knowledge/ingest —— 手动录入结果。"""
    id: str | None = None
    inserted: bool = False
    gated: bool = False
    verdict: str = "clear"
    pending_id: str | None = None
    concern: str | None = None
    solution: str | None = None


# ===========================================================================
# 今日小结 / LLM 状态
# ===========================================================================
class SummaryToday(BaseModel):
    """GET /api/summary/today —— 今日小结（无 key / 失败分支 summary=None）。"""
    summary: str | None = None
    events_count: int = 0
    events: list[EventItem] = Field(default_factory=list)
    need_key: bool = False
    error: str | None = None


class LlmStatus(BaseModel):
    """GET /api/llm/status —— LLM 配置状态。"""
    configured: bool
    model: str = "deepseek-chat"


class LlmKeySet(BaseModel):
    """POST /api/llm/key —— key 录入结果。"""
    configured: bool = True


# ===========================================================================
# 宠物喂养 / 番茄钟写操作
# ===========================================================================
class PunchResult(BaseModel):
    """POST /api/pet/punch —— 每日打卡结果。"""
    punched_today: bool
    food: int
    reward: int


class FeedResult(BaseModel):
    """POST /api/pet/feed —— 喂食结果。"""
    hunger: int
    food: int
    recovery: int


class PomodoroRecordResult(BaseModel):
    """POST /api/pomodoro/record、/api/pet/pomodoro —— 番茄钟记录结果。"""
    recorded: bool
    minutes: int
    phase: str
    status: str
    today_count: int
    today_minutes: int


class PomodoroSessionSave(BaseModel):
    """POST /api/pomodoro/session —— 会话保存结果（比 PomodoroSession 多 saved）。"""
    saved: bool
    phase: str
    remaining_seconds: int
    running: bool
    completed_in_cycle: int
