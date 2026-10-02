"""CosmOS's own built-in third-eye ("extra_eyes") feature - identical
geometry to what used to live inline in avatar3d._build_face() (a real,
direct extraction, not a rewrite).

Unlike ears/horns/halo, extra_eyes never participated in the hairstyle-
override check inside build_character()/build_rig() (only ears/horns/
halo trigger it), so it was safe to remove from _build_face()'s own
named parameter list entirely - reads "extra_eyes" straight off `traits`
(**extra_traits), same as any other simple bolt-on accessory. Still
needs `metrics["eyes"]` (the iris/pupil color) though, since the third
eye is built from the exact same sclera/iris/pupil stack as the normal
two eyes (just smaller), not new geometry, to stay visually consistent."""

WHITE = (0.96, 0.97, 1.0, 1)
PUPIL_BLACK = (0.05, 0.05, 0.07, 1)


def _build_extra_eye_geometry(box, outlined_box, vertex, normal, color, tris, idx, third_z, hs, eyes, face_y):
	idx = outlined_box(vertex, normal, color, tris, idx, (-0.07 * hs, face_y - 0.02 * hs, third_z - 0.065 * hs), (0.07 * hs, face_y + 0.03 * hs, third_z + 0.065 * hs), WHITE)
	idx = box(vertex, normal, color, tris, idx, (-0.03 * hs, face_y - 0.035 * hs, third_z - 0.03 * hs), (0.03 * hs, face_y + 0.03 * hs, third_z + 0.03 * hs), eyes)
	idx = box(vertex, normal, color, tris, idx, (-0.013 * hs, face_y - 0.05 * hs, third_z - 0.013 * hs), (0.013 * hs, face_y + 0.03 * hs, third_z + 0.013 * hs), PUPIL_BLACK)
	return idx


def _extra_eye_common(metrics):
	head_z0 = metrics["LEG_H"] + metrics["TORSO_H"]
	HEAD_S = metrics["HEAD_S"]
	hs = HEAD_S / 0.85
	eye_z = head_z0 + HEAD_S * 0.56
	face_y = -HEAD_S / 2
	third_z = eye_z + 0.22 * hs
	return third_z, hs, face_y


def build_portrait(vertex, normal, color, tris, idx, box, outlined_box, traits, metrics):
	if not traits.get("extra_eyes", False):
		return idx
	third_z, hs, face_y = _extra_eye_common(metrics)
	return _build_extra_eye_geometry(box, outlined_box, vertex, normal, color, tris, idx, third_z, hs, metrics["eyes"], face_y)


def build_animated(rig, traits, metrics):
	if not traits.get("extra_eyes", False):
		return
	if rig.neck is None:
		return
	import avatar3d as a3d
	import avatar3d_rig as a3d_rig

	third_z, hs, face_y = _extra_eye_common(metrics)
	vdata, vertex, normal, color, tris = a3d_rig._new_buffers()
	idx = _build_extra_eye_geometry(a3d._box, a3d._outlined_box, vertex, normal, color, tris, 0, third_z, hs, metrics["eyes"], face_y)
	extra_eye_np = a3d_rig._finish(vdata, tris, "extra_eyes")
	extra_eye_np.reparentTo(rig.neck)
	extra_eye_np.setPos(rig.root, 0.0, 0.0, 0.0)
