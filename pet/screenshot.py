"""全屏截图（PySide6 版）：全屏覆盖 + QPainter 橡皮筋框选（Esc 取消）。

对应原 tkinter screenshot.py 的 RegionSelector：
- 全屏无边框置顶覆盖层，显示当前屏幕图像（灰度半透明）；
- 鼠标左键拖出选区，实时绘制霓虹绿矩形 + 尺寸角标；
- 释放时返回选区 PNG bytes（>=4px 有效，否则取消）；Esc 取消返回 None；
- 截图用 QScreen.grabWindow（Qt 原生，零 Pillow 依赖）。

返回值为 PNG 字节（直接可上传后端），与原版返回 box + caller 裁剪不同，
但语义等价且更内聚。
"""
from __future__ import annotations

import sys

from PySide6.QtCore import Qt, QRect, QPoint, QBuffer, QEventLoop
from PySide6.QtGui import QPainter, QColor, QPen, QBrush, QGuiApplication
from PySide6.QtWidgets import QWidget

from .widgets import theme as T

SELECT_COLOR = T.ACCENT
MIN_SIZE = 4           # 最小有效选区边长（px）


class RegionSelector(QWidget):
    """全屏框选截图。run() 模态阻塞，返回 PNG bytes 或 None。"""

    def __init__(self) -> None:
        super().__init__(None)
        self._begin: QPoint | None = None
        self._end: QPoint | None = None
        self._screen_pix = None
        self._png: bytes | None = None
        # 高 DPI 缩放比（物理像素 / 逻辑像素）：无 run() 前置时默认 1:1
        self._scale_x = 1.0
        self._scale_y = 1.0

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setMouseTracking(True)

    # ------------------------------------------------------------------
    def run(self) -> bytes | None:
        """模态运行；返回选中区域 PNG bytes 或 None（取消）。"""
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            return None
        self._screen_pix = screen.grabWindow(0)
        # 高 DPI：pixmap 为物理分辨率，窗口/选区为逻辑坐标，
        # 记录 物理/逻辑 比例供还原与裁剪换算（dpr=1 时恒为 1，行为不变）。
        geo = screen.geometry()
        self._scale_x = self._screen_pix.width() / max(1, geo.width())
        self._scale_y = self._screen_pix.height() / max(1, geo.height())
        self.setGeometry(geo)
        self.show()
        self.raise_()
        self.activateWindow()

        # 模态循环：阻塞直到完成
        self._loop = QEventLoop(self)
        self._loop.exec()
        return self._png

    # ------------------------------------------------------------------
    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.drawPixmap(0, 0, self.width(), self.height(), self._screen_pix)

        # 半透明遮罩（未选区时整屏淡罩，选区时选中区还原）
        dim = QColor(0, 0, 0, 130)
        p.fillRect(self.rect(), dim)
        if self._begin is not None and self._end is not None:
            rect = self._norm_rect()
            # 选区还原：sourceRect 须换算物理坐标，否则高 DPI 下错位/拉伸
            p.drawPixmap(
                rect, self._screen_pix,
                QRect(round(rect.x() * self._scale_x),
                      round(rect.y() * self._scale_y),
                      round(rect.width() * self._scale_x),
                      round(rect.height() * self._scale_y)))
            # 霓虹绿选区框
            p.setPen(QPen(QColor(SELECT_COLOR), 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(rect)
            # 尺寸角标
            label = f"{rect.width()} × {rect.height()}"
            p.setPen(QColor("white"))
            p.setFont(T.font(T.F_SMALL))
            p.drawText(rect.topRight() + QPoint(8, 18), label)
        p.end()

    def _norm_rect(self) -> QRect:
        x1, y1 = self._begin.x(), self._begin.y()
        x2, y2 = self._end.x(), self._end.y()
        return QRect(min(x1, x2), min(y1, y2),
                     abs(x2 - x1), abs(y2 - y1))

    # ------------------------------------------------------------------
    # 事件
    # ------------------------------------------------------------------
    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._begin = event.position().toPoint()
            self._end = self._begin

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._begin is not None:
            self._end = event.position().toPoint()
            self.update()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if self._begin is None:
            return
        self._end = event.position().toPoint()
        rect = self._norm_rect()
        self._begin = None
        if rect.width() >= MIN_SIZE and rect.height() >= MIN_SIZE:
            self._finish(self._crop(rect))
        else:
            self._finish(None)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_Escape:
            self._finish(None)
        else:
            super().keyPressEvent(event)

    # ------------------------------------------------------------------
    def _crop(self, rect: QRect) -> bytes:
        """裁剪选中区并编码 PNG。

        高 DPI 下 pixmap 为物理分辨率，须将逻辑选区换算为物理坐标再裁剪，
        否则截取区域偏小、内容放大；越界部分 intersect 裁剪兜底。
        """
        px = QRect(
            round(rect.x() * self._scale_x),
            round(rect.y() * self._scale_y),
            round(rect.width() * self._scale_x),
            round(rect.height() * self._scale_y),
        )
        px = px.intersected(
            QRect(0, 0, self._screen_pix.width(), self._screen_pix.height()))
        crop = self._screen_pix.copy(px)
        buf = QBuffer()
        buf.open(QBuffer.OpenModeFlag.WriteOnly)
        ok = crop.save(buf, "PNG")
        data = bytes(buf.data())
        buf.close()
        return data if ok else b""

    def _finish(self, png: bytes | None) -> None:
        self._png = png
        self.hide()
        self.close()
        loop = getattr(self, "_loop", None)
        if loop is not None and loop.isRunning():
            loop.quit()
