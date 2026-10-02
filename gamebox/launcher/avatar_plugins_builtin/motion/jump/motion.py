"""CosmOS's own built-in "jump" motion - identical to what used
to live in avatar3d_anim.py's own MOTIONS dict (a real, direct
extraction via inspect.getsource() against the live function, not
a manual retype - guarantees byte-for-byte identical behavior).
Imports its own shared helpers directly from avatar3d_anim rather
than duplicating them - the same "no duplication, reuse the real
thing" reasoning avatar_plugins_builtin/motion/wave/ already
established."""
import math

from avatar3d_anim import _wheelchair_locomotion


def pose(rig, t):
	"""A single periodic hop in place - root arcs up and back down
	(a real parabola-ish arc via a squared-sine, hangs at the top rather
	than a symmetric sine's slower ease), knees tuck slightly (both hips
	pitch the same direction, unlike walk's opposite-phase swing), and
	arms swing up together for momentum."""
	phase = 2 * math.pi * t
	air = max(0.0, math.sin(phase)) ** 0.6  # eased arc, hangs near the peak
	rig.root.setZ(0.22 * air)
	tuck = 35 * max(0.0, math.sin(phase))
	if rig.hip_l is not None:
		rig.hip_l.setP(tuck)
	if rig.hip_r is not None:
		rig.hip_r.setP(tuck)
	arm_raise = -60 * max(0.0, math.sin(phase))
	if rig.shoulder_l is not None:
		rig.shoulder_l.setP(arm_raise)
	if rig.shoulder_r is not None:
		rig.shoulder_r.setP(arm_raise)
	# Wheelchair equivalent of a jump: a real "wheelie hop" - the whole
	# chair still arcs up via the same root.setZ above, but the arms do
	# a sharp pull-back on the rims (popping the wheelie) instead of
	# raising overhead, and the wheels get a quick lurch of rotation.
	_wheelchair_locomotion(rig, t, speed=1.0, push_amplitude=50)
