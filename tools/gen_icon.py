#!/usr/bin/env python3
"""Generate icon-192.png / icon-512.png — dark tile, green check. Pure stdlib."""
import struct
import zlib
from pathlib import Path

BG = (14, 17, 23)        # #0e1117
FG = (74, 222, 128)      # #4ade80

def dist_seg(px, py, ax, ay, bx, by):
    vx, vy = bx - ax, by - ay
    wx, wy = px - ax, py - ay
    c1 = vx * wx + vy * wy
    c2 = vx * vx + vy * vy
    t = 0.0 if c2 == 0 else max(0.0, min(1.0, c1 / c2))
    dx, dy = px - (ax + t * vx), py - (ay + t * vy)
    return (dx * dx + dy * dy) ** 0.5

def make(size, out):
    # checkmark inside the maskable safe zone (center ~60%)
    a = (0.30 * size, 0.53 * size)
    b = (0.44 * size, 0.67 * size)
    c = (0.72 * size, 0.35 * size)
    r = 0.055 * size
    rows = []
    for y in range(size):
        row = bytearray([0])  # filter type 0
        for x in range(size):
            d = min(dist_seg(x, y, *a, *b), dist_seg(x, y, *b, *c))
            if d <= r:
                col = FG
            elif d <= r + 1.5:  # cheap anti-alias blend
                k = (d - r) / 1.5
                col = tuple(int(FG[i] * (1 - k) + BG[i] * k) for i in range(3))
            else:
                col = BG
            row += bytes(col)
        rows.append(bytes(row))
    raw = b"".join(rows)

    def chunk(typ, data):
        c = struct.pack(">I", len(data)) + typ + data
        return c + struct.pack(">I", zlib.crc32(typ + data) & 0xFFFFFFFF)

    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(raw, 9))
    png += chunk(b"IEND", b"")
    Path(out).write_bytes(png)
    print(out, len(png), "bytes")

here = Path(__file__).resolve().parent.parent
make(192, here / "icon-192.png")
make(512, here / "icon-512.png")
