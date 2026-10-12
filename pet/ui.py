"""桌面 UI 组件库：Orbis 太空风（深空蓝 + 霓虹绿 + liquid-glass 玻璃）通用组件。

中圆角按钮、玻璃数据卡、侧边栏导航、玻璃渐变边缘卡片、iMessage 风聊天气泡、
简洁页头（含霓虹绿下划线）。全部颜色与字体引用 theme.py，统一 space 主题。
"""
from __future__ import annotations

import tkinter as tk
import tkinter.font as tkfont
from typing import Callable

from . import theme as T


def rounded_rect(c: tk.Canvas, x0: float, y0: float, x1: float, y1: float,
                 r: float, **kw) -> int:
    """在 Canvas 上画圆角矩形（smooth 多边形近似），返回图形 id。"""
    # 圆角半径保护：负值或超过短边一半会产生退化多边形（TclError），
    # 导致调用方 delete("all") 后绘制中断、控件内容丢失。
    r = max(0, min(r, min(x1 - x0, y1 - y0) / 2))
    pts = [x0 + r, y0, x1 - r, y0, x1, y0, x1, y0 + r, x1, y1 - r, x1, y1,
           x1 - r, y1, x0 + r, y1, x0, y1, x0, y1 - r, x0, y0 + r, x0, y0]
    return c.create_polygon(pts, smooth=True, **kw)


def glass_shine(parent: tk.Widget) -> None:
    """玻璃卡顶部 1px 高光线（liquid-glass 顶部反光近似），place 于卡顶边缘。"""
    tk.Frame(parent, bg=T.GLASS_HI, height=1).place(x=0, y=0, relwidth=1.0)


def _measure(font: tuple, text: str) -> int:
    fam = font[0]
    size = font[1]
    weight = font[2] if len(font) > 2 and font[2] == "bold" else "normal"
    return tkfont.Font(family=fam, size=size, weight=weight).measure(text)


# ---------------------------------------------------------------------------
# 颜色插值（hover 平滑过渡动效，Pro Max 150-300ms 准则）
# ---------------------------------------------------------------------------
def _hex_to_rgb(hex_str: str) -> tuple[int, int, int]:
    h = hex_str.lstrip("#")
    if len(h) == 3:
        h = "".join(ch * 2 for ch in h)
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def _lerp_hex(a: str, b: str, t: float) -> str:
    """颜色线性插值：t=0→a，t=1→b。用于驱动平滑过渡动画。"""
    ra, ga, ba = _hex_to_rgb(a)
    rb, gb, bb = _hex_to_rgb(b)
    return "#{:02X}{:02X}{:02X}".format(
        round(ra + (rb - ra) * t),
        round(ga + (gb - ga) * t),
        round(ba + (bb - ba) * t),
    )


# ---------------------------------------------------------------------------
# 容器
# ---------------------------------------------------------------------------
class SoftCard(tk.Frame):
    """玻璃卡：CARD 深空底 + 1px 渐变边缘 + 顶部高光。内容直接 pack 进卡片。"""

    def __init__(self, parent: tk.Widget, *, padx: int = T.PAD_CARD,
                 pady: int = T.PAD_CARD, **kw) -> None:
        super().__init__(parent, bg=T.CARD, highlightthickness=1,
                         highlightbackground=T.GLASS_EDGE, padx=padx, pady=pady, **kw)
        glass_shine(self)


# 兼容旧引用（dashboard 用 ui.Card）
Card = SoftCard


def section_title(parent: tk.Widget, text: str, **kw) -> tk.Label:
    """卡片 / 区块内小节标题。背景跟随父容器，主文字色。"""
    return tk.Label(parent, text=text, bg=parent.cget("bg"), fg=T.INK,
                    font=T.F_H, anchor="w", **kw)


def message(parent: tk.Widget, text: str = "", color: str = T.INK2,
            bg: str | None = None, font=T.F_SMALL, **kw) -> tk.Label:
    """统一状态消息标签。"""
    return tk.Label(parent, text=text, bg=bg or parent.cget("bg"), fg=color,
                    font=font, anchor="w", **kw)


# ---------------------------------------------------------------------------
# 圆角按钮（中圆角，苹果风）
# ---------------------------------------------------------------------------
class CandyButton(tk.Canvas):
    """苹果风按钮：primary=蓝实底白字 / ghost=卡片底细描边，中圆角，hover 加深，支持 disabled。"""

    def __init__(self, parent: tk.Widget, text: str, command,
                 primary: bool = True, padx: int = 14, pady: int = 4,
                 icon: int | None = None, font=None, **kw) -> None:
        self._text = str(text)
        self._command = command
        self._primary = bool(primary)
        self._icon = icon
        self._padx = padx
        self._font = font or T.F_BODY
        self._enabled = True
        self._hover = False
        self._anim_after: str | None = None
        self._anim_step = 0
        # 当前实际填充色（动画渐变的起点），初始 = 基础态
        self._fill = T.ACCENT if self._primary else T.CARD
        self._fg = T.ON_ACCENT if self._primary else T.INK2
        self._outline = self._fill if self._primary else T.BORDER
        self._resize()
        super().__init__(parent, width=self._bw, height=self._bh,
                         bg=parent.cget("bg"), highlightthickness=0,
                         cursor="hand2", **kw)
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Button-1>", self._on_click)
        self._draw()

    # -- 动效 ------------------------------------------------------------
    def _cancel_anim(self) -> None:
        if self._anim_after is not None:
            try:
                self.after_cancel(self._anim_after)
            except tk.TclError:
                pass
            self._anim_after = None

    def _base_colors(self) -> tuple[str, str, str]:
        """基础态颜色（未 hover）。"""
        if self._primary:
            return T.ACCENT, T.ON_ACCENT, T.ACCENT
        return T.CARD, T.INK2, T.BORDER

    def _target_colors(self) -> tuple[str, str, str]:
        """hover 态颜色。"""
        if self._primary:
            return T.ACCENT_HOVER, T.ON_ACCENT, T.ACCENT_HOVER
        return T.CARD_HOVER, T.INK, T.BORDER

    def _animate(self, t_fill: str, t_fg: str, t_outline: str) -> None:
        """从当前色平滑渐变到目标色（MOTION_MS 分步插值，避免瞬时跳变）。"""
        self._cancel_anim()
        if not self._enabled:
            self._draw()
            return
        s_fill, s_fg, s_outline = self._fill, self._fg, self._outline
        if (s_fill, s_fg, s_outline) == (t_fill, t_fg, t_outline):
            self._draw()
            return
        steps = max(2, T.MOTION_STEPS)
        step_ms = max(10, T.MOTION_MS // steps)
        self._anim_step = 0

        def tick() -> None:
            self._anim_step += 1
            t = self._anim_step / steps
            self._fill = _lerp_hex(s_fill, t_fill, t)
            self._fg = _lerp_hex(s_fg, t_fg, t)
            self._outline = _lerp_hex(s_outline, t_outline, t)
            self._draw()
            if self._anim_step < steps:
                self._anim_after = self.after(step_ms, tick)
            else:
                self._anim_after = None

        tick()

    # -- 尺寸（按文本测量） -------------------------------------------------
    def _resize(self) -> None:
        f = self._font
        th = tkfont.Font(family=f[0], size=f[1],
                         weight=f[2] if len(f) > 2 and f[2] == "bold" else "normal"
                         ).metrics("linespace")
        self._bh = th + 8   # 上下各留 4px
        self._bw = _measure(f, self._text) + self._padx * 2 + 12

    # -- 状态 --------------------------------------------------------------
    def set_primary(self, primary: bool) -> None:
        self._primary = bool(primary)
        # 状态切换直接落到当前颜色（不做动画），避免与 hover 动画竞态
        self._cancel_anim()
        self._fill, self._fg, self._outline = (
            self._target_colors() if self._hover else self._base_colors())
        self._draw()

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = bool(enabled)
        self.configure(cursor="hand2" if enabled else "arrow")
        self._draw()

    def configure(self, **kw) -> None:
        # 本组件自定义选项单独处理，其余合法项交给真实 Canvas
        state = kw.pop("state", None)
        text = kw.pop("text", None)
        command = kw.pop("command", None)
        safe = {k: v for k, v in kw.items()
                if k in ("width", "height", "bg", "cursor", "highlightthickness")}
        if safe:
            tk.Canvas.configure(self, **safe)
        if state is not None:
            self._enabled = state != "disabled"
            tk.Canvas.configure(self, cursor="hand2" if self._enabled else "arrow")
        if text is not None and text != self._text:
            self._text = str(text)
            self._resize()
            tk.Canvas.configure(self, width=self._bw, height=self._bh)
        if command is not None:
            self._command = command
        self._draw()

    config = configure

    # -- 事件 --------------------------------------------------------------
    def _on_enter(self, _e: tk.Event) -> None:
        self._hover = True
        self._animate(*self._target_colors())

    def _on_leave(self, _e: tk.Event) -> None:
        self._hover = False
        self._animate(*self._base_colors())

    def _on_click(self, _e: tk.Event) -> None:
        if self._enabled and self._command:
            self._command()

    def destroy(self) -> None:
        self._cancel_anim()
        super().destroy()

    # -- 绘制 --------------------------------------------------------------
    def _draw(self) -> None:
        self.delete("all")
        c = self
        W, H = self._bw, self._bh
        r = min(8, H / 2)  # 中圆角（非全胶囊，更克制）
        if not self._enabled:
            fill, fg, outline = T.CARD_ACTIVE, T.MUTED, T.CARD_ACTIVE
        else:
            fill, fg, outline = self._fill, self._fg, self._outline
        rounded_rect(c, 1, 1, W - 1, H - 1, r, fill=fill, outline=outline, width=1)
        if not self._primary and self._enabled:
            # 玻璃次级按钮：顶部 1px 高光（liquid-glass 反光）
            c.create_line(r + 2, 1.7, W - r - 2, 1.7, fill=T.GLASS_HI)
        if self._icon is not None:
            # 线框图标 + 文字整体居中
            tw = _measure(self._font, self._text)
            total = tw + 16
            ix = W / 2 - total / 2
            c.create_text(ix + 8, H / 2, text=chr(self._icon),
                          font=(T.F_ICON, 11), fill=fg, anchor="center")
            c.create_text(ix + 16 + tw / 2, H / 2, text=self._text, fill=fg,
                          font=self._font)
        else:
            c.create_text(W / 2, H / 2, text=self._text, fill=fg, font=self._font)


def primary_button(parent: tk.Widget, text: str, command, **kw) -> CandyButton:
    """主按钮：蓝底白字。"""
    return CandyButton(parent, text, command, primary=True, **kw)


def ghost_button(parent: tk.Widget, text: str, command, **kw) -> CandyButton:
    """次级按钮：卡片底 + 中性细描边。"""
    return CandyButton(parent, text, command, primary=False, **kw)


def chip_button(parent: tk.Widget, text: str, command, selected: bool = False,
                **kw) -> CandyButton:
    """筛选 chip：选中蓝底白字，未选卡片底描边。"""
    return CandyButton(parent, text, command, primary=bool(selected),
                       padx=10, pady=2, font=T.F_SMALL, **kw)


def chip_set(btn: CandyButton, selected: bool) -> None:
    btn.set_primary(selected)


# ---------------------------------------------------------------------------
# 现代数据卡（emoji 小图标 + 大数字 + 副标题）
# ---------------------------------------------------------------------------
class CandyStatCard(tk.Canvas):
    """苹果风统计卡：线框图标（语义色）+ 标题（顶行）、大数字（中）、副标题（底），hover 轻变。

    icon 传 Segoe Fluent Icons 码位（int，见 theme.ICON）；用 color 语义色着色，
    替代 emoji（UI/UX Pro Max「图标不用 emoji」准则在 tkinter 的最优实现）。
    """

    def __init__(self, parent: tk.Widget, title: str, icon: int | None,
                 color: str = T.ACCENT, height: int = T.STAT_H, **kw) -> None:
        super().__init__(parent, height=height, bg=T.BG,
                         highlightthickness=0, **kw)
        self._title = title
        self._icon = icon
        self._color = color
        self._value = "—"
        self._sub = ""
        self._hover = False
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Configure>", lambda _e: self._draw())
        self._draw()

    def _on_enter(self, _e: tk.Event) -> None:
        self._hover = True
        self._draw()

    def _on_leave(self, _e: tk.Event) -> None:
        self._hover = False
        self._draw()

    def set(self, value, sub: str = "") -> None:
        self._value = str(value)
        self._sub = sub
        self._draw()

    def _font_for(self, text: str, maxw: int):
        for size, weight in ((22, "bold"), (15, "bold"), (12, "normal")):
            f = tkfont.Font(family=T.F_DISPLAY, size=size, weight=weight)
            if f.measure(text) <= maxw:
                return (T.F_DISPLAY, size, weight) if weight != "normal" else (T.F_DISPLAY, size)
        return T.F_SMALL

    def _draw(self) -> None:
        self.delete("all")
        c = self
        W = max(60, self.winfo_width())
        H = max(40, self.winfo_height())
        fill = T.CARD_HOVER if self._hover else T.CARD
        rounded_rect(c, 1, 1, W - 1, H - 1, T.RADIUS, fill=fill,
                     outline=T.GLASS_EDGE, width=1)
        # 玻璃顶部高光（liquid-glass 反光）
        c.create_line(T.RADIUS + 2, 1.7, W - T.RADIUS - 2, 1.7, fill=T.GLASS_HI)

        # 顶行：线框图标（语义色着色）+ 标题
        pad = 18
        if self._icon is not None:
            c.create_text(pad, 22, text=chr(self._icon), anchor="w",
                          font=(T.F_ICON, 13), fill=self._color)
            c.create_text(pad + 22, 22, text=self._title, anchor="w",
                          fill=T.INK2, font=T.F_SMALL)
        else:
            c.create_text(pad, 22, text=self._title, anchor="w",
                          fill=T.INK2, font=T.F_SMALL)

        # 中部：大数字（主文字色）→ 底部：副标题（弱化）
        f = self._font_for(self._value, W - pad * 2)
        c.create_text(pad, H / 2 + 8, text=self._value, anchor="w", fill=T.INK,
                      font=f)
        if self._sub:
            c.create_text(pad, H - 14, text=self._sub, anchor="w", fill=T.MUTED,
                          font=T.F_TINY)


# 兼容旧引用（dashboard 用 ui.StatCard）
StatCard = CandyStatCard


# ---------------------------------------------------------------------------
# 侧边栏导航项
# ---------------------------------------------------------------------------
class NavItem(tk.Canvas):
    """macOS 侧边栏导航：选中强调淡底 + 深字，悬停浅灰，无描边。"""

    def __init__(self, parent: tk.Widget, label: str, command,
                 icon: int | None = None, height: int = T.NAV_ITEM_H, **kw) -> None:
        super().__init__(parent, height=height, bg=T.SURFACE,
                         highlightthickness=0, **kw)
        self._label = label
        self._icon = icon
        self._command = command
        self._selected = False
        self._hover = False
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Button-1>", lambda _e: self._command())
        self.bind("<Configure>", lambda _e: self._draw())
        self._draw()

    def set_selected(self, selected: bool) -> None:
        self._selected = bool(selected)
        self._draw()

    def _on_enter(self, _e: tk.Event) -> None:
        self._hover = True
        self._draw()

    def _on_leave(self, _e: tk.Event) -> None:
        self._hover = False
        self._draw()

    def _draw(self) -> None:
        # 布局未完成/尺寸异常时跳过绘制，保留已有内容（防止 delete 后绘制中断导致按钮消失）
        if self.winfo_height() <= 2:
            return
        self.delete("all")
        c = self
        W = max(40, self.winfo_width())
        H = self.winfo_height()
        # macOS 侧边栏：仅选中/悬停显示浅灰胶囊底，未选中无底
        if self._selected:
            fill, fg = T.CARD_ACTIVE, T.INK
        elif self._hover:
            fill, fg = T.CARD_HOVER, T.INK
        else:
            fill, fg = T.SURFACE, T.INK2
        r = max(1, min(9, (H - 6) / 2))
        rounded_rect(c, 6, 3, W - 6, H - 3, r, fill=fill, outline="")
        if self._selected:
            # 左侧 3px 语义指示条（选中强调，Bento 风格）
            c.create_rectangle(7, H / 2 - 9, 10, H / 2 + 9, fill=T.ACCENT,
                               outline="")
        text_x = 28
        if self._icon is not None:
            c.create_text(32, H / 2, text=chr(self._icon), anchor="w",
                          font=(T.F_ICON, 12), fill=fg)
            text_x = 52
        c.create_text(text_x, H / 2, text=self._label, anchor="w", fill=fg,
                      font=T.F_NAV)


# ---------------------------------------------------------------------------
# 状态点 + 页头
# ---------------------------------------------------------------------------
class StatusDot(tk.Canvas):
    """运行状态小圆点（绿=在线 / 红=离线 / 橙=进行中）。"""

    def __init__(self, parent: tk.Widget, color: str = T.OK,
                 size: int = 10, **kw) -> None:
        super().__init__(parent, width=size, height=size,
                         bg=parent.cget("bg"), highlightthickness=0, **kw)
        self._color = color
        self._size = size
        self._draw()

    def set(self, color: str) -> None:
        self._color = color
        self._draw()

    def _draw(self) -> None:
        self.delete("all")
        s = self._size
        self.create_oval(1, 1, s - 1, s - 1, fill=self._color, outline="")


class SceneHeader(tk.Canvas):
    """（已移除绘本装饰）保留类以兼容旧引用，不再绘制。"""

    def __init__(self, parent: tk.Widget, width: int = 0, height: int = 0,
                 **kw) -> None:
        super().__init__(parent, width=max(1, width), height=max(1, height),
                         bg=parent.cget("bg"), highlightthickness=0, **kw)

    def _draw(self) -> None:
        self.delete("all")


class PageHeader(tk.Frame):
    """页内顶栏：左「大标题 + 副标题(+状态点)」，右「操作按钮区」。"""

    def __init__(self, parent: tk.Widget, title: str, subtitle: str = "",
                 status_color: str | None = None, bg: str = T.BG) -> None:
        super().__init__(parent, bg=bg)
        left = tk.Frame(self, bg=bg)
        left.pack(side="left", fill="x", expand=True)
        tk.Label(left, text=title, bg=bg, fg=T.INK, font=T.F_TITLE,
                 anchor="w").pack(fill="x")
        # 霓虹绿下划线（Orbis 强调语义：页面标识）
        tk.Frame(left, bg=T.ACCENT, height=3, width=72).pack(anchor="w", pady=(2, 0))

        sub_row = tk.Frame(left, bg=bg)
        sub_row.pack(fill="x", pady=(4, 0))
        self._sub = tk.Label(sub_row, text=subtitle, bg=bg, fg=T.INK2,
                             font=T.F_SMALL, anchor="w")
        self._sub.pack(side="left")
        self._dot: StatusDot | None = None
        if status_color:
            self._dot = StatusDot(sub_row, status_color)
            self._dot.pack(side="left", padx=(8, 0))

        self.actions = tk.Frame(self, bg=bg)
        self.actions.pack(side="right", anchor="n", pady=(4, 0))

    def set_subtitle(self, text: str) -> None:
        self._sub.configure(text=text)

    def set_status(self, color: str) -> None:
        if self._dot is not None:
            self._dot.set(color)


# ---------------------------------------------------------------------------
# 聊天气泡（iMessage 风）：pet=灰泡深字 / user=蓝泡白字，自动换行、高度自适应
# ---------------------------------------------------------------------------
class Bubble(tk.Canvas):
    """圆角聊天气泡。maxw 为最大宽度，超宽自动折行；高度按行数自适应。"""

    def __init__(self, parent: tk.Widget, text: str, who: str,
                 maxw: int = 320, **kw) -> None:
        self._text = str(text)
        self._who = who
        font = T.F_BODY
        f = tkfont.Font(family=font[0], size=font[1])
        avail = max(60, int(maxw) - 30)
        lines: list[str] = []
        for raw in self._text.split("\n"):
            cur = ""
            for ch in raw:
                if cur and f.measure(cur + ch) > avail:
                    lines.append(cur)
                    cur = ch
                else:
                    cur += ch
            lines.append(cur)
        th = f.metrics("linespace")
        h = max(30, len(lines) * th + 18)
        wmax = max((f.measure(l) for l in lines), default=0) + 30
        w = max(56, min(int(maxw), wmax))
        super().__init__(parent, width=w, height=h, bg=parent.cget("bg"),
                         highlightthickness=0, **kw)
        is_user = who == "user"
        fill = T.BUBBLE_USER if is_user else T.BUBBLE_PET
        text_color = T.ON_ACCENT if is_user else T.INK
        rounded_rect(self, 0, 0, w, h, 14, fill=fill, outline="")
        y = 9 + th / 2
        for line in lines:
            self.create_text(w / 2, y, text=line, fill=text_color, font=font)
            y += th


# ---------------------------------------------------------------------------
# Bento 模块化卡片：圆角分层 + 统一标题栏 + 内容区（body 直接 pack 进）
# span 由调用方在 grid 中用 columnspan/rowspan 控制（1x1 / 2x1 / 2x2 分级）
# ---------------------------------------------------------------------------
class BentoCard(tk.Frame):
    """Bento 玻璃卡：CARD 深空底 + 1px 渐变边缘 + 顶部高光 + 可选标题栏。"""

    def __init__(self, parent: tk.Widget, title: str = "", *,
                 padx: int = T.PAD_CARD, pady: int = T.PAD_CARD, **kw) -> None:
        super().__init__(parent, bg=T.CARD, highlightthickness=1,
                         highlightbackground=T.GLASS_EDGE, padx=padx, pady=pady, **kw)
        glass_shine(self)
        self.title_label: tk.Label | None = None
        if title:
            self.title_label = section_title(self, title)
            self.title_label.pack(fill="x", pady=(0, 8))
        self.body = tk.Frame(self, bg=T.CARD)
        self.body.pack(fill="both", expand=True)


# ---------------------------------------------------------------------------
# 空状态引导：线框图标 + 标题 + 副文案 + 可选操作按钮
# ---------------------------------------------------------------------------
class EmptyState(tk.Frame):
    """空数据引导（数据源未配置 / 无事件时显示）。place 居中于父容器。"""

    def __init__(self, parent: tk.Widget, icon: int | None, title: str,
                 subtitle: str = "", action_text: str | None = None,
                 action: Callable | None = None, **kw) -> None:
        super().__init__(parent, bg=parent.cget("bg"), **kw)
        bg = self.cget("bg")
        inner = tk.Frame(self, bg=bg)
        inner.place(relx=0.5, rely=0.5, anchor="center")
        if icon is not None:
            tk.Label(inner, text=chr(icon), font=(T.F_ICON, 30), fg=T.MUTED,
                     bg=bg).pack(pady=(0, 10))
        tk.Label(inner, text=title, font=(T.F_SANS, 13, "bold"), fg=T.INK,
                 bg=bg).pack()
        if subtitle:
            tk.Label(inner, text=subtitle, font=T.F_SMALL, fg=T.INK2, bg=bg,
                     justify="center", wraplength=320).pack(pady=(4, 0))
        if action_text and action:
            ghost_button(inner, action_text, action, icon=T.ICON["sources"]).pack(
                pady=(14, 0))


# ---------------------------------------------------------------------------
# 键盘焦点可见性（Pro Max 可访问性准则：焦点环不可移除）
# ---------------------------------------------------------------------------
def focus_ring(widget: tk.Widget) -> None:
    """为输入控件绑定键盘焦点高亮描边（FocusIn=2px ACCENT，FocusOut 还原）。"""

    def _in(_e: tk.Event) -> None:
        try:
            widget.configure(highlightthickness=2, highlightcolor=T.FOCUS_RING)
        except tk.TclError:
            pass

    def _out(_e: tk.Event) -> None:
        try:
            widget.configure(highlightthickness=1, highlightbackground=T.BORDER)
        except tk.TclError:
            pass

    widget.bind("<FocusIn>", _in, add="+")
    widget.bind("<FocusOut>", _out, add="+")


# ---------------------------------------------------------------------------
# 垂直滚动容器（内容超长页面：总览含 Collection 行后使用）
# ---------------------------------------------------------------------------
class ScrollFrame(tk.Frame):
    """Canvas + 垂直滚动条 + inner 内容帧。构建内容时 pack/grid 进 self.inner。"""

    def __init__(self, parent: tk.Widget, bg: str = T.BG, **kw) -> None:
        super().__init__(parent, bg=bg, **kw)
        self.canvas = tk.Canvas(self, bg=bg, highlightthickness=0)
        self._sb = tk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self._sb.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self._sb.pack(side="right", fill="y")
        self.inner = tk.Frame(self.canvas, bg=bg)
        self._win = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.inner.bind("<Configure>",
                        lambda _e: self.canvas.configure(
                            scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>",
                         lambda e: self.canvas.itemconfigure(self._win, width=e.width))
        try:
            self._wheel_id = self.canvas.bind_all("<MouseWheel>", self._on_wheel,
                                                  add="+")
        except tk.TclError:
            self._wheel_id = None

    def _on_wheel(self, e) -> None:
        try:
            if not self.winfo_exists() or not self.winfo_ismapped():
                return
        except tk.TclError:
            return
        self.canvas.yview_scroll(int(-e.delta / 120), "units")

    def scroll_to_top(self) -> None:
        self.canvas.yview_moveto(0.0)

    def destroy(self) -> None:
        try:
            self.canvas.unbind_all("<MouseWheel>")
        except tk.TclError:
            pass
        super().destroy()
