#!/usr/bin/env python3
"""CosmOS's own Minecraft-blocky, Mii/Xbox-avatar-concept 3D
character generator - customizable skin, hair, eyebrow, shirt, pants/
skirt, eye, and shoe color, on top of dozens of real body/clothing/
animal/mythical/accessibility traits (see build_character()'s own
docstring). NOT seed-random (that's launcher/avatar_gen.py's own older
convention, deliberately dropped here) - every attribute is an
explicit, real pick a person makes about their own avatar, the
*_COLORS/HAIRSTYLES/etc. lists below are just the swatch/option lists a
picker UI offers (see avatar3d_viewer.py). OffscreenRenderer renders a
real frame to a Texture - avatar3d_viewer.py pulls that repeatedly on a
timer for a live animated preview; avatar3d_rig.py builds a smaller,
pivot-based rig subset of this same geometry for that animation."""
import sys
from pathlib import Path

from panda3d.core import (
	GeomVertexFormat, GeomVertexData, GeomVertexWriter, GeomTriangles, Geom, GeomNode,
	AmbientLight, DirectionalLight, NodePath, loadPrcFileData,
	GraphicsPipe, FrameBufferProperties, WindowProperties, GraphicsOutput, Texture,
)
from direct.showbase.ShowBase import ShowBase
import math

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import debuglog  # noqa: E402

log = debuglog.setup("avatar3d")

loadPrcFileData("", "window-type offscreen")
loadPrcFileData("", "audio-library-name null")

SKIN_TONES = ["#ffe0bd", "#f1c27d", "#e0ac69", "#c68642", "#8d5524", "#4a2c17"]
HAIR_COLORS = ["#1a1a1a", "#3b2417", "#6b4423", "#c99b4a", "#e8c840", "#a83232", "#e8e8e8", "#3355cc", "#cc3388"]
SHIRT_COLORS = ["#ff0090", "#c400ff", "#00e5ff", "#ffb800", "#00ff85", "#7dff00", "#ff3d3d", "#f4f7ff", "#2a2a3a"]
PANTS_COLORS = ["#22252e", "#2b3a55", "#4a3728", "#5c1414", "#1e3d2f", "#3a2a5c", "#6b6b6b", "#0a0e16"]
EYE_COLORS = ["#3b2417", "#1e5fa8", "#2e8b57", "#6b6b6b", "#8b4513", "#00e5ff", "#7dff00"]
SHOE_COLORS = ["#1a1a1a", "#5c1414", "#f4f7ff", "#3355cc", "#8d5524", "#c400ff"]

# Style variety - separate from color: which SHAPE of hair/eyes/top/
# bottom, not just which color. Every one of these is a real, distinct
# geometry branch below (not a shared box with a different color slapped
# on), the same "explicit pick, no randomness" reasoning already applied
# everywhere else in this generator.
HAIRSTYLES = ["short", "medium", "long", "ponytail", "pigtails", "bun", "afro", "curly", "spiky", "buzz", "undercut", "bald"]
EYE_SHAPES = ["round", "narrow", "sharp", "wide", "hooded"]
NOSE_SHAPES = ["straight", "button", "wide", "pointed", "upturned", "flat"]
MOUTH_STYLES = ["neutral", "smile", "frown", "small", "smirk", "open"]
HAT_STYLES = ["cap", "beanie", "wide_brim", "beret"]
FACIAL_HAIR = ["none", "mustache", "goatee", "full_beard", "stubble"]
TOP_STYLES = ["long_sleeve", "short_sleeve", "tank_top", "hoodie", "collared", "jacket", "turtleneck", "vest"]
BOTTOM_STYLES = ["pants", "shorts", "skirt", "dress", "overalls"]
PANTS_STYLES = ["straight", "skinny", "baggy", "cropped"]
SHORTS_STYLES = ["regular", "bermuda", "athletic"]
SKIRT_STYLES = ["a_line", "pencil", "pleated", "mini"]
ACCESSORIES = ["glasses", "hat", "backpack", "satchel", "purse", "necklace", "earrings", "tie", "wristband", "socks", "gloves", "headphones"]
GLASSES_COLORS = ["#1a1a1a", "#3b2417", "#5c1414", "#2a2a3a", "#ff0090"]
HAT_COLORS = PANTS_COLORS
JEWELRY_COLORS = ["#ffd700", "#c0c0c0", "#1a1a1a", "#5c1414", "#ff0090"]
HEADPHONES_COLORS = ["#1a1a1a", "#2a2a3a", "#f4f7ff", "#ff0090", "#00e5ff"]

# Furry/anthro customization - ears and tail are each a single
# choice (like hairstyle), not a combinable accessories-list entry,
# since a face has one ear style at a time - but the two are
# independent of EACH OTHER (a cat-eared character can have a fox
# tail), matching how furries actually mix and match species traits
# rather than picking one fixed "species".
EARS = [
	"none", "human", "cat", "fox", "wolf", "bunny", "dog", "bear", "deer", "horse", "pig",
	"raccoon", "mouse", "lizard", "goat", "bat", "dragon", "elf", "alien",
]
TAIL_STYLES = [
	"none", "cat", "fox", "wolf", "bunny", "dog", "bear", "deer", "horse", "pig",
	"raccoon", "lizard", "dragon", "demon",
]
INNER_EAR_COLOR = (0.93, 0.7, 0.75, 1)
FUR_PATTERNS = ["solid", "spots", "stripes", "patches"]
NOSE_TIP_COLOR = (0.08, 0.07, 0.08, 1)
WHISKER_COLOR = (0.96, 0.96, 0.93, 1)
CLAW_COLOR = (0.94, 0.92, 0.85, 1)

# Mythical/alien features - each independent of ears/tail (a
# horned, winged, cat-eared character is a valid combination, matching
# the same "mix and match traits, don't force one fixed species" rule
# already established for ears/tail).
HORN_STYLES = ["none", "small", "curved", "demon", "unicorn"]
WING_STYLES = ["none", "angel", "bat", "fairy", "dragon"]
HORN_COLOR_DEFAULT = "#e8ddc8"

# Limb/mobility/accessibility customization - real, independent traits,
# not a single "disability mode" switch. missing_arm/missing_leg/
# missing_eye each pick which SIDE(S) (none/left/right/both), and
# combine freely with everything else (a wheelchair user can still have
# any hairstyle, animal ears, a prosthetic arm, etc).
LEG_TYPES = ["human", "digitigrade"]
LIMB_SIDES = ["none", "left", "right", "both"]
PROSTHETIC_OPTIONS = ["left_arm", "right_arm", "left_leg", "right_leg"]
PROSTHETIC_COLOR_DEFAULT = "#c0c0c0"
FRECKLE_COLOR = (0.55, 0.35, 0.22, 1)
# Opaque, not alpha-blended - this file's whole pipeline is flat unlit
# vertex color with no blend state set up, so an alpha<1 here would
# just be silently ignored (rendered fully opaque) rather than
# blending like a real blush would. A light, already-translucent-
# looking pink at alpha=1 gets the same visual effect without relying
# on a blend mode that was never wired up.
BLUSH_COLOR = (0.92, 0.55, 0.58, 1)


def _box(vertex, normal, color, tris, idx, lo, hi, col):
	"""One axis-aligned box, per-face flat-shaded (min corner lo, max
	corner hi, both (x,y,z) tuples) - the shared low-level primitive
	every body part below is built from, same idea as the cube in the
	earlier embedding spike, just parameterized."""
	x0, y0, z0 = lo
	x1, y1, z1 = hi
	faces = [
		((0, 0, 1), [(x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)]),
		((0, 0, -1), [(x0, y1, z0), (x1, y1, z0), (x1, y0, z0), (x0, y0, z0)]),
		((0, 1, 0), [(x0, y1, z0), (x0, y1, z1), (x1, y1, z1), (x1, y1, z0)]),
		((0, -1, 0), [(x0, y0, z1), (x0, y0, z0), (x1, y0, z0), (x1, y0, z1)]),
		((1, 0, 0), [(x1, y0, z1), (x1, y0, z0), (x1, y1, z0), (x1, y1, z1)]),
		((-1, 0, 0), [(x0, y0, z0), (x0, y0, z1), (x0, y1, z1), (x0, y1, z0)]),
	]
	for n, pts in faces:
		for p in pts:
			vertex.addData3(*p)
			normal.addData3(*n)
			color.addData4(*col)
		tris.addVertices(idx, idx + 1, idx + 2)
		tris.addVertices(idx, idx + 2, idx + 3)
		idx += 4
	return idx


def _disc_x(vertex, normal, color, tris, idx, cx, cy, cz, thickness, radius, col, segments=20):
	"""A flat cylindrical disc (a wheel/coin shape) whose two flat
	circular faces are perpendicular to the X axis - i.e. the axle runs
	along X, exactly matching a wheelchair wheel's own real orientation
	(this file's own established convention: a wheel's axle points
	left-right, so its flat face lies in the Y-Z plane). Direct, blunt
	feedback: the wheelchair's wheels were plain rectangular boxes -
	"why the fuck are the wheels square?" - so this is a real, actually-
	round shape for them, built the same way _box's own per-face flat-
	shaded style works (SHARED vertices per ring, not per-triangle -
	smooth-ish around the rim, still flat-faceted per segment, which
	reads fine at this scale) rather than reusing an ellipsoid/capsule
	generator (deliberately not reintroducing organic body-style
	curvature elsewhere - this is a narrowly-scoped shape for wheels
	specifically, not a return to the earlier rounded-body style that
	was rejected). Same (vertex, normal, color, tris, idx) -> idx
	calling convention as _box. Winding/normals verified by direct
	render (a solid, correctly-lit circular puck, not a black disc or a
	visibly hollow shell) rather than assumed."""
	x0, x1 = cx - thickness / 2, cx + thickness / 2

	def _write(x, y, z, nx, ny, nz):
		vertex.addData3(x, y, z)
		normal.addData3(nx, ny, nz)
		color.addData4(*col)

	# Rim wall - two same-radius rings (at x0 and x1), purely radial
	# normals (zero X component).
	ring0 = idx
	for j in range(segments):
		theta = 2 * math.pi * j / segments
		ny, nz = math.cos(theta), math.sin(theta)
		_write(x0, cy + radius * ny, cz + radius * nz, 0.0, ny, nz)
		idx += 1
	ring1 = idx
	for j in range(segments):
		theta = 2 * math.pi * j / segments
		ny, nz = math.cos(theta), math.sin(theta)
		_write(x1, cy + radius * ny, cz + radius * nz, 0.0, ny, nz)
		idx += 1
	for j in range(segments):
		jn = (j + 1) % segments
		tris.addVertices(ring0 + j, ring1 + j, ring1 + jn)
		tris.addVertices(ring0 + j, ring1 + jn, ring0 + jn)

	# End caps - own vertex rings (own +-X normals, not shared with the
	# rim wall's radial ones), triangle-fanned from a center vertex.
	center0 = idx
	_write(x0, cy, cz, -1.0, 0.0, 0.0)
	idx += 1
	cap0 = idx
	for j in range(segments):
		theta = 2 * math.pi * j / segments
		_write(x0, cy + radius * math.cos(theta), cz + radius * math.sin(theta), -1.0, 0.0, 0.0)
		idx += 1
	for j in range(segments):
		jn = (j + 1) % segments
		tris.addVertices(center0, cap0 + j, cap0 + jn)

	center1 = idx
	_write(x1, cy, cz, 1.0, 0.0, 0.0)
	idx += 1
	cap1 = idx
	for j in range(segments):
		theta = 2 * math.pi * j / segments
		_write(x1, cy + radius * math.cos(theta), cz + radius * math.sin(theta), 1.0, 0.0, 0.0)
		idx += 1
	for j in range(segments):
		jn = (j + 1) % segments
		tris.addVertices(center1, cap1 + jn, cap1 + j)

	return idx


OUTLINE_WIDTH = 0.013
# A real, confirmed bug found live (the user directly flagged the hair
# looking "jacked up" - a streaky/glitchy texture visible across nearly
# every render this whole session): several hair-cap styles use
# -HEAD_S/2 as their own front face, EXACTLY matching the head box's
# own front face wherever the two overlap in z (the hairline region) -
# perfectly coplanar surfaces the GPU can't consistently pick between,
# i.e. real z-fighting, not a lighting/texture artifact. This epsilon
# pushes the hair's front just barely past the head's, enough to kill
# the z-fighting outright while staying nowhere near the eyes' own much
# larger poke-out depths (0.09+), so it can't reintroduce the original
# "hair occludes eyes" bug that made hair flush with the head's front
# face the rule in the first place.
HAIR_FRONT_EPSILON = 0.004


def _hair_back_fill(vertex, normal, color, tris, idx, hair, head_z0, HEAD_S, cap_bottom_z, back_bottom_frac=0.28):
	"""Real short/buzzed/curly hair doesn't stop in a flat ring around
	the WHOLE head at the same height - only the front needs to stay
	above the eyes. Every "cap" hairstyle box before this fix cut off at
	the identical z all the way around (front, sides, AND back), which
	reads as a bowl cut: a lid of hair floating on bare skin, most
	obviously wrong at the back where nothing needs to stay uncovered
	(a real, confirmed-live complaint: "why is there a bowl cut with no
	hair on the back of the head"). Fills in the back half only (y >= 0,
	the same side the face's own -y front never reaches) down to near
	ear/jaw level, leaving the front boundary - and every hairstyle's
	existing "never occlude the eyes" rule - completely untouched."""
	back_bottom_z = head_z0 + HEAD_S * back_bottom_frac
	return _box(
		vertex, normal, color, tris, idx,
		(-HEAD_S / 2 - 0.05, 0.0, back_bottom_z), (HEAD_S / 2 + 0.05, HEAD_S / 2 + 0.05, cap_bottom_z), hair,
	)


def _outlined_box(vertex, normal, color, tris, idx, lo, hi, col, width=OUTLINE_WIDTH, flush_z0=False, flush_z1=False, flush_x0=False, flush_x1=False):
	"""Plain box, no outline. This used to draw a black backing box
	behind the real one to create a visible border on every side (see
	git history for the original rationale) - but that border was a
	z-fighting bug factory: it needed one exception after another (an
	inset on the y-ends to stop solid-black backs when viewed from
	behind, then flush_z0/z1 to stop solid-black joints on rotation,
	then flush_x0/x1 to stop solid-black limbs from a profile angle)
	and STILL broke on ordinary camera angles (a real, confirmed-
	live "black buzzing on the shoulders" report even after all three
	fixes). Direct instruction: remove all outlining rather than keep
	patching individual faces. The width/flush_* parameters are kept
	(unused) purely so every existing call site still works unchanged."""
	return _box(vertex, normal, color, tris, idx, lo, hi, col)


def _leg_hock_spur(vertex, normal, color, tris, idx, lx0, leg_w, col, back_y=0.21):
	"""leg_type="digitigrade" support - a small backward-poking wedge
	near the ankle/heel (a real, recognizable animal "hock" marker), NOT
	a full reversed-knee leg reshape. A deliberate, documented scope cut:
	reshaping the whole leg per bottom_style would mean touching 5
	separate branches, each with its own z-range/width convention - this
	one shared spur applies uniformly regardless of which is active,
	at the cost of a less dramatic digitigrade silhouette.
	A real, confirmed-live bug in the first pass: the spur's own front
	(y=0.16) sat WELL INSIDE every bottom_style branch's own leg/shoe
	back face (which extends out to y=0.2-0.24 depending on branch,
	solid all the way there) - so only the thin sliver past that back
	face (0.2ish-0.3) was ever actually visible, reading as a barely-
	there hairline rather than a real spur. Fixed by starting the
	spur's front at `back_y` (0.21 by default, safely past the deepest
	back face any of the other branches calling this use) so the WHOLE
	shape shows - _build_foot/_build_leg_pants pass their own actual
	heel back-face explicitly instead of relying on that default, so
	the spur always starts exactly where their own foot geometry
	actually ends, not a fixed guess that desyncs the moment that
	geometry's own depth changes."""
	return _outlined_box(
		vertex, normal, color, tris, idx,
		(lx0 - 0.015, back_y, 0.04), (lx0 + leg_w + 0.015, back_y + 0.21, 0.2), col,
	)


# Thread color for stitch tick marks (see _hem_band) - a plain off-
# white/cream, the color real thread reads as against almost any fabric
# color, rather than trying to pick a matching thread per garment color.
STITCH_THREAD_COLOR = (0.92, 0.9, 0.85, 1)
STITCH_POKE = 0.006  # how far a stitch tick pokes past the hem's own front face, same scale as this file's other poke-out epsilons (HAIR_FRONT_EPSILON, the mouth-open overlay)
CUFF_HEM_H = 0.05  # pants-cuff hem band height - the garment box next to it is shrunk by exactly this much so the two sit flush with no overlap
SLEEVE_HEM_H = 0.05  # same idea, sleeve cuff
SHIRT_HEM_H = 0.06  # same idea, shirt hem at the waistline


def _seam_line(vertex, normal, color, tris, idx, x_edge, side, z0, z1, col, width=0.018, y_half=0.07):
	"""A thin accent-colored ridge running the full height of a garment
	segment (pants leg, sleeve, ...) - the same real detail as a
	trouser's own side seam. A real, confirmed-live bug in the first
	version: centered ON `x_edge` so half the strip sat embedded INSIDE
	the leg's own volume, with the exact same y/z extent as the leg
	already occupying that space - two coincident surfaces fighting for
	the same depth, flickering on every frame. Entirely OUTSIDE the
	surface now instead (poking away from the body, in whichever x
	direction `side` says is outward), the same "poke out past what's
	behind it, never straddle it" rule this file already uses for
	stitches/eyes/mouth/hair."""
	x0, x1 = (x_edge, x_edge + width) if side == 1 else (x_edge - width, x_edge)
	return _box(vertex, normal, color, tris, idx, (x0, -y_half, z0), (x1, y_half, z1), col)


def _hem_band(vertex, normal, color, tris, idx, x0, x1, y0, y1, z0, z1, accent_c, stitch_c=None, stitch_count=5):
	"""A hem/cuff: a thin accent-colored band across a garment's own
	(x0,x1,y0,y1) footprint, plus a row of small stitch tick marks
	poking past the FRONT (more-negative-y) face. `z0,z1` must be a
	slice the garment box ITSELF doesn't also cover - a real, confirmed-
	live bug in the first version: this band's z-range was centered ON
	the garment's own edge, so half of it duplicated a chunk of the
	garment box's own volume (identical x0/x1/y0/y1, an exact overlap,
	not just an adjacent surface) - the textbook z-fighting setup, not
	a subtle case of it. Callers now shrink the garment's own box by
	this band's height and pass the exact freed-up (z0,z1) slice here
	instead, so the two boxes sit flush adjacent with zero overlap.
	`stitch_c` defaults to STITCH_THREAD_COLOR (the plain off-white
	every hem used before customization was requested) when not given."""
	stitch_c = stitch_c if stitch_c is not None else STITCH_THREAD_COLOR
	idx = _box(vertex, normal, color, tris, idx, (x0, y0, z0), (x1, y1, z1), accent_c)
	band_h = z1 - z0
	width = x1 - x0
	stitch_w = width / (stitch_count * 2)
	for i in range(stitch_count):
		sx0 = x0 + (2 * i + 0.5) * stitch_w
		idx = _box(
			vertex, normal, color, tris, idx,
			(sx0, y0 - STITCH_POKE, z0 + band_h * 0.2), (sx0 + stitch_w, y0 + 0.002, z0 + band_h * 0.8),
			stitch_c,
		)
	return idx


FABRIC_PATTERNS = ("solid", "stripes", "plaid", "polka_dot", "color_block")
PATTERN_POKE = 0.006  # same poke-out epsilon as stitches - a pattern overlay must never sit flush/embedded in the garment's own front face, same z-fighting reasoning as _hem_band's own docstring


def _apply_fabric_pattern(vertex, normal, color, tris, idx, x0, x1, y_front, z0, z1, pattern, pattern_c):
	"""Overlays a fabric pattern across one flat garment face (x0..x1,
	z0..z1, at front face `y_front`) in `pattern_c` - a real, deliberate
	choice given this whole generator is flat per-vertex color with no
	UV/texture support at all: a "pattern" here means real extra
	geometry poking a hair past the garment's own front face (the same
	"never sit flush/embedded" rule as every other overlay in this file
	- stitches, eyes, the mouth), not a texture swap. No-ops for
	"solid" or a missing pattern_c so every call site can pass whatever
	pattern/color it was given unconditionally, the same "just call it,
	let the no-op cases handle themselves" convention _leg_hock_spur/
	_hem_band/etc. already use."""
	if pattern == "solid" or pattern_c is None or pattern not in FABRIC_PATTERNS:
		return idx
	width, height = x1 - x0, z1 - z0
	if width <= 0 or height <= 0:
		return idx
	y0, y1 = y_front - PATTERN_POKE, y_front + 0.002
	if pattern == "stripes":
		band_h = height / 5
		for i in (0, 2, 4):
			z_lo = z0 + i * band_h
			idx = _box(vertex, normal, color, tris, idx, (x0, y0, z_lo), (x1, y1, min(z_lo + band_h, z1)), pattern_c)
	elif pattern == "plaid":
		band_h = height / 5
		for i in (0, 2, 4):
			z_lo = z0 + i * band_h
			idx = _box(vertex, normal, color, tris, idx, (x0, y0, z_lo), (x1, y1, min(z_lo + band_h, z1)), pattern_c)
		band_w = width / 4
		for j in (0, 2):
			x_lo = x0 + j * band_w
			idx = _box(vertex, normal, color, tris, idx, (x_lo, y0, z0), (min(x_lo + band_w, x1), y1, z1), pattern_c)
	elif pattern == "polka_dot":
		cols, rows = 3, 4
		cell_w, cell_h = width / cols, height / rows
		dot_w, dot_h = cell_w * 0.42, cell_h * 0.42
		for r in range(rows):
			row_offset = cell_w * 0.5 if r % 2 else 0.0
			for c in range(cols):
				cx = x0 + c * cell_w + cell_w * 0.5 + row_offset
				if cx + dot_w / 2 > x1:
					cx -= cell_w * cols  # wrap the staggered dot back onto the garment instead of spilling past its edge
				cz = z0 + r * cell_h + cell_h * 0.5
				idx = _box(vertex, normal, color, tris, idx, (cx - dot_w / 2, y0, cz - dot_h / 2), (cx + dot_w / 2, y1, cz + dot_h / 2), pattern_c)
	elif pattern == "color_block":
		idx = _box(vertex, normal, color, tris, idx, (x0, y0, z0), (x1, y1, z0 + height / 2), pattern_c)
	return idx


def _hex_to_rgba(h):
	h = h.lstrip("#")
	r, g, b = (int(h[i : i + 2], 16) / 255 for i in (0, 2, 4))
	return (r, g, b, 1)


def _wedge_x(vertex, normal, color, tris, idx, x0, x1, y_back, y_front, z0, z_top, col):
	"""A right-triangular prism extruded along X, per-face flat-shaded
	like _box - the right angle sits at (y_back, z0): a vertical back
	face (y=y_back, spanning z0..z_top) meets a flat bottom face
	(z=z0, spanning y_front..y_back) at 90 degrees, and the hypotenuse
	(the sloped face) runs from the top of that back edge down to the
	front tip sitting on the ground - a real shoe toe's own pointed
	slope, not another flat-fronted box. `y_back` must be the more
	positive (further back) of the two y values, `y_front` the toe tip;
	z_top must be > z0."""
	dy, dz = y_back - y_front, z_top - z0
	mag = (dy * dy + dz * dz) ** 0.5
	slope_n = (0.0, -dz / mag, dy / mag)
	faces = [
		((0, 1, 0), [(x0, y_back, z0), (x0, y_back, z_top), (x1, y_back, z_top), (x1, y_back, z0)]),
		((0, 0, -1), [(x0, y_back, z0), (x1, y_back, z0), (x1, y_front, z0), (x0, y_front, z0)]),
		(slope_n, [(x0, y_back, z_top), (x0, y_front, z0), (x1, y_front, z0), (x1, y_back, z_top)]),
		((-1, 0, 0), [(x0, y_back, z0), (x0, y_front, z0), (x0, y_back, z_top)]),
		((1, 0, 0), [(x1, y_back, z0), (x1, y_back, z_top), (x1, y_front, z0)]),
	]
	for n, pts in faces:
		for p in pts:
			vertex.addData3(*p)
			normal.addData3(*n)
			color.addData4(*col)
		if len(pts) == 4:
			tris.addVertices(idx, idx + 1, idx + 2)
			tris.addVertices(idx, idx + 2, idx + 3)
			idx += 4
		else:
			tris.addVertices(idx, idx + 1, idx + 2)
			idx += 3
	return idx


def _shade(col, factor):
	"""Darkens (factor<1) or lightens (factor>1, clamped to 1.0) an RGBA
	color for accent details (seams, hems) that need to visibly read as
	a different shade of the same garment color rather than a random
	unrelated one."""
	return tuple(min(1.0, max(0.0, c * factor)) for c in col[:3]) + (col[3],)


# Age also drives real body-PROPORTION scaling below ADULT_AGE, not just
# the elderly-only wrinkle/graying effects above it (see _build_face's
# own ELDERLY_AGE) - a real child isn't just a small adult: the head
# shrinks much less than the body does as age drops, the same real
# anatomical fact behind why a toddler's head reads as a much bigger
# fraction of their own height than an adult's. Dimensions.com's own
# child/toddler reference pages are still marked "under development"
# there (no published numbers to check against, unlike every other
# proportion question this project defers to that site for) - these two
# ratios are reasoned instead from the standard "head-unit" figure-
# proportion convention character-design/animation reference material
# already uses (a toddler reading at roughly half an adult's own
# torso+leg height with a barely-shrunk head, scaling smoothly toward a
# school-age child, then a teen, as age rises back toward ADULT_AGE).
# Scope cut, deliberate: this scales HEIGHT (LEG_H/TORSO_H/HEAD_S) and
# WIDTH (chest/hip/limb half-widths) - the two dimensions that actually
# read as "child-sized" from the normal camera angle - not the dozens
# of fixed absolute front-to-back DEPTH offsets threaded through the
# rest of this file (torso thickness, hand/foot depth, ...), which stay
# at their adult size regardless of age. Reproportioning every one of
# those individually to also shrink with age would be a real second
# project on the scale of the head's own `hs` rescale, not attempted
# here - a young character reads correctly shorter/narrower/bigger-
# headed at a glance, just not fully depth-correct in profile.
ADULT_AGE = 0.5
CHILD_HEAD_SCALE_MIN = 0.72  # a toddler's head, at its smallest here, is still ~72% of an adult's
CHILD_BODY_SCALE_MIN = 0.5  # a toddler's own torso+legs shrink much further - to half an adult's


def _age_body_scale(age):
	"""(head_scale, body_scale) multipliers - both 1.0 for any age at or
	above ADULT_AGE (0.5, the existing neutral-adult default every
	current caller keeps getting unless it opts into a younger age),
	easing down toward CHILD_HEAD_SCALE_MIN/CHILD_BODY_SCALE_MIN as age
	drops toward 0 (a toddler). Shared by build_character and
	avatar3d_rig.build_rig - both apply it to the exact same LEG_H/
	TORSO_H/HEAD_S/chest_half_w/hip_half_w/limb_half_w constants, so a
	profile's own age reads the same silhouette in a static portrait and
	in the live rig, not two independently-drifting copies of this same
	math (the same "share it, don't duplicate it" reasoning this file's
	own _build_hair_and_toppers/_build_face extractions already
	established for the head)."""
	if age >= ADULT_AGE:
		return 1.0, 1.0
	t = age / ADULT_AGE  # 1.0 at ADULT_AGE, 0.0 at a newborn/toddler extreme
	head_scale = CHILD_HEAD_SCALE_MIN + (1.0 - CHILD_HEAD_SCALE_MIN) * t
	body_scale = CHILD_BODY_SCALE_MIN + (1.0 - CHILD_BODY_SCALE_MIN) * t
	return head_scale, body_scale


def _build_leg_pants(vertex, normal, color, tris, idx, side, hip_half_w, limb_half_w, pants_style, leg_type, missing_leg, skin, pants, shoes, LEG_H):
	"""One side of the "pants" bottom_style leg - straight/skinny/baggy/
	cropped width variants, digitigrade hock spur, floor-level shoe box.
	Extracted verbatim from build_character's own "pants" branch (zero
	behavior change) so avatar_rig.py's rig builder can reuse the exact
	same geometry math instead of re-deriving it. No-ops (returns idx
	unchanged) when this side is missing (missing_leg)."""
	side_name = "left" if side == -1 else "right"
	if missing_leg in (side_name, "both"):
		return idx
	leg_w = {"skinny": limb_half_w * 0.72, "baggy": limb_half_w * 1.45}.get(pants_style, limb_half_w)
	hem_z = LEG_H * 0.42 if pants_style == "cropped" else 0.25
	lx0 = side * hip_half_w * 0.5 - leg_w
	idx = _box(vertex, normal, color, tris, idx, (lx0, -0.2, hem_z), (lx0 + leg_w * 2, 0.2, LEG_H), pants)
	if pants_style == "cropped":
		idx = _box(vertex, normal, color, tris, idx, (lx0, -0.15, 0.25), (lx0 + leg_w * 2, 0.15, hem_z), skin)
	# Thick ankle block + a small mid-step + a right-triangle toe wedge
	# - see _build_foot's own docstring for the full history (hook look
	# -> thick-ankle correction -> mid-step follow-up -> lengthened
	# mid-step + pointed toe wedge) and for the shift further forward.
	idx = _box(vertex, normal, color, tris, idx, (lx0, MID_STEP_Y, 0), (lx0 + leg_w * 2, ANKLE_BACK_Y, 0.25), shoes)
	idx = _box(vertex, normal, color, tris, idx, (lx0, TOE_STEP_Y, 0), (lx0 + leg_w * 2, MID_STEP_Y, 0.25 * 0.55), shoes)
	idx = _wedge_x(vertex, normal, color, tris, idx, lx0, lx0 + leg_w * 2, TOE_STEP_Y, TOE_FRONT_Y, 0, 0.25 * 0.55, shoes)
	if leg_type == "digitigrade":
		idx = _leg_hock_spur(vertex, normal, color, tris, idx, lx0, leg_w * 2, skin, back_y=ANKLE_BACK_Y)
	return idx


def _build_leg_thigh(vertex, normal, color, tris, idx, side, hip_half_w, limb_half_w, pants_style, missing_leg, LEG_H, knee_z, pants, accent_c=None, pattern="solid", pattern_c=None, skin=None, bare_hem_z=None):
	"""Thigh segment (hip to knee) of a jointed leg - straight/skinny/
	baggy width variants, extracted verbatim from build_character's own
	"pants" branch (zero behavior change) so avatar_rig.py's rig builder
	can reuse the exact same geometry math instead of re-deriving it.
	No-ops (returns idx unchanged) when this side is missing. `accent_c`
	(the seam color) defaults to a darker shade of `pants` when not
	given explicitly. `pattern`/`pattern_c` are _apply_fabric_pattern's
	own - see its docstring.

	`bare_hem_z`, when given and above this segment's own knee_z, is
	the "shorts" bottom_style's own thigh_z (see build_character's own
	shorts branch - shorts_style picks how high the bare hem sits, and
	unlike pants_style="cropped" - which always falls below the knee,
	entirely _build_leg_shin's own problem - "bermuda" shorts can reach
	high enough to cut into the THIGH segment too), splitting this
	segment into bare skin below it and pants fabric above; when None
	or at/below knee_z (the cut falls in the shin instead, or there's
	no cut at all), this segment is unaffected - full pants, exactly as
	before."""
	side_name = "left" if side == -1 else "right"
	if missing_leg in (side_name, "both"):
		return idx
	accent_c = accent_c if accent_c is not None else _shade(pants, 0.7)
	leg_w = {"skinny": limb_half_w * 0.72, "baggy": limb_half_w * 1.45}.get(pants_style, limb_half_w)
	lx0 = side * hip_half_w * 0.5 - leg_w
	bare = bare_hem_z is not None and bare_hem_z > knee_z
	pants_z0 = bare_hem_z if bare else knee_z
	idx = _box(vertex, normal, color, tris, idx, (lx0, -0.2, pants_z0), (lx0 + leg_w * 2, 0.2, LEG_H), pants)
	idx = _apply_fabric_pattern(vertex, normal, color, tris, idx, lx0, lx0 + leg_w * 2, -0.2, pants_z0, LEG_H, pattern, pattern_c)
	if bare:
		idx = _box(vertex, normal, color, tris, idx, (lx0, -0.15, knee_z), (lx0 + leg_w * 2, 0.15, bare_hem_z), skin)
	# Outer side seam - a real trouser detail (the panels making up each
	# leg are sewn together along this exact line), not a random accent:
	# `outer_x` is whichever edge sits farther from the body's own
	# centerline (x=0) - lx0 itself for the left leg (side=-1, whose own
	# lx0 is already the more-negative/farther edge), the opposite edge
	# for the right. Only spans the actual pants portion (pants_z0..LEG_H)
	# - no seam on bare skin.
	outer_x = lx0 if side == -1 else lx0 + leg_w * 2
	idx = _seam_line(vertex, normal, color, tris, idx, outer_x, side, pants_z0, LEG_H, accent_c)
	return idx


def _build_leg_shin(vertex, normal, color, tris, idx, side, hip_half_w, limb_half_w, pants_style, missing_leg, LEG_H, knee_z, ankle_z, pants, skin, accent_c=None, stitch_c=None, pattern="solid", pattern_c=None, bare_hem_z=None):
	"""Shin segment (knee to ankle) of a jointed leg - a "cropped" style
	bares skin down to the ankle (relocated here from _build_leg_pants's
	single-segment version, since the shin is where a cropped hem
	actually falls now). `accent_c`/`stitch_c` match _build_leg_thigh's
	own `accent_c` (seam+cuff color) and _hem_band's own `stitch_c`.
	`pattern`/`pattern_c` are _apply_fabric_pattern's own - skipped for
	"cropped" (there's bare skin below the knee, no pants fabric there
	to pattern).

	`bare_hem_z` is _build_leg_thigh's own identical "shorts" param -
	when it falls at/below this segment's own knee_z (the common case -
	only "bermuda" shorts reach high enough to land in the thigh
	instead, that segment's own problem), the shin is bare skin from
	there down to the ankle, same "cropped"-style bare-skin branch just
	driven by a different, shorts-specific height instead of the fixed
	LEG_H*0.42 "cropped" uses. `socks` used to draw a colored band just
	above the shoe here - fully moved out to
	avatar_plugins_builtin/bodypart/socks/ (reparented onto
	rig.knee_l/r, the same pivot this segment's own geometry hangs off
	of - see that plugin's own docstring)."""
	side_name = "left" if side == -1 else "right"
	if missing_leg in (side_name, "both"):
		return idx
	accent_c = accent_c if accent_c is not None else _shade(pants, 0.7)
	leg_w = {"skinny": limb_half_w * 0.72, "baggy": limb_half_w * 1.45}.get(pants_style, limb_half_w)
	lx0 = side * hip_half_w * 0.5 - leg_w
	is_bare = pants_style == "cropped" or bare_hem_z is not None
	if bare_hem_z is not None:
		hem_z = min(bare_hem_z, knee_z)
	else:
		hem_z = LEG_H * 0.42 if pants_style == "cropped" else ankle_z
	hem_z = max(hem_z, ankle_z)
	if is_bare:
		# A "bermuda" shorts cut above knee_z already drew this segment's
		# entire pants portion over in _build_leg_thigh - nothing left
		# for this segment to add there, just the bare skin below.
		thigh_already_bare = bare_hem_z is not None and bare_hem_z > knee_z
		if not thigh_already_bare:
			idx = _box(vertex, normal, color, tris, idx, (lx0, -0.2, hem_z), (lx0 + leg_w * 2, 0.2, knee_z), pants)
		idx = _box(vertex, normal, color, tris, idx, (lx0, -0.15, ankle_z), (lx0 + leg_w * 2, 0.15, hem_z), skin)
	else:
		# Pants box shrunk by CUFF_HEM_H off its own bottom, so the cuff
		# hem band below fills that exact freed slice instead of
		# overlapping it (see _hem_band's own docstring for why the
		# first version's overlapping approach z-fought).
		idx = _box(vertex, normal, color, tris, idx, (lx0, -0.2, hem_z + CUFF_HEM_H), (lx0 + leg_w * 2, 0.2, knee_z), pants)
		idx = _apply_fabric_pattern(vertex, normal, color, tris, idx, lx0, lx0 + leg_w * 2, -0.2, hem_z + CUFF_HEM_H, knee_z, pattern, pattern_c)
		# Same outer side seam as the thigh (see _build_leg_thigh's own
		# comment) - only drawn when this segment is actually pants down
		# to hem_z (a "cropped" leg bares skin below the knee instead,
		# nothing to seam there). Spans the full original z-range
		# (through both the pants box above and the cuff hem below) -
		# safe to do since the seam is now purely external in x, never
		# overlapping either of them.
		outer_x = lx0 if side == -1 else lx0 + leg_w * 2
		idx = _seam_line(vertex, normal, color, tris, idx, outer_x, side, hem_z, knee_z, accent_c)
		idx = _hem_band(vertex, normal, color, tris, idx, lx0, lx0 + leg_w * 2, -0.2, 0.2, hem_z, hem_z + CUFF_HEM_H, accent_c, stitch_c=stitch_c)
	return idx


ANKLE_BACK_Y = 0.2  # heel/ankle block's own back face - matches the shin's own back face (_build_leg_shin's box is (lx0,-0.2,...) to (...,0.2,...)) exactly, a real, confirmed-live correction: at 0.16 there was a small backward step-in where the ankle sat slightly forward of the calf's own back line instead of continuing it, rather than a real leg's own flush, continuous line down the back with the curve only showing up at the front where the foot steps out past it
MID_STEP_Y = -0.10  # boundary between the thick ankle block and the small step below it - pulled forward from 0.05 per direct request ("stretch the ankle to right before the toe wedge"), so the full-height ankle block now runs almost the whole way to the wedge and only a slight (MID_STEP_Y to TOE_STEP_Y) right-angle notch remains right at that junction, reading as a curve down into the toe rather than one long flat mid-foot shelf
TOE_STEP_Y = -0.15  # boundary between the small step and the toe wedge - lengthened further forward (was -0.03) per direct request
TOE_FRONT_Y = -0.34  # toe tip - the wedge's own pointed front, sitting flush on the ground (z=0)


def _build_foot(vertex, normal, color, tris, idx, side, hip_half_w, limb_half_w, pants_style, leg_type, missing_leg, ankle_z, shoes, skin):
	"""Foot segment (below the ankle) of a jointed leg - a thick ankle
	block (heel through mid-foot, one uniform full-height mass - a real,
	confirmed-live correction from an earlier 3-step version that
	stepped down TWICE at roughly equal size, which read as a foot with
	no real ankle mass at all), then a small intermediate mid-step (a
	real, confirmed-live follow-up request - a single sharp drop
	straight to the toe read too abrupt once the ankle itself was fixed)
	down into the toe block proper. The mid-step is deliberately small
	(MID_STEP_Y to TOE_STEP_Y is a much narrower band than either the
	ankle or the toe) - a transition, not a third co-equal segment the
	way the original 3-step version's mid band was. Also shifted more
	forward overall (ANKLE_BACK_Y pulled in from the old heel's own
	0.24, TOE_FRONT_Y pushed out from the old toe's own -0.22) - a real
	foot's ankle sits well back of its own overall length, not
	centered, so the toe needs to reach out further than the heel
	reaches back. Plus the digitigrade hock spur, relocated here from
	_build_leg_pants's single-segment version so the foot can pivot
	independently at the ankle.

	`paws`/`claws` used to draw a paw-pad overlay and claw tips right
	here, purely additively (poking out over whatever shoe/bare-foot
	geometry is already drawn above, never gated on anything this
	function itself tracks) - both fully moved out to their own bodypart
	plugins (avatar_plugins_builtin/bodypart/paws|claws/), reparented
	onto rig.ankle_l/r, the same pivot this segment's own geometry hangs
	off of."""
	side_name = "left" if side == -1 else "right"
	if missing_leg in (side_name, "both"):
		return idx
	leg_w = {"skinny": limb_half_w * 0.72, "baggy": limb_half_w * 1.45}.get(pants_style, limb_half_w)
	lx0 = side * hip_half_w * 0.5 - leg_w
	toe_z = ankle_z * 0.55
	mid_z = toe_z  # a real, confirmed-live correction: at its own separate 0.78 ratio, the mid-step's own top didn't match the wedge's own back height, leaving a hidden second step right where the slope was meant to start
	idx = _box(vertex, normal, color, tris, idx, (lx0, MID_STEP_Y, 0), (lx0 + leg_w * 2, ANKLE_BACK_Y, ankle_z), shoes)
	idx = _box(vertex, normal, color, tris, idx, (lx0, TOE_STEP_Y, 0), (lx0 + leg_w * 2, MID_STEP_Y, mid_z), shoes)
	# Toe wedge - a real right-triangle prism (see _wedge_x's own
	# docstring), not a box: the front of the foot now comes to an
	# actual sloped point at the ground instead of a flat vertical
	# front face, per direct request.
	idx = _wedge_x(vertex, normal, color, tris, idx, lx0, lx0 + leg_w * 2, TOE_STEP_Y, TOE_FRONT_Y, 0, toe_z, shoes)
	if leg_type == "digitigrade":
		idx = _leg_hock_spur(vertex, normal, color, tris, idx, lx0, leg_w * 2, skin, back_y=ANKLE_BACK_Y)
	return idx


def _build_torso(
	vertex, normal, color, tris, idx, LEG_H, TORSO_H, HEAD_S, hip_half_w, chest_half_w, skin, shirt,
	shirt_accent_c, stitch_c, shirt_pattern="solid", shirt_pattern_c=None, cropped=False,
	top_style="long_sleeve", hood_up=False, drawstring_c=None, bottom_style="pants", pants=None,
	accessories=(),
):
	"""The whole static (non-posable) torso silhouette - hip/waist/chest
	bands (with "cropped"'s own bare-midriff recolor), fabric pattern,
	hem, and every top_style's own collar/hood/lapel/turtleneck neckline
	geometry, plus the "overalls" bottom_style's own bib+straps.
	backpack/satchel/purse/necklace/tie all moved out to their own files
	under avatar_plugins_builtin/bodypart/ (see avatar_plugin_backend.py's
	own module docstring for the plugin format) - this function no
	longer draws any torso-worn accessory itself at all, they're each
	their own built-in "bodypart" feature now - extracted from
	build_character's own original torso code (zero behavior change
	there) so avatar3d_rig.build_rig() can share this
	exact silhouette on its own static torso buffer instead of the
	stripped-down hip/waist/chest-only version it used to draw on its
	own - a real, direct request ("add everything else we did in
	scratchpad with clothing, hair, accessories, etc.") to bring the
	animated rig up to the static path's own full richness here, the
	same way an earlier pass already did for the head (hairstyle/
	eye_shape/nose_shape/mouth_style/facial_hair/...).

	Arm sleeves/bare-skin (also top_style-driven) are NOT here - those
	attach to POSABLE shoulder/elbow joints in the rig, out of a single
	static-torso extraction's own scope; see _build_arm_upper/
	_build_arm_forearm's own top_style handling for that half, and
	_build_leg_thigh/_build_leg_shin's own bare_hem_z for the
	equivalent "shorts" bottom_style handling on the legs. skirt/dress
	bottom_style is deliberately NOT here either - build_rig()'s own
	docstring explains why those two stay static-portrait-only."""
	hip_top_z = LEG_H + TORSO_H * 0.22
	waist_z = LEG_H + TORSO_H * 0.45
	waist_half_w = min(hip_half_w, chest_half_w) * 0.84
	lower_torso_color = skin if cropped else shirt
	hip_z0 = LEG_H if cropped else LEG_H + SHIRT_HEM_H
	idx = _box(vertex, normal, color, tris, idx, (-hip_half_w, -0.27, hip_z0), (hip_half_w, 0.27, hip_top_z), lower_torso_color)
	idx = _box(vertex, normal, color, tris, idx, (-waist_half_w, -0.245, hip_top_z), (waist_half_w, 0.245, waist_z), lower_torso_color)
	idx = _box(vertex, normal, color, tris, idx, (-chest_half_w, -0.26, waist_z), (chest_half_w, 0.26, LEG_H + TORSO_H), shirt)
	idx = _apply_fabric_pattern(vertex, normal, color, tris, idx, -chest_half_w, chest_half_w, -0.26, waist_z, LEG_H + TORSO_H, shirt_pattern, shirt_pattern_c)
	if not cropped:
		# Shirt hem - the real finished bottom edge of the shirt, right
		# where it meets the pants, plus its own stitch marks. Skipped
		# when cropped: there's no hem on bare midriff skin.
		idx = _apply_fabric_pattern(vertex, normal, color, tris, idx, -hip_half_w, hip_half_w, -0.27, hip_z0, hip_top_z, shirt_pattern, shirt_pattern_c)
		idx = _apply_fabric_pattern(vertex, normal, color, tris, idx, -waist_half_w, waist_half_w, -0.245, hip_top_z, waist_z, shirt_pattern, shirt_pattern_c)
		idx = _hem_band(vertex, normal, color, tris, idx, -hip_half_w, hip_half_w, -0.27, 0.27, LEG_H, LEG_H + SHIRT_HEM_H, shirt_accent_c, stitch_c=stitch_c)

	collar_z0 = LEG_H + TORSO_H
	if top_style == "collared":
		for side in (-1, 1):
			flap_x0, flap_x1 = sorted((side * 0.04, side * 0.24))
			idx = _outlined_box(vertex, normal, color, tris, idx, (flap_x0, -0.29, collar_z0 - 0.16), (flap_x1, -0.14, collar_z0 - 0.005), shirt)

	if top_style == "hoodie":
		if hood_up:
			# The hood pulled up and over the head - the same "poke out
			# past the head/hair's own front face" technique as the
			# "hat" accessory uses.
			hood_bottom = collar_z0 + HEAD_S * 0.58
			hood_top = collar_z0 + HEAD_S + 0.12
			idx = _outlined_box(
				vertex, normal, color, tris, idx,
				(-HEAD_S / 2 - 0.09, -HEAD_S / 2 - 0.02, hood_bottom), (HEAD_S / 2 + 0.09, HEAD_S / 2 + 0.09, hood_top), shirt,
			)
		else:
			idx = _outlined_box(
				vertex, normal, color, tris, idx,
				(-chest_half_w * 0.55, -0.05, collar_z0), (chest_half_w * 0.55, 0.32, collar_z0 + 0.18), shirt,
			)
		# Drawstrings - two thin cords hanging down the front, regardless
		# of hood_up state.
		for side in (-1, 1):
			sx = side * 0.045
			idx = _outlined_box(
				vertex, normal, color, tris, idx,
				(sx - 0.012, -0.28, collar_z0 - 0.35), (sx + 0.012, -0.22, collar_z0 - 0.08), drawstring_c,
			)
			idx = _outlined_box(
				vertex, normal, color, tris, idx,
				(sx - 0.02, -0.29, collar_z0 - 0.4), (sx + 0.02, -0.21, collar_z0 - 0.33), drawstring_c,
			)

	if top_style == "jacket":
		idx = _outlined_box(
			vertex, normal, color, tris, idx,
			(-chest_half_w * 0.55, -0.05, collar_z0), (chest_half_w * 0.55, 0.32, collar_z0 + 0.18), shirt,
		)
		for side in (-1, 1):
			lapel_x0, lapel_x1 = sorted((side * chest_half_w * 0.2, side * (chest_half_w + 0.06)))
			idx = _outlined_box(
				vertex, normal, color, tris, idx,
				(lapel_x0, -0.32, collar_z0 - 0.32), (lapel_x1, -0.1, collar_z0 - 0.02), shirt,
			)

	if top_style == "turtleneck":
		idx = _outlined_box(
			vertex, normal, color, tris, idx,
			(-chest_half_w * 0.5, -0.24, collar_z0 - 0.14), (chest_half_w * 0.5, 0.24, collar_z0 - 0.005), shirt,
		)

	if top_style == "vest":
		for side in (-1, 1):
			lapel_x0, lapel_x1 = sorted((side * chest_half_w * 0.15, side * (chest_half_w + 0.03)))
			idx = _outlined_box(
				vertex, normal, color, tris, idx,
				(lapel_x0, -0.29, collar_z0 - 0.45), (lapel_x1, -0.12, collar_z0 - 0.06), shirt,
			)

	if bottom_style == "overalls":
		idx = _outlined_box(
			vertex, normal, color, tris, idx,
			(-hip_half_w * 0.75, -0.29, LEG_H), (hip_half_w * 0.75, -0.2, LEG_H + TORSO_H * 0.55), pants,
		)
		for side in (-1, 1):
			strap_x0, strap_x1 = sorted((side * hip_half_w * 0.55, side * hip_half_w * 0.72))
			idx = _outlined_box(
				vertex, normal, color, tris, idx,
				(strap_x0, -0.28, LEG_H + TORSO_H * 0.5), (strap_x1, 0.3, LEG_H + TORSO_H * 0.98), pants,
			)

	return idx


def _build_wheelchair_frame(vertex, normal, color, tris, idx, hip_half_w, limb_half_w, missing_leg, bottom_style, shirt, pants, shoes, wheelchair_c, LEG_H, HEAD_S, TORSO_H):
	"""The wheelchair's seated legs/footrests/seat/backrest/handles -
	everything from build_character's own `if wheelchair:` block EXCEPT
	the wheels themselves (see _build_wheel, which needs its own pivot
	for spinning and so stays separate). Extracted verbatim - zero
	behavior change - so avatar_rig.py can reuse this exact geometry for
	the static (unanimated) part of a wheelchair rig."""
	leg_thick = limb_half_w * 2
	thigh_z1 = LEG_H
	thigh_z0 = thigh_z1 - leg_thick
	hip_y = -0.2
	knee_y = -0.6
	footrest_z = 0.12
	foot_len = 0.22
	leg_color = shirt if bottom_style == "dress" else pants
	for side in (-1, 1):
		side_name = "left" if side == -1 else "right"
		if missing_leg in (side_name, "both"):
			continue
		lx0 = side * hip_half_w * 0.5 - limb_half_w
		# Thigh - horizontal, hip to knee, at a fixed (hip) height.
		idx = _outlined_box(
			vertex, normal, color, tris, idx,
			(lx0, knee_y, thigh_z0), (lx0 + limb_half_w * 2, hip_y, thigh_z1), leg_color,
		)
		# Shin - vertical, straight down from the knee to the footrest,
		# at a fixed (knee) Y position - a real corner with the thigh
		# above, not a diagonal guess.
		idx = _outlined_box(
			vertex, normal, color, tris, idx,
			(lx0, knee_y - leg_thick / 2, footrest_z), (lx0 + limb_half_w * 2, knee_y + leg_thick / 2, thigh_z0), leg_color,
		)
		# Foot - a flat slab pointing forward from the ankle, sitting ON
		# TOP of the footrest plate below.
		idx = _outlined_box(
			vertex, normal, color, tris, idx,
			(lx0 - 0.02, knee_y - leg_thick / 2 - foot_len, footrest_z), (lx0 + limb_half_w * 2 + 0.02, knee_y - leg_thick / 2, footrest_z + 0.06), shoes,
		)
		# Footrest plate - a real flat plate the foot rests on, not just
		# a shoe floating at the right height.
		idx = _outlined_box(
			vertex, normal, color, tris, idx,
			(lx0 - 0.05, knee_y - leg_thick / 2 - foot_len - 0.03, footrest_z - 0.03), (lx0 + limb_half_w * 2 + 0.05, knee_y + leg_thick / 2 + 0.02, footrest_z), wheelchair_c,
		)
	# Both the seat and the backrest/handles below must stay narrower
	# than the wheel's own INNER edge - a real, confirmed-live bug:
	# growing the wheels' radius/thickness (per direct feedback they
	# read as "too small") made the wheel visibly bigger everywhere BUT
	# its inner edge, which didn't move with it - so the seat/backrest,
	# whose own widths already reached past that inner edge before,
	# went from a minor/unnoticeable overlap to a real, visible "wheel
	# clipping into the handles" once the wheel's now-much-larger radius
	# made that inner-edge region actually visible on screen. Computed
	# by calling _wheel_geometry itself (not a separately-tuned copy of
	# its gap constant) so this can never drift out of sync with the
	# wheel's own real position again, the same fix already applied to
	# avatar_rig.py's pivot placement.
	wx_probe, _, _, wheel_thick_probe, _ = _wheel_geometry(hip_half_w, LEG_H, 1)
	wheel_inner_x = wx_probe - wheel_thick_probe / 2
	seat_half_w = min(hip_half_w + 0.14, wheel_inner_x - 0.02)
	seat_top = thigh_z0
	# The seat's front edge extends forward past hip_y (matching the
	# thighs' own Y-range) rather than stopping flush with them - direct
	# feedback that the seat was entirely hidden behind the thighs from
	# every normal viewing angle (the thighs' own front face sits at
	# knee_y=-0.6, far more forward than the seat's old front at
	# hip_y=-0.2, so nothing of the seat ever showed at all). Since the
	# seat is wider than the thighs, this reach makes it visible as real
	# cushion "wings" flanking the legs instead of a purely theoretical
	# shape nothing ever reveals.
	idx = _outlined_box(
		vertex, normal, color, tris, idx,
		(-seat_half_w, -0.4, seat_top - 0.1), (seat_half_w, 0.15, seat_top), wheelchair_c,
	)
	# Backrest - only needs to clear the ARMS (which end exactly at
	# shoulder height, LEG_H+TORSO_H) to be visible at all, not the
	# whole head above that - a real, direct correction: the previous
	# version cleared the TOP of the head by a further 0.35, towering
	# well above shoulder height and reading as "sitting on a hand
	# truck" rather than a wheelchair (real wheelchair backrests sit at
	# roughly shoulder height, not above the head). A modest rise just
	# past the shoulder line is enough - there's nothing at that height
	# to occlude it either way, since the arms don't extend above
	# shoulder height.
	back_half_w = min(HEAD_S / 2 + 0.1, wheel_inner_x - 0.03)
	shoulder_z = LEG_H + TORSO_H
	backrest_top = shoulder_z + 0.15
	idx = _outlined_box(
		vertex, normal, color, tris, idx,
		(-back_half_w, 0.27, seat_top), (back_half_w, 0.42, backrest_top), wheelchair_c,
	)
	# Push handles - rising a bit further still above the backrest,
	# visible flanking the lower head/neck from the front.
	for side in (-1, 1):
		hx = side * (back_half_w - 0.04)
		idx = _outlined_box(
			vertex, normal, color, tris, idx,
			(hx - 0.035, 0.3, backrest_top - 0.05), (hx + 0.035, 0.55, backrest_top + 0.15), wheelchair_c,
		)
	return idx


def _wheel_geometry(hip_half_w, LEG_H, side):
	"""The wheelchair wheel's own real center/size - (wx, wheel_cy,
	wheel_cz, wheel_thick, wheel_r) - as its OWN function, called by
	BOTH _build_wheel (which draws the wheel here) and avatar_rig.py
	(which needs the exact same values to place the spinning pivot at
	the wheel's true center). A real, confirmed-live bug happened when
	these values were duplicated as two independent copies (one here,
	one in avatar_rig.py) and drifted out of sync after a size change -
	the wheel visibly orbited around the wrong point instead of
	spinning in place, since the pivot sat at the OLD center while the
	geometry was built around the NEW one. One shared function makes
	that class of bug impossible instead of just fixing it once."""
	wheel_bottom, wheel_top = 0.0, LEG_H * 1.15
	wheel_r = (wheel_top - wheel_bottom) / 2
	wheel_cz = (wheel_top + wheel_bottom) / 2
	# Thinner than the size pass that grew the radius - a real, direct
	# follow-up: a wide, thick wheel visually swallowed the seat sitting
	# right next to it from most angles. Thinning it (independent of the
	# inner-edge position, which the wx formula below keeps fixed at
	# hip_half_w+0.02 regardless of thickness) shrinks the wheel's own
	# visual mass without moving it any closer to the seat, letting the
	# seat actually read as its own real shape instead of being hidden
	# behind the wheel.
	wheel_thick = 0.16
	wheel_cy = 0.0
	# A real gap between the wheel and the seat/frame - direct feedback
	# that a near-zero gap (0.02) read as too compact, unlike a real
	# manual wheelchair's own real axle clearance. WHEEL_MOUNT_GAP is
	# the single source of truth for this distance - _build_wheelchair_
	# frame calls this same function (rather than keeping its own
	# separate copy of the gap) specifically so its seat/backrest width
	# clamps can never drift out of sync with this value again.
	WHEEL_MOUNT_GAP = 0.18
	wx = side * (hip_half_w + WHEEL_MOUNT_GAP + wheel_thick / 2)
	return wx, wheel_cy, wheel_cz, wheel_thick, wheel_r


def _build_wheel(vertex, normal, color, tris, idx, side, hip_half_w, LEG_H, tire_c, wheelchair_c):
	"""One wheelchair wheel (tire + metal rim + hub) - a real, round
	disc (axle along X, matching this file's own established "a real
	wheel's axle points left-right" reasoning), NOT a box. Direct,
	blunt feedback after the boxy version shipped: "why the fuck are
	the wheels square?" - _disc_x gives an actually circular wheel
	instead. Two-tone: tire_c for the rubber (the main disc), wheelchair_c
	for the rim/hub metal (two smaller discs on the outer face, poking
	out past the tire's own end cap by a small margin - same "poke out
	past what's behind it" rule as everywhere else in this file - so
	they're never coplanar/z-fighting with it). Direct follow-up
	feedback: even round, the wheels still read as too small ("sitting
	on a hand truck") - grown further here, both in radius (down almost
	to the ground, up well past hip height, like a real manual
	wheelchair's large drive wheels) and thickness (more front-view
	visual bulk) - see _wheel_geometry for the actual numbers."""
	wx, wheel_cy, wheel_cz, wheel_thick, wheel_r = _wheel_geometry(hip_half_w, LEG_H, side)
	idx = _disc_x(vertex, normal, color, tris, idx, wx, wheel_cy, wheel_cz, wheel_thick, wheel_r, tire_c)
	outer_face_x = wx + side * (wheel_thick / 2 + 0.012)
	rim_r = wheel_r * 0.55
	idx = _disc_x(vertex, normal, color, tris, idx, outer_face_x, wheel_cy, wheel_cz, 0.02, rim_r, wheelchair_c)
	hub_r = wheel_r * 0.25
	idx = _disc_x(vertex, normal, color, tris, idx, outer_face_x + side * 0.015, wheel_cy, wheel_cz, 0.02, hub_r, wheelchair_c)
	return idx


def _build_arm_upper(vertex, normal, color, tris, idx, side, chest_half_w, limb_half_w, missing_arm, prosthetics, shirt, prosthetic_c, elbow_z, shoulder_z, y_offset=0.0, pattern="solid", pattern_c=None, skin=None, top_style="long_sleeve"):
	"""Upper-arm segment (shoulder to elbow) of a jointed arm - a
	standalone extraction, NOT wired into build_character. Exists purely
	for avatar_rig.py's rig builder. `y_offset` shifts the whole segment
	toward the back (+y) - see avatar_rig.py's own ARM_Y_OFFSET for why:
	the real glenohumeral joint sits at the outer tip of the shoulder
	BLADE, a back bone, not centered front-to-back on the torso the way
	this rig's arms used to attach (checked against dimensions.com's own
	standing-figure reference rather than guessed, per direct
	instruction to use it for every future 3D-modeling proportion
	question).

	`top_style` decides whether this segment is sleeve (shirt) or bare
	(skin) - matches build_character's own identical top_style/arm
	logic exactly (tank_top/vest bare the WHOLE arm, every other style
	covers the upper arm; only "short_sleeve" splits at the elbow,
	which _build_arm_forearm's own top_style handling covers), added
	per direct request to give the rig the same clothing richness the
	static path already had (`skin` only needs to be real when a bare
	top_style is actually in play - every existing caller that never
	passes a bare top_style can keep omitting it, matching every other
	"only matters for a specific branch" default in this file)."""
	side_name = "left" if side == -1 else "right"
	if missing_arm in (side_name, "both"):
		return idx
	outer = side * (chest_half_w + limb_half_w * 2)
	inner = side * chest_half_w
	lo_x, hi_x = min(inner, outer), max(inner, outer)
	is_prosthetic = f"{side_name}_arm" in prosthetics
	bare = top_style in ("tank_top", "vest")
	color_c = prosthetic_c if is_prosthetic else (skin if bare else shirt)
	# Shoulder cap: without this the arm shaft just starts flush against
	# the torso's side wall at its own (much thinner) width, reading as
	# a stick poking out of a box with no shoulder at all. A wider,
	# deeper block spanning the top of the upper-arm segment gives an
	# actual shoulder silhouette, same blocky/pauldron language as the
	# rest of this rig's style. Kept flush with the torso's own x/z
	# boundary (chest_half_w, shoulder_z) rather than overlapping into
	# it - the cap rotates with the shoulder pivot but the torso doesn't,
	# so an inward overlap that looks fine at rest exposes its own
	# outline backing box (a real, confirmed-live bug) the instant the
	# arm swings and the overlap no longer lines up with the static torso.
	cap_h = 0.16
	cap_outer = side * (chest_half_w + limb_half_w * 2 + 0.07)
	cap_inner = side * chest_half_w
	cap_lo_x, cap_hi_x = min(cap_inner, cap_outer), max(cap_inner, cap_outer)
	# The outer (away-from-torso) x-end of a limb is a THIRD flush case,
	# same bug family as flush_z1 above but triggered by camera angle
	# instead of joint rotation: viewed from near-profile (looking down
	# this box's own x axis, e.g. orbiting the live player to the
	# character's side), the outline's x-expansion on that one end stops
	# being a thin front-view sliver and becomes the box's entire end-
	# cap face, winning the z-test and painting the whole limb black - a
	# real, confirmed-live report ("black buzzing on the shoulders" -
	# "buzzing" from the z-test winner flickering as the camera orbits
	# near the angle where the two end caps are almost coplanar). The
	# inner (torso-attached) end never faces a profile camera this way,
	# so only the outer end needs flushing.
	flush_outer_x0, flush_outer_x1 = (side == -1), (side == 1)
	idx = _outlined_box(
		vertex, normal, color, tris, idx,
		(cap_lo_x, -0.22 + y_offset, shoulder_z - cap_h), (cap_hi_x, 0.22 + y_offset, shoulder_z), color_c,
		flush_z1=True, flush_x0=flush_outer_x0, flush_x1=flush_outer_x1,
	)
	idx = _outlined_box(
		vertex, normal, color, tris, idx,
		(lo_x, -0.18 + y_offset, elbow_z), (hi_x, 0.18 + y_offset, shoulder_z), color_c,
		flush_z1=True, flush_x0=flush_outer_x0, flush_x1=flush_outer_x1,
	)
	if not is_prosthetic and not bare:  # no fabric pattern on a robotic prosthetic arm or bare skin
		idx = _apply_fabric_pattern(vertex, normal, color, tris, idx, lo_x, hi_x, -0.18 + y_offset, elbow_z, shoulder_z, pattern, pattern_c)
	return idx


def _build_arm_forearm(vertex, normal, color, tris, idx, side, chest_half_w, limb_half_w, missing_arm, prosthetics, shirt, prosthetic_c, wrist_z, elbow_z, y_offset=0.0, accent_c=None, stitch_c=None, pattern="solid", pattern_c=None, skin=None, top_style="long_sleeve"):
	"""Forearm segment (elbow to wrist) of a jointed arm - carries the
	prosthetic joint-ring accent at the elbow. `y_offset` matches
	_build_arm_upper's own - see that function's docstring. `accent_c`
	(the cuff color) defaults to a darker shade of `shirt`, `stitch_c`
	to _hem_band's own default, when not given explicitly.

	`top_style` (see _build_arm_upper's own identical param) bares this
	segment for "tank_top"/"vest" (whole arm) AND "short_sleeve" (this
	segment specifically - the forearm is exactly where that style's
	own sleeve/bare split falls, matching build_character's identical
	logic) - no cuff hem gets drawn on bare skin, same as no fabric
	pattern on it. `wristband` used to draw a thin accessory band right
	at the wrist end here - fully moved out to
	avatar_plugins_builtin/bodypart/wristband/ (reparented onto
	rig.elbow_l/r, the same pivot this segment's own geometry hangs off
	of - see that plugin's own docstring)."""
	side_name = "left" if side == -1 else "right"
	if missing_arm in (side_name, "both"):
		return idx
	accent_c = accent_c if accent_c is not None else _shade(shirt, 0.7)
	outer = side * (chest_half_w + limb_half_w * 2)
	inner = side * chest_half_w
	lo_x, hi_x = min(inner, outer), max(inner, outer)
	is_prosthetic = f"{side_name}_arm" in prosthetics
	bare = top_style in ("tank_top", "vest", "short_sleeve")
	color_c = prosthetic_c if is_prosthetic else (skin if bare else shirt)
	# Same outer-face profile-view fix as the upper arm/shoulder cap
	# (see _build_arm_upper) - this segment shares the identical
	# geometry pattern and is exposed to the exact same camera angle.
	flush_outer_x0, flush_outer_x1 = (side == -1), (side == 1)
	# Forearm box shrunk off its own wrist end by SLEEVE_HEM_H (skipped
	# for a prosthetic or bare skin, neither of which gets a cuff below)
	# so the cuff hem fills that exact freed slice instead of
	# overlapping it - see _hem_band's own docstring for why the first
	# version's overlapping approach z-fought.
	forearm_z0 = wrist_z if (is_prosthetic or bare) else wrist_z + SLEEVE_HEM_H
	idx = _outlined_box(
		vertex, normal, color, tris, idx, (lo_x, -0.18 + y_offset, forearm_z0), (hi_x, 0.18 + y_offset, elbow_z), color_c,
		flush_x0=flush_outer_x0, flush_x1=flush_outer_x1,
	)
	if is_prosthetic:
		idx = _outlined_box(
			vertex, normal, color, tris, idx,
			(lo_x - 0.005, -0.19 + y_offset, elbow_z - 0.03), (hi_x + 0.005, 0.19 + y_offset, elbow_z + 0.03), (0.35, 0.35, 0.37, 1),
		)
	elif not bare:
		idx = _apply_fabric_pattern(vertex, normal, color, tris, idx, lo_x, hi_x, -0.18 + y_offset, forearm_z0, elbow_z, pattern, pattern_c)
		# Sleeve cuff - a real long-sleeve detail (the hem where the
		# sleeve is folded/finished at the wrist), skipped for a
		# prosthetic or bare arm (nothing to hem).
		idx = _hem_band(
			vertex, normal, color, tris, idx, lo_x, hi_x, -0.18 + y_offset, 0.18 + y_offset,
			wrist_z, wrist_z + SLEEVE_HEM_H, accent_c, stitch_c=stitch_c,
		)
	return idx


def _build_hand(vertex, normal, color, tris, idx, side, chest_half_w, limb_half_w, missing_arm, prosthetics, skin, prosthetic_c, wrist_z, y_offset=0.0, age=0.5, gloves=False, gloves_c=None):
	"""Palm block only (below the wrist) of a jointed arm - narrower than
	the arm so it reads as a hand rather than more sleeve. The 4 fingers
	and thumb used to be baked into this same Geom as fixed boxes; per
	direct instruction ("I want fingers and thumbs to be able to be
	animated") they're now each their own pivot, built and posed
	independently in avatar_rig.py's build_rig() (same reason every
	other joint here - hip, knee, elbow, ... - is its own pivot rather
	than fused geometry). This function keeps just the palm; see
	build_rig() for FINGER_LEN/THUMB_LEN and the actual per-digit boxes,
	which reuse this function's own hand_lo_x/hand_hi_x/palm_z0 math
	(duplicated there rather than returned, matching how every other
	shared measurement in this file - leg_w, for instance - is
	recomputed at each call site instead of threaded through return
	values). `y_offset` matches _build_arm_upper's own (see its
	docstring) - the whole arm chain, palm included, carries it so nothing
	kinks back to center partway down.

	`gloves` recolors the palm (gloves_c) instead of skin - a real
	accessory, skipped on a prosthetic (a robotic hand wears no glove).
	`paws`/`claws` used to draw a paw-pad overlay/claw tips right here
	too - both fully moved out to their own bodypart plugins
	(avatar_plugins_builtin/bodypart/paws|claws/), same "poke out to
	override" additive technique as everywhere else in this file (the
	paw plugin's own geometry pokes out past this palm in every
	dimension, fully covering it) rather than an exclusive branch - this
	function always draws the normal palm unconditionally now, exactly
	like it did before paws/claws existed. This also keeps `wrist_pivot`
	guaranteed non-None whenever the arm itself isn't missing (see
	_pivoted_part's own docstring: a build_fn that draws nothing at all
	yields no pivot), which both plugins - and build_rig()'s own finger/
	thumb-pivot construction - depend on existing to reparent onto or
	check against."""
	side_name = "left" if side == -1 else "right"
	if missing_arm in (side_name, "both"):
		return idx
	outer = side * (chest_half_w + limb_half_w * 2)
	inner = side * chest_half_w
	lo_x, hi_x = min(inner, outer), max(inner, outer)
	is_prosthetic = f"{side_name}_arm" in prosthetics
	color_c = prosthetic_c if is_prosthetic else (gloves_c if gloves else skin)
	flush_outer_x0, flush_outer_x1 = (side == -1), (side == 1)
	hand_lo_x, hand_hi_x = lo_x + 0.01, hi_x - 0.01
	palm_z0 = wrist_z - 0.04
	idx = _outlined_box(
		vertex, normal, color, tris, idx, (hand_lo_x, -0.16 + y_offset, palm_z0), (hand_hi_x, 0.16 + y_offset, wrist_z), color_c,
		flush_x0=flush_outer_x0, flush_x1=flush_outer_x1,
	)
	if age > 0.65 and color_c == skin:  # no age spots on a gloved/prosthetic hand
		spot_c = _shade(skin, 0.6)
		spot_x = (hand_lo_x + hand_hi_x) / 2
		idx = _box(
			vertex, normal, color, tris, idx,
			(spot_x - 0.012, 0.1 + y_offset, palm_z0 + 0.01), (spot_x + 0.012, 0.13 + y_offset, palm_z0 + 0.025),
			spot_c,
		)
	return idx


NECK_H = 0.06  # neck box height - a real, confirmed-live correction: 0.14 read as "WAY too big... for this style" - a short stub, not a chunky block, is what actually fits this rig's own blocky-but-minimal proportions elsewhere (the wrists/ankles are thin joints, not thick collars either)


def _build_head_bundle(
	vertex, normal, color, tris, idx, skin, hair, eyes, eyebrows, missing_eye, head_z0, HEAD_S,
	age=0.5, eyes_r=None, iris_ring=False, skin_patches=False, skin_patch_c=None,
	hairstyle="short", eye_shape="round", nose_shape="straight", mouth_style="neutral",
	facial_hair="none", facial_hair_c=None, freckles=False, blush=False,
	muzzle=False,
):
	"""Head skin box (+ neck) + every hairstyle/eye_shape/nose_shape/
	mouth_style/facial_hair/freckles/blush - the full head richness
	build_character's own static path offers, via the two functions it
	shares this exact code with (_build_hair_and_toppers/_build_face -
	see their own docstrings). ears/horns/antennae/halo/glasses/hat/
	headphones/earrings/extra_eyes/whiskers/fur_pattern are NOT here
	anymore - each is its own built-in "bodypart" feature now
	(avatar_plugins_builtin/bodypart/ears|horns|antennae|halo|glasses|
	hat|headphones|earrings|extra_eyes|whiskers|fur_pattern/), drawn
	later still by the bodypart plugin hook, same as backpack/satchel/
	purse/necklace/tie. `muzzle` DOES stay a real, named parameter here (unlike those)
	because _build_face() needs it as a gate to skip drawing the normal
	nose+mouth - only the snout-replacement GEOMETRY itself moved out to
	the "muzzle" bodypart plugin, same treatment as ears/horns/halo's own
	gate-stays-core-geometry-moves-out split (see this function's own
	caller in both avatar3d.py/avatar3d_rig.py for where muzzle/
	snout_color get resolved from extra_traits rather than staying a
	build_character()/build_rig() named parameter, since - unlike ears/
	horns/halo - muzzle has no OUTER cross-function coupling of its own,
	just this local one).

	Geometry is otherwise identical to what build_character's own head-
	building code produces, EXCEPT the neck box just below, added per
	direct request ("give them a neck") - build_character's own separate
	static head has the head sitting flush on the shoulders with nothing
	between them, not mirrored into it (a deliberate silhouette
	difference, not an oversight - see build_character's own docstring
	on why). `skin` is used for the neck box right below, and by
	_build_face() further down."""
	hs = HEAD_S / 0.85
	neck_hw = HEAD_S * 0.24
	idx = _box(
		vertex, normal, color, tris, idx,
		(-neck_hw, -neck_hw, head_z0 - NECK_H), (neck_hw, neck_hw, head_z0), skin,
	)
	idx = _box(
		vertex, normal, color, tris, idx,
		(-HEAD_S / 2, -HEAD_S / 2, head_z0), (HEAD_S / 2, HEAD_S / 2, head_z0 + HEAD_S), skin,
	)
	idx = _build_hair_and_toppers(
		vertex, normal, color, tris, idx, hairstyle, hair, head_z0, HEAD_S, hs,
	)

	idx = _build_face(
		vertex, normal, color, tris, idx, skin, eyes, eyebrows, eyes_r, eye_shape, nose_shape, mouth_style,
		muzzle, facial_hair, facial_hair_c if facial_hair_c is not None else hair, blush, freckles,
		missing_eye, iris_ring, head_z0, HEAD_S, hs, age=age, skin_patches=skin_patches, skin_patch_c=skin_patch_c,
	)
	return idx


def _build_face(
	vertex, normal, color, tris, idx, skin, eyes, eyebrows, eyes_r, eye_shape, nose_shape, mouth_style,
	muzzle, facial_hair, facial_hair_c, blush, freckles,
	missing_eye, iris_ring, head_z0, HEAD_S, hs, age=0.5, skin_patches=False, skin_patch_c=None,
):
	"""Eyes/eyebrows through freckles, plus age-gated wrinkles/age-spots/
	skin-patches - shared by build_character (every eye_shape/
	nose_shape/mouth_style/facial_hair/... variant a picker UI offers)
	and _build_head_bundle (the animated rig - see that function's own
	docstring on why it needs every one of these too, not just the
	round/straight/neutral combo it used to hardcode on its own). Glasses/
	earrings/hats/headphones stay OUT of this function on purpose - real
	accessories, not part of the rig's own scope (see avatar3d_rig.py's
	build_rig() docstring) - build_character still draws those itself,
	right after calling this, recomputing eye_z/face_y locally (both
	simple HEAD_S-derived formulas, cheap to redo rather than thread back
	out of here just for two callers three lines later). Likewise
	paws/claws stay in build_character only - real BODY features (hands/
	feet), not head ones, out of scope for a head-only extraction.

	`extra_eyes`/`whiskers`/`fur_pattern` used to live here too - all
	three fully moved out to their own bodypart plugins
	(avatar_plugins_builtin/bodypart/extra_eyes|whiskers|fur_pattern/)
	since none has any coupling outside this function (fur_pattern's own
	arm-sleeve portion - build_character-only, the rig never had it -
	moved out along with the face portion, into the same plugin file).
	`muzzle` stays a real parameter (see this module's own
	`_build_head_bundle` docstring) purely as the gate deciding whether
	the normal nose_shape/mouth_style geometry below gets skipped - the
	snout-replacement geometry itself that used to sit in the `else`
	branch moved out to the "muzzle" bodypart plugin."""
	WHITE = (0.96, 0.97, 1.0, 1)
	PUPIL_BLACK = (0.05, 0.05, 0.07, 1)
	eye_z = head_z0 + HEAD_S * 0.56
	face_y = -HEAD_S / 2
	eyes_r = eyes_r if eyes_r is not None else eyes
	for side in (-1, 1):
		ex = side * HEAD_S * 0.22
		side_name = "left" if side == -1 else "right"
		eye_missing = missing_eye in (side_name, "both")
		eye_c = eyes if side == -1 else eyes_r
		if eye_missing:
			# A real, independent accessibility choice - the eyebrow
			# (below, outside this guard) and glasses (if worn) still
			# render normally over it, same as a real person missing an
			# eye. Just a subtle closed-eyelid/scar line rather than a
			# jarring gap or a raised bump where the eye would be.
			idx = _box(
				vertex, normal, color, tris, idx,
				(ex - 0.09 * hs, face_y - 0.015 * hs, eye_z - 0.012 * hs), (ex + 0.09 * hs, face_y - 0.005 * hs, eye_z + 0.012 * hs), (0.6, 0.45, 0.42, 1),
			)
		else:
			# Iris/pupil sized down from the first pass - a direct,
			# blunt report that the original proportions (iris ~60% of
			# the sclera's width) read as wide-eyed/dilated ("seen some
			# shit... or just did a line of coke"), not calm/normal.
			# More visible white around a smaller iris/pupil is what
			# actually reads as a relaxed, normal eye.
			idx = _outlined_box(vertex, normal, color, tris, idx, (ex - 0.1 * hs, face_y - 0.02 * hs, eye_z - 0.09 * hs), (ex + 0.1 * hs, face_y + 0.03 * hs, eye_z + 0.09 * hs), WHITE)
			if iris_ring:
				# Limbal ring - a real, common eye detail (a darker ring
				# right at the iris's own outer edge) - drawn BIGGER but
				# LESS forward than the iris itself so the iris's own
				# front face covers its center, leaving only a thin
				# visible border where the ring's larger footprint
				# extends past the iris - the same "poke out less than
				# what's meant to occlude it" ordering this file already
				# uses (eyelids over the sclera, the mouth-open overlay
				# over the closed mouth).
				idx = _box(vertex, normal, color, tris, idx, (ex - 0.052 * hs, face_y - 0.032 * hs, eye_z - 0.052 * hs), (ex + 0.052 * hs, face_y + 0.03 * hs, eye_z + 0.052 * hs), _shade(eye_c, 0.55))
			idx = _box(vertex, normal, color, tris, idx, (ex - 0.044 * hs, face_y - 0.035 * hs, eye_z - 0.044 * hs), (ex + 0.044 * hs, face_y + 0.03 * hs, eye_z + 0.044 * hs), eye_c)
			idx = _box(vertex, normal, color, tris, idx, (ex - 0.018 * hs, face_y - 0.05 * hs, eye_z - 0.018 * hs), (ex + 0.018 * hs, face_y + 0.03 * hs, eye_z + 0.018 * hs), PUPIL_BLACK)

		# Eyelids - "eyelids bro": a flat sclera/iris/pupil stack with
		# zero lid geometry reads as a permanently wide-open, bugging-out
		# stare no matter how the iris/pupil themselves are sized. Two
		# skin-colored boxes that sit in front of and crop the rest of
		# the eye. Depth is the whole point here: the eye stack's own
		# front-to-back order (closest to camera first) is pupil
		# (face_y-0.05) > iris (face_y-0.035) > sclera (face_y-0.02) -
		# a real, confirmed-live bug had the eyelid at only face_y-0.03,
		# BEHIND the pupil/iris, so wherever the lid's z-range overlapped
		# them the pupil/iris poked through in front of it instead of
		# being cropped ("clipping"). The eyelid must be the frontmost
		# layer of the whole stack to actually occlude anything behind
		# it, so it sits further out (face_y-0.07) than the pupil, the
		# most-forward of the other three layers.
		# eye_shape picks how much they crop, and - for "sharp" - lets
		# the two eye-halves crop by different amounts for a genuinely
		# angled corner rather than just a shorter/taller flat crop.
		# Skipped entirely when eye_missing (nothing left underneath to
		# crop).
		lid_y = (face_y - 0.07 * hs, face_y - 0.015 * hs)
		if eye_missing:
			pass
		elif eye_shape == "narrow":
			# A deeper crop from both sides than "round" - but NOT razor
			# thin: a real, confirmed-live bug had this visible gap at
			# only 0.01 world units tall (vs. "round"'s 0.08), which is
			# sub-pixel at any normal render size and reads as broken,
			# aliased hatching rather than a narrow eye. 0.045 is still
			# noticeably slimmer than "round" while staying thick enough
			# to rasterize cleanly at real-world thumbnail sizes.
			idx = _box(vertex, normal, color, tris, idx, (ex - 0.105 * hs, lid_y[0], eye_z + 0.0225 * hs), (ex + 0.105 * hs, lid_y[1], eye_z + 0.095 * hs), skin)
			idx = _box(vertex, normal, color, tris, idx, (ex - 0.105 * hs, lid_y[0], eye_z - 0.095 * hs), (ex + 0.105 * hs, lid_y[1], eye_z - 0.0225 * hs), skin)
		elif eye_shape == "sharp":
			# Split the upper lid into an inner half (toward the nose,
			# x=0) and an outer half (toward the ear) with different
			# crop depths, so the eye reads as angled/slanted rather
			# than symmetric - a blocky, staircase approximation of a
			# real diagonal, matching this whole model's Minecraft
			# aesthetic rather than fighting it with a smooth curve.
			# Same thinness fix as "narrow" above: both halves keep a
			# real, non-degenerate visible band (min 0.07) rather than
			# the old outer half's near-zero sliver.
			if side == 1:
				inner_x, outer_x = (ex - 0.105 * hs, ex), (ex, ex + 0.105 * hs)
			else:
				inner_x, outer_x = (ex, ex + 0.105 * hs), (ex - 0.105 * hs, ex)
			idx = _box(vertex, normal, color, tris, idx, (inner_x[0], lid_y[0], eye_z + 0.045 * hs), (inner_x[1], lid_y[1], eye_z + 0.095 * hs), skin)
			idx = _box(vertex, normal, color, tris, idx, (outer_x[0], lid_y[0], eye_z + 0.005 * hs), (outer_x[1], lid_y[1], eye_z + 0.095 * hs), skin)
			idx = _box(vertex, normal, color, tris, idx, (ex - 0.105 * hs, lid_y[0], eye_z - 0.095 * hs), (ex + 0.105 * hs, lid_y[1], eye_z - 0.065 * hs), skin)
		elif eye_shape == "wide":
			# Minimal crop from both lids - the most visible sclera of
			# any eye_shape, reading as a big, wide-open eye. Kept just
			# thick enough (0.04 min) to avoid the same sub-pixel
			# aliasing bug "narrow"'s first pass hit.
			idx = _box(vertex, normal, color, tris, idx, (ex - 0.105 * hs, lid_y[0], eye_z + 0.065 * hs), (ex + 0.105 * hs, lid_y[1], eye_z + 0.095 * hs), skin)
			idx = _box(vertex, normal, color, tris, idx, (ex - 0.105 * hs, lid_y[0], eye_z - 0.095 * hs), (ex + 0.105 * hs, lid_y[1], eye_z - 0.075 * hs), skin)
		elif eye_shape == "hooded":
			# The opposite emphasis from "narrow" (which crops evenly
			# from both sides): a much deeper UPPER lid (covering
			# nearly half the sclera) with the lower lid left thin like
			# "round"'s - a real, heavy-lidded look rather than a
			# uniformly smaller eye.
			idx = _box(vertex, normal, color, tris, idx, (ex - 0.105 * hs, lid_y[0], eye_z - 0.015 * hs), (ex + 0.105 * hs, lid_y[1], eye_z + 0.095 * hs), skin)
			idx = _box(vertex, normal, color, tris, idx, (ex - 0.105 * hs, lid_y[0], eye_z - 0.095 * hs), (ex + 0.105 * hs, lid_y[1], eye_z - 0.075 * hs), skin)
		else:  # "round" (default) - a bigger upper lid (a real open
			# eye's lid line sits above center, not symmetric) and a
			# thin lower lid.
			idx = _box(vertex, normal, color, tris, idx, (ex - 0.105 * hs, lid_y[0], eye_z + 0.015 * hs), (ex + 0.105 * hs, lid_y[1], eye_z + 0.095 * hs), skin)
			idx = _box(vertex, normal, color, tris, idx, (ex - 0.105 * hs, lid_y[0], eye_z - 0.095 * hs), (ex + 0.105 * hs, lid_y[1], eye_z - 0.065 * hs), skin)

		# Eyebrows - a real, separate report that zero eyebrows at all
		# left the face looking permanently startled/vacant regardless
		# of the eye proportions themselves. Defaults to hair color
		# (matches real faces most of the time) but is its own
		# independently-settable color, not hardcoded to match hair.
		# Outlined so it's never invisible against a same/close-colored
		# hair (a light-blonde brow against light hair, say).
		idx = _outlined_box(
			vertex, normal, color, tris, idx,
			(ex - 0.11 * hs, face_y - 0.06 * hs, eye_z + 0.12 * hs), (ex + 0.11 * hs, face_y - 0.01 * hs, eye_z + 0.16 * hs), eyebrows,
		)


	# Nose - a real, distinct shape per nose_shape, not just a fixed
	# skin-colored bump. "straight" is the original proportions; the
	# others vary width/how far it pokes forward (the bridge) rather
	# than adding new geometry, keeping every variant a single clean
	# box like the original.
	nose_z = eye_z - 0.2 * hs
	MOUTH_COLOR = (0.55, 0.25, 0.28, 1)
	mouth_z = nose_z - 0.16 * hs
	if not muzzle:
		if nose_shape == "button":
			n_half_w, n_poke, n_half_h = 0.04 * hs, 0.06 * hs, 0.05 * hs
		elif nose_shape == "wide":
			n_half_w, n_poke, n_half_h = 0.08 * hs, 0.09 * hs, 0.07 * hs
		elif nose_shape == "pointed":
			n_half_w, n_poke, n_half_h = 0.045 * hs, 0.12 * hs, 0.08 * hs
		elif nose_shape == "upturned":
			n_half_w, n_poke, n_half_h = 0.045 * hs, 0.05 * hs, 0.045 * hs
		elif nose_shape == "flat":
			n_half_w, n_poke, n_half_h = 0.05 * hs, 0.03 * hs, 0.05 * hs
		else:  # "straight" (default, original proportions)
			n_half_w, n_poke, n_half_h = 0.06 * hs, 0.09 * hs, 0.07 * hs
		# Outlined - being the same skin color as the rest of the face,
		# the nose has always relied purely on shading/perspective to
		# read at all, which a direct render confirmed doesn't hold up
		# reliably from every angle/resolution (the same "same color,
		# no visible edge" bug class as the collar/lapel/backpack-strap
		# fixes above). A real outline makes it visible unconditionally.
		idx = _outlined_box(
			vertex, normal, color, tris, idx,
			(-n_half_w, face_y - n_poke, nose_z - n_half_h), (n_half_w, face_y + 0.03 * hs, nose_z + n_half_h), skin,
		)

		# Mouth - mouth_style picks a real shape/expression, not just a
		# flat line. "smile"/"frown" split the mouth into a center
		# segment plus two corner segments raised or lowered in z - the
		# same blocky "staircase" approximation of a curve already used
		# for "sharp" eyes and "pleated" skirts, rather than fighting
		# axis-aligned boxes into a smooth curve.
		if mouth_style == "small":
			idx = _outlined_box(
				vertex, normal, color, tris, idx,
				(-0.05 * hs, face_y - 0.02 * hs, mouth_z - 0.02 * hs), (0.05 * hs, face_y + 0.03 * hs, mouth_z + 0.02 * hs), MOUTH_COLOR,
			)
		elif mouth_style == "open":
			# A taller box (open mouth) - the same width as "neutral"
			# but with real height instead of a thin line, plus a thin
			# white sliver peeking out the top to suggest teeth.
			idx = _outlined_box(
				vertex, normal, color, tris, idx,
				(-0.07 * hs, face_y - 0.02 * hs, mouth_z - 0.045 * hs), (0.07 * hs, face_y + 0.03 * hs, mouth_z + 0.02 * hs), MOUTH_COLOR,
			)
			idx = _outlined_box(
				vertex, normal, color, tris, idx,
				(-0.06 * hs, face_y - 0.025 * hs, mouth_z + 0.02 * hs), (0.06 * hs, face_y + 0.03 * hs, mouth_z + 0.03 * hs), (0.95, 0.94, 0.9, 1),
			)
		elif mouth_style in ("smile", "frown"):
			corner_dz = 0.028 * hs if mouth_style == "smile" else -0.028 * hs
			idx = _outlined_box(
				vertex, normal, color, tris, idx,
				(-0.045 * hs, face_y - 0.02 * hs, mouth_z - 0.02 * hs), (0.045 * hs, face_y + 0.03 * hs, mouth_z + 0.02 * hs), MOUTH_COLOR,
			)
			for side in (-1, 1):
				cx0, cx1 = sorted((side * 0.045 * hs, side * 0.095 * hs))
				idx = _outlined_box(
					vertex, normal, color, tris, idx,
					(cx0, face_y - 0.02 * hs, mouth_z - 0.02 * hs + corner_dz), (cx1, face_y + 0.03 * hs, mouth_z + 0.02 * hs + corner_dz), MOUTH_COLOR,
				)
		elif mouth_style == "smirk":
			# Like "smile" but ONLY one corner raised - a real
			# asymmetric expression, not just a smaller/mirrored smile.
			idx = _outlined_box(
				vertex, normal, color, tris, idx,
				(-0.045 * hs, face_y - 0.02 * hs, mouth_z - 0.02 * hs), (0.045 * hs, face_y + 0.03 * hs, mouth_z + 0.02 * hs), MOUTH_COLOR,
			)
			cx0, cx1 = sorted((0.045 * hs, 0.095 * hs))
			idx = _outlined_box(
				vertex, normal, color, tris, idx,
				(cx0, face_y - 0.02 * hs, mouth_z - 0.02 * hs + 0.028 * hs), (cx1, face_y + 0.03 * hs, mouth_z + 0.02 * hs + 0.028 * hs), MOUTH_COLOR,
			)
			cx0, cx1 = sorted((-0.045 * hs, -0.09 * hs))
			idx = _outlined_box(
				vertex, normal, color, tris, idx,
				(cx0, face_y - 0.02 * hs, mouth_z - 0.02 * hs), (cx1, face_y + 0.03 * hs, mouth_z + 0.02 * hs), MOUTH_COLOR,
			)
		else:  # "neutral" (default) - a thin flat line
			idx = _outlined_box(
				vertex, normal, color, tris, idx,
				(-0.09 * hs, face_y - 0.02 * hs, mouth_z - 0.025 * hs), (0.09 * hs, face_y + 0.03 * hs, mouth_z + 0.025 * hs), MOUTH_COLOR,
			)
	# else (muzzle=True): nose+mouth geometry skipped entirely - the
	# snout that replaces it is drawn by the "muzzle" bodypart plugin
	# instead (see this function's own docstring).

	# fur_pattern's face overlay used to draw here (spots/stripes/
	# patches, using nose_z/face_y/hs) - fully moved out to the
	# "fur_pattern" bodypart plugin (see this function's own docstring).

	if facial_hair != "none":
		# All variants sit at/below mouth height, poking past the
		# face's own front (same "poke out to be visible" rule as
		# everything else) - built from the same handful of boxes in
		# different combinations rather than separate geometry per
		# style, since a mustache/goatee/beard are all real subsets of
		# the same "mouth + chin + jaw" region.
		fh_y = (face_y - 0.05 * hs, face_y - 0.01 * hs)
		if facial_hair in ("mustache", "goatee", "full_beard"):
			idx = _outlined_box(
				vertex, normal, color, tris, idx,
				(-0.09 * hs, fh_y[0], mouth_z + 0.02 * hs), (0.09 * hs, fh_y[1], mouth_z + 0.05 * hs), facial_hair_c,
			)
		if facial_hair in ("goatee", "full_beard"):
			idx = _outlined_box(
				vertex, normal, color, tris, idx,
				(-0.06 * hs, fh_y[0], mouth_z - 0.12 * hs), (0.06 * hs, fh_y[1], mouth_z - 0.02 * hs), facial_hair_c,
			)
		if facial_hair == "full_beard":
			for side in (-1, 1):
				jx0, jx1 = sorted((side * 0.06 * hs, side * 0.14 * hs))
				idx = _outlined_box(
					vertex, normal, color, tris, idx,
					(jx0, fh_y[0], mouth_z - 0.1 * hs), (jx1, fh_y[1], mouth_z + 0.09 * hs), facial_hair_c,
				)
		if facial_hair == "stubble":
			# A subtle, shallow-poke tint over the whole lower face
			# (not built from the mustache/chin/jaw pieces above) -
			# reads as a light shadow rather than real hair growth.
			idx = _outlined_box(
				vertex, normal, color, tris, idx,
				(-0.13 * hs, face_y - 0.015 * hs, mouth_z - 0.1 * hs), (0.13 * hs, face_y - 0.002 * hs, mouth_z + 0.08 * hs), facial_hair_c,
			)

	if blush:
		# A soft rectangle on each cheek, below the eyes and outside
		# the nose - a real, distinct addition rather than relying on
		# skin_color alone to read as "rosy". Drawn BEFORE freckles
		# (below) since it's the broader base wash freckles sit on top
		# of, not the other way round.
		for side in (-1, 1):
			bx0, bx1 = sorted((side * 0.12 * hs, side * 0.24 * hs))
			idx = _box(
				vertex, normal, color, tris, idx,
				(bx0, face_y - 0.012 * hs, nose_z - 0.04 * hs), (bx1, face_y + 0.03 * hs, nose_z + 0.06 * hs), BLUSH_COLOR,
			)

	if freckles:
		# A scatter of tiny dots across each cheek - small enough that
		# an outline would swallow them, so left un-outlined (the one
		# deliberate exception in this file) like a real subtle skin
		# mark rather than a bold graphic element. A real, confirmed-
		# live bug: freckles used to poke out LESS (0.005) than blush
		# (0.012) and the two overlap in the same cheek region, so
		# blush - drawn after and more forward - completely hid every
		# freckle whenever both were enabled together (a direct render
		# showed zero visible dots). Fixed by making freckles the
		# MORE-forward of the two (0.02), consistent with a real
		# freckle being a mark on top of the skin/blush, not under it.
		for side in (-1, 1):
			base_x = side * 0.17 * hs
			for dx, dz in ((-0.02, 0.015), (0.02, 0.01), (0.0, -0.015), (0.035, -0.005), (-0.035, -0.01)):
				fx = base_x + dx * hs
				idx = _box(
					vertex, normal, color, tris, idx,
					(fx - 0.008 * hs, face_y - 0.02 * hs, nose_z + dz * hs - 0.008 * hs), (fx + 0.008 * hs, face_y + 0.03 * hs, nose_z + dz * hs + 0.008 * hs), FRECKLE_COLOR,
				)

	# Aging - `age` is 0 (young) to 1 (elderly), 0.5 a neutral adult (the
	# default every existing caller keeps getting unless it opts in).
	# Wrinkles/age spots only start appearing past ELDERLY_AGE rather
	# than scaling continuously from age=0 - a face with one faint
	# forehead line isn't "slightly older", it just reads as a rendering
	# artifact; real visible wrinkles/spots are an elderly-specific
	# detail, not a smooth gradient from childhood. Ported from
	# _build_head_bundle's own original standalone version (now removed
	# there in favor of calling this shared function) - build_character
	# never had an aging system of its own before this merge.
	ELDERLY_AGE = 0.65
	if age > ELDERLY_AGE:
		detail = min(1.0, (age - ELDERLY_AGE) / (1.0 - ELDERLY_AGE))
		wrinkle_c = _shade(skin, 0.7)
		spot_c = _shade(skin, 0.55)
		for side in (-1, 1):
			ex = side * HEAD_S * 0.22
			idx = _box(
				vertex, normal, color, tris, idx,
				(ex + side * 0.1 * hs, face_y - 0.005 * hs, eye_z - 0.01 * hs), (ex + side * 0.14 * hs, face_y + 0.005 * hs, eye_z + 0.03 * hs), wrinkle_c,
			)
		for side in (-1, 1):
			idx = _box(
				vertex, normal, color, tris, idx,
				(side * 0.075 * hs, face_y - 0.005 * hs, mouth_z + 0.02 * hs), (side * 0.075 * hs + side * 0.012 * hs, face_y + 0.005 * hs, nose_z - 0.03 * hs), wrinkle_c,
			)
		if detail > 0.6:
			for side in (-1, 1):
				ex = side * HEAD_S * 0.3
				idx = _box(
					vertex, normal, color, tris, idx,
					(ex - 0.012 * hs, face_y - 0.005 * hs, nose_z - 0.03 * hs), (ex + 0.012 * hs, face_y + 0.005 * hs, nose_z - 0.01 * hs), spot_c,
				)

	if skin_patches:
		# Vitiligo/birthmark-style patches - independent of the aging
		# system above. Ported from _build_head_bundle's own original
		# standalone version, same reasoning as the aging block above.
		patch_c = skin_patch_c if skin_patch_c is not None else _shade(skin, 1.35)
		PATCH_SPOTS = ((HEAD_S * 0.32, nose_z - 0.15 * hs, 0.05 * hs), (-HEAD_S * 0.18, eye_z + 0.1 * hs, 0.035 * hs))
		for cx, cz, r in PATCH_SPOTS:
			for ox, oz, scale in ((0, 0, 1.0), (r * 0.7, r * 0.3, 0.6), (-r * 0.5, -r * 0.6, 0.5)):
				rr = r * scale
				idx = _box(
					vertex, normal, color, tris, idx,
					(cx + ox - rr, face_y - 0.005 * hs, cz + oz - rr), (cx + ox + rr, face_y + 0.005 * hs, cz + oz + rr), patch_c,
				)

	return idx


def hair_top_z_for_style(hairstyle, head_z0, HEAD_S, hs):
	"""How high the given hairstyle's own silhouette actually reaches,
	in absolute world Z - the one shared number ears/horns/antennae all
	need to sit ON TOP of whatever hair is there instead of at a fixed
	head-relative height a tall style (afro, spiky, bun) could bury
	while a short one left them barely poking through (a real,
	confirmed report this fixed). Mirrors each hairstyle branch's own
	real top height (matching the z-offsets _build_hair_and_toppers()
	itself builds hair from) rather than a single guess, so it stays
	correct if those proportions ever change.

	A real, public (non-underscore) module function - not folded back
	into a private helper - specifically so it can be called from
	outside this file too: ears/horns/antennae all moved out into their
	own avatar_plugins_builtin/bodypart/ files (ears is the biggest
	single such extraction in this project - 15+ species branches, its
	own EAR_PARAMS/FLOPPY_EARS tables, several special-cased styles), and
	each needed this exact computation to still sit correctly on top of
	hair without re-embedding a second copy of this table that could
	drift out of sync with the real one hair itself is built from."""
	HAIR_TOP_OFFSET = {
		"bald": 0.0, "spiky": 0.30 * hs, "buzz": 0.03 * hs, "undercut": 0.08 * hs,
		"curly": 0.14 * hs, "bun": 0.25 * hs, "afro": HEAD_S * 0.45,
	}
	return head_z0 + HEAD_S + HAIR_TOP_OFFSET.get(hairstyle, 0.08 * hs)


def _build_hair_and_toppers(
	vertex, normal, color, tris, idx, hairstyle, hair, head_z0, HEAD_S, hs,
):
	"""Hairstyle (all 12) only, now - shared by build_character and
	_build_head_bundle (the animated rig, which used to hardcode "short"
	hair only before this got unified).

	Ears/horns/antennae/halo all used to live here too - each is its own
	built-in "bodypart" feature now (avatar_plugins_builtin/bodypart/
	ears|horns|antennae|halo/), drawn later still by the bodypart plugin
	hook. ears/horns/halo specifically still stay real, core, named
	parameters of build_character()/build_rig() themselves (not removed
	from either signature) purely for the hairstyle-override check just
	above this function's own call site (see that check's own comment) -
	only their actual GEOMETRY moved out, threaded to the plugin hook
	via metrics instead of through this function.
	hair_top_z_for_style() (a real, public module function - see its own
	docstring) is what makes that split possible: ears/horns/antennae
	(now all in their own plugin files) each need the exact same "how
	high does this hairstyle's own silhouette actually reach" number,
	without needing a second copy of that table that could drift out of
	sync with the real one hair itself is built from. Wings stay OUT of
	this function too - a torso-mounted, not head-mounted, feature (its
	own file now - see _build_wings()'s own former docstring, now
	avatar_plugins_builtin/bodypart/wings/); hat/headphones/glasses/
	earrings are real accessories, each its own file under
	avatar_plugins_builtin/bodypart/ too."""
	hh0 = head_z0 + HEAD_S * 0.62
	if hairstyle == "bald":
		pass  # no hair geometry at all - just the skin-colored head
	elif hairstyle == "spiky":
		# A narrow mohawk-like ridge of jagged, alternating-height
		# spikes down the center only - the sides stay bare (skin),
		# a genuinely different silhouette from a full cap, not a
		# recolor of one.
		spike_w = 0.16 * hs
		base_z = head_z0 + HEAD_S
		spikes = [(-0.32 * hs, 0.15 * hs), (-0.16 * hs, 0.30 * hs), (0.0, 0.18 * hs), (0.16 * hs, 0.30 * hs), (0.32 * hs, 0.15 * hs)]
		for y_c, spike_h in spikes:
			idx = _box(
				vertex, normal, color, tris, idx,
				(-spike_w / 2, y_c - 0.08 * hs, base_z), (spike_w / 2, y_c + 0.08 * hs, base_z + spike_h), hair,
			)
	elif hairstyle == "buzz":
		# An extremely thin cap hugging the head closely (barely poking
		# out at all, unlike "short"'s +0.05) - a genuinely shorter
		# silhouette, not just a recolor of "short".
		idx = _box(
			vertex, normal, color, tris, idx,
			(-HEAD_S / 2 - 0.015 * hs, -HEAD_S / 2 - HAIR_FRONT_EPSILON * hs, hh0 + 0.05 * hs), (HEAD_S / 2 + 0.015 * hs, HEAD_S / 2 + 0.015 * hs, head_z0 + HEAD_S + 0.03 * hs), hair,
		)
		idx = _hair_back_fill(vertex, normal, color, tris, idx, hair, head_z0, HEAD_S, hh0 + 0.05 * hs)
	elif hairstyle == "undercut":
		# Hair only across the TOP (narrower than the full head width),
		# leaving the sides bare/shaved - a real, distinct silhouette
		# from "short"'s full-width cap.
		idx = _box(
			vertex, normal, color, tris, idx,
			(-HEAD_S / 2 + 0.06 * hs, -HEAD_S / 2 - HAIR_FRONT_EPSILON * hs, hh0), (HEAD_S / 2 - 0.06 * hs, HEAD_S / 2 + 0.05 * hs, head_z0 + HEAD_S + 0.08 * hs), hair,
		)
	elif hairstyle == "curly":
		# A low base cap plus several small bump boxes scattered across
		# the top at different offsets/heights for a bumpy, curly
		# texture - a real, distinct approach from "afro"'s one smooth
		# big poof.
		idx = _box(
			vertex, normal, color, tris, idx,
			(-HEAD_S / 2 - 0.05 * hs, -HEAD_S / 2 - HAIR_FRONT_EPSILON * hs, hh0), (HEAD_S / 2 + 0.05 * hs, HEAD_S / 2 + 0.05 * hs, head_z0 + HEAD_S + 0.05 * hs), hair,
		)
		idx = _hair_back_fill(vertex, normal, color, tris, idx, hair, head_z0, HEAD_S, hh0)
		base_z = head_z0 + HEAD_S + 0.05 * hs
		bumps = [(-0.22 * hs, -0.15 * hs, 0.07 * hs), (0.05 * hs, -0.2 * hs, 0.09 * hs), (0.25 * hs, -0.1 * hs, 0.06 * hs), (-0.05 * hs, 0.15 * hs, 0.08 * hs), (0.2 * hs, 0.2 * hs, 0.07 * hs), (-0.25 * hs, 0.15 * hs, 0.06 * hs)]
		for bx, by, bh in bumps:
			idx = _box(
				vertex, normal, color, tris, idx,
				(bx - 0.09 * hs, by - 0.09 * hs, base_z), (bx + 0.09 * hs, by + 0.09 * hs, base_z + bh), hair,
			)
	else:
		# "short" cap (base for short/medium/long/ponytail) - a top/
		# back/sides cap only, sitting well above eye level.
		idx = _box(
			vertex, normal, color, tris, idx,
			(-HEAD_S / 2 - 0.05 * hs, -HEAD_S / 2 - HAIR_FRONT_EPSILON * hs, hh0), (HEAD_S / 2 + 0.05 * hs, HEAD_S / 2 + 0.05 * hs, head_z0 + HEAD_S + 0.08 * hs), hair,
		)
		idx = _hair_back_fill(vertex, normal, color, tris, idx, hair, head_z0, HEAD_S, hh0)
		if hairstyle == "medium":
			# A modest back/side extension down to the nape only (well
			# short of "long"'s past-the-shoulders drape) - a real,
			# in-between silhouette, not just "long" scaled down.
			idx = _box(
				vertex, normal, color, tris, idx,
				(-HEAD_S / 2 - 0.05 * hs, 0.1 * hs, head_z0 - 0.05 * hs), (HEAD_S / 2 + 0.05 * hs, HEAD_S / 2 + 0.05 * hs, hh0), hair,
			)
		elif hairstyle == "long":
			# Extra back-drape hanging down past the shoulders - stays
			# on the +y (back) half only, well clear of the front face,
			# so it can never re-trigger the front-occlusion bug above.
			idx = _box(
				vertex, normal, color, tris, idx,
				(-HEAD_S / 2 - 0.05 * hs, 0.05 * hs, head_z0 - 0.35 * hs), (HEAD_S / 2 + 0.05 * hs, HEAD_S / 2 + 0.05 * hs, hh0), hair,
			)
		elif hairstyle == "ponytail":
			# A single narrow trailing tail at the back center, rather
			# than a full-width drape - a genuinely different silhouette
			# from "long", not the same box narrowed.
			tail_w = 0.16 * hs
			idx = _box(
				vertex, normal, color, tris, idx,
				(-tail_w / 2, HEAD_S / 2 - 0.05 * hs, head_z0 - 0.55 * hs), (tail_w / 2, HEAD_S / 2 + 0.15 * hs, hh0), hair,
			)
		elif hairstyle == "pigtails":
			# TWO narrow trailing tails, one off each side of the head
			# near the back - a genuinely different silhouette from a
			# single center ponytail (visible from the front, unlike
			# ponytail's own tail which hides directly behind the head).
			tail_w = 0.13 * hs
			for side in (-1, 1):
				tx = side * (HEAD_S / 2 - 0.03 * hs)
				idx = _box(
					vertex, normal, color, tris, idx,
					(tx - tail_w / 2, HEAD_S / 2 - 0.12 * hs, head_z0 - 0.35 * hs), (tx + tail_w / 2, HEAD_S / 2 + 0.1 * hs, hh0), hair,
				)
		elif hairstyle == "bun":
			# The base cap plus a small round bump at the back-top of
			# the head for a top-knot/bun look.
			bun_r = 0.11 * hs
			idx = _box(
				vertex, normal, color, tris, idx,
				(-bun_r, 0.05 * hs, head_z0 + HEAD_S + 0.03 * hs), (bun_r, 0.05 * hs + bun_r * 2, head_z0 + HEAD_S + 0.03 * hs + bun_r * 2), hair,
			)
		elif hairstyle == "afro":
			# A big rounded poof, faked in blocky style with a wide box
			# tapering to a narrower one on top. The wide layer only
			# starts well above the eyebrow's own z-range (head_z0 +
			# HEAD_S*0.8, vs. eyebrows topping out at head_z0+0.636) even
			# though it pokes out further forward than the cap below it -
			# the same "front face must never out-poke something at the
			# same z-height" rule that governs the eye-lid/hair ordering
			# elsewhere, just applied to keep the poof from swallowing
			# the eyebrows the way it would if it started at hh0.
			poof_r = HEAD_S / 2 + 0.22 * hs
			idx = _box(
				vertex, normal, color, tris, idx,
				(-poof_r, -poof_r, head_z0 + HEAD_S * 0.8), (poof_r, poof_r, head_z0 + HEAD_S * 1.3), hair,
			)
			top_r = HEAD_S / 2 + 0.08 * hs
			idx = _box(
				vertex, normal, color, tris, idx,
				(-top_r, -top_r, head_z0 + HEAD_S * 1.3), (top_r, top_r, head_z0 + HEAD_S * 1.45), hair,
			)

	return idx


# Gender is a deliberate, explicit choice (not left to the seed's own
# random draw, unlike colors/skirt-vs-pants) - a baseline chest/hip
# width modifier layered under weight/muscle/shape, not a totally
# separate body. Never touches hairstyle/clothing/color choices at all
# (those stay fully independent, same as a real Mii system) - only
# silhouette.
GENDER_MODS = {
	"boy": {"chest": 0.06, "hip": -0.03},
	"girl": {"chest": -0.02, "hip": 0.06},
	"nonbinary": {"chest": 0.02, "hip": 0.02},
}


def _apply_bodypart_plugins_static(vertex, normal, color, tris, idx, traits, metrics):
	"""The real extension point for a "bodypart" avatar plugin's static-
	portrait geometry (see avatar_plugin_backend.py's own module
	docstring for the full plugin format) - every installed bodypart
	plugin's own build_portrait(vertex, normal, color, tris, idx, box,
	outlined_box, traits, metrics) -> idx gets called here, in the same
	shared vertex/normal/color/tris buffer and with the same _box()/
	_outlined_box() primitives every built-in _build_*() function in
	this file already writes geometry through - full access, not a
	restricted sandboxed API, the same trust level this project's
	existing "motion" plugins already get (real Python modules, loaded
	via importlib, not a data-only format).

	`traits` is build_character()'s own **extra_traits - only the keys
	build_character() itself doesn't recognize, so in practice each
	installed plugin's own namespaced fields (see avatar_plugin_backend.
	installed_bodypart_fields() for the "plugin_<id>_<key>" naming) - a
	plugin reads its own keys out of this dict by name, same as any
	other trait-driven _build_*() function here does with its own
	explicit kwargs. `metrics` is a small, deliberately curated set of
	commonly-needed body layout numbers/colors (not this function's
	entire 50+-parameter internal signature, which is exactly the kind
	of internal detail a third party shouldn't structurally depend on) -
	see the one real call site below for exactly what it contains.

	Deferred import (not at module level) so this module never has to
	import avatar_plugin_backend.py at load time just to support an
	extension point most processes importing avatar3d.py will never
	actually exercise - avatar_plugin_backend.py doesn't import this
	module back (no real cycle to dodge, just no reason to pay the
	extra import cost up front). Every installed plugin's own
	build_portrait runs inside its own try/except - one broken third-
	party plugin must never take down every single avatar render in the
	app, a real, deliberately different failure mode than an explicit
	"run this plugin" action would want (raise, don't silently skip)."""
	import avatar_plugin_backend

	for plugin_id, build_fn in avatar_plugin_backend.installed_bodypart_builders():
		try:
			result = build_fn(vertex, normal, color, tris, idx, _box, _outlined_box, traits, metrics)
			if result is not None:
				idx = result
		except Exception:
			log.exception(f"bodypart plugin {plugin_id!r} failed during static portrait render - skipped")
	return idx


def build_character(
	skin_color, hair_color, shirt_color, pants_color, eye_color, shoe_color,
	eyebrow_color=None, bottom_style="pants", top_style="long_sleeve", cropped=False, hood_up=False,
	pants_style="straight", shorts_style="regular", skirt_style="a_line",
	hairstyle="short", eye_shape="round", nose_shape="straight", mouth_style="neutral",
	facial_hair="none", facial_hair_color=None, freckles=False, blush=False,
	accessories=(),
	drawstring_color="#f4f7ff",
	ears="none", animal_color=None,
	ear_scale=1.0, ear_thickness=1.0, ear_depth=1.0,
	pet_species="none", pet_color="#8b5e3c",
	shirt_pattern="solid", shirt_pattern_color=None, pants_pattern="solid", pants_pattern_color=None,
	horns="none", horn_color=None, halo=False,
	horn_scale=1.0, horn_thickness=1.0,
	leg_type="human", missing_arm="none", missing_leg="none", missing_eye="none",
	prosthetics=(), prosthetic_color=PROSTHETIC_COLOR_DEFAULT,
	wheelchair=False, wheelchair_color="#2a2a3a", wheelchair_tire_color="#0f0f11",
	gender="nonbinary", weight=0.5, muscle=0.5, shape=0.5,
	pants_accent_color=None, shirt_accent_color=None, stitch_color=None,
	eye_color_right=None, iris_ring=False, skin_patches=False, skin_patch_color=None, age=0.5,
	**extra_traits,
):
	"""Mii-concept/Minecraft-blocky character - NOT seed-random anymore.
	Every color AND every style choice (hairstyle/eye_shape/nose_shape/
	mouth_style/top_style/bottom_style/accessories/hat_style, on top of
	the pre-existing gender/weight/muscle/shape) is a real, explicit
	pick a person makes about their own avatar (the *_COLORS/
	HAIRSTYLES/EYE_SHAPES/NOSE_SHAPES/MOUTH_STYLES/TOP_STYLES/
	BOTTOM_STYLES/ACCESSORIES/HAT_STYLES lists above are just the
	offered options for a picker UI to choose from) - the same
	"explicit, not left to chance" reasoning applied consistently
	everywhere in this generator.
	eyebrow_color defaults to hair_color (the most common real case) but
	is its own independent parameter - a direct answer to "should
	eyebrows always match hair" being "no, let it be different."
	accessories is a list, not a single choice (glasses + a hat + a bag
	can all be worn at once, unlike hairstyle/top_style/bottom_style
	which are each mutually exclusive by nature). cropped is likewise
	its own independent, orthogonal toggle rather than a separate
	top_style value - a direct correction from an earlier pass that made
	"crop_top" its own mutually-exclusive style: cropping should be
	combinable with ANY top_style (a cropped tank top, a cropped
	hoodie, a cropped jacket, ...), not just one specific silhouette on
	its own. hood_up only does anything when top_style == "hoodie" -
	it's a state of that one garment (hood up replaces the collar, it
	doesn't stack with it), not a general toggle like cropped.
	facial_hair is a single choice like hairstyle (a face has one beard/
	mustache state, not a combinable list), independent of gender - a
	real customization choice, not tied to "boy"/"girl"/"nonbinary".
	freckles/blush are simple independent booleans, same reasoning as
	cropped. ears/tail are each a single choice like hairstyle,
	but INDEPENDENT of each other (and of hairstyle itself) - a real
	furry customization mixes and matches species traits rather than
	picking one fixed species; animal_color defaults to hair_color so
	ears/tail match hair unless given their own color.
	horns/wings are each a single choice like ears, independent
	of it and of each other. halo is a simple boolean, same reasoning as
	cropped/freckles/blush.

	muzzle/whiskers/extra_eyes/snout_color/antennae/paws/paw_color/
	claws/claw_color/fur_pattern/pattern_color are NOT named parameters
	here anymore - each is its own built-in "bodypart" plugin now
	(avatar_plugins_builtin/bodypart/muzzle|whiskers|extra_eyes|
	antennae|paws|claws|fur_pattern/), resolved from **extra_traits
	below instead. fur_pattern's own arm-sleeve portion moved out
	alongside its face portion (see _build_face()'s own docstring) -
	`top_style` lands in `metrics` purely so that plugin can tell
	whether the arms are actually bare. This function's own paws/claws
	geometry was always purely additive (a "poke out to override" box
	overlay, not an exclusive branch), so both moved out cleanly here -
	`paws` is still resolved locally (just not passed to anything of
	this function's own) purely so it can land in `metrics` for the
	"claws" plugin to read, matching whatever the "paws" plugin itself
	drew (or didn't). muzzle stays a partial exception: _build_face()
	still needs a real `muzzle` boolean as a GATE (to skip drawing the
	normal nose+mouth), resolved from extra_traits right before the call
	that needs it - only the snout-replacement geometry itself moved to
	that plugin, not the gate (the same "keep what other code depends
	on, move only the geometry" split already applied to ears/horns/
	halo, and separately to avatar3d_rig.build_rig()'s own `paws` -
	_build_hand() there DOES have that same exclusive-branch gate, see
	its own docstring).
	missing_arm/missing_leg/missing_eye each pick a side ("none"/
	"left"/"right"/"both") rather than being a single flattening
	disability toggle - real, independent, combinable choices, the same
	"explicit, not a fixed package" philosophy as everything else here.
	prosthetics is a list like accessories (a person can have a
	prosthetic arm AND leg). wheelchair replaces the standing leg pose
	with a seated one and adds a real chair+wheels, rather than just
	tacking a chair prop onto standing legs.
	eye_color_right/iris_ring/skin_patches/skin_patch_color/age match
	avatar3d_rig.build_rig()'s own identical params exactly (both call
	the same shared _build_face() for this part of the head) - age (0
	young .. 1 elderly, 0.5 a neutral adult default) drives wrinkles/age
	spots past its own ELDERLY_AGE threshold; hair/eyebrow graying and
	the rig's own posture-stoop effect are rig-only (this static path has
	no posable "stoop", and graying an already-fixed hair_color here
	would just be a second, redundant way to ask for the same grayish
	color). Returns a Panda3D NodePath (not yet parented to any scene).

	**extra_traits catches any keyword this function doesn't itself
	recognize, rather than raising - a saved profile's traits dict is
	always splatted straight in as **traits by every real caller (see
	profiles.picture_traits_path()'s own docstring on why traits are
	stored as one flat dict, never a fixed schema), and an installed
	"bodypart" plugin (see avatar_plugin_backend.py) can add its own
	trait keys to that same dict via avatar3d_viewer's own dynamic
	CUSTOM_FIELDS. Without this catch-all, a profile with even one
	plugin-contributed trait value saved would crash every single
	render anywhere that plugin ends up uninstalled or unavailable -
	the same "never let a removed/foreign key break the core render"
	principle the animal_ears/groin_* trait migrations in profiles.py
	already established, just handled by accepting the key here instead
	of by stripping it there. Passed through unused to
	_apply_bodypart_plugins() below - see its own docstring."""
	# A direct, blunt report after seeing it live: trying to make
	# ears/horns/halo track every hairstyle's own height (via
	# hair_top_z) technically worked, but voluminous styles (afro,
	# curly, bun, spiky, ponytail, pigtails, long, medium) still looked
	# visually wrong poking ears/horns/a halo out of/through them - more
	# hair volume for these features to fight, not less. Rather than
	# keep chasing per-style tuning, hairstyle is restricted to a
	# compatible set (short/minimal, low-volume) WHENEVER any of these
	# three are active, silently overriding an incompatible request
	# rather than rendering a bad combination.
	if (ears != "none" or horns != "none" or halo) and hairstyle not in ("short", "buzz", "bald", "undercut"):
		hairstyle = "short"
	skin = _hex_to_rgba(skin_color)
	hair = _hex_to_rgba(hair_color)
	eyebrows = _hex_to_rgba(eyebrow_color) if eyebrow_color else hair
	shirt = _hex_to_rgba(shirt_color)
	pants = _hex_to_rgba(pants_color)
	# Seam/hem/stitch colors - see avatar_rig.build_rig's own identical
	# params for why (each defaults to a darker shade of its own
	# garment, stitch to a plain off-white thread, unless given
	# explicitly). pants_accent_color has no effect here yet - this
	# static path's own pants (_build_leg_pants) don't have a seam/hem
	# the way the rig's jointed legs do - kept for API symmetry with
	# build_rig rather than silently accepting and ignoring it.
	shirt_accent_c = _hex_to_rgba(shirt_accent_color) if shirt_accent_color else _shade(shirt, 0.7)
	stitch_c = _hex_to_rgba(stitch_color) if stitch_color else STITCH_THREAD_COLOR
	# shirt_pattern_c - same "defaults to the garment's own accent color"
	# convention as build_rig's own identical derivation - now genuinely
	# needed: the torso's own fabric pattern is drawn by the shared
	# _build_torso() call below, which takes this pre-derived RGBA
	# rather than the raw hex shirt_pattern_color. pants_pattern still
	# has no effect here - build_rig's own jointed legs/arms call
	# _apply_fabric_pattern() on themselves (_build_leg_thigh/
	# _build_leg_shin/_build_arm_upper/_build_arm_forearm), but this
	# static path's own separate leg/arm-drawing code below never grew
	# the equivalent calls - kept for API symmetry with build_rig rather
	# than silently accepting and ignoring it, same as pants_accent_color
	# above.
	shirt_pattern_c = _hex_to_rgba(shirt_pattern_color) if shirt_pattern_color else shirt_accent_c
	eyes = _hex_to_rgba(eye_color)
	eyes_r = _hex_to_rgba(eye_color_right) if eye_color_right else eyes
	shoes = _hex_to_rgba(shoe_color)
	facial_hair_c = _hex_to_rgba(facial_hair_color) if facial_hair_color else hair
	drawstring = _hex_to_rgba(drawstring_color)
	animal = _hex_to_rgba(animal_color) if animal_color else hair
	# muzzle: not a named parameter anymore (see this function's own
	# docstring) - resolved from extra_traits here, right before
	# _build_face() needs it as a gate.
	muzzle = bool(extra_traits.get("muzzle", False))
	# paws: not a named parameter anymore (see this function's own
	# docstring) - resolved here purely so the "claws" bodypart plugin
	# can read metrics["paws"] to match claw-tip positioning to whatever
	# "paws" itself drew (or didn't).
	paws = bool(extra_traits.get("paws", False))
	skin_patch_c = _hex_to_rgba(skin_patch_color) if skin_patch_color else None
	horn_c = _hex_to_rgba(horn_color) if horn_color else _hex_to_rgba(HORN_COLOR_DEFAULT)
	prosthetic_c = _hex_to_rgba(prosthetic_color)
	wheelchair_c = _hex_to_rgba(wheelchair_color)
	tire_c = _hex_to_rgba(wheelchair_tire_color)

	fmt = GeomVertexFormat.getV3n3c4()
	vdata = GeomVertexData("character", fmt, Geom.UHStatic)
	vertex = GeomVertexWriter(vdata, "vertex")
	normal = GeomVertexWriter(vdata, "normal")
	color = GeomVertexWriter(vdata, "color")
	tris = GeomTriangles(Geom.UHStatic)
	idx = 0

	# Proportions: feet at z=0, head top around z=3.15 - a moderate
	# (not chibi-huge) Minecraft-like head-to-body ratio, for an
	# ADULT_AGE (0.5, the default) character - see _age_body_scale's own
	# docstring for how a younger age reproportions these toward a
	# child's own bigger-headed, shorter, narrower silhouette instead.
	LEG_H, TORSO_H, HEAD_S = 1.15, 1.15, 0.7
	head_scale, body_scale = _age_body_scale(age)
	LEG_H *= body_scale
	TORSO_H *= body_scale
	HEAD_S *= head_scale

	gmod = GENDER_MODS.get(gender, GENDER_MODS["nonbinary"])
	weight_scale = 0.82 + weight * 0.55  # overall girth, 0.82x (thin) .. 1.37x (heavy)
	chest_half_w = (0.32 + muscle * 0.10 + gmod["chest"]) * weight_scale * body_scale
	hip_half_w = (0.28 + shape * 0.14 + gmod["hip"]) * weight_scale * body_scale
	limb_half_w = (0.15 + muscle * 0.05 + weight * 0.04) * (1 if weight_scale > 1 else weight_scale) * body_scale

	# Legs (pants/shorts) or a skirt/dress silhouette - sized off
	# hip_half_w, not a fixed constant, so shape/weight/gender all
	# visibly affect them regardless of which bottom_style is picked.
	# wheelchair REPLACES this whole block with a real seated pose (a
	# wheelchair user isn't a standing character with a chair prop
	# glued on) - the character's own hip/torso height (z=LEG_H) is
	# deliberately left unchanged rather than reworked to a true seated
	# height, a scope trade-off avoiding a cascading rework of every
	# other system (torso/arms/head/hair/eyes) that keys off LEG_H.
	if wheelchair:
		# Seat/legs/footrests/backrest/handles - see _build_wheelchair_frame
		# for the full history of fixes baked into these exact offsets
		# (bigger wheels, right-angle legs on a footrest, a backrest that
		# actually clears the head/arms to stay visible).
		idx = _build_wheelchair_frame(
			vertex, normal, color, tris, idx, hip_half_w, limb_half_w, missing_leg, bottom_style,
			shirt, pants, shoes, wheelchair_c, LEG_H, HEAD_S, TORSO_H,
		)
		# Wheels - kept separate from the frame above (see _build_wheel)
		# since each one needs its own pivot when spun for a rig/animated
		# rendering rather than the frame's static geometry.
		for side in (-1, 1):
			idx = _build_wheel(vertex, normal, color, tris, idx, side, hip_half_w, LEG_H, tire_c, wheelchair_c)
	elif bottom_style in ("skirt", "dress"):
		# A real, confirmed-live bug lived here: the flare box used to
		# span z=0 (the ankle/floor) all the way up, which is fully
		# opaque and MORE forward (-0.3) than the bare-leg/shoe boxes
		# drawn in that exact same z/x region (-0.15/-0.18) - so the
		# "skirt" was actually hiding the legs and shoes underneath it
		# entirely and rendering as floor-length every time, regardless
		# of skirt_style. The fix (applied to every skirt_style below):
		# the garment's own z-range must stop at a real hem height
		# ABOVE 0.25 (where the shoe box starts) so the bare-leg/shoe
		# geometry sits in a z-range the garment never touches at all,
		# rather than relying on depth-ordering to "win" a fight it
		# can't win. "dress" reuses this exact geometry, just colored
		# with shirt (not pants) so it reads as one continuous garment
		# with the torso above rather than a separate skirt piece.
		garment = shirt if bottom_style == "dress" else pants
		if skirt_style == "pencil":
			# Straight, no flare at all - same width top to bottom.
			hem_z = LEG_H * 0.5
			idx = _box(vertex, normal, color, tris, idx, (-hip_half_w, -0.25, hem_z), (hip_half_w, 0.25, LEG_H), garment)
		elif skirt_style == "mini":
			# Short hem, high up - a single band, not two-tier, since
			# there's not enough length left for a separate hip band.
			hem_z = LEG_H * 0.72
			idx = _box(vertex, normal, color, tris, idx, (-hip_half_w - 0.1, -0.28, hem_z), (hip_half_w + 0.1, 0.28, LEG_H), garment)
		elif skirt_style == "pleated":
			# A hip band plus a fluted hem built from several thin
			# vertical strips at alternating depth - a blocky
			# approximation of pleats, matching this whole model's
			# "don't fight the Minecraft aesthetic with smooth curves"
			# approach already used for "sharp" eyes.
			hem_z = LEG_H * 0.42
			band_z = LEG_H * 0.75
			idx = _box(vertex, normal, color, tris, idx, (-hip_half_w, -0.25, band_z), (hip_half_w, 0.25, LEG_H), garment)
			n = 6
			total_w = hip_half_w * 2 + 0.26
			seg_w = total_w / n
			for i in range(n):
				x0 = -hip_half_w - 0.13 + i * seg_w
				depth = 0.3 if i % 2 == 0 else 0.2
				idx = _box(vertex, normal, color, tris, idx, (x0, -depth, hem_z), (x0 + seg_w - 0.008, depth, band_z), garment)
		else:  # "a_line" (default) - flares out toward the hem
			hem_z = LEG_H * 0.42
			band_z = LEG_H * 0.75
			idx = _box(vertex, normal, color, tris, idx, (-hip_half_w - 0.15, -0.3, hem_z), (hip_half_w + 0.15, 0.3, band_z), garment)
			idx = _box(vertex, normal, color, tris, idx, (-hip_half_w, -0.25, band_z), (hip_half_w, 0.25, LEG_H), garment)
		for side in (-1, 1):
			side_name = "left" if side == -1 else "right"
			if missing_leg in (side_name, "both"):
				continue
			lx0 = side * hip_half_w * 0.5 - limb_half_w
			if hem_z > 0.25:
				idx = _box(vertex, normal, color, tris, idx, (lx0, -0.15, 0.25), (lx0 + limb_half_w * 2, 0.15, hem_z), skin)
			idx = _box(vertex, normal, color, tris, idx, (lx0, -0.18, 0), (lx0 + limb_half_w * 2, 0.2, 0.25), shoes)
			if leg_type == "digitigrade":
				idx = _leg_hock_spur(vertex, normal, color, tris, idx, lx0, limb_half_w * 2, skin)
	elif bottom_style == "overalls":
		# Straight pants-colored legs (the bib + straps that make this
		# read as overalls rather than plain pants are added near the
		# torso, below, since they reach up over the chest/shoulders).
		for side in (-1, 1):
			side_name = "left" if side == -1 else "right"
			if missing_leg in (side_name, "both"):
				continue
			lx0 = side * hip_half_w * 0.5 - limb_half_w
			idx = _box(vertex, normal, color, tris, idx, (lx0, -0.2, 0.25), (lx0 + limb_half_w * 2, 0.2, LEG_H), pants)
			idx = _box(vertex, normal, color, tris, idx, (lx0, -0.22, 0), (lx0 + limb_half_w * 2, 0.24, 0.25), shoes)
			if leg_type == "digitigrade":
				idx = _leg_hock_spur(vertex, normal, color, tris, idx, lx0, limb_half_w * 2, skin)
	elif bottom_style == "shorts":
		thigh_z = {"bermuda": LEG_H * 0.85, "athletic": LEG_H * 0.45}.get(shorts_style, LEG_H * 0.62)
		for side in (-1, 1):
			side_name = "left" if side == -1 else "right"
			if missing_leg in (side_name, "both"):
				continue
			lx0 = side * hip_half_w * 0.5 - limb_half_w
			idx = _box(vertex, normal, color, tris, idx, (lx0, -0.2, thigh_z), (lx0 + limb_half_w * 2, 0.2, LEG_H), pants)
			idx = _box(vertex, normal, color, tris, idx, (lx0, -0.15, 0.25), (lx0 + limb_half_w * 2, 0.15, thigh_z), skin)
			idx = _box(vertex, normal, color, tris, idx, (lx0, -0.18, 0), (lx0 + limb_half_w * 2, 0.2, 0.25), shoes)
			if leg_type == "digitigrade":
				idx = _leg_hock_spur(vertex, normal, color, tris, idx, lx0, limb_half_w * 2, skin)
	else:  # "pants" - pants_style picks width (straight/skinny/baggy)
		# and whether the leg is full-length or "cropped" (stops short
		# of the ankle, exposing bare skin above the shoe - the same
		# hem-above-0.25 pattern the skirt fix above uses).
		for side in (-1, 1):
			idx = _build_leg_pants(
				vertex, normal, color, tris, idx, side, hip_half_w, limb_half_w,
				pants_style, leg_type, missing_leg, skin, pants, shoes, LEG_H,
			)

	if ("left_leg" in prosthetics or "right_leg" in prosthetics) and not wheelchair:
		# A prosthetic leg - recolored (prosthetic_color) with a joint
		# ring, applied as an overlay poking past whatever the
		# bottom_style branch above already drew there, rather than
		# touching each of those 4 branches individually (the same
		# "poke out to override" technique "paws" already uses to
		# replace shoes/hands generically). Skipped under wheelchair,
		# whose own seated-leg drawing already has a different shape
		# this overlay isn't built to match.
		for side in (-1, 1):
			side_name = "left" if side == -1 else "right"
			if f"{side_name}_leg" not in prosthetics or missing_leg in (side_name, "both"):
				continue
			lx0 = side * hip_half_w * 0.5 - limb_half_w
			idx = _outlined_box(
				vertex, normal, color, tris, idx,
				(lx0 - 0.01, -0.21, 0.24), (lx0 + limb_half_w * 2 + 0.01, 0.21, LEG_H), prosthetic_c,
			)
			joint_z = LEG_H * 0.55
			idx = _outlined_box(
				vertex, normal, color, tris, idx,
				(lx0 - 0.015, -0.22, joint_z - 0.03), (lx0 + limb_half_w * 2 + 0.015, 0.22, joint_z + 0.03), (0.35, 0.35, 0.37, 1),
			)

	# tail is NOT drawn here anymore - it's its own built-in "bodypart"
	# feature now (avatar_plugins_builtin/bodypart/tail/), drawn later
	# still by the bodypart plugin hook.

	# Pet companion, standing beside the person (offset clear of the
	# widest possible hip_half_w - see avatar3d_pet.build_pet_static's
	# own docstring for why this is baked straight into the shared
	# absolute-coordinate buffer instead of a separate NodePath: this
	# whole function already merges everything, person included, into
	# ONE mesh). Lazy top-level import (matching this file's own
	# render_portrait()/texture_to_pil() precedent for PIL) rather than
	# a real module-level import, since avatar3d_pet.py itself imports
	# avatar3d - a real circular import that's only safe deferred past
	# both modules' own top-level code finishing.
	import avatar3d_pet
	pet_c = _hex_to_rgba(pet_color)
	idx = avatar3d_pet.build_pet_static(
		vertex, normal, color, tris, idx, pet_species, pet_c, _shade(pet_c, 0.7), _hex_to_rgba(avatar3d_pet.BEAK_COLOR_DEFAULT),
		x_offset=hip_half_w + 0.45,
	)

	# Torso (shirt) - hip/waist/chest bands (a waist needs 3 stacked
	# boxes, not 2 - see _build_torso's own docstring for why 2 alone
	# can't show a waist shape at all), top_style's own collar/hood/
	# lapel/turtleneck neckline, and "overalls"'s bib+straps - no torso-
	# worn accessories anymore, they're all their own built-in bodypart
	# features now (see _build_torso's own docstring) - shared verbatim
	# with avatar3d_rig.build_rig()'s own identical static torso via
	# _build_torso() (see its own docstring). `cropped` recolors the
	# hip+waist bands (not just one box) to skin instead of shirt,
	# exposing the midriff - independent of top_style (a cropped tank
	# top, a cropped hoodie, a cropped jacket, ... are all valid), not
	# its own mutually-exclusive style.
	idx = _build_torso(
		vertex, normal, color, tris, idx, LEG_H, TORSO_H, HEAD_S, hip_half_w, chest_half_w, skin, shirt,
		shirt_accent_c, stitch_c, shirt_pattern, shirt_pattern_c, cropped, top_style, hood_up, drawstring,
		bottom_style, pants, accessories,
	)

	# Arms - attached at chest width (the shoulder point), inner edge
	# overlapping the torso slightly so there's no visible gap between
	# them. top_style decides how much of the arm is sleeve (shirt) vs.
	# bare (skin): long_sleeve/hoodie/collared/jacket/turtleneck cover
	# the whole arm, short_sleeve covers only the upper arm with the
	# forearm bare, tank_top/vest are bare the whole way down (no
	# sleeve at all) - independent of `cropped`, which only ever
	# affects the torso.
	for side in (-1, 1):
		side_name = "left" if side == -1 else "right"
		if missing_arm in (side_name, "both"):
			# A real, independent accessibility choice - just skip this
			# arm's geometry entirely, same "explicit side, not a fixed
			# package" reasoning as missing_leg/missing_eye.
			continue
		outer = side * (chest_half_w + limb_half_w * 2)
		inner = side * chest_half_w
		lo_x, hi_x = min(inner, outer), max(inner, outer)
		if f"{side_name}_arm" in prosthetics:
			# A prosthetic/robotic arm - recolored (prosthetic_color,
			# independent of shirt_color) with a darker joint-ring
			# accent at roughly elbow height, overriding whatever
			# top_style's own sleeve logic would have drawn for this
			# arm specifically (the OTHER arm, if not prosthetic, still
			# follows top_style normally).
			idx = _outlined_box(vertex, normal, color, tris, idx, (lo_x, -0.18, LEG_H), (hi_x, 0.18, LEG_H + TORSO_H), prosthetic_c)
			joint_z = LEG_H + TORSO_H * 0.55
			idx = _outlined_box(
				vertex, normal, color, tris, idx,
				(lo_x - 0.005, -0.19, joint_z - 0.03), (hi_x + 0.005, 0.19, joint_z + 0.03), (0.35, 0.35, 0.37, 1),
			)
		elif top_style in ("tank_top", "vest"):
			idx = _box(vertex, normal, color, tris, idx, (lo_x, -0.18, LEG_H), (hi_x, 0.18, LEG_H + TORSO_H), skin)
		elif top_style == "short_sleeve":
			sleeve_z = LEG_H + TORSO_H * 0.72
			idx = _box(vertex, normal, color, tris, idx, (lo_x, -0.18, sleeve_z), (hi_x, 0.18, LEG_H + TORSO_H), shirt)
			idx = _box(vertex, normal, color, tris, idx, (lo_x, -0.16, LEG_H), (hi_x, 0.16, sleeve_z), skin)
		else:  # "long_sleeve"/"hoodie"/"collared"/"jacket"/"turtleneck" - full-length sleeve
			idx = _box(vertex, normal, color, tris, idx, (lo_x, -0.18, LEG_H), (hi_x, 0.18, LEG_H + TORSO_H), shirt)

	# top_style's collar/hood/lapel/turtleneck neckline and "overalls"'s
	# bib+straps are drawn by the _build_torso() call above (see its own
	# docstring) - no longer duplicated here. backpack/satchel/purse/
	# necklace/tie/wristband/socks/gloves are all drawn later still, by
	# the bodypart plugin hook at the very end of this function (see
	# _apply_bodypart_plugins_static()). Unlike those, the RIG path's
	# own "gloves" doesn't draw a bolt-on box the same way - it recolors
	# the palm in place inside _build_hand() instead (see that
	# function's own docstring on why it can't be extracted as a bolt-
	# on there) - the gloves plugin's build_animated() is a deliberate
	# no-op for that reason.

	# Head (skin)
	head_z0 = LEG_H + TORSO_H
	# Every fixed-size feature below (hair/ears/horns/antennae/halo/
	# eyes/eyebrows/nose/mouth/facial_hair/freckles/blush/muzzle/
	# whiskers/glasses/hats/headphones - literally everything in this
	# whole head section) is built from absolute-unit offsets from a
	# HEAD_S-scaled anchor (head_z0, hh0, eye_z, ...), not as fractions
	# of HEAD_S itself. A real, confirmed-live gap: HEAD_S was reduced
	# (0.85 -> 0.7, "the head size feels massive") for the rig's own
	# smaller head-building function and rescaled there via this same
	# `hs` technique, but this much bigger function - 12 hairstyles, 9+
	# ear species, horns, antennae, halo, 5 eye_shapes, 6 mouth_styles,
	# 6 nose_shapes, facial hair, freckles, blush, muzzle, whiskers,
	# glasses, hats, headphones - never got the equivalent treatment,
	# so every one of those branches was still sized for the old 0.85
	# head. Now that this path is about to be user-facing (character
	# customization + seed saving), it needs to actually look right
	# across all of them, not just the rig's single round-eyes/neutral-
	# mouth/short-hair combo. `hs` rescales every literal relative to
	# the HEAD_S=0.85 value each was originally tuned against.
	hs = HEAD_S / 0.85
	idx = _box(
		vertex, normal, color, tris, idx,
		(-HEAD_S / 2, -HEAD_S / 2, head_z0), (HEAD_S / 2, HEAD_S / 2, head_z0 + HEAD_S), skin,
	)

	# Hair/animal-ears/horns/antennae/halo - shared with
	# avatar3d_rig.py's own animated head bundle via
	# _build_hair_and_toppers() (see its own docstring for exactly what
	# it does and doesn't cover - hat/headphones stay right here, out of
	# that shared function's scope). wings are NOT built here anymore -
	# they're their own built-in "bodypart" feature now
	# (avatar_plugins_builtin/bodypart/wings/), drawn later still by the
	# bodypart plugin hook.
	idx = _build_hair_and_toppers(
		vertex, normal, color, tris, idx, hairstyle, hair, head_z0, HEAD_S, hs,
	)

	# Eyes/eyebrows/extra-eye/nose/mouth/muzzle/whiskers/facial_hair/
	# blush/freckles/wrinkles/skin-patches - shared with avatar3d_rig.py's
	# own animated head bundle via _build_face() (see its own docstring
	# for exactly what it does and doesn't cover). glasses/hat/
	# headphones/earrings/fur_pattern are NOT here anymore - each is its
	# own built-in "bodypart" feature now (avatar_plugins_builtin/bodypart/
	# glasses|hat|headphones|earrings|fur_pattern/), drawn later still by
	# the bodypart plugin hook, same as backpack/satchel/purse/necklace/
	# tie.
	idx = _build_face(
		vertex, normal, color, tris, idx, skin, eyes, eyebrows, eyes_r, eye_shape, nose_shape, mouth_style,
		muzzle, facial_hair, facial_hair_c, blush, freckles,
		missing_eye, iris_ring, head_z0, HEAD_S, hs, age=age, skin_patches=skin_patches, skin_patch_c=skin_patch_c,
	)
	# eye_z/nose_z used to be recomputed here for glasses/hat/headphones/
	# earrings/extra_eyes/whiskers/fur_pattern positioning - all fully
	# moved to their own bodypart plugins now (each recomputes these same
	# cheap HEAD_S-derived formulas itself, see e.g.
	# avatar_plugins_builtin/bodypart/glasses/bodypart.py), so nothing
	# here needs them anymore.

	# fur_pattern's own arm-sleeve overlay (tank_top/vest only, on
	# already-exposed skin) used to draw here too - fully moved out to
	# the "fur_pattern" bodypart plugin along with the face portion (see
	# _build_face()'s own docstring) - the rig never had this arm
	# portion at all (a real, pre-existing scope cut, not introduced by
	# this extraction), so the plugin's own build_animated() only needs
	# to cover the face.

	# paws/claws used to draw here (a wider paw-pad shape REPLACING both
	# hands and feet, plus independent claw tips) - both fully moved out
	# to their own bodypart plugins (avatar_plugins_builtin/bodypart/
	# paws|claws/), same as every other creature feature.

	idx = _apply_bodypart_plugins_static(
		vertex, normal, color, tris, idx, extra_traits,
		{
			"LEG_H": LEG_H, "TORSO_H": TORSO_H, "HEAD_S": HEAD_S,
			"hip_half_w": hip_half_w, "chest_half_w": chest_half_w, "limb_half_w": limb_half_w,
			"skin_color": skin_color, "shirt_color": shirt_color, "pants_color": pants_color,
			"skin": skin, "shirt": shirt, "pants": pants,
			"gender": gender, "weight": weight, "age": age,
			"animal": animal, "animal_color": animal_color, "hair": hair, "hair_color": hair_color,
			"hairstyle": hairstyle, "halo": halo,
			"horns": horns, "horn_color": horn_color, "horn_c": horn_c,
			"horn_scale": horn_scale, "horn_thickness": horn_thickness,
			"ears": ears, "ear_scale": ear_scale, "ear_thickness": ear_thickness, "ear_depth": ear_depth,
			"eyes": eyes,
			"muzzle": muzzle,
			"paws": paws,
			"top_style": top_style,
			"accessories": accessories,
		},
	)

	geom = Geom(vdata)
	geom.addPrimitive(tris)
	node = GeomNode("character")
	node.addGeom(geom)
	bottom_sub = {
		"pants": pants_style, "shorts": shorts_style, "skirt": skirt_style, "dress": skirt_style, "overalls": "standard",
	}[bottom_style]
	# hat_style used to be logged as "hat:<style>" here - it's no longer
	# one of this function's own named parameters (see build_portrait()
	# under avatar_plugins_builtin/bodypart/hat/ for where it lives now),
	# so this debug line just logs plain accessory membership again.
	acc_bits = list(accessories)
	face_bits = f"nose={nose_shape} mouth={mouth_style} facial_hair={facial_hair} freckles={freckles} blush={blush}"
	furry_bits = f"ears={ears} muzzle={muzzle} paws={paws}"
	# wings/tail/antennae/whiskers/extra_eyes/claws no longer logged
	# here - none
	# is one of this function's own named parameters anymore (see
	# build_portrait() under avatar_plugins_builtin/bodypart/
	# wings|tail|antennae|whiskers|extra_eyes/ for where they live now).
	# horns/halo stay real named parameters (see the hairstyle-override
	# check above) so they're still logged directly here.
	myth_bits = f"horns={horns} halo={halo}"
	body_bits = (
		f"leg_type={leg_type} missing_arm={missing_arm} missing_leg={missing_leg} missing_eye={missing_eye} "
		f"prosthetics={list(prosthetics)} wheelchair={wheelchair}"
		+ (f" wheelchair_color={wheelchair_color} tire_color={wheelchair_tire_color}" if wheelchair else "")
	)
	log_bits = (
		f"skin={skin_color} hair={hair_color}({hairstyle}) eyebrows={eyebrow_color or hair_color} "
		f"shirt={shirt_color}({top_style}{'/cropped' if cropped else ''}{'/hood_up' if hood_up else ''}) "
		f"pants={pants_color}({bottom_style}/{bottom_sub}) eyes={eye_color}({eye_shape}) {face_bits} {furry_bits} "
		f"{myth_bits} {body_bits} shoes={shoe_color} accessories={acc_bits} gender={gender}"
	)
	log.debug(f"build_character: {log_bits}")
	return NodePath(node)


class OffscreenRenderer(ShowBase):
	"""One shared Panda3D render context for the whole process - Panda3D's
	ShowBase is a real singleton (confirmed live: constructing a second
	one raises "Attempt to spawn multiple ShowBase instances!"), so this
	class is only ever instantiated once (see get_renderer()) even
	though many different call sites want their own differently-sized
	output - render_portrait() at whatever small-icon size a caller
	needs, avatar3d_viewer.py's own live animated preview at its own
	widget size. Each distinct size gets its own buffer/texture/camera
	(buffer_for_size()), all sharing this one instance's window/scene
	graph/lights rather than each trying to open a second ShowBase."""

	def __init__(self):
		super().__init__(windowType="offscreen")
		self._buffers = {}  # size -> (buffer, Texture, camera NodePath)
		self._portrait_buffers = {}  # size -> (buffer, Texture, camera NodePath), see portrait_buffer_for_size()

		alight = AmbientLight("alight")
		alight.setColor((0.4, 0.4, 0.45, 1))
		self.render.setLight(self.render.attachNewNode(alight))
		dlight = DirectionalLight("dlight")
		dlight.setColor((1, 1, 0.95, 1))
		dlnp = self.render.attachNewNode(dlight)
		dlnp.setHpr(-35, -55, 0)
		self.render.setLight(dlnp)

	def buffer_for_size(self, size):
		entry = self._buffers.get(size)
		if entry is not None:
			return entry
		fb_props = FrameBufferProperties()
		fb_props.setRgbaBits(8, 8, 8, 8)
		fb_props.setDepthBits(24)
		win_props = WindowProperties.size(size, size)
		buffer = self.graphicsEngine.makeOutput(
			self.pipe, f"offscreen buffer {size}", -2, fb_props, win_props,
			GraphicsPipe.BFRefuseWindow, self.win.getGsg(), self.win,
		)
		tex = Texture()
		buffer.addRenderTexture(tex, GraphicsOutput.RTMCopyRam)
		cam = self.makeCamera(buffer)
		cam.reparentTo(self.render)
		cam.setPos(0, -7.5, 1.65)
		cam.lookAt(0, 0, 1.65)
		entry = (buffer, tex, cam)
		self._buffers[size] = entry
		log.debug(f"OffscreenRenderer: opened a new {size}x{size} buffer ({len(self._buffers)} total)")
		return entry

	def portrait_buffer_for_size(self, size):
		"""A separate, head-and-shoulders-framed buffer/camera for an
		actual "profile picture" (avatar3d_portrait_anim.py's animated
		loop - see its own docstring on why it renders directly against a
		buffer instead of going through render_portrait()) - kept
		entirely separate from buffer_for_size()'s own whole-body camera
		rather than just repointing that one, since that framing is still
		exactly what AvatarCustomizePage's live customize preview needs
		(seeing the whole body/outfit while editing it, not just a
		headshot).

		head_z0 = LEG_H + TORSO_H (build_character()'s own local, 2.3 at
		this rig's base proportions) is where the neck/head sit; framed
		to keep some headroom above the top of the head (head_z0 +
		HEAD_S, 0.7 taller) down through the shoulders/upper chest just
		below head_z0, aimed at roughly eye height rather than the whole-
		body camera's mid-torso aim point."""
		entry = self._portrait_buffers.get(size)
		if entry is not None:
			return entry
		fb_props = FrameBufferProperties()
		fb_props.setRgbaBits(8, 8, 8, 8)
		fb_props.setDepthBits(24)
		win_props = WindowProperties.size(size, size)
		buffer = self.graphicsEngine.makeOutput(
			self.pipe, f"offscreen portrait buffer {size}", -2, fb_props, win_props,
			GraphicsPipe.BFRefuseWindow, self.win.getGsg(), self.win,
		)
		tex = Texture()
		buffer.addRenderTexture(tex, GraphicsOutput.RTMCopyRam)
		cam = self.makeCamera(buffer)
		cam.reparentTo(self.render)
		# A slight three-quarter angle (not a flat, straight-on mugshot) -
		# offset on X as well as pulled back on Y, still aimed at the same
		# head/shoulders point so the framing/headroom tuning above holds.
		cam.setPos(1.35, -3.0, 2.45)
		cam.lookAt(0, 0, 2.45)
		entry = (buffer, tex, cam)
		self._portrait_buffers[size] = entry
		log.debug(f"OffscreenRenderer: opened a new {size}x{size} portrait buffer ({len(self._portrait_buffers)} total)")
		return entry

	def texture_for_size(self, size):
		"""The raw Texture backing a given buffer size - for a caller (see
		avatar3d_viewer.py's own live frame loop) that poses a rig and
		renders/reads frames itself rather than going through
		render_character_to_image()'s own build-a-character-per-call
		shape."""
		_buffer, tex, _cam = self.buffer_for_size(size)
		return tex

	def render_character(self, out_path, size=400, **kwargs):
		_buffer, tex, _cam = self.buffer_for_size(size)
		char = build_character(**kwargs)
		char.reparentTo(self.render)
		char.setH(20)
		self.graphicsEngine.renderFrame()
		self.graphicsEngine.renderFrame()
		tex.write(out_path)
		char.removeNode()

	def render_character_to_image(self, size=400, **kwargs):
		"""Same render as render_character(), returned as an in-memory PIL
		Image instead of written to a path - what render_portrait() below
		actually wants (no real file ever needs to exist on disk for a
		picture that's regenerated fresh on demand, same reasoning
		avatar_gen.generate() already established)."""
		_buffer, tex, _cam = self.buffer_for_size(size)
		char = build_character(**kwargs)
		char.reparentTo(self.render)
		char.setH(20)
		self.graphicsEngine.renderFrame()
		self.graphicsEngine.renderFrame()
		image = texture_to_pil(tex)
		char.removeNode()
		return image


_renderer = None


def get_renderer():
	"""The one shared OffscreenRenderer for this whole process (see its
	own docstring on why there's only ever one) - lazily constructed on
	first real use, since importing this module (e.g. just to read
	SKIN_TONES/HAIRSTYLES/... for a picker UI) shouldn't require opening
	a real GPU context."""
	global _renderer
	if _renderer is None:
		_renderer = OffscreenRenderer()
	return _renderer


def texture_to_pil(tex):
	"""A rendered Texture -> a real, right-side-up PIL RGBA Image.
	getRamImageAs() forces a known byte order in C++ (fast) rather than
	the pure-Python per-pixel PNMImage.getRedVal()/getGreenVal()/...
	loop tried first, which was much too slow for anything resembling a
	live frame rate (160,000 individual Python-level calls per frame at
	just 400x400). OpenGL textures are stored bottom-to-top; PIL/GTK
	images are top-to-bottom - skipping the flip renders every frame
	upside down (confirmed live)."""
	from PIL import Image

	w, h = tex.getXSize(), tex.getYSize()
	raw = bytes(tex.getRamImageAs("RGBA"))
	return Image.frombytes("RGBA", (w, h), raw).transpose(Image.FLIP_TOP_BOTTOM)


def render_portrait(traits, size=256):
	"""A single static image from build_character(**traits) - the small-
	icon equivalent of avatar_gen.generate(seed, size): profile-picker
	tiles, the Dashboard widget, and anywhere else in the app that just
	needs one picture, not the live animated view (see
	avatar3d_viewer.py for that). `traits` is the same plain dict shape
	profiles.get_picture_traits() returns/avatar3d_rig.build_rig() takes
	(see avatar3d_viewer.CUSTOM_DEFAULTS) - build_character() simply
	falls back to its own defaults for every trait build_rig() doesn't
	expose (hairstyle, eye_shape, nose_shape, mouth_style, accessories,
	...), which is exactly what avatar3d_rig's own head bundle already
	hardcodes, so the static portrait and the live rig preview always
	agree."""
	return get_renderer().render_character_to_image(size=size, **traits)
