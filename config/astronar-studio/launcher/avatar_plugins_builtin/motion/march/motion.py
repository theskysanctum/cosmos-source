"""CosmOS's own built-in "march" motion - identical to what used
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
	"""An exaggerated high-step march - much bigger, slower leg lift
	than walk, a matching forward-only knee bend (same shape as the hip
	lift, not opposite-phase like walk's), arms swinging in time, a
	fixed elbow bend, and a stomp-timed root bob (a quick drop right as
	each foot would land)."""
	phase = 2 * math.pi * t
	lift = 55
	# Sign convention fixed the same way as walk() - see its own comment.
	# march is where this was actually caught: with the OLD, unflipped
	# sign, max(0.0, sin(...)) clamps out the half of the cycle that
	# would have been the (mislabeled) "forward" lift, leaving only the
	# actual-backward half visible - a real, confirmed-live "high-step
	# marching backward" bug, not just a subtler timing-only issue the
	# way it stayed hidden in walk/run/skip's own symmetric swings.
	hip_l_angle = -lift * max(0.0, math.sin(phase + math.pi))
	hip_r_angle = -lift * max(0.0, math.sin(phase))
	if rig.hip_l is not None:
		rig.hip_l.setP(hip_l_angle)
	if rig.hip_r is not None:
		rig.hip_r.setP(hip_r_angle)
	knee_l_angle = -40 * max(0.0, math.sin(phase + math.pi))
	knee_r_angle = -40 * max(0.0, math.sin(phase))
	if rig.knee_l is not None:
		rig.knee_l.setP(knee_l_angle)
	if rig.knee_r is not None:
		rig.knee_r.setP(knee_r_angle)
	if rig.ankle_l is not None:
		rig.ankle_l.setP(-(hip_l_angle + knee_l_angle) * 0.5)
	if rig.ankle_r is not None:
		rig.ankle_r.setP(-(hip_r_angle + knee_r_angle) * 0.5)
	arm_swing = 30
	if rig.shoulder_l is not None:
		rig.shoulder_l.setP(-arm_swing * math.sin(phase))
	if rig.shoulder_r is not None:
		rig.shoulder_r.setP(-arm_swing * math.sin(phase + math.pi))
	if rig.elbow_l is not None:
		rig.elbow_l.setP(-10)
	if rig.elbow_r is not None:
		rig.elbow_r.setP(-10)
	# A brief downward stomp each half-cycle, right as a raised leg
	# would be landing (a sharp negative pulse, not a smooth sine).
	stomp = -0.05 * max(0.0, math.sin(2 * phase)) ** 4
	rig.root.setZ(stomp)
	_wheelchair_locomotion(rig, t, speed=0.85, push_amplitude=35)
