"""CosmOS's own built-in whiskers feature - identical geometry to what
used to live inline in avatar3d._build_face() (a real, direct
extraction, not a rewrite).

Unlike ears/horns/halo, whiskers never participated in the hairstyle-
override check inside build_character()/build_rig(), so it was safe to
remove from _build_face()'s own named parameter list entirely - reads
"whiskers" straight off `traits` (**extra_traits), same as any other
simple bolt-on accessory. Independent of muzzle (a human-ish face can
still have whiskers) - the two were always independent booleans, never
coupled to each other."""
import avatar3d as a3d


def _build_whiskers_geometry(box, vertex, normal, color, tris, idx, face_y, nose_z, hs):
	for side in (-1, 1):
		wx0, wx1 = sorted((side * 0.13 * hs, side * 0.34 * hs))
		idx = box(
			vertex, normal, color, tris, idx,
			(wx0, face_y - 0.09 * hs, nose_z + 0.03 * hs), (wx1, face_y - 0.08 * hs, nose_z + 0.045 * hs), a3d.WHISKER_COLOR,
		)
		idx = box(
			vertex, normal, color, tris, idx,
			(wx0, face_y - 0.07 * hs, nose_z - 0.03 * hs), (wx1, face_y - 0.06 * hs, nose_z - 0.015 * hs), a3d.WHISKER_COLOR,
		)
	return idx


def _whiskers_common(metrics):
	head_z0 = metrics["LEG_H"] + metrics["TORSO_H"]
	HEAD_S = metrics["HEAD_S"]
	hs = HEAD_S / 0.85
	eye_z = head_z0 + HEAD_S * 0.56
	nose_z = eye_z - 0.2 * hs
	face_y = -HEAD_S / 2
	return face_y, nose_z, hs


def build_portrait(vertex, normal, color, tris, idx, box, outlined_box, traits, metrics):
	if not traits.get("whiskers", False):
		return idx
	face_y, nose_z, hs = _whiskers_common(metrics)
	return _build_whiskers_geometry(box, vertex, normal, color, tris, idx, face_y, nose_z, hs)


def build_animated(rig, traits, metrics):
	if not traits.get("whiskers", False):
		return
	if rig.neck is None:
		return
	import avatar3d_rig as a3d_rig

	face_y, nose_z, hs = _whiskers_common(metrics)
	vdata, vertex, normal, color, tris = a3d_rig._new_buffers()
	idx = _build_whiskers_geometry(a3d._box, vertex, normal, color, tris, 0, face_y, nose_z, hs)
	whiskers_np = a3d_rig._finish(vdata, tris, "whiskers")
	whiskers_np.reparentTo(rig.neck)
	whiskers_np.setPos(rig.root, 0.0, 0.0, 0.0)
