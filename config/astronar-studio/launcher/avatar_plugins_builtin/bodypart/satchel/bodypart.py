"""CosmOS's own built-in satchel accessory - identical geometry to what
used to live inline in avatar3d._build_torso() (a real, direct
extraction, not a rewrite). See avatar_plugins_builtin/bodypart/
backpack/bodypart.py for the fuller explanation of this pattern -
same trait-key backward compatibility, same reasoning throughout."""


def _hex_to_rgba(hex_color):
	h = hex_color.lstrip("#")
	r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
	return (r, g, b, 1)


def _satchel_color(traits, metrics):
	satchel_color = traits.get("satchel_color")
	return _hex_to_rgba(satchel_color) if satchel_color else metrics["pants"]


def _satchel_geometry(box, vertex, normal, color, tris, idx, chest_half_w, hip_half_w, LEG_H, TORSO_H, satchel_c):
	steps = 5
	z_top, z_bot = LEG_H + TORSO_H * 0.95, LEG_H + TORSO_H * 0.2
	x_top, x_bot = -chest_half_w * 0.5, hip_half_w * 0.55
	for i in range(steps):
		t0, t1 = i / steps, (i + 1) / steps
		x0, z0 = x_top + (x_bot - x_top) * t0, z_top + (z_bot - z_top) * t0
		x1, z1 = x_top + (x_bot - x_top) * t1, z_top + (z_bot - z_top) * t1
		idx = box(
			vertex, normal, color, tris, idx,
			(min(x0, x1) - 0.05, -0.29, min(z0, z1)), (max(x0, x1) + 0.05, -0.17, max(z0, z1) + 0.06), satchel_c,
		)
	idx = box(
		vertex, normal, color, tris, idx,
		(hip_half_w * 0.25, -0.34, LEG_H * 0.12), (hip_half_w * 0.85, -0.12, LEG_H * 0.5), satchel_c,
	)
	return idx


def build_portrait(vertex, normal, color, tris, idx, box, outlined_box, traits, metrics):
	if "satchel" not in metrics["accessories"]:
		return idx
	return _satchel_geometry(
		outlined_box, vertex, normal, color, tris, idx,
		metrics["chest_half_w"], metrics["hip_half_w"], metrics["LEG_H"], metrics["TORSO_H"],
		_satchel_color(traits, metrics),
	)


def build_animated(rig, traits, metrics):
	if "satchel" not in metrics["accessories"]:
		return
	if rig.waist is None:
		return
	import avatar3d as a3d
	import avatar3d_rig as a3d_rig

	vdata, vertex, normal, color, tris = a3d_rig._new_buffers()
	idx = _satchel_geometry(
		a3d._outlined_box, vertex, normal, color, tris, 0,
		metrics["chest_half_w"], metrics["hip_half_w"], metrics["LEG_H"], metrics["TORSO_H"],
		_satchel_color(traits, metrics),
	)
	satchel = a3d_rig._finish(vdata, tris, "satchel")
	satchel.reparentTo(rig.waist)
	satchel.setPos(rig.root, 0.0, 0.0, 0.0)
