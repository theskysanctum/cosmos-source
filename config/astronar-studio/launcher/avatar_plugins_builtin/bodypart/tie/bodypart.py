"""CosmOS's own built-in necktie accessory - identical geometry to what
used to live inline in avatar3d._build_torso() (a real, direct
extraction, not a rewrite). See avatar_plugins_builtin/bodypart/
necklace/bodypart.py's own docstring for why tie_color, like
necklace_color, has no "match pants" fallback and is never None."""


def _hex_to_rgba(hex_color):
	h = hex_color.lstrip("#")
	r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
	return (r, g, b, 1)


def build_portrait(vertex, normal, color, tris, idx, box, outlined_box, traits, metrics):
	if "tie" not in metrics["accessories"]:
		return idx
	tie_c = _hex_to_rgba(traits.get("tie_color", "#1a1a1a"))
	collar_z0 = metrics["LEG_H"] + metrics["TORSO_H"]
	LEG_H = metrics["LEG_H"]
	idx = outlined_box(
		vertex, normal, color, tris, idx,
		(-0.035, -0.28, collar_z0 - 0.09), (0.035, -0.21, collar_z0 - 0.02), tie_c,
	)
	idx = outlined_box(
		vertex, normal, color, tris, idx,
		(-0.025, -0.27, LEG_H + 0.05), (0.025, -0.22, collar_z0 - 0.1), tie_c,
	)
	return idx


def build_animated(rig, traits, metrics):
	if "tie" not in metrics["accessories"]:
		return
	if rig.waist is None:
		return
	import avatar3d as a3d
	import avatar3d_rig as a3d_rig

	tie_c = _hex_to_rgba(traits.get("tie_color", "#1a1a1a"))
	collar_z0 = metrics["LEG_H"] + metrics["TORSO_H"]
	LEG_H = metrics["LEG_H"]
	vdata, vertex, normal, color, tris = a3d_rig._new_buffers()
	idx = 0
	idx = a3d._outlined_box(
		vertex, normal, color, tris, idx,
		(-0.035, -0.28, collar_z0 - 0.09), (0.035, -0.21, collar_z0 - 0.02), tie_c,
	)
	idx = a3d._outlined_box(
		vertex, normal, color, tris, idx,
		(-0.025, -0.27, LEG_H + 0.05), (0.025, -0.22, collar_z0 - 0.1), tie_c,
	)
	tie = a3d_rig._finish(vdata, tris, "tie")
	tie.reparentTo(rig.waist)
	tie.setPos(rig.root, 0.0, 0.0, 0.0)
