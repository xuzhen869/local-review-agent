"""桌面主窗口（dashboard）：Linear/shadcn 风暗色界面 —— PySide6 版。

对应原 tkinter dashboard.py：
- 左侧导航 + QStackedWidget 右侧内容区，7 页（总览 / 周报 / 问答 / 事件流 /
  番茄钟 / 审计 / 数据源设置），数据全部通过 ApiClient 调本地后端（127.0.0.1:8100）；
- 页面用 QStackedWidget 缓存（切换不重建），showEvent 刷新数据 + 控制视频播放；
- 异步统一走 _Async（QThread worker 池），页面关闭时 stop() 收尾；
- 视觉 token 统一引用 widgets/theme.py，组件复用 widgets（StatCard/BentoCard/
  ScrollFrame/NavItem/PageHeader/charts）。
"""
from __future__ import annotations

import datetime as _dt
import html as _html

from PySide6.QtCore import Qt, QObject, QThread, Signal, QTimer, QRectF
from PySide6.QtGui import (
    QPainter, QColor, QPen, QBrush, QLinearGradient, QRadialGradient,
    QFontMetrics, QPainterPath, QRegion,
)
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QStackedWidget, QFrame, QHBoxLayout, QVBoxLayout,
    QLabel, QLineEdit, QPlainTextEdit, QTextEdit, QCheckBox, QTableWidget,
    QTableWidgetItem, QListWidget, QPushButton, QGridLayout, QHeaderView,
    QGraphicsDropShadowEffect,
)

from ..api_client import ApiClient
from ..widgets import theme as T
from ..widgets.buttons import (NavItem, primary_button, ghost_button,
                               chip_button, chip_set)
from ..widgets.cards import StatCard, EmptyState, ScrollFrame, BentoCard, message
from ..widgets.header import PageHeader
from ..widgets.charts import BarChart, Heatmap, DonutChart
from ..dialogs.pending import PendingDialog
from .media import VideoSurface, video_path

# 判断 Agent 空闲触发间隔：停止提问约 60s → 独立判断整段短期对话是否值得沉淀
_IDLE_JUDGE_MS = 60_000

_SOURCES = ["git", "fs", "ics", "imap", "clipboard", "manual"]
_SOURCE_LABELS = {
    "git": "Git", "fs": "文档", "ics": "日历", "imap": "邮件",
    "clipboard": "剪贴板", "manual": "手动",
    "learning": "长期知识",
}


def _fmt_bytes(n: int | None) -> str:
    if n is None:
        return "—"
    n = int(n)
    if n >= 1 << 30:
        return f"{n / (1 << 30):.1f} GB"
    if n >= 1 << 20:
        return f"{n / (1 << 20):.0f} MB"
    return f"{n} B"


def _day_key(ts: str) -> str:
    return (ts or "")[:10]


def _elide(text: str, limit: int) -> str:
    text = (text or "（无文本内容）").replace("\n", " ")
    return text if len(text) <= limit else text[:limit] + "…"


# ===========================================================================
# 页面异步执行器（QThread worker 池）
# ===========================================================================
class _PageWorker(QThread):
    """单次后端调用工作线程。done(result, err) 回主线程。"""

    done = Signal(object, object)

    def __init__(self, fn, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._fn = fn

    def run(self) -> None:  # noqa: D102
        try:
            result = self._fn()
        except Exception as exc:  # noqa: BLE001
            self.done.emit(None, exc)
        else:
            self.done.emit(result, None)


class _Async(QObject):
    """页面级异步执行器：并行跑 worker，回调回主线程；stop() 收尾所有 worker。"""

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._workers: list[_PageWorker] = []

    def run(self, fn, on_done) -> None:
        w = _PageWorker(fn, self)
        self._workers.append(w)

        def _finish(result, err) -> None:
            try:
                if w in self._workers:
                    self._workers.remove(w)
            except RuntimeError:
                pass
            w.deleteLater()
            try:
                on_done(result, err)
            except RuntimeError:
                pass  # 页面已销毁：控件方法访问抛 RuntimeError
            except Exception:  # noqa: BLE001
                pass

        w.done.connect(_finish)
        w.start()

    def stop(self) -> None:
        """中断所有 worker（页面关闭时调用）。wait 超时后 terminate 兜底。"""
        for w in list(self._workers):
            w.requestInterruption()
            if not w.wait(400):
                w.terminate()
                w.wait(150)
            w.deleteLater()
        self._workers.clear()


# ===========================================================================
# 主窗口
# ===========================================================================
class Dashboard(QMainWindow):
    """主窗口容器：左侧导航 + QStackedWidget 内容页切换。"""

    NAV = [
        ("overview", "总览", T.ICON["home"]),
        ("report", "周报", T.ICON["calendar"]),
        ("qa", "问答", T.ICON["comment"]),
        ("pomodoro", "番茄钟", T.ICON["clock"]),
    ]
    # 事件流不在主导航中：唯一入口是总览「最近活动 → 查看全部」。
    # 页面仍注册在 _PAGE_CLS 且被构建缓存，供 show_page("events") 跳转。

    def __init__(self, api: ApiClient, on_close=None) -> None:
        super().__init__()
        self.api = api
        self._on_close = on_close
        self._pages: dict[str, QWidget] = {}
        self._nav_items: dict[str, NavItem] = {}

        self.setWindowTitle(T.APP_NAME)
        self.resize(1120, 720)
        self.setMinimumSize(980, 640)
        # 主窗口深蓝兜底（仅窗口边缘动画瞬间可见；内容区被全屏视频覆盖）
        self.setStyleSheet(f"background-color: {T.BG};")

        central = QWidget(self)
        self.setCentralWidget(central)
        # 两层布局：底层 = 全屏动态视频背景（所有页面共享、持续循环），
        # 顶层 = 导航 + 内容区（透明，露出视频）——满足「背景全屏、每页都有视频」。
        deck = QGridLayout(central)
        deck.setContentsMargins(0, 0, 0, 0)
        deck.setSpacing(0)

        # 全程序统一一个全屏背景视频：总览直接用 hero.mp4 覆盖整页（主人要求）
        self._bg = VideoSurface(central, video_path("hero.mp4"), fps=18,
                                prefer_cv=True)
        self._bg.set_darken(0.42)
        self._bg.play()
        deck.addWidget(self._bg, 0, 0)

        shell = QWidget(central)
        shell.setStyleSheet("background: transparent;")
        root = QHBoxLayout(shell)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.nav = QFrame(shell)
        self.nav.setFixedWidth(T.NAV_WIDTH)
        # 半透明遮罩：压暗视频保证导航文字可读，同时视频从侧栏透出（全屏观感）
        self.nav.setStyleSheet(
            "background-color: rgba(0, 8, 31, 175); border: none;")
        root.addWidget(self.nav)

        self.stack = QStackedWidget(shell)
        self.stack.setStyleSheet("background: transparent;")
        root.addWidget(self.stack, 1)

        deck.addWidget(shell, 0, 0)

        self._build_nav()
        # 遍历 _PAGE_CLS 而非 NAV：事件流不在导航中，但必须仍被构建缓存，
        # 否则总览「查看全部」跳转 show_page("events") 会失效。
        for key, cls in _PAGE_CLS.items():
            page = cls(self)
            self._pages[key] = page
            self.stack.addWidget(page)

        self.show_page("overview")

    # ------------------------------------------------------------------
    def _build_nav(self) -> None:
        v = QVBoxLayout(self.nav)
        v.setContentsMargins(12, 20, 12, 12)
        v.setSpacing(2)

        logo_row = QHBoxLayout()
        logo_row.setSpacing(8)
        badge = QLabel("●", self.nav)
        badge.setStyleSheet(f"color: {T.ACCENT}; background: transparent;"
                            f" font-size: 20px;")
        logo_row.addWidget(badge)
        app = QLabel("回顾 Agent", self.nav)
        app.setStyleSheet(f"color: {T.INK}; background: transparent;")
        app.setFont(T.font((T.F_SANS, 13, "bold")))
        logo_row.addWidget(app)
        orbis = QLabel("ORBIS", self.nav)
        orbis.setStyleSheet(f"color: {T.ACCENT}; background: transparent;")
        orbis.setFont(T.font((T.F_GROTESK, 8)))
        logo_row.addWidget(orbis)
        logo_row.addStretch(1)
        v.addLayout(logo_row)
        v.addSpacing(16)

        for key, label, icon in self.NAV:
            item = NavItem(self.nav, label, lambda k=key: self.show_page(k),
                           icon=icon)
            v.addWidget(item)
            self._nav_items[key] = item

        v.addStretch(1)
        ver = QLabel(T.APP_VERSION, self.nav)
        ver.setStyleSheet(f"color: {T.MUTED}; background: transparent;")
        ver.setFont(T.font(T.F_TINY))
        v.addWidget(ver, 0, Qt.AlignmentFlag.AlignHCenter)

    # ------------------------------------------------------------------
    def show_page(self, key: str) -> None:
        page = self._pages.get(key)
        if page is None:
            return
        self._nav_active(key)
        self.stack.setCurrentWidget(page)

    def _nav_active(self, key: str) -> None:
        for k, item in self._nav_items.items():
            item.set_selected(k == key)

    def closeEvent(self, event) -> None:  # noqa: N802
        try:
            self._bg.stop()
        except Exception:  # noqa: BLE001
            pass
        for page in self._pages.values():
            async_ = getattr(page, "_async", None)
            if async_ is not None:
                try:
                    async_.stop()
                except Exception:  # noqa: BLE001
                    pass
        if self._on_close:
            try:
                self._on_close()
            except Exception:  # noqa: BLE001
                pass
        super().closeEvent(event)


# ===========================================================================
# Hero 视频横幅（一比一复刻预览稿：局部视频 + 横向 veil 渐变 + 底部霓虹 glow）
# ===========================================================================
class _HeroVeil(QWidget):
    """透明遮罩层：横向深蓝渐变（左 88% → 右透明）+ 底部霓虹绿径向 glow。

    独立透明层叠在 VideoSurface 之上（鼠标穿透），实现预览稿 .hero 的
    .veil（渐变遮罩）与 .glow（底部霓虹辉光）两个 CSS 层。
    """

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        w, h = self.width(), self.height()
        # veil：90deg 线性渐变（left 深 88% → right 透明 5%）
        g = QLinearGradient(0, 0, w, 0)
        g.setColorAt(0.0, QColor(1, 8, 40, 224))
        g.setColorAt(0.42, QColor(1, 8, 40, 140))
        g.setColorAt(0.78, QColor(1, 8, 40, 13))
        p.fillRect(self.rect(), QBrush(g))
        # glow：底部霓虹绿径向辉光（预览稿 at 12% 118%）
        r = QRadialGradient(w * 0.12, h * 1.18, w * 0.46)
        r.setColorAt(0.0, QColor(*T.NAV_SELECT_RGB, 26))
        r.setColorAt(0.6, QColor(*T.NAV_SELECT_RGB, 0))
        p.fillRect(self.rect(), QBrush(r))
        p.end()


class _HeroBanner(QWidget):
    """Hero 视频横幅（196px）：VideoSurface 局部播放 hero.mp4（圆角 mask）
    + _HeroVeil 遮罩/辉光 + 标题叠层 + pill 徽标 + LIVE badge。

    层级（z-order 由创建顺序保证）：视频（最底）→ veil（中层）→ 布局内容（最顶）。
    内容通过 layout_box() 暴露的水平布局承载。
    """

    def __init__(self, parent: QWidget | None = None, height: int = 196) -> None:
        super().__init__(parent)
        self.setFixedHeight(height)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        # 视频背景（最底）
        self._video = VideoSurface(self, video_path("hero.mp4"), mode="cover",
                                   prefer_cv=True)
        self._video.set_darken(0.62)
        self._video.play()
        self.video = self._video   # 暴露给 OverviewPage 统一可见性管理
        # 遮罩层（中层）
        self._veil = _HeroVeil(self)
        # 内容布局（最顶，手动定位 video/veil）
        self._lay = QHBoxLayout(self)
        self._lay.setContentsMargins(32, 24, 28, 22)
        self._lay.setSpacing(18)

    def layout_box(self) -> QHBoxLayout:
        """内部水平布局：调用方往里填标题叠层与 pill 徽标。"""
        return self._lay

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._video.setGeometry(self.rect())
        self._veil.setGeometry(self.rect())
        # 视频圆角裁剪（QPainterPath → QPolygon → QRegion）
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()).adjusted(0, 0, -1, -1),
                            T.RADIUS_CARD, T.RADIUS_CARD)
        self._video.setMask(QRegion(path.toFillPolygon().toPolygon()))

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(0.5, 0.5, self.width() - 1, self.height() - 1)
        path = QPainterPath()
        path.addRoundedRect(rect, T.RADIUS_CARD, T.RADIUS_CARD)
        p.setPen(QPen(QColor(T.GLASS_EDGE), 1))
        p.drawPath(path)
        p.end()


class _Pill(QLabel):
    """Hero pill 徽标：彩色圆点 + mono 文字，半透明玻璃圆角胶囊。

    圆点与文字用富文本分色（QLabel 支持 <span> 局部着色）。
    """

    def __init__(self, parent: QWidget, text: str, dot_color: str, *,
                 fg: str, bg: str, border: str) -> None:
        super().__init__(parent)
        self.setTextFormat(Qt.TextFormat.RichText)
        self.setText(f"<span style='color:{dot_color};'>●</span> {text}")
        self.setStyleSheet(
            f"color: {fg}; background-color: {bg}; border: 1px solid {border};"
            f" border-radius: 11px; padding: 4px 10px;")
        self.setFont(T.font((T.F_MONO[0], 9)))


# ===========================================================================
# 总览：Hero 视频 + 指标卡 + 热力图/趋势/环形 + 最近活动 + Collection
# ===========================================================================
class OverviewPage(QWidget):
    """总览（Orbis Hero）：视频横幅 + 指标卡 + 热力图/趋势/环形 + 最近活动 + Collection。15s 轮询。"""

    def __init__(self, dashboard: Dashboard) -> None:
        super().__init__(dashboard)
        self._dashboard = dashboard
        self.api: ApiClient = dashboard.api
        self._async = _Async(self)
        self._videos: list[VideoSurface] = []
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(15_000)
        self._poll_timer.timeout.connect(self._load)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self._scroller = ScrollFrame(self)
        outer.addWidget(self._scroller)
        lay = self._scroller.body_layout()
        lay.setContentsMargins(T.PAD_PAGE, T.PAD_PAGE, T.PAD_PAGE, T.PAD_PAGE)
        lay.setSpacing(T.GAP_CARD)

        self._build_hero(lay)
        self._build_metrics(lay)
        self._build_grid(lay)
        self._build_collection(lay)
        self._scroller.scroll_to_top()

    # -- 页面背景：一比一复刻预览稿 main 的三层渐变 --------------------------
    def paintEvent(self, event) -> None:  # noqa: N802
        """总览页自身绘制渐变背景（盖住 Dashboard 全屏视频，复刻预览稿 main）：
        线性 165deg #021F5B→#010828 打底 + 右上 #0B2A6B 径向辉光 + 左下 #2A1230 玫粉辉光。
        其余页面透明（继续露出全屏背景视频）。"""
        p = QPainter(self)
        w, h = self.width(), self.height()
        # 1) 线性底（165deg：右下 #021F5B → 左上 #010828，62% 处转实）
        g = QLinearGradient(w, h, 0, 0)
        g.setColorAt(0.0, QColor(T.BG_GRAD_A))
        g.setColorAt(0.62, QColor(T.BG_GRAD_B))
        g.setColorAt(1.0, QColor(T.BG_GRAD_B))
        p.fillRect(self.rect(), QBrush(g))
        # 2) 右上径向辉光（#0B2A6B，at 78% -10%）
        c1 = QColor(*T.BG_RADIAL_TOP)
        r1 = QRadialGradient(w * 0.78, -h * 0.10, max(w, h) * 0.7)
        r1.setColorAt(0.0, QColor(c1.red(), c1.green(), c1.blue(), 190))
        r1.setColorAt(0.55, QColor(c1.red(), c1.green(), c1.blue(), 0))
        p.fillRect(self.rect(), QBrush(r1))
        # 3) 左下玫粉辉光（#2A1230，at -5% 100%）
        c2 = QColor(*T.BG_RADIAL_BL)
        r2 = QRadialGradient(-w * 0.05, h * 1.0, max(w, h) * 0.55)
        r2.setColorAt(0.0, QColor(c2.red(), c2.green(), c2.blue(), 160))
        r2.setColorAt(0.50, QColor(c2.red(), c2.green(), c2.blue(), 0))
        p.fillRect(self.rect(), QBrush(r2))
        p.end()

    # -- Hero：视频横幅 + 大标题 + pill 徽标（Orbis S1） ------------------
    def _build_hero(self, lay: QVBoxLayout) -> None:
        # 196px 局部视频横幅（VideoSurface 播放 hero.mp4，全屏视频背景仍由
        # Dashboard 提供；本横幅内自带视频帧，与预览稿 .hero 一致）。
        hero = _HeroBanner(self)
        self._videos.append(hero.video)
        h = hero.layout_box()

        # 左侧：标题叠层（垂直居中）+ LIVE badge（左下角，对齐预览稿）
        left = QVBoxLayout()
        left.setSpacing(5)
        left.addStretch(1)
        title = QHBoxLayout()
        title.setSpacing(12)
        cn = QLabel("回顾", hero)
        cn.setStyleSheet(f"color: {T.INK}; background: transparent;")
        cn.setFont(T.font((T.F_SANS, 30, "bold")))
        title.addWidget(cn, 0, Qt.AlignmentFlag.AlignBottom)
        en = QLabel("AGENT", hero)
        en.setStyleSheet(f"color: {T.INK}; background: transparent;")
        en.setFont(T.font((T.F_GROTESK, 30)))
        title.addWidget(en, 0, Qt.AlignmentFlag.AlignBottom)
        orb = QLabel("Orbis", hero)
        orb.setStyleSheet(f"color: {T.ACCENT}; background: transparent;")
        orb.setFont(T.font((T.F_CONDIMENT, 30)))
        title.addWidget(orb, 0, Qt.AlignmentFlag.AlignBottom)
        left.addLayout(title)
        sub = QLabel("LOCAL CAPTURE · 15S AUTO REFRESH", hero)
        sub.setStyleSheet(f"color: {T.INK2}; background: transparent;")
        sub.setFont(T.font((T.F_MONO[0], 10)))
        left.addWidget(sub)
        left.addStretch(1)
        badge = QLabel("<span style='color:#6FFF00;'>LIVE ●</span> 后端运行中", hero)
        badge.setTextFormat(Qt.TextFormat.RichText)
        badge.setStyleSheet(
            f"color: {T.INK2}; background-color: rgba(10, 22, 64, 128);"
            f" border: 1px solid {T.GLASS_EDGE}; border-radius: 6px;"
            f" padding: 3px 8px;")
        badge.setFont(T.font((T.F_MONO[0], 8)))
        left.addWidget(badge)
        h.addLayout(left, 1)

        # 右侧：pill 徽标（右上角，水平排列）
        right = QVBoxLayout()
        right.setSpacing(8)
        pill_row = QHBoxLayout()
        pill_row.setSpacing(8)
        pills = (
            ("6 VIDEOS", "#64D2FF", "#A9B4D0",
             "rgba(10, 22, 64, 140)", "rgba(100, 210, 255, 80)"),
            ("ROSE ACCENT", "#B77081", "#F0C9D0",
             "rgba(183, 112, 129, 61)", "rgba(183, 112, 129, 140)"),
            ("NEON GREEN", "#6FFF00", "#6FFF00",
             "rgba(111, 255, 0, 41)", "rgba(111, 255, 0, 128)"),
        )
        for text, dot, fg, bg, border in pills:
            pill_row.addWidget(_Pill(hero, text, dot, fg=fg, bg=bg, border=border))
        right.addLayout(pill_row)
        right.addStretch(1)
        h.addLayout(right, 0)

        lay.addWidget(hero)

    # -- 指标卡行（玻璃，Anton 大数字） --------------------------------------
    def _build_metrics(self, lay: QVBoxLayout) -> None:
        cards = QHBoxLayout()
        cards.setSpacing(8)
        self._c_total = StatCard(self, "事件总数", T.ICON["list"], T.ACCENT)
        self._c_adapter = StatCard(self, "运行中适配器", T.ICON["adapter"], T.OK)
        self._c_queue = StatCard(self, "队列深度", T.ICON["queue"], T.WARN)
        self._c_disk = StatCard(self, "磁盘剩余", T.ICON["disk"], T.INFO)
        for c in (self._c_total, self._c_adapter, self._c_queue, self._c_disk):
            cards.addWidget(c, 1)
        lay.addLayout(cards)

    # -- Bento 网格：热力图(2x1) → 趋势(1x1)+环形(1x1) → 最近活动(2x1)
    def _build_grid(self, lay: QVBoxLayout) -> None:
        grid = QGridLayout()
        grid.setSpacing(T.GAP_CARD)
        grid.setColumnStretch(0, 3)
        grid.setColumnStretch(1, 2)
        grid.setRowStretch(0, 5)
        grid.setRowStretch(1, 4)
        grid.setRowStretch(2, 4)

        heat_card = BentoCard(self, "活动热力图")
        grid.addWidget(heat_card, 0, 0, 1, 2)
        self._heatmap = Heatmap(heat_card.body)
        h_lay = QVBoxLayout(heat_card.body)
        h_lay.setContentsMargins(0, 0, 0, 0)
        h_lay.addWidget(self._heatmap)

        trend_card = BentoCard(self, "近 14 天事件趋势")
        grid.addWidget(trend_card, 1, 0)
        self._trend = BarChart(trend_card.body)
        t_lay = QVBoxLayout(trend_card.body)
        t_lay.setContentsMargins(0, 0, 0, 0)
        t_lay.addWidget(self._trend)

        donut_card = BentoCard(self, "来源分布")
        grid.addWidget(donut_card, 1, 1)
        self._donut = DonutChart(donut_card.body)
        d_lay = QVBoxLayout(donut_card.body)
        d_lay.setContentsMargins(0, 0, 0, 0)
        d_lay.addWidget(self._donut)

        all_btn = ghost_button(self, "查看全部", self._goto_events,
                               icon=T.ICON["list"], font=T.F_SMALL, padx=10)
        recent_card = BentoCard(self, "最近活动", title_action=all_btn)
        grid.addWidget(recent_card, 2, 0, 1, 2)
        self._recent_box = QWidget(recent_card.body)
        self._recent_box.setStyleSheet("background: transparent;")
        self._recent_lay = QVBoxLayout(self._recent_box)
        self._recent_lay.setContentsMargins(0, 0, 0, 0)
        self._recent_lay.setSpacing(6)
        r_lay = QVBoxLayout(recent_card.body)
        r_lay.setContentsMargins(0, 0, 0, 0)
        r_lay.addWidget(self._recent_box)

        lay.addLayout(grid, 1)

    # -- Collection：NFT 视频缩略卡 + RARITY SCORE + 紫色圆按钮（Orbis S3）----
    def _build_collection(self, lay: QVBoxLayout) -> None:
        coll = QWidget(self)
        c_lay = QVBoxLayout(coll)
        c_lay.setContentsMargins(0, 0, 0, 0)
        c_lay.setSpacing(6)

        t1 = QLabel("COLLECTION OF", coll)
        t1.setStyleSheet(f"color: {T.INK}; background: transparent;")
        t1.setFont(T.font((T.F_GROTESK, 20)))
        c_lay.addWidget(t1)

        row2 = QHBoxLayout()
        row2.setSpacing(12)
        s1 = QLabel("Space", coll)
        s1.setStyleSheet(f"color: {T.ACCENT}; background: transparent;")
        s1.setFont(T.font((T.F_CONDIMENT, 24)))
        row2.addWidget(s1, 0, Qt.AlignmentFlag.AlignBottom)
        s2 = QLabel("objects", coll)
        s2.setStyleSheet(f"color: {T.INK}; background: transparent;")
        s2.setFont(T.font((T.F_GROTESK, 20)))
        row2.addWidget(s2, 0, Qt.AlignmentFlag.AlignBottom)
        row2.addStretch(1)
        c_lay.addLayout(row2)

        underline = QFrame(coll)
        underline.setFixedHeight(3)
        underline.setStyleSheet(f"background-color: {T.ACCENT}; border: none;")
        # 霓虹绿 rule 发光（近似预览稿 .coll .rule 的 box-shadow）
        _glow = QGraphicsDropShadowEffect(coll)
        _glow.setBlurRadius(12)
        _glow.setColor(QColor(*T.NAV_SELECT_RGB, 180))
        _glow.setOffset(0, 0)
        underline.setGraphicsEffect(_glow)
        c_lay.addWidget(underline)
        c_lay.addSpacing(10)

        cards = QHBoxLayout()
        cards.setSpacing(12)
        items = [("card1.mp4", "8.7 / 10"), ("card2.mp4", "9 / 10"),
                 ("card3.mp4", "8.2 / 10")]
        for i, (vid_name, score) in enumerate(items):
            # 视频铺满整卡背景（cover + 圆角 + 底部暗渐变），文字浮于其上
            card = _VideoNFTCard(coll, video_path(vid_name))
            card.setMinimumHeight(176)
            card_lay = card.content_layout()
            # 叠层文字：RARITY SCORE + Anton 评分（上部）→ CARD-N + 圆按钮（底部）
            lbl_r = QLabel("RARITY SCORE", card)
            lbl_r.setStyleSheet(f"color: {T.INK2}; background: transparent;")
            lbl_r.setFont(T.font((T.F_MONO[0], 8)))
            card_lay.addWidget(lbl_r)
            lbl_s = QLabel(score, card)
            lbl_s.setStyleSheet(f"color: {T.INK}; background: transparent;")
            lbl_s.setFont(T.font((T.F_GROTESK, 22)))
            card_lay.addWidget(lbl_s)
            card_lay.addStretch(1)
            bar = QHBoxLayout()
            bar.setSpacing(6)
            card_lbl = QLabel(f"CARD-{i + 1}", card)
            card_lbl.setStyleSheet(f"color: {T.INK2}; background: transparent;")
            card_lbl.setFont(T.font((T.F_MONO[0], 8)))
            bar.addWidget(card_lbl, 0, Qt.AlignmentFlag.AlignBottom)
            bar.addStretch(1)
            bar.addWidget(_CollectionButton(card), 0, Qt.AlignmentFlag.AlignBottom)
            card_lay.addLayout(bar)
            cards.addWidget(card, 1)
            self._videos.append(card.video)
        c_lay.addLayout(cards)
        lay.addWidget(coll)

    # -- 刷新/轮询 ------------------------------------------------------------
    def _load(self) -> None:
        self._async.run(self.api.health, self._health_done)
        self._async.run(lambda: self.api.get_events(limit=1000), self._events_done)

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
        # 清空旧行
        while self._recent_lay.count():
            it = self._recent_lay.takeAt(0)
            w = it.widget()
            if w is not None:
                w.deleteLater()
        if not items:
            es = EmptyState(
                self._recent_box, T.ICON["sources"], "还没有捕获到事件",
                "等待系统自动捕获事件，或用「问答 → 录入知识」手动记录",
            )
            self._recent_lay.addWidget(es)
            return
        for e in items[:12]:
            content = _elide(e.get("content"), 60)
            ts = (e.get("ts") or "")[11:16]
            src = e.get("source") or "?"
            row = _ActivityRow(self._recent_box, src, ts, content)
            row.clicked.connect(self._goto_source)
            self._recent_lay.addWidget(row)
        self._recent_lay.addStretch(1)

    # -- 跳转事件流：全部按钮（纯跳转）/ 单条行（跳转并预筛该来源）----------
    def _goto_events(self) -> None:
        self._dashboard.show_page("events")

    def _goto_source(self, src: str) -> None:
        events = self._dashboard._pages.get("events")
        if events is not None:
            events.select_source(src)
        self._dashboard.show_page("events")

    # -- 可见性控制：切走停止轮询/视频，切回刷新 ------------------------------
    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        for v in self._videos:
            v.set_active(True)
        self._load()
        self._poll_timer.start()

    def hideEvent(self, event) -> None:  # noqa: N802
        super().hideEvent(event)
        self._poll_timer.stop()
        for v in self._videos:
            v.set_active(False)


class _ActivityRow(QFrame):
    """最近活动行：hover 圆角高亮 + 点击信号（带来源，用于跳事件流并预筛）。"""

    clicked = Signal(str)

    def __init__(self, parent: QWidget, src: str, ts: str, content: str) -> None:
        super().__init__(parent)
        self._src = src
        self._hovered = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setStyleSheet("background: transparent; border: none;")
        h = QHBoxLayout(self)
        h.setContentsMargins(6, 2, 6, 2)
        h.setSpacing(8)
        color = T.SOURCE_COLORS.get(src, T.ACCENT)
        icon_lbl = QLabel(chr(T.ICON["list"]), self)
        icon_lbl.setStyleSheet(f"color: {color}; background: transparent;")
        icon_lbl.setFont(T.font((T.F_ICON, 12)))
        h.addWidget(icon_lbl)
        mid = QVBoxLayout()
        mid.setSpacing(0)
        c = QLabel(content, self)
        c.setStyleSheet(f"color: {T.INK}; background: transparent;")
        c.setFont(T.font(T.F_SMALL))
        mid.addWidget(c)
        s = QLabel(_SOURCE_LABELS.get(src, src), self)
        s.setStyleSheet(f"color: {T.MUTED}; background: transparent;")
        s.setFont(T.font(T.F_TINY))
        mid.addWidget(s)
        h.addLayout(mid, 1)
        t = QLabel(ts, self)
        t.setStyleSheet(f"color: {T.MUTED}; background: transparent;")
        t.setFont(T.font((T.F_MONO[0], 9)))
        h.addWidget(t)

    def paintEvent(self, event) -> None:  # noqa: N802
        if self._hovered:
            p = QPainter(self)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            path = QPainterPath()
            path.addRoundedRect(QRectF(0.5, 0.5, self.width() - 1,
                                       self.height() - 1), 8, 8)
            p.fillPath(path, QColor(*T.GLASS_HOVER_RGB, 60))
            p.end()
        super().paintEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self._src)
        super().mousePressEvent(event)

    def enterEvent(self, event) -> None:  # noqa: N802
        self._hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hovered = False
        self.update()
        super().leaveEvent(event)


class _NFTVeil(QWidget):
    """NFT 卡视频遮罩：顶部微暗 + 底部明显压暗，保证叠层文字可读。"""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        h = self.height()
        g = QLinearGradient(0, 0, 0, h)
        g.setColorAt(0.0, QColor(1, 8, 40, 95))
        g.setColorAt(0.45, QColor(1, 8, 40, 20))
        g.setColorAt(0.72, QColor(1, 8, 40, 150))
        g.setColorAt(1.0, QColor(1, 8, 40, 205))
        p.fillRect(self.rect(), QBrush(g))
        p.end()


class _VideoNFTCard(QWidget):
    """NFT 视频卡：cardN.mp4 铺满整卡背景（cover + 圆角 mask + 压暗）
    + 底部暗渐变遮罩 + 叠层文字（RARITY SCORE / 评分 / CARD-N / 圆按钮）。

    层级（创建顺序）：视频（最底）→ veil（中层）→ 文字布局（最顶）。
    """

    def __init__(self, parent: QWidget, video: str, darken: float = 0.55) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._video = VideoSurface(self, video, mode="cover", prefer_cv=True)
        self._video.set_darken(darken)
        self._video.play()
        self._veil = _NFTVeil(self)
        self._lay = QVBoxLayout(self)
        self._lay.setContentsMargins(12, 10, 12, 10)
        self._lay.setSpacing(4)
        self.video = self._video   # 暴露给 OverviewPage 统一可见性管理

    def content_layout(self) -> QVBoxLayout:
        """内部垂直布局：调用方往里填叠层文字。"""
        return self._lay

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._video.setGeometry(self.rect())
        self._veil.setGeometry(self.rect())
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()).adjusted(0, 0, -1, -1),
                            T.RADIUS, T.RADIUS)
        self._video.setMask(QRegion(path.toFillPolygon().toPolygon()))

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(0.5, 0.5, self.width() - 1, self.height() - 1)
        path = QPainterPath()
        path.addRoundedRect(rect, T.RADIUS, T.RADIUS)
        p.setPen(QPen(QColor(T.GLASS_EDGE), 1))
        p.drawPath(path)
        p.end()


class _CollectionButton(QPushButton):
    """Orbis NFT 紫色圆按钮（视频蓝紫灰→玫粉渐变）+ 白色箭头，hover 放大。"""

    def __init__(self, parent: QWidget) -> None:
        super().__init__("→", parent)
        self.setFixedSize(34, 34)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._hover = False
        self.setStyleSheet("border: none; color: #FFFFFF; font-size: 16px;"
                           " font-weight: bold; background: transparent;")

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = self.rect()
        c = r.center()
        s = 1.08 if self._hover else 1.0
        w, h = r.width() * s, r.height() * s
        circle = QRectF(c.x() - w / 2, c.y() - h / 2, w, h)
        # 阴影（下偏暗紫）
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(QColor(T.PURPLE_DEEP)))
        p.drawEllipse(circle.translated(0, 2))
        # 渐变底
        grad = QLinearGradient(0, circle.top(), 0, circle.bottom())
        grad.setColorAt(0.0, QColor(T.PURPLE_END))
        grad.setColorAt(1.0, QColor(T.PURPLE))
        p.setBrush(grad)
        p.drawEllipse(circle)
        # 白色箭头
        p.setPen(QPen(QColor("#FFFFFF"), 2, Qt.PenStyle.SolidLine,
                      Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        p.drawLine(int(c.x() - 4), int(c.y()), int(c.x() + 4), int(c.y()))
        p.drawLine(int(c.x() + 4), int(c.y()), int(c.x()), int(c.y() - 4))
        p.drawLine(int(c.x() + 4), int(c.y()), int(c.x()), int(c.y() + 4))
        p.end()

    def enterEvent(self, event) -> None:  # noqa: N802
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hover = False
        self.update()
        super().leaveEvent(event)


# ===========================================================================
# 周报：选择周 → 生成 → 展示（指标 + 主线 + 成就/阻塞/下周计划 + 溯源）
# ===========================================================================
class ReportPage(QWidget):
    """周报：按周聚合本地事件，LLM 生成主线与结论（带溯源，防编造）。"""

    def __init__(self, dashboard: Dashboard) -> None:
        super().__init__(dashboard)
        self._dashboard = dashboard
        self.api: ApiClient = dashboard.api
        self._async = _Async(self)
        self._videos: list[VideoSurface] = []
        self._build()

    @staticmethod
    def _default_monday() -> str:
        today = _dt.date.today()
        monday = today - _dt.timedelta(days=today.weekday())
        return monday.isoformat()

    def _build(self) -> None:
        # 全屏视频背景由 Dashboard 全局提供（about.mp4 持续循环、铺满整窗）。
        # 本页不再自带背景视频 / 玻璃面板：内容直接悬浮在视频之上（全屏观感）。
        # QTextEdit 与输入框保留不透明 CARD 底（全局 QSS 已带圆角边框），保证可读。
        lay = QVBoxLayout(self)
        lay.setContentsMargins(T.PAD_PAGE, T.PAD_PAGE, T.PAD_PAGE, T.PAD_PAGE)
        lay.setSpacing(T.GAP_CARD)

        header = PageHeader(self, "周报",
                            "按周聚合本地事件，LLM 生成主线与结论（带溯源，防编造）")
        lay.addWidget(header)

        row = QHBoxLayout()
        row.setSpacing(8)
        lbl = QLabel("周开始日期", self)
        lbl.setStyleSheet(f"color: {T.INK2}; background: transparent;")
        lbl.setFont(T.font(T.F_SMALL))
        row.addWidget(lbl)
        self._week_edit = QLineEdit(self)
        self._week_edit.setText(self._default_monday())
        self._week_edit.setFixedWidth(120)
        self._week_edit.returnPressed.connect(self._generate)
        row.addWidget(self._week_edit)
        row.addWidget(primary_button(self, "生成周报", self._generate,
                                     icon=T.ICON["check"], padx=14, font=T.F_SMALL))
        row.addWidget(ghost_button(self, "刷新", self._load,
                                   icon=T.ICON["refresh"], padx=12, font=T.F_SMALL))
        row.addStretch(1)
        lay.addLayout(row)

        self._msg = message(self, "")
        lay.addWidget(self._msg)

        self._text = QTextEdit(self)
        self._text.setReadOnly(True)
        self._text.setFont(T.font(T.F_BODY))
        self._text.setStyleSheet(
            f"QTextEdit {{ background-color: {T.INPUT_GLASS}; color: {T.INK};"
            f" border: 1px solid {T.GLASS_EDGE}; border-radius: 8px; }}")
        lay.addWidget(self._text, 1)

    def _generate(self) -> None:
        week = self._week_edit.text().strip()
        self._msg.setText("生成中（LLM 约 5-15 秒）…")
        self._msg.setStyleSheet(f"color: {T.INK2}; background: transparent;")
        self._async.run(lambda: self.api.report_generate(week or None), self._gen_done)

    def _gen_done(self, result, err) -> None:
        if err:
            self._msg.setText(f"生成失败：{err}")
            self._msg.setStyleSheet(f"color: {T.ERR}; background: transparent;")
            return
        self._msg.setText("✓ 已生成")
        self._msg.setStyleSheet(f"color: {T.OK}; background: transparent;")
        self._render(result)

    def _load(self) -> None:
        week = self._week_edit.text().strip()
        self._async.run(lambda: self.api.report_get(week or None), self._load_done)

    def _load_done(self, result, err) -> None:
        if err:
            self._msg.setText("该周暂无周报，点「生成周报」")
            self._msg.setStyleSheet(f"color: {T.INK2}; background: transparent;")
            self._text.clear()
            return
        self._render(result)

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
        self._text.setPlainText("\n".join(lines))

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        for v in self._videos:
            v.set_active(True)

    def hideEvent(self, event) -> None:  # noqa: N802
        super().hideEvent(event)
        for v in self._videos:
            v.set_active(False)


# ===========================================================================
# 问答：自然语言提问 → LLM 基于本地事件回答（带溯源）
# ===========================================================================
class QaPage(QWidget):
    """历史问答：基于本地事件回答并溯源。

    升级（阶段 3/4/5）：
    - 混合检索（语义 ∪ 关键词）+ 可选联网搜索，来源分「本地事件 / 网上搜索」标注；
    - 短期会话缓存：跨轮复用 session_id，问答告一段落（空闲 60s / 主动结束）
      触发判断 Agent 独立判断是否沉淀；
    - 知识录入区（/api/knowledge/ingest）：路由门控 → 入库或 Pending 确认弹窗；
    - 未处理 pending（门控打回 / 沉淀确认）进入页面时自动弹窗恢复处理。
    """

    def __init__(self, dashboard: Dashboard) -> None:
        super().__init__(dashboard)
        self._dashboard = dashboard
        self.api: ApiClient = dashboard.api
        self._async = _Async(self)
        # 短期会话缓存：跨轮复用 session_id（判断 Agent 的输入源）
        self._session_id: str | None = None
        # 已处理/已弹过确认的 pending_id，防止重复弹窗
        self._handled_pending: set[str] = set()
        self._build()
        # 判断 Agent 空闲触发：停止提问约 60s → 独立判断短期对话是否值得沉淀
        self._idle_timer = QTimer(self)
        self._idle_timer.setSingleShot(True)
        self._idle_timer.timeout.connect(self._judge_idle)

    def _build(self) -> None:
        lay = QVBoxLayout(self)
        lay.setContentsMargins(T.PAD_PAGE, T.PAD_PAGE, T.PAD_PAGE, T.PAD_PAGE)
        lay.setSpacing(T.GAP_CARD)

        header = PageHeader(self, "历史问答",
                            "问「本周我做了什么」「我学了什么」——基于本地事件回答并溯源")
        lay.addWidget(header)

        row = QHBoxLayout()
        row.setSpacing(8)
        self._entry = QLineEdit(self)
        self._entry.setPlaceholderText("例如：本周我做了什么？")
        self._entry.setMinimumWidth(420)
        self._entry.returnPressed.connect(self._ask)
        row.addWidget(self._entry, 1)
        row.addWidget(primary_button(self, "提问", self._ask, icon=T.ICON["search"],
                                     padx=16))
        row.addWidget(ghost_button(self, "结束会话", self._end_session,
                                   icon=T.ICON["close"], padx=12))
        lay.addLayout(row)

        self._msg = message(self, "")
        lay.addWidget(self._msg)

        ans_card = BentoCard(self, "回答", content_margin=12)
        self._answer = QTextEdit(ans_card.body)
        self._answer.setReadOnly(True)
        self._answer.setFont(T.font(T.F_BODY))
        self._answer.setFixedHeight(150)
        a_lay = ans_card.body_layout()
        a_lay.setContentsMargins(8, 8, 8, 8)
        a_lay.addWidget(self._answer)
        lay.addWidget(ans_card)

        src_card = BentoCard(self, "溯源事件", content_margin=12)
        self._sources = QListWidget(src_card.body)
        s_lay = src_card.body_layout()
        s_lay.setContentsMargins(8, 8, 8, 8)
        # 网上搜索结果（带「网上搜索」徽标 + 可点击链接，无结果时隐藏）
        self._web_lbl = QLabel(src_card.body)
        self._web_lbl.setTextFormat(Qt.TextFormat.RichText)
        self._web_lbl.setOpenExternalLinks(True)
        self._web_lbl.setWordWrap(True)
        self._web_lbl.setStyleSheet(f"color: {T.INK2}; background: transparent;")
        self._web_lbl.hide()
        s_lay.addWidget(self._sources)
        s_lay.addWidget(self._web_lbl)
        lay.addWidget(src_card, 1)

        ingest_card = BentoCard(self, "录入知识", content_margin=12)
        i_lay = ingest_card.body_layout()
        i_lay.setContentsMargins(8, 8, 8, 8)
        i_row = QHBoxLayout()
        i_row.setSpacing(8)
        self._ingest_entry = QLineEdit(ingest_card.body)
        self._ingest_entry.setPlaceholderText("录入知识点，如：事件总线是发布-订阅模式")
        self._ingest_entry.returnPressed.connect(self._ingest)
        i_row.addWidget(self._ingest_entry, 1)
        i_row.addWidget(primary_button(ingest_card.body, "录入", self._ingest,
                                       icon=T.ICON["knowledge"], padx=16))
        i_lay.addLayout(i_row)
        self._ingest_msg = message(ingest_card.body, "")
        i_lay.addWidget(self._ingest_msg)
        lay.addWidget(ingest_card)

    def _ask(self) -> None:
        question = self._entry.text().strip()
        if not question:
            return
        self._msg.setText("思考中（LLM 约 3-10 秒）…")
        self._msg.setStyleSheet(f"color: {T.INK2}; background: transparent;")
        # 重置空闲定时器：每次提问代表对话继续，告一段落后（60s 无操作）再判断沉淀
        self._idle_timer.start(_IDLE_JUDGE_MS)
        self._async.run(lambda: self.api.qa_ask(question, self._session_id), self._done)

    def _done(self, result, err) -> None:
        if err:
            self._msg.setText(f"提问失败：{err}")
            self._msg.setStyleSheet(f"color: {T.ERR}; background: transparent;")
            return
        # 跨轮复用会话（判断 Agent 的输入源）
        self._session_id = result.get("session_id") or self._session_id
        self._msg.setText(f"基于 {result.get('events_count', 0)} 条事件回答")
        self._msg.setStyleSheet(f"color: {T.INK2}; background: transparent;")
        answer = result.get("answer")
        self._answer.setPlainText(answer or result.get("message")
                                  or result.get("error") or "（无回答）")
        # 溯源事件（本地事件 / 长期知识卡片，按来源打标签）
        self._sources.clear()
        for s in result.get("sources") or []:
            content = _elide(s.get("content"), 60)
            src = _SOURCE_LABELS.get(s.get("source"), s.get("source"))
            tag = "长期知识" if s.get("source") == "learning" else "本地"
            self._sources.addItem(f"[{(s.get('ts') or '')[:16]}] {tag} · {src}  {content}")
        # 网上搜索结果：徽标 + 标题链接 + 摘要（不可信外部文本，仅作参考）
        web = result.get("web_sources") or []
        if web:
            lines = [f'<span style="color:{T.ACCENT}"><b>🛰 网上搜索</b></span>'
                     '（仅供参考，可能过时或不准确）']
            for w in web[:6]:
                title = _html.escape(w.get("title") or "无标题")
                url = _html.escape(w.get("url") or "", quote=True)
                snippet = _html.escape((w.get("snippet") or "")[:120])
                lines.append(f'<a href="{url}" style="color:{T.ACCENT}">{title}</a>'
                             f'<br>{snippet}')
            self._web_lbl.setText("<br><br>".join(lines))
            self._web_lbl.show()
        else:
            self._web_lbl.hide()

    # ------------------------------------------------------------------
    # 判断 Agent（独立判断沉淀）+ 主动结束会话
    # ------------------------------------------------------------------
    def _end_session(self) -> None:
        """主动结束会话：立即触发沉淀判断，并清空会话缓存 id（下轮开启新会话）。"""
        self._idle_timer.stop()
        sid = self._session_id
        self._session_id = None
        if not sid:
            self._msg.setText("当前没有进行中的会话")
            self._msg.setStyleSheet(f"color: {T.INK2}; background: transparent;")
            return
        self._msg.setText("正在判断本次对话是否有可沉淀的知识点…")
        self._msg.setStyleSheet(f"color: {T.INK2}; background: transparent;")
        self._async.run(lambda: self.api.qa_judge(sid), self._judged)

    def _judge_idle(self) -> None:
        """空闲触发：对话告一段落（60s 无提问），独立判断整段短期缓存。"""
        sid = self._session_id
        if not sid:
            return
        self._async.run(lambda: self.api.qa_judge(sid), self._judged)

    def _judged(self, result, err) -> None:
        """判断 Agent 回调：有候选 → 弹沉淀确认弹窗；其余（过短/无 key/降级）静默。"""
        if err:
            # 判断 Agent 降级不打扰：仅记录到状态栏
            self._msg.setText(f"沉淀判断失败（不影响问答）：{err}")
            self._msg.setStyleSheet(f"color: {T.MUTED}; background: transparent;")
            return
        if not result or not result.get("has_memory"):
            return
        item = {
            "id": result.get("pending_id"),
            "kind": "memory_save",
            "payload": {"concepts": result.get("concepts") or [],
                        "reason": result.get("reason") or ""},
        }
        self._prompt_pending(item)

    # ------------------------------------------------------------------
    # 知识录入（路由门控）与 Pending 统一处理
    # ------------------------------------------------------------------
    def _ingest(self) -> None:
        text = self._ingest_entry.text().strip()
        if not text:
            return
        # 后端 needs_review 响应不含原文本，本地暂存供弹窗展示
        self._pending_ingest_text = text
        self._ingest_msg.setText("门控判断中…")
        self._ingest_msg.setStyleSheet(f"color: {T.INK2}; background: transparent;")
        self._async.run(lambda: self.api.knowledge_ingest(text), self._ingest_done)
        self._ingest_entry.clear()

    def _ingest_done(self, result, err) -> None:
        if err:
            self._ingest_msg.setText(f"录入失败：{err}")
            self._ingest_msg.setStyleSheet(f"color: {T.ERR}; background: transparent;")
            return
        if result and result.get("verdict") == "needs_review":
            # 门控打回 → 弹确认弹窗（原文本 + concern + solution）
            # 注意：clear 直接入库时后端 gated 也为 True（表示经过门控），
            # 故必须以 verdict 为准，不能只看 gated。
            item = {
                "id": result.get("pending_id"),
                "kind": "review_ingest",
                "payload": {"original_text": self._pending_ingest_text,
                            "concern": result.get("concern"),
                            "solution": result.get("solution")},
            }
            self._ingest_msg.setText("内容需确认，请在弹窗中选择处理方式")
            self._ingest_msg.setStyleSheet(f"color: {T.WARN}; background: transparent;")
            self._prompt_pending(item)
        else:
            self._ingest_msg.setText("已入库")
            self._ingest_msg.setStyleSheet(f"color: {T.OK}; background: transparent;")

    def _prompt_pending(self, item: dict | None) -> None:
        """按 kind 弹对应确认弹窗，处理后调 /api/pending/decide。

        幂等：同一 pending_id 本页仅弹一次（避免切页/重复触发重复弹窗）。
        """
        if not item or not item.get("id"):
            return
        pid = item["id"]
        if pid in self._handled_pending:
            return
        kind = item.get("kind")
        if kind == "review_ingest":
            action = PendingDialog.review_ingest(self.window(), item)
        elif kind == "memory_save":
            action = PendingDialog.memory_save(self.window(), item)
        else:
            self._handled_pending.add(pid)
            return
        # 关闭对话框（action=None）= 暂不处理，本页不再重复弹（pending 7 天后自动过期）
        self._handled_pending.add(pid)
        if action:
            self._async.run(lambda: self.api.pending_decide(pid, action), self._decided)

    def _decided(self, result, err) -> None:
        if err:
            self._msg.setText(f"处理失败：{err}")
            self._msg.setStyleSheet(f"color: {T.ERR}; background: transparent;")
            return
        kind = result.get("kind")
        action = result.get("action")
        if action == "reject":
            self._msg.setText("已丢弃，不存入长期库")
        elif kind == "memory_save":
            self._msg.setText(f"已确认存入 {result.get('inserted', 0)} 张复习卡片")
        else:
            self._msg.setText("内容已入库")
        self._msg.setStyleSheet(f"color: {T.OK}; background: transparent;")

    # ------------------------------------------------------------------
    # 页面可见性：进入时恢复未处理 pending；离开时停掉空闲定时器
    # ------------------------------------------------------------------
    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._refresh_pending()

    def hideEvent(self, event) -> None:  # noqa: N802
        super().hideEvent(event)
        self._idle_timer.stop()

    def _refresh_pending(self) -> None:
        """拉取后端未处理 pending（重启后残留也能恢复弹窗），逐个处理。"""
        self._async.run(lambda: self.api.pending_list(status="pending"), self._pending_loaded)

    def _pending_loaded(self, result, err) -> None:
        if err:
            return
        for item in result.get("items") or []:
            self._prompt_pending(item)


# ===========================================================================
# 事件流：来源筛选 + 事件表格
# ===========================================================================
class EventsPage(QWidget):
    """事件流：来源筛选 chip + 事件表格。"""

    _HEADERS = ("时间", "来源", "内容", "引用", "分级", "")
    _WIDTHS = (150, 70, 400, 120, 60, 44)

    def __init__(self, dashboard: Dashboard) -> None:
        super().__init__(dashboard)
        self._dashboard = dashboard
        self.api: ApiClient = dashboard.api
        self._async = _Async(self)
        self._source = ""
        self._build()

    def _build(self) -> None:
        lay = QVBoxLayout(self)
        lay.setContentsMargins(T.PAD_PAGE, T.PAD_PAGE, T.PAD_PAGE, T.PAD_PAGE)
        lay.setSpacing(8)

        header = PageHeader(self, "事件流", "全部归一化事件（内容已脱敏）")
        lay.addWidget(header)
        self._sub = message(self, "")
        lay.addWidget(self._sub)

        chips_host = QWidget(self)
        chips_host.setStyleSheet("background: transparent;")
        chips = QHBoxLayout(chips_host)
        chips.setContentsMargins(0, 0, 0, 0)
        chips.setSpacing(6)
        self._chip_btns: dict[str, object] = {}
        for key in [""] + _SOURCES:
            text = "全部" if not key else _SOURCE_LABELS.get(key, key)
            btn = chip_button(chips_host, text, lambda k=key: self._filter(k),
                              selected=(key == self._source))
            chips.addWidget(btn)
            self._chip_btns[key] = btn
        chips.addStretch(1)
        chips.addWidget(primary_button(chips_host, "清除全部", self._delete_all,
                                       padx=14, font=T.F_SMALL))
        lay.addWidget(chips_host)

        card = BentoCard(self, content_margin=6)
        self._table = QTableWidget(0, len(self._HEADERS), card.body)
        self._table.setHorizontalHeaderLabels(self._HEADERS)
        self._table.verticalHeader().setVisible(False)
        self._table.verticalHeader().setDefaultSectionSize(28)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self._table.setAlternatingRowColors(True)
        for i, (h, w) in enumerate(zip(self._HEADERS, self._WIDTHS)):
            self._table.setColumnWidth(i, w)
        self._table.horizontalHeader().setStretchLastSection(False)
        # 内容列自适应拉伸；操作列保持固定窄宽
        self._table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.Stretch)
        t_lay = card.body_layout()
        t_lay.setContentsMargins(4, 4, 4, 4)
        t_lay.addWidget(self._table)
        lay.addWidget(card, 1)

        foot = QHBoxLayout()
        self._count_lbl = message(self, "")
        foot.addWidget(self._count_lbl)
        foot.addStretch(1)
        foot.addWidget(ghost_button(self, "刷新", self._load, icon=T.ICON["refresh"],
                                    padx=12, font=T.F_SMALL))
        lay.addLayout(foot)

    def _filter(self, key: str) -> None:
        self._source = key
        for k, btn in self._chip_btns.items():
            chip_set(btn, k == key)
        self._load()

    def select_source(self, key: str) -> None:
        """外部预筛（总览「最近活动」点击行跳转）：设置来源筛选。

        只更新状态与 chip 选中态，不主动拉数 —— 切页后由 showEvent 触发一次
        _load()，避免重复请求。
        """
        if key not in self._chip_btns:
            key = ""
        self._source = key
        for k, btn in self._chip_btns.items():
            chip_set(btn, k == key)

    def _load(self) -> None:
        self._async.run(lambda: self.api.get_events(
            source=self._source or None, limit=500), self._done)

    def _done(self, result, err) -> None:
        if err:
            self._sub.setText(f"加载失败：{err}")
            self._sub.setStyleSheet(f"color: {T.ERR}; background: transparent;")
            return
        items = result.get("items") or []
        # 行号 → 事件 id 映射（操作列删除按钮按行删除）
        self._row_ids = [e.get("id") or "" for e in items]
        self._sub.setText(
            f"全部归一化事件（内容已脱敏）· 共 {result.get('total', len(items))} 条")
        self._sub.setStyleSheet(f"color: {T.INK2}; background: transparent;")
        self._count_lbl.setText(f"显示 {len(items)} 条")
        self._table.setRowCount(0)
        for i, e in enumerate(items):
            content = _elide(e.get("content"), 120)
            ref = (e.get("source_ref") or "").split("/")[-1].split("\\")[-1]
            self._table.insertRow(i)
            values = (e.get("ts"),
                      _SOURCE_LABELS.get(e.get("source"), e.get("source")),
                      content, ref, e.get("grade") or "internal")
            for col, text in enumerate(values):
                item = QTableWidgetItem(str(text))
                item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                if col == 4:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self._table.setItem(i, col, item)
            # 操作列：每行删除按钮
            del_btn = primary_button(self._table, "×",
                                     lambda r=i: self._delete_row(r),
                                     padx=8, font=T.F_SMALL)
            del_btn.setToolTip("删除这条事件")
            self._table.setCellWidget(i, len(self._HEADERS) - 1, del_btn)

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._load()

    # -- 删除单条（每行操作列 ×）/ 清除全部（表格上方）----------------------
    def _delete_row(self, row: int) -> None:
        from ..dialogs import confirm
        if not 0 <= row < len(getattr(self, "_row_ids", [])):
            return
        event_id = self._row_ids[row]
        if not event_id:
            return
        if not confirm(self, "删除记录",
                       "确定删除这条事件吗？删除后不可恢复。", default_yes=False):
            return
        self._async.run(lambda: self.api.delete_event(event_id), self._done_delete)

    def _delete_all(self) -> None:
        from ..dialogs import confirm
        if not confirm(self, "清除全部",
                       "确定清除全部事件吗？将删除所有来源的事件，不可恢复。",
                       default_yes=False):
            return
        self._async.run(self.api.delete_events, self._done_delete_all)

    def _done_delete(self, result, err) -> None:
        if err:
            from ..dialogs import error
            error(self, "删除记录", f"删除失败：{err}")
            return
        self._load()

    def _done_delete_all(self, result, err) -> None:
        if err:
            from ..dialogs import error
            error(self, "清除全部", f"删除失败：{err}")
            return
        self._load()


# ===========================================================================
# 番茄钟：统计概览 + 打开番茄钟弹窗
# ===========================================================================
class PomodoroPage(QWidget):
    """番茄钟：统计卡片 + 打开完整状态机弹窗。"""

    def __init__(self, dashboard: Dashboard) -> None:
        super().__init__(dashboard)
        self._dashboard = dashboard
        self.api: ApiClient = dashboard.api
        self._async = _Async(self)
        self._build()

    def _build(self) -> None:
        lay = QVBoxLayout(self)
        lay.setContentsMargins(T.PAD_PAGE, T.PAD_PAGE, T.PAD_PAGE, T.PAD_PAGE)
        lay.setSpacing(T.GAP_CARD)

        header = PageHeader(self, "番茄钟",
                            "专注时段统计（完整状态机在弹窗中操作）")
        lay.addWidget(header)

        cards = QHBoxLayout()
        cards.setSpacing(8)
        self._c_today = StatCard(self, "今日番茄", T.ICON["pomodoro"], T.PHASE_FOCUS)
        self._c_week = StatCard(self, "本周番茄", T.ICON["calendar"], T.INFO)
        self._c_total = StatCard(self, "累计番茄", T.ICON["total"], T.OK)
        self._c_streak = StatCard(self, "连续天数", T.ICON["streak"], T.WARN)
        for c in (self._c_today, self._c_week, self._c_total, self._c_streak):
            cards.addWidget(c, 1)
        lay.addLayout(cards)

        body = BentoCard(self, content_margin=22)
        body.setMinimumHeight(280)
        b_lay = body.body_layout()
        b_lay.setAlignment(Qt.AlignmentFlag.AlignTop)
        b_lay.addSpacing(10)
        btn = primary_button(body.body, "打开番茄闹钟", self._open_pomodoro,
                             icon=T.ICON["play"], padx=24, font=(T.F_SANS, 11, "bold"))
        b_lay.addWidget(btn, 0, Qt.AlignmentFlag.AlignHCenter)
        b_lay.addSpacing(14)
        b_lay.addWidget(ghost_button(body.body, "刷新统计", self._load,
                                     icon=T.ICON["refresh"], padx=14,
                                     font=T.F_SMALL), 0, Qt.AlignmentFlag.AlignHCenter)
        lay.addWidget(body, 1)

    def _open_pomodoro(self) -> None:
        from ..dialogs.pomodoro import PomodoroDialog
        dlg = PomodoroDialog(self._notify, self.api, on_recorded=self._load)
        self._dashboard._pomodoro_dlg = dlg  # 保持引用防 GC
        dlg.show()

    def _notify(self, text: str) -> None:
        from ..dialogs import info
        info(self, "番茄钟", text)

    def _load(self) -> None:
        self._async.run(self.api.pomodoro_stats, self._done)

    def _done(self, result, err) -> None:
        if err:
            return
        today = result.get("today") or {}
        week = result.get("week") or {}
        self._c_today.set(today.get("count", 0), f"{today.get('focus_minutes', 0)} 分钟")
        self._c_week.set(week.get("count", 0), f"{week.get('focus_minutes', 0)} 分钟")
        self._c_total.set(result.get("total_count", 0),
                          f"{result.get('total_minutes', 0)} 分钟")
        self._c_streak.set(result.get("streak_days", 0),
                           f"中断 {result.get('interrupted_count', 0)} 次")

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._load()


# 页面类映射（置于全部页面类定义之后）
_PAGE_CLS = {
    "overview": OverviewPage,
    "report": ReportPage,
    "qa": QaPage,
    "events": EventsPage,
    "pomodoro": PomodoroPage,
}
