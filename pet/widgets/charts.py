"""QPainter 图表组件（Linear/shadcn 风暗色）：柱状图 / 热力图 / 环形图 —— PySide6 版。

算法与原 tkinter Canvas 版一致（圆角柱 / 蓝紫热力色阶 / 环形+中心总计），
仅绘制目标从 Canvas 换成 QPainter。颜色 / 字体引用 widgets/theme.py。
"""
from __future__ import annotations

import datetime as _dt
import math

from PySide6.QtCore import Qt, QRectF, QPointF
from PySide6.QtGui import QPainter, QColor, QPen, QBrush, QPainterPath
from PySide6.QtWidgets import QWidget

from . import theme as T


def _level_color(count: int) -> str:
    if count <= 0:
        return T.HEAT_LEVELS[0]
    if count <= 1:
        return T.HEAT_LEVELS[1]
    if count <= 3:
        return T.HEAT_LEVELS[2]
    if count <= 6:
        return T.HEAT_LEVELS[3]
    return T.HEAT_LEVELS[4]


def _rounded_rect_path(x0: float, y0: float, w: float, h: float, r: float) -> QPainterPath:
    r = min(r, w / 2, h / 2)
    path = QPainterPath()
    path.addRoundedRect(QRectF(x0, y0, w, h), r, r)
    return path


class BarChart(QWidget):
    """柱状图：圆角柱 + 网格 + x 轴标签 + 柱顶数值。set_data 后自动重绘。"""

    def __init__(self, parent: QWidget | None, width: int = 430, height: int = 210,
                 color: str = T.ACCENT) -> None:
        super().__init__(parent)
        self._color = color
        self._labels: list[str] = []
        self._values: list[int] = []
        self.setMinimumSize(width, height)

    def set_data(self, labels: list[str], values: list[int]) -> None:
        self._labels = [str(x) for x in labels]
        self._values = [int(x) for x in values]
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()
        left, right, top, bottom = 34, 10, 16, 26
        plot_w = W - left - right
        plot_h = H - top - bottom

        # 背景卡（半透明玻璃，透出全屏背景视频）
        p.setPen(QPen(QColor(T.BORDER)))
        p.setBrush(QBrush(QColor(*T.GLASS_RGB, T.GLASS_ALPHA)))
        p.drawPath(_rounded_rect_path(left, top, plot_w, plot_h, 6))

        if not self._values:
            self._draw_center_text(p, W / 2, H / 2, "暂无数据", T.INK2)
            p.end()
            return

        vmax = max(self._values) or 1
        # 水平网格 + 刻度
        p.setPen(QPen(QColor(T.BORDER)))
        p.setFont(T.font(T.F_TINY))
        for i in range(5):
            y = top + plot_h - plot_h * i / 4
            p.drawLine(int(left), int(y), int(W - right), int(y))
            p.setPen(QPen(QColor(T.INK2)))
            p.drawText(QPointF(left - 6, y + 3),
                       str(round(vmax * i / 4)))
            p.setPen(QPen(QColor(T.BORDER)))

        n = len(self._values)
        bw = max(2.0, plot_w / n * 0.62)
        for i, v in enumerate(self._values):
            x0 = left + plot_w / n * (i + 0.5) - bw / 2
            h = plot_h * v / vmax
            if h < 2:  # 极矮柱：跳过，避免圆角变形
                continue
            y0 = top + plot_h - h
            r = min(bw / 2, 3.0, h / 2)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(QColor(self._color)))
            p.drawPath(_rounded_rect_path(x0, y0, bw, h, r))
            p.setPen(QPen(QColor(T.INK2)))
            p.setFont(T.font(T.F_TINY))
            p.drawText(QPointF(x0 + bw / 2 - 4, y0 - 4), str(v))

        # x 轴标签（最多 ~14 个）
        step = max(1, (n + 13) // 14)
        p.setPen(QPen(QColor(T.INK2)))
        p.setFont(T.font(T.F_TINY))
        for i in range(0, n, step):
            x = left + plot_w / n * (i + 0.5)
            p.drawText(QPointF(x - 12, H - bottom + 10), self._labels[i])
        p.end()

    def _draw_center_text(self, p: QPainter, x: float, y: float,
                          text: str, color: str) -> None:
        p.setPen(QPen(QColor(color)))
        p.setFont(T.font(T.F_BODY))
        p.drawText(QPointF(x - 30, y), text)


class Heatmap(QWidget):
    """GitHub 风格活动热力图：列 = 最近 N 周，行 = 周一到周日。蓝紫新色阶。"""

    _WEEKDAYS = ["一", "二", "三", "四", "五", "六", "日"]

    def __init__(self, parent: QWidget | None, width: int = 430, height: int = 150,
                 weeks: int = 17) -> None:
        super().__init__(parent)
        self._weeks = max(4, int(weeks))
        self._counts: dict[str, int] = {}
        self.setMinimumSize(width, height)

    def set_counts(self, counts: dict) -> None:
        self._counts = {str(k): int(v) for k, v in (counts or {}).items()}
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()
        cell, gap = 12, 3
        left, top = 26, 16

        today = _dt.date.today()
        start = today - _dt.timedelta(weeks=self._weeks - 1)
        start = start - _dt.timedelta(days=start.weekday())

        p.setPen(Qt.PenStyle.NoPen)
        for w in range(self._weeks):
            day = start + _dt.timedelta(days=w * 7)
            for d in range(7):
                x = left + w * (cell + gap)
                y = top + d * (cell + gap)
                p.setBrush(QBrush(QColor(_level_color(self._counts.get(day.isoformat(), 0)))))
                p.drawPath(_rounded_rect_path(x, y, cell, cell, 3))
                day += _dt.timedelta(days=1)

        # 左侧星期标签
        p.setPen(QPen(QColor(T.INK2)))
        p.setFont(T.font(T.F_TINY))
        for d, label in enumerate(self._WEEKDAYS):
            y = top + d * (cell + gap) + cell / 2
            p.drawText(QPointF(left - 6 - 6, y + 3), label)

        # 图例
        legend_y = H - 6
        for i, label in enumerate(["少", "中", "多"]):
            x = left + self._weeks * (cell + gap) - 84 + i * 36
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(QColor(T.HEAT_LEVELS[i + 1])))
            p.drawPath(_rounded_rect_path(x, legend_y - 8, 8, 8, 2))
            p.setPen(QPen(QColor(T.INK2)))
            p.drawText(QPointF(x + 12, legend_y - 2), label)
        p.end()


class DonutChart(QWidget):
    """环形图：来源分布 + 中心总计 + 右侧图例。set_items 后自动重绘。"""

    def __init__(self, parent: QWidget | None, width: int = 430, height: int = 210) -> None:
        super().__init__(parent)
        self._items: list[tuple[str, int, str]] = []
        self.setMinimumSize(width, height)

    def set_items(self, items: list) -> None:
        self._items = [(str(lbl), int(val), str(col)) for lbl, val, col in items]
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()
        total = sum(v for _, v, _ in self._items)
        if not total:
            self._draw_center(p, W / 2, H / 2, "暂无数据", T.INK2)
            p.end()
            return

        cx, cy = 108, H / 2
        r_out, r_in = 66, 42
        start = 90.0

        # 各扇区
        for label, value, color in self._items:
            extent = -360.0 * value / total
            p.setPen(QPen(QColor(T.BG), 2))
            p.setBrush(QBrush(QColor(color)))
            p.drawPie(QRectF(cx - r_out, cy - r_out, r_out * 2, r_out * 2),
                      int(start * 16), int(extent * 16))
            start += extent

        # 中心孔（半透明玻璃）
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(QColor(*T.GLASS_RGB, T.GLASS_ALPHA)))
        p.drawEllipse(QRectF(cx - r_in, cy - r_in, r_in * 2, r_in * 2))

        # 中心总计
        p.setPen(QPen(QColor(T.INK)))
        p.setFont(T.font(T.F_H))
        p.drawText(QPointF(cx - 16, cy - 6), str(total))
        p.setPen(QPen(QColor(T.INK2)))
        p.setFont(T.font(T.F_TINY))
        p.drawText(QPointF(cx - 16, cy + 16), "总计")

        # 右侧图例
        ly = H / 2 - (len(self._items) * 19) / 2
        lx = cx + r_out + 24
        p.setPen(Qt.PenStyle.NoPen)
        for label, value, color in self._items:
            p.setBrush(QBrush(QColor(color)))
            p.drawPath(_rounded_rect_path(lx, ly, 10, 10, 2))
            p.setPen(QPen(QColor(T.INK)))
            p.setFont(T.font(T.F_SMALL))
            p.drawText(QPointF(lx + 16, ly + 9),
                       f"{label} {value}（{value / total * 100:.0f}%）")
            p.setPen(Qt.PenStyle.NoPen)
            ly += 19
        p.end()

    def _draw_center(self, p: QPainter, x: float, y: float,
                     text: str, color: str) -> None:
        p.setPen(QPen(QColor(color)))
        p.setFont(T.font(T.F_BODY))
        p.drawText(QPointF(x - 30, y), text)
