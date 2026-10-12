"""鸭子精灵窗口（PySide6 版）：5 动作随机循环动画 + 拖拽移动 + 点击回调。

对应原 tkinter petwidget.py 的 PetWindow，绘制层替换为预渲染的 3D 鸭子帧：
- 鸭子模型无内置动画，且本环境 QtQuick3D 不渲染 3D，故用软渲染器预渲染
  5 组动作帧（pet/resources/duck/），运行时 QTimer 按动作状态机轮播，纯 2D 开销；
- 动作集：idle 静止 / spin 转身 / jump 跳跃 / tilt 歪头 / bow 点头；
- 动作按权重随机循环（idle 权重最高），idle 连播数轮增强"静止感"；
- 鸭子保持模型原貌，**不叠加任何 2D 表情**（模型自带眼睛/鸭嘴）；
- 心情（happy/hungry/normal）影响呼吸动作（y 轴正弦微弹跳幅度/基线）；
- 透明无边框顶层窗口（WA_TranslucentBackground + FramelessWindowHint），Windows 拖拽移动；
- 拖拽阈值 6px：超过判拖拽，未超过按点击触发 on_click(x_root, y_root)。
"""
from __future__ import annotations

import json
import math
import random
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QRectF
from PySide6.QtGui import QPainter, QPixmap, QImage
from PySide6.QtWidgets import QApplication, QWidget

TICK_MS = 40        # 动画驱动帧间隔（呼吸 + 状态机节拍）
DRAG_THRESHOLD = 6  # 拖拽判定阈值（px）
ACTION_GAP_MS = 7000  # 动作间隔：非 idle 动作播完回静止，静止满 7 秒才随机切下一动作
PET_SIZE_CM = 2.5     # 鸭子物理尺寸（厘米），按屏幕物理 DPI 换算像素（1.5→2.5 递增）

DUCK_DIR = Path(__file__).resolve().parents[1] / "resources" / "duck"
META_FILE = DUCK_DIR / "duck_meta.json"


class Sprite(QWidget):
    """鸭子宠物窗口。on_click(x_root, y_root) 在点击（非拖拽）时调用。"""

    def __init__(self, on_click=None, *, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._on_click = on_click
        self._phase = 0
        self._mood = "happy"   # happy | normal | hungry（动作幅度）
        self._drag_anchor: tuple[int, int] | None = None
        self._press_xy: tuple[int, int] | None = None
        self._moved = False

        self._actions: dict = {}     # 动作名 -> {frames, frame_ms, weight, rounds}
        self._order: list[str] = []  # 动作名顺序
        self._load_resources()

        # 动作状态机
        self._action = "idle"
        self._frame_idx = 0
        self._round = 0
        self._tick_remain = 0    # 距换帧剩余 tick 数（帧间隔换算）
        self._action_start_tick = 0  # 当前动作开始的 tick（phase），用于 7 秒间隔计时

        # 透明无边框顶层窗口
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        px = self._cm_to_px(PET_SIZE_CM)
        self.setFixedSize(px, px)

        self._spr_x, self._spr_y = self._initial_pos()

        # 动画：动作状态机 + 呼吸
        self._tick = QTimer(self)
        self._tick.setInterval(TICK_MS)
        self._tick.timeout.connect(self._on_tick)
        self._tick.start()

        self.move(self._spr_x, self._spr_y)

    # ------------------------------------------------------------------
    # 资源加载
    # ------------------------------------------------------------------
    def _load_resources(self) -> None:
        """读动作表 meta + 加载全部动作帧。资源缺失时抛异常（启动即暴露问题）。"""
        if not META_FILE.exists():
            raise RuntimeError(f"鸭子动作表缺失: {META_FILE}")
        meta = json.loads(META_FILE.read_text(encoding="utf-8"))
        actions = meta.get("actions") or {}
        if not actions:
            raise RuntimeError("鸭子动作表为空")
        self._order = meta.get("order") or list(actions.keys())
        for name, cfg in actions.items():
            n = int(cfg.get("frames") or 0)
            frames: list[QPixmap] = []
            for i in range(n):
                img = QImage(str(DUCK_DIR / f"{name}_{i:02d}.png"))
                if img.isNull():
                    raise RuntimeError(f"鸭子帧资源缺失: {DUCK_DIR / f'{name}_{i:02d}.png'}")
                frames.append(QPixmap.fromImage(img))
            self._actions[name] = {
                "frames": frames,
                "frame_ms": int(cfg.get("frame_ms") or 100),
                "weight": float(cfg.get("weight") or 1.0),
                "rounds": 1,   # 统一 1 轮：idle 静止时长由 ACTION_GAP_MS 控制
            }

    # ------------------------------------------------------------------
    # 布局
    # ------------------------------------------------------------------
    @staticmethod
    def _cm_to_px(cm: float) -> int:
        """厘米 → Qt 逻辑像素：按屏幕物理尺寸换算，保证屏幕上恰好显示该物理长度。"""
        screen = QApplication.primaryScreen()
        px = 60
        if screen is not None:
            phys = screen.physicalSize()        # QSizeF：屏幕物理尺寸（毫米）
            geo = screen.geometry()
            if phys.width() > 0 and geo.width() > 0:
                px = round(cm * geo.width() / (phys.width() / 10.0))
        return max(24, px)

    def _initial_pos(self) -> tuple[int, int]:
        """桌面右下区（屏幕宽 - 190, 高一半略上）。"""
        screen = self.screen()
        if screen is None:
            return 0, 0
        geo = screen.availableGeometry()
        return geo.right() - 190, max(geo.top() + 60, geo.center().y() - 110)

    # ------------------------------------------------------------------
    # 对外 API
    # ------------------------------------------------------------------
    def show_pet(self) -> None:
        self.show()

    def hide_pet(self) -> None:
        self.hide()

    def blink(self) -> None:
        """双击回调。鸭子模型不眨眼，仅触发一次重绘（保留 API 兼容）。"""
        self.update()

    def set_mood(self, mood: str) -> None:
        """'happy' | 'normal' | 'hungry'：影响呼吸动作，立即重绘。"""
        if mood in ("happy", "normal", "hungry"):
            self._mood = mood
            self.update()

    def current_mood(self) -> str:
        return self._mood

    # ------------------------------------------------------------------
    # 动作状态机
    # ------------------------------------------------------------------
    def _pick_next(self) -> str:
        """从非 idle 动作按权重随机选（idle 是静止填充，不参与随机）。"""
        names = [n for n in self._order if n != "idle"]
        weights = [self._actions[n]["weight"] for n in names]
        return random.choices(names, weights=weights, k=1)[0]

    def _on_tick(self) -> None:
        self._phase += 1
        cfg = self._actions[self._action]
        self._tick_remain += TICK_MS
        if self._tick_remain >= cfg["frame_ms"]:
            self._tick_remain = 0
            self._frame_idx += 1
            if self._frame_idx >= len(cfg["frames"]):
                self._frame_idx = 0
                self._round += 1
                if self._round >= cfg["rounds"]:
                    self._round = 0
                    self._on_action_ended()
        self.update()

    def _on_action_ended(self) -> None:
        """动作播完一轮：非 idle 动作回静止；idle 静止满 ACTION_GAP_MS 再随机切下一动作。"""
        elapsed = (self._phase - self._action_start_tick) * TICK_MS
        if self._action == "idle":
            if elapsed >= ACTION_GAP_MS:
                self._action = self._pick_next()
                self._action_start_tick = self._phase
        else:
            self._action = "idle"
            self._action_start_tick = self._phase

    # ------------------------------------------------------------------
    # 绘制：当前动作帧 + 呼吸偏移（不叠加表情）
    # ------------------------------------------------------------------
    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        pm = self._actions[self._action]["frames"][self._frame_idx]

        # 心情 → 呼吸动作：happy 弹跳高、hungry 垂头（基线下沉+幅度小）、normal 居中
        if self._mood == "happy":
            amp, base = 4.0, 0.0
        elif self._mood == "hungry":
            amp, base = 2.0, 2.0
        else:
            amp, base = 3.0, 0.0
        by = base + amp * math.sin(self._phase * 0.02)

        sz = self.width()   # 窗口固定为 PET_SIZE_CM 换算的正方形
        p.drawPixmap(QRectF(0, by, sz, sz), pm,
                     QRectF(0, 0, pm.width(), pm.height()))
        p.end()

    # ------------------------------------------------------------------
    # 拖拽 / 点击
    # ------------------------------------------------------------------
    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_anchor = (event.globalPosition().toPoint().x() - self.x(),
                                 event.globalPosition().toPoint().y() - self.y())
            self._press_xy = (event.globalPosition().toPoint().x(),
                              event.globalPosition().toPoint().y())
            self._moved = False

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._drag_anchor is None:
            return
        gx, gy = event.globalPosition().toPoint().x(), event.globalPosition().toPoint().y()
        if self._press_xy is not None:
            dx, dy = gx - self._press_xy[0], gy - self._press_xy[1]
            if abs(dx) + abs(dy) > DRAG_THRESHOLD:
                self._moved = True
        self.move(gx - self._drag_anchor[0], gy - self._drag_anchor[1])

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        gx = event.globalPosition().toPoint().x()
        gy = event.globalPosition().toPoint().y()
        if not self._moved and self._press_xy is not None and self._on_click:
            self._on_click(gx, gy)
        self._drag_anchor = None
        self._press_xy = None

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        self.blink()
