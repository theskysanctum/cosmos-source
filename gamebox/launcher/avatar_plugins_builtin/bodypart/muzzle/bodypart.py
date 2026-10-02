"""CosmOS's own built-in muzzle feature - identical geometry to what
used to live inline in avatar3d._build_face()'s own `else` branch (a
real, direct extraction, not a rewrite): a real protruding snout
REPLACING the flat nose_shape/mouth_style geometry entirely (a real
animal face is one continuous snout, not a flat nose+mouth on a flat
face) rather than fighting both systems' depth ordering against each
other. A wide base wraps cheek to cheek, a narrower bridge pokes out
further, a dark nose-tip caps the front (deliberately more forward than
any nose_shape's own max poke, so it's always the frontmost point
regardless), and a mouth-line sits on the snout's own underside.

`muzzle` (the boolean) is a partial exception to the usual "safe" bolt-
on pattern - see this feature's own bodypart.json: _build_face() still
needs a real `muzzle` gate to skip drawing the normal nose+mouth, so
build_character()/build_rig() resolve it from **extra_traits and thread
it through as a real argument (same value this plugin reads back off
`metrics["muzzle"]`) rather than leaving the gate to silently desync
from whatever this plugin decides to draw."""
import avatar3d as a3d

MOUTH_COLOR = (0.55, 0.25, 0.28, 1)


def _build_muzzle_geometry(outlined_box, vertex, normal, color, tris, idx, face_y, nose_z, mouth_z, hs, snout_c):
	snout_top, snout_bottom = nose_z + 0.14 * hs, mouth_z - 0.08 * hs
	idx = outlined_box(
		vertex, normal, color, tris, idx,
		(-0.13 * hs, face_y - 0.04 * hs, snout_bottom), (0.13 * hs, face_y + 0.03 * hs, snout_top), snout_c,
	)
	idx = outlined_box(
		vertex, normal, color, tris, idx,
		(-0.08 * hs, face_y - 0.13 * hs, nose_z - 0.05 * hs), (0.08 * hs, face_y - 0.02 * hs, nose_z + 0.09 * hs), snout_c,
	)
	idx = outlined_box(
		vertex, normal, color, tris, idx,
		(-0.045 * hs, face_y - 0.19 * hs, nose_z + 0.01 * hs), (0.045 * hs, face_y - 0.1 * hs, nose_z + 0.08 * hs), a3d.NOSE_TIP_COLOR,
	)
	idx = outlined_box(
		vertex, normal, color, tris, idx,
		(-0.06 * hs, face_y - 0.09 * hs, mouth_z - 0.02 * hs), (0.06 * hs, face_y - 0.03 * hs, mouth_z + 0.02 * hs), MOUTH_COLOR,
	)
	return idx


def _muzzle_common(traits, metrics):
	head_z0 = metrics["LEG_H"] + metrics["TORSO_H"]
	HEAD_S = metrics["HEAD_S"]
	hs = HEAD_S / 0.85
	eye_z = head_z0 + HEAD_S * 0.56
	nose_z = eye_z - 0.2 * hs
	mouth_z = nose_z - 0.16 * hs
	face_y = -HEAD_S / 2
	snout_color = traits.get("snout_color")
	snout_c = a3d._hex_to_rgba(snout_color) if snout_color else metrics["skin"]
	return face_y, nose_z, mouth_z, hs, snout_c


def build_portrait(vertex, normal, color, tris, idx, box, outlined_box, traits, metrics):
	if not metrics["muzzle"]:
		return idx
	face_y, nose_z, mouth_z, hs, snout_c = _muzzle_common(traits, metrics)
	return _build_muzzle_geometry(outlined_box, vertex, normal, color, tris, idx, face_y, nose_z, mouth_z, hs, snout_c)


def build_animated(rig, traits, metrics):
	if not metrics["muzzle"]:
		return
	if rig.neck is None:
		return
	import avatar3d_rig as a3d_rig

	face_y, nose_z, mouth_z, hs, snout_c = _muzzle_common(traits, metrics)
	vdata, vertex, normal, color, tris = a3d_rig._new_buffers()
	idx = _build_muzzle_geometry(a3d._outlined_box, vertex, normal, color, tris, 0, face_y, nose_z, mouth_z, hs, snout_c)
	muzzle_np = a3d_rig._finish(vdata, tris, "muzzle")
	muzzle_np.reparentTo(rig.neck)
	muzzle_np.setPos(rig.root, 0.0, 0.0, 0.0)
