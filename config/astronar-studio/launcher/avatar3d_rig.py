"""A rigged, animatable variant of avatar3d's character generator.

`build_character()` bakes everything into ONE static merged mesh with no
joints. This module builds the same character as a real pivot-based rig
- pants-leg, shorts-leg, or digitigrade legs, wheelchair, every
top_style's own sleeve/bare-arm split, the full head richness
(hairstyle/eye_shape/nose_shape/mouth_style/facial_hair/freckles/blush/
muzzle/whiskers/fur_pattern), every clothing/accessory feature
build_character offers except skirt/dress (glasses/hat/backpack/satchel/
purse/necklace/earrings/tie/wristband/socks/gloves/headphones, paws/
claws, cropped midriff, "overalls" bib), ears/horns/halo/
extra_eyes/antennae/missing_eye, tail, wings - as separate small Geoms
for each animatable part (legs, arms, head/neck, wheelchair wheels),
each parented under a NodePath "joint" positioned at its real world
pivot point (hip/shoulder/neck/wheel-hub), so rotating the joint swings
the geometry correctly - the same idea as Minecraft's own skeleton model
(separate cuboids rotating around shoulder/hip/neck pivots).

Every animated part's geometry is produced by calling avatar3d's
own extracted sub-builders (`_build_leg_pants`, `_build_wheelchair_frame`,
`_build_wheel`, `_build_leg_thigh`/`_build_leg_shin`/`_build_foot`,
`_build_arm_upper`/`_build_arm_forearm`/`_build_hand`, `_build_torso`,
`_build_head_bundle`) - the exact same, already-debugged box math the
static renderer uses, not a second hand-written copy of it. See the
"Rigged, animated avatar characters" plan for the full design rationale
(world-point-offset pivot trick, HPR conventions, scope boundary)."""
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from panda3d.core import GeomVertexFormat, GeomVertexData, GeomVertexWriter, GeomTriangles, Geom, GeomNode, NodePath

import avatar3d as a3d

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import debuglog  # noqa: E402

log = debuglog.setup("avatar3d_rig")

# Must match avatar3d.build_character's own local constants exactly
# (not importable - they're defined inside that function, not at module
# level). Verified equal at the time of writing; if that function's own
# proportions ever change, these need to change with them.
# Named with an _ADULT suffix (rather than the plain LEG_H/TORSO_H/
# HEAD_S every callsite below actually uses) because build_rig() itself
# now derives its own real, possibly age-scaled LEG_H/TORSO_H/HEAD_S
# locals from these - Python treats a name assigned anywhere in a
# function as local for the ENTIRE function body, so if build_rig's
# local variables were named the same as these module constants,
# reading these adult baseline values on the right-hand side of that
# derivation would itself raise UnboundLocalError instead of seeing the
# module-level default.
LEG_H_ADULT, TORSO_H_ADULT, HEAD_S_ADULT = 1.15, 1.15, 0.7


def _new_buffers():
	fmt = GeomVertexFormat.getV3n3c4()
	vdata = GeomVertexData("part", fmt, Geom.UHStatic)
	vertex = GeomVertexWriter(vdata, "vertex")
	normal = GeomVertexWriter(vdata, "normal")
	color = GeomVertexWriter(vdata, "color")
	tris = GeomTriangles(Geom.UHStatic)
	return vdata, vertex, normal, color, tris


def _finish(vdata, tris, name):
	geom = Geom(vdata)
	geom.addPrimitive(tris)
	node = GeomNode(name)
	node.addGeom(geom)
	return NodePath(node)


def _pivoted_part(parent, name, world_point, build_fn, origin=None):
	"""Builds one small Geom via build_fn(vertex, normal, color, tris, 0) -> idx,
	whose geometry assumes real absolute world coordinates (the same math
	avatar3d's sub-builders already use, i.e. coordinates relative
	to `origin` - the rig's own root, NOT necessarily `parent` - see
	below), then rigs it to rotate around world_point: a `pivot`
	NodePath is placed AT world_point (expressed in `origin`'s
	coordinate space via Panda's own setPos(otherNodePath, ...), so this
	is correct no matter how many ancestors sit between `parent` and
	`origin` - e.g. a shoulder pivot parented under `waist`, itself a
	child of `root`, still lands at the right ROOT-relative height) and
	parented under `parent`; the built geometry is reparented under
	`pivot` and shifted by -world_point (relative to `pivot` itself,
	where no further ancestor chain is involved). At rest, this leaves
	the geometry sitting at `origin`-relative (0,0,0) plus its own
	baked-in absolute coordinates - i.e. exactly where it always was -
	but because the geometry's position is now relative to the pivot,
	rotating `pivot` swings it around the correct real-world joint
	point, with zero changes to the underlying box-authoring math.

	`origin` defaults to `parent` (the common case - a pivot attached
	directly to the rig's root, where "world_point relative to parent"
	and "world_point relative to origin" are the same thing).

	Returns (pivot, part) - both None if build_fn drew nothing at all
	(idx stayed 0 - e.g. a missing limb), since there's then nothing to
	pivot and no pivot should exist for callers to find."""
	origin = parent if origin is None else origin
	vdata, vertex, normal, color, tris = _new_buffers()
	idx = build_fn(vertex, normal, color, tris, 0)
	if idx == 0:
		return None, None
	part = _finish(vdata, tris, name)
	pivot = parent.attachNewNode(name + "_pivot")
	px, py, pz = world_point
	pivot.setPos(origin, px, py, pz)
	part.reparentTo(pivot)
	part.setPos(-px, -py, -pz)
	return pivot, part


# How far above/below knee_z the knee-cap filler box (see
# _build_shin_with_knee_cap) extends - small enough to read as part of
# the joint rather than a separate lump, big enough to actually bridge
# the gap a bent knee opens up.
KNEE_CAP_HALF_H = 0.05

# Finger/thumb sizing for build_rig()'s own per-digit pivots (see the
# "Fingers + thumb" block in the arm-building loop). FINGER_GAP matches
# the old fused-geometry version's own spacing between fingers exactly
# (kept as a separate constant now that fingers are built one at a time
# rather than in a single loop sharing local variables).
FINGER_LEN = 0.07
FINGER_GAP = 0.006
THUMB_LEN = 0.05

# The whole arm chain (shoulder/elbow/wrist/hand/fingers/thumb) is
# shifted toward the back (+y) by this much - a real, confirmed
# correction: the arm used to attach dead-centered front-to-back on the
# torso, but the real glenohumeral joint sits at the outer tip of the
# shoulder BLADE (a back bone), not centered on the chest. Checked
# against dimensions.com's own standing-figure proportions (shoulder
# width, chest depth) rather than guessed, per direct instruction to
# use it for every 3D-modeling proportion question from here on -
# though its own published figures stop at overall body measurements
# (18in shoulder width, ~10in chest depth) and don't break the skeleton
# down further, so this specific offset is reasoned from standard
# skeletal anatomy (the scapula sits on the back third of the ribcage)
# applied to those proportions, not a number dimensions.com states
# outright.
ARM_Y_OFFSET = 0.06

# Sign that makes waist.setP() lean the upper body FORWARD (see
# build_rig's own posture-stoop code, right before its `return rig`).
# A real, confirmed-live bug: the first version assumed the same
# "positive setP() = backward" convention already confirmed for hip/
# shoulder pivots also held for waist and used -1 - checked directly
# this time (queried a world position at both signs rather than
# assumed again) instead of re-guessing from the hip/shoulder result.
STOOP_SIGN = 1


def _build_shin_with_knee_cap(vertex, normal, color, tris, idx, side, hip_half_w, limb_half_w, pants_style, missing_leg, LEG_H, knee_z, ankle_z, pants, skin, accent_c=None, stitch_c=None, pattern="solid", pattern_c=None, bare_hem_z=None):
	"""Wraps avatar3d's own _build_leg_shin with one extra small
	box straddling the knee joint itself. The thigh and shin are two
	independently-rotating, flat-capped boxes that only stay flush at
	rest (see _build_leg_thigh/_build_leg_shin's own shared cross-
	section math) - once avatar_anim actually bends a knee, the shin's
	own flat top face tilts away from the pivot point on one side,
	opening a wedge-shaped gap that shows the hollow inside of the leg.
	This box is centered exactly on the knee pivot itself (same
	(lx, 0, knee_z) point _pivoted_part rotates around) and sized to the
	leg's own cross-section, so rotating the knee swings this box right
	along with the shin and it stays centered on the joint - covering
	whatever gap the shin's rotation would otherwise open, from dead
	flush at rest up through a full bent-knee pose.

	`bare_hem_z` is _build_leg_shin's own identical param, threaded
	straight through - see that function's own docstring. `socks` used
	to be threaded through here too - fully moved out to
	avatar_plugins_builtin/bodypart/socks/ (see that plugin's own
	docstring)."""
	idx = a3d._build_leg_shin(
		vertex, normal, color, tris, idx, side, hip_half_w, limb_half_w, pants_style, missing_leg, LEG_H, knee_z, ankle_z, pants, skin,
		accent_c=accent_c, stitch_c=stitch_c, pattern=pattern, pattern_c=pattern_c,
		bare_hem_z=bare_hem_z,
	)
	side_name = "left" if side == -1 else "right"
	if missing_leg in (side_name, "both"):
		return idx
	leg_w = {"skinny": limb_half_w * 0.72, "baggy": limb_half_w * 1.45}.get(pants_style, limb_half_w)
	lx0 = side * hip_half_w * 0.5 - leg_w
	# Same knee-bare test _build_leg_thigh's own `bare` uses (see its
	# docstring) - "regular"/"bermuda" shorts put the knee itself in the
	# bare-skin region (a real, confirmed-live check: KNEE_Z sits just
	# below both of those thigh_z cutoffs), so the filler needs to be
	# skin there too, not a stray pants-colored patch sitting on bare
	# skin. "athletic" (thigh_z below KNEE_Z) still covers the knee in
	# fabric, same as plain full-length pants.
	knee_bare = bare_hem_z is not None and bare_hem_z > knee_z
	knee_cap_c = skin if knee_bare else pants
	idx = a3d._box(
		vertex, normal, color, tris, idx,
		(lx0, -0.2, knee_z - KNEE_CAP_HALF_H), (lx0 + leg_w * 2, 0.2, knee_z + KNEE_CAP_HALF_H), knee_cap_c,
	)
	return idx


@dataclass
class Rig:
	"""A posable character. Any joint is None when that part doesn't
	exist for this character (a missing limb, or wheels-only when there
	are no standing legs) - callers must check before posing (e.g.
	`if rig.hip_l is not None: rig.hip_l.setP(...)`)."""
	root: NodePath
	# The waist/hip line - torso, arms, and head all hang off this, so
	# bending it forward/back (a bow, a lean) moves the upper body while
	# legs (attached directly to root, not to waist) stay planted. Always
	# present - there's no "missing torso" case the way a limb can be
	# missing.
	waist: Optional[NodePath] = None
	hip_l: Optional[NodePath] = None
	hip_r: Optional[NodePath] = None
	knee_l: Optional[NodePath] = None
	knee_r: Optional[NodePath] = None
	ankle_l: Optional[NodePath] = None
	ankle_r: Optional[NodePath] = None
	shoulder_l: Optional[NodePath] = None
	shoulder_r: Optional[NodePath] = None
	elbow_l: Optional[NodePath] = None
	elbow_r: Optional[NodePath] = None
	wrist_l: Optional[NodePath] = None
	wrist_r: Optional[NodePath] = None
	# Each hand's 4 fingers, as a plain list of 4 pivots (index order
	# matches the palm's own inner-to-outer layout in _build_hand) -
	# a list rather than 4 more named fields since "which finger" never
	# needs to be addressed by name, just "all of them" or "one of
	# them" for a curl/fist pose. Empty (not a list of Nones) when the
	# hand itself doesn't exist, matching missing_arm's own "nothing to
	# pose" contract for every other joint here.
	fingers_l: list = field(default_factory=list)
	fingers_r: list = field(default_factory=list)
	thumb_l: Optional[NodePath] = None
	thumb_r: Optional[NodePath] = None
	neck: Optional[NodePath] = None
	wheel_l: Optional[NodePath] = None
	wheel_r: Optional[NodePath] = None
	# Blink is implemented as a single "eyelids closed" overlay shown in
	# front of the head bundle's own (always-open) eyes, rather than two
	# separate open/closed head variants - much cheaper than rebuilding
	# the whole head bundle twice per blink state, and visually
	# identical (the overlay fully occludes the open eyes when shown,
	# exactly like a real eyelid). None when both eyes are missing (no
	# eyes left to close).
	eyes_closed: Optional[NodePath] = None
	# Same idea for the mouth: the head bundle's own mouth is always the
	# closed/neutral thin line; "mouth_open" is a separate overlay (the
	# same open-mouth-plus-teeth-sliver shape build_character's own
	# mouth_style="open" branch uses) shown in front of it during talk
	# frames.
	mouth_open: Optional[NodePath] = None


def _apply_bodypart_plugins_rig(rig, traits, metrics):
	"""The rig-side counterpart to avatar3d._apply_bodypart_plugins_static()
	- see that function's own docstring for the shared reasoning
	(deferred import so avatar3d_rig.py doesn't pay for
	avatar_plugin_backend.py at load time in every process that never
	touches this extension point; per-plugin try/except so one broken
	third-party plugin can't take down every avatar render).
	Every installed "bodypart" plugin's own build_animated(rig, traits,
	metrics) -> None runs here, given the real, already-built rig to
	attach new geometry to (see avatar_plugin_backend.py's own module
	docstring for exactly how a plugin is expected to do that - reparent
	a small buffer built via a3d._box()/_finish() to whichever real
	Rig joint fits, e.g. rig.waist/rig.neck/rig.shoulder_l)."""
	import avatar_plugin_backend

	for plugin_id, build_fn in avatar_plugin_backend.installed_bodypart_rig_builders():
		try:
			build_fn(rig, traits, metrics)
		except Exception:
			log.exception(f"bodypart plugin {plugin_id!r} failed during rig build - skipped")


def build_rig(
	skin_color="#f1c27d", hair_color="#3b2417", shirt_color="#ff0090", pants_color="#22252e",
	eye_color="#1e5fa8", shoe_color="#1a1a1a", eyebrow_color=None,
	pants_style="straight", leg_type="human",
	animal_color=None,
	ears="none", ear_scale=1.0, ear_thickness=1.0, ear_depth=1.0,
	horns="none", horn_color=None,
	horn_scale=1.0, horn_thickness=1.0,
	halo=False, missing_eye="none",
	missing_arm="none", missing_leg="none", prosthetics=(), prosthetic_color=a3d.PROSTHETIC_COLOR_DEFAULT,
	wheelchair=False, wheelchair_color="#2a2a3a", wheelchair_tire_color="#0f0f11",
	gender="nonbinary", weight=0.5, muscle=0.5, shape=0.5, age=0.5,
	pants_accent_color=None, shirt_accent_color=None, stitch_color=None,
	shirt_pattern="solid", shirt_pattern_color=None, pants_pattern="solid", pants_pattern_color=None,
	eye_color_right=None, iris_ring=False, skin_patches=False, skin_patch_color=None,
	hairstyle="short", eye_shape="round", nose_shape="straight", mouth_style="neutral",
	facial_hair="none", facial_hair_color=None, freckles=False, blush=False,
	bottom_style="pants", top_style="long_sleeve", cropped=False, hood_up=False, drawstring_color="#f4f7ff",
	shorts_style="regular",
	accessories=(),
	**extra_traits,
) -> Rig:
	"""Builds a rig covering the full head AND clothing/accessory richness
	build_character's own static path offers (hairstyle/eye_shape/
	nose_shape/mouth_style/facial_hair/freckles/blush/muzzle -
	see _build_head_bundle's own docstring; top_style's
	collar/hood/lapel/turtleneck neckline, cropped midriff, bottom_style
	"shorts"/"overalls", every accessory (glasses/hat/backpack/satchel/
	purse/necklace/earrings/tie/wristband/socks/gloves/headphones), and
	paws/claws - a real, direct request, "add everything else we did in
	scratchpad with clothing, hair, accessories, etc.", to bring the
	rig up to the static path's own full richness, the same way an
	earlier pass already did for the head): pants-leg, shorts-leg, or
	digitigrade legs, wheelchair (legs replaced by a static frame +
	spinning wheels), every top_style's own sleeve/bare-arm split
	(+ missing_arm/prosthetic), ears/horns/halo/missing_eye, tail, wings.

	extra_eyes/whiskers/antennae/claws/claw_color/paw_color/fur_pattern/
	pattern_color are NOT named parameters here anymore - each is its
	own built-in "bodypart" plugin now (resolved from **extra_traits,
	same as tail/wings/antennae already were - fur_pattern's own
	build_character-only arm-sleeve overlay was never part of this
	function's own scope to begin with, a real pre-existing scope cut,
	so only the face portion applies here, same as before this
	extraction). muzzle/snout_color is a partial exception, same shape
	as build_character's own identical treatment: _build_head_bundle/
	_build_face still need a real `muzzle` boolean as a GATE (to skip
	the normal nose+mouth), resolved from extra_traits right below and
	threaded through - only the snout-replacement geometry itself moved
	to the "muzzle" bodypart plugin. `paws`/`claws` are a different
	shape again: neither is a gate inside _build_hand()/_build_foot()
	anymore (both draw their own normal geometry unconditionally now,
	same "poke out to override" technique the paw/claw plugins' own
	geometry uses on top of it - see _build_hand's own docstring) -
	`paws` is still resolved locally purely because THIS function's own
	finger/thumb-pivot construction below needs a real boolean to decide
	whether to build them at all (real animal paws have no separate
	articulated digits), not because any sub-builder needs it as a gate.

	`bottom_style="skirt"/"dress"` is the one deliberate scope cut left:
	this rig only ever has jointed, leg-shaped geometry (there's no
	skirt/dress mesh here, and a skirt/dress wouldn't have separate legs
	to pose anyway) - build_character stays the only path for those two.
	`paws=True` also skips building this rig's own finger/thumb pivots
	entirely (see _build_hand's own docstring: real animal paws have no
	separate articulated digits, so there's nothing under the paw shape
	for them to pose) - `rig.fingers_l/r` and `rig.thumb_l/r` come back
	empty/None in that case, same contract as a missing hand.

	`ear_scale`/`ear_thickness`/`ear_depth` (a3d._build_hair_and_toppers's
	own params) independently resize ears on top of each species' own
	fixed proportions - see that function's own docstring for exactly
	which axis each multiplier controls. All three default to 1.0
	(unscaled, today's fixed per-species look). tail's own equivalent
	tail_scale/tail_thickness/tail_depth now live inside
	avatar_plugins_builtin/bodypart/tail/bodypart.py instead - tail is no
	longer one of this function's own named parameters.

	`age` (0 young, 1 elderly, 0.5 a neutral adult default) drives 4
	separate effects per direct instruction: hair/eyebrow graying here
	(below), facial wrinkles/age spots in _build_head_bundle (see its
	own ELDERLY_AGE threshold), a hand age spot in _build_hand, and a
	baseline forward waist stoop applied once, right after the waist
	pivot exists below - a real, known limitation of that last one: any
	motion function that itself sets waist.setP() (run's forward lean,
	a bow, ...) overrides this baseline for as long as that motion
	plays, since every pose in avatar_anim.py sets ABSOLUTE angles, not
	deltas from a rest pose. Fixing that would mean reworking how every
	existing motion poses the waist, not just adding age - a real,
	deliberate scope cut for this pass rather than a currently-caught
	bug.

	**extra_traits catches any keyword this function doesn't itself
	recognize - same reasoning as avatar3d.build_character()'s own
	identical catch-all (see its docstring): a saved profile's traits
	dict is always splatted straight in, and an installed "bodypart"
	plugin's own namespaced fields need somewhere to land that doesn't
	crash every render. Passed through unused to
	_apply_bodypart_plugins_rig() below."""
	# Same compatible-hairstyle restriction as build_character's own
	# identical guard, for the identical reason (voluminous styles -
	# afro/curly/bun/spiky/ponytail/pigtails/long/medium - visually
	# fight ears/horns/halo poking out of/through them) - both
	# functions share the exact same hair-building code now
	# (_build_hair_and_toppers), so the same conflict applies here too.
	if (ears != "none" or horns != "none" or halo) and hairstyle not in ("short", "buzz", "bald", "undercut"):
		hairstyle = "short"
	skin = a3d._hex_to_rgba(skin_color)
	hair = a3d._hex_to_rgba(hair_color)
	# Graying - starts at age=0.4 (a hint of gray, "salt and pepper"),
	# fully gray/white by age=1.0. A separate, earlier-starting effect
	# from the wrinkles/spots below (ELDERLY_AGE=0.65) since real hair
	# often starts graying well before a face reads as visibly elderly.
	GRAY_START_AGE = 0.4
	GRAY_COLOR = (0.82, 0.82, 0.84, 1)
	gray_amount = max(0.0, min(1.0, (age - GRAY_START_AGE) / (1.0 - GRAY_START_AGE)))
	if gray_amount > 0:
		hair = tuple(h * (1 - gray_amount) + g * gray_amount for h, g in zip(hair, GRAY_COLOR))
	eyebrows = a3d._hex_to_rgba(eyebrow_color) if eyebrow_color else hair
	shirt = a3d._hex_to_rgba(shirt_color)
	pants = a3d._hex_to_rgba(pants_color)
	# Seam/hem/stitch colors - each defaults to a darker shade of its
	# own garment (stitch to a plain off-white thread) when not given
	# explicitly, matching what every seam/hem looked like before this
	# was made customizable; passing a real color overrides that shade.
	pants_accent_c = a3d._hex_to_rgba(pants_accent_color) if pants_accent_color else a3d._shade(pants, 0.7)
	shirt_accent_c = a3d._hex_to_rgba(shirt_accent_color) if shirt_accent_color else a3d._shade(shirt, 0.7)
	stitch_c = a3d._hex_to_rgba(stitch_color) if stitch_color else a3d.STITCH_THREAD_COLOR
	# Fabric patterns (stripes/plaid/polka_dot/color_block - see
	# avatar3d._apply_fabric_pattern's own docstring for why this
	# is extra poked-out geometry, not a texture) - pattern_color
	# defaults to the garment's own accent color (already computed
	# above) rather than requiring a THIRD color pick per garment.
	shirt_pattern_c = a3d._hex_to_rgba(shirt_pattern_color) if shirt_pattern_color else shirt_accent_c
	pants_pattern_c = a3d._hex_to_rgba(pants_pattern_color) if pants_pattern_color else pants_accent_c
	# Skin patch color (vitiligo lighter by default, or pass a darker
	# shade for a birthmark look instead) - see _build_head_bundle's
	# own docstring on why the positions themselves are fixed, not
	# random.
	skin_patch_c = a3d._hex_to_rgba(skin_patch_color) if skin_patch_color else None
	# facial_hair_c defaults to hair (same "matches hair unless given its
	# own color" convention animal_color/eyebrow_color already use) -
	# matches build_character's own identical defaulting exactly.
	facial_hair_c = a3d._hex_to_rgba(facial_hair_color) if facial_hair_color else hair
	# muzzle: not a named parameter anymore (see this function's own
	# docstring) - resolved from extra_traits here, right before
	# _build_head_bundle() needs it as a gate.
	muzzle = bool(extra_traits.get("muzzle", False))
	eyes = a3d._hex_to_rgba(eye_color)
	eyes_r = a3d._hex_to_rgba(eye_color_right) if eye_color_right else eyes
	shoes = a3d._hex_to_rgba(shoe_color)
	animal = a3d._hex_to_rgba(animal_color) if animal_color else hair
	horn_c = a3d._hex_to_rgba(horn_color) if horn_color else a3d._hex_to_rgba(a3d.HORN_COLOR_DEFAULT)
	prosthetic_c = a3d._hex_to_rgba(prosthetic_color)
	wheelchair_c = a3d._hex_to_rgba(wheelchair_color)
	tire_c = a3d._hex_to_rgba(wheelchair_tire_color)
	# Clothing/accessory colors - identical defaulting to build_character's
	# own copy of these exact same lines. backpack/satchel/purse/
	# necklace/tie/glasses/hat/headphones/earrings/wristband/socks's own
	# equivalent defaulting now happens inside their own files under
	# avatar_plugins_builtin/bodypart/ instead - none of them are this
	# function's own named parameters anymore. `gloves_color` is a
	# partial exception, same shape as muzzle/snout_color: _build_hand()
	# still needs a real `gloves_c` recolor value threaded through (see
	# its own docstring - gloves recolors the palm in place rather than
	# adding bolt-on geometry, so it can't move out as cleanly as
	# wristband/socks did), so it's resolved from **extra_traits here
	# rather than staying a named parameter.
	gloves_color = extra_traits.get("gloves_color")
	gloves_c = a3d._hex_to_rgba(gloves_color) if gloves_color else a3d._hex_to_rgba("#1a1a1a")
	drawstring_c = a3d._hex_to_rgba(drawstring_color)
	# paws: not a named parameter anymore (see this function's own
	# docstring) - resolved here purely so the finger/thumb-pivot-skip
	# check below has a real boolean to test (_build_hand() itself no
	# longer needs it - see its own docstring). paw_color/claw_color/
	# claws have no such need anywhere in this function, so neither
	# needs resolving here at all - both plugins compute their own
	# colors straight from traits.
	paws = bool(extra_traits.get("paws", False))

	# Age also reproportions the whole body below ADULT_AGE, not just
	# hair/wrinkles above it - see avatar3d._age_body_scale's own
	# docstring for the reasoning. Shadows the module-level LEG_H/
	# TORSO_H/HEAD_S (still their own real adult defaults, used
	# unscaled by _build_shin_with_knee_cap's own default parameter and
	# nowhere else outside this function) with this call's own real,
	# possibly age-scaled values - everything below derives from these
	# three plus chest/hip/limb_half_w, so scaling just these five
	# numbers reproportions the entire rig consistently.
	head_scale, body_scale = a3d._age_body_scale(age)
	LEG_H = LEG_H_ADULT * body_scale
	TORSO_H = TORSO_H_ADULT * body_scale
	HEAD_S = HEAD_S_ADULT * head_scale

	gmod = a3d.GENDER_MODS.get(gender, a3d.GENDER_MODS["nonbinary"])
	weight_scale = 0.82 + weight * 0.55
	chest_half_w = (0.32 + muscle * 0.10 + gmod["chest"]) * weight_scale * body_scale
	hip_half_w = (0.28 + shape * 0.14 + gmod["hip"]) * weight_scale * body_scale
	limb_half_w = (0.15 + muscle * 0.05 + weight * 0.04) * (1 if weight_scale > 1 else weight_scale) * body_scale

	# neck_z0 is the actual neck PIVOT's own position (the base of the
	# neck, at the top of the torso) - unchanged from before, so every
	# existing nod/shake_head/etc. animation's own rotation center stays
	# exactly where it was. head_z0 (where the visible head box itself
	# starts) is raised above that by NECK_H per direct request ("give
	# them a neck") - _build_head_bundle draws the actual neck box
	# filling that gap, still rotating as one rigid piece with the head
	# since both are in the same buffer under the same pivot.
	neck_z0 = LEG_H + TORSO_H
	head_z0 = neck_z0 + a3d.NECK_H
	SHOULDER_Z = LEG_H + TORSO_H
	# KNEE_Z/ANKLE_Z used to be fixed absolute heights (0.68/0.25) tuned
	# only against the adult LEG_H=1.15 - a real bug once LEG_H itself
	# became age-scalable: a shorter child leg would still put the knee/
	# ankle at those same fixed adult heights, landing them in the wrong
	# fraction of the now-shorter leg (or even above it entirely at the
	# toddler extreme). Expressed as the exact same fractions of LEG_H
	# instead (0.68/1.15, 0.25/1.15 - identical position for today's
	# unscaled adult LEG_H, correct for any scaled one).
	KNEE_Z = LEG_H * (0.68 / 1.15)
	ANKLE_Z = LEG_H * (0.25 / 1.15)
	ELBOW_Z = LEG_H + TORSO_H * 0.55
	WRIST_Z = LEG_H
	root = NodePath("avatar_rig_root")
	rig = Rig(root=root)

	# --- Static torso: shirt/top_style/cropped/bottom_style-bib/
	# accessories + wheelchair frame (if any) + tail + wings ---
	# Hip/waist/chest three-band torso, collar/hood/lapel/turtleneck
	# neckline, and "overalls" bib+straps - shared verbatim with
	# build_character's own identical static torso via _build_torso()
	# (see its own docstring for why two stacked boxes alone can't show
	# a waist shape, and for the full list of what it draws). backpack/
	# satchel/purse/necklace/tie are all drawn separately, by the
	# bodypart plugin hook below (_apply_bodypart_plugins_rig()) - see
	# each one's own build_animated() under avatar_plugins_builtin/
	# bodypart/.
	vdata, vertex, normal, color, tris = _new_buffers()
	idx = 0
	idx = a3d._build_torso(
		vertex, normal, color, tris, idx, LEG_H, TORSO_H, HEAD_S, hip_half_w, chest_half_w, skin, shirt,
		shirt_accent_c, stitch_c, shirt_pattern, shirt_pattern_c, cropped, top_style, hood_up, drawstring_c,
		bottom_style, pants, accessories,
	)
	# tail/wings are NOT built here anymore - each is its own built-in
	# "bodypart" feature now (avatar_plugins_builtin/bodypart/tail|wings/),
	# drawn separately by the bodypart plugin hook below
	# (_apply_bodypart_plugins_rig()).
	torso_np = _finish(vdata, tris, "torso")

	# Waist pivot - torso, arms, and head are all children of this
	# (matching real anatomy: they move together when you bend at the
	# hips), while legs stay direct children of root below, unaffected
	# by any waist rotation, so a bow/lean bends the upper body without
	# the legs tipping over with it. Same world-point-offset trick as
	# _pivoted_part: waist sits at world height LEG_H, and torso_np's
	# own baked-in absolute-z geometry is recentered under it by the
	# negative of that offset.
	waist = root.attachNewNode("waist")
	waist.setPos(0.0, 0.0, LEG_H)
	torso_np.reparentTo(waist)
	torso_np.setPos(0.0, 0.0, -LEG_H)
	rig.waist = waist

	# --- Legs (pants/digitigrade) or wheels (wheelchair) ---
	if wheelchair:
		# The chair FRAME (seat/backrest/footrests) - a real, confirmed-
		# live bug: this used to be baked into the same merged buffer as
		# the torso above, reparented to `waist` right along with it, so
		# any waist rotation (a bow, and now age's own posture stoop)
		# tilted the physical chair along with the person's own leaning
		# torso - "the wheelchair looks like a nollie", the seat popping
		# up away from the wheels' own fixed axle since the wheels
		# (built separately below, parented straight to `root`) never
		# moved to match. A wheelchair's frame doesn't tilt just because
		# the person sitting in it leans forward - only the person does
		# - so this is now its own piece, parented straight to `root`
		# exactly like the wheels are, never touched by waist's own
		# rotation at all.
		fvdata, fvertex, fnormal, fcolor, ftris = _new_buffers()
		fidx = a3d._build_wheelchair_frame(
			fvertex, fnormal, fcolor, ftris, 0, hip_half_w, limb_half_w, missing_leg, "pants",
			shirt, pants, shoes, wheelchair_c, LEG_H, HEAD_S, TORSO_H,
		)
		frame_np = _finish(fvdata, ftris, "wheelchair_frame")
		frame_np.reparentTo(root)
		for side, attr in ((-1, "wheel_l"), (1, "wheel_r")):
			# wx/wheel_cz come from the SAME _wheel_geometry() call
			# _build_wheel itself uses - see that function's own
			# docstring for why this must never be a second, independent
			# copy of these numbers.
			wx, _wheel_cy, wheel_cz, _wheel_thick, _wheel_r = a3d._wheel_geometry(hip_half_w, LEG_H, side)
			pivot, _ = _pivoted_part(
				root, attr, (wx, 0.0, wheel_cz),
				lambda v, n, c, t, i, side=side: a3d._build_wheel(v, n, c, t, i, side, hip_half_w, LEG_H, tire_c, wheelchair_c),
			)
			setattr(rig, attr, pivot)
	else:
		# bottom_style="shorts": thigh_z formula verbatim from
		# build_character's own identical "shorts" branch (see that
		# branch's own comment) - below this height is bare skin, above
		# it pants fabric, split across the thigh/shin segments exactly
		# as _build_leg_thigh/_build_leg_shin's own bare_hem_z docstrings
		# describe. None (the "pants"/"overalls" default) draws a plain
		# full-length pants leg, unchanged from before this param
		# existed.
		bare_hem_z = {"bermuda": LEG_H * 0.85, "athletic": LEG_H * 0.45}.get(shorts_style, LEG_H * 0.62) if bottom_style == "shorts" else None
		# A real 3-joint chain per leg (hip -> knee -> ankle), each
		# pivot's own build_fn drawing just that one segment
		# (_build_leg_thigh/_build_leg_shin/_build_foot). Every joint in
		# one leg shares the same X offset (hip/knee/ankle sit in a
		# vertical line at rest) - harmless even once posed, since every
		# rotation here is pure pitch about local X, which never touches
		# X itself. Each joint is only attempted if its parent joint
		# actually exists, so missing_leg correctly yields hip/knee/ankle
		# all None for that side rather than a dangling partial chain.
		for side, side_l in ((-1, "l"), (1, "r")):
			lx = side * hip_half_w * 0.5
			hip_pivot, _ = _pivoted_part(
				root, f"hip_{side_l}", (lx, 0.0, LEG_H),
				lambda v, n, c, t, i, side=side: a3d._build_leg_thigh(
					v, n, c, t, i, side, hip_half_w, limb_half_w, pants_style, missing_leg, LEG_H, KNEE_Z, pants,
					accent_c=pants_accent_c, pattern=pants_pattern, pattern_c=pants_pattern_c,
					skin=skin, bare_hem_z=bare_hem_z,
				),
				origin=root,
			)
			setattr(rig, f"hip_{side_l}", hip_pivot)
			if hip_pivot is None:
				continue
			knee_pivot, _ = _pivoted_part(
				hip_pivot, f"knee_{side_l}", (lx, 0.0, KNEE_Z),
				lambda v, n, c, t, i, side=side: _build_shin_with_knee_cap(
					v, n, c, t, i, side, hip_half_w, limb_half_w, pants_style, missing_leg, LEG_H, KNEE_Z, ANKLE_Z, pants, skin,
					accent_c=pants_accent_c, stitch_c=stitch_c, pattern=pants_pattern, pattern_c=pants_pattern_c,
					bare_hem_z=bare_hem_z,
				),
				origin=root,
			)
			setattr(rig, f"knee_{side_l}", knee_pivot)
			if knee_pivot is None:
				continue
			ankle_pivot, _ = _pivoted_part(
				knee_pivot, f"ankle_{side_l}", (lx, 0.0, ANKLE_Z),
				lambda v, n, c, t, i, side=side: a3d._build_foot(
					v, n, c, t, i, side, hip_half_w, limb_half_w, pants_style, leg_type, missing_leg, ANKLE_Z, shoes, skin,
				),
				origin=root,
			)
			setattr(rig, f"ankle_{side_l}", ankle_pivot)

	# --- Arms (long_sleeve/prosthetic) - children of `waist`, not
	# `root`, so they move with the torso when it bends. world_point is
	# still the plain absolute coordinate (same as build_character's own
	# math); `origin=root` tells _pivoted_part to place the pivot at
	# that height in ROOT's space regardless of waist's own offset.
	# A real 3-joint chain per arm (shoulder -> elbow -> wrist), same
	# "only attempt the next joint if the parent exists" guarding as the
	# legs above, so missing_arm correctly yields shoulder/elbow/wrist
	# all None for that side.
	for side, side_l in ((-1, "l"), (1, "r")):
		shoulder_pivot, _ = _pivoted_part(
			waist, f"shoulder_{side_l}", (side * chest_half_w, ARM_Y_OFFSET, SHOULDER_Z),
			lambda v, n, c, t, i, side=side: a3d._build_arm_upper(
				v, n, c, t, i, side, chest_half_w, limb_half_w, missing_arm, prosthetics, shirt, prosthetic_c, ELBOW_Z, SHOULDER_Z,
				y_offset=ARM_Y_OFFSET, pattern=shirt_pattern, pattern_c=shirt_pattern_c, skin=skin, top_style=top_style,
			),
			origin=root,
		)
		setattr(rig, f"shoulder_{side_l}", shoulder_pivot)
		if shoulder_pivot is None:
			continue
		elbow_pivot, _ = _pivoted_part(
			shoulder_pivot, f"elbow_{side_l}", (side * chest_half_w, ARM_Y_OFFSET, ELBOW_Z),
			lambda v, n, c, t, i, side=side: a3d._build_arm_forearm(
				v, n, c, t, i, side, chest_half_w, limb_half_w, missing_arm, prosthetics, shirt, prosthetic_c, WRIST_Z, ELBOW_Z,
				y_offset=ARM_Y_OFFSET, accent_c=shirt_accent_c, stitch_c=stitch_c, pattern=shirt_pattern, pattern_c=shirt_pattern_c,
				skin=skin, top_style=top_style,
			),
			origin=root,
		)
		setattr(rig, f"elbow_{side_l}", elbow_pivot)
		if elbow_pivot is None:
			continue
		wrist_pivot, _ = _pivoted_part(
			elbow_pivot, f"wrist_{side_l}", (side * chest_half_w, ARM_Y_OFFSET, WRIST_Z),
			lambda v, n, c, t, i, side=side: a3d._build_hand(
				v, n, c, t, i, side, chest_half_w, limb_half_w, missing_arm, prosthetics, skin, prosthetic_c, WRIST_Z,
				y_offset=ARM_Y_OFFSET, age=age, gloves="gloves" in accessories, gloves_c=gloves_c,
			),
			origin=root,
		)
		setattr(rig, f"wrist_{side_l}", wrist_pivot)
		if wrist_pivot is None or paws:
			# `paws` (see _build_hand's own docstring) replaces the palm
			# with a real animal paw shape that has no separate
			# articulated digits - nothing under it for finger/thumb
			# pivots to pose, so this rig doesn't build any, same
			# "nothing to pose" contract as a missing hand.
			setattr(rig, f"fingers_{side_l}", [])
			setattr(rig, f"thumb_{side_l}", None)
			continue

		# --- Fingers + thumb - each its own pivot, parented under the
		# SAME wrist_pivot the (now finger/thumb-less) palm box lives
		# under, so curling a digit doesn't need to know how the hand
		# itself is currently oriented (it inherits the wrist's own
		# pose for free, same as every other joint in this chain).
		# hand_lo_x/hand_hi_x/palm_z0 duplicate _build_hand's own math
		# exactly (see that function's own docstring for why this is
		# recomputed here rather than returned from it).
		side_name = "left" if side == -1 else "right"
		color_c = prosthetic_c if f"{side_name}_arm" in prosthetics else skin
		outer = side * (chest_half_w + limb_half_w * 2)
		inner = side * chest_half_w
		lo_x, hi_x = min(inner, outer), max(inner, outer)
		hand_lo_x, hand_hi_x = lo_x + 0.01, hi_x - 0.01
		palm_z0 = WRIST_Z - 0.04
		finger_w = (hand_hi_x - hand_lo_x - FINGER_GAP * 3) / 4
		finger_tip_z = palm_z0 - FINGER_LEN

		finger_pivots = []
		for i in range(4):
			fx0 = hand_lo_x + i * (finger_w + FINGER_GAP)
			fx_center = fx0 + finger_w / 2
			finger_pivot, _ = _pivoted_part(
				wrist_pivot, f"finger{i}_{side_l}", (fx_center, -0.095 + ARM_Y_OFFSET, palm_z0),
				lambda v, n, c, t, idx0, fx0=fx0, finger_w=finger_w: a3d._box(
					v, n, c, t, idx0, (fx0, -0.16 + ARM_Y_OFFSET, finger_tip_z), (fx0 + finger_w, -0.03 + ARM_Y_OFFSET, palm_z0), color_c,
				),
				origin=root,
			)
			finger_pivots.append(finger_pivot)
		setattr(rig, f"fingers_{side_l}", finger_pivots)

		# Thumb - real human hands (checked against dimensions.com's own
		# hand anatomy reference rather than guessed) don't attach the
		# thumb from the same knuckle line as the 4 fingers at all: its
		# metacarpal is its own mobile bone forming the thenar bulge at
		# the base/side of the palm, so the visible digit starts partway
		# down the palm's own height, not from a thin shared top edge -
		# which is exactly why touching the palm at only one z-plane
		# (the old version) read as "not connected to the hand", a real,
		# confirmed-live gap: same-height boxes meeting edge-to-edge with
		# a 0.01 x-gap between them never actually shared any volume.
		# Fixed on BOTH counts here: this box now overlaps a real chunk
		# of the palm's own x-range AND spans down from partway up the
		# palm's height (not just its bottom edge), so there's an actual
		# shared, connected volume at rest, not a hairline touch.
		# Still on the BACK half of the hand (+y), behind where the 4
		# fingers sit (-y) - a separate, real, confirmed-live correction
		# (level with the fingers on the front plane it read as a stubby
		# sideways nub, i.e. a "thumbs up" gesture, not a thumb tucked
		# behind the fingers the way a relaxed hand actually sits) - and
		# still using the same single-axis pitch curl as the 4 fingers
		# (and every other joint in this rig) rather than a true
		# opposable thumb's own separate rotation axis, a deliberate
		# scope cut for the same "one rotational DOF per joint" reason
		# the hip/shoulder only ever get a single pitch axis, not real
		# anatomical accuracy.
		thumb_w = finger_w * 1.1
		thumb_overlap = 0.03
		thumb_x0, thumb_x1 = (
			(inner - thumb_w, inner + thumb_overlap) if side == 1 else (inner - thumb_overlap, inner + thumb_w)
		)
		thumb_x_center = (thumb_x0 + thumb_x1) / 2
		thumb_top_z = palm_z0 + (WRIST_Z - palm_z0) * 0.6
		thumb_tip_z = palm_z0 - THUMB_LEN
		thumb_pivot, _ = _pivoted_part(
			wrist_pivot, f"thumb_{side_l}", (thumb_x_center, 0.085 + ARM_Y_OFFSET, thumb_top_z),
			lambda v, n, c, t, idx0, thumb_x0=thumb_x0, thumb_x1=thumb_x1: a3d._box(
				v, n, c, t, idx0, (thumb_x0, 0.02 + ARM_Y_OFFSET, thumb_tip_z), (thumb_x1, 0.15 + ARM_Y_OFFSET, thumb_top_z), color_c,
			),
			origin=root,
		)
		setattr(rig, f"thumb_{side_l}", thumb_pivot)

	# --- Head/neck (short hair, round eyes) - also a child of `waist` for
	# the same reason (the head rides on the torso, not the legs). ears/
	# horns/halo/antennae/extra_eyes/whiskers are NOT built here anymore -
	# each is its own built-in "bodypart" feature now (see
	# _apply_bodypart_plugins_rig() below).
	neck, _ = _pivoted_part(
		waist, "neck", (0.0, 0.0, neck_z0),
		lambda v, n, c, t, i: a3d._build_head_bundle(
			v, n, c, t, i, skin, hair, eyes, eyebrows,
			missing_eye, head_z0, HEAD_S, age=age,
			eyes_r=eyes_r, iris_ring=iris_ring, skin_patches=skin_patches, skin_patch_c=skin_patch_c,
			hairstyle=hairstyle, eye_shape=eye_shape, nose_shape=nose_shape, mouth_style=mouth_style,
			facial_hair=facial_hair, facial_hair_c=facial_hair_c, freckles=freckles, blush=blush,
			muzzle=muzzle,
		),
		origin=root,
	)
	rig.neck = neck

	# --- Blink overlay: one skin-colored "closed eyelid" box per
	# existing (non-missing) eye, hidden by default, fully covering the
	# eye's own z-range at a y just in front of the eyelid's own
	# frontmost surface (face_y-0.07) so it cleanly occludes the whole
	# eye when shown.
	if neck is not None and missing_eye != "both":
		eye_z = head_z0 + HEAD_S * 0.56
		face_y = -HEAD_S / 2
		vdata2, vertex2, normal2, color2, tris2 = _new_buffers()
		idx2 = 0
		for side in (-1, 1):
			side_name = "left" if side == -1 else "right"
			if missing_eye == side_name:
				continue
			ex = side * HEAD_S * 0.22
			idx2 = a3d._box(
				vertex2, normal2, color2, tris2, idx2,
				(ex - 0.105, face_y - 0.075, eye_z - 0.095), (ex + 0.105, face_y - 0.065, eye_z + 0.095), skin,
			)
		if idx2 > 0:
			eyes_closed = _finish(vdata2, tris2, "eyes_closed")
			eyes_closed.reparentTo(neck)
			# setPos(root, 0, 0, 0): its own baked geometry already
			# encodes the correct absolute position, so the NODE itself
			# needs zero additional offset in root's space - regardless
			# of how deep the root->waist->neck chain is.
			eyes_closed.setPos(root, 0.0, 0.0, 0.0)
			eyes_closed.hide()
			rig.eyes_closed = eyes_closed

	# --- Mouth-open overlay: same shape as build_character's own
	# mouth_style="open" branch (a taller box plus a teeth sliver),
	# pushed MOUTH_OVERLAY_EPS further forward than the head bundle's
	# own closed/neutral mouth line so it cleanly occludes it when shown
	# instead of z-fighting with it (same "poke out past what's behind
	# it" rule as everywhere else in this file's box geometry).
	if neck is not None:
		eye_z = head_z0 + HEAD_S * 0.56
		face_y = -HEAD_S / 2
		nose_z = eye_z - 0.2
		mouth_z = nose_z - 0.16
		MOUTH_COLOR = (0.55, 0.25, 0.28, 1)
		MOUTH_OVERLAY_EPS = 0.006
		open_front_y = face_y - 0.02 - MOUTH_OVERLAY_EPS
		vdata3, vertex3, normal3, color3, tris3 = _new_buffers()
		idx3 = 0
		idx3 = a3d._box(
			vertex3, normal3, color3, tris3, idx3,
			(-0.07, open_front_y, mouth_z - 0.045), (0.07, face_y + 0.03, mouth_z + 0.02), MOUTH_COLOR,
		)
		idx3 = a3d._box(
			vertex3, normal3, color3, tris3, idx3,
			(-0.06, open_front_y, mouth_z + 0.02), (0.06, face_y + 0.03, mouth_z + 0.03), (0.95, 0.94, 0.9, 1),
		)
		mouth_open = _finish(vdata3, tris3, "mouth_open")
		mouth_open.reparentTo(neck)
		mouth_open.setPos(root, 0.0, 0.0, 0.0)
		mouth_open.hide()
		rig.mouth_open = mouth_open

	# Posture stoop - a real, confirmed-live-requested effect: at high
	# `age`, the whole upper body (torso/arms/head all hang off `waist`,
	# so tipping it forward moves all of them together) tips forward a
	# bit, matching how a real elderly posture often stoops. Applied
	# here, AFTER every child (legs, arms, head) already exists - a
	# real, confirmed-live bug in the first version applied this right
	# after `waist` was created, BEFORE the arm/head pivots below it
	# were built. Those pivots anchor themselves to a fixed ROOT-
	# relative position (origin=root, so bending at the waist during a
	# walk/run/bow doesn't need the pose code to know how many ancestors
	# sit above it) - Panda's setPos(root, ...) computes that anchor by
	# solving for whatever LOCAL offset cancels out the parent chain's
	# CURRENT transform, so building those pivots while `waist` already
	# had a stoop rotation baked in a counter-rotation into each one,
	# exactly canceling the stoop for anything under `waist` (arms,
	# head) while leaving legs (parented straight to `root`, unaffected
	# either way) stooped alone - "the hand doesn't move with the
	# slouch", confirmed live. Setting it only now, once every pivot's
	# own fixed local offset is already locked in against a rotation-
	# free waist, means this rotates the whole assembly as one rigid
	# body instead.
	ELDERLY_AGE = 0.65
	if age > ELDERLY_AGE:
		stoop = (age - ELDERLY_AGE) / (1.0 - ELDERLY_AGE) * 10  # degrees, up to 10 at age=1.0
		waist.setP(STOOP_SIGN * stoop)

	_apply_bodypart_plugins_rig(
		rig, extra_traits,
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
			"pants_style": pants_style,
			"paws": paws,
			"missing_arm": missing_arm, "missing_leg": missing_leg, "prosthetics": prosthetics,
			"accessories": accessories,
		},
	)
	return rig
