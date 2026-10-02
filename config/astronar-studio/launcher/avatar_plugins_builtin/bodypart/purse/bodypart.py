"""CosmOS's own built-in purse accessory - identical geometry to what
used to live inline in avatar3d._build_torso() (a real, direct
extraction, not a rewrite). See avatar_plugins_builtin/bodypart/
backpack/bodypart.py for the fuller explanation of this pattern -
same trait-key backward compatibility, same reasoning throughout."""


def _hex_to_rgba(hex_color):
	h = hex_color.lstrip("#")
	r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
	return (r, g, b, 1)


def _purse_color(traits, metrics):
	purse_color = traits.get("purse_color")
	return _hex_to_rgba(purse_color) if purse_color else metrics["pants"]


def build_portrait(vertex, normal, color, tris, idx, box, outlined_box, traits, metrics):
	if "purse" not in metrics["accessories"]:
		return idx
	LEG_H, TORSO_H, hip_half_w = metrics["LEG_H"], metrics["TORSO_H"], metrics["hip_half_w"]
	purse_c = _purse_color(traits, metrics)
	idx = outlined_box(
		vertex, normal, color, tris, idx,
		(-hip_half_w * 0.85, -0.28, LEG_H * 0.55), (-hip_half_w * 0.65, -0.18, LEG_H + TORSO_H * 0.9), purse_c,
	)
	idx = outlined_box(
		vertex, normal, color, tris, idx,
		(-hip_half_w * 0.95, -0.32, LEG_H * 0.18), (-hip_half_w * 0.45, -0.12, LEG_H * 0.5), purse_c,
	)
	return idx


def build_animated(rig, traits, metrics):
	if "purse" not in metrics["accessories"]:
		return
	if rig.waist is None:
		return
	import avatar3d as a3d
	import avatar3d_rig as a3d_rig

	LEG_H, TORSO_H, hip_half_w = metrics["LEG_H"], metrics["TORSO_H"], metrics["hip_half_w"]
	purse_c = _purse_color(traits, metrics)
	vdata, vertex, normal, color, tris = a3d_rig._new_buffers()
	idx = 0
	idx = a3d._outlined_box(
		vertex, normal, color, tris, idx,
		(-hip_half_w * 0.85, -0.28, LEG_H * 0.55), (-hip_half_w * 0.65, -0.18, LEG_H + TORSO_H * 0.9), purse_c,
	)
	idx = a3d._outlined_box(
		vertex, normal, color, tris, idx,
		(-hip_half_w * 0.95, -0.32, LEG_H * 0.18), (-hip_half_w * 0.45, -0.12, LEG_H * 0.5), purse_c,
	)
	purse = a3d_rig._finish(vdata, tris, "purse")
	purse.reparentTo(rig.waist)
	purse.setPos(rig.root, 0.0, 0.0, 0.0)
