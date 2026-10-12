"""圆角按钮 / 侧边栏导航项 —— PySide6 版。

对应原 tkinter ui.py 的 CandyButton / NavItem：
- CandyButton：primary=霓虹绿实底 / ghost=卡片底细描边，中圆角，hover 平滑渐变；
- NavItem：选中强调淡底 + 左侧 3px 语义指示条，悬停浅灰。
hover 过渡用 QVariantAnimation 在 MOTION_MS 内插值背景色。
"""
from __future__ import annotations

from typing import Callable

from PySide6.QtCore import Qt, QVariantAnimation, QEasingCurve, QRectF
from PySide6.QtGui import (
    QColor, QFontMetrics, QPainter, QPainterPath, QPen, QBrush, QLinearGradient,
)
from PySide6.QtWidgets import (
    QPushButton, QWidget, QSizePolicy,
)

from . import theme as T


def _lerp_hex(a: str, b: str, t: float) -> str:
    """颜色线性插值：t=0→a，t=1→b。"""
    ca, cb = QColor(a), QColor(b)
    c = QColor(
        round(ca.red() + (cb.red() - ca.red()) * t),
        round(ca.green() + (cb.green() - ca.green()) * t),
        round(ca.blue() + (cb.blue() - ca.blue()) * t),
    )
    return c.name()


class _AnimatedButton(QPushButton):
    """带 hover 颜色动画的基础按钮。"""

    def __init__(self, parent: QWidget | None, text: str) -> None:
        super().__init__(text, parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(T.MOTION_MS)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(self._apply_color)
        self._base_colors: tuple[str, str, str] = (T.ACCENT, T.ON_ACCENT, T.ACCENT)
        self._hover_colors: tuple[str, str, str] = (T.ACCENT_HOVER, T.ON_ACCENT, T.ACCENT_HOVER)
        self._current: tuple[str, str, str] = self._base_colors

    # -- 样式应用 ----------------------------------------------------------
    def _style(self, bg: str, fg: str, border: str) -> str:
        return (
            f"QPushButton {{"
            f"  background-color: {bg}; color: {fg};"
            f"  border: 1px solid {border}; border-radius: 8px;"
            f"  padding: 4px 14px; font-family: \"{T.F_SANS}\";"
            f"}}"
            f"QPushButton:disabled {{ color: {T.MUTED}; background-color: {T.CARD_ACTIVE};"
            f"  border-color: {T.CARD_ACTIVE}; }}"
        )

    def _apply_color(self, val) -> None:
        t = float(val)
        bg = _lerp_hex(self._current[0], self._target[0], t)
        fg = _lerp_hex(self._current[1], self._target[1], t)
        border = _lerp_hex(self._current[2], self._target[2], t)
        self.setStyleSheet(self._style(bg, fg, border))
        if t >= 1.0:
            self._current = self._target

    def _start(self, target: tuple[str, str, str]) -> None:
        if target == self._current:
            return
        self._target = target
        self._anim.stop()
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.start()

    # -- 事件 --------------------------------------------------------------
    def enterEvent(self, event) -> None:  # noqa: N802
        self._start(self._hover_colors)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._start(self._base_colors)
        super().leaveEvent(event)


class CandyButton(_AnimatedButton):
    """苹果风按钮：primary=霓虹绿实底 / ghost=卡片底细描边，中圆角，hover 加深。"""

    def __init__(self, parent: QWidget | None, text: str, command: Callable | None,
                 primary: bool = True, *, icon: int | None = None,
                 font=None, padx: int = 14) -> None:
        super().__init__(parent, text)
        self._command = command
        self._primary = bool(primary)
        self._icon = icon
        self._padx = padx
        self.setFont(T.font(font or T.F_BODY))
        if icon is not None:
            self.setText(f" {text}")
        if self._primary:
            self._base_colors = (T.ACCENT, T.ON_ACCENT, T.ACCENT)
            self._hover_colors = (T.ACCENT_HOVER, T.ON_ACCENT, T.ACCENT_HOVER)
        else:
            self._base_colors = (T.CARD, T.INK2, T.BORDER)
            self._hover_colors = (T.CARD_HOVER, T.INK, T.BORDER)
        self._current = self._base_colors
        self._target = self._base_colors
        self._apply_color(1.0)
        self.set_padding(padx)
        self.clicked.connect(self._on_clicked)

    def set_padding(self, padx: int) -> None:
        fm = QFontMetrics(self.font())
        w = fm.horizontalAdvance(self.text()) + padx * 2 + (18 if self._icon else 0)
        self.setMinimumWidth(w)
        self.setMinimumHeight(max(28, fm.height() + 8))

    def _on_clicked(self) -> None:
        if self._command and self.isEnabled():
            self._command()

    def set_primary(self, primary: bool) -> None:
        """切换 primary/ghost 样式（用于 chip 选中态）。"""
        self._primary = bool(primary)
        if self._primary:
            self._base_colors = (T.ACCENT, T.ON_ACCENT, T.ACCENT)
            self._hover_colors = (T.ACCENT_HOVER, T.ON_ACCENT, T.ACCENT_HOVER)
        else:
            self._base_colors = (T.CARD, T.INK2, T.BORDER)
            self._hover_colors = (T.CARD_HOVER, T.INK, T.BORDER)
        self._current = self._base_colors
        self._target = self._base_colors
        self._apply_color(1.0)


def primary_button(parent: QWidget, text: str, command: Callable | None, **kw) -> CandyButton:
    """主按钮：霓虹绿底深字。"""
    return CandyButton(parent, text, command, primary=True, **kw)


def ghost_button(parent: QWidget, text: str, command: Callable | None, **kw) -> CandyButton:
    """次级按钮：卡片底 + 中性细描边。"""
    return CandyButton(parent, text, command, primary=False, **kw)


def danger_button(parent: QWidget, text: str, command: Callable | None, **kw) -> CandyButton:
    """危险按钮：红底白字，用于破坏性操作（清除/删除）。"""
    btn = CandyButton(parent, text, command, primary=False, **kw)
    base = (T.ERR, "#FFFFFF", T.ERR)
    hover = (_lerp_hex(T.ERR, "#000000", 0.22), "#FFFFFF",
             _lerp_hex(T.ERR, "#000000", 0.22))
    btn._base_colors = base
    btn._hover_colors = hover
    btn._current = base
    btn._target = base
    btn._apply_color(1.0)
    return btn


def chip_button(parent: QWidget, text: str, command: Callable | None,
                selected: bool = False, **kw) -> CandyButton:
    """筛选 chip：选中霓虹绿底，未选卡片底描边。"""
    return CandyButton(parent, text, command, primary=bool(selected), **kw)


def chip_set(btn: CandyButton, selected: bool) -> None:
    btn.set_primary(selected)


# ---------------------------------------------------------------------------
# 侧边栏导航项（预览稿对齐：选中横向霓虹绿渐变底 + 发光左条）
# ---------------------------------------------------------------------------
class NavItem(QPushButton):
    """侧边栏导航（对齐「回顾Agent_UI重构预览.html」.nav li.active）：

    选中 = 横向霓虹绿渐变底（14%→2%）+ 左 3px 霓虹绿指示条 + 两侧发光扩散 + 白字；
    hover = 淡白底 + 白字；未选中 = 透明底 + INK2 字。QPainter 自绘（QSS 不支持渐变）。
    """

    def __init__(self, parent: QWidget, label: str, command: Callable | None,
                 icon: int | None = None, height: int = T.NAV_ITEM_H) -> None:
        super().__init__(label, parent)
        self._label = label
        self._icon = icon
        self._selected = False
        self._hovered = False
        self.setFixedHeight(height)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setCheckable(True)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.clicked.connect(lambda: command() if command else None)
        # 抑制 QPushButton 默认文字/背景，全部自绘
        self.setStyleSheet("background: transparent; border: none; color: transparent;")

    def set_selected(self, selected: bool) -> None:
        self._selected = bool(selected)
        self.setChecked(selected)
        self.update()

    def _text_color(self) -> QColor:
        if self._selected:
            return QColor("#FFFFFF")
        return QColor(T.INK2 if not self._hovered else T.INK)

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        rect = QRectF(1, 1, w - 2, h - 2)
        path = QPainterPath()
        path.addRoundedRect(rect, 10, 10)

        if self._selected:
            # 选中：横向霓虹绿渐变底（14% → 2%）
            grad = QLinearGradient(0, 0, w, 0)
            grad.setColorAt(0.0, QColor(*T.NAV_SELECT_RGB, 36))
            grad.setColorAt(1.0, QColor(*T.NAV_SELECT_RGB, 5))
            p.fillPath(path, QBrush(grad))
            # 左侧 3px 霓虹绿指示条 + 两侧发光扩散（近似 CSS 发光）
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(*T.NAV_SELECT_RGB, 60))
            p.drawRoundedRect(QRectF(0, rect.top() + 8, 10, h - 16), 5, 5)
            p.setBrush(QColor(*T.NAV_SELECT_RGB, 255))
            p.drawRoundedRect(QRectF(0, rect.top() + 8, 3, h - 16), 1.5, 1.5)
        elif self._hovered:
            # hover：淡白底
            p.fillPath(path, QColor(*T.NAV_HOVER_RGB, 13))

        # 文字：图标（F_ICON）+ 标签（F_NAV），左对齐垂直居中
        fg = self._text_color()
        x = 12
        if self._icon:
            p.setFont(T.font((T.F_ICON, 14)))
            p.setPen(fg)
            p.drawText(QRectF(x, 0, 22, h), Qt.AlignmentFlag.AlignVCenter, chr(self._icon))
            x += 22
        p.setFont(T.font(T.F_NAV))
        p.setPen(fg)
        p.drawText(QRectF(x, 0, w - x - 8, h), Qt.AlignmentFlag.AlignVCenter, self._label)
        p.end()

    def enterEvent(self, event) -> None:  # noqa: N802
        self._hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hovered = False
        self.update()
        super().leaveEvent(event)
