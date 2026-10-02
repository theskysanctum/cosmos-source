#!/usr/bin/env python3
"""Generates CosmOS's default profile pictures.

Every picture is drawn here, from scratch, out of plain shapes and gradients, so
they are original works with no photographs, characters or third-party artwork in
them: released under CC0 (public domain) - use, change and redistribute freely.
Run:  python3 generate_avatars.py        (writes avatar-NN-name.png next to this file)

The set is themed on the desktop's own palette (space-dark blue, magenta, white)
and is deterministic, so regenerating gives byte-identical files.
"""
import math
import os
import random

from PIL import Image, ImageChops, ImageDraw, ImageFilter

SIZE = 512
SS = 3                      # supersampling factor for smooth edges
BIG = SIZE * SS
BG = (10, 14, 22)
FG = (232, 236, 244)
MAGENTA = (255, 0, 144)


def canvas(top, bottom):
    """Vertical gradient background."""
    img = Image.new("RGB", (BIG, BIG))
    px = ImageDraw.Draw(img)
    for y in range(BIG):
        t = y / (BIG - 1)
        px.line([(0, y), (BIG, y)], fill=tuple(int(a + (b - a) * t) for a, b in zip(top, bottom)))
    return img


def stars(img, seed, count=90, tint=FG):
    rnd = random.Random(seed)
    d = ImageDraw.Draw(img)
    for _ in range(count):
        x, y = rnd.randrange(BIG), rnd.randrange(BIG)
        r = rnd.choice((1, 1, 2, 2, 3)) * SS // 2 + 1
        shade = rnd.randint(140, 255)
        d.ellipse([x - r, y - r, x + r, y + r], fill=tuple(int(c * shade / 255) for c in tint))


def disc(img, cx, cy, r, fill, glow=None):
    if glow:
        halo = Image.new("RGB", img.size, (0, 0, 0))
        ImageDraw.Draw(halo).ellipse([cx - r * 1.25, cy - r * 1.25, cx + r * 1.25, cy + r * 1.25], fill=glow)
        halo = halo.filter(ImageFilter.GaussianBlur(r * 0.35))
        img.paste(ImageChops.add(img, halo))
    ImageDraw.Draw(img).ellipse([cx - r, cy - r, cx + r, cy + r], fill=fill)


def shaded_sphere(img, cx, cy, r, light, dark):
    """A sphere lit from the upper left."""
    sphere = Image.new("RGB", img.size, (0, 0, 0))
    mask = Image.new("L", img.size, 0)
    ImageDraw.Draw(mask).ellipse([cx - r, cy - r, cx + r, cy + r], fill=255)
    px = ImageDraw.Draw(sphere)
    steps = 64
    for i in range(steps, 0, -1):
        t = i / steps
        ox, oy = -0.35 * r * (1 - t), -0.35 * r * (1 - t)
        rr = r * t
        color = tuple(int(dark[k] + (light[k] - dark[k]) * (1 - t) ** 0.8) for k in range(3))
        px.ellipse([cx + ox - rr, cy + oy - rr, cx + ox + rr, cy + oy + rr], fill=color)
    img.paste(sphere, (0, 0), mask)


def ring(img, cx, cy, rx, ry, width, color, tilt, front):
    """An elliptical ring; front=False draws only the half behind the planet."""
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    d.ellipse([cx - rx, cy - ry, cx + rx, cy + ry], outline=color + (255,), width=width)
    layer = layer.rotate(tilt, center=(cx, cy), resample=Image.BICUBIC)
    mask = Image.new("L", img.size, 0)
    md = ImageDraw.Draw(mask)
    md.rectangle([0, cy if front else 0, BIG, BIG if front else cy], fill=255)
    layer.putalpha(ImageChops.multiply(layer.getchannel("A"), mask))
    img.paste(layer, (0, 0), layer)


def finish(img, name):
    img = img.resize((SIZE, SIZE), Image.LANCZOS)
    img.save(os.path.join(os.path.dirname(os.path.abspath(__file__)), name), optimize=True)


def ringed_planet():
    img = canvas((10, 14, 30), (40, 12, 60))
    stars(img, 1)
    c = BIG // 2
    ring(img, c, c, int(BIG * .44), int(BIG * .12), 14 * SS // 2, (255, 120, 190), -22, front=False)
    shaded_sphere(img, c, c, int(BIG * .26), (255, 190, 120), (120, 30, 80))
    ring(img, c, c, int(BIG * .44), int(BIG * .12), 14 * SS // 2, (255, 120, 190), -22, front=True)
    return img


def crescent_moon():
    img = canvas((6, 10, 24), (18, 36, 70))
    stars(img, 2, 110)
    c, r = BIG // 2, int(BIG * .3)
    disc(img, c, c, r, (240, 236, 210), glow=(60, 60, 40))
    ImageDraw.Draw(img).ellipse([c - r + r * .55, c - r - r * .12, c + r + r * .55, c + r - r * .12], fill=(12, 20, 44))
    return img


def sun_and_horizon():
    img = canvas((255, 0, 144), (20, 10, 60))
    d = ImageDraw.Draw(img)
    c, r = BIG // 2, int(BIG * .24)
    for i in range(7):                      # retro sun with cut stripes
        pass
    disc(img, c, int(BIG * .46), r, (255, 226, 120), glow=(200, 60, 100))
    d = ImageDraw.Draw(img)
    for i in range(6):
        y = int(BIG * .46 + r * (0.05 + i * 0.16))
        d.rectangle([c - r - 4, y, c + r + 4, y + int(6 * SS * (1 + i * .5))], fill=(30, 12, 70))
    d.rectangle([0, int(BIG * .68), BIG, BIG], fill=(14, 10, 40))
    for i in range(1, 9):                   # perspective grid
        d.line([(c + (i - 4.5) * BIG * .05, int(BIG * .68)), (c + (i - 4.5) * BIG * .34, BIG)], fill=MAGENTA, width=3)
    for j in range(5):
        y = int(BIG * .68 + (BIG * .32) * (j / 5) ** 1.7)
        d.line([(0, y), (BIG, y)], fill=MAGENTA, width=3)
    return img


def comet():
    img = canvas((8, 8, 24), (10, 30, 60))
    stars(img, 3)
    layer = Image.new("RGB", img.size, (0, 0, 0))
    d = ImageDraw.Draw(layer)
    hx, hy = int(BIG * .68), int(BIG * .34)
    for i in range(40):                     # tail
        t = i / 40
        r = int(BIG * .07 * (1 - t) + 3)
        x, y = hx - t * BIG * .55, hy + t * BIG * .5
        c = tuple(int(v * (1 - t) ** 1.4) for v in (120, 200, 255))
        d.ellipse([x - r, y - r, x + r, y + r], fill=c)
    layer = layer.filter(ImageFilter.GaussianBlur(BIG * .012))
    img.paste(ImageChops.add(img, layer))
    disc(img, hx, hy, int(BIG * .065), (230, 250, 255), glow=(90, 170, 255))
    return img


def spiral_galaxy():
    img = canvas((6, 6, 18), (6, 6, 18))
    rnd = random.Random(4)
    layer = Image.new("RGB", img.size, (0, 0, 0))
    d = ImageDraw.Draw(layer)
    c = BIG // 2
    for arm in range(2):
        for i in range(2600):
            t = i / 2600
            a = arm * math.pi + t * 5.2
            rad = t * BIG * .42
            x = c + math.cos(a) * rad + rnd.gauss(0, BIG * .022 * (0.4 + t))
            y = c + math.sin(a) * rad * .72 + rnd.gauss(0, BIG * .022 * (0.4 + t))
            col = (255, int(120 + 100 * (1 - t)), int(160 + 90 * (1 - t))) if t < .6 else (150, 120, 255)
            r = rnd.choice((2, 3, 4)) * SS // 2
            d.ellipse([x - r, y - r, x + r, y + r], fill=tuple(int(v * (0.35 + 0.65 * (1 - t))) for v in col))
    layer = layer.filter(ImageFilter.GaussianBlur(2 * SS))
    img.paste(ImageChops.add(img, layer))
    disc(img, c, c, int(BIG * .05), (255, 240, 220), glow=(255, 190, 120))
    stars(img, 5, 60)
    return img


def rocket():
    img = canvas((10, 12, 40), (60, 20, 90))
    stars(img, 6, 80)
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    cx, cy = BIG // 2, BIG // 2
    d.polygon([(cx, cy - BIG * .3), (cx + BIG * .1, cy - BIG * .05), (cx + BIG * .1, cy + BIG * .16),
               (cx - BIG * .1, cy + BIG * .16), (cx - BIG * .1, cy - BIG * .05)], fill=FG + (255,))
    d.polygon([(cx - BIG * .1, cy + BIG * .02), (cx - BIG * .2, cy + BIG * .22), (cx - BIG * .1, cy + BIG * .16)], fill=MAGENTA + (255,))
    d.polygon([(cx + BIG * .1, cy + BIG * .02), (cx + BIG * .2, cy + BIG * .22), (cx + BIG * .1, cy + BIG * .16)], fill=MAGENTA + (255,))
    d.ellipse([cx - BIG * .055, cy - BIG * .1, cx + BIG * .055, cy + BIG * .01], fill=(90, 190, 255, 255))
    d.polygon([(cx - BIG * .06, cy + BIG * .16), (cx, cy + BIG * .34), (cx + BIG * .06, cy + BIG * .16)], fill=(255, 190, 60, 255))
    img.paste(layer, (0, 0), layer)
    return img


def eclipse():
    img = canvas((4, 4, 12), (20, 6, 40))
    stars(img, 7, 70)
    c, r = BIG // 2, int(BIG * .26)
    for k in range(18, 0, -1):              # corona
        rr = r + k * BIG * .012
        ImageDraw.Draw(img).ellipse([c - rr, c - rr, c + rr, c + rr], outline=(255, int(60 + k * 6), int(150 + k * 4)), width=SS)
    disc(img, c, c, r, (4, 4, 10), glow=(255, 60, 160))
    return img


def orbit_rings():
    img = canvas((8, 10, 28), (20, 34, 70))
    stars(img, 8, 60)
    c = BIG // 2
    d = ImageDraw.Draw(img)
    for i, rr in enumerate((.14, .24, .34, .44)):
        r = int(BIG * rr)
        d.ellipse([c - r, c - r, c + r, c + r], outline=(70 + i * 20, 90 + i * 15, 160), width=2 * SS)
        a = i * 1.9 + .6
        disc(img, int(c + math.cos(a) * r), int(c + math.sin(a) * r), int(BIG * (.02 + i * .008)), (255, 120 + i * 30, 190))
    disc(img, c, c, int(BIG * .07), (255, 226, 130), glow=(255, 150, 60))
    return img


DRAWINGS = [ringed_planet, crescent_moon, sun_and_horizon, comet, spiral_galaxy, rocket, eclipse, orbit_rings]
NAMES = ["ringed-planet", "crescent-moon", "synthwave-sun", "comet", "spiral-galaxy", "rocket", "eclipse", "orbits"]

if __name__ == "__main__":
    for number, (draw, name) in enumerate(zip(DRAWINGS, NAMES), 1):
        finish(draw(), f"avatar-{number:02d}-{name}.png")
        print(f"avatar-{number:02d}-{name}.png")
