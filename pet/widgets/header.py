"""页内顶栏 / 状态点 / 场景头 —— PySide6 版。

对应原 tkinter ui.py 的 PageHeader / StatusDot / SceneHeader。
PageHeader：左「大标题 + 霓虹绿下划线 + 副标题(+状态点)」，右「操作按钮区」。
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame, QLabel, QHBoxLayout, QVBoxLayout, QWidget,
)

from . import theme as T


class StatusDot(QWidget):
    """运行状态小圆点（绿=在线 / 红=离线 / 橙=进行中）。QPainter 画圆。"""

    def __init__(self, parent: QWidget | None, color: str = T.OK, size: int = 10) -> None:
        super().__init__(parent)
        self._color = color
        self.setFixedSize(size, size)

    def set(self, color: str) -> None:
        self._color = color
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        from PySide6.QtGui import QPainter, QColor
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setBrush(QColor(self._color))
        p.setPen(Qt.PenStyle.NoPen)
        w, h = self.width(), self.height()
        p.drawEllipse(1, 1, max(1, w - 2), max(1, h - 2))
        p.end()


class SceneHeader(QWidget):
    """（已移除绘本装饰）保留类以兼容旧引用，不绘制内容。"""

    def __init__(self, parent: QWidget | None, width: int = 0, height: int = 0) -> None:
        super().__init__(parent)
        self.setMinimumSize(max(1, width), max(1, height))


class PageHeader(QFrame):
    """页内顶栏：左「大标题 + 副标题(+状态点)」，右「操作按钮区」。"""

    def __init__(self, parent: QWidget, title: str, subtitle: str = "",
                 status_color: str | None = None) -> None:
        super().__init__(parent)
        # 背景透明：页面底由 Dashboard 全屏视频提供，标题头不再涂深蓝遮挡视频
        self.setStyleSheet("background: transparent;")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(16)

        # 左侧：标题 + 霓虹下划线 + 副标题
        left = QVBoxLayout()
        left.setSpacing(2)
        title_lbl = QLabel(title, self)
        title_lbl.setStyleSheet(f"color: {T.INK}; background: transparent;")
        title_lbl.setFont(T.font(T.F_TITLE))
        left.addWidget(title_lbl)

        # 霓虹绿下划线
        underline = QFrame(self)
        underline.setFixedSize(72, 3)
        underline.setStyleSheet(f"background-color: {T.ACCENT};")
        left.addWidget(underline)

        sub_row = QHBoxLayout()
        sub_row.setSpacing(8)
        self._sub = QLabel(subtitle, self)
        self._sub.setStyleSheet(f"color: {T.INK2}; background: transparent;")
        self._sub.setFont(T.font(T.F_SMALL))
        self._sub.setWordWrap(True)
        sub_row.addWidget(self._sub)
        self._dot: StatusDot | None = None
        if status_color:
            self._dot = StatusDot(self, status_color)
            sub_row.addWidget(self._dot, 0, Qt.AlignmentFlag.AlignTop)
        sub_row.addStretch(1)
        left.addLayout(sub_row)
        left.addStretch(1)

        lay.addLayout(left, 1)

        # 右侧：操作按钮区
        self.actions = QWidget(self)
        self._actions_layout = QHBoxLayout(self.actions)
        self._actions_layout.setContentsMargins(0, 4, 0, 0)
        self._actions_layout.setSpacing(8)
        lay.addWidget(self.actions, 0, Qt.AlignmentFlag.AlignTop)

    def set_subtitle(self, text: str) -> None:
        self._sub.setText(text)

    def set_status(self, color: str) -> None:
        if self._dot is not None:
            self._dot.set(color)

    def add_action(self, w: QWidget) -> None:
        self._actions_layout.addWidget(w)

    def action_layout(self) -> QHBoxLayout:
        return self._actions_layout
