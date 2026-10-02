"""CosmOS's own built-in hat accessory (cap/beanie/wide_brim/beret) -
identical geometry to what used to live inline in both
avatar3d.build_character() and avatar3d._build_head_bundle() (two
separate but identical copies, one per path - a real, direct
extraction, not a rewrite). hat_style is no longer one of build_
character()'s/build_rig()'s own named parameters - it's read straight
out of `traits` here instead, exactly like hat_color already was."""


def _hex_to_rgba(hex_color):
	h = hex_color.lstrip("#")
	r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
	return (r, g, b, 1)


def _hat_geometry(box, vertex, normal, color, tris, idx, HEAD_S, head_z0, hat_style, hat_c):
	if hat_style == "beanie":
		hb0 = head_z0 + HEAD_S * 0.55
		hb1 = head_z0 + HEAD_S + 0.05
		idx = box(
			vertex, normal, color, tris, idx,
			(-HEAD_S / 2 - 0.06, -HEAD_S / 2 - 0.02, hb0), (HEAD_S / 2 + 0.06, HEAD_S / 2 + 0.06, hb1), hat_c,
		)
		idx = box(vertex, normal, color, tris, idx, (-0.06, -0.06, hb1), (0.06, 0.06, hb1 + 0.1), hat_c)
	elif hat_style == "wide_brim":
		wb0 = head_z0 + HEAD_S * 0.72
		wb1 = head_z0 + HEAD_S + 0.05
		idx = box(
			vertex, normal, color, tris, idx,
			(-HEAD_S / 2 - 0.03, -HEAD_S / 2 - 0.02, wb0), (HEAD_S / 2 + 0.03, HEAD_S / 2 + 0.03, wb1), hat_c,
		)
		idx = box(
			vertex, normal, color, tris, idx,
			(-HEAD_S / 2 - 0.22, -HEAD_S / 2 - 0.22, wb0 - 0.04), (HEAD_S / 2 + 0.22, HEAD_S / 2 + 0.22, wb0 + 0.02), hat_c,
		)
	elif hat_style == "beret":
		br0 = head_z0 + HEAD_S * 0.75
		br1 = br0 + 0.08
		idx = box(
			vertex, normal, color, tris, idx,
			(-HEAD_S / 2 - 0.12, -HEAD_S / 2 - 0.05, br0), (HEAD_S / 2 + 0.02, HEAD_S / 2 + 0.15, br1), hat_c,
		)
	else:  # "cap" (default)
		hat_bottom = head_z0 + HEAD_S * 0.68
		hat_top = head_z0 + HEAD_S + 0.1
		idx = box(
			vertex, normal, color, tris, idx,
			(-HEAD_S / 2 - 0.09, -HEAD_S / 2 - 0.02, hat_bottom), (HEAD_S / 2 + 0.09, HEAD_S / 2 + 0.09, hat_top), hat_c,
		)
		idx = box(
			vertex, normal, color, tris, idx,
			(-HEAD_S * 0.3, -HEAD_S / 2 - 0.16, hat_bottom - 0.03), (HEAD_S * 0.3, -HEAD_S / 2 - 0.02, hat_bottom + 0.05), hat_c,
		)
	return idx


def _hat_color(traits, metrics):
	hat_color = traits.get("hat_color")
	return _hex_to_rgba(hat_color) if hat_color else metrics["pants"]


def build_portrait(vertex, normal, color, tris, idx, box, outlined_box, traits, metrics):
	if "hat" not in metrics["accessories"]:
		return idx
	head_z0 = metrics["LEG_H"] + metrics["TORSO_H"]
	hat_style = traits.get("hat_style", "cap")
	hat_c = _hat_color(traits, metrics)
	return _hat_geometry(outlined_box, vertex, normal, color, tris, idx, metrics["HEAD_S"], head_z0, hat_style, hat_c)


def build_animated(rig, traits, metrics):
	if "hat" not in metrics["accessories"]:
		return
	if rig.neck is None:
		return
	import avatar3d as a3d
	import avatar3d_rig as a3d_rig

	head_z0 = metrics["LEG_H"] + metrics["TORSO_H"]
	hat_style = traits.get("hat_style", "cap")
	hat_c = _hat_color(traits, metrics)
	vdata, vertex, normal, color, tris = a3d_rig._new_buffers()
	idx = _hat_geometry(a3d._outlined_box, vertex, normal, color, tris, 0, metrics["HEAD_S"], head_z0, hat_style, hat_c)
	hat = a3d_rig._finish(vdata, tris, "hat")
	hat.reparentTo(rig.neck)
	hat.setPos(rig.root, 0.0, 0.0, 0.0)
