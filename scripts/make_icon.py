# -*- coding: utf-8 -*-
"""用纯 Python 生成 PowerRenamePy 的应用图标 icon.ico（不依赖 PIL）。

图标设计：深蓝底色 + 白色双向箭头（表示重命名/交换）。
只生成一个 256x256 尺寸的 32bpp PNG 式 ICO（Windows 资源管理器和 PyInstaller 均可使用）。
"""

from __future__ import annotations

import struct
from pathlib import Path

SIZE = 256
BG = (45, 108, 223)       # #2D6CDF 深蓝
ARROW = (255, 255, 255)   # 白色
ARROW_LIGHT = (180, 205, 255)  # 浅蓝（箭头内高光）


def in_rounded_rect(x, y, x0, y0, x1, y1, r):
    if x < x0 or x > x1 or y < y0 or y > y1:
        return False
    # 四角圆角
    corners = [(x0 + r, y0 + r, 1, 1), (x1 - r, y0 + r, -1, 1),
               (x0 + r, y1 - r, 1, -1), (x1 - r, y1 - r, -1, -1)]
    for cx, cy, dx, dy in corners:
        if (x - cx) * dx < 0 and (y - cy) * dy < 0:
            if (x - cx) ** 2 + (y - cy) ** 2 > r * r:
                return False
    return True


def in_arrow(x, y):
    """水平双向箭头：中心横杆 + 左右三角箭头头。"""
    cx, cy = SIZE / 2, SIZE / 2
    # 中心横杆
    if abs(y - cy) <= 34 and abs(x - cx) <= 66:
        return True
    # 左箭头头（指向左）
    if x <= cx - 30:
        t = (cx - 30 - x) / 70  # 0..1 从左到右
        half = 34 * (1 - t) + 14 * t
        if x >= cx - 100 and abs(y - cy) <= half:
            return True
    # 右箭头头（指向右）
    if x >= cx + 30:
        t = (x - (cx + 30)) / 70
        half = 14 * (1 - t) + 34 * t
        if x <= cx + 100 and abs(y - cy) <= half:
            return True
    return False


def in_arrow_light(x, y):
    """箭头高光：中心杆下半部偏白的细条。"""
    if abs(x - cx) <= 66 and cy - 18 <= y <= cy + 18:
        return True
    return False


def main() -> None:
    cx = SIZE / 2
    cy = SIZE / 2
    pixels = []  # BGRA, 自下而上
    for py in range(SIZE - 1, -1, -1):
        for px in range(SIZE):
            if not in_rounded_rect(px, py, 4, 4, SIZE - 5, SIZE - 5, 40):
                pixels.append((0, 0, 0, 0))
                continue
            if in_arrow(px, py):
                if abs(py - cy) <= 30 and 20 <= abs(px - cx) <= 66:
                    c = ARROW_LIGHT
                else:
                    c = ARROW
            else:
                # 简单竖向渐变让底色不呆板
                t = py / SIZE
                c = (int(45 + 25 * (1 - t)), int(108 - 18 * t), 223)
            pixels.append((c[2], c[1], c[0], 255))  # BGRA

    # BITMAPINFOHEADER (40B) + 像素 + AND 掩码（全 0，不透明）
    header = struct.pack("<IiiHHIIiiII", 40, SIZE, SIZE, 1, 32, 0,
                         len(pixels) * 4 + SIZE * (SIZE // 8), 0, 0, 0, 0)
    and_mask_size = (SIZE // 32) * 4 * SIZE  # 1bpp 每行对齐到 4 字节
    and_mask = b"\x00" * and_mask_size
    bmp = header + b"".join(struct.pack("<BBBB", *p) for p in pixels) + and_mask

    # ICONDIR + 单个 ICONDIRENTRY
    icon = struct.pack("<HHH", 0, 1, 1)
    icon += struct.pack("<BBBBHHII", 0, 0, 0, 0, 1, 32, len(bmp), 6 + 16)
    icon += bmp

    out = Path(__file__).resolve().parent.parent / "assets" / "icon.ico"
    out.write_bytes(icon)
    print(f"icon written: {out} ({out.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
