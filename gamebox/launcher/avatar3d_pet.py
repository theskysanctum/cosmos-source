"""Low-level box-geometry builders for a pet companion, standing beside
the person - the pet's own equivalent of avatar3d.py's human sub-
builders (`_build_leg_thigh`, `_build_torso`, ...): small, single-
purpose functions that draw ONE body part each into whatever buffer
they're given, shared verbatim by both the static path (build_character()
merges every part into the person's own one mesh, no joints - see
build_pet_static() below) and the jointed path (avatar3d_pet_rig.py
wraps each part in its own small pivoted Geom, the exact same
world-point-offset trick avatar3d_rig.py already uses for the person).

Every part builder below takes an `x_offset` (default 0.0) added to
every box's own x-coordinates, rather than building at some fixed
"beside the person" position itself - the static path bakes a real
offset in directly (build_character() merges everything, person and
pet alike, into ONE buffer of absolute coordinates, so there is no
separate pet NodePath to reposition after the fact); the jointed path
(avatar3d_pet_rig.py) instead leaves this at 0.0 and repositions the
whole pet_root NodePath once, the same "root gets one real position,
everything under it stays authored at its own natural origin" pattern
avatar3d_rig.py's own `root`/`waist` already use.

Body-plan proportions (QUADRUPED_PARAMS below) are checked against real
shoulder-height/body-length figures from dimensions.com per this
project's own standing "always check real proportions there before
building" instruction - not guessed: a Ragdoll/Bombay cat average
(~10.5in shoulder, ~19in nose-to-rump), a Beagle (~14.5in withers,
~23.5in body - used as this generator's one generic "dog", the same
"one representative size, not a breed picker" scope cut the human
generator itself makes for age/body sliders instead of a breed list),
and a Dutch Rabbit (~8.25in shoulder, ~12.5in body - a real PET breed,
not the WILD Eastern Cottontail an earlier pass used, whose own lean,
built-for-outrunning-predators proportions read as a deer/goat instead
of a compact pet rabbit; see QUADRUPED_PARAMS's own comment).
dimensions.com's own bird collection page is "under development" (no
species data at all, confirmed live) - the bird's own proportions below
are reasoned from common knowledge of a small perched pet bird
(budgerigar-sized) instead, the one species here without a real citable
source. Converted to this scene's own arbitrary units at roughly
1 unit = 24 real-world inches (derived from the human generator's own
adult total height, LEG_H+TORSO_H+HEAD_S = 3.0 units, against a
representative ~70in adult) - approximate on purpose (the human body
itself is already a stylized "moderate, not chibi-huge" Minecraft-blocky
proportion set, not a literal 1:1 scale figure), but internally
consistent so cat/dog/rabbit/bird read at correct SIZE relative to each
other and to the person standing next to them.

Species fall into two real body plans, not one reskinned shape:
QUADRUPED_PARAMS (cat/dog/rabbit - 4 legs, a head+ears+snout, a tail)
and BIRD_PARAMS (2 legs, wings instead of front legs, a beak instead of
a snout) - the same "a genuinely different skeleton, not a recolor"
reasoning the human generator's own wheelchair (legs replaced by a
frame+wheels) already established."""
import avatar3d as a3d

PET_SPECIES = ["none", "cat", "dog", "rabbit", "bird"]
QUADRUPEDS = ("cat", "dog", "rabbit")

# (shoulder_h, body_len, body_w, head_s, leg_w, ear_style, tail_style,
# back_leg_scale). body_w/leg_w aren't independently cited (dimensions.com
# gives height/length, not girth) - derived as fixed fractions of
# shoulder_h (0.5/0.13-0.2) for internal consistency across species
# instead, the same "reasoned from real anatomy, not a number
# dimensions.com states outright" scope avatar3d_rig.py's own
# ARM_Y_OFFSET comment already accepts for a number the site doesn't
# itself break out.
#
# rabbit's own numbers are a real, confirmed-live fix: the first pass
# used an Eastern Cottontail (a WILD rabbit, built lean and long for
# outrunning predators) as its reference, which - combined with sharing
# cat/dog's own flat-box torso and identical front/back leg thickness -
# rendered as a deer/goat silhouette, not a rabbit. Switched to a Dutch
# Rabbit (a real PET breed on dimensions.com, ~8.25in shoulder/~12.5in
# body - body_len:shoulder_h of ~1.5, notably more compact than the
# cottontail's own ~2.1) for a genuinely compact, round-bodied pet
# rabbit instead of a lean wild one. `back_leg_scale` (1.0 for cat/dog,
# unchanged) is a second real fix on top of that: a rabbit's hind legs/
# haunches are visibly bigger than its front legs (built for jumping,
# not walking on 4 even legs the way a cat/dog's are) - this scales
# just the BACK leg pair's own thickness, the one species-specific
# asymmetry real rabbit anatomy actually needs.
QUADRUPED_PARAMS = {
	"cat": (0.44, 0.79, 0.22, 0.20, 0.09, "cat", "cat", 1.0),
	"dog": (0.60, 0.98, 0.30, 0.27, 0.12, "floppy", "dog", 1.0),
	"rabbit": (0.34, 0.52, 0.20, 0.18, 0.075, "tall", "stub", 1.6),
}

# perch_h/body_len/body_w/head_s/leg_w - reasoned proportions (see this
# module's own docstring on why: dimensions.com has no bird species
# data at all), budgerigar-sized.
BIRD_PARAMS = {"perch_h": 0.19, "body_len": 0.29, "body_w": 0.12, "head_s": 0.10, "leg_w": 0.03}

INNER_EAR_COLOR = (0.93, 0.75, 0.78, 1)
BEAK_COLOR_DEFAULT = "#e8a23d"
BIRD_LEG_COLOR = (0.85, 0.6, 0.25, 1)


def _leg_box(vertex, normal, color, tris, idx, x, y, leg_w, z0, z1, color_c):
	"""One straight leg segment, paw (z0) to shoulder (z1) - deliberately
	a single box, not a human-style multi-joint chain: at this small a
	scale a single hip pivot rotating one straight leg already reads as
	a real walk/idle animation (see avatar3d_pet_anim.py), and a second
	knee joint would add pose complexity with no visible payoff at pet
	size. `x`/`y` are the leg's own already-offset absolute center;
	z0/z1 absolute world heights - same "bake real absolute coordinates,
	let _pivoted_part recenter them" convention avatar3d_rig.py's own
	leg/arm builders use."""
	return a3d._outlined_box(
		vertex, normal, color, tris, idx,
		(x - leg_w / 2, y - leg_w / 2, z0), (x + leg_w / 2, y + leg_w / 2, z1), color_c,
	)


def _paw_box(vertex, normal, color, tris, idx, x, y, leg_w, color_c):
	"""A small flat pad at the very bottom of a leg - purely a silhouette
	read (distinguishes "foot" from "leg shaft" at a glance, the same
	"a thick ankle block reads as a foot" reasoning the human generator's
	own _build_foot docstring gives), not a separate pivot."""
	pad_w = leg_w * 1.3
	return a3d._outlined_box(
		vertex, normal, color, tris, idx,
		(x - pad_w / 2, y - pad_w / 2 - leg_w * 0.15, 0.0), (x + pad_w / 2, y + pad_w / 2 + leg_w * 0.15, leg_w * 0.6), color_c,
	)


# Torso's own top/bottom, as a fraction of body_w above/below
# shoulder_h - shared between _build_pet_torso and _build_pet_head
# (which anchors the head off the torso's own top), so both must use
# this SAME formula (same "recompute the one relevant shared number
# identically in both call sites" tradeoff avatar3d_rig.py's own
# finger/thumb pivot math already accepts, rather than threading an
# extra return value through). A real, confirmed-live proportions fix:
# the first pass sat the torso's bottom edge flush at shoulder_h with
# no overlap into the legs at all and only body_w*0.9 of height above
# it - rendered as a stilt-legged, paper-thin sliver with the head
# blended flat into the torso's own silhouette (no visible neck/head
# boundary). Dipping the bottom edge below shoulder_h hides the leg/
# torso seam under real overlap instead of a hard flush line, and
# raising the top edge well above shoulder_h gives the torso real
# visible girth plus enough clearance for the head to sit ABOVE it
# (see _build_pet_head) rather than crammed into the same z-band.
PET_TORSO_Z0_FRAC, PET_TORSO_Z1_FRAC = -0.3, 1.15


def _build_pet_torso(vertex, normal, color, tris, idx, body_len, body_w, shoulder_h, color_c, x_offset=0.0):
	"""The single elongated body box every quadruped shares - see
	PET_TORSO_Z0_FRAC/PET_TORSO_Z1_FRAC's own comment for exactly how
	its z-range is derived and why."""
	y0, y1 = -body_len * 0.5, body_len * 0.42
	z0, z1 = shoulder_h + body_w * PET_TORSO_Z0_FRAC, shoulder_h + body_w * PET_TORSO_Z1_FRAC
	return a3d._outlined_box(
		vertex, normal, color, tris, idx,
		(x_offset - body_w / 2, y0, z0), (x_offset + body_w / 2, y1, z1), color_c,
	)


def _build_pet_head(vertex, normal, color, tris, idx, ear_style, head_s, body_len, body_w, shoulder_h, color_c, snout_c, x_offset=0.0):
	"""Head + snout + species ear_style, mounted at the FRONT (-y) of
	the torso (see _build_pet_torso's own y0) and sitting mostly ABOVE
	its own top edge (PET_TORSO_Z1_FRAC), overlapping down into it by
	just head_s*0.15 for a real connection rather than a floating gap -
	see PET_TORSO_Z0_FRAC/Z1_FRAC's own comment for why (this used to
	sit centered in the torso's own z-band instead, reading as one
	blended mass with no visible head silhouette at all)."""
	torso_z1 = shoulder_h + body_w * PET_TORSO_Z1_FRAC
	head_bottom = torso_z1 - head_s * 0.15
	head_cz = head_bottom + head_s / 2
	head_y1 = -body_len * 0.5 + head_s * 0.15
	head_y0 = head_y1 - head_s
	idx = a3d._outlined_box(
		vertex, normal, color, tris, idx,
		(x_offset - head_s / 2, head_y0, head_cz - head_s / 2), (x_offset + head_s / 2, head_y1, head_cz + head_s / 2), color_c,
	)
	# Snout - a smaller box poking further forward (-y) than the head's
	# own front face, the same "poke out to be visible/read as a real
	# feature" rule the human generator's eyebrows/ears/hat all use.
	snout_w = head_s * 0.42
	snout_y0 = head_y0 - head_s * 0.32
	idx = a3d._outlined_box(
		vertex, normal, color, tris, idx,
		(x_offset - snout_w / 2, snout_y0, head_cz - head_s * 0.12), (x_offset + snout_w / 2, head_y0 + head_s * 0.08, head_cz + head_s * 0.14), snout_c,
	)
	# Nose tip - a tiny dark cap on the very front of the snout.
	idx = a3d._box(
		vertex, normal, color, tris, idx,
		(x_offset - snout_w * 0.3, snout_y0 - 0.012, head_cz - snout_w * 0.1), (x_offset + snout_w * 0.3, snout_y0 + 0.01, head_cz + snout_w * 0.18), (0.08, 0.07, 0.08, 1),
	)
	# Eyes - a real, confirmed gap: the only face marking here used to
	# be the nose tip above, so every species read as faceless. Two
	# small dark boxes on the head's own front-upper area (above the
	# snout, below the ears), poking slightly past the head's own front
	# face - same "poke out past what it's attached to" rule as every
	# other raised feature in this file, so they're never swallowed by
	# the head's own same-toned silhouette the way the first-pass
	# floppy ear was.
	eye_w = head_s * 0.16
	eye_z = head_cz + head_s * 0.22
	for side in (-1, 1):
		ex = x_offset + side * head_s * 0.26
		idx = a3d._box(
			vertex, normal, color, tris, idx,
			(ex - eye_w / 2, head_y0 - 0.015, eye_z - eye_w / 2), (ex + eye_w / 2, head_y0 + head_s * 0.08, eye_z + eye_w / 2), (0.08, 0.07, 0.08, 1),
		)
	ear_top = head_cz + head_s / 2
	if ear_style == "cat":
		# Small triangular-read taper (base+tip, same technique as the
		# human generator's own upright ears), close-set and upright.
		for side in (-1, 1):
			ex = x_offset + side * head_s * 0.28
			idx = a3d._outlined_box(
				vertex, normal, color, tris, idx,
				(ex - head_s * 0.13, head_y0 + head_s * 0.15, ear_top), (ex + head_s * 0.13, head_y1 - head_s * 0.1, ear_top + head_s * 0.42), color_c,
			)
			tip_w = head_s * 0.08
			idx = a3d._outlined_box(
				vertex, normal, color, tris, idx,
				(ex - tip_w, head_y0 + head_s * 0.18, ear_top + head_s * 0.42), (ex + tip_w, head_y1 - head_s * 0.13, ear_top + head_s * 0.62), color_c,
			)
	elif ear_style == "floppy":
		# Hangs down the SIDE of the head instead of standing up - a
		# dog's own silhouette, same "attach at the surface, hang down"
		# construction as the human generator's own floppy ears.
		# A real, confirmed-live visibility bug: the first pass centered
		# this flap at just 0.55*head_s from the head's own centerline,
		# barely past the head box's own half-width (0.5*head_s) edge -
		# almost entirely swallowed by the head's own same-color
		# silhouette instead of reading as a separate flap. Pushed
		# further out (0.66) and hung entirely BELOW head_cz (not
		# straddling it) for a real "flap hanging off the side" read.
		for side in (-1, 1):
			ex = x_offset + side * head_s * 0.66
			idx = a3d._outlined_box(
				vertex, normal, color, tris, idx,
				(ex - head_s * 0.13, head_y0 + head_s * 0.1, head_cz - head_s * 0.8), (ex + head_s * 0.13, head_y1 - head_s * 0.15, head_cz - head_s * 0.05), color_c,
			)
	elif ear_style == "tall":
		# Long, narrow, upright - a rabbit's own famous long-eared
		# silhouette, taller and much narrower than "cat"'s.
		for side in (-1, 1):
			ex = x_offset + side * head_s * 0.22
			idx = a3d._outlined_box(
				vertex, normal, color, tris, idx,
				(ex - head_s * 0.09, head_y0 + head_s * 0.25, ear_top), (ex + head_s * 0.09, head_y1 - head_s * 0.2, ear_top + head_s * 0.95), color_c,
			)
			idx = a3d._box(
				vertex, normal, color, tris, idx,
				(ex - head_s * 0.05, head_y0 + head_s * 0.28, ear_top + head_s * 0.05), (ex + head_s * 0.05, head_y1 - head_s * 0.25, ear_top + head_s * 0.85), INNER_EAR_COLOR,
			)
	return idx


# (segments, width-fraction-of-body_w, length, curl_dir) per species
# tail - the exact same (segments, width, length, curl) shape this
# project's human generator already uses for its own tail (see
# avatar3d._build_tail's own docstring) - reused conceptually, not
# called directly, since a pet tail attaches to a totally different
# (much lower, torso-relative) root point.
PET_TAIL_PARAMS = {
	"cat": (4, 0.75, 0.4, "up"),
	"dog": (2, 0.7, 0.16, "up"),
	"stub": (1, 0.9, 0.06, "flat"),
}


def _build_pet_tail(vertex, normal, color, tris, idx, tail_style, body_len, body_w, shoulder_h, color_c, tail_scale=1.0, tail_thickness=1.0, x_offset=0.0):
	"""Segmented, staircase-curled tail at the BACK (+y) of the torso -
	same blocky-arc technique as avatar3d._build_tail, sized off the
	body's own body_w (a tail's thickness scales with the animal, not a
	fixed constant) rather than an unrelated per-species width table."""
	segs, width_frac, length, curl_dir = PET_TAIL_PARAMS[tail_style]
	length *= tail_scale
	tail_w = body_w * width_frac * 0.32 * tail_thickness
	back_y0 = body_len * 0.42
	tail_z0 = shoulder_h + body_w * 0.55
	curl = 0.5 if curl_dir == "up" else 0.05
	for i in range(segs):
		t0, t1 = i / segs, (i + 1) / segs
		y0, y1 = back_y0 + length * t0, back_y0 + length * t1
		zoff0, zoff1 = curl * (t0 ** 2) * length, curl * (t1 ** 2) * length
		z_lo, z_hi = tail_z0 + min(zoff0, zoff1), tail_z0 + max(zoff0, zoff1) + tail_w
		idx = a3d._outlined_box(
			vertex, normal, color, tris, idx,
			(x_offset - tail_w / 2, y0, z_lo), (x_offset + tail_w / 2, y1, z_hi), color_c,
		)
	return idx


def _build_bird_wing(vertex, normal, color, tris, idx, side, body_len, body_w, shoulder_h, color_c, x_offset=0.0):
	"""A single folded wing along the bird's own side - a flat angled
	panel (perched/resting pose, not spread for flight; this generator
	never animates flight, only an idle bob/hop - see
	avatar3d_pet_anim.py), REPLACING where a quadruped's front leg would
	attach, same "a genuinely different limb here, not a reused one"
	reasoning as the human generator's own wheelchair swap."""
	x0 = x_offset + side * body_w * 0.42
	x1 = x_offset + side * (body_w * 0.42 + 0.012)
	y0, y1 = -body_len * 0.1, body_len * 0.32
	# Spans within the torso's own z-range (see PET_TORSO_Z0_FRAC/
	# Z1_FRAC's own comment) with a small inset top/bottom, rather than
	# its own independent fixed fractions - keeps the wing sitting flush
	# against the actual torso silhouette even if that range is tuned
	# again later.
	z0 = shoulder_h + body_w * (PET_TORSO_Z0_FRAC + 0.15)
	z1 = shoulder_h + body_w * (PET_TORSO_Z1_FRAC - 0.1)
	return a3d._outlined_box(vertex, normal, color, tris, idx, (min(x0, x1), y0, z0), (max(x0, x1), y1, z1), color_c)


def _build_bird_beak(vertex, normal, color, tris, idx, head_s, body_len, head_cz, beak_c, x_offset=0.0):
	"""A stubby triangular-read beak poking off the front of the head -
	sized up from a first pass that read as an almost invisible sliver
	(beak_w=0.22*head_s, only 0.4*head_s deep) to something that
	actually registers as a beak at this small a scale."""
	beak_w = head_s * 0.4
	y1 = -body_len * 0.5 + head_s * 0.15 - head_s
	y0 = y1 - head_s * 0.55
	return a3d._outlined_box(
		vertex, normal, color, tris, idx,
		(x_offset - beak_w / 2, y0, head_cz - head_s * 0.14), (x_offset + beak_w / 2, y1, head_cz + head_s * 0.14), beak_c,
	)


def build_pet_static(vertex, normal, color, tris, idx, species, color_c, snout_c, beak_c, x_offset=0.0, tail_scale=1.0, tail_thickness=1.0):
	"""Every part of one pet, fully merged (no joints) at a fixed idle
	pose, offset sideways by `x_offset` so it stands beside the person
	rather than inside them - the pet's own equivalent of
	avatar3d.build_character() appending tail/wings into its own single
	buffer. Called directly by build_character() (see its own
	pet_species handling) so every render_portrait() call site
	everywhere in the app shows the pet for free, zero extra call sites
	needed. No-op for species="none". `snout_c`/`beak_c` default to a
	shade of `color_c` at the CALL site (see build_character's own
	defaulting), not inside this function, matching every other
	optional-accent-color convention in this project."""
	if species == "none":
		return idx
	if species == "bird":
		p = BIRD_PARAMS
		shoulder_h = p["perch_h"]
		idx = _leg_box(vertex, normal, color, tris, idx, x_offset - p["leg_w"] * 1.4, 0.0, p["leg_w"], 0.0, shoulder_h, BIRD_LEG_COLOR)
		idx = _leg_box(vertex, normal, color, tris, idx, x_offset + p["leg_w"] * 1.4, 0.0, p["leg_w"], 0.0, shoulder_h, BIRD_LEG_COLOR)
		idx = _build_pet_torso(vertex, normal, color, tris, idx, p["body_len"], p["body_w"], shoulder_h, color_c, x_offset=x_offset)
		# Matches _build_pet_head's own internal head_cz formula exactly
		# (see PET_TORSO_Z0_FRAC/Z1_FRAC's own comment on why this is a
		# recompute rather than a threaded return value) - needed here
		# too since the beak anchors off this same point.
		torso_z1 = shoulder_h + p["body_w"] * PET_TORSO_Z1_FRAC
		head_cz = (torso_z1 - p["head_s"] * 0.15) + p["head_s"] / 2
		idx = _build_pet_head(vertex, normal, color, tris, idx, "none", p["head_s"], p["body_len"], p["body_w"], shoulder_h, color_c, color_c, x_offset=x_offset)
		idx = _build_bird_beak(vertex, normal, color, tris, idx, p["head_s"], p["body_len"], head_cz, beak_c, x_offset=x_offset)
		idx = _build_bird_wing(vertex, normal, color, tris, idx, -1, p["body_len"], p["body_w"], shoulder_h, color_c, x_offset=x_offset)
		idx = _build_bird_wing(vertex, normal, color, tris, idx, 1, p["body_len"], p["body_w"], shoulder_h, color_c, x_offset=x_offset)
		return idx
	shoulder_h, body_len, body_w, head_s, leg_w, ear_style, tail_style, back_leg_scale = QUADRUPED_PARAMS[species]
	front_y, back_y = -body_len * 0.28, body_len * 0.28
	back_leg_w = leg_w * back_leg_scale
	for side in (-1, 1):
		for y, w in ((front_y, leg_w), (back_y, back_leg_w)):
			leg_x = x_offset + side * (body_w / 2 - w * 0.5)
			idx = _leg_box(vertex, normal, color, tris, idx, leg_x, y, w, 0.0, shoulder_h, color_c)
			idx = _paw_box(vertex, normal, color, tris, idx, leg_x, y, w, color_c)
	idx = _build_pet_torso(vertex, normal, color, tris, idx, body_len, body_w, shoulder_h, color_c, x_offset=x_offset)
	idx = _build_pet_head(vertex, normal, color, tris, idx, ear_style, head_s, body_len, body_w, shoulder_h, color_c, snout_c, x_offset=x_offset)
	idx = _build_pet_tail(vertex, normal, color, tris, idx, tail_style, body_len, body_w, shoulder_h, color_c, tail_scale=tail_scale, tail_thickness=tail_thickness, x_offset=x_offset)
	return idx
