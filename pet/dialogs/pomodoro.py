"""番茄闹钟（PySide6 版）：完整 Pomodoro 状态机 + Qt 进度环 UI。

- TimerState 纯状态机**原样保留**（来自原 tkinter pomodoro.py，与 UI 解耦）；
- 进度环 + 分段 cycle 指示用 QPainter 绘制；
- 开始/暂停/跳过/重置/设置；focus 完成→后端记录，跳过→记录 interrupted；
- 会话跨重启持久化（关闭保存，打开恢复为暂停态）；
- 时长/每轮番茄数可配置，存后端加密库。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Callable

from PySide6.QtCore import Qt, QTimer, QRectF, QPointF, Signal, QThread
from PySide6.QtGui import QPainter, QColor, QPen, QBrush, QFont
from PySide6.QtWidgets import (
    QDialog, QWidget, QLabel, QVBoxLayout, QHBoxLayout, QPushButton, QSpinBox,
    QGridLayout,
)

from ..api_client import ApiClient
from ..widgets import theme as T

_PHASE_COLORS = ("focus", "short_break", "long_break")


def _phase_color(phase: str) -> str:
    """当前阶段强调色（随主题动态读取）。"""
    return {"focus": T.PHASE_FOCUS, "short_break": T.PHASE_SHORT,
            "long_break": T.PHASE_LONG}.get(phase, T.ACCENT)


_PHASE_LABEL = {
    "focus": "🍅 专注中",
    "short_break": "☕ 短休息",
    "long_break": "🌴 长休息",
}
_PHASE_DONE_NOTIFY = {
    "focus": "🍅 番茄完成！休息一下吧～",
    "short_break": "☕ 短休息结束，开始新的番茄！",
    "long_break": "🌴 一轮完成！好好放松一下～",
}

# 会话超过该时长视为过期（不自动恢复）
_SESSION_TTL_SECONDS = 12 * 3600
# session 每 N 次 tick 才落库一次，避免每秒一个 HTTP 请求
_SESSION_SAVE_EVERY = 10


# ===========================================================================
# 纯状态机（原样保留，与 UI 解耦）
# ===========================================================================
class TimerState:
    """Pomodoro 循环状态机。phase: focus/short_break/long_break。"""

    def __init__(self, durations: dict[str, int], laps: int) -> None:
        self.durations = durations          # {"focus": sec, ...}
        self.laps = max(1, laps)            # 每轮番茄数
        self.phase = "focus"
        self.remaining = durations["focus"]
        self.running = False
        self.completed = 0                  # 本轮已完成番茄数

    # ------------------------------------------------------------------
    @property
    def total(self) -> int:
        return self.durations[self.phase]

    @property
    def progress(self) -> float:
        total = self.total
        if total <= 0:
            return 0.0
        return min(max((total - self.remaining) / total, 0.0), 1.0)

    @property
    def duration_minutes(self) -> int:
        return round(self.total / 60)

    # ------------------------------------------------------------------
    def start(self) -> None:
        self.running = True

    def pause(self) -> None:
        self.running = False

    def toggle(self) -> bool:
        self.running = not self.running
        return self.running

    def tick(self) -> bool:
        """每秒减 1；到 0 自动进入下一阶段并返回 True。"""
        if not self.running:
            return False
        self.remaining -= 1
        if self.remaining <= 0:
            self._advance()
            return True
        return False

    def skip(self) -> str:
        """跳过当前阶段，返回被跳过的阶段（供中断记录）。"""
        prev = self.phase
        self._advance()
        return prev

    def reset_phase(self) -> None:
        """重置当前阶段（暂停）。"""
        self.remaining = self.total
        self.running = False

    def _advance(self) -> None:
        if self.phase == "focus":
            self.completed += 1
            if self.completed >= self.laps:
                self.phase = "long_break"
                self.completed = 0
            else:
                self.phase = "short_break"
        else:
            self.phase = "focus"
        self.remaining = self.durations[self.phase]

    # ------------------------------------------------------------------
    # cycle 分段展示（8 段：focus/break 交替，末尾长休息）
    # ------------------------------------------------------------------
    def cycle_segments(self) -> list[dict[str, Any]]:
        total = self.laps * 2
        current = self._cycle_index(total)
        segs = []
        for i in range(total):
            if i < current:
                state = "completed"
            elif i == current:
                state = "active"
            else:
                state = "pending"
            segs.append({"index": i, "state": state,
                         "is_focus": i % 2 == 0})
        return segs

    def _cycle_index(self, total: int) -> int:
        if self.phase == "long_break":
            return total - 1
        if self.phase == "short_break":
            return max(0, min(self.completed * 2 - 1, total - 1))
        return max(0, min(self.completed * 2, total - 1))


# ===========================================================================
# Qt UI
# ===========================================================================
class _RingWidget(QWidget):
    """进度环 + 中心时间 + 底部段点指示（QPainter 绘制）。"""

    def __init__(self, parent: QWidget | None) -> None:
        super().__init__(parent)
        self._progress = 0.0
        self._phase_color = T.PHASE_FOCUS
        self._time_text = "25:00"
        self._segments: list[dict[str, Any]] = []
        self.setFixedSize(180, 200)

    def set_state(self, progress: float, phase_color: str,
                  time_text: str, segments: list[dict[str, Any]]) -> None:
        self._progress = progress
        self._phase_color = phase_color
        self._time_text = time_text
        self._segments = segments
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        cx, cy = 90, 90
        pad = 12
        rect = QRectF(pad, pad, 180 - pad * 2, 180 - pad * 2)

        # 底环
        p.setPen(QPen(QColor(T.BORDER), 10))
        p.drawArc(rect, 90 * 16, -359.9 * 16)
        # 进度弧
        extent = -self._progress * 359.9
        p.setPen(QPen(QColor(self._phase_color), 10,
                      Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawArc(rect, 90 * 16, int(extent * 16))

        # 中心时间
        p.setPen(QColor(T.INK))
        f = QFont(T.F_MONO[0], 30)
        f.setBold(True)
        p.setFont(f)
        fm = p.fontMetrics()
        p.drawText(QPointF(cx - fm.horizontalAdvance(self._time_text) / 2,
                           cy + fm.ascent() / 2), self._time_text)

        # 段点指示
        segs = self._segments
        n = max(1, len(segs))
        gap = 24
        start_x = cx - (n - 1) * gap / 2
        y = 174
        for seg in segs:
            x = start_x + seg["index"] * gap
            if seg["state"] == "completed":
                fill = outline = self._phase_color
            elif seg["state"] == "active":
                fill, outline = self._phase_color, T.INK
            else:
                fill, outline = T.CARD, T.BORDER
            p.setPen(QPen(QColor(outline), 1))
            p.setBrush(QBrush(QColor(fill)))
            p.drawEllipse(QPointF(x, y), 5, 5)
        p.end()


class _ApiWorker(QThread):
    """单次后端调用工作线程。done(result, err) 回主线程。"""

    done = Signal(object, object)

    def __init__(self, fn, parent=None) -> None:
        super().__init__(parent)
        self._fn = fn

    def run(self) -> None:  # noqa: D102
        try:
            result = self._fn()
        except Exception as exc:  # noqa: BLE001
            self.done.emit(None, exc)
        else:
            self.done.emit(result, None)


class PomodoroDialog(QDialog):
    def __init__(self, notify: Callable[[str], None],
                 api: ApiClient, on_recorded: Callable[[], None] | None = None) -> None:
        super().__init__(None)
        self.notify = notify
        self.api = api
        self.on_recorded = on_recorded

        self._default_durations = {"focus": 25 * 60, "short_break": 5 * 60,
                                   "long_break": 15 * 60}
        self.state = TimerState(self._default_durations, 4)
        self._today_count = 0
        self._week_count = 0
        self._total_count = 0
        self._tick_count = 0
        self._session_loaded = False
        self._closed = False
        self._worker: _ApiWorker | None = None

        self.setWindowTitle("番茄闹钟")
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        self.resize(340, 420)
        self.setStyleSheet(f"background-color: {T.BG};")
        self._build_ui()
        self._update_display()
        self._load_initial()

    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 12, 12, 10)
        lay.setSpacing(6)

        self.phase_label = QLabel(_PHASE_LABEL[self.state.phase], self)
        self.phase_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.phase_label.setFont(T.font((T.F_SANS, 14, "bold")))
        lay.addWidget(self.phase_label)

        self.ring = _RingWidget(self)
        lay.addWidget(self.ring, 0, Qt.AlignmentFlag.AlignHCenter)

        self.stats_label = QLabel("今日 🍅 0 · 本周 0 · 累计 0", self)
        self.stats_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.stats_label.setStyleSheet(f"color: {T.INK2};")
        self.stats_label.setFont(T.font(T.F_SMALL))
        lay.addWidget(self.stats_label)

        # 按钮行 1：开始/跳过/重置
        btns = QHBoxLayout()
        btns.setSpacing(8)
        self.toggle_btn = self._button("▶ 开始", self._toggle, primary=True)
        btns.addWidget(self.toggle_btn)
        btns.addWidget(self._button("⏭ 跳过", self._skip))
        btns.addWidget(self._button("↺ 重置", self._reset))
        lay.addLayout(btns)

        # 按钮行 2：设置/关闭
        btns2 = QHBoxLayout()
        btns2.setSpacing(8)
        btns2.addWidget(self._button("⚙ 设置", self._open_settings))
        btns2.addWidget(self._button("✕ 关闭", self._close))
        lay.addLayout(btns2)

    def _button(self, text: str, command: Callable, *,
                primary: bool = False) -> QPushButton:
        btn = QPushButton(text, self)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        if primary:
            style = (f"QPushButton {{ background-color: {T.ACCENT}; color: {T.ON_ACCENT};"
                     f"  border: none; border-radius: 8px; padding: 6px 16px;"
                     f"  font-weight: bold; font-family: \"{T.F_SANS}\"; }}"
                     f"QPushButton:hover {{ background-color: {T.ACCENT_HOVER}; }}")
        else:
            style = (f"QPushButton {{ background-color: {T.CARD}; color: {T.INK2};"
                     f"  border: 1px solid {T.BORDER}; border-radius: 8px; padding: 6px 16px;"
                     f"  font-weight: bold; font-family: \"{T.F_SANS}\"; }}"
                     f"QPushButton:hover {{ background-color: {T.CARD_HOVER}; color: {T.INK}; }}")
        btn.setStyleSheet(style)
        btn.clicked.connect(lambda _c=False, fn=command: fn())
        return btn

    # ------------------------------------------------------------------
    # 初始加载：配置 + 统计 + 会话恢复（并行后台线程）
    # ------------------------------------------------------------------
    def _load_initial(self) -> None:
        self._run(self.api.pomodoro_config, self._apply_config)
        self._run(self.api.pomodoro_stats, self._apply_stats)
        self._run(self.api.pomodoro_session_get, self._apply_session)

    def _apply_config(self, result, err) -> None:
        cfg = result if (result and not err) else None
        if not cfg:
            return
        self._default_durations = {
            "focus": max(1, int(cfg.get("focus_minutes", 25))) * 60,
            "short_break": max(1, int(cfg.get("short_break_minutes", 5))) * 60,
            "long_break": max(1, int(cfg.get("long_break_minutes", 15))) * 60,
        }
        laps = max(1, int(cfg.get("laps_per_cycle", 4)))
        self.state.durations = dict(self._default_durations)
        self.state.laps = laps
        if not self._session_loaded:
            self.state.remaining = min(self.state.remaining, self.state.total)
        self._update_display()

    def _apply_stats(self, result, err) -> None:
        stats = result if (result and not err) else None
        if not stats:
            return
        self._today_count = int(stats.get("today", {}).get("count", 0))
        self._week_count = int(stats.get("week", {}).get("count", 0))
        self._total_count = int(stats.get("total", {}).get("count", 0))
        self._refresh_stats_label()

    def _apply_session(self, result, err) -> None:
        sess = result if (result and not err) else None
        if not sess:
            return
        try:
            updated = sess.get("updated_at") or ""
            if updated:
                updated_dt = datetime.fromisoformat(updated)
                age = datetime.now(updated_dt.tzinfo) - updated_dt
                if age.total_seconds() > _SESSION_TTL_SECONDS:
                    return  # 过期，不恢复
        except (ValueError, TypeError):
            return
        phase = sess.get("phase")
        if phase not in _PHASE_COLORS:
            return
        self.state.phase = phase
        self.state.remaining = max(1, int(sess.get("remaining_seconds", self.state.total)))
        self.state.completed = max(0, int(sess.get("completed_in_cycle", 0)))
        if self.state.phase == "short_break" and self.state.completed < 1:
            self.state.completed = 1  # 短休息必然紧随一个完成的番茄
        self.state.running = False
        self.state.remaining = min(self.state.remaining, self.state.total)
        self._session_loaded = True
        self._update_display()

    # ------------------------------------------------------------------
    # 计时
    # ------------------------------------------------------------------
    def _toggle(self) -> None:
        running = self.state.toggle()
        self.toggle_btn.setText("⏸ 暂停" if running else "▶ 继续")
        if running:
            self._schedule_tick()
        else:
            self._cancel_tick()
            self._save_session()

    def _schedule_tick(self) -> None:
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    def _cancel_tick(self) -> None:
        timer = getattr(self, "_timer", None)
        if timer is not None:
            timer.stop()
            timer.deleteLater()
            self._timer = None

    def _tick(self) -> None:
        prev_phase = self.state.phase
        if self.state.tick():
            self._on_phase_done(prev_phase)
        else:
            self._tick_count += 1
            self._update_display()
            if self._tick_count % _SESSION_SAVE_EVERY == 0:
                self._save_session()

    def _on_phase_done(self, done_phase: str) -> None:
        if done_phase == "focus":
            minutes = round(self._default_durations["focus"] / 60)
            self._record(minutes, "focus", "completed")
        self._update_display()
        self.notify(_PHASE_DONE_NOTIFY.get(done_phase, "时间到！"))
        self._save_session()

    def _skip(self) -> None:
        if self.state.phase == "focus":
            focus_minutes = round(self._default_durations["focus"] / 60)
        skipped = self.state.skip()
        if skipped == "focus":
            self._record(focus_minutes, "focus", "interrupted")
        self._update_display()
        self.notify("已跳过当前阶段")
        self._save_session()

    def _reset(self) -> None:
        self._cancel_tick()
        self.state.reset_phase()
        self.toggle_btn.setText("▶ 开始")
        self._update_display()
        self._save_session()

    def _close(self) -> None:
        self._cancel_tick()
        self._save_session()
        self._closed = True
        self.accept()

    # ------------------------------------------------------------------
    # 后端交互
    # ------------------------------------------------------------------
    def _record(self, minutes: int, phase: str, status: str) -> None:
        def done(result, err) -> None:
            if err or not result:
                return
            self._today_count = int(result.get("today_count", self._today_count))
            self._refresh_stats_label()
            if self.on_recorded:
                self.on_recorded()
        self._run(lambda: self.api.pomodoro_record(minutes, phase=phase, status=status),
                  done)

    def _save_session(self) -> None:
        if self._closed:
            return
        self._run(lambda: self.api.pomodoro_session_save(
            self.state.phase, self.state.remaining,
            self.state.running, self.state.completed), lambda _r, _e: None)

    def _run(self, fn, on_done) -> None:
        worker = _ApiWorker(fn, self)
        worker.done.connect(on_done)
        worker.done.connect(self._on_worker_done)
        worker.start()

    def _on_worker_done(self, *_args) -> None:
        # worker 在 lambda 局部引用，Qt 持有连接；无需清理栈
        pass

    # ------------------------------------------------------------------
    # 设置弹窗
    # ------------------------------------------------------------------
    def _open_settings(self) -> None:
        dlg = QDialog(self)
        dlg.setWindowTitle("番茄钟设置")
        dlg.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        dlg.resize(280, 220)
        dlg.setStyleSheet(f"background-color: {T.BG};")
        grid = QGridLayout(dlg)
        grid.setSpacing(8)

        fields = [
            ("专注时长（分钟）", "focus", 1, 120),
            ("短休息（分钟）", "short_break", 1, 120),
            ("长休息（分钟）", "long_break", 1, 120),
            ("每轮番茄数", "laps_per_cycle", 1, 12),
        ]
        spins: dict[str, QSpinBox] = {}
        for i, (label, key, lo, hi) in enumerate(fields):
            lbl = QLabel(label, dlg)
            lbl.setStyleSheet(f"color: {T.INK2};")
            lbl.setFont(T.font(T.F_SMALL))
            grid.addWidget(lbl, i, 0)
            spin = QSpinBox(dlg)
            spin.setRange(lo, hi)
            spin.setValue(self._default_durations_minutes(key))
            spin.setStyleSheet(f"background-color: {T.CARD}; color: {T.INK};")
            spins[key] = spin
            grid.addWidget(spin, i, 1)

        btns = QHBoxLayout()
        btns.setSpacing(8)

        def save() -> None:
            cfg = {
                "focus_minutes": spins["focus"].value(),
                "short_break_minutes": spins["short_break"].value(),
                "long_break_minutes": spins["long_break"].value(),
                "laps_per_cycle": spins["laps_per_cycle"].value(),
            }

            def done(result, err) -> None:
                if err or not result:
                    return
                self._default_durations = {
                    "focus": max(1, int(result["focus_minutes"])) * 60,
                    "short_break": max(1, int(result["short_break_minutes"])) * 60,
                    "long_break": max(1, int(result["long_break_minutes"])) * 60,
                }
                self.state.durations = dict(self._default_durations)
                self.state.laps = max(1, int(result["laps_per_cycle"]))
                self.state.remaining = min(self.state.remaining, self.state.total)
                self._update_display()
                dlg.accept()
            self._run(lambda: self.api.pomodoro_save_config(cfg), done)

        ok = self._button("保存", save, primary=True)
        cancel = self._button("取消", dlg.reject)
        btns.addWidget(ok)
        btns.addWidget(cancel)
        grid.addLayout(btns, len(fields), 0, 1, 2)
        dlg.exec()

    def _default_durations_minutes(self, key: str) -> int:
        if key == "laps_per_cycle":
            return self.state.laps
        return round(self._default_durations[key] / 60)

    # ------------------------------------------------------------------
    # 渲染
    # ------------------------------------------------------------------
    def _update_display(self) -> None:
        m, s = divmod(self.state.remaining, 60)
        color = _phase_color(self.state.phase)
        self.phase_label.setText(_PHASE_LABEL[self.state.phase])
        self.phase_label.setStyleSheet(f"color: {color};")
        self.ring.set_state(self.state.progress, color,
                            f"{m:02d}:{s:02d}", self.state.cycle_segments())

    def _refresh_stats_label(self) -> None:
        self.stats_label.setText(
            f"今日 🍅 {self._today_count} · 本周 {self._week_count} · 累计 {self._total_count}"
        )

    # ------------------------------------------------------------------
    # 兼容旧接口（供外部设置今日计数）
    # ------------------------------------------------------------------
    def set_today_count(self, count: int) -> None:
        self._today_count = max(0, int(count))
        self._refresh_stats_label()
