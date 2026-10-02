"""CosmOS's own built-in socks accessory - identical geometry to what
used to live inline in avatar3d.build_character() (the static path) and
avatar3d._build_leg_shin() (the rig path) - a real, direct extraction,
not a rewrite. Reads the same trait keys the old inline version did
("socks" membership in the shared "accessories" list, "socks_color" as
a hex string or None).

The static and rig paths were already NOT identical before this
extraction (a real, pre-existing divergence, not introduced here): the
static path draws a fixed-height band (z=0.25 to 0.4, independent of
pants_style), while the rig path anchors to the real ankle_z and widens
with pants_style's own "skinny"/"baggy" leg_w modifier (matching
whatever width the shin segment itself used). Both behaviors are
preserved here exactly, just relocated - the rig version reparents onto
rig.knee_l/r, the same pivot the shin's own geometry hangs off of."""


def _hex_to_rgba(hex_color):
	h = hex_color.lstrip("#")
	r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
	return (r, g, b, 1)


def _socks_color(traits):
	c = traits.get("socks_color")
	return _hex_to_rgba(c) if c else (0.96, 0.97, 1.0, 1)


def build_portrait(vertex, normal, color, tris, idx, box, outlined_box, traits, metrics):
	if "socks" not in metrics["accessories"]:
		return idx
	sc = _socks_color(traits)
	limb_half_w, hip_half_w = metrics["limb_half_w"], metrics["hip_half_w"]
	for side in (-1, 1):
		lx0 = side * hip_half_w * 0.5 - limb_half_w
		idx = outlined_box(
			vertex, normal, color, tris, idx,
			(lx0, -0.16, 0.25), (lx0 + limb_half_w * 2, 0.16, 0.4), sc,
		)
	return idx


def build_animated(rig, traits, metrics):
	if "socks" not in metrics["accessories"]:
		return
	import avatar3d as a3d
	import avatar3d_rig as a3d_rig

	sc = _socks_color(traits)
	limb_half_w, hip_half_w = metrics["limb_half_w"], metrics["hip_half_w"]
	pants_style = metrics["pants_style"]
	leg_w = {"skinny": limb_half_w * 0.72, "baggy": limb_half_w * 1.45}.get(pants_style, limb_half_w)
	ankle_z = metrics["LEG_H"] * (0.25 / 1.15)  # avatar3d_rig.py's own ANKLE_Z formula
	for side, pivot in ((-1, rig.knee_l), (1, rig.knee_r)):
		if pivot is None:
			continue
		lx0 = side * hip_half_w * 0.5 - leg_w
		vdata, vertex, normal, color, tris = a3d_rig._new_buffers()
		idx = a3d._outlined_box(
			vertex, normal, color, tris, 0,
			(lx0, -0.16, ankle_z), (lx0 + leg_w * 2, 0.16, ankle_z + 0.15), sc,
		)
		sock = a3d_rig._finish(vdata, tris, f"socks_{'l' if side == -1 else 'r'}")
		sock.reparentTo(pivot)
		sock.setPos(rig.root, 0.0, 0.0, 0.0)
