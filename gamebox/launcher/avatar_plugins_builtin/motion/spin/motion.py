"""CosmOS's own built-in "spin" motion - identical to what used
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
	"""A full 360-degree turntable spin of the whole character, with
	idle's own bob/blink/mouth still running underneath so a slowly-
	spinning showcase shot still looks alive, not like a static model
	on a turntable."""
	rig.root.setH(360 * t)
	phase = 2 * math.pi * t
	rig.root.setZ(0.015 * math.sin(phase * 2))
	_blink(rig, t)
	_mouth_flap(rig, t, toggles_per_loop=2)
