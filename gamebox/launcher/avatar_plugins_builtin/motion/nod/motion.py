"""CosmOS's own built-in "nod" motion - identical to what used
to live in avatar3d_anim.py's own MOTIONS dict (a real, direct
extraction via inspect.getsource() against the live function, not
a manual retype - guarantees byte-for-byte identical behavior).
Imports its own shared helpers directly from avatar3d_anim rather
than duplicating them - the same "no duplication, reuse the real
thing" reasoning avatar_plugins_builtin/motion/wave/ already
established."""
import math

from avatar3d_anim import _blink, _mouth_flap


def pose(rig, t):
	"""A "yes" head nod - neck pitches down and back up - with the
	usual idle bob/blink/mouth layered underneath."""
	phase = 2 * math.pi * t
	if rig.neck is not None:
		rig.neck.setP(18 * math.sin(phase))
	rig.root.setZ(0.012 * math.sin(phase))
	_blink(rig, t, center=0.75)
	_mouth_flap(rig, t, toggles_per_loop=2)
