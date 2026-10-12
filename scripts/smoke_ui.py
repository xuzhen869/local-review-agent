"""UI 冒烟渲染：依次打开 7 页并截图保存到 assets/screenshots/（验收用）。"""
from __future__ import annotations

import os
import sys
import time
import tkinter as tk

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PIL import ImageGrab  # noqa: E402

from pet import theme as T  # noqa: E402
from pet.api_client import ApiClient  # noqa: E402
from pet.dashboard import Dashboard  # noqa: E402

OUT = os.path.join(ROOT, "assets", "screenshots")
os.makedirs(OUT, exist_ok=True)

T.set_theme("space")
root = tk.Tk()
root.withdraw()
T.ensure_fonts()

dash = Dashboard(root, ApiClient())
dash.deiconify()
dash.attributes("-topmost", True)
dash.update_idletasks()

PAGES = ["overview", "report", "qa", "events", "pomodoro", "audit", "settings"]
for key in PAGES:
    dash.show(key)
    for _ in range(8):
        root.update()
        time.sleep(0.05)
    dash.update_idletasks()
    x, y = dash.winfo_rootx(), dash.winfo_rooty()
    w, h = dash.winfo_width(), dash.winfo_height()
    if w > 50 and h > 50:
        img = ImageGrab.grab((x, y, x + w, y + h))
        img.save(os.path.join(OUT, f"{key}.png"))
        print(f"saved {key}.png {w}x{h}")
    time.sleep(0.3)

dash.destroy()
root.destroy()
print("done ->", OUT)
