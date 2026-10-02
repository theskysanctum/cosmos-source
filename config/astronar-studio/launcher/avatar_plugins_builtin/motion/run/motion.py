"""CosmOS's own built-in "run" motion - identical to what used
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
	"""A faster, bigger-stride walk - larger hip/arm swing, a higher and
	quicker bob, a real forward lean at the waist (plus a bit more at
	the neck) - the legs stay under the body, only the upper half leans
	into the sprint - plus a bigger knee bend/ankle counter-rotation and
	a fixed elbow bend, scaled up from walk's own. Two full stride
	cycles per loop (double walk's cadence, per direct feedback that a
	single cycle read as too slow for a "run") - still exactly periodic
	since 2 is an integer multiple (phase completes two full turns by
	t=1, landing back on the same value as t=0)."""
	phase = 2 * math.pi * t * 2
	if rig.waist is not None:
		rig.waist.setP(15)
	swing = 42
	# Sign convention fixed the same way as walk() - see its own comment.
	hip_l_angle = -swing * math.sin(phase + math.pi)
	hip_r_angle = -swing * math.sin(phase)
	if rig.hip_l is not None:
		rig.hip_l.setP(hip_l_angle)
	if rig.hip_r is not None:
		rig.hip_r.setP(hip_r_angle)
	knee_l_angle = -45 * max(0.0, math.sin(phase + math.pi))
	knee_r_angle = -45 * max(0.0, math.sin(phase))
	if rig.knee_l is not None:
		rig.knee_l.setP(knee_l_angle)
	if rig.knee_r is not None:
		rig.knee_r.setP(knee_r_angle)
	if rig.ankle_l is not None:
		rig.ankle_l.setP(-(hip_l_angle + knee_l_angle) * 0.5)
	if rig.ankle_r is not None:
		rig.ankle_r.setP(-(hip_r_angle + knee_r_angle) * 0.5)
	arm_swing = 38
	if rig.shoulder_l is not None:
		rig.shoulder_l.setP(-arm_swing * math.sin(phase))
	if rig.shoulder_r is not None:
		rig.shoulder_r.setP(-arm_swing * math.sin(phase + math.pi))
	if rig.elbow_l is not None:
		rig.elbow_l.setP(-10)
	if rig.elbow_r is not None:
		rig.elbow_r.setP(-10)
	rig.root.setZ(0.07 * abs(math.sin(2 * phase)))
	if rig.neck is not None:
		rig.neck.setP(8)
	_wheelchair_locomotion(rig, t, speed=2.0, push_amplitude=45)
