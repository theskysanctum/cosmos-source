"""CosmOS's own built-in "skip" motion - identical to what used
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
	"""A hopping skip - leg swing like walk, but the root bob is tied
	tightly to EACH leg's forward swing (a real hop on every step, not
	just a gentle sway), for a bouncy, playful gait - plus a knee bend/
	ankle counter-rotation and a fixed elbow bend, scaled to skip's own
	swing amplitude."""
	phase = 2 * math.pi * t
	swing = 32
	# Sign convention fixed the same way as walk() - see its own comment.
	hip_l_angle = -swing * math.sin(phase + math.pi)
	hip_r_angle = -swing * math.sin(phase)
	if rig.hip_l is not None:
		rig.hip_l.setP(hip_l_angle)
	if rig.hip_r is not None:
		rig.hip_r.setP(hip_r_angle)
	knee_l_angle = -35 * max(0.0, math.sin(phase + math.pi))
	knee_r_angle = -35 * max(0.0, math.sin(phase))
	if rig.knee_l is not None:
		rig.knee_l.setP(knee_l_angle)
	if rig.knee_r is not None:
		rig.knee_r.setP(knee_r_angle)
	if rig.ankle_l is not None:
		rig.ankle_l.setP(-(hip_l_angle + knee_l_angle) * 0.5)
	if rig.ankle_r is not None:
		rig.ankle_r.setP(-(hip_r_angle + knee_r_angle) * 0.5)
	arm_swing = 26
	if rig.shoulder_l is not None:
		rig.shoulder_l.setP(-arm_swing * math.sin(phase + math.pi))
	if rig.shoulder_r is not None:
		rig.shoulder_r.setP(-arm_swing * math.sin(phase))
	if rig.elbow_l is not None:
		rig.elbow_l.setP(-10)
	if rig.elbow_r is not None:
		rig.elbow_r.setP(-10)
	rig.root.setZ(0.1 * abs(math.sin(2 * phase)) ** 0.6)
	_wheelchair_locomotion(rig, t, speed=1.1, push_amplitude=38)
