"""CosmOS's own built-in "bow" motion - identical to what used
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
	"""A polite bow - waist (not root, which the legs are also attached
	to - pitching that would tip the whole body forward like a falling
	plank instead of bending at the hips) pitches forward and eases back
	upright, with the head dipping a touch further than the torso.
	Deliberately shallow angles (20 degrees at the waist, an extra 8 at
	the neck - nested rotations compose, so the head's own real total
	pitch peaks at 28, not something steeper) and an ease-down/hold/
	ease-up shape rather than a plain sinusoid, so it reads as one
	deliberate, dignified bow per loop instead of rocking/overbalancing
	like a bird bobbing its head - keeping this the same respectful,
	non-cartoonish register every culturally-loaded gesture here should
	stay in."""
	if t < 0.35:
		lean = 0.5 - 0.5 * math.cos(math.pi * (t / 0.35))
	elif t < 0.65:
		lean = 1.0
	else:
		lean = 0.5 - 0.5 * math.cos(math.pi * (1 - (t - 0.65) / 0.35))
	rig.waist.setP(20 * lean)
	if rig.neck is not None:
		rig.neck.setP(8 * lean)
	_blink(rig, t)
