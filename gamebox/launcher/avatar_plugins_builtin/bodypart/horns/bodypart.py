"""CosmOS's own built-in mythical horns feature - identical geometry to
what used to live inline in avatar3d._build_hair_and_toppers() (a real,
direct extraction, not a rewrite).

horns/horn_color/horn_c/horn_scale/horn_thickness are read from
`metrics`, not `traits` - unlike most extracted accessories, they stay
real, core, named parameters of build_character()/build_rig()
themselves (see avatar3d.py's own hairstyle-override check, right
before its own metrics dict is built: ears/horns/halo silently force
hairstyle down to a low-volume style since voluminous hair otherwise
fights visually with them) - only the actual geometry moved out here,
not the trait itself.

Uses avatar3d.hair_top_z_for_style() (a real, public module function)
for the exact same "how high does this hairstyle's own silhouette
reach" number ears (staying in core) also needs - see that function's
own docstring for why it exists as a real, shared, importable
function rather than a second private copy of the same table."""
import avatar3d as a3d


def _build_horns_geometry(box, vertex, normal, color, tris, idx, horns, horn_c, horn_base_z, hs, horn_scale, horn_thickness):
	if horns == "none":
		return idx
	if horns == "unicorn":
		# A single center horn, base tapering to a point.
		w1 = 0.04 * hs * horn_scale * horn_thickness
		d1 = 0.045 * hs * horn_scale * horn_thickness
		z1 = horn_base_z + 0.32 * hs * horn_scale
		w2 = 0.02 * hs * horn_scale * horn_thickness
		d2 = 0.025 * hs * horn_scale * horn_thickness
		z2 = horn_base_z + 0.5 * hs * horn_scale
		idx = box(
			vertex, normal, color, tris, idx,
			(-w1, -d1, horn_base_z), (w1, d1, z1), horn_c,
		)
		idx = box(
			vertex, normal, color, tris, idx,
			(-w2, -d2, z1), (w2, d2, z2), horn_c,
		)
	else:
		for side in (-1, 1):
			hx = side * 0.09 * hs * horn_scale
			if horns == "small":
				w = 0.04 * hs * horn_scale * horn_thickness
				d = 0.045 * hs * horn_scale * horn_thickness
				z1 = horn_base_z + 0.16 * hs * horn_scale
				idx = box(
					vertex, normal, color, tris, idx,
					(hx - w, -d, horn_base_z), (hx + w, d, z1), horn_c,
				)
			elif horns == "curved":
				# Base + a forward-curving tip segment (ram-like).
				bw = 0.045 * hs * horn_scale * horn_thickness
				bd = 0.045 * hs * horn_scale * horn_thickness
				bz = horn_base_z + 0.17 * hs * horn_scale
				idx = box(
					vertex, normal, color, tris, idx,
					(hx - bw, -bd, horn_base_z), (hx + bw, bd, bz), horn_c,
				)
				tw = 0.03 * hs * horn_scale * horn_thickness
				ty0 = -0.16 * hs * horn_scale
				ty1 = -0.03 * hs * horn_scale
				tz0 = horn_base_z + 0.11 * hs * horn_scale
				tz1 = horn_base_z + 0.28 * hs * horn_scale
				idx = box(
					vertex, normal, color, tris, idx,
					(hx - tw, ty0, tz0), (hx + tw, ty1, tz1), horn_c,
				)
			else:  # "demon" - backward-swept, pointed
				bw = 0.04 * hs * horn_scale * horn_thickness
				by0 = -0.03 * hs * horn_scale
				by1 = 0.07 * hs * horn_scale
				bz = horn_base_z + 0.17 * hs * horn_scale
				idx = box(
					vertex, normal, color, tris, idx,
					(hx - bw, by0, horn_base_z), (hx + bw, by1, bz), horn_c,
				)
				tw = 0.025 * hs * horn_scale * horn_thickness
				ty0 = 0.04 * hs * horn_scale
				ty1 = 0.22 * hs * horn_scale
				tz0 = horn_base_z + 0.14 * hs * horn_scale
				tz1 = horn_base_z + 0.34 * hs * horn_scale
				idx = box(
					vertex, normal, color, tris, idx,
					(hx - tw, ty0, tz0), (hx + tw, ty1, tz1), horn_c,
				)
	return idx


def _horn_base_z(metrics):
	head_z0 = metrics["LEG_H"] + metrics["TORSO_H"]
	HEAD_S = metrics["HEAD_S"]
	hs = HEAD_S / 0.85
	top = a3d.hair_top_z_for_style(metrics["hairstyle"], head_z0, HEAD_S, hs)
	return max(head_z0 + HEAD_S - 0.05 * hs, top - 0.05 * hs), hs


def build_portrait(vertex, normal, color, tris, idx, box, outlined_box, traits, metrics):
	horns = metrics["horns"]
	if horns == "none":
		return idx
	horn_base_z, hs = _horn_base_z(metrics)
	return _build_horns_geometry(
		outlined_box, vertex, normal, color, tris, idx,
		horns, metrics["horn_c"], horn_base_z, hs, metrics["horn_scale"], metrics["horn_thickness"],
	)


def build_animated(rig, traits, metrics):
	horns = metrics["horns"]
	if horns == "none":
		return
	if rig.neck is None:
		return
	import avatar3d_rig as a3d_rig

	horn_base_z, hs = _horn_base_z(metrics)
	vdata, vertex, normal, color, tris = a3d_rig._new_buffers()
	idx = _build_horns_geometry(
		a3d._outlined_box, vertex, normal, color, tris, 0,
		horns, metrics["horn_c"], horn_base_z, hs, metrics["horn_scale"], metrics["horn_thickness"],
	)
	horns_np = a3d_rig._finish(vdata, tris, "horns")
	horns_np.reparentTo(rig.neck)
	horns_np.setPos(rig.root, 0.0, 0.0, 0.0)
