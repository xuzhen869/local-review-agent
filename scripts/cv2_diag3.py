"""临时诊断3：frozen 下真实 pet.media 模块的 _CV_OK / video_path / cv2 打开（用后清理）。"""
import json
import os
import sys

out = {}
try:
    from pet import media
    out["media_import"] = "ok"
    out["CV_OK"] = media._CV_OK
    out["cap"] = getattr(media, "VideoSurface", None) is not None
    p = media.video_path("hero.mp4")
    out["video_path"] = p
    out["video_exists"] = os.path.exists(p)
    try:
        import cv2
        out["cv2_import"] = "ok"
        cap = cv2.VideoCapture(p)
        opened = cap is not None and cap.isOpened()
        out["opened"] = bool(opened)
        if opened:
            ret, fr = cap.read()
            out["first_frame"] = bool(ret)
            cap.release()
    except Exception as e:  # noqa: BLE001
        out["cv2_error"] = f"{type(e).__name__}: {e}"
except Exception as e:  # noqa: BLE001
    out["error"] = f"{type(e).__name__}: {e}"

dest = os.path.join(os.path.dirname(sys.executable), "cv2_diag3.json")
with open(dest, "w", encoding="utf-8") as f:
    json.dump(out, f, indent=2)
print(json.dumps(out, ensure_ascii=False))
