"""桌面宠物窗口（tkinter 版）：卡通柴犬 + 呼吸/眨眼动画 + 拖拽移动 + 点击回调。

- 透明色抠底（Windows `-transparentcolor`），canvas 画柴犬；
- 呼吸弹跳 + 周期性眨眼；点击触发回调；拖拽移动。
"""
from __future__ import annotations

import math
import random
import time
import tkinter as tk

FUR = "#E8B87F"
EAR_INNER = "#F5C999"
EYE = "#3B2A20"
NOSE = "#7A5230"
BLUSH = "#FF8282"
MOUTH = "#7A5230"
TRANSPARENT = "#FF00FF"  # 透明色（Windows 抠底）

SIZE = 136


class PetWindow:
    """柴犬宠物窗口。on_click(x_root, y_root) 在点击（非拖拽）时调用。"""

    def __init__(self, root: tk.Tk, on_click) -> None:
        self._on_click = on_click
        self._phase = 0.0
        self._eyes_open = True
        self._blink_until = 0.0
        self._mood = "happy"   # happy | normal | hungry
        self._drag_anchor = None
        self._press_xy = None
        self._moved = False

        self.root = tk.Toplevel(root)
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.attributes("-transparentcolor", TRANSPARENT)
        self.root.configure(bg=TRANSPARENT)

        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        self.root.geometry(f"{SIZE}x{SIZE}+{sw - 190}+{max(60, sh // 2 - 110)}")

        self.canvas = tk.Canvas(
            self.root, width=SIZE, height=SIZE,
            bg=TRANSPARENT, highlightthickness=0,
        )
        self.canvas.pack()

        # 动画与眨眼
        self._tick_after = root.after(40, self._tick)
        self._blink_after = root.after(3000, self._schedule_blink)

        # 交互
        self.canvas.bind("<Button-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.canvas.bind("<Double-Button-1>", lambda _e: self.blink())

    # ------------------------------------------------------------------
    def show(self) -> None:
        self.root.deiconify()

    def hide(self) -> None:
        self.root.withdraw()

    def destroy(self) -> None:
        if self._tick_after:
            self.root.after_cancel(self._tick_after)
        if self._blink_after:
            self.root.after_cancel(self._blink_after)
        self.root.destroy()

    def blink(self) -> None:
        self._eyes_open = False
        self._blink_until = time.monotonic() + 0.18

    def set_mood(self, mood: str) -> None:
        """'happy' | 'normal' | 'hungry'：影响表情，立即重绘。"""
        if mood in ("happy", "normal", "hungry"):
            self._mood = mood
            self._redraw()

    def _schedule_blink(self) -> None:
        self.blink()
        self._blink_after = self.root.after(
            2500 + int(1500 * random.random()), self._schedule_blink
        )

    def _tick(self) -> None:
        self._phase += 1
        if time.monotonic() > self._blink_until:
            self._eyes_open = True
        self._redraw()
        self._tick_after = self.root.after(40, self._tick)

    # ------------------------------------------------------------------
    def _redraw(self) -> None:
        c = self.canvas
        c.delete("all")
        cx, cy = SIZE / 2, SIZE / 2 + 5
        r = SIZE * 0.46
        cy += 2.0 * math.sin(self._phase * 0.004)  # 呼吸弹跳

        # 耳朵（三角）
        for sign in (-1, 1):
            c.create_polygon(
                cx + sign * r * 0.82, cy - r * 0.55,
                cx + sign * r * 0.30, cy - r * 0.98,
                cx + sign * r * 0.18, cy - r * 0.30,
                fill=FUR, outline="",
            )
            c.create_polygon(
                cx + sign * r * 0.58, cy - r * 0.48,
                cx + sign * r * 0.32, cy - r * 0.82,
                cx + sign * r * 0.24, cy - r * 0.42,
                fill=EAR_INNER, outline="",
            )
        # 脸
        c.create_oval(cx - r, cy - r * 0.92, cx + r, cy + r * 0.92, fill=FUR, outline="")

        # 眼睛（眨眼时 / 委屈时半闭，下弯线）
        eye_r = r * 0.12
        hungry = self._mood == "hungry"
        for sign in (-1, 1):
            ex, ey = cx + sign * r * 0.30, cy - r * 0.18
            if self._eyes_open and not hungry:
                c.create_oval(ex - eye_r, ey - eye_r, ex + eye_r, ey + eye_r, fill=EYE, outline="")
                c.create_oval(
                    ex - eye_r * 0.45, ey - eye_r * 0.5,
                    ex - eye_r * 0.05, ey - eye_r * 0.1,
                    fill="white", outline="",
                )
            else:
                c.create_arc(ex - eye_r, ey - eye_r * 0.6, ex + eye_r, ey + eye_r * 0.6,
                             start=180, extent=180, style="arc", outline=EYE, width=3)

        # 腮红
        br = r * 0.17
        for sign in (-1, 1):
            bx, by = cx + sign * r * 0.58, cy + r * 0.06
            c.create_oval(bx - br, by - br * 0.8, bx + br, by + br * 0.8, fill=BLUSH, outline="")

        # 鼻子
        c.create_oval(cx - r * 0.09, cy + r * 0.19, cx + r * 0.09, cy + r * 0.33,
                      fill=NOSE, outline="")
        # 嘴（按心情）：happy 微笑 / normal 平缓 / hungry 撅嘴
        mw = r * 0.26
        if self._mood == "hungry":
            c.create_arc(cx - mw, cy + r * 0.30, cx + mw, cy + r * 0.30 + r * 0.30,
                         start=180, extent=180, style="arc", outline=MOUTH, width=2)
        elif self._mood == "normal":
            c.create_line(cx - mw, cy + r * 0.40, cx + mw, cy + r * 0.40,
                          fill=MOUTH, width=2, capstyle="round")
        else:
            c.create_arc(cx - mw, cy + r * 0.26, cx + mw, cy + r * 0.56,
                         start=0, extent=180, style="arc", outline=MOUTH, width=2)

    # ------------------------------------------------------------------
    def _on_press(self, event) -> None:
        self._drag_anchor = (event.x_root - self.root.winfo_x(),
                             event.y_root - self.root.winfo_y())
        self._press_xy = (event.x_root, event.y_root)
        self._moved = False

    def _on_drag(self, event) -> None:
        if self._drag_anchor is None:
            return
        dx = event.x_root - self._press_xy[0]
        dy = event.y_root - self._press_xy[1]
        if abs(dx) + abs(dy) > 6:
            self._moved = True
        x = event.x_root - self._drag_anchor[0]
        y = event.y_root - self._drag_anchor[1]
        self.root.geometry(f"+{x}+{y}")

    def _on_release(self, event) -> None:
        if not self._moved and self._press_xy is not None:
            self._on_click(event.x_root, event.y_root)
        self._drag_anchor = None
        self._press_xy = None
