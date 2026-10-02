"""CosmOS's own built-in backpack accessory - identical geometry to
what used to live inline in avatar3d._build_torso() (a real, direct
extraction, not a rewrite - see that function's own docstring), moved
out as the first feature to actually go through the "bodypart" plugin
hook rather than the shared torso buffer directly.

Reads the SAME trait keys the old inline version did ("backpack"
membership in the shared "accessories" list, "backpack_color" as a hex
string or None) - a saved profile from before this extraction still
renders identically, no migration needed, since neither key's name or
meaning changed, only where the code that reads them lives."""


def _hex_to_rgba(hex_color):
	h = hex_color.lstrip("#")
	r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
	return (r, g, b, 1)


def _backpack_color(traits, metrics):
	backpack_color = traits.get("backpack_color")
	return _hex_to_rgba(backpack_color) if backpack_color else metrics["pants"]


def build_portrait(vertex, normal, color, tris, idx, box, outlined_box, traits, metrics):
	if "backpack" not in metrics["accessories"]:
		return idx
	backpack_c = _backpack_color(traits, metrics)
	LEG_H, TORSO_H, chest_half_w = metrics["LEG_H"], metrics["TORSO_H"], metrics["chest_half_w"]
	idx = outlined_box(
		vertex, normal, color, tris, idx,
		(-chest_half_w * 0.5, 0.27, LEG_H + TORSO_H * 0.15), (chest_half_w * 0.5, 0.55, LEG_H + TORSO_H * 0.92), backpack_c,
	)
	for side in (-1, 1):
		strap_x0, strap_x1 = sorted((side * chest_half_w * 0.55, side * chest_half_w * 0.72))
		idx = outlined_box(
			vertex, normal, color, tris, idx,
			(strap_x0, -0.28, LEG_H + TORSO_H * 0.8), (strap_x1, 0.3, LEG_H + TORSO_H * 0.92), backpack_c,
		)
	return idx


def build_animated(rig, traits, metrics):
	if "backpack" not in metrics["accessories"]:
		return
	if rig.waist is None:
		return
	import avatar3d as a3d
	import avatar3d_rig as a3d_rig

	backpack_c = _backpack_color(traits, metrics)
	LEG_H, TORSO_H, chest_half_w = metrics["LEG_H"], metrics["TORSO_H"], metrics["chest_half_w"]
	vdata, vertex, normal, color, tris = a3d_rig._new_buffers()
	idx = 0
	idx = a3d._outlined_box(
		vertex, normal, color, tris, idx,
		(-chest_half_w * 0.5, 0.27, LEG_H + TORSO_H * 0.15), (chest_half_w * 0.5, 0.55, LEG_H + TORSO_H * 0.92), backpack_c,
	)
	for side in (-1, 1):
		strap_x0, strap_x1 = sorted((side * chest_half_w * 0.55, side * chest_half_w * 0.72))
		idx = a3d._outlined_box(
			vertex, normal, color, tris, idx,
			(strap_x0, -0.28, LEG_H + TORSO_H * 0.8), (strap_x1, 0.3, LEG_H + TORSO_H * 0.92), backpack_c,
		)
	backpack = a3d_rig._finish(vdata, tris, "backpack")
	backpack.reparentTo(rig.waist)
	backpack.setPos(rig.root, 0.0, 0.0, 0.0)
