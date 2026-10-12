"""番茄闹钟：完整 Pomodoro 状态机（设计移植自 GitHub 优秀项目 Cadence/pomo）。

- 状态机：focus → short_break → focus → … → 第 N 个 focus → long_break（N 可配置）；
- 进度环 + 分段 cycle 指示；开始/暂停/跳过/重置/设置；
- focus 自然完成 → 后端记录 completed；跳过 focus → 记录 interrupted；
- 会话跨重启持久化（关闭保存，打开恢复为暂停态，用户手动继续）；
- 时长/每轮番茄数可配置，配置存后端加密库。
"""
from __future__ import annotations

import threading
import tkinter as tk
from typing import Any, Callable

from . import theme as T

_PHASE_COLORS = ("focus", "short_break", "long_break")


def _phase_color(phase: str) -> str:
    """当前阶段强调色（随主题动态读取，避免模块加载时快照旧 token）。"""
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
_BG = T.BG
_INK = T.INK
_INK_DIM = T.INK2
_INK_FAINT = T.MUTED
_BTN_BG = T.CARD
_BTN_ACTIVE = T.CARD_HOVER


def _refresh_colors() -> None:
    """刷新颜色缓存（主题切换后新开弹窗即用新主题）。"""
    global _BG, _INK, _INK_DIM, _INK_FAINT, _BTN_BG, _BTN_ACTIVE
    _BG = T.BG
    _INK = T.INK
    _INK_DIM = T.INK2
    _INK_FAINT = T.MUTED
    _BTN_BG = T.CARD
    _BTN_ACTIVE = T.CARD_HOVER

# 会话超过该时长视为过期（不自动恢复）
_SESSION_TTL_SECONDS = 12 * 3600
# session 每 N 次 tick 才落库一次，避免每秒一个 HTTP 请求
_SESSION_SAVE_EVERY = 10


# ===========================================================================
# 纯状态机（与 UI 解耦）
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
# UI
# ===========================================================================
class PomodoroDialog:
    def __init__(self, root: tk.Tk, notify: Callable[[str], None],
                 api: Any, on_recorded: Callable[[], None] | None = None) -> None:
        self.root = root
        self.notify = notify
        self.api = api
        self.on_recorded = on_recorded

        self._default_durations = {"focus": 25 * 60, "short_break": 5 * 60, "long_break": 15 * 60}
        self.state = TimerState(self._default_durations, 4)
        self._today_count = 0
        self._week_count = 0
        self._total_count = 0
        self._after_id: str | None = None
        self._tick_count = 0
        self._session_loaded = False
        self._closed = False

        _refresh_colors()  # 确保用当前主题 token
        self.win = tk.Toplevel(root)
        self.win.title("番茄闹钟")
        self.win.geometry("340x400")
        self.win.configure(bg=_BG)
        self.win.attributes("-topmost", True)
        # 注：不能 transient(root) —— 主 root withdraw 隐藏时 transient 子窗口不可见
        self.win.protocol("WM_DELETE_WINDOW", self._close)
        T.apply_window_material(self.win, acrylic=True)

        self._build_ui()
        self._update_display()
        self._load_initial()

    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        self.phase_label = tk.Label(self.win, text=_PHASE_LABEL[self.state.phase],
                                    bg=_BG, fg=_phase_color(self.state.phase),
                                    font=(T.F_SANS, 14, "bold"))
        self.phase_label.pack(pady=(14, 4))

        # 进度环
        self.canvas = tk.Canvas(self.win, width=180, height=180, bg=_BG,
                                highlightthickness=0)
        self.canvas.pack()
        self._cx, self._cy = 90, 90
        self._ring_pad = 12
        self._time_id: int | None = None

        # 今日/本周/累计
        self.stats_label = tk.Label(self.win, text="今日 🍅 0 · 本周 0 · 累计 0",
                                    bg=_BG, fg=_INK_DIM, font=(T.F_SANS, 10))
        self.stats_label.pack(pady=(2, 2))

        # 按钮区
        btns = tk.Frame(self.win, bg=_BG)
        btns.pack(pady=(6, 4))
        self.toggle_btn = self._button(btns, "▶ 开始", self._toggle, primary=True)
        self.toggle_btn.pack(side="left", padx=4)
        self._button(btns, "⏭ 跳过", self._skip).pack(side="left", padx=4)
        self._button(btns, "↺ 重置", self._reset).pack(side="left", padx=4)

        btns2 = tk.Frame(self.win, bg=_BG)
        btns2.pack(pady=(2, 8))
        self._button(btns2, "⚙ 设置", self._open_settings).pack(side="left", padx=4)
        self._button(btns2, "✕ 关闭", self._close).pack(side="left", padx=4)

        self._draw_ring()
        self._draw_segments()

    def _button(self, parent, text, command, *, primary: bool = False) -> tk.Button:
        return tk.Button(
            parent, text=text, command=command,
            bg=T.ACCENT if primary else _BTN_BG,
            fg=T.ON_ACCENT if primary else _INK_DIM,
            relief="flat", activebackground=T.ACCENT_HOVER if primary else _BTN_ACTIVE,
            activeforeground=T.ON_ACCENT, padx=14, pady=6,
            font=(T.F_SANS, 10, "bold"), cursor="hand2",
        )

    # ------------------------------------------------------------------
    # 初始加载：配置 + 统计 + 会话恢复（并行后台线程）
    # ------------------------------------------------------------------
    def _load_initial(self) -> None:
        def load_config() -> None:
            try:
                cfg = self.api.pomodoro_config()
            except Exception:
                cfg = None
            self.win.after(0, lambda: self._apply_config(cfg))

        def load_stats() -> None:
            try:
                stats = self.api.pomodoro_stats()
            except Exception:
                stats = None
            self.win.after(0, lambda: self._apply_stats(stats))

        def load_session() -> None:
            try:
                sess = self.api.pomodoro_session_get()
            except Exception:
                sess = None
            self.win.after(0, lambda: self._apply_session(sess))

        for fn in (load_config, load_stats, load_session):
            threading.Thread(target=fn, daemon=True).start()

    def _apply_config(self, cfg: dict | None) -> None:
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
            # 会话恢复以配置为准对齐剩余时间
            self.state.remaining = min(self.state.remaining, self.state.total)
        self._update_display()
        self._draw_segments()

    def _apply_stats(self, stats: dict | None) -> None:
        if not stats:
            return
        self._today_count = int(stats.get("today", {}).get("count", 0))
        self._week_count = int(stats.get("week", {}).get("count", 0))
        self._total_count = int(stats.get("total", {}).get("count", 0))
        self._refresh_stats_label()

    def _apply_session(self, sess: dict | None) -> None:
        if not sess:
            return
        try:
            updated = sess.get("updated_at") or ""
            if updated:
                from datetime import datetime
                updated_dt = datetime.fromisoformat(updated)
                from datetime import timedelta
                age = datetime.now(updated_dt.tzinfo) - updated_dt
                if age.total_seconds() > _SESSION_TTL_SECONDS:
                    return  # 过期，不恢复
        except (ValueError, TypeError):
            return
        phase = sess.get("phase")
        if phase not in _PHASE_COLORS:
            return
        # 恢复进行中番茄为「暂停态」，由用户手动继续，避免静默倒计时
        self.state.phase = phase
        self.state.remaining = max(1, int(sess.get("remaining_seconds", self.state.total)))
        self.state.completed = max(0, int(sess.get("completed_in_cycle", 0)))
        if self.state.phase == "short_break" and self.state.completed < 1:
            self.state.completed = 1  # 短休息必然紧随一个完成的番茄
        self.state.running = False
        self.state.remaining = min(self.state.remaining, self.state.total)
        self._session_loaded = True
        self._update_display()
        self._draw_segments()

    # ------------------------------------------------------------------
    # 计时
    # ------------------------------------------------------------------
    def _toggle(self) -> None:
        running = self.state.toggle()
        self.toggle_btn.config(text="⏸ 暂停" if running else "▶ 继续")
        if running:
            self._schedule_tick()
        else:
            self._cancel_tick()
            self._save_session()

    def _schedule_tick(self) -> None:
        self._after_id = self.root.after(1000, self._tick)

    def _cancel_tick(self) -> None:
        if self._after_id:
            self.root.after_cancel(self._after_id)
            self._after_id = None

    def _tick(self) -> None:
        prev_phase = self.state.phase
        if self.state.tick():
            self._on_phase_done(prev_phase)
        else:
            self._tick_count += 1
            self._update_display()
            if self._tick_count % _SESSION_SAVE_EVERY == 0:
                self._save_session()
            if self.state.running:
                self._schedule_tick()

    def _on_phase_done(self, done_phase: str) -> None:
        if done_phase == "focus":
            minutes = round(self._default_durations["focus"] / 60)
            self._record(minutes, "focus", "completed")
        self._update_display()
        self._draw_segments()
        self.notify(_PHASE_DONE_NOTIFY.get(done_phase, "时间到！"))
        self._save_session()
        if self.state.running:
            self._schedule_tick()

    def _skip(self) -> None:
        if self.state.phase == "focus":
            focus_minutes = round(self._default_durations["focus"] / 60)
        skipped = self.state.skip()
        if skipped == "focus":
            self._record(focus_minutes, "focus", "interrupted")
        self._update_display()
        self._draw_segments()
        self.notify("已跳过当前阶段")
        self._save_session()
        if self.state.running:
            self._schedule_tick()

    def _reset(self) -> None:
        self._cancel_tick()
        self.state.reset_phase()
        self.toggle_btn.config(text="▶ 开始")
        self._update_display()
        self._draw_segments()
        self._save_session()

    def _close(self) -> None:
        self._cancel_tick()
        self._save_session()
        self._closed = True
        self.win.destroy()

    # ------------------------------------------------------------------
    # 后端交互
    # ------------------------------------------------------------------
    def _record(self, minutes: int, phase: str, status: str) -> None:
        def work() -> Any:
            return self.api.pomodoro_record(minutes, phase=phase, status=status)

        def done(result, err) -> None:
            if err:
                return
            self._today_count = int(result.get("today_count", self._today_count))
            self._refresh_stats_label()
            if self.on_recorded:
                self.on_recorded()

        self._bg(work, done)

    def _save_session(self) -> None:
        if self._closed:
            return

        def work() -> Any:
            return self.api.pomodoro_session_save(
                self.state.phase, self.state.remaining,
                self.state.running, self.state.completed,
            )

        self._bg(work, lambda _r, _e: None)

    def _bg(self, fn, on_done) -> None:
        def runner() -> None:
            try:
                result = fn()
            except Exception as exc:  # noqa: BLE001
                # 用默认参数绑定 exc：except 块结束后异常变量会被 del，lambda 延迟执行会 NameError
                self.win.after(0, lambda e=exc: on_done(None, e))
            else:
                self.win.after(0, lambda r=result: on_done(r, None))
        threading.Thread(target=runner, daemon=True).start()

    # ------------------------------------------------------------------
    # 设置弹窗
    # ------------------------------------------------------------------
    def _open_settings(self) -> None:
        win = tk.Toplevel(self.win)
        win.title("番茄钟设置")
        win.geometry("280x220")
        win.configure(bg=_BG)
        win.transient(self.win)
        win.attributes("-topmost", True)
        T.apply_window_material(win, acrylic=True)

        # key 与 _default_durations 的 phase 名对齐（focus/short_break/long_break）
        fields = [
            ("专注时长（分钟）", "focus"),
            ("短休息（分钟）", "short_break"),
            ("长休息（分钟）", "long_break"),
            ("每轮番茄数", "laps_per_cycle"),
        ]
        vars_: dict[str, tk.StringVar] = {}
        for i, (label, key) in enumerate(fields):
            tk.Label(win, text=label, bg=_BG, fg=_INK_DIM,
                     font=(T.F_SANS, 10)).grid(row=i, column=0, padx=12, pady=6, sticky="w")
            var = tk.StringVar(value=str(self._default_durations_minutes(key)))
            vars_[key] = var
            tk.Spinbox(win, from_=1, to=120 if key != "laps_per_cycle" else 12,
                       textvariable=var, width=6, font=(T.F_SANS, 10),
                       bg=T.CARD, fg=_INK, insertbackground=_INK,
                       buttonbackground=_BTN_BG).grid(row=i, column=1, padx=12, pady=6)

        def save() -> None:
            cfg = {
                "focus_minutes": _safe_int(vars_["focus"].get(), 25),
                "short_break_minutes": _safe_int(vars_["short_break"].get(), 5),
                "long_break_minutes": _safe_int(vars_["long_break"].get(), 15),
                "laps_per_cycle": _safe_int(vars_["laps_per_cycle"].get(), 4),
            }

            def work() -> Any:
                return self.api.pomodoro_save_config(cfg)

            def done(result, err) -> None:
                if err:
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
                self._draw_segments()
                win.destroy()

            self._bg(work, done)

        btns = tk.Frame(win, bg=_BG)
        btns.grid(row=len(fields), column=0, columnspan=2, pady=10)
        tk.Button(btns, text="保存", command=save, bg=T.ACCENT, fg=T.ON_ACCENT,
                  relief="flat", padx=14, pady=4, cursor="hand2",
                  font=(T.F_SANS, 10, "bold")).pack(side="left", padx=6)
        tk.Button(btns, text="取消", command=win.destroy, bg=_BTN_BG, fg=_INK_DIM,
                  relief="flat", padx=14, pady=4, cursor="hand2",
                  font=(T.F_SANS, 10, "bold")).pack(side="left", padx=6)

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
        self.phase_label.config(text=_PHASE_LABEL[self.state.phase], fg=color)
        self._draw_ring()
        self._draw_segments()

    def _draw_ring(self) -> None:
        self.canvas.delete("ring")
        pad = self._ring_pad
        x0, y0 = pad, pad
        x1, y1 = 180 - pad, 180 - pad
        self.canvas.create_arc(x0, y0, x1, y1, start=90, extent=-359.9,
                               style=tk.ARC, width=10, outline=T.BORDER, tags="ring")
        extent = -self.state.progress * 359.9
        self.canvas.create_arc(x0, y0, x1, y1, start=90, extent=extent,
                               style=tk.ARC, width=10,
                               outline=_phase_color(self.state.phase), tags="ring")
        m, s = divmod(self.state.remaining, 60)
        text = f"{m:02d}:{s:02d}"
        self.canvas.delete("time")
        self.canvas.create_text(self._cx, self._cy, text=text,
                                font=(T.F_MONO[0], 30, "bold"), fill=_INK, tags="time")

    def _draw_segments(self) -> None:
        self.canvas.delete("seg")
        segs = self.state.cycle_segments()
        n = len(segs)
        gap = 24
        start_x = self._cx - (n - 1) * gap / 2
        y = 168
        for seg in segs:
            x = start_x + seg["index"] * gap
            color = _phase_color(self.state.phase)
            if seg["state"] == "completed":
                fill = color
                outline = color
            elif seg["state"] == "active":
                fill = color
                outline = T.INK
            else:
                fill = _BTN_BG
                outline = T.BORDER
            self.canvas.create_oval(x - 5, y - 5, x + 5, y + 5,
                                    fill=fill, outline=outline, width=1, tags="seg")

    def _refresh_stats_label(self) -> None:
        self.stats_label.config(
            text=f"今日 🍅 {self._today_count} · 本周 {self._week_count} · 累计 {self._total_count}"
        )

    # ------------------------------------------------------------------
    # 兼容旧接口（供外部设置今日计数）
    # ------------------------------------------------------------------
    def set_today_count(self, count: int) -> None:
        self._today_count = max(0, int(count))
        self._refresh_stats_label()


def _safe_int(text: str, default: int) -> int:
    try:
        return int(text.strip())
    except (ValueError, AttributeError):
        return default
