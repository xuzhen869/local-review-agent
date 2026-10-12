"""DeepSeek Key / 知识点 / 今日小结弹窗（PySide6 版）。

对应原 tkinter dialogs.py 的 KeyDialog / KnowledgeDialog / SummaryDialog：
- KnowledgeDialog：知识点录入，预填剪贴板可编辑，保存返回文本；
- KeyDialog：DeepSeek API Key 录入（掩码显示）；
- SummaryDialog：今日小结只读展示，简单 Markdown 渲染（# 标题 / 列表）。
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QTextCursor
from PySide6.QtWidgets import (
    QDialog, QLabel, QLineEdit, QPlainTextEdit, QTextEdit, QVBoxLayout,
    QHBoxLayout, QSizePolicy,
)

from ..widgets import theme as T
from ..widgets.buttons import ghost_button, primary_button


class _BaseDialog(QDialog):
    """统一置顶独立弹窗基类（不 transient，主窗口可能隐藏）。"""

    def __init__(self, title: str, width: int, height: int) -> None:
        super().__init__(None)
        self.setWindowTitle(title)
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        self.resize(width, height)
        self.setStyleSheet(f"background-color: {T.BG};")


class KnowledgeDialog(_BaseDialog):
    """知识点录入：预填剪贴板，可编辑。exec() 后读 result_text（None=取消）。"""

    def __init__(self, initial_text: str = "") -> None:
        super().__init__("记录知识点", 560, 380)
        self.result_text: str | None = None

        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 10)
        lay.setSpacing(8)

        hint = QLabel("粘贴或编辑要存入知识库的内容：", self)
        hint.setStyleSheet(f"color: {T.INK2};")
        hint.setFont(T.font(T.F_SMALL))
        lay.addWidget(hint)

        self.text = QPlainTextEdit(self)
        self.text.setStyleSheet(f"background-color: {T.CARD}; color: {T.INK};")
        self.text.setFont(T.font(T.F_BODY))
        if initial_text:
            self.text.setPlainText(initial_text)
        self.text.setFocus()
        lay.addWidget(self.text, 1)

        btns = QHBoxLayout()
        btns.addStretch(1)
        save = primary_button(self, "存入知识库", self._save)
        cancel = ghost_button(self, "取消", self.reject)
        btns.addWidget(cancel)
        btns.addWidget(save)
        lay.addLayout(btns)

        save.setDefault(True)
        self.exec()

    def _save(self) -> None:
        self.result_text = self.text.toPlainText().strip()
        self.accept()


class KeyDialog(_BaseDialog):
    """DeepSeek API Key 录入（掩码）。exec() 后读 result_key（None=取消）。"""

    def __init__(self, title: str = "录入 DeepSeek API Key") -> None:
        super().__init__(title, 480, 130)
        self.result_key: str | None = None

        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 14, 14, 12)
        lay.setSpacing(8)

        hint = QLabel("DeepSeek API Key（加密存储，不明文落盘）：", self)
        hint.setStyleSheet(f"color: {T.INK2};")
        hint.setFont(T.font(T.F_BODY))
        lay.addWidget(hint)

        self.edit = QLineEdit(self)
        self.edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.edit.setStyleSheet(f"background-color: {T.CARD}; color: {T.INK};")
        self.edit.setFont(T.font(T.F_BODY))
        self.edit.setFocus()
        self.edit.returnPressed.connect(self._save)
        lay.addWidget(self.edit)

        btns = QHBoxLayout()
        btns.addStretch(1)
        save = primary_button(self, "保存", self._save)
        cancel = ghost_button(self, "取消", self.reject)
        btns.addWidget(cancel)
        btns.addWidget(save)
        lay.addLayout(btns)

        save.setDefault(True)
        self.exec()

    def _save(self) -> None:
        self.result_key = self.edit.text().strip()
        self.accept()


class SummaryDialog(_BaseDialog):
    """今日小结展示（只读，简单 Markdown 渲染）。exec() 后即关闭。"""

    def __init__(self, summary: str | None, *, error: str | None = None) -> None:
        super().__init__("今日周报", 640, 520)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 14, 14, 10)
        lay.setSpacing(8)

        self.text = QTextEdit(self)
        self.text.setReadOnly(True)
        self.text.setStyleSheet(f"background-color: {T.CARD}; color: {T.INK};")
        self.text.setFont(T.font(T.F_BODY))
        self.text.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        lay.addWidget(self.text, 1)

        btns = QHBoxLayout()
        btns.addStretch(1)
        close = ghost_button(self, "关闭", self.accept)
        btns.addWidget(close)
        lay.addLayout(btns)

        if error:
            self._append(f"生成失败：\n\n{error}\n\n（检查 DeepSeek Key 是否有效）\n",
                         color=T.ERR)
        elif summary:
            self._render_markdown(summary)
        else:
            self._append("（无内容）")
        close.setDefault(True)
        self.exec()

    # -- Markdown 渲染 ----------------------------------------------------
    def _render_markdown(self, markdown: str) -> None:
        for line in markdown.splitlines():
            stripped = line.strip()
            if stripped.startswith("### "):
                self._append("\n" + stripped[4:] + "\n",
                             bold=True, size=13, color=T.ACCENT)
            elif stripped.startswith("## "):
                self._append("\n" + stripped[3:] + "\n",
                             bold=True, size=14, color=T.ACCENT)
            elif stripped.startswith("# "):
                self._append("\n" + stripped[2:] + "\n",
                             bold=True, size=15)
            elif stripped.startswith("- "):
                self._append("  • " + stripped[2:] + "\n")
            else:
                self._append(line + "\n")

    def _append(self, text: str, *, bold: bool = False, size: int = 11,
                color: str = T.INK) -> None:
        self.text.moveCursor(QTextCursor.MoveOperation.End)
        self.text.setFontWeight(700 if bold else 400)
        font = self.text.font()
        font.setPointSize(size)
        self.text.setCurrentFont(font)
        self.text.setTextColor(QColor(color))
        self.text.insertPlainText(text)
        self.text.setFontWeight(400)
        self.text.moveCursor(QTextCursor.MoveOperation.End)
