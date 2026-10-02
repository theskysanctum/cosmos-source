"""CosmOS's own built-in headphones accessory - identical geometry to
what used to live inline in both avatar3d.build_character() and
avatar3d._build_head_bundle() (two separate but identical copies, one
per path - a real, direct extraction, not a rewrite)."""


def _hex_to_rgba(hex_color):
	h = hex_color.lstrip("#")
	r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
	return (r, g, b, 1)


def _headphones_geometry(box, vertex, normal, color, tris, idx, HEAD_S, head_z0, headphones_c):
	band_z0 = head_z0 + HEAD_S + 0.02
	idx = box(
		vertex, normal, color, tris, idx,
		(-HEAD_S / 2 - 0.04, -0.08, band_z0), (HEAD_S / 2 + 0.04, 0.08, band_z0 + 0.05), headphones_c,
	)
	ear_h_z = head_z0 + HEAD_S * 0.56
	for side in (-1, 1):
		cup_x0, cup_x1 = sorted((side * (HEAD_S / 2 - 0.03), side * (HEAD_S / 2 + 0.07)))
		idx = box(
			vertex, normal, color, tris, idx,
			(cup_x0, -0.1, ear_h_z - 0.1), (cup_x1, 0.1, ear_h_z + 0.14), headphones_c,
		)
	return idx


def build_portrait(vertex, normal, color, tris, idx, box, outlined_box, traits, metrics):
	if "headphones" not in metrics["accessories"]:
		return idx
	head_z0 = metrics["LEG_H"] + metrics["TORSO_H"]
	headphones_c = _hex_to_rgba(traits.get("headphones_color", "#1a1a1a"))
	return _headphones_geometry(outlined_box, vertex, normal, color, tris, idx, metrics["HEAD_S"], head_z0, headphones_c)


def build_animated(rig, traits, metrics):
	if "headphones" not in metrics["accessories"]:
		return
	if rig.neck is None:
		return
	import avatar3d as a3d
	import avatar3d_rig as a3d_rig

	head_z0 = metrics["LEG_H"] + metrics["TORSO_H"]
	headphones_c = _hex_to_rgba(traits.get("headphones_color", "#1a1a1a"))
	vdata, vertex, normal, color, tris = a3d_rig._new_buffers()
	idx = _headphones_geometry(a3d._outlined_box, vertex, normal, color, tris, 0, metrics["HEAD_S"], head_z0, headphones_c)
	headphones = a3d_rig._finish(vdata, tris, "headphones")
	headphones.reparentTo(rig.neck)
	headphones.setPos(rig.root, 0.0, 0.0, 0.0)
