"""CosmOS's own built-in earrings accessory - identical geometry to
what used to live inline in both avatar3d.build_character() and
avatar3d._build_head_bundle() (two separate but identical copies, one
per path - a real, direct extraction, not a rewrite)."""


def _hex_to_rgba(hex_color):
	h = hex_color.lstrip("#")
	r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
	return (r, g, b, 1)


def _earrings_geometry(box, vertex, normal, color, tris, idx, HEAD_S, hs, eye_z, earrings_c):
	for side in (-1, 1):
		ear_x0, ear_x1 = sorted((side * (HEAD_S / 2 + 0.05 * hs), side * (HEAD_S / 2 - 0.02 * hs)))
		idx = box(
			vertex, normal, color, tris, idx,
			(ear_x0, -0.05 * hs, eye_z - 0.08 * hs), (ear_x1, 0.05 * hs, eye_z - 0.03 * hs), earrings_c,
		)
	return idx


def build_portrait(vertex, normal, color, tris, idx, box, outlined_box, traits, metrics):
	if "earrings" not in metrics["accessories"]:
		return idx
	HEAD_S = metrics["HEAD_S"]
	hs = HEAD_S / 0.85
	eye_z = metrics["LEG_H"] + metrics["TORSO_H"] + HEAD_S * 0.56
	earrings_c = _hex_to_rgba(traits.get("earrings_color", "#ffd700"))
	return _earrings_geometry(outlined_box, vertex, normal, color, tris, idx, HEAD_S, hs, eye_z, earrings_c)


def build_animated(rig, traits, metrics):
	if "earrings" not in metrics["accessories"]:
		return
	if rig.neck is None:
		return
	import avatar3d as a3d
	import avatar3d_rig as a3d_rig

	HEAD_S = metrics["HEAD_S"]
	hs = HEAD_S / 0.85
	eye_z = metrics["LEG_H"] + metrics["TORSO_H"] + HEAD_S * 0.56
	earrings_c = _hex_to_rgba(traits.get("earrings_color", "#ffd700"))
	vdata, vertex, normal, color, tris = a3d_rig._new_buffers()
	idx = _earrings_geometry(a3d._outlined_box, vertex, normal, color, tris, 0, HEAD_S, hs, eye_z, earrings_c)
	earrings = a3d_rig._finish(vdata, tris, "earrings")
	earrings.reparentTo(rig.neck)
	earrings.setPos(rig.root, 0.0, 0.0, 0.0)
