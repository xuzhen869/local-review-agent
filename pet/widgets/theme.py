"""全局设计 token：Orbis 太空风（深空蓝 + 霓虹绿 + liquid-glass 玻璃）—— PySide6/Qt6 版。

视觉语言：固定暗色太空主题（#010828 深空海军蓝底 / #EFF4FF 奶油白字 / #6FFF00 霓虹绿点缀），
Anton 压缩标题字体 + Condiment 手写体点缀 + 等宽正文标签 + 玻璃卡片渐变边缘。

本模块是原 tkinter theme.py 的 Qt 迁移：颜色 / 间距 / 图标 / 字体 token 保持同名，
上层组件（widgets / panels / dialogs）统一引用；另提供 Qt 专用辅助：
- font(...)        将 (family, size, weight) 元组转为 QFont
- build_qss()      由 token 生成全局 QSS 样式表
- ensure_fonts()   QFontDatabase 注册 Anton/Condiment 私有字体（不写系统）
- apply_window_material()  DWM Acrylic 磨砂 + 暗色标题栏（pywinstyles，失败静默降级）
"""
from __future__ import annotations

import ctypes
import os
import sys
from typing import Any

from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtCore import Qt

# ---------------------------------------------------------------------------
# 字体族（Anton=压缩标题 / Condiment=手写点缀 / 等宽=正文标签 / YaHei=中文正文）
# Anton 无 CJK 字形：中文标题走 F_SANS(YaHei Bold)，拉丁与数字走 F_GROTESK
#
# ⚠ 关键约束：QFontDatabase.families() 在 QApplication 创建前调用会触发 native
# 崩溃（非 Python 异常，try/except 拦不住）。因此顶层字体 token 一律用保守默认值
# （Windows 必自带字体），系统字体探测统一放在 ensure_fonts()（QApplication 创建
# 后调用）内执行并刷新全部相关 token。
# ---------------------------------------------------------------------------
def _families() -> set[str]:
    """枚举系统字体。仅在 QApplication 创建后调用，否则会 native 崩溃。"""
    return set(QFontDatabase.families())


def _pick(family: str, *fallbacks: str) -> str:
    fams = _families()
    for f in (family, *fallbacks):
        if f in fams:
            return f
    return family


# 顶层保守默认（Windows 自带、必存在；ensure_fonts 后按系统字体刷新）
F_GROTESK = "Segoe UI"                       # 拉丁/数字显示字体
F_CONDIMENT = "Segoe Script"                 # 手写体点缀
F_SANS = "Microsoft YaHei UI"                # 中文正文（现代黑体）
F_DISPLAY = F_GROTESK
F_ICON = "Segoe Fluent Icons"                # 线框图标字体（默认候选）

# 字体 token（保持与 tkinter 版同名）：(family, size, weight) weight ∈ {"normal","bold"}
F_TITLE = (F_SANS, 18, "bold")        # 页面大标题（中文）
F_H = (F_SANS, 13, "bold")            # 卡片 / 区块标题
F_BODY = (F_SANS, 10)                 # 正文 / 按钮
F_SMALL = (F_SANS, 9)                 # 次要文字 / 副标题
F_TINY = (F_SANS, 8)                  # 图例 / 角标
F_MONO = ("Cascadia Mono", "Consolas", "monospace")   # 代码 / 时间戳 / 标签
F_STAT = (F_GROTESK, 22, "normal")    # 统计大数字
F_STAT_SMALL = (F_GROTESK, 14, "normal")   # 统计大数字（超宽降级）
F_HERO = (F_GROTESK, 32, "normal")    # Hero 大标题
F_CUR = (F_CONDIMENT, 24, "normal")   # 手写体点缀基准字号
F_EMOJI = ("Segoe UI Emoji", 12)      # 图标 emoji
F_NAV = (F_SANS, 10, "bold")          # 侧边栏导航文字


def font(spec: tuple | str | QFont, default_size: int = 10) -> QFont:
    """把字体 token 统一转成 QFont。

    支持三种入参：
    - tuple：(family, size[, weight])
    - str  ：仅 family，用 default_size
    - QFont：原样返回
    """
    if isinstance(spec, QFont):
        return QFont(spec)
    if isinstance(spec, str):
        return QFont(spec, default_size)
    family = spec[0] if spec and spec[0] else F_SANS
    size = int(spec[1]) if len(spec) > 1 and spec[1] else default_size
    weight = "bold" if len(spec) > 2 and spec[2] == "bold" else "normal"
    f = QFont(family, max(6, size))
    f.setWeight(QFont.Weight.Bold if weight == "bold" else QFont.Weight.Normal)
    return f


# ---------------------------------------------------------------------------
# 字体私有注册（QFontDatabase.addApplicationFont，不写入系统）
# ---------------------------------------------------------------------------
_FONT_DIRS: str | None = None


def _find_font_dir() -> str | None:
    """定位 assets/fonts：优先 _MEIPASS（PyInstaller），再 exe 旁，最后源码项目根。"""
    global _FONT_DIRS
    if _FONT_DIRS is not None:
        return _FONT_DIRS
    cands: list[str] = []
    if getattr(sys, "_MEIPASS", None):
        cands.append(os.path.join(sys._MEIPASS, "assets", "fonts"))
    if getattr(sys, "frozen", False) and sys.executable:
        cands.append(os.path.join(os.path.dirname(sys.executable), "assets", "fonts"))
    cands.append(os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),  # widgets/.. = pet/
        "assets", "fonts"))
    cands.append(os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "assets", "fonts"))  # pet/.. = 项目根
    for c in cands:
        if os.path.isdir(c):
            _FONT_DIRS = c
            return c
    return None


def ensure_fonts() -> None:
    """注册 Anton/Condiment 私有字体并刷新相关字体 token。

    必须在 QApplication 创建后调用（QFontDatabase.families 需要 app 实例）。
    失败时静默回退系统字体，绝不抛异常。
    """
    global F_GROTESK, F_CONDIMENT, F_SANS, F_ICON, F_DISPLAY
    global F_TITLE, F_H, F_BODY, F_SMALL, F_TINY, F_NAV
    global F_STAT, F_STAT_SMALL, F_HERO, F_CUR
    d = _find_font_dir()
    if d:
        for name in ("Anton-Regular.ttf", "Condiment-Regular.ttf"):
            p = os.path.join(d, name)
            if os.path.exists(p):
                try:
                    QFontDatabase.addApplicationFont(p)
                except Exception:  # noqa: BLE001
                    pass
    F_GROTESK = _pick("Anton", "Arial Black", "Segoe UI Variable Display", "Segoe UI")
    F_CONDIMENT = _pick("Condiment", "Segoe Script", "Segoe Print", "Segoe UI")
    F_SANS = _pick("Segoe UI Variable Text", "Segoe UI", "Microsoft YaHei UI")
    F_ICON = _pick("Segoe Fluent Icons", "Segoe UI Symbol", "Segoe MDL2 Assets")
    F_DISPLAY = F_GROTESK
    F_TITLE = (F_SANS, 18, "bold")
    F_H = (F_SANS, 13, "bold")
    F_BODY = (F_SANS, 10)
    F_SMALL = (F_SANS, 9)
    F_TINY = (F_SANS, 8)
    F_NAV = (F_SANS, 10, "bold")
    F_STAT = (F_GROTESK, 22, "normal")
    F_STAT_SMALL = (F_GROTESK, 14, "normal")
    F_HERO = (F_GROTESK, 32, "normal")
    F_CUR = (F_CONDIMENT, 24, "normal")


# ---------------------------------------------------------------------------
# 图标（Segoe Fluent Icons 码位，PUA 私有区）——沿用既有语义映射
# ---------------------------------------------------------------------------
ICON = {
    "home": 0xE80F,      # 总览
    "calendar": 0xE787,  # 周报
    "comment": 0xE90A,   # 问答
    "list": 0xEA37,      # 事件流
    "clock": 0xE917,     # 番茄钟
    "shield": 0xEA18,    # 审计日志
    "settings": 0xE713,  # 数据源设置
    "refresh": 0xE895,   # 刷新 / 同步
    "add": 0xE710,       # 添加
    "check": 0xE73E,     # 勾选 / 完成
    "delete": 0xE74D,    # 删除
    "close": 0xE711,     # 关闭
    "play": 0xE768,      # 开始
    "search": 0xE721,    # 搜索
    "edit": 0xE70F,      # 编辑
    "save": 0xE74E,      # 保存
    "info": 0xE946,      # 信息
    "warning": 0xE7BA,   # 警告
    "document": 0xE8A5,  # 文档
    "folder": 0xE8B7,    # 文件夹
    "send": 0xE724,      # 发送
    "eye": 0xE7B3,       # 显示 / 查看
    "adapter": 0xEA18,   # 适配器（盾牌）
    "queue": 0xE946,     # 队列（信息）
    "disk": 0xE74E,      # 磁盘（保存/存储）
    "streak": 0xE73E,    # 连续（勾选）
    "total": 0xE710,     # 累计（添加）
    "report": 0xE787,    # 周报（日历）
    "qa": 0xE90A,        # 问答（评论）
    "pomodoro": 0xE917,  # 番茄钟（时钟）
    "audit": 0xEA18,     # 审计（盾牌）
    "sources": 0xE713,   # 数据源（设置）
    "todo": 0xE70F,      # 待办（编辑）
    "review": 0xE8A5,    # 复习（文档）
    "chat": 0xE90A,      # 聊天（评论）
    "knowledge": 0xE8A5, # 知识点（文档）
    "extract": 0xE710,   # 提取（添加）
}


# ---------------------------------------------------------------------------
# 单一定暗色太空主题 token（Orbis）
# ---------------------------------------------------------------------------
_SPACE = {
    "BG": "#010828",            # 页面底（深空海军蓝）
    "SURFACE": "#00081F",       # 侧边栏（更深黑）
    "CARD": "#0A1640",          # 玻璃卡底（视频靛蓝 #021F5B 提亮）
    "CARD_HOVER": "#1A2A5C",    # 玻璃卡悬浮
    "CARD_ACTIVE": "#22346B",   # 玻璃卡按下 / 选中
    "BORDER": "#22315F",        # 玻璃渐变边缘（暗侧）
    "INK": "#EFF4FF",           # 主文字（cream）
    "INK2": "#A9B4D0",          # 次要文字
    "MUTED": "#6A7396",         # 弱化文字
    "ACCENT": "#6FFF00",        # 霓虹绿主色
    "ACCENT_HOVER": "#8FFF33",  # 霓虹绿悬浮
    "ACCENT_DEEP": "#57CC00",   # 霓虹绿按压
    "ON_ACCENT": "#010828",     # 霓虹绿底上的文字（深空蓝）
    "OK": "#30D158",
    "WARN": "#FF9F0A",
    "ERR": "#FF453A",
    "INFO": "#787EB8",
    "PURPLE": "#787EB8",
    "PURPLE_END": "#B77081",
    "PURPLE_DEEP": "#5A3E6E",
    "ON_PURPLE": "#FFFFFF",
    "SOURCE_COLORS": {
        "git": "#254995", "fs": "#30D158", "ics": "#FF9F0A",
        "imap": "#FF453A", "clipboard": "#B77081", "manual": "#64D2FF",
    },
    "HEAT_LEVELS": ("#0A1030", "#253A6E", "#4A3E6E", "#7A4A6E", "#B77081"),  # 深蓝→蓝紫→玫紫→玫粉（视频同源）
    "BUBBLE_PET": "#141E4E",    # 聊天：对方（深玻璃）
    "BUBBLE_USER": "#B77081",   # 聊天：自己（视频玫粉）
    "GRADE_AGAIN": "#FF453A",
    "GRADE_HARD": "#FF9F0A",
    "GRADE_GOOD": "#787EB8",
    "GRADE_EASY": "#30D158",
    "PHASE_FOCUS": "#B77081",   # 番茄：专注（视频玫粉）
    "PHASE_SHORT": "#30D158",
    "PHASE_LONG": "#787EB8",
    "ALT_ROW": "#0C1438",       # 表格斑马纹
    "EMPH_TINT": "#2A1A24",     # 玫粉 15% 淡底
    "GLASS_HI": "#4A5CA8",      # 玻璃顶部高光
    "GLASS_EDGE": "#22315F",    # 玻璃渐变边缘描边
    "FOCUS_RING": "#B77081",    # 键盘焦点描边
    # 半透明玻璃（QSS 用 rgba 字符串；QPainter 用下方 GLASS_RGB/ALPHA 整数形式）
    "CARD_GLASS": "rgba(10, 22, 64, 205)",          # 玻璃卡底（透出背景视频）
    "CARD_HOVER_GLASS": "rgba(26, 42, 92, 215)",    # 玻璃卡悬浮
    "CARD_ACTIVE_GLASS": "rgba(34, 52, 107, 225)",  # 玻璃卡选中
    "INPUT_GLASS": "rgba(10, 22, 64, 205)",         # 输入框玻璃
    "TABLE_GLASS": "rgba(10, 22, 64, 195)",         # 表格玻璃
    "GLASS_RGB": (10, 22, 64),          # QPainter 玻璃底色（RGB 整数）
    "GLASS_HOVER_RGB": (26, 42, 92),    # QPainter 玻璃悬浮色
    "GLASS_ALPHA": 205,
    "GLASS_HOVER_ALPHA": 215,
    # 一比一复刻预览稿（回顾Agent_UI重构预览.html）新增 token
    "BG_GRAD_A": "#021F5B",             # 总览背景线性渐变起（视频靛蓝）
    "BG_GRAD_B": "#010828",             # 总览背景线性渐变止（深空海军蓝）
    "BG_RADIAL_TOP": (11, 42, 107),     # 总览右上径向辉光（#0B2A6B，RGB）
    "BG_RADIAL_BL": (42, 18, 48),       # 总览左下玫粉辉光（#2A1230，RGB）
    "CARD_HI_RGB": (255, 255, 255),     # 卡片顶部高光线（白）
    "CARD_HI_ALPHA": 41,                # 高光线 alpha（≈ white 16%）
    "BORDER_HOVER": "#2E4180",          # 卡片 hover 边框（预览稿 #2E4180）
    "NAV_SELECT_RGB": (111, 255, 0),    # 导航选中霓虹绿（渐变底/指示条）
    "NAV_HOVER_RGB": (255, 255, 255),   # 导航 hover 淡白底
}

_THEMES: dict[str, dict] = {"space": _SPACE}
_current = "space"


def current_theme() -> str:
    """当前主题名（恒为 "space"）。"""
    return _current


def set_theme(name: str | None = None) -> None:
    """兼容旧接口：恒设为 space 主题。"""
    global _current
    _current = "space"
    globals().update(_SPACE)


def detect_system_theme() -> str:
    """兼容旧接口：恒返回 "space"（固定暗色太空主题）。"""
    return "space"


# ---------------------------------------------------------------------------
# 间距 / 尺寸
# ---------------------------------------------------------------------------
PAD_PAGE = 24            # 页面边距
PAD_CARD = 16            # 卡片内边距
GAP_CARD = 16            # 卡片间距
RADIUS = 16              # 视觉元素圆角
RADIUS_CARD = 20         # 大卡片圆角
NAV_WIDTH = 160          # 侧边栏宽
NAV_ITEM_H = 38          # 导航项高
STAT_H = 108             # 统计卡高

# 动效 token：hover/交互平滑过渡
MOTION_MS = 180          # 过渡时长
MOTION_STEPS = 8         # 颜色渐变插值步数

APP_NAME = "回顾 Agent"
APP_VERSION = "v1.1.0-orbis"


# ---------------------------------------------------------------------------
# 全局 QSS（由 token 生成）
# ---------------------------------------------------------------------------
def build_qss() -> str:
    """生成全局 QSS 样式表（深空太空主题）。"""
    return f"""
    QWidget {{
        color: {INK};
        font-family: "{F_SANS}";
        font-size: 10pt;
    }}
    /* 注意：全局 QWidget 不再设 background-color —— 页面背景由 Dashboard 全屏
       视频提供，卡片（BentoCard/StatCard/输入框等）各自绘制不透明底。若保留全局
       深蓝背景会把所有页面染成纯色、盖住视频，导致全屏视频背景失效。 */
    QToolTip {{
        background-color: {CARD_ACTIVE};
        color: {INK};
        border: 1px solid {BORDER};
        border-radius: 4px;
        padding: 4px 8px;
    }}
    QScrollBar:vertical {{
        background: {BG};
        width: 10px;
        margin: 0;
    }}
    QScrollBar::handle:vertical {{
        background: {CARD_ACTIVE};
        border-radius: 5px;
        min-height: 24px;
    }}
    QScrollBar::handle:vertical:hover {{ background: {BORDER}; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
    QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox {{
        background-color: {INPUT_GLASS};
        color: {INK};
        border: 1px solid {BORDER};
        border-radius: 6px;
        padding: 4px 8px;
        selection-background-color: {ACCENT};
        selection-color: {ON_ACCENT};
    }}
    QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QSpinBox:focus {{
        border: 1px solid {ACCENT};
    }}
    QComboBox {{
        background-color: {INPUT_GLASS};
        color: {INK};
        border: 1px solid {BORDER};
        border-radius: 6px;
        padding: 4px 8px;
    }}
    QComboBox::drop-down {{ border: none; width: 20px; }}
    QComboBox QAbstractItemView {{
        background-color: {CARD_ACTIVE};
        color: {INK};
        border: 1px solid {BORDER};
        selection-background-color: {ACCENT};
        selection-color: {ON_ACCENT};
    }}
    QListWidget, QTreeWidget, QTableWidget {{
        background-color: {TABLE_GLASS};
        color: {INK};
        border: 1px solid {BORDER};
        border-radius: 6px;
        alternate-background-color: {ALT_ROW};
    }}
    QListWidget::item:selected, QTreeWidget::item:selected, QTableWidget::item:selected {{
        background-color: {CARD_ACTIVE};
    }}
    QMenu {{
        background-color: {SURFACE};
        color: {INK};
        border: 1px solid {BORDER};
        border-radius: 6px;
    }}
    QMenu::item {{ padding: 6px 22px; }}
    QMenu::item:selected {{ background-color: {CARD_ACTIVE}; }}
    QMenu::separator {{ height: 1px; background: {BORDER}; margin: 4px 8px; }}
    QCheckBox {{ spacing: 6px; }}
    QCheckBox::indicator {{
        width: 16px; height: 16px;
        border: 1px solid {BORDER};
        border-radius: 4px;
        background: {INPUT_GLASS};
    }}
    QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; }}
    QHeaderView::section {{
        background-color: {SURFACE};
        color: {INK2};
        border: none;
        border-bottom: 1px solid {BORDER};
        padding: 6px 8px;
        font-weight: bold;
    }}
    QTableWidget {{ gridline-color: {BORDER}; }}
    QMessageBox {{ background-color: {SURFACE}; }}
    """


# ---------------------------------------------------------------------------
# 顶层窗口 Acrylic 磨砂（pywinstyles；缺失/失败静默降级为纯色）
# ---------------------------------------------------------------------------
def apply_window_material(win: Any, *, acrylic: bool = True) -> None:
    """给顶层 QWidget/QMainWindow 应用深空蓝标题栏 + 边框色，与 UI 统一色调。

    注意：不再调用 pywinstyles.apply_style("acrylic")。Acrylic 磨砂会把窗口
    （尤其顶部标题栏区域）做成半透明，透过窗口看到桌面，在界面顶部形成一条
    「透明方框」，主人已要求去掉。改为纯色窗口（背景由 Dashboard 全屏视频提供），
    标题栏用 DWMWA_CAPTION_COLOR 染成深空蓝（CARD），标题文字奶油白，边框深蓝，
    与页面卡片玻璃色调一致。acrylic 参数保留仅为兼容旧调用，恒不应用磨砂。
    """
    try:
        import pywinstyles
    except Exception:  # noqa: BLE001
        return
    try:
        # 标题栏统一为深空蓝玻璃色调：背景 CARD 深蓝 + 标题奶油白 + 深蓝描边
        pywinstyles.change_title_color(win, _SPACE["INK"])      # 标题文字（cream）
        pywinstyles.change_border_color(win, _SPACE["BORDER"])  # 窗口描边（深蓝）
        pywinstyles.change_header_color(win, _SPACE["CARD"])    # 标题栏背景（Win11 DWMWA_CAPTION_COLOR）
    except Exception:  # noqa: BLE001
        pass


# 默认激活 space 主题（main.py 启动后调用 ensure_fonts 刷新字体）
set_theme("space")
