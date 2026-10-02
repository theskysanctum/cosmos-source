"""CosmOS's own built-in wings feature - identical geometry to what
used to live in avatar3d._build_wings() (a real, direct extraction, not
a rewrite - shared verbatim between the static portrait and animated
rig paths before this).

wing_color has no dedicated customizer field (never did - only
"wings"/"wing_scale"/"wing_thickness" were ever real CUSTOM_FIELDS
rows), so traits.get("wing_color") is always None through the live
customizer and the same conditional default (white for "angel", hair
color otherwise) applies exactly as it did before this extraction."""


def _hex_to_rgba(hex_color):
	h = hex_color.lstrip("#")
	r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
	return (r, g, b, 1)


def _wing_color(traits, metrics):
	wing_color = traits.get("wing_color")
	if wing_color:
		return _hex_to_rgba(wing_color)
	wings = traits.get("wings", "none")
	if wings == "angel":
		return (0.97, 0.97, 0.98, 1)
	return metrics["hair"]


def _build_wings_geometry(box, vertex, normal, color, tris, idx, wings, wing_c, chest_half_w, LEG_H, TORSO_H, wing_scale, wing_thickness):
	if wings == "none":
		return idx
	WING_PARAMS = {
		"angel": (4, 0.55, 0.1),
		"bat": (3, 0.5, 0.045),
		"fairy": (2, 0.22, 0.035),
		"dragon": (4, 0.58, 0.065),
	}
	segs, span, thick = WING_PARAMS[wings]
	span *= wing_scale
	thick *= wing_thickness
	z_top = LEG_H + TORSO_H * 0.85
	for side in (-1, 1):
		x0, z0 = side * chest_half_w * 0.5, z_top
		for i in range(segs):
			t0, t1 = i / segs, (i + 1) / segs
			x1 = side * (chest_half_w * 0.5 + span * t1)
			xa, xb = sorted((x0, x1))
			zz0 = z0 - span * 0.35 * t1
			zz1 = zz0 + thick + span * 0.12 * (1 - t0)
			idx = box(
				vertex, normal, color, tris, idx,
				(xa, 0.22, zz0), (xb, 0.22 + thick, zz1), wing_c,
			)
	return idx


def build_portrait(vertex, normal, color, tris, idx, box, outlined_box, traits, metrics):
	wings = traits.get("wings", "none")
	wing_scale = traits.get("wing_scale", 1.0)
	wing_thickness = traits.get("wing_thickness", 1.0)
	wing_c = _wing_color(traits, metrics)
	return _build_wings_geometry(
		outlined_box, vertex, normal, color, tris, idx,
		wings, wing_c, metrics["chest_half_w"], metrics["LEG_H"], metrics["TORSO_H"], wing_scale, wing_thickness,
	)


def build_animated(rig, traits, metrics):
	wings = traits.get("wings", "none")
	if wings == "none":
		return
	if rig.waist is None:
		return
	import avatar3d as a3d
	import avatar3d_rig as a3d_rig

	wing_scale = traits.get("wing_scale", 1.0)
	wing_thickness = traits.get("wing_thickness", 1.0)
	wing_c = _wing_color(traits, metrics)
	vdata, vertex, normal, color, tris = a3d_rig._new_buffers()
	idx = _build_wings_geometry(
		a3d._outlined_box, vertex, normal, color, tris, 0,
		wings, wing_c, metrics["chest_half_w"], metrics["LEG_H"], metrics["TORSO_H"], wing_scale, wing_thickness,
	)
	wings_np = a3d_rig._finish(vdata, tris, "wings")
	wings_np.reparentTo(rig.waist)
	wings_np.setPos(rig.root, 0.0, 0.0, 0.0)
