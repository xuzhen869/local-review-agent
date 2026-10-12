"""玻璃数据卡 / 统计卡 / 空状态引导 —— PySide6 版。

对应原 tkinter ui.py 的 SoftCard / CandyStatCard / EmptyState / section_title / message。
统一引用 widgets/theme.py 的 token；圆角与 1px 渐变边缘用 QSS 或 QPainter 实现。
"""
from __future__ import annotations

from typing import Callable

from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import (
    QFontMetrics, QPainter, QColor, QPen, QBrush, QPainterPath, QLinearGradient,
)
from PySide6.QtWidgets import (
    QFrame, QLabel, QVBoxLayout, QHBoxLayout, QSizePolicy, QWidget, QScrollArea,
)

from . import theme as T
from .buttons import ghost_button


def _rounded_style(bg: str, border: str, radius: int) -> str:
    return (
        f"background-color: {bg}; border: 1px solid {border};"
        f" border-radius: {radius}px;"
    )


class SoftCard(QFrame):
    """玻璃卡：CARD 深空底 + 1px 渐变边缘 + 顶部高光。内容通过 add_widget/add_row 加入。"""

    def __init__(self, parent: QWidget | None = None, *,
                 bg: str | None = None, radius: int = T.RADIUS_CARD,
                 content_margin: int = T.PAD_CARD) -> None:
        super().__init__(parent)
        self.setObjectName("SoftCard")
        # 玻璃卡：默认用半透明 CARD_GLASS（透出全屏背景视频）
        self.setStyleSheet(_rounded_style(bg or T.CARD_GLASS, T.GLASS_EDGE, radius))
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(content_margin, content_margin,
                                        content_margin, content_margin)
        self._layout.setSpacing(8)

    # -- 内容 API（兼容原 pack 用法） -------------------------------------
    def add_widget(self, w: QWidget, *, stretch: int = 0,
                   align: Qt.AlignmentFlag = Qt.AlignmentFlag.AlignTop) -> None:
        self._layout.addWidget(w, stretch, align)

    def add_row(self, w: QWidget) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(w)
        self._layout.addLayout(row)
        return row

    def body_layout(self) -> QVBoxLayout:
        return self._layout


# 兼容旧引用（dashboard 用 ui.Card）
Card = SoftCard


class CandyStatCard(QFrame):
    """统计卡（对齐预览稿「回顾Agent_UI重构预览.html」）：

    顶行 = 标题（左）+ 线框图标（右，语义色）；中 = Anton 大数字；
    底 = mono foot 标签。QPainter 自绘：顶部 CARD_HOVER → CARD 渐变底、
    顶部 1px 高光线、1px 边缘；hover 亮化 + 边框转 BORDER_HOVER。
    """

    def __init__(self, parent: QWidget | None, title: str, icon: int | None,
                 color: str = T.ACCENT, height: int = T.STAT_H) -> None:
        super().__init__(parent)
        self._title = title
        self._icon = icon
        self._color = color
        self._value = "—"
        self._sub = ""
        self._hovered = False
        self.setFixedHeight(height)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)

        self._lay = QVBoxLayout(self)
        self._lay.setContentsMargins(18, 14, 18, 12)
        self._lay.setSpacing(4)

        # 顶行：标题（左）+ 图标（右，语义色）
        top = QHBoxLayout()
        top.setSpacing(8)
        self._title_lbl = QLabel(self)
        self._title_lbl.setStyleSheet(f"color: {T.INK2}; background: transparent;")
        self._title_lbl.setFont(T.font(T.F_SMALL))
        top.addWidget(self._title_lbl)
        top.addStretch(1)
        self._icon_lbl = QLabel(self)
        self._icon_lbl.setStyleSheet(f"color: {color}; background: transparent;")
        self._icon_lbl.setFont(T.font((T.F_ICON, 17)))
        top.addWidget(self._icon_lbl)
        self._lay.addLayout(top)

        # Anton 大数字（HTML 30px）
        self._value_lbl = QLabel(self._value, self)
        self._value_lbl.setStyleSheet(f"color: {T.INK}; background: transparent;")
        self._value_lbl.setFont(T.font((T.F_GROTESK, 30)))
        self._lay.addWidget(self._value_lbl)

        # mono foot 标签（HTML 8.5px）
        self._sub_lbl = QLabel(self)
        self._sub_lbl.setStyleSheet(f"color: {T.MUTED}; background: transparent;")
        self._sub_lbl.setFont(T.font((T.F_MONO[0], 8)))
        self._lay.addWidget(self._sub_lbl)

        self._render_title()

    # -- 状态 --------------------------------------------------------------
    def set(self, value, sub: str = "") -> None:
        self._value = str(value)
        self._sub = sub
        self._value_lbl.setText(self._value)
        self._sub_lbl.setText(self._sub if sub else "")
        self._shrink_value_if_needed()

    def _shrink_value_if_needed(self) -> None:
        fm = QFontMetrics(self._value_lbl.font())
        avail = max(40, self.width() - 36)
        if fm.horizontalAdvance(self._value) > avail:
            self._value_lbl.setFont(T.font((T.F_GROTESK, 20)))

    def _render_title(self) -> None:
        self._title_lbl.setText(self._title)

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(0.5, 0.5, self.width() - 1, self.height() - 1)
        path = QPainterPath()
        path.addRoundedRect(rect, T.RADIUS, T.RADIUS)
        # 渐变底：顶部 CARD_HOVER → 34% 起 CARD（hover 整体亮化）
        grad = QLinearGradient(0, 0, 0, self.height())
        if self._hovered:
            grad.setColorAt(0.0, QColor(*T.GLASS_HOVER_RGB, 230))
            grad.setColorAt(0.34, QColor(*T.GLASS_HOVER_RGB, 200))
            grad.setColorAt(1.0, QColor(*T.GLASS_RGB, 205))
        else:
            grad.setColorAt(0.0, QColor(*T.GLASS_HOVER_RGB, 215))
            grad.setColorAt(0.34, QColor(*T.GLASS_RGB, 205))
            grad.setColorAt(1.0, QColor(*T.GLASS_RGB, 205))
        p.fillPath(path, QBrush(grad))
        # 顶部 1px 高光线（白 → 透明，水平渐变）
        hl = QLinearGradient(rect.left(), 0, rect.right(), 0)
        hl.setColorAt(0.0, QColor(0, 0, 0, 0))
        hl.setColorAt(0.5, QColor(*T.CARD_HI_RGB, T.CARD_HI_ALPHA))
        hl.setColorAt(1.0, QColor(0, 0, 0, 0))
        p.fillRect(QRectF(rect.left() + 2, rect.top() + 0.5,
                          rect.width() - 4, 1), QBrush(hl))
        # 边框：hover 转 BORDER_HOVER
        border = T.BORDER_HOVER if self._hovered else T.GLASS_EDGE
        p.setPen(QPen(QColor(border), 1))
        p.drawPath(path)
        p.end()

    def enterEvent(self, event) -> None:  # noqa: N802
        self._hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hovered = False
        self.update()
        super().leaveEvent(event)


# 兼容旧引用（dashboard 用 ui.StatCard）
StatCard = CandyStatCard


# ---------------------------------------------------------------------------
# 文本辅助
# ---------------------------------------------------------------------------
def section_title(parent: QWidget, text: str) -> QLabel:
    """卡片 / 区块内小节标题。"""
    lbl = QLabel(text, parent)
    lbl.setStyleSheet(f"color: {T.INK}; background: transparent;")
    lbl.setFont(T.font(T.F_H))
    return lbl


def message(parent: QWidget, text: str = "", color: str = T.INK2,
            *, wraplength: int = 0) -> QLabel:
    """统一状态消息标签。"""
    lbl = QLabel(text, parent)
    lbl.setStyleSheet(f"color: {color}; background: transparent;")
    lbl.setFont(T.font(T.F_SMALL))
    lbl.setWordWrap(True)
    if wraplength:
        lbl.setMaximumWidth(wraplength)
    return lbl


# ---------------------------------------------------------------------------
# 空状态引导：线框图标 + 标题 + 副文案 + 可选操作按钮
# ---------------------------------------------------------------------------
class EmptyState(QFrame):
    """空数据引导（数据源未配置 / 无事件时显示）。居中于父容器。"""

    def __init__(self, parent: QWidget, icon: int | None, title: str,
                 subtitle: str = "", action_text: str | None = None,
                 action: Callable | None = None) -> None:
        super().__init__(parent)
        self.setStyleSheet("background: transparent;")
        self._lay = QVBoxLayout(self)
        self._lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._lay.setSpacing(6)

        if icon is not None:
            ic = QLabel(chr(icon), self)
            ic.setStyleSheet(f"color: {T.MUTED}; background: transparent;")
            f = T.font((T.F_ICON, 30))
            ic.setFont(f)
            ic.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._lay.addWidget(ic)

        t = QLabel(title, self)
        t.setStyleSheet(f"color: {T.INK}; background: transparent;")
        t.setFont(T.font((T.F_SANS, 13, "bold")))
        t.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._lay.addWidget(t)

        if subtitle:
            s = QLabel(subtitle, self)
            s.setStyleSheet(f"color: {T.INK2}; background: transparent;")
            s.setFont(T.font(T.F_SMALL))
            s.setAlignment(Qt.AlignmentFlag.AlignCenter)
            s.setWordWrap(True)
            s.setMaximumWidth(360)
            self._lay.addWidget(s)

        if action_text and action:
            btn = ghost_button(self, action_text, action, icon=T.ICON["sources"])
            self._lay.addWidget(btn, 0, Qt.AlignmentFlag.AlignHCenter)


# ---------------------------------------------------------------------------
# 滚动容器（透明）、Bento 玻璃网格卡
# ---------------------------------------------------------------------------
class ScrollFrame(QScrollArea):
    """透明滚动容器。inner 为可滚动宿主，通过 body_layout() 添加内容。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setStyleSheet(
            "QScrollArea { background: transparent; border: none; }"
            "QScrollArea > QWidget > QWidget { background: transparent; }")
        self.inner = QWidget(self)
        self.inner.setStyleSheet("background: transparent;")
        self.setWidget(self.inner)
        self._lay = QVBoxLayout(self.inner)
        self._lay.setContentsMargins(0, 0, 0, 0)
        self._lay.setSpacing(0)

    def body_layout(self) -> QVBoxLayout:
        return self._lay

    def scroll_to_top(self) -> None:
        self.verticalScrollBar().setValue(0)


class BentoCard(QFrame):
    """Bento 网格卡：玻璃渐变底（顶部高光 → 卡片底）+ 圆角 + 边缘描边。

    顶部可选标题行（标题右侧可挂 title_action 动作按钮），内容放入 .body。
    """

    def __init__(self, parent: QWidget | None, title: str = "",
                 radius: int = T.RADIUS_CARD, content_margin: int = 14,
                 title_action: QWidget | None = None) -> None:
        super().__init__(parent)
        self._radius = radius
        self._lay = QVBoxLayout(self)
        self._lay.setContentsMargins(content_margin, content_margin,
                                     content_margin, content_margin)
        self._lay.setSpacing(8)
        if title:
            head = QHBoxLayout()
            head.setSpacing(8)
            head.addWidget(section_title(self, title))
            head.addStretch(1)
            if title_action is not None:
                head.addWidget(title_action)
            self._lay.addLayout(head)
        self.body = QWidget(self)
        self.body.setStyleSheet("background: transparent;")
        self._lay.addWidget(self.body, 1)

    def body_layout(self) -> QVBoxLayout:
        """内容 body 的内部布局（惰性创建，保持与 SoftCard.body_layout 同名）。"""
        lay = self.body.layout()
        if lay is None:
            lay = QVBoxLayout(self.body)
            lay.setContentsMargins(0, 0, 0, 0)
            lay.setSpacing(8)
        return lay

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(0.5, 0.5, self.width() - 1, self.height() - 1)
        path = QPainterPath()
        path.addRoundedRect(rect, self._radius, self._radius)
        # 玻璃渐变：半透明深蓝（QPainter 用整数 RGB+Alpha，rgba 字符串 QColor 不支持）
        grad = QLinearGradient(0, 0, 0, self.height())
        grad.setColorAt(0.0, QColor(*T.GLASS_HOVER_RGB, T.GLASS_HOVER_ALPHA))
        grad.setColorAt(0.25, QColor(*T.GLASS_RGB, T.GLASS_ALPHA))
        grad.setColorAt(1.0, QColor(*T.GLASS_RGB, T.GLASS_ALPHA))
        p.fillPath(path, QBrush(grad))
        # 顶部 1px 高光线（对齐预览稿 .card::before）
        hl = QLinearGradient(rect.left(), 0, rect.right(), 0)
        hl.setColorAt(0.0, QColor(0, 0, 0, 0))
        hl.setColorAt(0.5, QColor(*T.CARD_HI_RGB, T.CARD_HI_ALPHA))
        hl.setColorAt(1.0, QColor(0, 0, 0, 0))
        p.fillRect(QRectF(rect.left() + 2, rect.top() + 0.5,
                          rect.width() - 4, 1), QBrush(hl))
        p.setPen(QPen(QColor(T.GLASS_EDGE), 1))
        p.drawPath(path)
        p.end()
