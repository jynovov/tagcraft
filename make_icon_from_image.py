#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
用一张完整插画生成项目图标（不裁切、不做圆形徽章，整张图直接缩放到图标尺寸）。

流程：
    1. 读取原图；不是正方形就补透明边，保证构图完整
    2. 缩放到 512 画布（ICO 必须有多种尺寸，这一步缩放不可避免）
    3. 输出 PNG + 多尺寸 ICO（小尺寸必须用 BMP 条目，见 ico_util.py）

素材来源：
    assets/source_deepseek_girl.jpg —— 豆包 AI 生成的 DeepSeek 形象图
    图片版权、DeepSeek 名称与商标归各自权利人；MIT 只覆盖代码，不覆盖这张图。
"""

import os

from PIL import Image

from ico_util import write_ico

HERE = os.path.dirname(os.path.abspath(__file__))
ASSETS = os.path.join(HERE, "assets")
SRC = os.path.join(ASSETS, "source_deepseek_girl.jpg")

S = 512                      # 输出画布边长


def square(im):
    """把任意尺寸的图放进正方形画布，居中、补透明边，不裁切。"""
    w, h = im.size
    side = max(w, h)
    canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    canvas.alpha_composite(im.convert("RGBA"), ((side - w) // 2, (side - h) // 2))
    return canvas


def main():
    im = square(Image.open(SRC))
    canvas = im.resize((S, S), Image.LANCZOS)

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
