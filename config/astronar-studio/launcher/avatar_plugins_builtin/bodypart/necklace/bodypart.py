"""CosmOS's own built-in necklace accessory - identical geometry to
what used to live inline in avatar3d._build_torso() (a real, direct
extraction, not a rewrite). See avatar_plugins_builtin/bodypart/
backpack/bodypart.py for the fuller explanation of this pattern.

Unlike backpack/satchel/purse, necklace_color has no "match pants"
fallback (avatar3d_viewer.py's own field declares it "kind": "color",
not "color_opt") - CUSTOM_DEFAULTS always gives it a real starting hex
value, so traits["necklace_color"] is never None here."""


def _hex_to_rgba(hex_color):
	h = hex_color.lstrip("#")
	r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
	return (r, g, b, 1)


def build_portrait(vertex, normal, color, tris, idx, box, outlined_box, traits, metrics):
	if "necklace" not in metrics["accessories"]:
		return idx
	necklace_c = _hex_to_rgba(traits.get("necklace_color", "#ffd700"))
	collar_z0 = metrics["LEG_H"] + metrics["TORSO_H"]
	chest_half_w = metrics["chest_half_w"]
	idx = outlined_box(
		vertex, normal, color, tris, idx,
		(-chest_half_w * 0.35, -0.28, collar_z0 - 0.05), (chest_half_w * 0.35, -0.2, collar_z0 - 0.01), necklace_c,
	)
	idx = outlined_box(
		vertex, normal, color, tris, idx,
		(-0.025, -0.3, collar_z0 - 0.16), (0.025, -0.22, collar_z0 - 0.06), necklace_c,
	)
	return idx


def build_animated(rig, traits, metrics):
	if "necklace" not in metrics["accessories"]:
		return
	if rig.waist is None:
		return
	import avatar3d as a3d
	import avatar3d_rig as a3d_rig

	necklace_c = _hex_to_rgba(traits.get("necklace_color", "#ffd700"))
	collar_z0 = metrics["LEG_H"] + metrics["TORSO_H"]
	chest_half_w = metrics["chest_half_w"]
	vdata, vertex, normal, color, tris = a3d_rig._new_buffers()
	idx = 0
	idx = a3d._outlined_box(
		vertex, normal, color, tris, idx,
		(-chest_half_w * 0.35, -0.28, collar_z0 - 0.05), (chest_half_w * 0.35, -0.2, collar_z0 - 0.01), necklace_c,
	)
	idx = a3d._outlined_box(
		vertex, normal, color, tris, idx,
		(-0.025, -0.3, collar_z0 - 0.16), (0.025, -0.22, collar_z0 - 0.06), necklace_c,
	)
	necklace = a3d_rig._finish(vdata, tris, "necklace")
	necklace.reparentTo(rig.waist)
	necklace.setPos(rig.root, 0.0, 0.0, 0.0)
