#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""生成 CONTCAR GIF 项目的应用图标 assets/app.ico。

设计: 圆角方块 + 青绿渐变 + 六元环晶格 + 中心原子 + 旋转箭头,
     与 XDAT-gif 的蓝色分子图标区分开。
"""

from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

HERE = Path(__file__).resolve().parent
OUT = HERE / "assets" / "app.ico"


def rounded_gradient(size: int) -> Image.Image:
    """青绿 -> 深蓝绿 竖直渐变的圆角方块。"""
    scale = 4
    s = size * scale
    grad = Image.new("RGBA", (s, s))
    px = grad.load()
    top = (48, 209, 175)    # 青绿
    bot = (16, 112, 122)    # 深蓝绿
    for y in range(s):
        t = y / max(1, s - 1)
        r = int(top[0] + (bot[0] - top[0]) * t)
        g = int(top[1] + (bot[1] - top[1]) * t)
        b = int(top[2] + (bot[2] - top[2]) * t)
        for x in range(s):
            px[x, y] = (r, g, b, 255)

    # 左上角高光
    hi = Image.new("L", (s, s), 0)
    hd = ImageDraw.Draw(hi)
    hd.polygon([(0, 0), (s, 0), (0, s)], fill=60)
    hi = hi.filter(ImageFilter.GaussianBlur(s * 0.12))
    white = Image.new("RGBA", (s, s), (255, 255, 255, 255))
    grad = Image.composite(white, grad, hi)

    mask = Image.new("L", (s, s), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, s - 1, s - 1],
                                           radius=int(s * 0.22), fill=255)
    out = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    out.paste(grad, (0, 0), mask)
    return out.resize((size, size), Image.LANCZOS)


def draw_crystal(img: Image.Image) -> Image.Image:
    """绘制六元环晶格 + 中心原子 + 环绕旋转箭头。"""
    s = img.size[0]
    scale = 4
    big = img.resize((s * scale, s * scale), Image.LANCZOS)
    d = ImageDraw.Draw(big, "RGBA")
    W = s * scale
    cx, cy, ring_r = W * 0.50, W * 0.47, W * 0.16

    # 六元环顶点
    ring = []
    for k in range(6):
        ang = -math.pi / 2 + k * math.pi / 3
        ring.append((cx + ring_r * math.cos(ang),
                     cy + ring_r * math.sin(ang)))

    # 六元环的键 (实线)
    for i in range(6):
        d.line([ring[i], ring[(i + 1) % 6]],
               fill=(255, 255, 255, 230), width=max(1, int(W * 0.020)))

    # 环顶点原子 (小球)
    for x, y in ring:
        r = W * 0.052
        d.ellipse([x - r, y - r, x + r, y + r],
                  fill=(255, 255, 255, 255), outline=(240, 255, 250, 255),
                  width=max(1, int(r * 0.16)))

    # 中心原子 (金色)
    r = W * 0.085
    d.ellipse([cx - r, cy - r, cx + r, cy + r],
              fill=(255, 209, 35, 255), outline=(255, 255, 255, 255),
              width=max(1, int(r * 0.16)))

    # 环绕旋转箭头 (白色圆弧 + 箭头)
    arc_bbox = [cx - W * 0.27, cy - W * 0.27, cx + W * 0.27, cy + W * 0.27]
    d.arc(arc_bbox, start=-70, end=220, fill=(255, 255, 255, 235),
          width=max(1, int(W * 0.024)))
    # 箭头头部
    tip_ang = math.radians(220)
    tip_x = cx + W * 0.27 * math.cos(tip_ang)
    tip_y = cy + W * 0.27 * math.sin(tip_ang)
    d.polygon([
        (tip_x, tip_y),
        (tip_x - W * 0.035, tip_y - W * 0.045),
        (tip_x + W * 0.050, tip_y - W * 0.010),
    ], fill=(255, 255, 255, 235))

    return big.resize((s, s), Image.LANCZOS)


def main():
    base = rounded_gradient(256)
    base = draw_crystal(base)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    sizes = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    base.save(OUT, format="ICO", sizes=sizes)
    base.save(OUT.with_name("app.png"), format="PNG")
    print("saved", OUT)


if __name__ == "__main__":
    main()
