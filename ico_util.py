#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ICO 写入工具：手写 ICO 容器，小尺寸用标准 BMP(DIB) 条目。

为什么不用 Pillow 的 img.save("x.ico", sizes=[...])：
    它会把每个尺寸都存成 PNG 压缩格式，而资源管理器加载 16/32 这类小图标时
    不认 PNG 条目 —— 结果是桌面/任务栏上显示成白纸图标。
    本文具按 Windows 通行做法：16~128 用 BMP，256 用 PNG。
"""

import struct


def bmp_entry(img):
    """把一个 RGBA 图转成 ICO 内的 BMP(DIB) 条目。"""
    w, h = img.size
    px = img.convert("RGBA").load()
    xor = bytearray()
    for y in range(h - 1, -1, -1):            # DIB 自下而上
        for x in range(w):
            r, g, b, a = px[x, y]
            xor += bytes((b, g, r, a))        # BGRA
    mask_row = ((w + 31) // 32) * 4           # AND 掩码每行 4 字节对齐
    and_mask = b"\x00" * (mask_row * h)
    header = struct.pack("<IiiHHIIiiII", 40, w, h * 2, 1, 32, 0,
                         len(xor) + len(and_mask), 0, 0, 0, 0)
    return header + bytes(xor) + and_mask


def write_ico(path, source, bmp_sizes=(16, 20, 24, 32, 40, 48, 64, 128), png_sizes=(256,)):
    """把一张 PIL 图写成多尺寸 ICO。

    path       输出 .ico 路径
    source     源图（建议 512 以上，RGBA）
    bmp_sizes  用 BMP 格式存储的尺寸（兼容性最好）
    png_sizes  用 PNG 格式存储的尺寸（256 必须用 PNG，否则文件巨大）
    """
    entries = []
    for s in bmp_sizes:
        entries.append((source.resize((s, s), __import__("PIL.Image", fromlist=["Image"]).LANCZOS), False))
    for s in png_sizes:
        entries.append((source.resize((s, s), __import__("PIL.Image", fromlist=["Image"]).LANCZOS), True))

    import io
    blobs = []
    for img, use_png in entries:
        if use_png:
            buf = io.BytesIO()
            img.save(buf, "PNG")
            blobs.append(buf.getvalue())
        else:
            blobs.append(bmp_entry(img))

    n = len(blobs)
    head = struct.pack("<HHH", 0, 1, n)
    offset = 6 + 16 * n
    dirs = b""
    for (img, _), blob in zip(entries, blobs):
        w, h = img.size
        dirs += struct.pack("<BBBBHHII", w % 256, h % 256, 0, 0, 1, 32, len(blob), offset)
        offset += len(blob)
    with open(path, "wb") as f:
        f.write(head + dirs + b"".join(blobs))
    return n
