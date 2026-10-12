"""Orbis 太空风：视频背景/缩略图播放组件 VideoSurface。

cv2.VideoCapture 解码 mp4 + Pillow→PhotoImage 在 Canvas 上逐帧显示，循环播放。
- 视频文件缺失 / cv2 不可用 → 纯黑占位（预留视频空间），可选 idle 星云图。
- 帧率上限（背景 18fps / 缩略 15fps），仅可见页面播放（set_active），切页即停。
- cover 模式等比裁切填满（object-cover 语义）；任何异常静默降级为黑，绝不抛向调用方。
"""
from __future__ import annotations

import os
import sys
import tkinter as tk

try:
    import cv2
    import numpy as np
    from PIL import Image, ImageTk
    _CV_OK = True
except Exception:  # noqa: BLE001
    _CV_OK = False


def _asset_base() -> list[str]:
    """候选资源根目录：PyInstaller _MEIPASS → 源码项目根 → exe 旁。"""
    cands: list[str] = []
    if getattr(sys, "_MEIPASS", None):
        cands.append(sys._MEIPASS)
    cands.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # pet/..
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


def _resize_cover(frame, w: int, h: int) -> np.ndarray:
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


class VideoSurface(tk.Canvas):
    """循环播放 mp4 的 Canvas。mode: cover=裁切填满 / fit=等比留边。"""

    def __init__(self, parent: tk.Widget, video: str | None = None, *,
                 mode: str = "cover", fps: int = 18,
                 idle: str | None = None, **kw) -> None:
        kw.setdefault("highlightthickness", 0)
        # 深蓝黑（非纯黑）：pywinstyles acrylic 会把纯黑像素当透明遮罩磨砂化，
        # 导致视频帧被 DWM 混合掉；用非纯黑底色可正常显示视频。
        kw.setdefault("bg", "#000820")
        super().__init__(parent, **kw)
        self._video = video
        self._mode = mode
        self._fps = max(8, min(30, int(fps)))
        self._idle = idle
        self._cap = None
        self._running = False
        self._after_id: str | None = None
        self._photo = None
        self._frames = 0
        self._diag_saved = 0  # 临时诊断：已导出帧图数量（修好后清理）
        self._image_id = None
        self._idle_photo = None
        self._overlay: list[dict] = []
        self._darken = 1.0  # 帧压暗系数（<1 时整体变暗，增强文字对比度）
        self.bind("<Configure>", self._on_configure)
        self._open()

    # -- 打开 / 释放 -----------------------------------------------------
    def _write_diag(self, data: dict) -> None:
        """临时诊断：VideoSurface 打开状态写 exe 旁日志（排查视频黑底用，修好后清理）。"""
        try:
            import json as _json
            base = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else "."
            with open(os.path.join(base, "reviewagent_video_diag.log"), "a", encoding="utf-8") as f:
                f.write(_json.dumps(data, ensure_ascii=False) + "\n")
        except Exception:  # noqa: BLE001
            pass

    def _open(self) -> None:
        """尝试打开视频；失败则保持黑底 + 可选 idle 星云。"""
        if self._cap is not None:
            self._cap.release()
            self._cap = None
        diag = {"cv_ok": _CV_OK, "video": self._video,
                "exists": bool(self._video and os.path.exists(self._video))}
        if not _CV_OK or not self._video or not os.path.exists(self._video):
            diag["result"] = "skip"
            self._write_diag(diag)
            self._draw_idle()
            return
        try:
            cap = cv2.VideoCapture(self._video)
            if cap is not None and cap.isOpened():
                self._cap = cap
                diag["result"] = "opened"
                diag["size"] = (int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
                                int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)))
            else:
                if cap is not None:
                    cap.release()
                diag["result"] = "open_failed"
                self._draw_idle()
        except Exception as e:  # noqa: BLE001
            diag["result"] = f"exc:{type(e).__name__}:{e}"
            self._draw_idle()
        self._write_diag(diag)

    def _draw_idle(self) -> None:
        """无视频可用：黑底（预留空间），若给 idle 星云图则显示。"""
        self._write_diag({"idle_drawn": True, "video": self._video,
                          "cap": self._cap is not None, "cv_ok": _CV_OK})
        self.delete("all")
        self._photo = None
        if self._idle and os.path.exists(self._idle):
            try:
                w, h = self.winfo_width(), self.winfo_height()
                if w > 1 and h > 1:
                    img = Image.open(self._idle)
                    img = _pillow_cover(img, w, h)
                    self._idle_photo = ImageTk.PhotoImage(img)
                    self._image_id = self.create_image(
                        w // 2, h // 2, image=self._idle_photo)
            except Exception:  # noqa: BLE001
                self._idle_photo = None
        else:
            self._idle_photo = None
        self._draw_overlay()

    def set_darken(self, factor: float) -> None:
        """帧级压暗：factor<1 时整体变暗（如 0.5=半亮），用于背景视频保证文字可读。"""
        self._darken = max(0.3, min(1.0, float(factor)))

    def set_overlay(self, items: list[dict]) -> None:
        """覆盖层文字（标题/点缀），始终绘制在视频或黑底之上。

        items: [{text, x, y, font, fill, anchor?}]；anchor 默认 nw。
        用于在视频背景上叠大标题，避免 tkinter 不透明 Label 遮挡视频。
        """
        self._overlay = list(items or [])
        self._draw_overlay()

    def _draw_overlay(self) -> None:
        if not self._overlay:
            return
        self.delete("overlay")
        for it in self._overlay:
            self.create_text(it["x"], it["y"], text=it["text"], font=it["font"],
                             fill=it["fill"],
                             anchor=it.get("anchor", "nw"), tags="overlay")

    @property
    def has_video(self) -> bool:
        return self._cap is not None

    # -- 播放控制 ---------------------------------------------------------
    def play(self) -> None:
        if self._cap is None or self._running:
            return
        self._running = True
        self._write_diag({"play": True, "video": self._video,
                          "cap": self._cap is not None, "cv_ok": _CV_OK})
        self._tick()

    def stop(self) -> None:
        self._running = False
        if self._after_id is not None:
            try:
                self.after_cancel(self._after_id)
            except tk.TclError:
                pass
            self._after_id = None
        self._draw_idle()

    def set_active(self, active: bool) -> None:
        """页面可见性控制：可见→播放，隐藏→停止（省资源）。"""
        if active:
            self.play()
        else:
            self.stop()

    # -- 帧循环 -----------------------------------------------------------
    def _tick(self) -> None:
        self._after_id = None
        if not self._running or self._cap is None:
            return
        try:
            ret, frame = self._cap.read()
            if not ret:
                self._cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                ret, frame = self._cap.read()
            if not ret:
                self.stop()
                return
            w, h = self.winfo_width(), self.winfo_height()
            if w <= 1 or h <= 1:
                w, h = 1280, 720
            frame = _resize_cover(frame, w, h)
            if self._darken < 1.0:
                frame = (frame.astype(np.uint16) * self._darken).astype(np.uint8)
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            img = Image.fromarray(rgb)
            self._photo = ImageTk.PhotoImage(img)
            if self._image_id is None:
                self._image_id = self.create_image(w // 2, h // 2,
                                                   image=self._photo)
            else:
                self.itemconfigure(self._image_id, image=self._photo)
            self._draw_overlay()
            self._frames += 1
            # 临时诊断：把当前帧导出 PNG 到 exe 旁，确认视频帧真实渲染（修好后清理）
            if self._diag_saved < 2 and self._frames % 5 == 0:
                try:
                    base = os.path.dirname(sys.executable) if getattr(
                        sys, "frozen", False) else "."
                    stem = os.path.splitext(os.path.basename(
                        self._video or "surface"))[0]
                    out = os.path.join(
                        base, f"video_frame_{stem}_{self._diag_saved}.png")
                    Image.fromarray(rgb).save(out, format="PNG")
                    self._diag_saved += 1
                except Exception:  # noqa: BLE001
                    pass
        except Exception as e:  # noqa: BLE001
            self._write_diag({"tick_error": f"{type(e).__name__}: {e}",
                              "frames": getattr(self, "_frames", 0),
                              "canvas_size": (self.winfo_width(),
                                              self.winfo_height())})
            self.stop()
            return
        self._after_id = self.after(max(20, int(1000 / self._fps)), self._tick)

    def _on_configure(self, _e) -> None:
        # 静止 idle 图随尺寸重绘；播放中下一帧自会适配
        if self._cap is None and self._idle_photo is not None:
            self._draw_idle()

    def destroy(self) -> None:
        self._running = False
        if self._after_id is not None:
            try:
                self.after_cancel(self._after_id)
            except tk.TclError:
                pass
            self._after_id = None
        if self._cap is not None:
            try:
                self._cap.release()
            except Exception:  # noqa: BLE001
                pass
            self._cap = None
        super().destroy()


def _pillow_cover(img: Image.Image, w: int, h: int) -> Image.Image:
    """PIL 等比裁切填满（用于 idle 星云图）。"""
    iw, ih = img.size
    scale = max(w / iw, h / ih)
    nw, nh = max(1, int(round(iw * scale))), max(1, int(round(ih * scale)))
    img = img.resize((nw, nh), Image.LANCZOS)
    x0, y0 = (nw - w) // 2, (nh - h) // 2
    return img.crop((x0, y0, x0 + w, y0 + h))
