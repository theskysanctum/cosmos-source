"""CosmOS's own built-in furry/anthro tail feature - identical geometry
to what used to live in avatar3d._build_tail() (a real, direct
extraction, not a rewrite - shared verbatim between the static portrait
and animated rig paths before this, exactly the kind of already-unified
helper this extraction pattern fits best).

Reads `tail`/`tail_scale`/`tail_thickness`/`tail_depth` from `traits`
(this plugin's own fields) and `animal` (the fur color, an RGBA tuple)/
`LEG_H` from `metrics` - `animal_color`/`animal` stay a real, shared
CUSTOM_FIELDS entry in avatar3d_viewer.py itself (not part of this
plugin's own manifest) since ears, staying in core, uses the exact same
field."""


def _build_tail_geometry(box, vertex, normal, color, tris, idx, tail, animal, LEG_H, tail_scale, tail_thickness, tail_depth):
	if tail == "none":
		return idx
	# (segments, width, length, curl, tip_style) - tip_style adds a
	# per-species accent on top of the shared construction: a
	# lighter/darker last segment ("white_tip"/"spade_tip"/"arrow_tip")
	# or alternating banded segments ("rings").
	TAIL_PARAMS = {
		"cat": (5, 0.10, 0.85, 0.4, None),
		"fox": (5, 0.16, 0.90, 0.25, "white_tip"),
		"wolf": (5, 0.15, 0.85, 0.15, None),
		"bunny": (1, 0.16, 0.12, 0.0, None),
		"dog": (3, 0.13, 0.4, 0.1, None),
		"bear": (1, 0.15, 0.15, 0.0, None),
		"deer": (2, 0.11, 0.22, 0.05, "white_tip"),
		"horse": (6, 0.09, 1.1, 0.05, None),
		"pig": (2, 0.08, 0.25, 0.9, None),
		"raccoon": (5, 0.15, 0.78, 0.2, "rings"),
		"lizard": (6, 0.13, 1.0, 0.05, None),
		"dragon": (6, 0.14, 1.05, 0.1, "spade_tip"),
		"demon": (5, 0.08, 0.9, 0.55, "arrow_tip"),
	}
	segs, tail_w, length, curl, tip_style = TAIL_PARAMS[tail]
	length *= tail_scale
	seg_width = tail_w * tail_thickness
	seg_depth = tail_w * tail_depth
	tail_color = (0.96, 0.96, 0.96, 1) if tail == "bunny" else animal
	TIP_ACCENT_COLORS = {
		"white_tip": (0.96, 0.96, 0.96, 1),
		"spade_tip": (0.15, 0.12, 0.14, 1),
		"arrow_tip": (0.1, 0.08, 0.09, 1),
	}
	RING_COLOR = (0.15, 0.15, 0.16, 1)
	back_y0, tail_z0 = 0.3, LEG_H + 0.05
	for i in range(segs):
		t0, t1 = i / segs, (i + 1) / segs
		y0, y1 = back_y0 + length * t0, back_y0 + length * t1
		zoff0, zoff1 = curl * (t0 ** 2) * length, curl * (t1 ** 2) * length
		z_lo, z_hi = tail_z0 + min(zoff0, zoff1), tail_z0 + max(zoff0, zoff1) + seg_depth
		if tip_style == "rings":
			seg_color = RING_COLOR if i % 2 == 1 else tail_color
		elif tip_style in TIP_ACCENT_COLORS and i == segs - 1:
			seg_color = TIP_ACCENT_COLORS[tip_style]
		else:
			seg_color = tail_color
		idx = box(
			vertex, normal, color, tris, idx,
			(-seg_width / 2, y0, z_lo), (seg_width / 2, y1, z_hi), seg_color,
		)
	return idx


def build_portrait(vertex, normal, color, tris, idx, box, outlined_box, traits, metrics):
	tail = traits.get("tail", "none")
	tail_scale = traits.get("tail_scale", 1.0)
	tail_thickness = traits.get("tail_thickness", 1.0)
	tail_depth = traits.get("tail_depth", 1.0)
	return _build_tail_geometry(
		outlined_box, vertex, normal, color, tris, idx,
		tail, metrics["animal"], metrics["LEG_H"], tail_scale, tail_thickness, tail_depth,
	)


def build_animated(rig, traits, metrics):
	tail = traits.get("tail", "none")
	if tail == "none":
		return
	if rig.waist is None:
		return
	import avatar3d as a3d
	import avatar3d_rig as a3d_rig

	tail_scale = traits.get("tail_scale", 1.0)
	tail_thickness = traits.get("tail_thickness", 1.0)
	tail_depth = traits.get("tail_depth", 1.0)
	vdata, vertex, normal, color, tris = a3d_rig._new_buffers()
	idx = _build_tail_geometry(
		a3d._outlined_box, vertex, normal, color, tris, 0,
		tail, metrics["animal"], metrics["LEG_H"], tail_scale, tail_thickness, tail_depth,
	)
	tail_np = a3d_rig._finish(vdata, tris, "tail")
	tail_np.reparentTo(rig.waist)
	tail_np.setPos(rig.root, 0.0, 0.0, 0.0)
