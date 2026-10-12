"""tkinter Canvas 图表组件（Linear/shadcn 风暗色）：柱状图 / 热力图 / 环形图。

纯 Canvas 矢量绘制，无第三方图表依赖；颜色 / 字体全部引用 theme.py。
柱状图用圆角柱，环形图中心显示总计，热力图用蓝紫新色阶。
"""
from __future__ import annotations

import datetime as _dt
import tkinter as tk

from . import theme as T
from .ui import rounded_rect


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


class BarChart(tk.Canvas):
    """柱状图：圆角柱 + 网格 + x 轴标签 + 柱顶数值。

    set_data(labels: list[str], values: list[int]) 后自动重绘。
    """

    def __init__(self, parent: tk.Widget, width: int = 430, height: int = 210,
                 color: str = T.ACCENT, **kw) -> None:
        super().__init__(parent, width=width, height=height,
                         bg=parent.cget("bg"), highlightthickness=0, **kw)
        self._color = color
        self._labels: list[str] = []
        self._values: list[int] = []

    def set_data(self, labels: list[str], values: list[int]) -> None:
        self._labels = [str(x) for x in labels]
        self._values = [int(x) for x in values]
        self._draw()

    def _draw(self) -> None:
        self.delete("all")
        c = self
        W = int(c["width"])
        H = int(c["height"])
        left, right, top, bottom = 34, 10, 16, 26
        plot_w = W - left - right
        plot_h = H - top - bottom
        rounded_rect(c, left, top, W - right, H - bottom, 6,
                     fill=T.CARD, outline=T.BORDER)

        if not self._values:
            c.create_text(W / 2, H / 2, text="暂无数据", fill=T.INK2, font=T.F_BODY)
            return

        vmax = max(self._values) or 1
        for i in range(5):
            y = top + plot_h - plot_h * i / 4
            c.create_line(left, y, W - right, y, fill=T.BORDER)
            c.create_text(left - 6, y, text=str(round(vmax * i / 4)), anchor="e",
                          fill=T.INK2, font=T.F_TINY)

        n = len(self._values)
        if n == 0:
            return
        bw = max(2.0, plot_w / n * 0.62)
        for i, v in enumerate(self._values):
            x0 = left + plot_w / n * (i + 0.5) - bw / 2
            h = plot_h * v / vmax
            if h < 2:  # 极矮柱：画一条基准线，避免圆角变形
                continue
            y0 = top + plot_h - h
            r = min(bw / 2, 3.0, h / 2)
            rounded_rect(c, x0, y0, x0 + bw, top + plot_h, r,
                         fill=self._color, outline="")
            c.create_text(x0 + bw / 2, y0 - 6, text=str(v), fill=T.INK2,
                          font=T.F_TINY)

        step = max(1, (n + 13) // 14)  # 最多显示 ~14 个标签
        for i in range(0, n, step):
            x = left + plot_w / n * (i + 0.5)
            c.create_text(x, H - bottom + 10, text=self._labels[i], fill=T.INK2,
                          font=T.F_TINY)


class Heatmap(tk.Canvas):
    """GitHub 风格活动热力图：列 = 最近 N 周，行 = 周一到周日。蓝紫新色阶。

    set_counts(counts: dict[str, int])，key 为 ISO 日期 "YYYY-MM-DD"。
    """

    _WEEKDAYS = ["一", "二", "三", "四", "五", "六", "日"]

    def __init__(self, parent: tk.Widget, width: int = 430, height: int = 150,
                 weeks: int = 17, **kw) -> None:
        super().__init__(parent, width=width, height=height,
                         bg=parent.cget("bg"), highlightthickness=0, **kw)
        self._weeks = max(4, int(weeks))
        self._counts: dict[str, int] = {}

    def set_counts(self, counts: dict) -> None:
        self._counts = {str(k): int(v) for k, v in (counts or {}).items()}
        self._draw()

    def _draw(self) -> None:
        self.delete("all")
        c = self
        W = int(c["width"])
        H = int(c["height"])
        cell, gap = 12, 3
        left, top = 26, 16

        today = _dt.date.today()
        start = today - _dt.timedelta(weeks=self._weeks - 1)
        start = start - _dt.timedelta(days=start.weekday())  # 对齐周一

        for w in range(self._weeks):
            day = start + _dt.timedelta(days=w * 7)
            for d in range(7):
                x = left + w * (cell + gap)
                y = top + d * (cell + gap)
                rounded_rect(c, x, y, x + cell, y + cell, 3,
                             fill=_level_color(self._counts.get(day.isoformat(), 0)),
                             outline="")
                day += _dt.timedelta(days=1)

        for d, label in enumerate(self._WEEKDAYS):
            c.create_text(left - 6, top + d * (cell + gap) + cell / 2, text=label,
                          anchor="e", fill=T.INK2, font=T.F_TINY)

        # 图例
        legend_y = H - 6
        for i, label in enumerate(["少", "中", "多"]):
            x = left + self._weeks * (cell + gap) - 84 + i * 36
            rounded_rect(c, x, legend_y - 8, x + 8, legend_y, 2,
                         fill=T.HEAT_LEVELS[i + 1], outline="")
            c.create_text(x + 12, legend_y - 4, anchor="w", text=label,
                          fill=T.INK2, font=T.F_TINY)


class DonutChart(tk.Canvas):
    """环形图：来源分布 + 中心总计 + 右侧图例。

    set_items(items: list[tuple[label, value, color]])。
    """

    def __init__(self, parent: tk.Widget, width: int = 430, height: int = 210, **kw) -> None:
        super().__init__(parent, width=width, height=height,
                         bg=parent.cget("bg"), highlightthickness=0, **kw)
        self._items: list[tuple[str, int, str]] = []

    def set_items(self, items: list) -> None:
        self._items = [(str(lbl), int(val), str(col)) for lbl, val, col in items]
        self._draw()

    def _draw(self) -> None:
        self.delete("all")
        c = self
        W = int(c["width"])
        H = int(c["height"])
        total = sum(v for _, v, _ in self._items)
        if not total:
            c.create_text(W / 2, H / 2, text="暂无数据", fill=T.INK2, font=T.F_BODY)
            return

        cx, cy = 108, H / 2
        r_out, r_in = 66, 42
        start = 90.0
        for label, value, color in self._items:
            extent = -360.0 * value / total
            c.create_arc(cx - r_out, cy - r_out, cx + r_out, cy + r_out,
                         start=start, extent=extent, fill=color, outline=T.BG, width=2)
            start += extent
        c.create_oval(cx - r_in, cy - r_in, cx + r_in, cy + r_in, fill=T.CARD, outline="")

        # 中心总计
        c.create_text(cx, cy - 8, text=str(total), fill=T.INK, font=T.F_H)
        c.create_text(cx, cy + 12, text="总计", fill=T.INK2, font=T.F_TINY)

        ly = H / 2 - (len(self._items) * 19) / 2
        lx = cx + r_out + 24
        for label, value, color in self._items:
            rounded_rect(c, lx, ly, lx + 10, ly + 10, 2, fill=color, outline="")
            c.create_text(lx + 16, ly + 5, anchor="w", fill=T.INK, font=T.F_SMALL,
                          text=f"{label} {value}（{value / total * 100:.0f}%）")
            ly += 19
