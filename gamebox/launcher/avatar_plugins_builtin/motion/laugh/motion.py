"""CosmOS's own built-in "laugh" motion - identical to what used
to live in avatar3d_anim.py's own MOTIONS dict (a real, direct
extraction via inspect.getsource() against the live function, not
a manual retype - guarantees byte-for-byte identical behavior).
Imports its own shared helpers directly from avatar3d_anim rather
than duplicating them - the same "no duplication, reuse the real
thing" reasoning avatar_plugins_builtin/motion/wave/ already
established."""
import math

from avatar3d_anim import _mouth_flap, _blink


def pose(rig, t):
	"""Rapid mouth flutter and a quick, light bounce - a laughing loop.
	Faster and shallower than `cheer`'s big single pump."""
	phase = 2 * math.pi * t
	rig.root.setZ(0.03 * abs(math.sin(phase * 3)))
	if rig.neck is not None:
		rig.neck.setP(-6 * abs(math.sin(phase * 3)))
	_mouth_flap(rig, t, toggles_per_loop=6, duty=0.5)
	_blink(rig, t, center=0.5, width=0.03)
