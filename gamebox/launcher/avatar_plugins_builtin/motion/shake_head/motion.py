"""CosmOS's own built-in "shake_head" motion - identical to what used
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
	"""A "no" head shake - neck turns left/right - with the usual idle
	bob/blink layered underneath."""
	phase = 2 * math.pi * t
	if rig.neck is not None:
		rig.neck.setH(24 * math.sin(phase))
	rig.root.setZ(0.012 * math.sin(phase))
	_blink(rig, t, center=0.75)
