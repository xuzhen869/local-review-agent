"""临时诊断：验证 PyInstaller frozen 环境下 cv2 能否打开打包的视频（用后清理）。"""
import json
import os
import sys

out = {}
out["frozen"] = getattr(sys, "frozen", False)
out["meipass"] = getattr(sys, "_MEIPASS", None)
out["exe_dir"] = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else os.getcwd()

try:
    import cv2
    out["cv2_import"] = "ok"
    out["cv2_version"] = cv2.__version__
    out["cv2_file"] = cv2.__file__
    base = getattr(sys, "_MEIPASS", None) or os.getcwd()
    vp = os.path.join(base, "assets", "videos", "hero.mp4")
    out["video_path"] = vp
    out["video_exists"] = os.path.exists(vp)
    if os.path.exists(vp):
        cap = cv2.VideoCapture(vp)
        opened = cap is not None and cap.isOpened()
        out["opened"] = bool(opened)
        if opened:
            out["size"] = f"{int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))}x{int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))}"
            ret, fr = cap.read()
            out["first_frame"] = bool(ret)
            cap.release()
except Exception as e:  # noqa: BLE001
    out["error"] = f"{type(e).__name__}: {e}"

dest = os.path.join(out["exe_dir"], "cv2_diag.json")
try:
    with open(dest, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)
    out["result_written"] = dest
except Exception as e:  # noqa: BLE001
    out["result_written_error"] = f"{type(e).__name__}: {e}"
    with open(os.path.join(out["exe_dir"], "cv2_diag.txt"), "w") as f:
        f.write(repr(out))

print(json.dumps(out, ensure_ascii=False, indent=2))
