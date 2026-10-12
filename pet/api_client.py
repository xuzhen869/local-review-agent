"""后端 HTTP 客户端（宠物为瘦客户端，所有数据/LLM 逻辑在后端）。

阻塞式 urllib；调用方须放入工作线程（QThread）避免阻塞 Qt 事件循环。
"""
from __future__ import annotations

import io
import json
import os
import uuid
from typing import Any

import urllib.error
import urllib.parse
import urllib.request

from shared.contracts import (AuditList, CaptureAnalyzeResult, CaptureSolutionResult,
                              CaptureScreenshotResult, ClassifyResult, DeletedBool,
                              DeletedCount, DueCardList, EventList, FeedResult, Health,
                              IngestResult, JudgeResult, LearningExtractResult,
                              LlmKeySet, LlmStatus, OkResult, PendingDecideResult,
                              PendingList, PetChatReply, PetState, PomodoroConfig,
                              PomodoroRecordResult, PomodoroSession,
                              PomodoroSessionSave, PomodoroStats, PunchResult,
                              QaAnswer, ReportGenerateResult, ReportList, ReviewResult,
                              Sources, SourcesUpdateResult, SummaryToday, TodoItem,
                              TodoList, WeeklyReport)

BASE_URL = "http://127.0.0.1:8100"
_REQUEST_TIMEOUT = 60.0


class BackendError(RuntimeError):
    """后端不可达或业务错误。"""


class ApiClient:
    def __init__(self, base_url: str = BASE_URL) -> None:
        self.base = base_url.rstrip("/")
        # 本地 API 认证：launch 注入环境变量的 Bearer token
        token = os.environ.get("REVIEW_AGENT_TOKEN", "") or ""
        self._headers = {"Authorization": f"Bearer {token}"} if token else {}

    # ------------------------------------------------------------------
    def _get(self, path: str, timeout: float = _REQUEST_TIMEOUT) -> Any:
        req = urllib.request.Request(self.base + path, headers=self._headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as exc:
            raise BackendError(self._detail(exc)) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise BackendError(f"无法连接后端（{exc}）。请先运行 python run.py") from exc

    def _post_json(self, path: str, payload: dict, timeout: float = _REQUEST_TIMEOUT) -> Any:
        return self._request_json("POST", path, payload, timeout)

    def _put_json(self, path: str, payload: dict, timeout: float = _REQUEST_TIMEOUT) -> Any:
        return self._request_json("PUT", path, payload, timeout)

    def _delete_json(self, path: str, timeout: float = _REQUEST_TIMEOUT) -> Any:
        return self._request_json("DELETE", path, None, timeout)

    def _request_json(self, method: str, path: str, payload: dict | None,
                      timeout: float) -> Any:
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        headers = {"Content-Type": "application/json", **self._headers}
        req = urllib.request.Request(
            self.base + path, data=data, headers=headers, method=method,
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as exc:
            raise BackendError(self._detail(exc)) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise BackendError(f"无法连接后端（{exc}）。请先运行 python run.py") from exc

    def _post_image(self, path: str, image_bytes: bytes, filename: str) -> Any:
        boundary = "----lra" + uuid.uuid4().hex
        parts = io.BytesIO()
        parts.write(
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"image\"; "
            f"filename=\"{filename}\"\r\nContent-Type: image/png\r\n\r\n".encode("utf-8")
        )
        parts.write(image_bytes)
        parts.write(f"\r\n--{boundary}--\r\n".encode("utf-8"))
        headers = {
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            **self._headers,
        }
        req = urllib.request.Request(
            self.base + path, data=parts.getvalue(), headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=_REQUEST_TIMEOUT) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as exc:
            raise BackendError(self._detail(exc)) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise BackendError(f"无法连接后端（{exc}）。请先运行 python run.py") from exc

    @staticmethod
    def _detail(exc: urllib.error.HTTPError) -> str:
        try:
            body = json.loads(exc.read().decode("utf-8"))
            return str(body.get("detail", exc))
        except Exception:
            return str(exc)

    # ------------------------------------------------------------------
    def health(self) -> Health:
        return self._get("/api/health", timeout=5.0)

    # ------------------------------------------------------------------
    # 事件查询 / 数据源配置 / 审计（原 Web 仪表盘功能，现供桌面主窗口使用）
    # ------------------------------------------------------------------
    def get_events(self, source: str | None = None, category: str | None = None,
                   limit: int = 100, offset: int = 0) -> EventList:
        params = []
        if source:
            params.append(f"source={urllib.parse.quote(source)}")
        if category:
            params.append(f"category={urllib.parse.quote(category)}")
        params.append(f"limit={int(limit)}")
        if offset:
            params.append(f"offset={int(offset)}")
        return self._get(f"/api/events?{'&'.join(params)}", timeout=15.0)

    def delete_event(self, event_id: str) -> DeletedBool:
        """删除单条事件（级联清理向量索引）。"""
        return self._delete_json(f"/api/events/{event_id}", timeout=10.0)

    def delete_events(self) -> DeletedCount:
        """删除全部事件（级联清理向量索引）。"""
        return self._delete_json("/api/events", timeout=20.0)

    def get_sources(self) -> Sources:
        return self._get("/api/sources", timeout=5.0)

    def save_sources(self, sources: dict) -> SourcesUpdateResult:
        return self._post_json("/api/sources", {"sources": sources}, timeout=20.0)

    def get_audit(self, limit: int = 200) -> AuditList:
        return self._get(f"/api/audit?limit={int(limit)}", timeout=15.0)

    # ------------------------------------------------------------------
    # 事件分类 / 周报（P1）
    # ------------------------------------------------------------------
    def classify(self) -> ClassifyResult:
        return self._post_json("/api/classify", {}, timeout=120.0)

    def report_generate(self, week_start: str | None = None) -> ReportGenerateResult:
        path = "/api/report/weekly"
        if week_start:
            path += f"?week_start={urllib.parse.quote(week_start)}"
        return self._post_json(path, {}, timeout=120.0)

    def report_get(self, week_start: str | None = None) -> WeeklyReport:
        path = "/api/report/weekly"
        if week_start:
            path += f"?week_start={urllib.parse.quote(week_start)}"
        return self._get(path, timeout=15.0)

    def reports_list(self) -> ReportList:
        return self._get("/api/reports", timeout=10.0)

    def qa_ask(self, question: str, session_id: str | None = None) -> QaAnswer:
        payload: dict = {"question": question}
        if session_id:
            payload["session_id"] = session_id
        return self._post_json("/api/qa/ask", payload, timeout=90.0)

    # ------------------------------------------------------------------
    # 问答升级（阶段 3/4）：沉淀判断 / 挂起确认 / 问答框录入
    # ------------------------------------------------------------------
    def qa_judge(self, session_id: str) -> JudgeResult:
        """判断 Agent：独立判断短期对话是否值得沉淀为复习卡片。"""
        return self._post_json("/api/qa/judge", {"session_id": session_id}, timeout=60.0)

    def pending_list(self, kind: str | None = None, status: str | None = None) -> PendingList:
        """查询挂起确认项（kind/status 过滤，新→旧）。"""
        params = []
        if kind:
            params.append(f"kind={urllib.parse.quote(kind)}")
        if status:
            params.append(f"status={urllib.parse.quote(status)}")
        return self._get(f"/api/pending?{'&'.join(params)}", timeout=10.0)

    def pending_decide(self, pending_id: str, action: str) -> PendingDecideResult:
        """处理挂起项：approve 系列落库 / reject 丢弃。"""
        return self._post_json(
            "/api/pending/decide", {"pending_id": pending_id, "action": action},
            timeout=20.0)

    def knowledge_ingest(self, text: str) -> IngestResult:
        """问答框录入（阶段4）：路由门控 → 入库或 pending。"""
        return self._post_json("/api/knowledge/ingest", {"text": text}, timeout=20.0)

    # ------------------------------------------------------------------
    # 宠物 AI 对话（P2）
    # ------------------------------------------------------------------
    def pet_chat(self, message: str, history: list | None = None) -> PetChatReply:
        return self._post_json(
            "/api/pet/chat", {"message": message, "history": history or []},
            timeout=60.0,
        )

    # ------------------------------------------------------------------
    # 待办清单（P2）
    # ------------------------------------------------------------------
    def todos_list(self, include_done: bool = True) -> TodoList:
        return self._get(
            f"/api/todos?include_done={'true' if include_done else 'false'}", timeout=10.0)

    def todos_due(self, minutes: int = 60) -> TodoList:
        return self._get(f"/api/todos/due?minutes={int(minutes)}", timeout=10.0)

    def todo_create(self, text: str, priority: str = "中",
                    due_at: str | None = None) -> TodoItem:
        payload: dict = {"text": text, "priority": priority}
        if due_at:
            payload["due_at"] = due_at
        return self._post_json("/api/todos", payload, timeout=10.0)

    def todo_update(self, todo_id: str, *, done: bool | None = None,
                    text: str | None = None, priority: str | None = None,
                    due_at: str | None = None) -> TodoItem:
        payload: dict = {}
        if done is not None:
            payload["done"] = done
        if text is not None:
            payload["text"] = text
        if priority is not None:
            payload["priority"] = priority
        if due_at is not None:
            payload["due_at"] = due_at
        return self._put_json(f"/api/todos/{todo_id}", payload, timeout=10.0)

    def todo_delete(self, todo_id: str) -> OkResult:
        return self._delete_json(f"/api/todos/{todo_id}", timeout=10.0)

    # ------------------------------------------------------------------
    # 学习回顾间隔重复（P3）
    # ------------------------------------------------------------------
    def learning_extract(self) -> LearningExtractResult:
        return self._post_json("/api/learning/extract", {}, timeout=90.0)

    def learning_due(self, limit: int = 20) -> DueCardList:
        return self._get(f"/api/learning/due?limit={int(limit)}", timeout=10.0)

    def learning_review(self, card_id: str, grade: str) -> ReviewResult:
        return self._post_json(f"/api/learning/review/{card_id}", {"grade": grade},
                               timeout=10.0)

    def llm_status(self) -> LlmStatus:
        return self._get("/api/llm/status", timeout=5.0)

    def set_llm_key(self, key: str) -> LlmKeySet:
        return self._post_json("/api/llm/key", {"key": key}, timeout=10.0)

    def capture_knowledge(self, text: str) -> IngestResult:
        return self._post_json("/api/capture/knowledge", {"text": text}, timeout=15.0)

    def capture_screenshot(self, png_bytes: bytes) -> CaptureScreenshotResult:
        return self._post_image("/api/capture/screenshot", png_bytes, "shot.png")

    def capture_analyze(self, png_bytes: bytes) -> CaptureAnalyzeResult:
        """一键截图：保存 PNG + OCR + 判断 Agent 生成方案（不自动入库）。"""
        return self._post_image("/api/capture/analyze", png_bytes, "shot.png")

    def capture_solution(self, text: str, path_rel: str = "",
                         mode: str = "raw") -> CaptureSolutionResult:
        """截图方案确认入库：方案文本 + 关联截图相对路径 + 来源模式。

        mode: "ai"=采用 AI 修改方案 / "raw"=原样入库原文。
        """
        return self._post_json(
            "/api/capture/solution",
            {"text": text, "path_rel": path_rel, "mode": mode}, timeout=20.0)

    def summary_today(self) -> SummaryToday:
        return self._get("/api/summary/today", timeout=90.0)

    # ------------------------------------------------------------------
    # 宠物喂养 / 番茄钟
    # ------------------------------------------------------------------
    def pet_state(self) -> PetState:
        return self._get("/api/pet/state", timeout=5.0)

    def pet_punch(self) -> PunchResult:
        return self._post_json("/api/pet/punch", {}, timeout=10.0)

    def pet_feed(self) -> FeedResult:
        return self._post_json("/api/pet/feed", {}, timeout=10.0)

    def pet_pomodoro(self, minutes: int) -> PomodoroRecordResult:
        return self._post_json("/api/pet/pomodoro", {"minutes": minutes}, timeout=10.0)

    # ------------------------------------------------------------------
    # 番茄钟（完整状态机：配置 / 记录 / 统计 / 会话）
    # ------------------------------------------------------------------
    def pomodoro_config(self) -> PomodoroConfig:
        return self._get("/api/pomodoro/config", timeout=5.0)

    def pomodoro_save_config(self, cfg: dict) -> PomodoroConfig:
        return self._post_json("/api/pomodoro/config", cfg, timeout=10.0)

    def pomodoro_record(self, minutes: int, phase: str = "focus",
                        status: str = "completed") -> PomodoroRecordResult:
        return self._post_json(
            "/api/pomodoro/record",
            {"minutes": minutes, "phase": phase, "status": status},
            timeout=10.0,
        )

    def pomodoro_stats(self) -> PomodoroStats:
        return self._get("/api/pomodoro/stats", timeout=5.0)

    def pomodoro_session_get(self) -> PomodoroSession:
        return self._get("/api/pomodoro/session", timeout=5.0)

    def pomodoro_session_save(self, phase: str, remaining_seconds: int,
                              running: bool, completed_in_cycle: int) -> PomodoroSessionSave:
        return self._post_json(
            "/api/pomodoro/session",
            {"phase": phase, "remaining_seconds": remaining_seconds,
             "running": running, "completed_in_cycle": completed_in_cycle},
            timeout=10.0,
        )
