"""CosmOS's own built-in "dance" motion - identical to what used
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
	"""A playful side-to-side dance loop - root sways left/right, hips
	and shoulders swing in a faster, bouncier pattern than walk (not a
	real gait, legs move together rather than alternating), a head bob/
	turn, plus the idle blink and mouth movement so it reads as alive
	and having fun, not just a mechanical loop."""
	phase = 2 * math.pi * t
	rig.root.setX(0.12 * math.sin(phase))
	rig.root.setZ(0.05 * abs(math.sin(2 * phase)))
	sway = 26
	if rig.hip_l is not None:
		rig.hip_l.setP(sway * math.sin(phase))
	if rig.hip_r is not None:
		rig.hip_r.setP(sway * math.sin(phase))
	arm_sway = 34
	if rig.shoulder_l is not None:
		rig.shoulder_l.setP(arm_sway * math.sin(phase + math.pi / 2))
	if rig.shoulder_r is not None:
		rig.shoulder_r.setP(arm_sway * math.sin(phase - math.pi / 2))
	if rig.neck is not None:
		rig.neck.setH(14 * math.sin(phase))
	_blink(rig, t)
	_mouth_flap(rig, t, toggles_per_loop=2)
