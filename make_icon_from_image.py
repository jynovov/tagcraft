#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
用一张插画生成项目图标（圆形徽章样式，只取面部）。

流程：
    1. 按 CROP 坐标裁出脸部方形区域
    2. 提一点对比度与饱和度（原图偏淡，缩到 32px 会糊）
    3. 放大后轻度锐化，圆形取景，加一圈紫色描边
    4. 输出 PNG + 多尺寸 ICO（小尺寸必须用 BMP 条目，见 ico_util.py）

素材来源：
    仓库内 assets/source_keqing_user.jpg —— 刻晴同人插画
    刻晴是米哈游《原神》的角色，该插画版权归原作者所有；
    MIT 许可证只覆盖本仓库的代码，不覆盖这张图。
"""

import os
from collections import deque

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter

from ico_util import write_ico

HERE = os.path.dirname(os.path.abspath(__file__))
ASSETS = os.path.join(HERE, "assets")
SRC = os.path.join(ASSETS, "source_keqing_user.jpg")

S = 512                      # 输出画布（源图不大，做 512 足够，避免过度放大发虚）
CROP = (165, 268, 425, 528)  # 只保留面部：以脸部为中心的 260×260 方形取景

RING = (226, 210, 248)       # 外圈亮描边
RING2 = (120, 78, 190)       # 外圈暗描边
BG_EDGE = (46, 26, 78)       # 圆外底色


def load_head():
    im = Image.open(SRC).convert("RGBA").crop(CROP)
    return im


def circle_mask(size, ss=2):
    """抗锯齿的圆形蒙版。"""
    m = Image.new("L", (size * ss, size * ss), 0)
    ImageDraw.Draw(m).ellipse([0, 0, size * ss - 1, size * ss - 1], fill=255)
    return m.resize((size, size), Image.LANCZOS)


def main():
    head = load_head()
    # 原图偏淡，提一点对比度和饱和度，缩到 32px 时才看得清五官
    head = ImageEnhance.Color(head).enhance(1.18)
    head = ImageEnhance.Contrast(head).enhance(1.10)
    # 取景缩放到内圈直径
    inner = int(S * 0.90)
    head_big = head.resize((inner, inner), Image.LANCZOS)
    # 源图不大，放大后轻微锐化，小尺寸下更清楚
    head_big = head_big.filter(ImageFilter.UnsharpMask(radius=2, percent=70, threshold=3))
    head_big.putalpha(circle_mask(inner))

    canvas = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    # 圆外留一圈深色，避免图标贴边显脏
    d = ImageDraw.Draw(canvas)
    d.ellipse([0, 0, S - 1, S - 1], fill=BG_EDGE + (255,))
    canvas.alpha_composite(head_big, ((S - inner) // 2, (S - inner) // 2))

    # 双层描边
    d = ImageDraw.Draw(canvas)
    r0 = (S - inner) // 2
    d.ellipse([r0 - 8, r0 - 8, S - r0 + 7, S - r0 + 7], outline=RING2 + (255,), width=14)
    d.ellipse([r0 - 2, r0 - 2, S - r0 + 1, S - r0 + 1], outline=RING + (245,), width=7)
    canvas.putalpha(Image.composite(canvas.split()[3], Image.new("L", (S, S), 0),
                                    circle_mask(S)))

    canvas.save(os.path.join(ASSETS, "tagcraft.png"))
    canvas.resize((512, 512), Image.LANCZOS).save(os.path.join(ASSETS, "tagcraft_512.png"))
    canvas.resize((256, 256), Image.LANCZOS).save(os.path.join(ASSETS, "tagcraft_256.png"))
    n = write_ico(os.path.join(ASSETS, "tagcraft.ico"), canvas)

    # 小尺寸预览
    prev = Image.new("RGBA", (400, 120), (24, 16, 38, 255))
    x = 20
    for s in (16, 20, 32, 48, 64):
        prev.alpha_composite(canvas.resize((s, s), Image.LANCZOS), (x, (120 - s) // 2))
        x += s + 22
    prev.save(os.path.join(ASSETS, "preview_sizes.png"))

    print("ICO 包含 %d 个尺寸" % n)
    for f in ("tagcraft.png", "tagcraft_512.png", "tagcraft_256.png", "tagcraft.ico",
              "preview_sizes.png"):
        p = os.path.join(ASSETS, f)
        print("  生成 %-20s %8d 字节" % (f, os.path.getsize(p)))


if __name__ == "__main__":
    main()
