"""A rigged, animatable pet companion - this module's own equivalent of
avatar3d_rig.py, at pet scale: a small pivot-based skeleton built from
avatar3d_pet.py's own extracted sub-builders (`_leg_box`, `_build_pet_tail`,
`_build_bird_wing`, ...), the same "real Geoms parented under world-
point-positioned pivot NodePaths" trick avatar3d_rig.py already
established for the person (see `_pivoted_part`/`_new_buffers`/`_finish`,
reused verbatim here by direct import rather than re-derived).

Deliberately simpler than the human rig: each leg is ONE pivot rotating
a single straight box (see avatar3d_pet._leg_box's own docstring on why
a second knee joint has no visible payoff at this scale), the head/ears/
snout are ONE rigid pivot (no separate jaw/eye joints), and the tail is
ONE pivot for the whole segmented tail (no per-segment wagging) - real
animation richness (avatar3d_pet_anim.py) comes from HOW these few
joints move, not from a large joint count. Quadrupeds (cat/dog/rabbit)
and the bird are two real, different skeletons - not one reskinned
shape - matching avatar3d_pet.py's own QUADRUPEDS/BIRD_PARAMS split:
`PetRig.leg_bl`/`leg_br` and the ear-bearing head come back None for a
bird (2 legs, wings instead of front legs, no ear geometry to speak
of), `PetRig.wing_l`/`wing_r` come back None for every quadruped -
callers must check before posing, the same contract avatar3d_rig.Rig's
own docstring already establishes for a missing human limb."""
from dataclasses import dataclass
from typing import Optional

from panda3d.core import NodePath

import avatar3d as a3d
import avatar3d_pet as pet
from avatar3d_rig import _finish, _new_buffers, _pivoted_part


@dataclass
class PetRig:
	"""A posable pet. Any joint is None when this species' own skeleton
	doesn't have it - see this module's own docstring on the quadruped/
	bird split. Always has a `root` and a `torso` (the one static, non-
	posed piece every species shares - the body itself never needs its
	own joint, only what's attached to it does)."""
	root: NodePath
	torso: Optional[NodePath] = None
	leg_fl: Optional[NodePath] = None
	leg_fr: Optional[NodePath] = None
	leg_bl: Optional[NodePath] = None
	leg_br: Optional[NodePath] = None
	neck: Optional[NodePath] = None
	tail: Optional[NodePath] = None
	wing_l: Optional[NodePath] = None
	wing_r: Optional[NodePath] = None


def build_pet_rig(species="none", color="#8b5e3c") -> Optional[PetRig]:
	"""None for species="none" - unlike avatar3d_rig.build_rig() (which
	always returns a real Rig, every joint just None for a missing
	limb), a pet either exists or it doesn't; there's no "partial pet"
	state the way a human can be missing one arm, so there's nothing
	for a caller to reparent/pose when there's no pet at all. Callers
	(avatar3d_viewer.AvatarCustomizePage, avatar3d_previewer.py) must
	check for None the same way they already check avatar3d_rig's own
	optional joints.

	`color` is this pet's one real customizable trait besides species
	(the "species + color only" scope this pass deliberately chose,
	matching avatar3d.build_character()'s own identical pet_color) -
	snout/beak color are derived from it (a shade of it, or a fixed
	beak tone), not independently exposed."""
	if species == "none":
		return None
	color_c = a3d._hex_to_rgba(color)
	snout_c = a3d._shade(color_c, 0.7)
	beak_c = a3d._hex_to_rgba(pet.BEAK_COLOR_DEFAULT)
	root = NodePath("pet_rig_root")
	rig = PetRig(root=root)

	if species == "bird":
		p = pet.BIRD_PARAMS
		shoulder_h = p["perch_h"]
		for side, attr in ((-1, "leg_fl"), (1, "leg_fr")):
			x = side * p["leg_w"] * 1.4
			pivot, _ = _pivoted_part(
				root, attr, (x, 0.0, shoulder_h),
				lambda v, n, c, t, i, x=x: pet._leg_box(v, n, c, t, i, x, 0.0, p["leg_w"], 0.0, shoulder_h, pet.BIRD_LEG_COLOR),
			)
			setattr(rig, attr, pivot)
		tvdata, tvertex, tnormal, tcolor, ttris = _new_buffers()
		tidx = pet._build_pet_torso(tvertex, tnormal, tcolor, ttris, 0, p["body_len"], p["body_w"], shoulder_h, color_c)
		torso_np = _finish(tvdata, ttris, "pet_torso")
		torso_np.reparentTo(root)
		rig.torso = torso_np
		torso_z1 = shoulder_h + p["body_w"] * pet.PET_TORSO_Z1_FRAC
		head_cz = (torso_z1 - p["head_s"] * 0.15) + p["head_s"] / 2
		neck_pivot, _ = _pivoted_part(
			root, "neck", (0.0, -p["body_len"] * 0.5, head_cz),
			lambda v, n, c, t, i: _pet_bird_head_and_beak(v, n, c, t, i, p, shoulder_h, head_cz, color_c, beak_c),
			origin=root,
		)
		rig.neck = neck_pivot
		for side, attr in ((-1, "wing_l"), (1, "wing_r")):
			pivot, _ = _pivoted_part(
				root, attr, (side * p["body_w"] * 0.42, 0.0, shoulder_h + p["body_w"] * 0.5),
				lambda v, n, c, t, i, side=side: pet._build_bird_wing(v, n, c, t, i, side, p["body_len"], p["body_w"], shoulder_h, color_c),
				origin=root,
			)
			setattr(rig, attr, pivot)
		return rig

	shoulder_h, body_len, body_w, head_s, leg_w, ear_style, tail_style, back_leg_scale = pet.QUADRUPED_PARAMS[species]
	front_y, back_y = -body_len * 0.28, body_len * 0.28
	back_leg_w = leg_w * back_leg_scale
	for side, back, attr in ((-1, False, "leg_fl"), (1, False, "leg_fr"), (-1, True, "leg_bl"), (1, True, "leg_br")):
		leg_w_this = back_leg_w if back else leg_w
		leg_x = side * (body_w / 2 - leg_w_this * 0.5)
		leg_y = back_y if back else front_y
		pivot, _ = _pivoted_part(
			root, attr, (leg_x, leg_y, shoulder_h),
			lambda v, n, c, t, i, leg_x=leg_x, leg_y=leg_y, leg_w_this=leg_w_this: _pet_leg_with_paw(v, n, c, t, i, leg_x, leg_y, leg_w_this, shoulder_h, color_c),
		)
		setattr(rig, attr, pivot)

	tvdata, tvertex, tnormal, tcolor, ttris = _new_buffers()
	tidx = pet._build_pet_torso(tvertex, tnormal, tcolor, ttris, 0, body_len, body_w, shoulder_h, color_c)
	torso_np = _finish(tvdata, ttris, "pet_torso")
	torso_np.reparentTo(root)
	rig.torso = torso_np

	torso_z1 = shoulder_h + body_w * pet.PET_TORSO_Z1_FRAC
	head_bottom = torso_z1 - head_s * 0.15
	head_cz = head_bottom + head_s / 2
	neck_pivot, _ = _pivoted_part(
		root, "neck", (0.0, -body_len * 0.5, head_cz),
		lambda v, n, c, t, i: pet._build_pet_head(v, n, c, t, i, ear_style, head_s, body_len, body_w, shoulder_h, color_c, snout_c),
		origin=root,
	)
	rig.neck = neck_pivot

	tail_z0 = shoulder_h + body_w * 0.55
	tail_pivot, _ = _pivoted_part(
		root, "tail", (0.0, body_len * 0.42, tail_z0),
		lambda v, n, c, t, i: pet._build_pet_tail(v, n, c, t, i, tail_style, body_len, body_w, shoulder_h, color_c),
		origin=root,
	)
	rig.tail = tail_pivot
	return rig


def _pet_leg_with_paw(vertex, normal, color, tris, idx, x, y, leg_w, shoulder_h, color_c):
	idx = pet._leg_box(vertex, normal, color, tris, idx, x, y, leg_w, 0.0, shoulder_h, color_c)
	idx = pet._paw_box(vertex, normal, color, tris, idx, x, y, leg_w, color_c)
	return idx


def _pet_bird_head_and_beak(vertex, normal, color, tris, idx, p, shoulder_h, head_cz, color_c, beak_c):
	idx = pet._build_pet_head(vertex, normal, color, tris, idx, "none", p["head_s"], p["body_len"], p["body_w"], shoulder_h, color_c, color_c)
	idx = pet._build_bird_beak(vertex, normal, color, tris, idx, p["head_s"], p["body_len"], head_cz, beak_c)
	return idx
