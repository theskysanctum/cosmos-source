"""CosmOS's own built-in animal paws feature - identical geometry to
what used to live inline in avatar3d.build_character() (the static
path, both hands and feet in one combined block) and avatar3d._build_hand()/
_build_foot() (the rig path) - a real, direct extraction, not a
rewrite.

`paws` (the boolean) is read from `metrics`, not `traits` - like ears/
horns/halo/muzzle, it stays a real, resolved value inside
build_character()/build_rig() themselves (see avatar3d_rig.build_rig()'s
own docstring: real animal paws have no separate articulated digits, so
build_rig() skips building finger/thumb pivots at all when paws is set)
- only the geometry moved out here. Unlike muzzle, this ISN'T a gate
inside _build_hand()/_build_foot() anymore - both now always draw their
own normal palm/shoe geometry unconditionally, and this plugin's own
paw-pad pokes out over it in every dimension, fully covering it (the
same additive "poke out to override" technique already used everywhere
else in this codebase, including this plugin's own foot-paw below) -
`paws` only needed to survive as a real value for the finger/thumb-
pivot decision, not as a drawing gate.

The static and rig paws were already NOT identical before this
extraction (not introduced here): the static path draws BOTH the hand-
paw and foot-paw shape unconditionally additively (no is_prosthetic
check at all), while the rig's hand-paw is skipped on a prosthetic hand
and the rig's foot-paw has no such check (no prosthetic-leg concept
exists here). Both behaviors are preserved here exactly, just
relocated."""


def _hex_to_rgba(hex_color):
	h = hex_color.lstrip("#")
	r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
	return (r, g, b, 1)


def _paw_color(traits, metrics):
	paw_color = traits.get("paw_color")
	return _hex_to_rgba(paw_color) if paw_color else metrics["skin"]


def build_portrait(vertex, normal, color, tris, idx, box, outlined_box, traits, metrics):
	if not metrics["paws"]:
		return idx
	paw_c = _paw_color(traits, metrics)
	chest_half_w, limb_half_w, hip_half_w, LEG_H = (
		metrics["chest_half_w"], metrics["limb_half_w"], metrics["hip_half_w"], metrics["LEG_H"],
	)
	for side in (-1, 1):
		outer = side * (chest_half_w + limb_half_w * 2)
		inner = side * chest_half_w
		hx0, hx1 = sorted((inner - 0.01, outer + 0.01))
		idx = outlined_box(
			vertex, normal, color, tris, idx,
			(hx0, -0.21, LEG_H - 0.04), (hx1, 0.19, LEG_H + 0.1), paw_c,
		)
		toe_w = (hx1 - hx0) / 4
		for i in range(3):
			tx0 = hx0 + toe_w * (i + 0.6)
			idx = outlined_box(
				vertex, normal, color, tris, idx,
				(tx0, -0.23, LEG_H - 0.07), (tx0 + toe_w * 0.6, -0.17, LEG_H - 0.01), paw_c,
			)
		lx0 = side * hip_half_w * 0.5 - limb_half_w
		fx0, fx1 = lx0 - 0.02, lx0 + limb_half_w * 2 + 0.02
		idx = outlined_box(
			vertex, normal, color, tris, idx,
			(fx0, -0.24, -0.02), (fx1, 0.22, 0.16), paw_c,
		)
		toe_w = (fx1 - fx0) / 4
		for i in range(3):
			tx0 = fx0 + toe_w * (i + 0.6)
			idx = outlined_box(
				vertex, normal, color, tris, idx,
				(tx0, -0.27, -0.03), (tx0 + toe_w * 0.6, -0.21, 0.03), paw_c,
			)
	return idx


def build_animated(rig, traits, metrics):
	if not metrics["paws"]:
		return
	import avatar3d as a3d
	import avatar3d_rig as a3d_rig

	paw_c = _paw_color(traits, metrics)
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
		hx0, hx1 = sorted((inner - 0.01, outer + 0.01))
		vdata, vertex, normal, color, tris = a3d_rig._new_buffers()
		idx = a3d._outlined_box(
			vertex, normal, color, tris, 0,
			(hx0, -0.21 + y_offset, wrist_z - 0.04), (hx1, 0.19 + y_offset, wrist_z + 0.1), paw_c,
		)
		toe_w = (hx1 - hx0) / 4
		for i in range(3):
			tx0 = hx0 + toe_w * (i + 0.6)
			idx = a3d._outlined_box(
				vertex, normal, color, tris, idx,
				(tx0, -0.23 + y_offset, wrist_z - 0.07), (tx0 + toe_w * 0.6, -0.17 + y_offset, wrist_z - 0.01), paw_c,
			)
		paw = a3d_rig._finish(vdata, tris, f"paw_hand_{'l' if side == -1 else 'r'}")
		paw.reparentTo(pivot)
		paw.setPos(rig.root, 0.0, 0.0, 0.0)

	pants_style = metrics["pants_style"]
	leg_w = {"skinny": limb_half_w * 0.72, "baggy": limb_half_w * 1.45}.get(pants_style, limb_half_w)
	for side, pivot in ((-1, rig.ankle_l), (1, rig.ankle_r)):
		if pivot is None:
			continue
		lx0 = side * hip_half_w * 0.5 - leg_w
		fx0, fx1 = lx0 - 0.02, lx0 + leg_w * 2 + 0.02
		vdata, vertex, normal, color, tris = a3d_rig._new_buffers()
		idx = a3d._outlined_box(
			vertex, normal, color, tris, 0,
			(fx0, -0.24, -0.02), (fx1, 0.22, 0.16), paw_c,
		)
		toe_w = (fx1 - fx0) / 4
		for i in range(3):
			tx0 = fx0 + toe_w * (i + 0.6)
			idx = a3d._outlined_box(
				vertex, normal, color, tris, idx,
				(tx0, -0.27, -0.03), (tx0 + toe_w * 0.6, -0.21, 0.03), paw_c,
			)
		paw = a3d_rig._finish(vdata, tris, f"paw_foot_{'l' if side == -1 else 'r'}")
		paw.reparentTo(pivot)
		paw.setPos(rig.root, 0.0, 0.0, 0.0)
