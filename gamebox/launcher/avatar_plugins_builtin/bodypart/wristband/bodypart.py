"""CosmOS's own built-in wristband accessory - identical geometry to
what used to live inline in avatar3d.build_character() (the static
path) and avatar3d._build_arm_forearm() (the rig path) - a real, direct
extraction, not a rewrite. Reads the same trait keys the old inline
version did ("wristband" membership in the shared "accessories" list,
"wristband_color" as a hex string or None).

The static and rig paths were already NOT identical before this
extraction (a real, pre-existing divergence, not introduced here): the
static path only ever drew ONE band, on the right wrist, anchored at
LEG_H (not actually wrist height - a pre-existing quirk, left exactly
as it was rather than "fixed" as part of a pure extraction pass), while
the rig path draws a band on BOTH wrists, anchored at the real wrist_z.
Both behaviors are preserved here exactly, just relocated."""


def _hex_to_rgba(hex_color):
	h = hex_color.lstrip("#")
	r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
	return (r, g, b, 1)


def _wristband_color(traits):
	c = traits.get("wristband_color")
	return _hex_to_rgba(c) if c else (0.1, 0.1, 0.12, 1)


def build_portrait(vertex, normal, color, tris, idx, box, outlined_box, traits, metrics):
	if "wristband" not in metrics["accessories"]:
		return idx
	wc = _wristband_color(traits)
	chest_half_w, limb_half_w, LEG_H = metrics["chest_half_w"], metrics["limb_half_w"], metrics["LEG_H"]
	side = 1
	outer = side * (chest_half_w + limb_half_w * 2)
	inner = side * chest_half_w
	wb_x0, wb_x1 = sorted((inner, outer))
	return outlined_box(
		vertex, normal, color, tris, idx,
		(wb_x0, -0.19, LEG_H + 0.02), (wb_x1, 0.19, LEG_H + 0.09), wc,
	)


def build_animated(rig, traits, metrics):
	if "wristband" not in metrics["accessories"]:
		return
	import avatar3d as a3d
	import avatar3d_rig as a3d_rig

	wc = _wristband_color(traits)
	chest_half_w, limb_half_w = metrics["chest_half_w"], metrics["limb_half_w"]
	wrist_z = metrics["LEG_H"]  # avatar3d_rig.py's own WRIST_Z = LEG_H exactly
	y_offset = 0.06  # avatar3d_rig.py's own ARM_Y_OFFSET
	for side, pivot in ((-1, rig.elbow_l), (1, rig.elbow_r)):
		if pivot is None:
			continue
		outer = side * (chest_half_w + limb_half_w * 2)
		inner = side * chest_half_w
		lo_x, hi_x = min(inner, outer), max(inner, outer)
		vdata, vertex, normal, color, tris = a3d_rig._new_buffers()
		idx = a3d._outlined_box(
			vertex, normal, color, tris, 0,
			(lo_x, -0.19 + y_offset, wrist_z + 0.02), (hi_x, 0.19 + y_offset, wrist_z + 0.09), wc,
		)
		band = a3d_rig._finish(vdata, tris, f"wristband_{'l' if side == -1 else 'r'}")
		band.reparentTo(pivot)
		band.setPos(rig.root, 0.0, 0.0, 0.0)
