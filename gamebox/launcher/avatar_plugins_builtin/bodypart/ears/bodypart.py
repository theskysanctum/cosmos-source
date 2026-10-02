"""CosmOS's own built-in furry/fantasy ears feature - identical
geometry to what used to live inline in avatar3d._build_hair_and_toppers()
(a real, direct extraction, not a rewrite). The single largest bodypart
extraction in this project - 15+ species across 6 real construction
branches (upright base+tip EAR_PARAMS species, floppy FLOPPY_EARS
species, human, elf, lizard's dorsal frill, alien's antenna-style ears).

ears/ear_scale/ear_thickness/ear_depth are read from `metrics`, not
`traits` - like horns/halo, ears stays a real, core, named parameter of
build_character()/build_rig() themselves (see avatar3d.py's own
hairstyle-override check: ears/horns/halo silently force hairstyle down
to a low-volume style since voluminous hair otherwise fights visually
with them) - only the actual geometry moved out here, not the trait
itself.

Uses avatar3d.hair_top_z_for_style() (a real, public module function)
for the exact same "how high does this hairstyle's own silhouette
reach" number horns/antennae also need - see that function's own
docstring for why it exists as a real, shared, importable function
rather than a second private copy of the same table."""
import avatar3d as a3d


def _build_ears_geometry(box, outlined_box, vertex, normal, color, tris, idx, ears, animal, skin, head_z0, HEAD_S, hs, hair_top_z, ear_scale, ear_thickness, ear_depth):
	if ears == "none":
		return idx
	ear_base_z = head_z0 + HEAD_S - 0.02 * hs
	ear_top_z = max(ear_base_z, hair_top_z - 0.02 * hs)
	# Upright, base+tip-tapered styles (spread, width, height) - the
	# vast majority of species share this one construction, only
	# varying proportions.
	EAR_PARAMS = {
		"cat": (0.15, 0.17, 0.34),
		"fox": (0.16, 0.19, 0.42),
		"wolf": (0.18, 0.19, 0.36),
		"bunny": (0.12, 0.13, 0.58),
		"bear": (0.17, 0.20, 0.22),
		"deer": (0.15, 0.17, 0.33),
		"horse": (0.15, 0.14, 0.36),
		"mouse": (0.17, 0.28, 0.50),
		"raccoon": (0.18, 0.18, 0.26),
		"bat": (0.20, 0.26, 0.48),
		"dragon": (0.13, 0.14, 0.30),
	}
	FLOPPY_EARS = {
		"dog": (0.12, 0.34),
		"pig": (0.14, 0.20),
		"goat": (0.11, 0.48),
	}
	if ears in FLOPPY_EARS:
		# Floppy - a single box hanging DOWN the side of the head,
		# rather than standing upright.
		ear_w, ear_len = (v * hs for v in FLOPPY_EARS[ears])
		ear_w *= ear_scale * ear_thickness
		ear_len *= ear_scale
		floppy_near_y = -HEAD_S / 2 - 0.005 * hs
		floppy_far_y = floppy_near_y + 0.145 * hs * ear_depth
		floppy_attach_z = head_z0 + HEAD_S * 0.68
		for side in (-1, 1):
			ex_e = side * (HEAD_S / 2 + ear_w * 0.5)
			idx = outlined_box(
				vertex, normal, color, tris, idx,
				(ex_e - ear_w, floppy_near_y, floppy_attach_z - ear_len), (ex_e + ear_w, floppy_far_y, floppy_attach_z + 0.03 * hs), animal,
			)
	elif ears == "human":
		# Small, rounded, SIDE-mounted at ear height, skin-colored.
		human_h_z = head_z0 + HEAD_S * 0.56
		human_reach = 0.07 * hs * ear_scale
		human_half_z = 0.085 * hs * ear_thickness
		human_half_y = 0.045 * hs * ear_depth
		human_color = skin if skin is not None else animal
		for side in (-1, 1):
			hx0, hx1 = sorted((side * (HEAD_S / 2 - 0.02 * hs), side * (HEAD_S / 2 + human_reach)))
			idx = outlined_box(
				vertex, normal, color, tris, idx,
				(hx0, -0.02 * hs - human_half_y, human_h_z - human_half_z), (hx1, -0.02 * hs + human_half_y, human_h_z + human_half_z), human_color,
			)
	elif ears == "elf":
		# Pointed and SIDE-mounted at ear height, skin-colored (a real
		# human ear, not fur).
		ear_h_z = head_z0 + HEAD_S * 0.56
		base_out = 0.09 * hs * ear_scale
		tip_out = 0.13 * hs * ear_scale
		base_half_z = 0.08 * hs * ear_thickness
		tip_half_z = 0.08 * hs * ear_thickness
		base_half_y = 0.05 * hs * ear_depth
		tip_half_y = 0.0125 * hs * ear_depth
		elf_color = skin if skin is not None else animal
		for side in (-1, 1):
			base_x0, base_x1 = sorted((side * (HEAD_S / 2 - 0.03 * hs), side * (HEAD_S / 2 + base_out)))
			idx = outlined_box(
				vertex, normal, color, tris, idx,
				(base_x0, -0.02 * hs - base_half_y, ear_h_z - base_half_z), (base_x1, -0.02 * hs + base_half_y, ear_h_z + base_half_z), elf_color,
			)
			tip_x0, tip_x1 = sorted((side * (HEAD_S / 2 + base_out), side * (HEAD_S / 2 + base_out + tip_out)))
			idx = outlined_box(
				vertex, normal, color, tris, idx,
				(tip_x0, -0.0275 * hs - tip_half_y, ear_h_z + 0.05 * hs - tip_half_z), (tip_x1, -0.0275 * hs + tip_half_y, ear_h_z + 0.05 * hs + tip_half_z), elf_color,
			)
	elif ears == "lizard":
		# A dorsal frill down the center-back of the head instead of
		# paired side ears (reptiles don't have external ears).
		frill_x_half = 0.03 * hs * ear_thickness
		frill_y_half = 0.05 * hs * ear_depth
		for i, y_c in enumerate((-0.1 * hs, 0.05 * hs, 0.2 * hs)):
			h = (0.16 - i * 0.03) * hs * ear_scale
			idx = outlined_box(
				vertex, normal, color, tris, idx,
				(-frill_x_half, y_c - frill_y_half, ear_top_z), (frill_x_half, y_c + frill_y_half, ear_top_z + h), animal,
			)
	elif ears == "alien":
		# Two thin stalks with ball tips going straight up.
		ant_len = 0.4 * hs * ear_scale
		ant_top = ear_top_z + ant_len
		ball_h = 0.07 * hs * ear_scale
		stalk_half = 0.02 * hs * ear_thickness
		stalk_half_y = 0.02 * hs * ear_depth
		ball_half = 0.045 * hs * ear_thickness
		ball_half_y = 0.045 * hs * ear_depth
		for side in (-1, 1):
			ex_e = side * 0.1 * hs
			idx = outlined_box(
				vertex, normal, color, tris, idx,
				(ex_e - stalk_half, -stalk_half_y, ear_top_z), (ex_e + stalk_half, stalk_half_y, ant_top), animal,
			)
			idx = outlined_box(
				vertex, normal, color, tris, idx,
				(ex_e - ball_half, -ball_half_y, ant_top), (ex_e + ball_half, ball_half_y, ant_top + ball_h), animal,
			)
	else:
		spread, width, height = (v * hs for v in EAR_PARAMS[ears])
		spread *= ear_scale
		width *= ear_scale * ear_thickness
		height *= ear_scale
		base_h = height * 0.68
		depth_half = 0.06 * hs * ear_depth
		tip_depth_half = 0.04 * hs * ear_depth
		for side in (-1, 1):
			ex_e = side * spread
			idx = outlined_box(
				vertex, normal, color, tris, idx,
				(ex_e - width / 2, -depth_half, ear_top_z), (ex_e + width / 2, depth_half, ear_top_z + base_h), animal,
			)
			tip_w = width * 0.55
			idx = outlined_box(
				vertex, normal, color, tris, idx,
				(ex_e - tip_w / 2, -tip_depth_half, ear_top_z + base_h), (ex_e + tip_w / 2, tip_depth_half, ear_top_z + height), animal,
			)
			idx = box(
				vertex, normal, color, tris, idx,
				(ex_e - width * 0.28, -depth_half - 0.01 * hs, ear_top_z + 0.015 * hs), (ex_e + width * 0.28, -depth_half, ear_top_z + base_h * 0.85), a3d.INNER_EAR_COLOR,
			)
	return idx


def _ears_common(metrics):
	head_z0 = metrics["LEG_H"] + metrics["TORSO_H"]
	HEAD_S = metrics["HEAD_S"]
	hs = HEAD_S / 0.85
	hair_top_z = a3d.hair_top_z_for_style(metrics["hairstyle"], head_z0, HEAD_S, hs)
	return head_z0, HEAD_S, hs, hair_top_z


def build_portrait(vertex, normal, color, tris, idx, box, outlined_box, traits, metrics):
	ears = metrics["ears"]
	if ears == "none":
		return idx
	head_z0, HEAD_S, hs, hair_top_z = _ears_common(metrics)
	return _build_ears_geometry(
		box, outlined_box, vertex, normal, color, tris, idx,
		ears, metrics["animal"], metrics["skin"], head_z0, HEAD_S, hs, hair_top_z,
		metrics["ear_scale"], metrics["ear_thickness"], metrics["ear_depth"],
	)


def build_animated(rig, traits, metrics):
	ears = metrics["ears"]
	if ears == "none":
		return
	if rig.neck is None:
		return
	import avatar3d_rig as a3d_rig

	head_z0, HEAD_S, hs, hair_top_z = _ears_common(metrics)
	vdata, vertex, normal, color, tris = a3d_rig._new_buffers()
	idx = _build_ears_geometry(
		a3d._box, a3d._outlined_box, vertex, normal, color, tris, 0,
		ears, metrics["animal"], metrics["skin"], head_z0, HEAD_S, hs, hair_top_z,
		metrics["ear_scale"], metrics["ear_thickness"], metrics["ear_depth"],
	)
	ears_np = a3d_rig._finish(vdata, tris, "ears")
	ears_np.reparentTo(rig.neck)
	ears_np.setPos(rig.root, 0.0, 0.0, 0.0)
