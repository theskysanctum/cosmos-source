"""CosmOS's own built-in claws feature - identical geometry to what
used to live inline in avatar3d.build_character() (the static path,
both hands and feet in one combined block) and avatar3d._build_hand()/
_build_foot() (the rig path) - a real, direct extraction, not a
rewrite.

Purely additive in every path it appeared in (no exclusive branch to
preserve, unlike paws), so it moved out cleanly - reads "claws" and
"claw_color" straight off `traits`, same as any other simple bolt-on.
Still reads `metrics["paws"]` though, since claw-tip position shifts to
match wherever "paws" itself drew (or didn't) - the two features are
independent booleans, but their geometry was always positioned
relative to each other.

The static and rig paths were already NOT identical before this
extraction (not introduced here): the static path draws claws
unconditionally (no is_prosthetic check at all, on either hands or
feet), while the rig's hand claws are skipped on a prosthetic hand (the
rig's foot claws have no such check, matching the static foot). Both
behaviors are preserved here exactly, just relocated."""


def _hex_to_rgba(hex_color):
	h = hex_color.lstrip("#")
	r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
	return (r, g, b, 1)


def _claw_color(traits):
	c = traits.get("claw_color")
	return _hex_to_rgba(c) if c else _hex_to_rgba("#f0ebd9")


def build_portrait(vertex, normal, color, tris, idx, box, outlined_box, traits, metrics):
	if not traits.get("claws", False):
		return idx
	claw_c = _claw_color(traits)
	paws = metrics["paws"]
	chest_half_w, limb_half_w, hip_half_w, LEG_H = (
		metrics["chest_half_w"], metrics["limb_half_w"], metrics["hip_half_w"], metrics["LEG_H"],
	)
	for side in (-1, 1):
		outer = side * (chest_half_w + limb_half_w * 2)
		inner = side * chest_half_w
		hx0, hx1 = sorted((inner, outer))
		claw_w = (hx1 - hx0) / 4
		for i in range(3):
			cx0 = hx0 + claw_w * (i + 0.7)
			idx = outlined_box(
				vertex, normal, color, tris, idx,
				(cx0, -0.24 if paws else -0.2, LEG_H - 0.06), (cx0 + claw_w * 0.4, -0.19 if paws else -0.15, LEG_H - 0.01), claw_c,
			)
		lx0 = side * hip_half_w * 0.5 - limb_half_w
		claw_w = (limb_half_w * 2) / 4
		for i in range(3):
			cx0 = lx0 + claw_w * (i + 0.7)
			idx = outlined_box(
				vertex, normal, color, tris, idx,
				(cx0, -0.29 if paws else -0.23, -0.02), (cx0 + claw_w * 0.4, -0.23 if paws else -0.17, 0.03), claw_c,
			)
	return idx


def build_animated(rig, traits, metrics):
	if not traits.get("claws", False):
		return
	import avatar3d as a3d
	import avatar3d_rig as a3d_rig

	claw_c = _claw_color(traits)
	paws = metrics["paws"]
	chest_half_w, limb_half_w, hip_half_w = metrics["chest_half_w"], metrics["limb_half_w"], metrics["hip_half_w"]
	wrist_z = metrics["LEG_H"]  # avatar3d_rig.py's own WRIST_Z = LEG_H exactly
	y_offset = 0.06  # avatar3d_rig.py's own ARM_Y_OFFSET
	prosthetics = metrics["prosthetics"]

	for side, pivot in ((-1, rig.wrist_l), (1, rig.wrist_r)):
		if pivot is None:
			continue
		side_name = "left" if side == -1 else "right"
		if f"{side_name}_arm" in prosthetics:
			continue
		outer = side * (chest_half_w + limb_half_w * 2)
		inner = side * chest_half_w
		hx0, hx1 = sorted((inner, outer))
		claw_w = (hx1 - hx0) / 4
		vdata, vertex, normal, color, tris = a3d_rig._new_buffers()
		idx = 0
		for i in range(3):
			cx0 = hx0 + claw_w * (i + 0.7)
			idx = a3d._outlined_box(
				vertex, normal, color, tris, idx,
				(cx0, (-0.24 if paws else -0.2) + y_offset, wrist_z - 0.06), (cx0 + claw_w * 0.4, (-0.19 if paws else -0.15) + y_offset, wrist_z - 0.01), claw_c,
			)
		claw = a3d_rig._finish(vdata, tris, f"claws_hand_{'l' if side == -1 else 'r'}")
		claw.reparentTo(pivot)
		claw.setPos(rig.root, 0.0, 0.0, 0.0)

	pants_style = metrics["pants_style"]
	leg_w = {"skinny": limb_half_w * 0.72, "baggy": limb_half_w * 1.45}.get(pants_style, limb_half_w)
	for side, pivot in ((-1, rig.ankle_l), (1, rig.ankle_r)):
		if pivot is None:
			continue
		lx0 = side * hip_half_w * 0.5 - leg_w
		claw_w = (leg_w * 2) / 4
		vdata, vertex, normal, color, tris = a3d_rig._new_buffers()
		idx = 0
		for i in range(3):
			cx0 = lx0 + claw_w * (i + 0.7)
			idx = a3d._outlined_box(
				vertex, normal, color, tris, idx,
				(cx0, -0.29 if paws else -0.23, -0.02), (cx0 + claw_w * 0.4, -0.23 if paws else -0.17, 0.03), claw_c,
			)
		claw = a3d_rig._finish(vdata, tris, f"claws_foot_{'l' if side == -1 else 'r'}")
		claw.reparentTo(pivot)
		claw.setPos(rig.root, 0.0, 0.0, 0.0)
