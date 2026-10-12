"""截图解决方案弹窗（PySide6 版）——两栏对比 + 双按钮。

「一键截图 → 判断 Agent 生成方案」的确认输出框：
- 左「截图原文」：预填 OCR 识别原文，可编辑（OCR 有错字可手动修正）；
- 右「AI 修改方案」：预填 AI 生成的解决方案，可编辑；
- 底部两按钮：
  ·「原样入库」→ 存左框内容（result_mode="raw"）；
  ·「采用 AI 修改」→ 存右框内容（result_mode="ai"）；
- 无 LLM Key 降级时右框为空，此时「采用 AI 修改」禁用（AI 没生成方案），
  主人可在右框手写或直接「原样入库」。

exec() 后读 result_mode / result_text；取消（reject）时 result_mode 为 None。
"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QPlainTextEdit, QVBoxLayout,
)

from ..widgets import theme as T
from ..widgets.buttons import ghost_button, primary_button
from .key import _BaseDialog


class SolutionDialog(_BaseDialog):
    """截图解决方案：原文 / AI 修改双框对比。exec() 后读 result_mode + result_text。"""

    def __init__(self, solution: str, ocr_text: str, *, degraded: bool) -> None:
        super().__init__("截图解决方案", 760, 480)
        self.result_mode: str | None = None   # "ai" / "raw"；取消保持 None
        self.result_text: str | None = None

        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 10)
        lay.setSpacing(8)

        if degraded:
            hint_text = "AI 未能自动生成方案：请在右侧手写方案，或直接「原样入库」保存原文。"
        else:
            hint_text = "确认保存内容：可直接入库原文，或采用 AI 修改后的方案（两框均可再编辑）。"
        hint = QLabel(hint_text, self)
        hint.setStyleSheet(f"color: {T.INK2};")
        hint.setFont(T.font(T.F_SMALL))
        lay.addWidget(hint)

        # 左右两栏标题
        titles = QHBoxLayout()
        titles.setSpacing(10)
        raw_title = QLabel("截图原文", self)
        raw_title.setStyleSheet(f"color: {T.INK2};")
        raw_title.setFont(T.font(T.F_SMALL))
        ai_title = QLabel("AI 修改方案", self)
        ai_title.setStyleSheet(f"color: {T.ACCENT};")
        ai_title.setFont(T.font(T.F_SMALL))
        titles.addWidget(raw_title, 1)
        titles.addWidget(ai_title, 1)
        lay.addLayout(titles)

        # 左右两栏编辑框（并排，各占一半）
        cols = QHBoxLayout()
        cols.setSpacing(10)
        self.raw_text = QPlainTextEdit(self)
        self.raw_text.setStyleSheet(f"background-color: {T.CARD}; color: {T.INK};")
        self.raw_text.setFont(T.font(T.F_BODY))
        self.raw_text.setPlainText(ocr_text or "")
        self.ai_text = QPlainTextEdit(self)
        self.ai_text.setStyleSheet(f"background-color: {T.CARD}; color: {T.INK};")
        self.ai_text.setFont(T.font(T.F_BODY))
        self.ai_text.setPlainText(solution or "")
        cols.addWidget(self.raw_text, 1)
        cols.addWidget(self.ai_text, 1)
        lay.addLayout(cols, 1)

        # 底部双按钮：左「原样入库」右「采用 AI 修改」（主操作）
        btns = QHBoxLayout()
        btns.addStretch(1)
        self.raw_save = ghost_button(self, "原样入库", self._save_raw)
        self.ai_save = primary_button(self, "采用 AI 修改", self._save_ai)
        btns.addWidget(self.raw_save)
        btns.addWidget(self.ai_save)
        lay.addLayout(btns)

        # AI 方案为空时「采用 AI 修改」禁用；有内容自动启用
        self.ai_text.textChanged.connect(self._sync_ai_button)
        self.ai_text.setFocus()
        self._sync_ai_button()
        self.exec()

    def _sync_ai_button(self) -> None:
        self.ai_save.setEnabled(bool(self.ai_text.toPlainText().strip()))

    def _save_ai(self) -> None:
        text = self.ai_text.toPlainText().strip()
        if not text:  # 兜底：空则退回原文
            text = self.raw_text.toPlainText().strip()
        self.result_mode = "ai"
        self.result_text = text
        self.accept()

    def _save_raw(self) -> None:
        self.result_mode = "raw"
        self.result_text = self.raw_text.toPlainText().strip()
        self.accept()
