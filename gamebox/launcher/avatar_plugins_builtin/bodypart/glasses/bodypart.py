"""CosmOS's own built-in glasses accessory - identical geometry to what
used to live inline in both avatar3d.build_character() and
avatar3d._build_head_bundle() (two separate but identical copies, one
per path - a real, direct extraction, not a rewrite; unifying them into
one file is a real bonus this extraction gets for free). Uses `box`
(not `outlined_box`), matching the original exactly - a thin
rectangular frame around each eye built from 4 bars, not an outlined
box, so the sclera/iris/pupil stay visible through the "lens" opening."""


def _hex_to_rgba(hex_color):
	h = hex_color.lstrip("#")
	r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
	return (r, g, b, 1)


def _glasses_geometry(box, vertex, normal, color, tris, idx, HEAD_S, hs, eye_z, face_y, glasses_c):
	for side in (-1, 1):
		ex = side * HEAD_S * 0.22
		gy = (face_y - 0.11 * hs, face_y - 0.095 * hs)
		bar = 0.022 * hs
		fx0, fx1 = ex - 0.115 * hs, ex + 0.115 * hs
		fz0, fz1 = eye_z - 0.105 * hs, eye_z + 0.105 * hs
		idx = box(vertex, normal, color, tris, idx, (fx0, gy[0], fz1 - bar), (fx1, gy[1], fz1), glasses_c)  # top
		idx = box(vertex, normal, color, tris, idx, (fx0, gy[0], fz0), (fx1, gy[1], fz0 + bar), glasses_c)  # bottom
		idx = box(vertex, normal, color, tris, idx, (fx0, gy[0], fz0), (fx0 + bar, gy[1], fz1), glasses_c)  # left
		idx = box(vertex, normal, color, tris, idx, (fx1 - bar, gy[0], fz0), (fx1, gy[1], fz1), glasses_c)  # right
	idx = box(
		vertex, normal, color, tris, idx,
		(-HEAD_S * 0.075, face_y - 0.11 * hs, eye_z - 0.011 * hs), (HEAD_S * 0.075, face_y - 0.095 * hs, eye_z + 0.011 * hs), glasses_c,
	)
	return idx


def build_portrait(vertex, normal, color, tris, idx, box, outlined_box, traits, metrics):
	if "glasses" not in metrics["accessories"]:
		return idx
	HEAD_S = metrics["HEAD_S"]
	hs = HEAD_S / 0.85
	eye_z = metrics["LEG_H"] + metrics["TORSO_H"] + HEAD_S * 0.56
	face_y = -HEAD_S / 2
	glasses_c = _hex_to_rgba(traits.get("glasses_color", "#1a1a1a"))
	return _glasses_geometry(box, vertex, normal, color, tris, idx, HEAD_S, hs, eye_z, face_y, glasses_c)


def build_animated(rig, traits, metrics):
	if "glasses" not in metrics["accessories"]:
		return
	if rig.neck is None:
		return
	import avatar3d as a3d
	import avatar3d_rig as a3d_rig

	HEAD_S = metrics["HEAD_S"]
	hs = HEAD_S / 0.85
	eye_z = metrics["LEG_H"] + metrics["TORSO_H"] + HEAD_S * 0.56
	face_y = -HEAD_S / 2
	glasses_c = _hex_to_rgba(traits.get("glasses_color", "#1a1a1a"))
	vdata, vertex, normal, color, tris = a3d_rig._new_buffers()
	idx = _glasses_geometry(a3d._box, vertex, normal, color, tris, 0, HEAD_S, hs, eye_z, face_y, glasses_c)
	glasses = a3d_rig._finish(vdata, tris, "glasses")
	glasses.reparentTo(rig.neck)
	glasses.setPos(rig.root, 0.0, 0.0, 0.0)
