"""下载 Orbis 太空风背景视频到 assets/videos/（按 manifest.json）。

用法：
  backend/.venv/Scripts/python.exe scripts/download_videos.py          # 全部
  backend/.venv/Scripts/python.exe scripts/download_videos.py hero cta  # 指定角色

特性：断点续传（curl -C -）、自动重试、已存在且大小>0 跳过。
网络说明：CloudFront 在部分网络被封锁；可在代理/VPN 环境下运行，或将
手动下载的文件按 manifest 中的 file 名放入 assets/videos/（程序自动识别）。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_MANIFEST = os.path.join(_ROOT, "assets", "videos", "manifest.json")
_VIDEO_DIR = os.path.join(_ROOT, "assets", "videos")


def _load() -> list[dict]:
    with open(_MANIFEST, "r", encoding="utf-8") as f:
        return json.load(f)["videos"]


def _download(url: str, dest: str) -> bool:
    if os.path.exists(dest) and os.path.getsize(dest) > 0:
        print(f"[skip] 已存在: {os.path.basename(dest)}")
        return True
    for attempt in range(1, 4):
        print(f"[try {attempt}] {os.path.basename(dest)}")
        r = subprocess.run(
            ["curl", "-sS", "-C", "-", "--connect-timeout", "20",
             "--retry", "2", "-o", dest, url],
            capture_output=True, text=True)
        if r.returncode == 0 and os.path.exists(dest) and os.path.getsize(dest) > 0:
            print(f"[ok] {os.path.basename(dest)}  {os.path.getsize(dest)} B")
            return True
        print(f"    curl rc={r.returncode} {r.stderr.strip()[-160:]}")
    return False


def main() -> int:
    want = set(sys.argv[1:])
    videos = _load()
    ok = True
    for v in videos:
        if want and v["role"] not in want:
            continue
        ok = _download(v["url"], os.path.join(_VIDEO_DIR, v["file"])) and ok
    print("\n完成。" if ok else "\n部分失败，可重跑续传。")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
