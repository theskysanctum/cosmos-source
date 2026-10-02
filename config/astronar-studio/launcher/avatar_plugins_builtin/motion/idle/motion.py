"""CosmOS's own built-in "idle" motion - identical to what used
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
	"""A gentler/slower root bob (breathing) than walk's, a periodic
	blink, and a slow head drift - half the frequency of the bob/loop (a
	real, direct correction: the head turn used to complete a full
	side-to-side swivel every 2s loop, reported as "a bit fast" -
	halving the frequency keeps it exactly periodic (sin(phase/2) is 0
	at both t=0 and t->1) while reading as a calm drift rather than a
	twitchy swivel). Deliberately no mouth movement - idle is a resting
	pose, not talking, and a flapping mouth read as wrong there (a
	direct correction after _mouth_flap was found called here too)."""
	phase = 2 * math.pi * t
	rig.root.setZ(0.015 * math.sin(phase))
	if rig.neck is not None:
		rig.neck.setH(5 * math.sin(phase / 2))
	_blink(rig, t)
