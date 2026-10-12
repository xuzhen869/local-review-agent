"""应用控制器（PySide6 版）：托盘 + 柴犬精灵 + 台词气泡 + 后台任务线程池 + 健康提醒。

对应原 tkinter main.py 的 PetApp，线程模型从 `threading + root.after` 改为
QThread + signal/slot，其余调度逻辑（托盘菜单 / 宠物点击菜单 / 截图 / 知识点 /
周报 / 喂养 / 番茄钟 / 待办 / 回顾 / 聊天）逐项移植，动作名保持 `on_*`。

面板/弹窗在方法内延迟 import（阶段 2/3 交付的 Qt 模块），与旧 main.py 一致。
api_client 与 backend 零改动。
"""
from __future__ import annotations

import io
import sys
import traceback as _tb
from datetime import datetime as _dt
from pathlib import Path

from PySide6.QtCore import QObject, QPoint, QThread, QTimer, Qt, Signal
from PySide6.QtGui import QIcon, QImage, QPixmap
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from . import api_client
from .core import PetStatePoll, SpeechBubble, Sprite, random_line
from .core.sprite import DUCK_DIR
from .dialogs import error, info, msgbox
from .widgets import theme as T

WATER_MS = 45 * 60 * 1000   # 喝水提醒
BREAK_MS = 60 * 60 * 1000   # 休息提醒
TODO_MS = 5 * 60 * 1000     # 待办到期轮询
SCREENSHOT_HIDE_MS = 180    # 截图前隐藏宠物到截取的延迟


def _log_path(name: str) -> Path:
    return Path(__file__).resolve().parent.parent / "logs" / name


def make_icon(size: int = 64) -> QIcon:
    """托盘图标：鸭子正面帧（预渲染 duck_00）缩略 → QIcon。"""
    img = QImage(str(DUCK_DIR / "duck_00.png"))
    if img.isNull():
        raise RuntimeError(f"鸭子图标资源缺失: {DUCK_DIR / 'duck_00.png'}")
    pm = QPixmap.fromImage(img).scaled(
        size, size,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
    return QIcon(pm)


class _TaskWorker(QThread):
    """单次阻塞任务工作线程：api_client 阻塞调用在 run() 执行，结果回主线程。

    done(result, err)：result 为调用返回，err 为异常（None 表示成功）。
    """

    done = Signal(object, object)

    def __init__(self, fn, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._fn = fn

    def run(self) -> None:  # noqa: D102
        try:
            result = self._fn()
        except Exception as exc:  # noqa: BLE001
            self.done.emit(None, exc)
        else:
            self.done.emit(result, None)


class AppController(QObject):
    """全局控制器。backend_stopper：run.py 传入的停止回调，退出时调用。"""

    def __init__(self, backend_stopper=None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._backend_stopper = backend_stopper
        self.api = api_client.ApiClient()
        self._workers: list[_TaskWorker] = []

        self.tray: QSystemTrayIcon | None = None
        self.sprite = Sprite(on_click=self._on_sprite_click)
        self.bubble = SpeechBubble()
        self._poll = PetStatePoll(self.api, self)

        self._pet_state_cache: dict = {}
        self._pet_visible: bool = True
        self._notified_todos: set[str] = set()

        # 弹窗引用（防 GC；None=未打开）
        self._dashboard = None
        self._chat_dlg = None
        self._todos_dlg = None
        self._review_dlg = None
        self._pomodoro_dlg = None

        self._install_error_hook()
        self._build_tray()

        self._poll.state_updated.connect(self._on_state_updated)
        self._poll.state_failed.connect(lambda _e: None)   # 后端未启动静默容忍

        QTimer.singleShot(500, self._check_backend)
        QTimer.singleShot(800, self._poll.start)
        QTimer.singleShot(900, self.on_dashboard)
        self._start_reminders()
        self.sprite.show_pet()

    # ------------------------------------------------------------------
    # 日志 / 错误
    # ------------------------------------------------------------------
    def _install_error_hook(self) -> None:
        """捕获所有 Qt slot 未捕获异常写入 logs/pet_errors.log，避免静默无反应。"""

        def _hook(exc_type, exc, tb) -> None:
            try:
                p = _log_path("pet_errors.log")
                p.parent.mkdir(parents=True, exist_ok=True)
                with open(p, "a", encoding="utf-8") as f:
                    f.write(f"\n===== {_dt.now().strftime('%Y-%m-%d %H:%M:%S')} =====\n")
                    f.write("".join(_tb.format_exception(exc_type, exc, tb)))
                    f.write("\n")
            except Exception:  # noqa: BLE001
                pass

        sys.excepthook = _hook

    def _log_pet_event(self, message: str) -> None:
        """诊断：宠物交互事件写 logs/pet_events.log（时间戳）。"""
        try:
            p = _log_path("pet_events.log")
            p.parent.mkdir(parents=True, exist_ok=True)
            with open(p, "a", encoding="utf-8") as f:
                f.write(f"{_dt.now().strftime('%H:%M:%S')} {message}\n")
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------------
    # 后台任务
    # ------------------------------------------------------------------
    def run_async(self, fn, on_done) -> None:
        """fn() 放入工作线程；on_done(result, err) 回主线程执行。"""
        worker = _TaskWorker(fn, self)
        self._workers.append(worker)

        def _finish(result, err) -> None:
            try:
                on_done(result, err)
            except Exception:  # noqa: BLE001
                self._log_pet_event(f"callback error: {err!r}")
            finally:
                if worker in self._workers:
                    self._workers.remove(worker)
                worker.deleteLater()

        worker.done.connect(_finish)
        worker.start()

    def _quit_workers(self) -> None:
        for w in self._workers:
            w.requestInterruption()
            w.wait(500)
            w.deleteLater()
        self._workers.clear()

    # ------------------------------------------------------------------
    # 托盘
    # ------------------------------------------------------------------
    def _build_tray(self) -> None:
        self.tray = QSystemTrayIcon(make_icon(), self)
        self.tray.setToolTip("回顾 Agent · 点击鸭子")
        self.tray.setContextMenu(self._tray_menu())
        self.tray.activated.connect(self._on_tray_activated)
        self.tray.show()

    def _tray_menu(self) -> QMenu:
        menu = QMenu()
        menu.addAction("📊 打开主界面", self.on_dashboard)
        menu.addAction("🦆 显示 / 隐藏鸭子", self.toggle_pet)
        menu.addAction("💬 和鸭子聊天", self.on_pet_chat)
        menu.addAction("📝 待办清单", self.on_todos)
        menu.addAction("🎓 学习回顾", self.on_review)
        menu.addAction("✨ 提取新卡片", self.on_extract)
        menu.addAction("📸 一键截图", self.on_screenshot)
        menu.addAction("🧠 记录知识点", self.on_knowledge)
        menu.addAction("📋 今日周报", self.on_summary)
        menu.addAction("🔑 设置 DeepSeek Key", self.on_set_key)
        menu.addSeparator()
        menu.addAction("🚪 退出", self._quit)
        return menu

    def _on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.on_dashboard()

    # ------------------------------------------------------------------
    # 宠物点击 → 弹出菜单
    # ------------------------------------------------------------------
    def _on_sprite_click(self, x_root: int, y_root: int) -> None:
        self._log_pet_event(f"click at ({x_root}, {y_root})")
        menu = QMenu()
        menu.setStyleSheet(
            f"QMenu {{ background-color: {T.SURFACE}; color: {T.INK};"
            f"  border: 1px solid {T.BORDER}; border-radius: 6px; }}"
            f"QMenu::item {{ padding: 6px 18px; }}"
            f"QMenu::item:selected {{ background-color: {T.CARD_ACTIVE}; }}"
        )
        st = self._pet_state_cache or {}
        mood_icon = {"happy": "😊", "normal": "🙂", "hungry": "😿"}.get(st.get("mood"), "🙂")
        hunger_max = st.get("hunger_max") or 100
        hunger_pct = int(st.get("hunger", hunger_max) / hunger_max * 100)
        status = (f"{mood_icon} 饥饿 {hunger_pct}% · 食物×{st.get('food', 0)}"
                  f" · 番茄{st.get('today_pomodoro', 0)}")
        act = menu.addAction(status)
        act.setEnabled(False)   # 状态行，不可点
        menu.addSeparator()

        menu.addAction("📊 打开主界面", self.on_dashboard)
        menu.addSeparator()
        menu.addAction("📸 一键截图", self.on_screenshot)
        menu.addAction("🧠 记录知识点", self.on_knowledge)
        menu.addAction("📋 今日周报", self.on_summary)
        menu.addSeparator()

        punch_label = "📅 每日打卡（已打卡 ✓）" if st.get("punched_today") else "📅 每日打卡（领食物×3）"
        menu.addAction(punch_label, self.on_punch)
        menu.addAction("🍚 喂养宠物", self.on_feed)
        menu.addAction("🍅 番茄闹钟", self.on_pomodoro)
        menu.addAction("💬 和鸭子聊天", self.on_pet_chat)
        menu.addAction("📝 待办清单", self.on_todos)
        menu.addAction("🎓 学习回顾", self.on_review)
        menu.addAction("✨ 提取新卡片", self.on_extract)
        menu.addSeparator()
        menu.addAction("🔑 设置 DeepSeek Key", self.on_set_key)
        menu.addAction("🦆 隐藏鸭子", self.toggle_pet)
        menu.addAction("🚪 退出", self._quit)
        # PySide6 6.11 起 QMenu.exec 只接受 QPoint，传 (int, int) 会抛 TypeError
        menu.exec(QPoint(x_root, y_root))

    # ------------------------------------------------------------------
    # 台词气泡
    # ------------------------------------------------------------------
    def _say(self, text: str, *, mood: str | None = None) -> None:
        """宠物上方弹台词气泡。anchor 用 sprite 全局坐标。"""
        self.bubble.say(
            text,
            anchor=(self.sprite.x(), self.sprite.y()),
            bubble_w=self.sprite.width(),
            bubble_h=self.sprite.height(),
        )
        self._log_pet_event(f"speech: {text[:30]}")

    # ------------------------------------------------------------------
    # 健康提醒
    # ------------------------------------------------------------------
    def _start_reminders(self) -> None:
        self._water_timer = QTimer(self)
        self._water_timer.setInterval(WATER_MS)
        self._water_timer.timeout.connect(self._remind_water)
        self._water_timer.start()

        self._break_timer = QTimer(self)
        self._break_timer.setInterval(BREAK_MS)
        self._break_timer.timeout.connect(self._remind_break)
        self._break_timer.start()

        self._todo_timer = QTimer(self)
        self._todo_timer.setInterval(TODO_MS)
        self._todo_timer.timeout.connect(self._check_todos_due)
        self._todo_timer.start()

    def _remind_water(self) -> None:
        self._notify("💧 主人喝口水吧，保持水分～")
        self._say(random_line(mood="normal"))

    def _remind_break(self) -> None:
        self._notify("🧘 坐了好一会儿了，站起来活动一下吧")
        self._say(random_line(mood="normal"))

    def _check_todos_due(self) -> None:
        self.run_async(self.api.todos_due, self._todos_due_done)

    def _todos_due_done(self, result, err) -> None:
        if err or not result:
            return
        for it in (result.get("items") or []):
            todo_id = it.get("id")
            if todo_id and todo_id not in self._notified_todos:
                self._notify(f"⏰ 待办到期：{it.get('text', '')}")
                self._notified_todos.add(todo_id)

    def _notify(self, message: str) -> None:
        """托盘气泡通知；托盘不可用时回退消息框。"""
        if self.tray and QSystemTrayIcon.isSystemTrayAvailable():
            self.tray.showMessage("回顾 Agent", message,
                                  QSystemTrayIcon.MessageIcon.Information, 5000)
            return
        info(None, "回顾 Agent", message)

    # ------------------------------------------------------------------
    # 主界面 / 柴犬开关
    # ------------------------------------------------------------------
    def on_dashboard(self) -> None:
        """打开（或聚焦）主窗口。已存在则置顶，否则重建。"""
        from .panels.dashboard import Dashboard

        if self._dashboard is not None:
            if self._dashboard.isVisible():
                self._dashboard.raise_()
                self._dashboard.activateWindow()
                return
            self._dashboard = None
        self._dashboard = Dashboard(self.api, on_close=self._dashboard_closed)
        T.apply_window_material(self._dashboard)
        self._dashboard.show()
        self._log_pet_event("dashboard opened")

    def _dashboard_closed(self) -> None:
        self._dashboard = None

    def on_pet_chat(self) -> None:
        from .dialogs.chat import ChatDialog
        dlg = ChatDialog(self.api)
        T.apply_window_material(dlg, acrylic=True)
        self._chat_dlg = dlg
        dlg.show()

    def on_todos(self) -> None:
        from .dialogs.todos import TodoDialog
        dlg = TodoDialog(self.api)
        T.apply_window_material(dlg, acrylic=True)
        self._todos_dlg = dlg
        dlg.show()

    def on_review(self) -> None:
        from .dialogs.review import ReviewDialog
        dlg = ReviewDialog(self.api)
        T.apply_window_material(dlg, acrylic=True)
        self._review_dlg = dlg
        dlg.show()

    def on_extract(self) -> None:
        def done(result, err) -> None:
            if err:
                error(None, "提取失败", str(err))
                return
            if result.get("need_key"):
                self._notify("需要先配置 DeepSeek Key 才能提取学习卡片")
            elif result.get("extracted"):
                self._notify(f"✨ 已提取 {result['extracted']} 张新学习卡片，可以去「学习回顾」复习")
                self._say(random_line(mood="praise"))
            else:
                self._notify("近 7 天没有可提取的新内容")
        self.run_async(self.api.learning_extract, done)

    def toggle_pet(self) -> None:
        """显示 / 隐藏柴犬悬浮窗。"""
        if self._pet_visible:
            self.sprite.hide_pet()
        else:
            self.sprite.show_pet()
        self._pet_visible = not self._pet_visible
        self._log_pet_event(f"pet visible={self._pet_visible}")

    # ------------------------------------------------------------------
    # 功能一：一键截图
    # ------------------------------------------------------------------
    def on_screenshot(self) -> None:
        self.sprite.hide_pet()
        QTimer.singleShot(SCREENSHOT_HIDE_MS, self._do_screenshot)

    def _do_screenshot(self) -> None:
        from .screenshot import RegionSelector
        try:
            selector = RegionSelector()
            png = selector.run()
        finally:
            self.sprite.show_pet()
        if png is None:
            return
        self._notify("正在识别并分析截图…")
        self.run_async(lambda: self.api.capture_analyze(png), self._screenshot_analyzed)

    def _screenshot_analyzed(self, result, err) -> None:
        if err:
            error(None, "截图失败", str(err))
            return
        if not result.get("ocr_text"):
            info(None, "截图已保存", "未识别到文字，已保存原始截图。")
            return
        if not result.get("ocr_available"):
            info(None, "截图已保存",
                 "OCR 引擎（RapidOCR）尚未就绪，已保存原始截图。下载完成后将自动启用文字识别。")
            return
        # 弹双框对比：左原文 / 右 AI 修改，点「原样入库」或「采用 AI 修改」确认
        from .dialogs.solution import SolutionDialog
        dlg = SolutionDialog(result.get("solution") or "",
                             result.get("ocr_text") or "",
                             degraded=bool(result.get("degraded")))
        if not dlg.result_mode or not dlg.result_text:
            return  # 取消
        self._notify("正在存入知识库…")
        self.run_async(
            lambda: self.api.capture_solution(
                dlg.result_text, result.get("path_rel") or "", mode=dlg.result_mode),
            self._solution_saved)

    def _solution_saved(self, result, err) -> None:
        if err:
            error(None, "保存失败", str(err))
            return
        mode_label = "采用 AI 方案" if result.get("mode") == "ai" else "原文入库"
        inserted = "新增" if result.get("inserted") else "已存在（未重复入库）"
        info(None, "已存入知识库", f"解决方案已保存（{mode_label} · {inserted}）。")

    # ------------------------------------------------------------------
    # 功能二：记录知识点
    # ------------------------------------------------------------------
    def on_knowledge(self) -> None:
        from .dialogs.key import KnowledgeDialog
        clip = QApplication.clipboard().text()
        dlg = KnowledgeDialog(clip)
        T.apply_window_material(dlg, acrylic=True)
        text = dlg.result_text
        if not text:
            return
        self.run_async(
            lambda: self.api.capture_knowledge(text),
            lambda _r, err: error(None, "保存失败", str(err)) if err
            else self._notify("知识点已存入知识库 ✓"),
        )

    # ------------------------------------------------------------------
    # 功能三：今日周报
    # ------------------------------------------------------------------
    def on_summary(self) -> None:
        self.run_async(self.api.summary_today, self._summary_done)

    def _summary_done(self, result, err) -> None:
        if err:
            error(None, "今日周报", str(err))
            return
        if result.get("need_key"):
            self._prompt_key_then_summary()
            return
        from .dialogs.key import SummaryDialog
        SummaryDialog(result.get("summary"), error=result.get("error"))

    def _prompt_key_then_summary(self) -> None:
        from .dialogs.key import KeyDialog
        dlg = KeyDialog(title="录入 DeepSeek Key 以生成今日小结")
        key = dlg.result_key
        if not key:
            return
        self.run_async(
            lambda: (self.api.set_llm_key(key), self.api.summary_today()),
            self._summary_done,
        )

    # ------------------------------------------------------------------
    # 设置 DeepSeek Key
    # ------------------------------------------------------------------
    def on_set_key(self) -> None:
        from .dialogs.key import KeyDialog
        dlg = KeyDialog()
        T.apply_window_material(dlg, acrylic=True)
        key = dlg.result_key
        if not key:
            return
        self.run_async(
            lambda: self.api.set_llm_key(key),
            lambda _r, err: error(None, "保存失败", str(err)) if err
            else self._notify("Key 已保存 ✓"),
        )

    # ------------------------------------------------------------------
    # 喂养系统：打卡 / 喂食 / 番茄钟
    # ------------------------------------------------------------------
    def on_punch(self) -> None:
        self.run_async(self.api.pet_punch, self._punch_done)

    def _punch_done(self, result, err) -> None:
        if err:
            error(None, "每日打卡", str(err))
            return
        self._notify(f"打卡成功！获得食物 ×{result.get('reward')}（当前食物 ×{result.get('food')}）")
        self._say(random_line(mood="praise"))
        self._poll.refresh()

    def on_feed(self) -> None:
        self.run_async(self.api.pet_feed, self._feed_done)

    def _feed_done(self, result, err) -> None:
        if err:
            error(None, "喂养宠物", str(err))
            return
        self._notify(f"好耶！饥饿值 +{result.get('recovery')}（当前 {result.get('hunger')}/100）")
        self._say("好吃！谢谢主人～ 🍖", mood="happy")
        self._poll.refresh()

    def on_pomodoro(self) -> None:
        from .dialogs.pomodoro import PomodoroDialog
        dlg = PomodoroDialog(self._notify, self.api, on_recorded=self._poll.refresh)
        T.apply_window_material(dlg, acrylic=True)
        self._pomodoro_dlg = dlg
        dlg.show()

    # ------------------------------------------------------------------
    # 宠物状态驱动
    # ------------------------------------------------------------------
    def _on_state_updated(self, result: dict) -> None:
        self._pet_state_cache = result
        self.sprite.set_mood(result.get("mood", "happy"))

    def _check_backend(self) -> None:
        def on_done(_result, err) -> None:
            if err:
                self._notify(f"后端未运行：{err}")
        self.run_async(self.api.health, on_done)

    # ------------------------------------------------------------------
    # 退出
    # ------------------------------------------------------------------
    def _quit(self) -> None:
        if self._dashboard is not None:
            try:
                self._dashboard.close()
            except Exception:  # noqa: BLE001
                pass
            self._dashboard = None
        for dlg in (self._chat_dlg, self._todos_dlg, self._review_dlg,
                    self._pomodoro_dlg):
            if dlg is not None:
                try:
                    dlg.close()
                except Exception:  # noqa: BLE001
                    pass
        self._poll.stop()
        self._quit_workers()
        if self.tray is not None:
            self.tray.hide()
        self.bubble.dismiss()
        self.sprite.hide_pet()
        if self._backend_stopper:
            self._backend_stopper()
        QApplication.instance().quit()
