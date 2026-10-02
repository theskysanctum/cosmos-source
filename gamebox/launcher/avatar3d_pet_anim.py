"""Motion functions for a avatar3d_pet_rig.PetRig - this module's own
tiny equivalent of avatar3d_anim.py: a `play(rig, name, t)` entry point
that resets every joint to a neutral rest pose before applying the
named motion, exactly the same "absolute pose each frame, reset first"
contract avatar3d_anim.play()'s own docstring establishes (and for the
identical reason - a motion function here poses ABSOLUTE angles, not
deltas, so without a reset-first step a joint an earlier motion touched
could stay stuck once that motion stops playing).

Only "idle" exists for now (the one motion this pass's own scope calls
for - a pet standing/sitting beside the person with its own small,
independent-of-the-person life to it: weight shifting between legs, a
tail wag, a head bob/look-around, or - for a bird - a wing ruffle
instead of leg weight-shift). More motions (a walk cycle for a pet that
actually moves) are a real, natural follow-up, not built here."""
import math


def _rest_pose(rig):
	"""Resets every joint a motion function might pose back to neutral -
	see avatar3d_anim._rest_pose's own docstring for why this runs
	before every motion, not just once at rig-build time."""
	for joint in (rig.leg_fl, rig.leg_fr, rig.leg_bl, rig.leg_br, rig.neck, rig.tail, rig.wing_l, rig.wing_r):
		if joint is not None:
			joint.setP(0)
			joint.setH(0)
	rig.root.setZ(0)


def idle(rig, t):
	"""A slow root bob (breathing) plus, per species shape:
	quadrupeds shift weight between diagonal leg pairs (front-left/
	back-right vs front-right/back-left - a real, subtle standing-still
	weight shift, not a walk cycle) and wag the tail at its own,
	faster, independent frequency (a tail wags quicker than a body
	breathes); a bird instead gives its wings a small periodic ruffle
	(no tail/4-leg weight-shift to speak of - see PetRig's own
	docstring on why those come back None for it) and both body plans
	share the same slow head bob/look, half the root bob's own
	frequency (same "half-frequency stays exactly periodic across the
	loop" reasoning the built-in "idle" motion's own docstring gives
	for the person - avatar_plugins_builtin/motion/idle/motion.py)."""
	phase = 2 * math.pi * t
	rig.root.setZ(0.012 * math.sin(phase))
	if rig.neck is not None:
		rig.neck.setH(10 * math.sin(phase / 2))
	if rig.tail is not None:
		rig.tail.setH(16 * math.sin(phase * 1.5))
	shift = 3.5 * math.sin(phase)
	if rig.leg_fl is not None:
		rig.leg_fl.setP(shift)
	if rig.leg_br is not None:
		rig.leg_br.setP(shift)
	if rig.leg_fr is not None:
		rig.leg_fr.setP(-shift)
	if rig.leg_bl is not None:
		rig.leg_bl.setP(-shift)
	if rig.wing_l is not None:
		rig.wing_l.setP(4 * math.sin(phase * 2))
	if rig.wing_r is not None:
		rig.wing_r.setP(4 * math.sin(phase * 2))


MOTIONS = {"idle": idle}


def play(rig, name, t):
	"""The one real entry point a driver should call - see this module's
	own docstring on why (matches avatar3d_anim.play()'s identical
	contract)."""
	_rest_pose(rig)
	MOTIONS[name](rig, t)
