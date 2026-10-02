"""CosmOS's own built-in "walk" motion - identical to what used
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
	"""Sinusoidal hip swing, opposite-phase left/right, with a
	contralateral arm swing (the right arm swings with the left leg's
	phase, matching a natural human gait), a small root bob standing in
	for a torso bob, a knee bend on each leg's own forward swing (never
	bending backward), a partial ankle counter-rotation so the foot
	doesn't swing wildly out of alignment with the ground, and a fixed
	slight elbow bend for a more natural resting arm shape than a
	perfectly straight one."""
	phase = 2 * math.pi * t
	swing = 28  # degrees
	# Negated throughout this function (hip/knee/shoulder alike) - a
	# real, confirmed-live sign bug: positive setP() on this rig's
	# hip/shoulder pivots actually swings BACKWARD, not forward (checked
	# directly by querying world position, not assumed) - invisible here
	# since a symmetric sine swing looks like walking either way, but it
	# threw off the knee's own "bend only on the forward swing" timing
	# and, combined with the shoulder sharing the identical inversion,
	# would have flipped the contralateral arm/leg pairing the instant
	# just one side of that pairing got corrected (see march's own much
	# more visibly broken high-step for where this was actually caught).
	hip_l_angle = -swing * math.sin(phase + math.pi)
	hip_r_angle = -swing * math.sin(phase)
	if rig.hip_l is not None:
		rig.hip_l.setP(hip_l_angle)
	if rig.hip_r is not None:
		rig.hip_r.setP(hip_r_angle)
	knee_l_angle = -30 * max(0.0, math.sin(phase + math.pi))
	knee_r_angle = -30 * max(0.0, math.sin(phase))
	if rig.knee_l is not None:
		rig.knee_l.setP(knee_l_angle)
	if rig.knee_r is not None:
		rig.knee_r.setP(knee_r_angle)
	if rig.ankle_l is not None:
		rig.ankle_l.setP(-(hip_l_angle + knee_l_angle) * 0.5)
	if rig.ankle_r is not None:
		rig.ankle_r.setP(-(hip_r_angle + knee_r_angle) * 0.5)
	arm_swing = 22
	if rig.shoulder_l is not None:
		rig.shoulder_l.setP(-arm_swing * math.sin(phase))
	if rig.shoulder_r is not None:
		rig.shoulder_r.setP(-arm_swing * math.sin(phase + math.pi))
	if rig.elbow_l is not None:
		rig.elbow_l.setP(-10)
	if rig.elbow_r is not None:
		rig.elbow_r.setP(-10)
	rig.root.setZ(0.035 * abs(math.sin(phase)))
	_wheelchair_locomotion(rig, t, speed=1.0)
