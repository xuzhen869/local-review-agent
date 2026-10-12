"""Orbis 太空风：视频背景/缩略图播放组件（PySide6 版）。

播放栈（用户已确认优先 QMediaPlayer）：
1. **QMediaPlayer + QVideoWidget**：Qt 原生解码，省资源；失败/缺失自动降级；
2. **opencv fallback**：cv2 逐帧解码 → QImage → 绘制（保留原 tkinter 算法）；
3. **纯色黑底 + idle 星云图**：两者都不可用时占位。

视频与覆盖层分两层：
- QVideoWidget / cv2 帧 / 黑底画在底层（VideoSurface.paintEvent）；
- 覆盖层文字画在独立透明 _OverlayLayer（置顶），避免被 QVideoWidget 盖住。

与原 tkinter media.py 同名 API：play / stop / set_active / set_overlay / has_video。
cover 模式等比裁切填满（object-cover 语义）。任何异常静默降级，绝不抛向调用方。
"""
from __future__ import annotations

import os
import sys

from PySide6.QtCore import Qt, QUrl, QTimer, QPointF, QRectF
from PySide6.QtGui import QImage, QPainter, QColor, QPixmap, QPainterPath, QRegion
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import QWidget

from ..widgets import theme as T

try:
    import cv2
    import numpy as np
    _CV_OK = True
except Exception:  # noqa: BLE001
    cv2 = None
    _CV_OK = False


def _asset_base() -> list[str]:
    """候选资源根目录：PyInstaller _MEIPASS → 源码项目根 → exe 旁。"""
    cands: list[str] = []
    if getattr(sys, "_MEIPASS", None):
        cands.append(sys._MEIPASS)
    cands.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # panels/.. = pet/
    cands.append(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))))  # pet/.. = 项目根
    if getattr(sys, "frozen", False) and sys.executable:
        cands.append(os.path.dirname(sys.executable))
    return cands


def video_path(name: str) -> str:
    """定位 assets/videos/<name>。文件不存在也返回预期路径（VideoSurface 黑底降级）。"""
    for base in _asset_base():
        p = os.path.join(base, "assets", "videos", name)
        if os.path.exists(p):
            return p
    return os.path.join(_asset_base()[0], "assets", "videos", name)


class _OverlayLayer(QWidget):
    """透明覆盖层：绘制 set_overlay 的文字，始终置于视频之上。"""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self._items: list[dict] = []
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, False)
        self.setStyleSheet("background: transparent;")

    def set_items(self, items: list[dict]) -> None:
        self._items = list(items or [])
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        if not self._items:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        for it in self._items:
            p.setPen(QColor(it["fill"]))
            f = it.get("font")
            if isinstance(f, tuple):
                p.setFont(T.font(f))
            elif f is not None:
                p.setFont(f)
            else:
                p.setFont(T.font(T.F_H))
            anchor = it.get("anchor", "nw")
            x, y = it["x"], it["y"]
            if anchor == "center":
                p.drawText(QRectF(0, y - 20, w, 40),
                           Qt.AlignmentFlag.AlignCenter, it["text"])
            elif anchor == "w":
                p.drawText(QPointF(x, y), it["text"])
            else:
                p.drawText(QPointF(x, y), it["text"])
        p.end()


class VideoSurface(QWidget):
    """循环播放 mp4。mode: cover=裁切填满 / fit=等比留边。

    优先 QMediaPlayer（QVideoWidget 底层渲染）；mediaplayer 不可用（如平台缺
    codec）时回退 cv2 逐帧；再不可用则黑底 + 可选 idle 星云图。
    覆盖层文字在独立 _OverlayLayer 上绘制（置于视频之上）。
    """

    def __init__(self, parent: QWidget | None = None, video: str | None = None, *,
                 mode: str = "cover", fps: int = 18,
                 idle: str | None = None,
                 prefer_cv: bool = False) -> None:
        super().__init__(parent)
        self._video = video
        self._mode = mode
        self._fps = max(8, min(30, int(fps)))
        self._idle = idle
        self._overlay: list[dict] = []
        self._using_mp = False
        self._prefer_cv = bool(prefer_cv)
        self._darken = 1.0  # 帧压暗系数（<1 时整体变暗，增强文字对比度）
        self._mp_player: QMediaPlayer | None = None
        self._mp_audio: QAudioOutput | None = None
        self._mp_widget: QVideoWidget | None = None
        self._idle_pix: QPixmap | None = None
        self._cv_cap = None
        self._cv_timer: QTimer | None = None
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self.setStyleSheet("background-color: #000820;")
        self._build_mp()
        # 覆盖层置于视频之上（后创建默认在顶层；_open 时再确保 raise）
        self._overlay_layer = _OverlayLayer(self)
        self._layout_children()
        self._open()
        self._overlay_layer.raise_()

    # ------------------------------------------------------------------
    # 播放栈初始化
    # ------------------------------------------------------------------
    def _build_mp(self) -> None:
        """构造 QMediaPlayer + QVideoWidget（隐藏，实际用 cv2 或 idle 时关闭）。"""
        try:
            self._mp_audio = QAudioOutput(self)
            self._mp_audio.setVolume(0.0)   # 视频背景静音
            self._mp_player = QMediaPlayer(self)
            self._mp_player.setAudioOutput(self._mp_audio)
            self._mp_widget = QVideoWidget(self)
            self._mp_widget.hide()
            self._mp_widget.setAttribute(
                Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            self._mp_player.setVideoOutput(self._mp_widget)
            self._mp_player.mediaStatusChanged.connect(self._on_mp_status)
        except Exception:  # noqa: BLE001
            self._mp_player = None
            self._mp_widget = None

    def _open(self) -> None:
        """尝试打开视频；失败则黑底 + 可选 idle 星云。

        prefer_cv=True 时跳过 QMediaPlayer/QVideoWidget（原生窗口会盖住普通
        QWidget 的覆盖文字/控件），强制走 cv2 帧绘制路径，保证 overlay 与上层
        控件始终可见。
        """
        self._close_cv()
        if (not self._prefer_cv and self._mp_player is not None
                and self._video and os.path.exists(self._video)):
            # 优先 QMediaPlayer
            self._using_mp = True
            self._mp_widget.show()
            self._mp_player.setSource(QUrl.fromLocalFile(self._video))
            self._mp_player.play()
            self._overlay_layer.raise_()
            return
        # 降级：cv2 或 idle
        self._using_mp = False
        if self._mp_widget is not None:
            self._mp_widget.hide()
        if _CV_OK and self._video and os.path.exists(self._video):
            try:
                cap = cv2.VideoCapture(self._video)
                if cap is not None and cap.isOpened():
                    self._cv_cap = cap
                    self._start_cv()
                    return
                if cap is not None:
                    cap.release()
            except Exception:  # noqa: BLE001
                self._cv_cap = None
        self._draw_idle()

    def _close_cv(self) -> None:
        if self._cv_timer is not None:
            self._cv_timer.stop()
            self._cv_timer = None
        if self._cv_cap is not None:
            try:
                self._cv_cap.release()
            except Exception:  # noqa: BLE001
                pass
            self._cv_cap = None

    # ------------------------------------------------------------------
    # QMediaPlayer 状态
    # ------------------------------------------------------------------
    def _on_mp_status(self, status: QMediaPlayer.MediaStatus) -> None:
        if status in (QMediaPlayer.MediaStatus.EndOfMedia,
                      QMediaPlayer.MediaStatus.InvalidMedia):
            if self._using_mp:
                if status == QMediaPlayer.MediaStatus.EndOfMedia:
                    self._mp_player.setPosition(0)
                    self._mp_player.play()
                else:
                    # InvalidMedia：codec 缺失 → 降级 cv2
                    self._fallback_cv()

    def _fallback_cv(self) -> None:
        self._using_mp = False
        if self._mp_player is not None:
            self._mp_player.stop()
        if self._mp_widget is not None:
            self._mp_widget.hide()
        if _CV_OK and self._video and os.path.exists(self._video):
            try:
                cap = cv2.VideoCapture(self._video)
                if cap is not None and cap.isOpened():
                    self._cv_cap = cap
                    self._start_cv()
                    return
            except Exception:  # noqa: BLE001
                self._cv_cap = None
        self._draw_idle()

    # ------------------------------------------------------------------
    # 覆盖层 / 压暗
    # ------------------------------------------------------------------
    def set_darken(self, factor: float) -> None:
        """帧级压暗：factor<1 时整体变暗（如 0.5=半亮），用于背景视频保证文字可读。"""
        self._darken = max(0.3, min(1.0, float(factor)))

    # ------------------------------------------------------------------
    # 圆角（缩略图卡片用）：QPainterPath → QRegion mask，resize 时重算
    # ------------------------------------------------------------------
    def set_radius(self, radius: int) -> None:
        """设置圆角（px）。>0 时按圆角裁剪本控件及子控件绘制区域。"""
        self._radius = max(0, int(radius))
        self._apply_mask()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._apply_mask()

    def _apply_mask(self) -> None:
        if self._radius > 0 and self.width() > 0 and self.height() > 0:
            path = QPainterPath()
            path.addRoundedRect(QRectF(0.5, 0.5, self.width() - 1,
                                       self.height() - 1),
                                self._radius, self._radius)
            self.setMask(QRegion(path.toFillPolygon().toPolygon()))
        else:
            self.clearMask()

    def set_overlay(self, items: list[dict]) -> None:
        """覆盖层文字（标题/点缀），始终绘制在视频或黑底之上。

        items: [{text, x, y, font, fill, anchor?}]；anchor 默认 nw。
        """
        self._overlay = list(items or [])
        self._overlay_layer.set_items(self._overlay)

    @property
    def has_video(self) -> bool:
        return self._using_mp or self._cv_cap is not None

    # ------------------------------------------------------------------
    # 播放控制
    # ------------------------------------------------------------------
    def play(self) -> None:
        if self._using_mp and self._mp_player is not None:
            self._mp_player.play()
        elif self._cv_cap is not None and self._cv_timer is None:
            self._start_cv()

    def stop(self) -> None:
        if self._using_mp and self._mp_player is not None:
            self._mp_player.pause()
            # 关键：隐藏 QVideoWidget 原生窗口，否则切页后 native 窗口残留会
            # 覆盖在其它 QWidget 之上（表现为导航/内容"消失"）。
            if self._mp_widget is not None:
                self._mp_widget.hide()
        else:
            self._close_cv()
            self._draw_idle()

    def set_active(self, active: bool) -> None:
        """页面可见性控制：可见→播放，隐藏→停止（省资源）。"""
        if active:
            self.play()
        else:
            self.stop()

    # ------------------------------------------------------------------
    # cv2 fallback 帧循环
    # ------------------------------------------------------------------
    def _start_cv(self) -> None:
        self._cv_timer = QTimer(self)
        self._cv_timer.setInterval(max(20, int(1000 / self._fps)))
        self._cv_timer.timeout.connect(self._cv_tick)
        self._cv_timer.start()

    def _cv_tick(self) -> None:
        if self._cv_cap is None:
            return
        try:
            ret, frame = self._cv_cap.read()
            if not ret:
                self._cv_cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                ret, frame = self._cv_cap.read()
            if not ret:
                self.stop()
                return
            w, h = self.width(), self.height()
            if w <= 1 or h <= 1:
                w, h = 1280, 720
            frame = _resize_cover(frame, w, h)
            if self._darken < 1.0:
                frame = (frame.astype(np.uint16) * self._darken).astype(np.uint8)
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            qimg = QImage(rgb.data, rgb.shape[1], rgb.shape[0],
                          rgb.shape[2] * rgb.shape[1], QImage.Format.Format_RGB888)
            self._cv_pix = QPixmap.fromImage(qimg.copy())
            self.update()
        except Exception:  # noqa: BLE001
            self._close_cv()
            self._draw_idle()

    # ------------------------------------------------------------------
    # 绘制 / 几何
    # ------------------------------------------------------------------
    def _layout_children(self) -> None:
        """QVideoWidget / overlay 铺满自身（子部件无布局管理，手动同步几何）。"""
        r = self.rect()
        if self._mp_widget is not None:
            self._mp_widget.setGeometry(r)
        self._overlay_layer.setGeometry(r)

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        w, h = self.width(), self.height()
        # 视频：mp 时 QVideoWidget 已在底层渲染；cv2 则贴 QPixmap
        if not self._using_mp:
            if getattr(self, "_cv_pix", None) is not None:
                p.drawPixmap(0, 0, self._cv_pix)
            else:
                p.fillRect(0, 0, w, h, QColor("#000820"))
                if self._idle_pix is not None:
                    p.drawPixmap(0, 0, self._idle_pix)
        p.end()

    def resizeEvent(self, event) -> None:  # noqa: N802
        self._layout_children()
        if not self.has_video and self._idle_pix is not None:
            self._draw_idle()
        super().resizeEvent(event)

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._layout_children()
        self._overlay_layer.raise_()

    # ------------------------------------------------------------------
    def _draw_idle(self) -> None:
        """无视频可用：黑底（预留空间），若给 idle 星云图则显示。"""
        self._idle_pix = None
        if self._idle and os.path.exists(self._idle):
            try:
                img = _pillow_cover_path(self._idle, max(1, self.width()),
                                         max(1, self.height()))
                self._idle_pix = QPixmap.fromImage(img)
            except Exception:  # noqa: BLE001
                self._idle_pix = None
        self.update()

    def destroy(self) -> None:
        self._close_cv()
        if self._mp_player is not None:
            self._mp_player.stop()
        super().destroy()


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------
def _resize_cover(frame, w: int, h: int):
    """等比放大并居中裁切，铺满目标区域（object-cover）。"""
    fh, fw = frame.shape[:2]
    if fh <= 0 or fw <= 0 or w <= 0 or h <= 0:
        return frame
    scale = max(w / fw, h / fh)
    nw, nh = max(1, int(round(fw * scale))), max(1, int(round(fh * scale)))
    try:
        frame = cv2.resize(frame, (nw, nh), interpolation=cv2.INTER_AREA)
    except cv2.error:  # noqa: BLE001
        return frame
    x0 = max(0, (nw - w) // 2)
    y0 = max(0, (nh - h) // 2)
    return frame[y0:y0 + h, x0:x0 + w]


def _pillow_cover_path(path: str, w: int, h: int) -> QImage:
    """idle 星云图：读图 → 等比裁切 → QImage。"""
    from PIL import Image
    img = Image.open(path)
    img = _pillow_cover(img, w, h)
    if img.mode != "RGB":
        img = img.convert("RGB")
    return QImage(img.tobytes(), img.width, img.height,
                  img.width * 3, QImage.Format.Format_RGB888)


def _pillow_cover(img, w: int, h: int):
    """PIL 等比裁切填满（用于 idle 星云图）。"""
    iw, ih = img.size
    scale = max(w / iw, h / ih)
    nw, nh = max(1, int(round(iw * scale))), max(1, int(round(ih * scale)))
    img = img.resize((nw, nh), Image.LANCZOS)
    x0, y0 = (nw - w) // 2, (nh - h) // 2
    return img.crop((x0, y0, x0 + w, y0 + h))
