# -*- coding: utf-8 -*-
"""Q版宇航员·手办风格预览（PIL 程序化建模）。

依据主人提示词逐项还原（2D 卡通版）：
- 泡泡玛特盲盒手办风：大头小身 Q 版比例
- 哑光白色厚衬垫宇航服（缝线）+ 胸前方形控制面板（按钮/旋钮）+ 手臂徽章
- 巨大透明玻璃头盔：虹彩折射描边 + 粉色光泽 + 顶部高光
- 头盔内橙→黄径向渐变圆脸 + 极简黑点眼 + 水平直线嘴
- 云状地面 + 深蓝渐变星空背景
- 电影级布光：顶部体积光 + 右侧轮廓光 + 低饱和色彩
"""
import random

import numpy as np
from PIL import Image, ImageDraw

S = 512
CX, HEAD_Y = 256, 208          # 人物水平中心 / 头部圆心
HELMET_R = 124                 # 玻璃头盔半径（包住脸）
FACE_R = 102                   # 渐变圆脸半径

img = Image.new("RGBA", (S, S))
dr = ImageDraw.Draw(img, "RGBA")

# --------------------------------------------------------------------------
# 1. 深蓝渐变天空（顶部略亮 → 底部深空）
# --------------------------------------------------------------------------
top = np.array([2, 42, 96], np.uint8)
bot = np.array([1, 8, 40], np.uint8)
bg = np.zeros((S, S, 3), np.uint8)
for y in range(S):
    t = y / S
    bg[y, :] = (top * (1 - t) + bot * t).astype(np.uint8)
img.paste(Image.fromarray(np.dstack([bg, np.full((S, S), 255, np.uint8)]), "RGBA"), (0, 0))
dr = ImageDraw.Draw(img, "RGBA")

# --------------------------------------------------------------------------
# 2. 星空（140 细星 + 少量带光晕亮星）
# --------------------------------------------------------------------------
rng = random.Random(7)
for _ in range(140):
    x, y = rng.uniform(8, S - 8), rng.uniform(6, S * 0.72)
    r = rng.uniform(0.5, 1.7)
    a = rng.randint(110, 255)
    dr.ellipse([x - r, y - r, x + r, y + r], fill=(255, 255, 255, a))
for _ in range(8):
    x, y = rng.uniform(10, S - 10), rng.uniform(6, S * 0.68)
    r = rng.uniform(2.5, 5.5)
    dr.ellipse([x - r, y - r, x + r, y + r], fill=(255, 255, 255, 42))
    dr.ellipse([x - r * 0.35, y - r * 0.35, x + r * 0.35, y + r * 0.35],
               fill=(255, 255, 255, 220))

# --------------------------------------------------------------------------
# 3. 云状地面（低饱和白/淡紫，柔软堆叠）
# --------------------------------------------------------------------------
clouds = [(CX, 466, 175, 30), (130, 474, 115, 24), (386, 474, 115, 24),
          (CX - 78, 452, 95, 22), (CX + 82, 452, 95, 22)]
for x, y, w, h in clouds:
    dr.ellipse([x - w, y - h, x + w, y + h], fill=(226, 234, 250, 62))
    dr.ellipse([x - w * 0.62, y - h * 0.72, x + w * 0.62, y + h * 0.5],
               fill=(255, 255, 255, 36))
# 云顶柔光
dr.ellipse([CX - 120, 432, CX + 120, 486], fill=(235, 242, 255, 26))

# --------------------------------------------------------------------------
# 宇航服基础色（哑光白，低饱和）
# --------------------------------------------------------------------------
SUIT = (236, 240, 248, 255)
SUIT_EDGE = (198, 204, 218, 255)
SUIT_SUTURE = (178, 185, 202, 255)

# --------------------------------------------------------------------------
# 4. 腿（Q 版短粗 + 靴子）
# --------------------------------------------------------------------------
for side in (-1, 1):
    lx = CX + side * 42
    dr.rounded_rectangle([lx - 30, 388, lx + 30, 452], radius=22,
                         fill=SUIT, outline=SUIT_EDGE, width=2)
    # 缝线
    dr.line([lx - 12, 396, lx - 12, 444], fill=SUIT_SUTURE, width=2)
    dr.line([lx + 12, 396, lx + 12, 444], fill=SUIT_SUTURE, width=2)
    # 靴子
    dr.rounded_rectangle([lx - 34, 436, lx + 34, 470], radius=15,
                         fill=(222, 228, 240, 255), outline=SUIT_EDGE, width=2)

# --------------------------------------------------------------------------
# 5. 身体（厚实衬垫 + 侧缝线）
# --------------------------------------------------------------------------
dr.rounded_rectangle([CX - 80, 302, CX + 80, 408], radius=36,
                     fill=SUIT, outline=SUIT_EDGE, width=2)
for side in (-1, 1):
    bx = CX + side * 64
    for yy in range(322, 398, 13):
        dr.line([bx, yy, bx, yy + 7], fill=SUIT_SUTURE, width=2)

# --------------------------------------------------------------------------
# 6. 手臂（下垂短粗 + 手套 + 徽章）
# --------------------------------------------------------------------------
for side in (-1, 1):
    ax = CX + side * 90
    dr.rounded_rectangle([ax - 26, 320, ax + 26, 404], radius=20,
                         fill=SUIT, outline=SUIT_EDGE, width=2)
    # 手套
    dr.ellipse([ax - 28, 388, ax + 28, 430], fill=(206, 216, 232, 255),
               outline=(185, 192, 206, 255), width=2)
    # 手臂小徽章（圆形）
    dr.ellipse([ax - 11, 350, ax + 11, 372], fill=(150, 162, 186, 255),
               outline=(128, 140, 166, 255), width=1)
    dr.ellipse([ax - 6, 355, ax + 6, 367], fill=(255, 255, 255, 90))

# --------------------------------------------------------------------------
# 7. 胸前方形控制面板（按钮 + 旋钮）
# --------------------------------------------------------------------------
dr.rounded_rectangle([CX - 56, 324, CX + 56, 388], radius=12,
                     fill=(68, 80, 108, 255), outline=(138, 148, 174, 255), width=2)
# 按钮（红 / 蓝 / 黄）+ 中心高光
for bx, by, col in [(CX - 30, 340, (255, 122, 122)),
                    (CX - 6, 340, (122, 202, 255)),
                    (CX + 18, 340, (255, 222, 128))]:
    dr.ellipse([bx - 7, by - 7, bx + 7, by + 7], fill=col)
    dr.ellipse([bx - 3, by - 3, bx + 3, by + 3], fill=(255, 255, 255, 220))
# 横条旋钮 + 指针
dr.rounded_rectangle([CX - 30, 360, CX + 4, 374], radius=6,
                     fill=(40, 50, 72, 255), outline=(162, 172, 194, 255), width=1)
dr.line([CX - 14, 367, CX + 2, 361], fill=(255, 255, 255, 210), width=3)

# --------------------------------------------------------------------------
# 8. 头（橙→黄径向渐变圆脸）
# --------------------------------------------------------------------------
yy, xx = np.mgrid[0:S, 0:S]
dist = np.sqrt((xx - CX) ** 2 + (yy - HEAD_Y) ** 2)
t = np.clip(dist / FACE_R, 0, 1)
orange = np.array([255, 158, 38], np.uint8)   # 脸中心：暖橙
yellow = np.array([255, 224, 122], np.uint8)  # 脸边缘：柔黄
face_rgb = (orange[None, None, :] * (1 - t)[..., None]
            + yellow[None, None, :] * t[..., None]).astype(np.uint8)
face = Image.fromarray(
    np.dstack([face_rgb, np.where(dist <= FACE_R, 255, 0).astype(np.uint8)]), "RGBA")
img.alpha_composite(face, (0, 0))
dr = ImageDraw.Draw(img, "RGBA")

# --------------------------------------------------------------------------
# 9. 极简五官：黑圆点眼 + 水平直线嘴
# --------------------------------------------------------------------------
eye_y = HEAD_Y - 12
for side in (-1, 1):
    ex = CX + side * 41
    dr.ellipse([ex - 9, eye_y - 9, ex + 9, eye_y + 9], fill=(34, 34, 38, 255))
    dr.ellipse([ex - 3, eye_y - 3, ex + 3, eye_y + 3], fill=(255, 255, 255, 235))
dr.line([CX - 33, HEAD_Y + 30, CX + 33, HEAD_Y + 30],
        fill=(42, 42, 48, 255), width=5)

# --------------------------------------------------------------------------
# 10. 巨大透明玻璃头盔（独立图层，alpha_composite 真混合实现「透明玻璃」）
#     ImageDraw 半透明填充会直接覆盖底层像素，因此头盔必须画到单独图层，
#     再合成到主图——橙脸/五官才能透过玻璃可见。
# --------------------------------------------------------------------------
helmet = Image.new("RGBA", (S, S))
hd = ImageDraw.Draw(helmet, "RGBA")
hr = HELMET_R
# 玻璃底色（微白透明）
hd.ellipse([CX - hr, HEAD_Y - hr, CX + hr, HEAD_Y + hr], fill=(224, 238, 255, 34))
# 虹彩折射描边（粉 / 青 / 紫多环）
for i, col in enumerate([(255, 128, 200, 92), (132, 202, 255, 92),
                         (200, 150, 255, 82)]):
    r0 = hr - 10 + i * 6
    hd.arc([CX - r0, HEAD_Y - r0, CX + r0, HEAD_Y + r0], 0, 360, fill=col, width=9)
# 粉色光泽（左下暖光）
hd.ellipse([CX - hr * 0.72, HEAD_Y + hr * 0.02, CX - hr * 0.12, HEAD_Y + hr * 0.68],
           fill=(255, 170, 210, 46))
# 顶部/右上高光（玻璃反射）
hd.ellipse([CX + hr * 0.28, HEAD_Y - hr * 0.86, CX + hr * 0.86, HEAD_Y - hr * 0.12],
           fill=(255, 255, 255, 96))
hd.ellipse([CX + hr * 0.42, HEAD_Y - hr * 0.7, CX + hr * 0.72, HEAD_Y - hr * 0.3],
           fill=(255, 255, 255, 150))
# 玻璃外描边
hd.ellipse([CX - hr, HEAD_Y - hr, CX + hr, HEAD_Y + hr],
           outline=(240, 248, 255, 205), width=3)
img.alpha_composite(helmet, (0, 0))

# --------------------------------------------------------------------------
# 11. 电影级布光（独立图层真混合）：顶部体积光 + 右侧轮廓光 + 光晕
# --------------------------------------------------------------------------
light = Image.new("RGBA", (S, S))
ld = ImageDraw.Draw(light, "RGBA")
ld.polygon([(CX + 150, 0), (CX + 250, 0), (CX + 60, S), (CX - 40, S)],
           fill=(255, 255, 255, 14))
ld.ellipse([CX - 170, -90, CX + 170, 180], fill=(255, 255, 255, 22))
# 右侧轮廓光（暖白）
ld.polygon([(CX + 150, 60), (CX + 230, 120), (CX + 190, 430), (CX + 130, 360)],
           fill=(255, 240, 220, 36))
img.alpha_composite(light, (0, 0))

img.convert("RGB").save(r"C:\Users\xu\Desktop\回顾Agent-视频素材\宇航员预览.png")
print("saved 512x512 -> 桌面/回顾Agent-视频素材/宇航员预览.png")
