"""CosmOS's own built-in "cheer" motion - identical to what used
to live in avatar3d_anim.py's own MOTIONS dict (a real, direct
extraction via inspect.getsource() against the live function, not
a manual retype - guarantees byte-for-byte identical behavior).
Imports its own shared helpers directly from avatar3d_anim rather
than duplicating them - the same "no duplication, reuse the real
thing" reasoning avatar_plugins_builtin/motion/wave/ already
established."""
import math

from avatar3d_anim import _blink


def pose(rig, t):
	"""Both arms pump overhead together with a bouncy jump, and the
	mouth opens right at the peak (a cheer/"yay") - a big, celebratory,
	fully symmetric motion (unlike wave's one-sided version)."""
	phase = 2 * math.pi * t
	lift = 0.5 - 0.5 * math.cos(phase)  # eases 0->1->0, peak at t=0.5
	arm_angle = -150 * lift  # sweeps from hanging down toward straight up
	if rig.shoulder_l is not None:
		rig.shoulder_l.setP(arm_angle)
	if rig.shoulder_r is not None:
		rig.shoulder_r.setP(arm_angle)
	rig.root.setZ(0.12 * lift)
	if rig.mouth_open is not None:
		if lift > 0.6:
			rig.mouth_open.show()
		else:
			rig.mouth_open.hide()
	_blink(rig, t, center=0.15)
