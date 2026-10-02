"""CosmOS's own built-in "flex" motion - identical to what used
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
	"""A held double-bicep-flex pose (both arms raised and slightly
	spread) with a small triumphant shake and the usual idle blink."""
	phase = 2 * math.pi * t
	base_angle = -115
	shake = 4 * math.sin(phase * 8)
	if rig.shoulder_l is not None:
		rig.shoulder_l.setP(base_angle + shake)
		rig.shoulder_l.setH(-18)
	if rig.shoulder_r is not None:
		rig.shoulder_r.setP(base_angle - shake)
		rig.shoulder_r.setH(18)
	rig.root.setZ(0.01 * math.sin(phase))
	_blink(rig, t)
