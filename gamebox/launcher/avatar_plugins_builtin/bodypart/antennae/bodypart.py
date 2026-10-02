"""CosmOS's own built-in antennae feature - identical geometry to what
used to live inline in avatar3d._build_hair_and_toppers() (a real,
direct extraction, not a rewrite).

Unlike horns/halo, antennae never participated in the hairstyle-
override check inside build_character()/build_rig() (see those two
functions' own comment on it - only ears/horns/halo trigger it), so it
was safe to remove from their named parameter lists entirely - reads
"antennae" straight off `traits` (**extra_traits), same as any other
simple bolt-on accessory. Still needs `metrics["hairstyle"]` (the
FINAL, possibly-overridden value - see avatar3d.hair_top_z_for_style()'s
own docstring) though, since antennae positions itself relative to
wherever hair actually ended up, exactly like ears/horns."""
import avatar3d as a3d


def _build_antennae_geometry(box, vertex, normal, color, tris, idx, ant_base, hs, animal):
	ant_top = ant_base + 0.35 * hs
	for side in (-1, 1):
		ex_a = side * 0.08 * hs
		idx = box(
			vertex, normal, color, tris, idx,
			(ex_a - 0.015 * hs, -0.02 * hs, ant_base), (ex_a + 0.015 * hs, 0.02 * hs, ant_top), animal,
		)
		idx = box(
			vertex, normal, color, tris, idx,
			(ex_a - 0.035 * hs, -0.035 * hs, ant_top), (ex_a + 0.035 * hs, 0.035 * hs, ant_top + 0.05 * hs), animal,
		)
	return idx


def _antennae_base_z(metrics):
	head_z0 = metrics["LEG_H"] + metrics["TORSO_H"]
	HEAD_S = metrics["HEAD_S"]
	hs = HEAD_S / 0.85
	top = a3d.hair_top_z_for_style(metrics["hairstyle"], head_z0, HEAD_S, hs)
	return max(head_z0 + HEAD_S - 0.02 * hs, top - 0.02 * hs), hs


def build_portrait(vertex, normal, color, tris, idx, box, outlined_box, traits, metrics):
	if not traits.get("antennae", False):
		return idx
	ant_base, hs = _antennae_base_z(metrics)
	return _build_antennae_geometry(outlined_box, vertex, normal, color, tris, idx, ant_base, hs, metrics["animal"])


def build_animated(rig, traits, metrics):
	if not traits.get("antennae", False):
		return
	if rig.neck is None:
		return
	import avatar3d_rig as a3d_rig

	ant_base, hs = _antennae_base_z(metrics)
	vdata, vertex, normal, color, tris = a3d_rig._new_buffers()
	idx = _build_antennae_geometry(a3d._outlined_box, vertex, normal, color, tris, 0, ant_base, hs, metrics["animal"])
	antennae_np = a3d_rig._finish(vdata, tris, "antennae")
	antennae_np.reparentTo(rig.neck)
	antennae_np.setPos(rig.root, 0.0, 0.0, 0.0)
