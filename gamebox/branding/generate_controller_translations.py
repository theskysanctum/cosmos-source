#!/usr/bin/env python3
"""Loads the real, hand-drawn cosmos_controller.png UNCHANGED and
produces 3 derived copies with ONLY the platform-specific button labels
patched - everything else (silhouette, D-pad, sticks, logo, the
TL/TR/BL/BR position callouts, the LEFT STICK/D-PAD/RIGHT STICK/FACE
BUTTONS category callouts) stays byte-for-byte the original art. Never
touches/regenerates cosmos_controller.png itself - that's real,
hand-drawn work, not something to reproduce from primitives.

Every coordinate below was measured directly off the real source image
(pixel-scanned for each glyph's actual bounding box, then visually
verified via cropped zooms) - not guessed. Re-run this if
cosmos_controller.png itself is ever redrawn/relabeled and these
coordinates need to move with it."""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

SCRIPT_DIR = Path(__file__).resolve().parent
SRC = SCRIPT_DIR / "cosmos_controller.png"
FONT_DIR = SCRIPT_DIR.parent / "launcher" / "fonts"
BOLD = str(FONT_DIR / "Rajdhani-Bold.ttf")

BODY_FILL = (27, 27, 27, 255)
LINE = (225, 19, 223, 255)  # sampled directly from the source art's own line color

# (left, top, right, bottom) of each glyph's real bounding box in the
# source image.
BOXES = {
	"TL_pill": (347, 29, 377, 50),
	"BL_pill": (347, 91, 377, 111),
	"R1_pill": (929, 30, 954, 50),
	"R2_pill": (929, 91, 959, 111),
	"P": (937, 181, 958, 210),
	"F": (869, 251, 886, 280),
	"V": (1004, 251, 1028, 280),
	"N": (935, 320, 957, 349),
	"SELECT_text": (534, 289, 594, 300),
	"START_text": (718, 289, 768, 300),
}

# font point size to use per region, tuned to roughly match each
# region's own real glyph height above
SIZES = {
	"TL_pill": 20, "BL_pill": 20, "R1_pill": 20, "R2_pill": 20,
	"P": 26, "F": 26, "V": 26, "N": 26,
	"SELECT_text": 15, "START_text": 15,
}

STROKE = {
	"TL_pill": 2, "BL_pill": 2, "R1_pill": 2, "R2_pill": 2,
	"P": 3, "F": 3, "V": 3, "N": 3,
	"SELECT_text": 1, "START_text": 1,
}

# "-"/"+" have almost no glyph area compared to a real letter at the
# same point size - confirmed live they rendered as a barely-visible
# speck at the same size everything else uses, so Switch's Select/Start
# labels specifically need a real size bump to read as anything at all.
SIZE_OVERRIDES = {
	("switch", "SELECT_text"): 30,
	("switch", "START_text"): 34,
}
STROKE_OVERRIDES = {
	("switch", "SELECT_text"): 3,
	("switch", "START_text"): 3,
}

# Real per-platform button names. Face-button rotation for Switch is
# deliberate, not a typo: Nintendo's own physical ABXY layout is rotated
# one position clockwise from Xbox/PlayStation's south=primary
# convention - A is east, B is south, X is north, Y is west.
PLATFORMS = {
	"xbox": {
		"TL_pill": "LB", "BL_pill": "LT", "R1_pill": "RB", "R2_pill": "RT",
		"P": "Y", "F": "X", "V": "B", "N": "A",
		"SELECT_text": "VIEW", "START_text": "MENU",
	},
	"switch": {
		"TL_pill": "L", "BL_pill": "ZL", "R1_pill": "R", "R2_pill": "ZR",
		"P": "X", "F": "Y", "V": "A", "N": "B",
		"SELECT_text": "-", "START_text": "+",
	},
	# playstation handled separately below - face buttons are drawn as
	# real shape icons (triangle/square/circle/cross), not letters, the
	# authentic way those buttons are actually labeled - everything else
	# (shoulders, select/start) is still plain text like the other two.
	"playstation": {
		"TL_pill": "L1", "BL_pill": "L2", "R1_pill": "R1", "R2_pill": "R2",
		"SELECT_text": "SHARE", "START_text": "OPTIONS",
	},
}

PS_SHAPES = {"P": "triangle", "F": "square", "V": "circle", "N": "cross"}


def erase(draw, box, pad=2):
	x0, y0, x1, y1 = box
	draw.rectangle((x0 - pad, y0 - pad, x1 + pad, y1 + pad), fill=BODY_FILL)


def draw_label(draw, box, text, size, stroke_w, platform, region):
	size = SIZE_OVERRIDES.get((platform, region), size)
	stroke_w = STROKE_OVERRIDES.get((platform, region), stroke_w)
	cx = (box[0] + box[2]) / 2
	cy = (box[1] + box[3]) / 2
	fnt = ImageFont.truetype(BOLD, size)
	# Hollow neon-outline look matching the source art's own text style
	# (confirmed by pixel-sampling a real glyph there - it's stroke-only,
	# not solid-filled): fill=BODY_FILL makes the glyph's own interior
	# invisible against the body, stroke_fill=LINE draws just its outline.
	draw.text((cx, cy), text, font=fnt, fill=BODY_FILL, stroke_width=stroke_w, stroke_fill=LINE, anchor="mm")


def draw_ps_shape(draw, box, kind):
	cx = (box[0] + box[2]) / 2
	cy = (box[1] + box[3]) / 2
	w = 3
	if kind == "triangle":
		s = 13
		draw.polygon([(cx, cy - s), (cx - s * 1.05, cy + s * 0.8), (cx + s * 1.05, cy + s * 0.8)], outline=LINE, width=w)
	elif kind == "square":
		s = 10
		draw.rectangle((cx - s, cy - s, cx + s, cy + s), outline=LINE, width=w)
	elif kind == "circle":
		s = 12
		draw.ellipse((cx - s, cy - s, cx + s, cy + s), outline=LINE, width=w)
	elif kind == "cross":
		s = 10
		draw.line((cx - s, cy - s, cx + s, cy + s), fill=LINE, width=w)
		draw.line((cx - s, cy + s, cx + s, cy - s), fill=LINE, width=w)


def build(platform, labels, out_path):
	im = Image.open(SRC).convert("RGBA").copy()
	d = ImageDraw.Draw(im)
	for region, box in BOXES.items():
		erase(d, box)
	for region, text in labels.items():
		if platform == "playstation" and region in PS_SHAPES:
			continue
		draw_label(d, BOXES[region], text, SIZES[region], STROKE[region], platform, region)
	if platform == "playstation":
		for region, kind in PS_SHAPES.items():
			draw_ps_shape(d, BOXES[region], kind)
	im.save(out_path)
	print(f"wrote {out_path}")


if __name__ == "__main__":
	for platform, labels in PLATFORMS.items():
		build(platform, labels, SCRIPT_DIR / f"cosmos_controller_{platform}.png")
