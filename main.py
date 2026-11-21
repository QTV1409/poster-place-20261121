#!/usr/bin/env python3
"""VELMORA — a travel poster for a place that isn't there.

Generates one travel poster (800x1024 PNG) for Velmora, a city of floating
islands above a sea of clouds, and writes it to /output/entry.png.

Pure Python 3 standard library only: no network, no external assets, no
fonts on disk. The scene is drawn with a small software rasterizer
(gradients, polygons, soft ellipses, thick strokes) and the lettering uses
a hand-drawn vector stroke font defined below, so the whole poster is
produced from this one file, deterministically.
"""

import math
import os
import struct
import sys
import zlib

W, H = 800, 1024
OUT = "/output/entry.png"

# ---------------------------------------------------------------- palette
CREAM = (255, 243, 214)
GOLD = (255, 207, 125)
AMBER = (242, 166, 90)
SUN_CORE = (255, 233, 168)
SUN_GLOW = (255, 179, 71)
INK = (32, 38, 74)          # deep indigo (footer band, borders of night)
ROCK_DARK = (56, 41, 78)
ROCK_MID = (69, 51, 88)
ROCK_LIGHT = (90, 66, 104)
GRASS = (154, 143, 85)
GRASS_DARK = (122, 112, 62)
TREE = (44, 33, 64)
FAR_ISLE = (110, 74, 128)
LIGHTHOUSE = (255, 243, 214)
ROOF_RED = (201, 80, 79)
CLOUD_HI = (255, 233, 201)
CLOUD_MID = (255, 217, 168)
CLOUD_LO = (217, 138, 142)
BIRD = (42, 33, 64)

SKY_STOPS = [
    (0, (29, 35, 70)),
    (260, (74, 53, 114)),
    (470, (138, 74, 114)),
    (600, (217, 106, 99)),
    (700, (242, 166, 90)),
    (760, (247, 192, 120)),
    (1024, (232, 160, 106)),
]

CLOUD_STOPS = [
    (740, (247, 201, 143)),
    (860, (240, 178, 140)),
    (1024, (222, 148, 128)),
]


def lerp(a, b, t):
    return a + (b - a) * t


def mix(c1, c2, t):
    return tuple(int(round(lerp(a, b, t))) for a, b in zip(c1, c2))


def clamp01(x):
    return 0.0 if x < 0.0 else 1.0 if x > 1.0 else x


def hash01(i):
    """Deterministic pseudo-random in [0, 1) from an integer."""
    return (math.sin(i * 12.9898) * 43758.5453) % 1.0


# ---------------------------------------------------------------- canvas
class Canvas:
    def __init__(self, w, h):
        self.w = w
        self.h = h
        self.px = bytearray(w * h * 3)

    def blend(self, x, y, color, a):
        if a <= 0.0 or x < 0 or y < 0 or x >= self.w or y >= self.h:
            return
        i = (y * self.w + x) * 3
        b = self.px
        if a >= 1.0:
            b[i] = color[0]
            b[i + 1] = color[1]
            b[i + 2] = color[2]
        else:
            b[i] = int(b[i] + (color[0] - b[i]) * a)
            b[i + 1] = int(b[i + 1] + (color[1] - b[i + 1]) * a)
            b[i + 2] = int(b[i + 2] + (color[2] - b[i + 2]) * a)

    def fill_span(self, y, x0f, x1f, color, a=1.0):
        """Fill the overlap of [x0f, x1f) with pixels of row y (AA edges)."""
        if y < 0 or y >= self.h or a <= 0.0 or x1f <= x0f:
            return
        x0 = int(math.floor(x0f))
        x1 = int(math.floor(x1f))
        if x1 < 0 or x0 >= self.w:
            return
        x0c = max(x0, 0)
        x1c = min(x1, self.w - 1)
        for x in range(x0c, x1c + 1):
            lo = max(x, x0f)
            hi = min(x + 1.0, x1f)
            cov = hi - lo
            if cov > 0.0:
                self.blend(x, y, color, a * cov)

    def fill_rect(self, x0, y0, x1, y1, color, a=1.0):
        for y in range(max(0, y0), min(self.h, y1)):
            self.fill_span(y, float(x0), float(x1), color, a)

    def vgrad(self, stops, y0=0, y1=None):
        """Vertical gradient over the whole width, from y0 to y1."""
        if y1 is None:
            y1 = self.h
        for y in range(max(0, y0), min(self.h, y1)):
            col = stops[0][1]
            for (ya, ca), (yb, cb) in zip(stops, stops[1:]):
                if ya <= y <= yb:
                    t = 0.0 if yb == ya else (y - ya) / (yb - ya)
                    col = mix(ca, cb, t)
                    break
            else:
                col = stops[-1][1]
            row = bytes(col) * self.w
            i = y * self.w * 3
            self.px[i:i + self.w * 3] = row

    def poly(self, pts, color, a=1.0):
        """Filled polygon with anti-aliased edges (scanline)."""
        if not pts:
            return
        ys = [p[1] for p in pts]
        y0 = max(0, int(math.floor(min(ys))))
        y1 = min(self.h - 1, int(math.ceil(max(ys))))
        n = len(pts)
        for y in range(y0, y1 + 1):
            yc = y + 0.5
            xs = []
            for i in range(n):
                x1, y1p = pts[i]
                x2, y2p = pts[(i + 1) % n]
                if (y1p <= yc < y2p) or (y2p <= yc < y1p):
                    t = (yc - y1p) / (y2p - y1p)
                    xs.append(x1 + t * (x2 - x1))
            xs.sort()
            for k in range(0, len(xs) - 1, 2):
                self.fill_span(y, xs[k], xs[k + 1], color, a)

    def blob(self, cx, cy, rx, ry, color, a, feather=0.25):
        """Soft ellipse: full alpha inside, smooth falloff to the edge."""
        x0 = max(0, int(cx - rx))
        x1 = min(self.w - 1, int(cx + rx) + 1)
        y0 = max(0, int(cy - ry))
        y1 = min(self.h - 1, int(cy + ry) + 1)
        for y in range(y0, y1 + 1):
            dy = (y + 0.5 - cy) / ry
            for x in range(x0, x1 + 1):
                dx = (x + 0.5 - cx) / rx
                d2 = dx * dx + dy * dy
                if d2 >= 1.0:
                    continue
                d = math.sqrt(d2)
                t = clamp01((1.0 - d) / feather) if feather > 0 else 1.0
                if t > 0.0:
                    self.blend(x, y, color, a * t)

    def seg(self, x1, y1, x2, y2, color, hw, a=1.0):
        """Thick line segment with round caps (capsule distance test)."""
        dx = x2 - x1
        dy = y2 - y1
        L2 = dx * dx + dy * dy
        pad = hw + 1.5
        x0 = max(0, int(min(x1, x2) - pad))
        x1b = min(self.w - 1, int(max(x1, x2) + pad) + 1)
        y0 = max(0, int(min(y1, y2) - pad))
        y1b = min(self.h - 1, int(max(y1, y2) + pad) + 1)
        if L2 == 0.0:
            self.blob(x1, y1, hw, hw, color, a, feather=0.0)
            return
        inv = 1.0 / L2
        for y in range(y0, y1b + 1):
            py = y + 0.5
            for x in range(x0, x1b + 1):
                px = x + 0.5
                t = ((px - x1) * dx + (py - y1) * dy) * inv
                t = 0.0 if t < 0.0 else 1.0 if t > 1.0 else t
                qx = x1 + t * dx
                qy = y1 + t * dy
                d = math.hypot(px - qx, py - qy)
                cov = clamp01(hw + 0.75 - d)
                if cov > 0.0:
                    self.blend(x, y, color, a * cov)


# ------------------------------------------------------- stroke font (14u)
# Glyphs as polylines on a 0..10 (x) by 0..14 (y) grid, y=0 top, y=14 baseline.
FONT = {
    "A": [[(0, 14), (5, 0), (10, 14)], [(2.6, 8), (7.4, 8)]],
    "B": [[(0, 0), (0, 14)], [(0, 0), (6.5, 0), (9, 2), (9, 5), (6.5, 7), (0, 7)],
          [(0, 7), (6.5, 7), (9, 9), (9, 12), (6.5, 14), (0, 14)]],
    "C": [[(9, 2), (7, 0), (3, 0), (0, 3), (0, 11), (3, 14), (7, 14), (9, 12)]],
    "D": [[(0, 0), (0, 14)], [(0, 0), (6, 0), (9, 3), (9, 11), (6, 14), (0, 14)]],
    "E": [[(0, 0), (0, 14)], [(0, 0), (9, 0)], [(0, 7), (7, 7)], [(0, 14), (9, 14)]],
    "F": [[(0, 0), (0, 14)], [(0, 0), (9, 0)], [(0, 7), (7, 7)]],
    "H": [[(0, 0), (0, 14)], [(10, 0), (10, 14)], [(0, 7), (10, 7)]],
    "I": [[(5, 0), (5, 14)], [(2, 0), (8, 0)], [(2, 14), (8, 14)]],
    "L": [[(0, 0), (0, 14)], [(0, 14), (9, 14)]],
    "M": [[(0, 14), (0, 0), (5, 9), (10, 0), (10, 14)]],
    "N": [[(0, 14), (0, 0), (10, 14), (10, 0)]],
    "O": [[(3, 0), (7, 0), (10, 3), (10, 11), (7, 14), (3, 14), (0, 11), (0, 3), (3, 0)]],
    "R": [[(0, 0), (0, 14)], [(0, 0), (6.5, 0), (9, 2), (9, 5), (6.5, 7), (0, 7)],
          [(6, 7), (10, 14)]],
    "S": [[(9, 2), (7, 0), (3, 0), (1, 2), (1, 5), (3, 7), (7, 7), (9, 9),
           (9, 12), (7, 14), (3, 14), (1, 12)]],
    "T": [[(0, 0), (10, 0)], [(5, 0), (5, 14)]],
    "U": [[(0, 0), (0, 11), (2, 14), (8, 14), (10, 11), (10, 0)]],
    "V": [[(0, 0), (5, 14), (10, 0)]],
    "W": [[(0, 0), (2.5, 14), (5, 6), (7.5, 14), (10, 0)]],
    "Y": [[(0, 0), (5, 7), (10, 0)], [(5, 7), (5, 14)]],
}


def draw_text(cv, s, cx, baseline_y, cap, color, spacing=0.0, a=1.0):
    """Draw centred text; cap = cap height in px; baseline_y = baseline."""
    u = cap / 14.0
    adv = 12.0 * u + spacing
    total = len(s) * adv - spacing
    x = cx - total / 2.0
    hw = 1.15 * u
    for ch in s:
        if ch == " ":
            x += adv
            continue
        glyph = FONT.get(ch)
        if glyph:
            for stroke in glyph:
                for (ax, ay), (bx, by) in zip(stroke, stroke[1:]):
                    cv.seg(x + ax * u, baseline_y - (14 - ay) * u,
                           x + bx * u, baseline_y - (14 - by) * u,
                           color, hw, a)
        x += adv
    return total


# ---------------------------------------------------------------- scene
def tree(cv, x, base_y, s, color=TREE):
    cv.poly([(x - 8 * s, base_y), (x + 8 * s, base_y),
             (x, base_y - 22 * s)], color)


def bird(cv, x, y, s):
    cv.seg(x - 9 * s, y, x, y + 4 * s, BIRD, 1.6 * s)
    cv.seg(x, y + 4 * s, x + 9 * s, y, BIRD, 1.6 * s)


def draw_poster():
    cv = Canvas(W, H)

    # --- sky
    cv.vgrad(SKY_STOPS)

    # --- stars in the upper sky
    for i in range(48):
        x = 14 + hash01(i * 3 + 1) * (W - 28)
        y = 30 + hash01(i * 7 + 2) * 270
        r = 0.8 + hash01(i * 11 + 3) * 1.1
        cv.blob(x, y, r, r, CREAM, 0.25 + hash01(i * 13 + 4) * 0.5, feather=0.0)

    # --- low sun, right of centre, with a wide glow
    cv.blob(620, 375, 280, 280, SUN_GLOW, 0.45, feather=0.65)
    cv.blob(620, 375, 150, 150, SUN_GLOW, 0.5, feather=0.6)
    cv.blob(620, 375, 88, 88, SUN_CORE, 1.0, feather=0.04)

    # --- distant hazy island, low on the horizon
    cv.poly([(30, 478), (90, 448), (150, 462), (230, 438), (310, 455),
             (390, 440), (470, 458), (560, 442), (650, 460), (730, 446),
             (775, 468), (760, 492), (690, 508), (560, 516), (420, 510),
             (280, 516), (140, 508), (55, 494)], FAR_ISLE, a=0.88)
    tree(cv, 300, 452, 0.9, color=(88, 58, 104))
    tree(cv, 560, 450, 0.8, color=(88, 58, 104))

    # --- main island: rocky underside tapering to a point
    cv.poly([(170, 548), (505, 548), (498, 592), (482, 645), (452, 700),
             (415, 748), (372, 782), (340, 798), (308, 782), (265, 748),
             (228, 700), (198, 645), (182, 592)], ROCK_DARK)
    # strata lines across the rock
    cv.seg(190, 612, 482, 612, ROCK_MID, 3.0, a=0.6)
    cv.seg(214, 678, 458, 678, ROCK_MID, 3.0, a=0.6)
    cv.seg(296, 736, 384, 736, ROCK_LIGHT, 2.5, a=0.5)
    # warm rim light along the top edge
    cv.seg(170, 548, 505, 548, GOLD, 2.2, a=0.85)

    # --- grassy plateau on the main island
    cv.poly([(170, 548), (505, 548), (505, 560), (170, 560)], GRASS)
    cv.poly([(170, 548), (505, 548), (505, 553), (170, 553)], GRASS_DARK)
    tree(cv, 215, 548, 1.0)
    tree(cv, 262, 548, 0.85)
    tree(cv, 425, 548, 1.0)
    tree(cv, 462, 548, 0.8)

    # --- lighthouse on the main island
    cv.poly([(290, 548), (310, 548), (306, 470), (294, 470)], LIGHTHOUSE)
    cv.poly([(293, 502), (307, 502), (305, 489), (295, 489)], ROOF_RED)
    cv.blob(300, 468, 10, 8, ROOF_RED, 1.0, feather=0.1)
    cv.blob(300, 466, 6, 6, SUN_CORE, 1.0, feather=0.1)
    # light beams sweeping up into the dusk
    cv.poly([(300, 466), (150, 392), (150, 428)], SUN_CORE, a=0.12)
    cv.poly([(300, 466), (472, 386), (472, 422)], SUN_CORE, a=0.12)
    cv.blob(300, 466, 26, 20, SUN_CORE, 0.35, feather=0.6)

    # --- waterfall pouring off the main island into the clouds
    cv.seg(478, 556, 472, 620, CLOUD_HI, 5.0, a=0.8)
    cv.seg(472, 620, 476, 680, CLOUD_HI, 5.0, a=0.8)
    cv.seg(476, 680, 464, 742, CLOUD_HI, 5.0, a=0.8)
    cv.seg(478, 556, 472, 620, (255, 248, 224), 2.0, a=0.9)
    cv.seg(472, 620, 476, 680, (255, 248, 224), 2.0, a=0.9)
    cv.seg(476, 680, 464, 742, (255, 248, 224), 2.0, a=0.9)
    cv.blob(464, 748, 46, 24, CLOUD_HI, 0.6, feather=0.5)

    # --- smaller island, higher, with a little pavilion (sun sets behind it)
    cv.poly([(560, 425), (705, 425), (695, 460), (678, 510), (655, 560),
             (638, 598), (620, 555), (600, 505), (575, 460)], ROCK_MID)
    cv.poly([(560, 425), (705, 425), (705, 437), (560, 437)], GRASS_DARK)
    cv.seg(560, 425, 705, 425, GOLD, 1.8, a=0.8)
    tree(cv, 585, 425, 0.7)
    tree(cv, 690, 425, 0.65)
    cv.poly([(626, 425), (654, 425), (654, 398), (626, 398)], LIGHTHOUSE)
    cv.poly([(620, 398), (660, 398), (640, 382)], ROOF_RED)

    # --- birds
    for (bx, by, bs) in [(250, 330, 1.0), (302, 298, 0.8), (352, 344, 0.9),
                         (520, 288, 0.85), (566, 322, 0.7), (180, 380, 0.75)]:
        bird(cv, bx, by, bs)

    # --- sea of clouds, bottom third (soft top edge: puffs break the line)
    cv.vgrad(CLOUD_STOPS, y0=740, y1=H)
    top_puffs = [(40, 742, 80, 26), (150, 752, 100, 30), (270, 740, 85, 26),
                 (390, 754, 110, 30), (510, 742, 90, 26), (630, 754, 105, 30),
                 (750, 744, 85, 26)]
    for (cx, cy, rx, ry) in top_puffs:
        cv.blob(cx, cy, rx, ry, CLOUD_MID, 0.55, feather=0.6)
    shadow = [(70, 800, 95, 30), (260, 830, 120, 36), (470, 810, 110, 32),
              (660, 835, 115, 34), (740, 900, 100, 30), (150, 910, 110, 32),
              (380, 925, 120, 34), (560, 915, 110, 32)]
    for (cx, cy, rx, ry) in shadow:
        cv.blob(cx, cy + 16, rx, ry, CLOUD_LO, 0.35, feather=0.5)
    puffs = [(60, 772, 90, 34), (180, 800, 120, 42), (300, 762, 100, 36),
             (430, 808, 130, 44), (560, 775, 110, 38), (690, 800, 120, 42),
             (770, 765, 90, 34), (120, 880, 110, 36), (300, 905, 130, 40),
             (480, 885, 120, 38), (660, 905, 125, 40), (750, 880, 95, 34)]
    for (cx, cy, rx, ry) in puffs:
        cv.blob(cx, cy, rx, ry, CLOUD_HI, 0.85, feather=0.45)
        cv.blob(cx - rx * 0.25, cy - ry * 0.25, rx * 0.6, ry * 0.6,
                CLOUD_MID, 0.7, feather=0.5)

    # --- footer band with the tagline's promise
    cv.fill_rect(0, 938, W, H, INK)
    cv.fill_rect(0, 936, W, 939, GOLD, a=0.9)
    draw_text(cv, "WHERE THE SEA LEARNS TO FLY", 400, 992, 21, CREAM,
              spacing=2.0)
    # small gold diamonds flanking the footer line
    for dx in (96, 704):
        cv.poly([(dx, 972), (dx + 7, 982), (dx, 992), (dx - 7, 982)], GOLD)

    # --- title and tagline, set into the night sky
    draw_text(cv, "VELMORA", 400, 160, 96, CREAM, spacing=2.0)
    draw_text(cv, "ISLANDS ABOVE THE CLOUDS", 400, 200, 25, GOLD, spacing=3.0)
    cv.seg(205, 224, 388, 224, GOLD, 1.1, a=0.85)
    cv.seg(412, 224, 595, 224, GOLD, 1.1, a=0.85)
    cv.poly([(400, 218), (407, 224), (400, 230), (393, 224)], GOLD)

    # --- double poster border
    cv.fill_rect(16, 16, 784, 19, CREAM, a=0.85)
    cv.fill_rect(16, 1005, 784, 1008, CREAM, a=0.85)
    cv.fill_rect(16, 16, 19, 1008, CREAM, a=0.85)
    cv.fill_rect(781, 16, 784, 1008, CREAM, a=0.85)
    cv.fill_rect(26, 26, 774, 27, CREAM, a=0.45)
    cv.fill_rect(26, 997, 774, 998, CREAM, a=0.45)
    cv.fill_rect(26, 26, 27, 998, CREAM, a=0.45)
    cv.fill_rect(773, 26, 774, 998, CREAM, a=0.45)

    return cv


# ---------------------------------------------------------------- PNG out
def _crc(data):
    return zlib.crc32(data) & 0xFFFFFFFF


def _chunk(typ, data):
    return struct.pack(">I", len(data)) + typ + data + struct.pack(
        ">I", _crc(typ + data))


def _filter_row(kind, row, prev, bpp=3):
    if kind == 0:
        return row
    out = bytearray(len(row))
    if kind == 1:  # Sub
        for i, v in enumerate(row):
            a = row[i - bpp] if i >= bpp else 0
            out[i] = (v - a) & 0xFF
    elif kind == 2:  # Up
        for i, v in enumerate(row):
            out[i] = (v - prev[i]) & 0xFF
    elif kind == 3:  # Average
        for i, v in enumerate(row):
            a = row[i - bpp] if i >= bpp else 0
            out[i] = (v - ((a + prev[i]) >> 1)) & 0xFF
    else:  # Paeth
        for i, v in enumerate(row):
            a = row[i - bpp] if i >= bpp else 0
            b = prev[i]
            c = prev[i - bpp] if i >= bpp else 0
            p = a + b - c
            pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
            pr = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
            out[i] = (v - pr) & 0xFF
    return bytes(out)


def write_png(path, w, h, px):
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    raw = bytearray()
    prev = bytes(w * 3)
    for y in range(h):
        row = bytes(px[y * w * 3:(y + 1) * w * 3])
        best = None
        for kind in (0, 1, 2, 3, 4):
            f = _filter_row(kind, row, prev)
            c = zlib.compress(bytes([kind]) + f, 6)
            if best is None or len(c) < len(best[0]):
                best = (c, kind, f)
        raw += bytes([best[1]]) + best[2]
        prev = row
    data = (b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", ihdr)
            + _chunk(b"IDAT", zlib.compress(bytes(raw), 9))
            + _chunk(b"IEND", b""))
    with open(path, "wb") as f:
        f.write(data)
    return len(data)


def main():
    cv = draw_poster()
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    size = write_png(OUT, W, H, cv.px)
    print(f"wrote {OUT}: {W}x{H}, {size} bytes")
    if size > 1048576:
        print("ERROR: over the 1 MiB gate", file=sys.stderr)
        sys.exit(1)
    print("OK: within the 1 MiB / 1024x1024 gate")


if __name__ == "__main__":
    main()
