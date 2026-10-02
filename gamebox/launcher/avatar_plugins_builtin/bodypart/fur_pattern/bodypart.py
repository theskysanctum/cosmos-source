"""CosmOS's own built-in fur_pattern feature (spots/stripes/patches) -
identical geometry to what used to live inline in avatar3d._build_face()
(the face portion, shared by both build_character() and the rig) and
avatar3d.build_character() itself (a separate arm-sleeve portion,
static-portrait-only - the rig never had this half at all, a real,
pre-existing scope cut, not introduced by this extraction) - a real,
direct extraction, not a rewrite.

Purely additive in every path it appeared in (no exclusive branch, no
gate needed anywhere), so it moved out cleanly - reads "fur_pattern"
and "pattern_color" straight off `traits`, same as any other simple
bolt-on. Still needs `metrics["top_style"]` for the arm-sleeve portion
though (only drawn when top_style leaves the arms bare - "tank_top"/
"vest")."""


def _hex_to_rgba(hex_color):
	h = hex_color.lstrip("#")
	r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
	return (r, g, b, 1)


def _pattern_color(traits, metrics):
	pattern_color = traits.get("pattern_color")
	return _hex_to_rgba(pattern_color) if pattern_color else metrics["hair"]


def _face_metrics(metrics):
	head_z0 = metrics["LEG_H"] + metrics["TORSO_H"]
	HEAD_S = metrics["HEAD_S"]
	hs = HEAD_S / 0.85
	eye_z = head_z0 + HEAD_S * 0.56
	nose_z = eye_z - 0.2 * hs
	face_y = -HEAD_S / 2
	return HEAD_S, hs, nose_z, face_y


def _build_face_overlay(box, vertex, normal, color, tris, idx, fur_pattern, pattern_c, HEAD_S, hs, nose_z, face_y):
	if fur_pattern == "spots":
		for side in (-1, 1):
			base_x = side * 0.15 * hs
			for dx, dz in ((-0.03, 0.05), (0.04, 0.03), (-0.01, -0.06), (0.05, -0.08), (-0.06, -0.02)):
				fx = base_x + dx * hs
				idx = box(
					vertex, normal, color, tris, idx,
					(fx - 0.018 * hs, face_y - 0.008 * hs, nose_z + dz * hs - 0.018 * hs), (fx + 0.018 * hs, face_y + 0.03 * hs, nose_z + dz * hs + 0.018 * hs), pattern_c,
				)
	elif fur_pattern == "stripes":
		for z_off in (-0.1, -0.02, 0.06, 0.14):
			idx = box(
				vertex, normal, color, tris, idx,
				(-HEAD_S / 2 + 0.02 * hs, face_y - 0.006 * hs, nose_z + z_off * hs), (HEAD_S / 2 - 0.02 * hs, face_y - 0.001 * hs, nose_z + z_off * hs + 0.025 * hs), pattern_c,
			)
	elif fur_pattern == "patches":
		idx = box(
			vertex, normal, color, tris, idx,
			(-HEAD_S / 2 + 0.03 * hs, face_y - 0.006 * hs, nose_z - 0.02 * hs), (-0.02 * hs, face_y - 0.001 * hs, nose_z + 0.16 * hs), pattern_c,
		)
		idx = box(
			vertex, normal, color, tris, idx,
			(0.05 * hs, face_y - 0.006 * hs, nose_z - 0.14 * hs), (HEAD_S / 2 - 0.05 * hs, face_y - 0.001 * hs, nose_z + 0.02 * hs), pattern_c,
		)
	return idx


def build_portrait(vertex, normal, color, tris, idx, box, outlined_box, traits, metrics):
	fur_pattern = traits.get("fur_pattern", "solid")
	if fur_pattern == "solid":
		return idx
	pattern_c = _pattern_color(traits, metrics)
	HEAD_S, hs, nose_z, face_y = _face_metrics(metrics)
	idx = _build_face_overlay(box, vertex, normal, color, tris, idx, fur_pattern, pattern_c, HEAD_S, hs, nose_z, face_y)

	if metrics["top_style"] in ("tank_top", "vest"):
		# Arm-sleeve portion - build_character()-only, see this file's
		# own docstring.
		chest_half_w, limb_half_w, LEG_H, TORSO_H = (
			metrics["chest_half_w"], metrics["limb_half_w"], metrics["LEG_H"], metrics["TORSO_H"],
		)
		for side in (-1, 1):
			outer = side * (chest_half_w + limb_half_w * 2)
			inner = side * chest_half_w
			ax0, ax1 = sorted((inner, outer))
			if fur_pattern == "spots":
				for az in (LEG_H + TORSO_H * 0.25, LEG_H + TORSO_H * 0.6):
					idx = box(
						vertex, normal, color, tris, idx,
						(ax0 + (ax1 - ax0) * 0.3, -0.185, az), (ax0 + (ax1 - ax0) * 0.7, -0.16, az + 0.05), pattern_c,
					)
			elif fur_pattern == "stripes":
				for az in (LEG_H + TORSO_H * 0.2, LEG_H + TORSO_H * 0.45, LEG_H + TORSO_H * 0.7):
					idx = box(
						vertex, normal, color, tris, idx,
						(ax0, -0.185, az), (ax1, -0.16, az + 0.04), pattern_c,
					)
			elif fur_pattern == "patches":
				idx = box(
					vertex, normal, color, tris, idx,
					(ax0, -0.185, LEG_H + TORSO_H * 0.3), (ax1, -0.16, LEG_H + TORSO_H * 0.7), pattern_c,
				)
	return idx


def build_animated(rig, traits, metrics):
	# Face portion only - the rig never had the arm-sleeve portion (see
	# this file's own docstring).
	fur_pattern = traits.get("fur_pattern", "solid")
	if fur_pattern == "solid":
		return
	if rig.neck is None:
		return
	import avatar3d as a3d
	import avatar3d_rig as a3d_rig

	pattern_c = _pattern_color(traits, metrics)
	HEAD_S, hs, nose_z, face_y = _face_metrics(metrics)
	vdata, vertex, normal, color, tris = a3d_rig._new_buffers()
	idx = _build_face_overlay(a3d._box, vertex, normal, color, tris, 0, fur_pattern, pattern_c, HEAD_S, hs, nose_z, face_y)
	overlay = a3d_rig._finish(vdata, tris, "fur_pattern")
	overlay.reparentTo(rig.neck)
	overlay.setPos(rig.root, 0.0, 0.0, 0.0)
