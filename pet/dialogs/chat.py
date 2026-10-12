"""柴犬聊天弹窗（PySide6 版）：QQ 风格聊天气泡 + 输入框 + 发送。

对应原 tkinter chat.py 的 ChatDialog：
- 走后端 /api/pet/chat（人设 + 最近事件上下文 + 对话历史）；
- 消息区为滚动气泡（pet=深玻璃泡 / user=霓虹绿泡），复用 widgets.bubble.Bubble；
- 发送期间禁用输入，回复后恢复；异步走 QThread。
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QScrollArea, QLineEdit, QWidget,
)

from ..api_client import ApiClient
from ..widgets import theme as T
from ..widgets.buttons import primary_button
from ..widgets.bubble import Bubble


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


class ChatDialog(QDialog):
    """与柴犬「阿柴」聊天（气泡消息）。"""

    def __init__(self, api: ApiClient) -> None:
        super().__init__(None)
        self.api = api
        self._history: list[dict] = []
        self._worker: _ApiWorker | None = None

        self.setWindowTitle("和阿柴聊天")
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        self.resize(460, 560)
        self.setMinimumSize(380, 440)
        self.setStyleSheet(f"background-color: {T.BG};")

        self._build()
        self._append("pet", "汪！我是阿柴，主人想聊什么？我能记住你最近在做什么哦～")

    # ------------------------------------------------------------------
    def _build(self) -> None:
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 12, 10, 12)
        lay.setSpacing(8)

        # 滚动消息区
        self._scroll = QScrollArea(self)
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self._scroll.setStyleSheet(f"background: {T.BG};")
        self._msg_host = QWidget(self._scroll)
        self._msg_host.setStyleSheet(f"background: {T.BG};")
        self._msg_lay = QVBoxLayout(self._msg_host)
        self._msg_lay.setContentsMargins(4, 4, 4, 4)
        self._msg_lay.setSpacing(6)
        self._msg_lay.addStretch(1)   # 顶部弹性，消息靠下
        self._scroll.setWidget(self._msg_host)
        lay.addWidget(self._scroll, 1)

        # 输入行
        row = QHBoxLayout()
        row.setSpacing(8)
        self._entry = QLineEdit(self)
        self._entry.setPlaceholderText("和阿柴说点什么…")
        self._entry.setStyleSheet(
            f"background-color: {T.CARD}; color: {T.INK};"
            f" border: 1px solid {T.BORDER}; border-radius: 6px; padding: 5px 8px;")
        self._entry.returnPressed.connect(self._send)
        self._entry.setFocus()
        row.addWidget(self._entry, 1)
        send = primary_button(self, "发送", self._send)
        row.addWidget(send)
        lay.addLayout(row)

    # ------------------------------------------------------------------
    def _append(self, who: str, text: str) -> None:
        maxw = max(220, self._msg_host.width() - 48)
        bubble = Bubble(self._msg_host, text, who, maxw=maxw)
        row = QHBoxLayout()
        row.setSpacing(0)
        row.addStretch(1 if who == "user" else 0)
        row.addWidget(bubble, 0)
        row.addStretch(0 if who == "user" else 1)
        self._msg_lay.insertLayout(self._msg_lay.count() - 1, row)
        # 滚动到底
        sb = self._scroll.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _set_typing(self, on: bool) -> None:
        self._entry.setEnabled(not on)

    def _send(self) -> None:
        msg = self._entry.text().strip()
        if not msg:
            return
        self._entry.clear()
        self._append("user", msg)
        self._history.append({"role": "user", "content": msg})
        self._set_typing(True)
        self._run(lambda: self.api.pet_chat(msg, self._history), self._done)

    def _done(self, result, err) -> None:
        self._set_typing(False)
        self._entry.setFocus()
        if err:
            self._append("pet", f"汪…刚才卡住了：{err}")
            return
        reply = (result or {}).get("reply", "")
        self._append("pet", reply)
        self._history.append({"role": "assistant", "content": reply})

    # ------------------------------------------------------------------
    def _run(self, fn, on_done) -> None:
        if self._worker is not None and self._worker.isRunning():
            return
        worker = _ApiWorker(fn, self)
        self._worker = worker
        worker.done.connect(on_done)
        worker.done.connect(self._on_worker_done)
        worker.start()

    def _on_worker_done(self, *_args) -> None:
        if self._worker is not None:
            self._worker.deleteLater()
            self._worker = None

    def closeEvent(self, event) -> None:  # noqa: N802
        if self._worker is not None and self._worker.isRunning():
            self._worker.requestInterruption()
            self._worker.wait(300)
        super().closeEvent(event)
