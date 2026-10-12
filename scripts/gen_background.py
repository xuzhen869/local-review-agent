"""生成 Orbis 太空风背景图 assets/background.png。

深空蓝底（#010828）渐变 + 星云柔光 + 随机星点 + 低噪点，预压暗以贴合页面。
产物作为各页面背景，并在视频缺失时作为降级画面。
"""
from __future__ import annotations

import os
import random
import sys

from PIL import Image, ImageDraw, ImageFilter

W, H = 1920, 1080
BASE_TOP = (1, 8, 40)        # #010828
BASE_BOT = (2, 14, 64)       # 底部略提亮，模拟深空透视
NEBULA = [
    ((0.18, 0.28), (26, 30, 90, 60)),   # 左中 蓝紫
    ((0.80, 0.22), (76, 20, 120, 45)),  # 右上 紫
    ((0.55, 0.85), (12, 24, 80, 55)),   # 下中 深蓝
]
STAR_COUNT = 900
NOISE_COUNT = 2600

rng = random.Random(20260806)


def _star_color() -> tuple[int, int, int]:
    r = rng.random()
    if r < 0.06:
        return (111, 255, 0)      # 少量霓虹绿点缀星
    if r < 0.30:
        return (160, 190, 255)    # 蓝白
    return (235, 240, 255)        # 奶油白


def main() -> int:
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    out = os.path.join(root, "assets", "background.png")

    img = Image.new("RGB", (W, H))
    d = ImageDraw.Draw(img)
    for y in range(H):
        t = y / (H - 1)
        row = tuple(round(a + (b - a) * t) for a, b in zip(BASE_TOP, BASE_BOT))
        d.line([(0, y), (W, y)], fill=row)

    # 星云柔光（径向渐变叠加）
    neb = Image.new("RGB", (W, H), (0, 0, 0))
    nd = ImageDraw.Draw(neb)
    for (cx, cy), color in NEBULA:
        x0, y0 = int(cx * W), int(cy * H)
        for i in range(120, 0, -4):
            r = i * 2
            a = int(color[3] * (1 - i / 120))
            nd.ellipse([x0 - r, y0 - r, x0 + r, y0 + r],
                       fill=(color[0], color[1], color[2], a))
    neb = neb.filter(ImageFilter.GaussianBlur(90))
    img = Image.composite(neb, img, neb.convert("L").point(lambda v: 160))

    # 星点（中心光晕 + 内核）
    stars = Image.new("RGB", (W, H), (0, 0, 0))
    sd = ImageDraw.Draw(stars)
    for _ in range(STAR_COUNT):
        x, y = rng.randint(0, W - 1), rng.randint(0, H - 1)
        s = rng.choice((1, 1, 2, 2, 3))
        col = _star_color()
        if s >= 2:
            sd.ellipse([x - s, y - s, x + s, y + s], fill=col)
        else:
            sd.point((x, y), fill=col)
    # 微噪点
    for _ in range(NOISE_COUNT):
        x, y = rng.randint(0, W - 1), rng.randint(0, H - 1)
        v = rng.randint(10, 40)
        stars.putpixel((x, y), (v, v, v + 6))
    stars = stars.filter(ImageFilter.GaussianBlur(0.6))
    img = Image.blend(img, stars, 0.9)

    img.save(out, "PNG")
    print("saved:", out, img.size)
    return 0


if __name__ == "__main__":
    sys.exit(main())
