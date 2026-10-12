"""聊天气泡（iMessage 风）：pet=深玻璃泡浅字 / user=霓虹绿泡深字 —— PySide6 版。

对应原 tkinter ui.py 的 Bubble。QLabel + QSS 圆角气泡，WordWrap 自动折行、
高度自适应。宽度由父容器（滚动区）决定，不强制等宽。
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QSizePolicy, QWidget

from . import theme as T


class Bubble(QLabel):
    """圆角聊天气泡。who ∈ {"user", "pet"}。"""

    def __init__(self, parent: QWidget, text: str, who: str,
                 maxw: int = 320) -> None:
        super().__init__(text, parent)
        self._who = who
        self.setWordWrap(True)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.setSizePolicy(
            QSizePolicy.Policy.Maximum,
            QSizePolicy.Policy.Preferred,
        )
        self.setMaximumWidth(maxw)
        self.setMinimumWidth(60)

        is_user = who == "user"
        fill = T.BUBBLE_USER if is_user else T.BUBBLE_PET
        fg = T.ON_ACCENT if is_user else T.INK
        self.setStyleSheet(
            f"QLabel {{ background-color: {fill}; color: {fg};"
            f"  border-radius: 14px; padding: 9px 14px;"
            f"  font-family: \"{T.F_SANS}\"; font-size: 10pt; }}"
        )
