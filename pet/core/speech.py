"""台词气泡（大肥鱼桌宠风）：QWidget 无边框圆角卡片 + 底部小三角指向宠物。

触发：宠物心情变化 / 喂食 / 打卡 / 番茄完成 / 健康提醒时，从台词池随机挑一句，
气泡出现在宠物上方，停留 SHOW_MS 后上浮 + 淡出动画。

气泡是独立顶层 Tool 窗口（透明背景、鼠标点击穿透），不抢焦点、不挡交互。
"""
from __future__ import annotations

import random

from PySide6.QtCore import Qt, QRectF, QPointF, QPropertyAnimation, QEasingCurve, QTimer
from PySide6.QtGui import (
    QPainter, QColor, QPainterPath, QFontMetrics, QPen, QBrush, QLinearGradient,
)
from PySide6.QtWidgets import QWidget

from ..widgets import theme as T

SHOW_MS = 3200        # 气泡停留时长
FADE_MS = 500         # 淡出时长
RISE_PX = 14          # 上浮距离
MAX_WIDTH = 200       # 气泡最大宽度
MIN_WIDTH = 72        # 气泡最小宽度
PAD_X, PAD_Y = 12, 9  # 文字内边距
TAIL_H = 8            # 底部三角高
RADIUS = 12           # 圆角


# ---------------------------------------------------------------------------
# 台词池（按心情分类；喂食/打卡/番茄/健康提醒可指定心情触发）
# ---------------------------------------------------------------------------
LINES: dict[str, list[str]] = {
    "happy": [
        "今天也元气满满！",
        "主人辛苦啦，摸摸～",
        "汪！要不要一起整理一下笔记？",
        "你专注的样子真好看 ✨",
        "刚才的小目标完成了没？",
        "阳光真好，适合学习！",
    ],
    "normal": [
        "需要我提醒你喝口水吗？",
        "累了就站起来走走～",
        "要不要定个番茄钟？",
        "还有待办没做哦。",
        "我在旁边陪着你～",
    ],
    "hungry": [
        "汪呜……我有点饿了…",
        "肚子咕咕叫了，喂喂我嘛…",
        "记得每日打卡领食物哦！",
        "好想啃一口小饼干…",
    ],
    "praise": [
        "太棒了！继续保持！",
        "哇，主人真厉害！",
        "今天就到此为止吧，好好休息！",
    ],
}

_WEIGHTS: tuple[str, ...] = ("happy", "normal", "hungry")


def random_line(mood: str | None = None, *, extra: str | None = None) -> str:
    """从台词池随机取一句。

    mood 指定心情；extra 为 None 时按权重从 happy/normal/hungry 抽。
    返回空串表示无可说台词（不会发生，池恒非空）。
    """
    pool: list[str] = []
    if extra:
        pool += LINES.get(extra, [])
    if mood:
        pool += LINES.get(mood, [])
    if not pool:
        pool = LINES[random.choice(_WEIGHTS)]
    return random.choice(pool)


# ---------------------------------------------------------------------------
# 气泡窗口
# ---------------------------------------------------------------------------
class SpeechBubble(QWidget):
    """台词气泡。say() 显示并自动淡出；可重复调用（会重置计时）。"""

    def __init__(self, *, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowTransparentForInput   # 鼠标点击穿透
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self._text = ""
        self._font = T.font(T.F_SMALL)

        self._hold = QTimer(self)
        self._hold.setSingleShot(True)
        self._hold.timeout.connect(self._fade_out)

        self._anim = QPropertyAnimation(self, b"windowOpacity", self)
        self._anim.setDuration(FADE_MS)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        self.hide()

    # ------------------------------------------------------------------
    # 对外 API
    # ------------------------------------------------------------------
    def say(self, text: str, *, anchor: tuple[int, int] | None = None,
            bubble_w: int | None = None, bubble_h: int | None = None) -> None:
        """在 (anchor_x, anchor_y) 上方显示台词。

        anchor 为宠物窗口左上角全局坐标；bubble_w/h 为宠物尺寸用于居中。
        anchor 为 None 时显示在屏幕中央偏上（兜底）。
        """
        self._text = text or "…"
        self.setFont(self._font)

        # 计算自适应尺寸
        fm = QFontMetrics(self._font)
        width = min(MAX_WIDTH, max(MIN_WIDTH, fm.horizontalAdvance(self._text) + PAD_X * 2))
        lines = self._wrap_lines(self._text, width - PAD_X * 2)
        height = fm.lineSpacing() * len(lines) + PAD_Y * 2 + TAIL_H

        # 定位：anchor 上方居中
        screen = self.screen()
        if anchor is not None and screen is not None:
            geo = screen.availableGeometry()
            x = anchor[0] + (bubble_w or 0) // 2 - width // 2
            y = anchor[1] - height - 4
            # 屏幕边缘夹取
            x = max(geo.left(), min(geo.right() - width, x))
            y = max(geo.top() + 4, y)
        else:
            x = max(0, width)  # 无 anchor 时由调用方 move
            y = 0
        self.setFixedSize(width, height)
        self.move(x, y)

        self.setWindowOpacity(1.0)
        self.show()
        self.raise_()
        self._hold.start(SHOW_MS)

    def dismiss(self) -> None:
        """立即隐藏（托盘退出等场景）。"""
        self._anim.stop()
        self.hide()

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------
    def _wrap_lines(self, text: str, max_w: int) -> list[str]:
        fm = QFontMetrics(self._font)
        lines: list[str] = []
        for para in text.split("\n"):
            if not para:
                lines.append("")
                continue
            cur = ""
            for ch in para:
                if fm.horizontalAdvance(cur + ch) > max_w:
                    lines.append(cur)
                    cur = ch
                else:
                    cur += ch
            lines.append(cur)
        return lines

    def _fade_out(self) -> None:
        self._anim.stop()
        self._anim.setStartValue(self.windowOpacity())
        self._anim.setEndValue(0.0)
        self._anim.finished.connect(self.hide)
        self._anim.start()

    # ------------------------------------------------------------------
    # 绘制：圆角卡 + 底部三角 + 文字
    # ------------------------------------------------------------------
    def paintEvent(self, event) -> None:  # noqa: N802
        if not self._text:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        body_h = h - TAIL_H
        r = RADIUS

        # 主体圆角矩形
        path = QPainterPath()
        path.addRoundedRect(QRectF(0.5, 0.5, w - 1, body_h - 1), r, r)
        p.setPen(QPen(QColor(T.CARD_ACTIVE), 1))
        p.setBrush(QBrush(QColor(T.CARD)))
        p.drawPath(path)

        # 底部小三角（指向宠物）
        tail = QPainterPath()
        tail.moveTo(w / 2 - TAIL_H, body_h - 1)
        tail.lineTo(w / 2, h - 1)
        tail.lineTo(w / 2 + TAIL_H, body_h - 1)
        tail.closeSubpath()
        p.setPen(QPen(QColor(T.CARD_ACTIVE), 1))
        p.setBrush(QBrush(QColor(T.CARD)))
        p.drawPath(tail)

        # 文字
        p.setPen(QColor(T.INK))
        p.setFont(self._font)
        fm = QFontMetrics(self._font)
        lines = self._wrap_lines(self._text, w - PAD_X * 2)
        y = (body_h - fm.lineSpacing() * len(lines)) / 2 + fm.ascent()
        for line in lines:
            p.drawText(QPointF(PAD_X, y), line)
            y += fm.lineSpacing()
        p.end()
