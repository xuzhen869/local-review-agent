"""桌面主窗口（dashboard）：Linear/shadcn 风暗色界面。

左侧导航 + 右侧内容区，7 页（总览 / 周报 / 问答 / 事件流 / 番茄钟 / 审计 / 数据源设置），
数据全部通过 ApiClient 调本地后端（127.0.0.1:8100）。
视觉 token 统一引用 theme.py，通用组件（统计卡 / 导航项 / 卡片 / 按钮）用 ui.py。
"""
from __future__ import annotations

import threading
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk

from . import theme as T
from . import ui
from . import media
from .api_client import ApiClient
from .charts import BarChart, DonutChart, Heatmap

_SOURCES = ["git", "fs", "ics", "imap", "clipboard", "manual"]
_SOURCE_LABELS = {
    "git": "Git", "fs": "文档", "ics": "日历", "imap": "邮件",
    "clipboard": "剪贴板", "manual": "手动",
}


def _collection_button(parent: tk.Widget) -> tk.Canvas:
    """Orbis NFT 紫色圆按钮（#B724FF→#7C3AED 渐变近似）+ 白色箭头，hover 放大。"""
    btn = tk.Canvas(parent, width=40, height=40, bg=parent.cget("bg"),
                    highlightthickness=0, cursor="hand2")
    scale = {"v": 1.0}

    def _draw() -> None:
        btn.delete("all")
        s = scale["v"]
        cx = cy = 20
        r = 16 * s
        # 阴影（下偏暗紫，shadow-purple 语义）
        btn.create_oval(cx - r + 1, cy - r + 2, cx + r + 1, cy + r + 2,
                        fill=T.PURPLE_DEEP, outline="")
        # 渐变近似：底层亮紫（PURPLE_END）→ 上层暗紫（PURPLE）偏移成上下渐变
        btn.create_oval(cx - r, cy - r, cx + r, cy + r,
                        fill=T.PURPLE_END, outline="")
        btn.create_oval(cx - r * 0.60, cy - r * 0.72, cx + r * 0.60, cy + r * 0.28,
                        fill=T.PURPLE, outline="")
        btn.create_text(cx, cy, text="→", fill=T.ON_PURPLE,
                        font=(T.F_SANS, 15, "bold"))

    btn.bind("<Enter>", lambda _e: (scale.update(v=1.12), _draw()))
    btn.bind("<Leave>", lambda _e: (scale.update(v=1.0), _draw()))
    _draw()
    return btn


def _day_key(ts: str) -> str:
    return (ts or "")[:10]


def _fmt_bytes(n: int | None) -> str:
    if n is None:
        return "—"
    n = int(n)
    if n >= 1 << 30:
        return f"{n / (1 << 30):.1f} GB"
    if n >= 1 << 20:
        return f"{n / (1 << 20):.0f} MB"
    return f"{n} B"


def _entry(parent: tk.Widget, **kw) -> tk.Entry:
    """统一输入框样式：CARD 底 + BORDER 细边 + 聚焦 ACCENT 高亮。"""
    kw.setdefault("bg", T.CARD)
    kw.setdefault("fg", T.INK)
    kw.setdefault("insertbackground", T.INK)
    kw.setdefault("relief", "flat")
    kw.setdefault("highlightthickness", 1)
    kw.setdefault("highlightbackground", T.BORDER)
    kw.setdefault("highlightcolor", T.ACCENT)
    kw.setdefault("font", T.F_BODY)
    w = tk.Entry(parent, **kw)
    ui.focus_ring(w)
    return w


def _text_field(parent: tk.Widget, **kw) -> tk.Text:
    """统一多行文本域样式（同 _entry）。"""
    kw.setdefault("bg", T.CARD)
    kw.setdefault("fg", T.INK)
    kw.setdefault("insertbackground", T.INK)
    kw.setdefault("relief", "flat")
    kw.setdefault("highlightthickness", 1)
    kw.setdefault("highlightbackground", T.BORDER)
    kw.setdefault("highlightcolor", T.ACCENT)
    kw.setdefault("font", T.F_BODY)
    kw.setdefault("padx", 8)
    kw.setdefault("pady", 6)
    w = tk.Text(parent, **kw)
    ui.focus_ring(w)
    return w


def _scrollbar(parent: tk.Widget, command) -> tk.Scrollbar:
    return tk.Scrollbar(parent, orient="vertical", command=command, bg=T.CARD,
                        troughcolor=T.BG, activebackground=T.CARD_HOVER,
                        highlightthickness=0, bd=0)


def _style_tree(tree: ttk.Treeview) -> None:
    style = ttk.Style()
    style.configure("Dark.Treeview", background=T.CARD, foreground=T.INK,
                    fieldbackground=T.CARD, borderwidth=0, rowheight=28)
    style.configure("Dark.Treeview.Heading", background=T.SURFACE, foreground=T.INK2,
                    relief="flat", font=(T.F_SANS, 9, "bold"))
    style.map("Dark.Treeview",
              background=[("selected", T.CARD_ACTIVE)],
              foreground=[("selected", T.INK)])
    tree.configure(style="Dark.Treeview", show="headings")
    tree.tag_configure("alt", background=T.ALT_ROW)


def _run_async(win: tk.Misc, fn, on_done) -> None:
    """后台线程调用 fn，结果经 after 回主线程。

    win 已销毁（页面切换）时静默跳过回调，避免更新失效控件的 TclError。
    """
    def _safe(cb):
        try:
            if not win.winfo_exists():
                return
        except tk.TclError:
            return
        try:
            cb()
        except tk.TclError:
            pass

    def _after(cb):
        try:
            win.after(0, lambda: _safe(cb))
        except tk.TclError:
            pass

    def work() -> None:
        try:
            result = fn()
        except Exception as exc:  # noqa: BLE001
            # 用默认参数绑定 exc：except 块结束后变量会被 del，延迟 lambda 会 NameError
            _after(lambda e=exc: on_done(None, e))
        else:
            _after(lambda r=result: on_done(r, None))

    threading.Thread(target=work, daemon=True).start()


class Dashboard(tk.Toplevel):
    """主窗口容器：左侧导航 + 右侧内容页切换。"""

    NAV = [
        ("overview", "总览", T.ICON["home"]),
        ("report", "周报", T.ICON["calendar"]),
        ("qa", "问答", T.ICON["comment"]),
        ("events", "事件流", T.ICON["list"]),
        ("pomodoro", "番茄钟", T.ICON["clock"]),
        ("audit", "审计日志", T.ICON["shield"]),
        ("settings", "数据源设置", T.ICON["settings"]),
    ]

    def __init__(self, root: tk.Tk, api: ApiClient, on_close=None) -> None:
        super().__init__(root)
        self.api = api
        self._on_close = on_close
        self.title(T.APP_NAME)
        self.geometry("1120x720")
        self.minsize(980, 640)
        self.configure(bg=T.BG)
        self._nav_buttons: dict[str, ui.NavItem] = {}
        # 问答会话状态：QaPage 跨切页保留对话历史与 session_id（多轮对话）
        self.qa_state: dict = {"session_id": None, "turns": []}
        self._build()
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.show("overview")

    def _build(self) -> None:
        # ---------------- 侧边栏（Linear 风） ----------------
        self.nav = tk.Frame(self, bg=T.SURFACE, width=T.NAV_WIDTH)
        self.nav.pack(side="left", fill="y")
        self.nav.pack_propagate(False)

        logo = tk.Frame(self.nav, bg=T.SURFACE)
        logo.pack(fill="x", padx=16, pady=(20, 16))
        badge = tk.Canvas(logo, width=20, height=20, bg=T.SURFACE,
                          highlightthickness=0)
        badge.create_oval(2, 2, 18, 18, fill=T.ACCENT, outline="")
        badge.pack(side="left")
        tk.Label(logo, text="回顾 Agent", bg=T.SURFACE, fg=T.INK,
                 font=(T.F_SANS, 13, "bold")).pack(side="left", padx=(8, 0))
        tk.Label(logo, text="ORBIS", bg=T.SURFACE, fg=T.ACCENT,
                 font=(T.F_GROTESK, 8)).pack(side="left", padx=(6, 0), pady=(2, 0))

        for key, label, icon in self.NAV:
            item = ui.NavItem(self.nav, label, command=lambda k=key: self.show(k),
                              icon=icon)
            item.pack(fill="x", padx=6, pady=(1, 0))
            self._nav_buttons[key] = item

        tk.Label(self.nav, text=T.APP_VERSION, bg=T.SURFACE, fg=T.MUTED,
                 font=T.F_TINY).pack(side="bottom", pady=10)

        # ---------------- 内容区 ----------------
        self.content = tk.Frame(self, bg=T.BG)
        self.content.pack(side="left", fill="both", expand=True)
        self._page: tk.Frame | None = None

    def _nav_active(self, key: str) -> None:
        for k, item in self._nav_buttons.items():
            item.set_selected(k == key)

    def show(self, key: str) -> None:
        if self._page is not None:
            self._page.destroy()
        self._nav_active(key)
        page_cls = {
            "overview": OverviewPage,
            "report": ReportPage,
            "qa": QaPage,
            "events": EventsPage,
            "pomodoro": PomodoroPage,
            "audit": AuditPage,
            "settings": SettingsPage,
        }.get(key)
        page = page_cls(self.content, self) if page_cls else None
        if page is not None:
            # 必须 pack，否则页面虽创建但不可见（内容区一片黑）
            page.pack(fill="both", expand=True)
        self._page = page

    def close(self) -> None:
        if self._page is not None:
            self._page.destroy()
            self._page = None
        try:
            self.destroy()
        except tk.TclError:
            pass
        if self._on_close:
            self._on_close()


# ---------------------------------------------------------------------------
# 总览：统计卡 + 热力图 + 近14天趋势 + 来源环形图 + 最近活动
# ---------------------------------------------------------------------------
class OverviewPage(tk.Frame):
    """总览（Orbis Hero）：视频横幅 + 指标卡 + 热力图/趋势/环形 + 最近活动 + Collection。15s 轮询。"""

    def __init__(self, parent: tk.Widget, dashboard: Dashboard) -> None:
        super().__init__(parent, bg=T.BG)
        self._dashboard = dashboard
        self.api: ApiClient = dashboard.api
        self._timer: str | None = None
        self._videos: list[media.VideoSurface] = []
        self._build()
        self._load()
        self._schedule_refresh()

    def _build(self) -> None:
        scroller = ui.ScrollFrame(self)
        scroller.pack(fill="both", expand=True)
        self._scroller = scroller
        body = tk.Frame(scroller.inner, bg=T.BG, padx=T.PAD_PAGE, pady=T.PAD_PAGE)
        body.pack(fill="both", expand=True)

        self._build_hero(body)
        self._build_metrics(body)
        self._build_grid(body)
        self._build_collection(body)
        scroller.scroll_to_top()

    # -- Hero：视频横幅 + 大标题叠层（Orbis S1） ------------------------------
    def _build_hero(self, body: tk.Widget) -> None:
        hero = tk.Frame(body, bg=T.BG, height=172)
        hero.pack(fill="x", pady=(0, T.PAD_PAGE))
        hero.pack_propagate(False)
        vid = media.VideoSurface(hero, media.video_path("hero.mp4"), fps=18)
        vid.set_darken(0.55)
        vid.place(x=0, y=0, relwidth=1, relheight=1)
        base_x = 34
        w_cn = tkfont.Font(family=T.F_SANS, size=38, weight="bold").measure("回顾")
        w_en = tkfont.Font(family=T.F_GROTESK, size=38).measure("AGENT")
        vid.set_overlay([
            {"text": "回顾", "x": base_x, "y": 36,
             "font": (T.F_SANS, 38, "bold"), "fill": T.INK},
            {"text": "AGENT", "x": base_x + w_cn + 14, "y": 36,
             "font": (T.F_GROTESK, 38), "fill": T.INK},
            {"text": "Orbis", "x": base_x + w_cn + 14 + w_en + 12, "y": 12,
             "font": (T.F_CONDIMENT, 26), "fill": T.ACCENT},
            {"text": "LOCAL CAPTURE · 15S AUTO REFRESH",
             "x": base_x, "y": 98, "font": (T.F_MONO[0], 10), "fill": T.INK2},
        ])
        vid.play()
        self._videos.append(vid)

    # -- 指标卡行（玻璃，Anton 大数字） --------------------------------------
    def _build_metrics(self, body: tk.Widget) -> None:
        cards = tk.Frame(body, bg=T.BG)
        cards.pack(fill="x")
        self._c_total = ui.StatCard(cards, "事件总数", T.ICON["list"], T.ACCENT)
        self._c_adapter = ui.StatCard(cards, "运行中适配器", T.ICON["adapter"], T.OK)
        self._c_queue = ui.StatCard(cards, "队列深度", T.ICON["queue"], T.WARN)
        self._c_disk = ui.StatCard(cards, "磁盘剩余", T.ICON["disk"], T.INFO)
        for i, card in enumerate([self._c_total, self._c_adapter,
                                  self._c_queue, self._c_disk]):
            card.grid(row=0, column=i, sticky="nsew", padx=4)
            cards.columnconfigure(i, weight=1)

    # -- Bento 网格：热力图(2x1) → 趋势(1x1)+环形(1x1) → 最近活动(2x1)
    def _build_grid(self, body: tk.Widget) -> None:
        grid = tk.Frame(body, bg=T.BG)
        grid.pack(fill="both", expand=True, pady=(T.GAP_CARD, 0))
        grid.columnconfigure(0, weight=3)
        grid.columnconfigure(1, weight=2)

        heat_card = ui.BentoCard(grid, "活动热力图")
        heat_card.grid(row=0, column=0, columnspan=2, sticky="nsew",
                       pady=(0, T.GAP_CARD))
        grid.rowconfigure(0, weight=5)
        self._heatmap = Heatmap(heat_card.body, height=120)
        self._heatmap.pack(fill="both", expand=True)

        trend_card = ui.BentoCard(grid, "近 14 天事件趋势")
        trend_card.grid(row=1, column=0, sticky="nsew",
                        padx=(0, T.GAP_CARD // 2))
        grid.rowconfigure(1, weight=4)
        self._trend = BarChart(trend_card.body, height=150)
        self._trend.pack(fill="both", expand=True)

        donut_card = ui.BentoCard(grid, "来源分布")
        donut_card.grid(row=1, column=1, sticky="nsew",
                        padx=(T.GAP_CARD // 2, 0))
        self._donut = DonutChart(donut_card.body, height=130)
        self._donut.pack(fill="both", expand=True)

        # 最近活动（卡片列表；空数据时显示引导空态）
        recent_card = ui.BentoCard(grid, "最近活动")
        recent_card.grid(row=2, column=0, columnspan=2, sticky="nsew",
                         pady=(T.GAP_CARD, 0))
        grid.rowconfigure(2, weight=4)
        self._recent_box = tk.Frame(recent_card.body, bg=T.CARD)
        self._recent_box.pack(fill="both", expand=True)

    # -- Collection：NFT 视频缩略卡（黑底预留）+ RARITY SCORE + 紫色圆按钮（Orbis S3）
    def _build_collection(self, body: tk.Widget) -> None:
        coll = tk.Frame(body, bg=T.BG)
        coll.pack(fill="x", pady=(T.PAD_PAGE, 0))

        tk.Label(coll, text="COLLECTION OF", bg=T.BG, fg=T.INK,
                 font=(T.F_GROTESK, 20)).pack(anchor="w")
        row2 = tk.Frame(coll, bg=T.BG)
        row2.pack(anchor="w", padx=(52, 0), pady=(2, 0))
        tk.Label(row2, text="Space", bg=T.BG, fg=T.ACCENT,
                 font=(T.F_CONDIMENT, 24)).pack(side="left", pady=(4, 0))
        tk.Label(row2, text="objects", bg=T.BG, fg=T.INK,
                 font=(T.F_GROTESK, 20)).pack(side="left", padx=(12, 0))
        # 霓虹绿下划线（CTA 下划线语义）
        tk.Frame(coll, bg=T.ACCENT, height=3).pack(fill="x", pady=(10, 14))

        cards = tk.Frame(coll, bg=T.BG)
        cards.pack(fill="x")
        items = [("card1.mp4", "8.7 / 10"), ("card2.mp4", "9 / 10"),
                 ("card3.mp4", "8.2 / 10")]
        for i, (vid, score) in enumerate(items):
            card = tk.Frame(cards, bg=T.CARD, highlightthickness=1,
                            highlightbackground=T.GLASS_EDGE, padx=10, pady=10)
            card.grid(row=0, column=i, sticky="nsew", padx=6)
            cards.columnconfigure(i, weight=1)
            ui.glass_shine(card)
            # 方形视频缩略槽（黑底预留，视频到位自动播放）
            thumb = media.VideoSurface(card, media.video_path(vid), fps=15)
            thumb.pack(fill="x")
            thumb.bind("<Configure>",
                       lambda e, t=thumb: t.configure(height=e.width))
            thumb.play()
            self._videos.append(thumb)
            # 底部玻璃条：RARITY SCORE + 紫色圆按钮
            bar = tk.Frame(card, bg=T.CARD)
            bar.pack(fill="x", pady=(10, 2))
            left = tk.Frame(bar, bg=T.CARD)
            left.pack(side="left")
            tk.Label(left, text="RARITY SCORE", bg=T.CARD, fg=T.INK2,
                     font=(T.F_MONO[0], 8)).pack(anchor="w")
            tk.Label(left, text=score, bg=T.CARD, fg=T.INK,
                     font=(T.F_GROTESK, 14)).pack(anchor="w")
            _collection_button(bar).pack(side="right", padx=(8, 0))

    def _schedule_refresh(self) -> None:
        self._timer = self.after(15_000, self._refresh)

    def _refresh(self) -> None:
        self._load()
        self._schedule_refresh()

    def _load(self) -> None:
        _run_async(self, self.api.health, self._health_done)
        _run_async(self, lambda: self.api.get_events(limit=1000), self._events_done)

    def _health_done(self, result, err) -> None:
        if err:
            return
        adapters = result.get("adapters") or {}
        running = sum(1 for a in adapters.values() if a.get("state") == "RUNNING")
        self._c_total.set(result.get("event_count", "—"))
        self._c_adapter.set(f"{running}/{len(adapters)}")
        self._c_queue.set(result.get("queue_size", "—"),
                          f"丢弃 {result.get('dropped_count', 0)}")
        self._c_disk.set(_fmt_bytes(result.get("disk_free_bytes")))

    def _events_done(self, result, err) -> None:
        if err:
            return
        items = result.get("items") or []
        by_day: dict[str, int] = {}
        by_source: dict[str, int] = {}
        for e in items:
            key = _day_key(e.get("ts"))
            by_day[key] = by_day.get(key, 0) + 1
            src = e.get("source") or "?"
            by_source[src] = by_source.get(src, 0) + 1
        self._heatmap.set_counts(by_day)

        import datetime as _dt
        days, counts = [], []
        today = _dt.date.today()
        for i in range(13, -1, -1):
            d = today - _dt.timedelta(days=i)
            days.append(f"{d.month}/{d.day}")
            counts.append(by_day.get(d.isoformat(), 0))
        self._trend.set_data(days, counts)

        items_sorted = sorted(by_source.items(), key=lambda kv: kv[1], reverse=True)
        self._donut.set_items(
            [(_SOURCE_LABELS.get(k, k), v, T.SOURCE_COLORS.get(k, T.ACCENT))
             for k, v in items_sorted]
        )

        self._render_recent(items)

    def _render_recent(self, items: list) -> None:
        """渲染最近活动卡片列表；空数据显示配置引导空态。"""
        for w in self._recent_box.winfo_children():
            w.destroy()
        if not items:
            ui.EmptyState(
                self._recent_box, T.ICON["sources"], "还没有捕获到事件",
                "去「数据源设置」配置 Git 仓库或文档目录，开始记录你的日常",
                action_text="去配置",
                action=lambda: self._dashboard.show("settings"),
            )
            return
        for e in items[:12]:
            content = (e.get("content") or "（无文本内容）").replace("\n", " ")
            if len(content) > 60:
                content = content[:60] + "…"
            ts = (e.get("ts") or "")[11:16]
            src = e.get("source") or "?"
            self._activity_row(self._recent_box, src, ts, content)

    def _activity_row(self, parent: tk.Widget, src: str, ts: str,
                      content: str) -> None:
        """最近活动单行：来源线框图标（语义色）+ 内容 + 来源标签 + 时间。"""
        row = tk.Frame(parent, bg=T.CARD)
        row.pack(fill="x", pady=(0, 6))
        color = T.SOURCE_COLORS.get(src, T.ACCENT)
        tk.Label(row, text=chr(T.ICON["list"]), font=(T.F_ICON, 12), fg=color,
                 bg=T.CARD).pack(side="left", padx=(0, 8))
        mid = tk.Frame(row, bg=T.CARD)
        mid.pack(side="left", fill="x", expand=True)
        tk.Label(mid, text=content, font=T.F_SMALL, fg=T.INK, bg=T.CARD,
                 anchor="w").pack(fill="x")
        tk.Label(mid, text=_SOURCE_LABELS.get(src, src), font=T.F_TINY,
                 fg=T.MUTED, bg=T.CARD, anchor="w").pack(fill="x")
        tk.Label(row, text=ts, font=T.F_MONO, fg=T.MUTED, bg=T.CARD,
                 anchor="e").pack(side="right", padx=(8, 0))

    def destroy(self) -> None:
        if self._timer is not None:
            try:
                self.after_cancel(self._timer)
            except tk.TclError:
                pass
        for v in getattr(self, "_videos", []):
            try:
                v.stop()
            except tk.TclError:
                pass
        super().destroy()


# ---------------------------------------------------------------------------
# 周报：选择周 → 生成 → 展示（指标 + 主线 + 成就/阻塞/下周计划 + 溯源）
# ---------------------------------------------------------------------------
class ReportPage(tk.Frame):
    """周报：按周聚合本地事件，LLM 生成主线与结论（带溯源，防编造）。"""

    def __init__(self, parent: tk.Widget, dashboard: Dashboard) -> None:
        super().__init__(parent, bg=T.BG)
        self._dashboard = dashboard
        self.api: ApiClient = dashboard.api
        self._videos: list[media.VideoSurface] = []
        self._build()
        self._load()

    def destroy(self) -> None:
        for v in getattr(self, "_videos", []):
            try:
                v.stop()
            except tk.TclError:
                pass
        super().destroy()

    @staticmethod
    def _default_monday() -> str:
        import datetime as _dt
        today = _dt.date.today()
        monday = today - _dt.timedelta(days=today.weekday())
        return monday.isoformat()

    def _build(self) -> None:
        # 整页动态视频背景：about.mp4 铺满，持续循环（Orbis S2 → UI 背景）
        bg = media.VideoSurface(self, media.video_path("about.mp4"), fps=18)
        bg.set_darken(0.5)
        bg.place(x=0, y=0, relwidth=1, relheight=1)
        bg.play()
        self._videos.append(bg)

        # 悬浮内容面板：四周留边距露出视频，深色玻璃承载内容保证可读
        panel = tk.Frame(self, bg=T.CARD, highlightthickness=1,
                         highlightbackground=T.GLASS_EDGE,
                         padx=T.PAD_PAGE, pady=T.PAD_PAGE)
        panel.place(x=T.PAD_PAGE, y=T.PAD_PAGE, relwidth=1.0, relheight=1.0,
                    width=-T.PAD_PAGE * 2, height=-T.PAD_PAGE * 2)

        header = ui.PageHeader(panel, "周报",
                               "按周聚合本地事件，LLM 生成主线与结论（带溯源，防编造）",
                               bg=T.CARD)
        header.pack(fill="x", pady=(0, T.PAD_PAGE))

        row = tk.Frame(header.actions, bg=T.CARD)
        row.pack(side="left")
        tk.Label(row, text="周开始日期", bg=T.CARD, fg=T.INK2,
                 font=T.F_SMALL).pack(side="left", padx=(0, 6))
        self._week_var = tk.StringVar(value=self._default_monday())
        _entry(row, textvariable=self._week_var, width=12).pack(side="left")
        ui.primary_button(row, "生成周报", self._generate,
                          icon=T.ICON["check"],
                          padx=14, pady=4, font=T.F_SMALL).pack(side="left", padx=(10, 6))
        ui.ghost_button(row, "刷新", self._load, icon=T.ICON["refresh"],
                        padx=12, pady=4, font=T.F_SMALL).pack(side="left")

        self._msg = ui.message(panel, "")
        self._msg.pack(fill="x", pady=(0, T.GAP_CARD))

        card = ui.Card(panel, padx=6, pady=6)
        card.pack(fill="both", expand=True)
        self._text = tk.Text(card, bg=T.CARD, fg=T.INK, relief="flat", wrap="word",
                             font=T.F_BODY, padx=14, pady=10, state="disabled",
                             spacing1=4, spacing3=4, highlightthickness=0)
        sb = _scrollbar(card, self._text.yview)
        self._text.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self._text.pack(fill="both", expand=True)

    def _generate(self) -> None:
        week = self._week_var.get().strip()
        self._msg.configure(text="生成中（LLM 约 5-15 秒）…", fg=T.INK2)
        _run_async(self, lambda: self.api.report_generate(week or None), self._gen_done)

    def _gen_done(self, result, err) -> None:
        if err:
            self._msg.configure(text=f"生成失败：{err}", fg=T.ERR)
            return
        self._msg.configure(text="✓ 已生成", fg=T.OK)
        self._render(result)

    def _load(self) -> None:
        week = self._week_var.get().strip()
        _run_async(self, lambda: self.api.report_get(week or None), self._load_done)

    def _load_done(self, result, err) -> None:
        if err:
            self._msg.configure(text="该周暂无周报，点「生成周报」", fg=T.INK2)
            self._set_text("")
            return
        self._render(result)

    def _set_text(self, content: str) -> None:
        self._text.configure(state="normal")
        self._text.delete("1.0", "end")
        if content:
            self._text.insert("1.0", content)
        self._text.configure(state="disabled")

    def _render(self, report) -> None:
        m = report.get("metrics") or {}
        c = report.get("conclusion") or {}
        lines = [f"📅 周报：{report.get('week_start')} ~ {report.get('week_end')}", ""]
        lines.append(
            f"事件总数 {m.get('event_count', 0)} · Git 提交 {m.get('commit_count', 0)}"
            f" · 番茄 {m.get('pomodoro_count', 0)} 个（{m.get('pomodoro_minutes', 0)} 分钟）")
        by_src = m.get("by_source") or {}
        if by_src:
            lines.append("来源：" + "、".join(f"{k}×{v}" for k, v in by_src.items()))
        by_cat = m.get("by_category") or {}
        if by_cat:
            lines.append("分类：" + "、".join(f"{k}×{v}" for k, v in by_cat.items()))
        lines.append("")
        if c.get("mainline"):
            lines.append("◆ 本周主线")
            lines.append(str(c["mainline"]))
            lines.append("")
        for title, key in [("🎯 成就", "achievements"), ("🚧 阻塞", "blockers"),
                           ("📌 下周计划", "next_week")]:
            items = c.get(key) or []
            if items:
                lines.append(f"{title}（{len(items)} 条，各带溯源）")
                for it in items:
                    lines.append(f"  • {it.get('text', '')}")
                lines.append("")
        tl = report.get("timeline") or {}
        if tl:
            lines.append("📆 每日事件数")
            for day in sorted(tl.keys()):
                lines.append(f"  {day}：{len(tl[day])} 条")
        self._set_text("\n".join(lines))


# ---------------------------------------------------------------------------
# 问答：自然语言提问 → LLM 基于本地事件回答（带溯源）
# ---------------------------------------------------------------------------
class QaPage(tk.Frame):
    """历史问答：对话流式界面——上方滚动对话区 + 底部固定输入栏。

    多轮连续：复用后端返回的 session_id；对话记录存于 Dashboard.qa_state，
    切换页面再回来时自动恢复历史。视觉全部复用 theme/ui 的 Orbis 太空风。
    """

    _BUBBLE_W = 560   # 用户气泡最大宽度

    def __init__(self, parent: tk.Widget, dashboard: Dashboard) -> None:
        super().__init__(parent, bg=T.BG, padx=T.PAD_PAGE, pady=T.PAD_PAGE)
        self._dashboard = dashboard
        self.api: ApiClient = dashboard.api
        self._state: dict = dashboard.qa_state     # dashboard 持有的会话状态
        self._session_id: str | None = self._state.get("session_id")
        self._busy = False
        self._pending: dict | None = None          # 请求期间待填充的回答卡
        self._build()
        self._restore_history()
        try:
            self._entry.focus_set()
        except tk.TclError:
            pass

    # ------------------------------------------------------------------ 布局
    def _build(self) -> None:
        header = ui.PageHeader(self, "问答",
                               "问「本周我做了什么」「我学了什么」——基于本地事件回答并溯源")
        header.pack(fill="x", pady=(0, T.PAD_PAGE))

        # 对话滚动区（占满剩余高度）
        self._scroll = ui.ScrollFrame(self, bg=T.BG)
        self._scroll.pack(fill="both", expand=True, pady=(0, T.GAP_CARD))
        self._hint: tk.Widget | None = None
        self._show_hint()

        # 底部固定输入栏（glass 底 + 描边，与其它卡片同风格）
        bar = tk.Frame(self, bg=T.CARD, highlightthickness=1,
                       highlightbackground=T.GLASS_EDGE, padx=12, pady=10)
        bar.pack(fill="x")
        self._entry = _entry(bar)
        self._entry.pack(side="left", fill="x", expand=True, ipady=6)
        self._entry.bind("<Return>", lambda _e: self._ask())
        self._send = ui.primary_button(bar, "发送", self._ask,
                                       icon=T.ICON["send"], padx=18, pady=7)
        self._send.pack(side="left", padx=(10, 0))

    def _show_hint(self) -> None:
        """空态引导（无对话时显示；居中于滚动区顶部）。"""
        self._hint = tk.Frame(self._scroll.inner, bg=T.BG)
        self._hint.pack(fill="x", pady=56)
        tk.Label(self._hint, text=chr(T.ICON["qa"]), font=(T.F_ICON, 30),
                 fg=T.MUTED, bg=T.BG).pack()
        tk.Label(self._hint, text="开始提问，回顾你的本地生活记录",
                 font=(T.F_SANS, 12, "bold"), fg=T.INK2, bg=T.BG).pack(pady=(12, 0))
        tk.Label(self._hint, text="例如：本周我做了什么 · 我学了什么\n"
                                  "基于本地事件回答并溯源 · 支持连续追问",
                 font=T.F_SMALL, fg=T.MUTED, bg=T.BG, justify="center",
                 wraplength=420).pack(pady=(6, 0))

    def _hide_hint(self) -> None:
        if self._hint is not None:
            try:
                self._hint.destroy()
            except tk.TclError:
                pass
            self._hint = None

    def _scroll_bottom(self) -> None:
        """等布局完成后滚动到最底（最新对话始终可见）。"""
        def _go() -> None:
            try:
                self._scroll.canvas.update_idletasks()
                self._scroll.canvas.yview_moveto(1.0)
            except tk.TclError:
                pass
        try:
            self.after(0, _go)
        except tk.TclError:
            pass

    # ------------------------------------------------------------------ 渲染
    def _restore_history(self) -> None:
        """从 Dashboard.qa_state 恢复历史对话（切页返回时）。"""
        for turn in self._state.get("turns") or []:
            self._append_turn(turn)

    def _append_turn(self, turn: dict) -> None:
        """完整渲染一轮：用户气泡 + 回答卡（真实轮与恢复共用）。"""
        self._hide_hint()
        self._append_question(turn.get("question") or "")
        self._render_answer(self._new_answer_card(), turn)
        self._scroll_bottom()

    def _append_question(self, question: str) -> None:
        qf = tk.Frame(self._scroll.inner, bg=T.BG)
        qf.pack(fill="x", pady=(10, 4))
        ui.Bubble(qf, question, "user", maxw=self._BUBBLE_W).pack(side="right")

    def _new_answer_card(self) -> tk.Frame:
        card = ui.Card(self._scroll.inner, padx=12, pady=10)
        card.pack(fill="x", pady=(4, 10))
        return card

    def _render_answer(self, card: tk.Widget, turn: dict) -> None:
        """填充回答卡：错误/空消息 + 回答正文（自适应换行）+ 可折叠溯源。"""
        bg = T.CARD
        err = turn.get("error")
        msg = turn.get("message")
        answer = turn.get("answer") or ""

        if err:
            ui.message(card, f"回答失败：{err}", color=T.ERR, bg=bg).pack(anchor="w")
        elif msg and not answer:
            ui.message(card, msg, color=T.WARN, bg=bg).pack(anchor="w")

        if answer:
            body = tk.Label(card, text=answer, bg=bg, fg=T.INK, font=T.F_BODY,
                            justify="left", anchor="w", wraplength=1)
            body.pack(fill="x")

            def _wrap(_e: tk.Event, w=body) -> None:
                try:
                    w.configure(wraplength=max(100, w.winfo_width()))
                except tk.TclError:
                    pass
            body.bind("<Configure>", _wrap)

        self._render_sources(card, turn)

    def _render_sources(self, card: tk.Widget, turn: dict) -> None:
        """溯源折叠行：点击「查看溯源」展开/收起来源列表，弱化但不丢功能。"""
        bg = T.CARD
        events_count = turn.get("events_count") or 0
        sources = turn.get("sources") or []

        meta = tk.Frame(card, bg=bg)
        meta.pack(fill="x", pady=(8, 0))
        if not sources:
            ui.message(meta, f"基于 {events_count} 条事件 · 无匹配来源",
                       color=T.MUTED, bg=bg, font=T.F_SMALL).pack(side="left")
            return

        body = tk.Frame(card, bg=bg)      # 初始隐藏，展开时 pack
        toggle = tk.Label(meta, text=f"基于 {events_count} 条事件 · 查看溯源 ▼",
                          fg=T.ACCENT, bg=bg, font=T.F_SMALL, cursor="hand2")
        toggle.pack(side="left")
        state = {"open": False}

        def _fill() -> None:
            for w in body.winfo_children():
                w.destroy()
            for s in sources:
                content = (s.get("content") or "").replace("\n", " ")
                if len(content) > 60:
                    content = content[:60] + "…"
                src = _SOURCE_LABELS.get(s.get("source"), s.get("source"))
                line = f"[{(s.get('ts') or '')[:16]}] {src}  {content}"
                ui.message(body, line, color=T.INK2, bg=bg, font=T.F_TINY,
                           wraplength=700).pack(anchor="w", pady=1)

        def _toggle(_e: tk.Event) -> None:
            state["open"] = not state["open"]
            if state["open"]:
                _fill()
                body.pack(fill="x", pady=(6, 0))
                toggle.configure(text=f"基于 {events_count} 条事件 · 收起溯源 ▲")
            else:
                body.pack_forget()
                toggle.configure(text=f"基于 {events_count} 条事件 · 查看溯源 ▼")
            self._scroll_bottom()

        toggle.bind("<Button-1>", _toggle)
        toggle.bind("<Enter>", lambda _e: toggle.configure(fg=T.ACCENT_HOVER))
        toggle.bind("<Leave>", lambda _e: toggle.configure(fg=T.ACCENT))

    # ------------------------------------------------------------------ 交互
    def _ask(self) -> None:
        if self._busy:
            return
        question = self._entry.get().strip()
        if not question:
            return
        self._entry.delete(0, "end")
        self._hide_hint()
        self._append_question(question)

        # 先显示占位回答卡（思考中），请求返回后填充真实内容
        card = self._new_answer_card()
        ui.message(card, "思考中（LLM 约 3-10 秒）…", color=T.INK2, bg=T.CARD
                   ).pack(anchor="w")
        self._pending = {"card": card, "question": question}
        self._scroll_bottom()

        self._busy = True
        self._send.set_enabled(False)
        _run_async(self, lambda: self.api.qa_ask(question, self._session_id),
                   self._done)

    def _done(self, result, err) -> None:
        self._busy = False
        try:
            self._send.set_enabled(True)
        except tk.TclError:
            pass

        pending = self._pending
        self._pending = None
        question = pending["question"] if pending else ""
        card = pending["card"] if pending else None

        if err is None and result is not None:
            sid = result.get("session_id")
            if sid:
                self._session_id = sid
                self._state["session_id"] = sid
            turn = {
                "question": question,
                "answer": result.get("answer"),
                "message": result.get("message"),
                "error": result.get("error"),
                "events_count": result.get("events_count") or 0,
                "sources": result.get("sources") or [],
            }
        else:
            turn = {
                "question": question,
                "answer": None, "message": None,
                "error": str(err) if err else "未知错误",
                "events_count": 0, "sources": [],
            }
        self._state["turns"].append(turn)

        if card is not None:
            try:
                for w in card.winfo_children():
                    w.destroy()
            except tk.TclError:
                card = None
        if card is not None:
            self._render_answer(card, turn)
            self._scroll_bottom()


# ---------------------------------------------------------------------------
# 事件流：来源筛选 + 事件表格
# ---------------------------------------------------------------------------
class EventsPage(tk.Frame):
    """事件流：来源筛选 chip + 事件表格。"""

    def __init__(self, parent: tk.Widget, dashboard: Dashboard) -> None:
        super().__init__(parent, bg=T.BG, padx=T.PAD_PAGE, pady=T.PAD_PAGE)
        self._dashboard = dashboard
        self.api: ApiClient = dashboard.api
        self._source = ""
        self._build()
        self._load()

    def _build(self) -> None:
        header = ui.PageHeader(self, "事件流", "全部归一化事件（内容已脱敏）")
        header.pack(fill="x", pady=(0, T.PAD_PAGE))
        self._sub = ui.message(self, "", bg=T.BG)
        self._sub.pack(fill="x")

        chips = tk.Frame(self, bg=T.BG)
        chips.pack(fill="x", pady=(T.GAP_CARD, T.GAP_CARD))
        self._chip_btns: dict[str, tk.Button] = {}
        for key in [""] + _SOURCES:
            text = "全部" if not key else _SOURCE_LABELS.get(key, key)
            btn = ui.chip_button(chips, text, lambda k=key: self._filter(k),
                                 selected=(key == self._source))
            btn.pack(side="left", padx=(0, 6))
            self._chip_btns[key] = btn

        card = ui.Card(self, padx=6, pady=6)
        card.pack(fill="both", expand=True)
        cols = ("ts", "source", "content", "ref", "grade")
        headers = ("时间", "来源", "内容", "引用", "分级")
        widths = (130, 64, 380, 120, 60)
        self._tree = ttk.Treeview(card, columns=cols, height=16)
        for c, h, w in zip(cols, headers, widths):
            self._tree.heading(c, text=h)
            self._tree.column(c, width=w, anchor="w" if c != "grade" else "center")
        _style_tree(self._tree)
        self._tree.pack(fill="both", expand=True)

        foot = tk.Frame(self, bg=T.BG)
        foot.pack(fill="x", pady=(T.GAP_CARD, 0))
        self._count_lbl = ui.message(foot, "")
        self._count_lbl.pack(side="left")
        ui.ghost_button(foot, "刷新", self._load, icon=T.ICON["refresh"],
                        padx=12, pady=3, font=T.F_SMALL).pack(side="right")

    def _filter(self, key: str) -> None:
        self._source = key
        for k, btn in self._chip_btns.items():
            ui.chip_set(btn, k == key)
        self._load()

    def _load(self) -> None:
        _run_async(self, lambda: self.api.get_events(
            source=self._source or None, limit=500), self._done)

    def _done(self, result, err) -> None:
        if err:
            self._sub.configure(text=f"加载失败：{err}", fg=T.ERR)
            return
        items = result.get("items") or []
        self._sub.configure(
            text=f"全部归一化事件（内容已脱敏）· 共 {result.get('total', len(items))} 条")
        self._count_lbl.configure(text=f"显示 {len(items)} 条")
        self._tree.delete(*self._tree.get_children())
        for i, e in enumerate(items):
            content = (e.get("content") or "（无文本内容）").replace("\n", " ")
            if len(content) > 120:
                content = content[:120] + "…"
            ref = (e.get("source_ref") or "").split("/")[-1].split("\\")[-1]
            self._tree.insert("", "end",
                              values=(e.get("ts"), _SOURCE_LABELS.get(e.get("source"), e.get("source")),
                                      content, ref, e.get("grade") or "internal"),
                              tags=("alt",) if i % 2 else ())


# ---------------------------------------------------------------------------
# 番茄钟：统计概览 + 打开番茄钟弹窗
# ---------------------------------------------------------------------------
class PomodoroPage(tk.Frame):
    """番茄钟：统计卡片 + 打开完整状态机弹窗。"""

    def __init__(self, parent: tk.Widget, dashboard: Dashboard) -> None:
        super().__init__(parent, bg=T.BG, padx=T.PAD_PAGE, pady=T.PAD_PAGE)
        self._dashboard = dashboard
        self.api: ApiClient = dashboard.api
        self._build()
        self._load()

    def _build(self) -> None:
        header = ui.PageHeader(self, "番茄钟",
                               "专注时段统计（完整状态机在弹窗中操作）")
        header.pack(fill="x", pady=(0, T.PAD_PAGE))

        cards = tk.Frame(self, bg=T.BG)
        cards.pack(fill="x")
        self._c_today = ui.StatCard(cards, "今日番茄", T.ICON["pomodoro"], T.PHASE_FOCUS)
        self._c_week = ui.StatCard(cards, "本周番茄", T.ICON["calendar"], T.INFO)
        self._c_total = ui.StatCard(cards, "累计番茄", T.ICON["total"], T.OK)
        self._c_streak = ui.StatCard(cards, "连续天数", T.ICON["streak"], T.WARN)
        for i, card in enumerate([self._c_today, self._c_week,
                                  self._c_total, self._c_streak]):
            card.grid(row=0, column=i, sticky="nsew", padx=4)
            cards.columnconfigure(i, weight=1)

        body = ui.Card(self, padx=22, pady=22)
        body.pack(fill="both", expand=True, pady=(T.GAP_CARD, 0))
        ui.primary_button(body, "打开番茄闹钟", self._open_pomodoro,
                          icon=T.ICON["play"],
                          padx=24, pady=10, font=(T.F_SANS, 11, "bold")).pack()
        self._hint = ui.message(body, "", color=T.INK2, wraplength=560,
                                justify="left")
        self._hint.pack(pady=(16, 0))
        ui.ghost_button(body, "刷新统计", self._load, icon=T.ICON["refresh"],
                        padx=14, pady=5, font=T.F_SMALL).pack(pady=(14, 0))

    def _open_pomodoro(self) -> None:
        from .pomodoro import PomodoroDialog
        dlg = PomodoroDialog(self._dashboard, self._notify, self.api,
                             on_recorded=self._load)
        self._dashboard._pomodoro_dlg = dlg  # 保持引用防 GC

    def _notify(self, message: str) -> None:
        import tkinter.messagebox as mb
        try:
            mb.showinfo("番茄钟", message, parent=self)
        except tk.TclError:
            pass

    def _load(self) -> None:
        _run_async(self, self.api.pomodoro_stats, self._done)

    def _done(self, result, err) -> None:
        if err:
            self._hint.configure(text=f"统计加载失败：{err}")
            return
        today = result.get("today") or {}
        week = result.get("week") or {}
        self._c_today.set(today.get("count", 0), f"{today.get('focus_minutes', 0)} 分钟")
        self._c_week.set(week.get("count", 0), f"{week.get('focus_minutes', 0)} 分钟")
        self._c_total.set(result.get("total_count", 0),
                          f"{result.get('total_minutes', 0)} 分钟")
        self._c_streak.set(result.get("streak_days", 0),
                           f"中断 {result.get('interrupted_count', 0)} 次")
        self._hint.configure(text=(
            "番茄钟支持完整状态机：专注 → 短休 → … → 第 N 个专注后长休。\n"
            "时长与每轮番茄数可在弹窗「设置」中调整；进行中会话跨重启恢复。"))


# ---------------------------------------------------------------------------
# 审计日志：不可抵赖的敏感操作与系统事件
# ---------------------------------------------------------------------------
class AuditPage(tk.Frame):
    """审计日志：append-only 敏感操作与系统事件记录。"""

    def __init__(self, parent: tk.Widget, dashboard: Dashboard) -> None:
        super().__init__(parent, bg=T.BG, padx=T.PAD_PAGE, pady=T.PAD_PAGE)
        self._dashboard = dashboard
        self.api: ApiClient = dashboard.api
        self._build()
        self._load()

    def _build(self) -> None:
        header = ui.PageHeader(self, "审计日志",
                               "敏感操作与系统事件的不可抵赖记录（append-only）")
        header.pack(fill="x", pady=(0, T.PAD_PAGE))

        card = ui.Card(self, padx=6, pady=6)
        card.pack(fill="both", expand=True)
        cols = ("ts", "actor", "action", "object", "result", "detail")
        headers = ("时间", "操作者", "动作", "对象", "结果", "详情")
        widths = (140, 70, 130, 150, 60, 300)
        self._tree = ttk.Treeview(card, columns=cols, height=16)
        for c, h, w in zip(cols, headers, widths):
            self._tree.heading(c, text=h)
            self._tree.column(c, width=w, anchor="w")
        _style_tree(self._tree)
        self._tree.pack(fill="both", expand=True)
        self._tree.tag_configure("ok", foreground=T.OK)
        self._tree.tag_configure("bad", foreground=T.ERR)

        foot = tk.Frame(self, bg=T.BG)
        foot.pack(fill="x", pady=(T.GAP_CARD, 0))
        ui.ghost_button(foot, "刷新", self._load, icon=T.ICON["refresh"],
                        padx=12, pady=3, font=T.F_SMALL).pack(side="right")

    def _load(self) -> None:
        _run_async(self, lambda: self.api.get_audit(limit=200), self._done)

    def _done(self, result, err) -> None:
        if err:
            return
        items = result.get("items") or []
        self._tree.delete(*self._tree.get_children())
        for a in items:
            obj = a.get("object_id") if a.get("object_id") else a.get("object_type")
            res = a.get("result") or ""
            tag = "ok" if res == "ok" else "bad"
            self._tree.insert("", "end",
                              values=(a.get("ts"), a.get("actor"), a.get("action"),
                                      obj, res, a.get("detail") or ""),
                              tags=(tag,))


# ---------------------------------------------------------------------------
# 数据源设置：git/fs/ics/imap/剪贴板 + 保存热重载
# ---------------------------------------------------------------------------
class SettingsPage(tk.Frame):
    """数据源设置：填写后保存即热重载。"""

    def __init__(self, parent: tk.Widget, dashboard: Dashboard) -> None:
        super().__init__(parent, bg=T.BG, padx=T.PAD_PAGE, pady=T.PAD_PAGE)
        self._dashboard = dashboard
        self.api: ApiClient = dashboard.api
        self._videos: list[media.VideoSurface] = []
        self._build()
        self._load()

    def destroy(self) -> None:
        for v in getattr(self, "_videos", []):
            try:
                v.stop()
            except tk.TclError:
                pass
        super().destroy()

    def _build(self) -> None:
        header = ui.PageHeader(self, "数据源设置",
                               "填写数据源后保存即生效（热重载，无需重启）。敏感字段请勿写入。")
        header.pack(fill="x", pady=(0, T.PAD_PAGE))

        body = tk.Frame(self, bg=T.BG)
        body.pack(fill="both", expand=True)
        left_card = ui.Card(body, padx=14, pady=12)
        right_card = ui.Card(body, padx=14, pady=12)
        left_card.pack(side="left", fill="both", expand=True, padx=(0, T.GAP_CARD // 2))
        right_card.pack(side="left", fill="both", expand=True, padx=(T.GAP_CARD // 2, 0))

        self._git_text = self._field(left_card, "Git 仓库绝对路径（每行一个）", height=3)
        self._fs_text = self._field(left_card, "文档监控目录绝对路径（每行一个）", height=3)
        self._clip_var = tk.BooleanVar(value=False)
        tk.Checkbutton(left_card, text="启用剪贴板捕获（高风险源，默认关闭）",
                       variable=self._clip_var, bg=T.CARD, fg=T.INK2,
                       selectcolor=T.BG, activebackground=T.CARD,
                       activeforeground=T.INK, font=T.F_SMALL).pack(anchor="w", pady=(6, 0))

        self._ics_files = self._field(right_card, "本地 ICS 日历文件（每行一个）", height=2)
        self._ics_urls = self._field(right_card, "远程 ICS URL（每行一个）", height=2)
        imap_row = tk.Frame(right_card, bg=T.CARD)
        imap_row.pack(fill="x", pady=(6, 0))
        self._imap_server = _entry(imap_row)
        self._imap_port = _entry(imap_row, width=7)
        self._imap_user = _entry(imap_row)
        self._imap_server.pack(side="left", fill="x", expand=True, padx=(0, 6))
        self._imap_port.pack(side="left", padx=(0, 6))
        self._imap_user.pack(side="left", fill="x", expand=True)
        tk.Label(right_card, text="IMAP：服务器 / 端口 / 账号（密码后续在密钥管理中录入，入加密库）",
                 bg=T.CARD, fg=T.MUTED, font=T.F_TINY, anchor="w").pack(fill="x", pady=(2, 0))

        foot = tk.Frame(self, bg=T.BG)
        foot.pack(fill="x", pady=(T.PAD_PAGE, 0))
        ui.primary_button(foot, "保存并热重载", self._save, icon=T.ICON["save"],
                          padx=18, pady=6, font=T.F_BODY).pack(side="left")
        self._msg = ui.message(foot, "")
        self._msg.pack(side="left", padx=12)

        # CTA 视频横幅（Orbis S4）——预留视频空间 + 霓虹下划线
        cta = tk.Frame(self, bg=T.BG, height=112)
        cta.pack(fill="x", pady=(T.PAD_PAGE, 0))
        cta.pack_propagate(False)
        vid = media.VideoSurface(cta, media.video_path("cta.mp4"), fps=18)
        vid.place(x=0, y=0, relwidth=1, relheight=1)
        vid.set_overlay([
            {"text": "JOIN US · GO BEYOND", "x": 24, "y": 20,
             "font": (T.F_GROTESK, 24), "fill": T.INK},
            {"text": "定义你的下一步", "x": 26, "y": 62,
             "font": (T.F_CONDIMENT, 20), "fill": T.ACCENT},
        ])
        tk.Frame(cta, bg=T.ACCENT, height=3).pack(fill="x", side="bottom")
        vid.play()
        self._videos.append(vid)

    def _field(self, parent: tk.Widget, label: str, height: int = 3) -> tk.Text:
        tk.Label(parent, text=label, bg=T.CARD, fg=T.INK2, font=T.F_SMALL,
                 anchor="w").pack(fill="x", pady=(4, 3))
        text = _text_field(parent, height=height)
        text.pack(fill="both", expand=True)
        return text

    def _load(self) -> None:
        _run_async(self, self.api.get_sources, self._load_done)

    def _load_done(self, result, err) -> None:
        if err:
            self._msg.configure(text=f"加载配置失败：{err}", fg=T.ERR)
            return
        s = result or {}
        self._set_lines(self._git_text, s.get("git", {}).get("repos"))
        self._set_lines(self._fs_text, s.get("fs", {}).get("dirs"))
        self._set_lines(self._ics_files, s.get("ics", {}).get("files"))
        self._set_lines(self._ics_urls, s.get("ics", {}).get("urls"))
        imap = s.get("imap") or {}
        self._imap_server.delete(0, "end")
        self._imap_server.insert(0, imap.get("server") or "")
        self._imap_port.delete(0, "end")
        self._imap_port.insert(0, str(imap.get("port") or 993))
        self._imap_user.delete(0, "end")
        self._imap_user.insert(0, imap.get("username") or "")
        self._clip_var.set(bool(s.get("clipboard", {}).get("enabled")))

    @staticmethod
    def _set_lines(text: tk.Text, lines) -> None:
        text.delete("1.0", "end")
        if lines:
            text.insert("1.0", "\n".join(str(x) for x in lines))

    @staticmethod
    def _to_list(raw: str) -> list[str]:
        return [line.strip() for line in raw.splitlines() if line.strip()]

    def _save(self) -> None:
        try:
            port = int(self._imap_port.get() or 993)
            if not 1 <= port <= 65535:
                raise ValueError
        except ValueError:
            self._msg.configure(text="IMAP 端口须为 1-65535 的整数", fg=T.ERR)
            return
        payload = {
            "git": {"repos": self._to_list(self._git_text.get("1.0", "end"))},
            "fs": {"dirs": self._to_list(self._fs_text.get("1.0", "end"))},
            "ics": {"files": self._to_list(self._ics_files.get("1.0", "end")),
                    "urls": self._to_list(self._ics_urls.get("1.0", "end"))},
            "imap": {
                "server": self._imap_server.get().strip(),
                "port": port,
                "username": self._imap_user.get().strip(),
            },
            "clipboard": {"enabled": self._clip_var.get(),
                          "poll_interval_seconds": 2, "daily_limit": 200},
        }
        self._msg.configure(text="保存中…", fg=T.INK2)
        _run_async(self, lambda: self.api.save_sources(payload), self._save_done)

    def _save_done(self, result, err) -> None:
        if err:
            self._msg.configure(text=f"保存失败：{err}", fg=T.ERR)
            return
        self._msg.configure(text="配置已保存，数据源已热重载 ✓", fg=T.OK)
