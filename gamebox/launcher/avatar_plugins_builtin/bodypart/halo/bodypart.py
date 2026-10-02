"""CosmOS's own built-in halo feature - identical geometry to what used
to live inline in avatar3d._build_hair_and_toppers() (a real, direct
extraction, not a rewrite).

`halo` is read from `metrics`, not `traits` - like horns, it stays a
real, core, named parameter of build_character()/build_rig() themselves
(see avatar3d.py's own hairstyle-override check) - only the geometry
moved out here. Doesn't need avatar3d.hair_top_z_for_style() at all -
halo_z is a fixed head-relative offset regardless of hairstyle (the
override check keeps hairstyle low-volume whenever halo is active
anyway, so it never needed to track hair height the way ears/horns/
antennae do)."""

HALO_COLOR = (1.0, 0.92, 0.55, 1)


def _build_halo_geometry(box, vertex, normal, color, tris, idx, halo_z, hs):
	idx = box(vertex, normal, color, tris, idx, (-0.16 * hs, -0.03 * hs, halo_z), (0.16 * hs, -0.02 * hs, halo_z + 0.03 * hs), HALO_COLOR)
	idx = box(vertex, normal, color, tris, idx, (-0.16 * hs, 0.02 * hs, halo_z), (0.16 * hs, 0.03 * hs, halo_z + 0.03 * hs), HALO_COLOR)
	idx = box(vertex, normal, color, tris, idx, (-0.17 * hs, -0.03 * hs, halo_z), (-0.15 * hs, 0.03 * hs, halo_z + 0.03 * hs), HALO_COLOR)
	idx = box(vertex, normal, color, tris, idx, (0.15 * hs, -0.03 * hs, halo_z), (0.17 * hs, 0.03 * hs, halo_z + 0.03 * hs), HALO_COLOR)
	return idx


def build_portrait(vertex, normal, color, tris, idx, box, outlined_box, traits, metrics):
	if not metrics["halo"]:
		return idx
	HEAD_S = metrics["HEAD_S"]
	hs = HEAD_S / 0.85
	halo_z = metrics["LEG_H"] + metrics["TORSO_H"] + HEAD_S + 0.25 * hs
	return _build_halo_geometry(outlined_box, vertex, normal, color, tris, idx, halo_z, hs)


def build_animated(rig, traits, metrics):
	if not metrics["halo"]:
		return
	if rig.neck is None:
		return
	import avatar3d as a3d
	import avatar3d_rig as a3d_rig

	HEAD_S = metrics["HEAD_S"]
	hs = HEAD_S / 0.85
	halo_z = metrics["LEG_H"] + metrics["TORSO_H"] + HEAD_S + 0.25 * hs
	vdata, vertex, normal, color, tris = a3d_rig._new_buffers()
	idx = _build_halo_geometry(a3d._outlined_box, vertex, normal, color, tris, 0, halo_z, hs)
	halo_np = a3d_rig._finish(vdata, tris, "halo")
	halo_np.reparentTo(rig.neck)
	halo_np.setPos(rig.root, 0.0, 0.0, 0.0)
