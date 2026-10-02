"""CosmOS's own built-in "crouch" motion - identical to what used
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
	"""A held, lowered crouch/duck - a real squat using the knee joints
	(a direct fix: the first version only bent the hip, which reads as
	leaning over, not crouching, since a real squat's dominant joint is
	the knee, not the hip). Hip bends a moderate amount, knee bends a
	lot more, and the ankle almost fully cancels the combined hip+knee
	rotation so the foot stays flat and planted on the ground rather
	than tipping forward - a static held pose, so (unlike walk's
	swing-through-the-air ankle, which only partially cancels) there's
	no reason for the foot to be anything but flat here.

	Two real, confirmed-live bugs in the original version, both fixed
	here: first, knee_angle had the SAME sign as hip_angle - fine for
	walk's swinging leg (thigh and shin tilting the same way lifts the
	foot off the ground), wrong for a planted squat, where the shin has
	to bend BACKWARD relative to the thigh (opposite sign) so the foot
	stays under the body - forming a "V" instead of continuing forward
	past horizontal into a mangled clump. Second, and the one that
	actually mattered visually: positive hip.setP() tilts the thigh
	BACKWARD, not forward (confirmed directly by rendering the hip
	joint alone against a visible landmark - the profile silhouette's
	nose bump - and checking which way the thigh leaned relative to the
	face). The original code's positive hip_angle was therefore already
	tilting the thigh backward before the knee even got involved, and
	even after fixing the knee's relative sign, a positive hip still
	read as a leg bending the wrong way entirely - a backward-leaning
	hock/digitigrade shape instead of a human squat (bluntly reported
	live as "goat legs"). hip_angle must be NEGATIVE (thigh forward)
	with knee_angle POSITIVE (shin folds back, mirroring the thigh) for
	the knee to visibly point forward like a real squat. The ankle's
	own cancel formula didn't need to change - it already computes off
	whatever the combined hip+knee angle actually is, regardless of
	which absolute sign that ends up being.

	Wheelchair characters have no legs to squat with, and the root
	lowering below would sink the whole (rigid, static) wheelchair
	frame through the floor along with the body - so this motion only
	lowers root when there's an actual leg chain to bend; a wheelchair
	character still gets the forward shoulder lean (leaning forward in
	the chair) and blink, just not the squat itself."""
	phase = 2 * math.pi * t
	if rig.hip_l is not None or rig.hip_r is not None:
		rig.root.setZ(-0.32 + 0.01 * math.sin(phase))
	hip_angle, knee_angle = -35, 70
	if rig.hip_l is not None:
		rig.hip_l.setP(hip_angle)
	if rig.hip_r is not None:
		rig.hip_r.setP(hip_angle)
	if rig.knee_l is not None:
		rig.knee_l.setP(knee_angle)
	if rig.knee_r is not None:
		rig.knee_r.setP(knee_angle)
	if rig.ankle_l is not None:
		rig.ankle_l.setP(-(hip_angle + knee_angle) * 0.9)
	if rig.ankle_r is not None:
		rig.ankle_r.setP(-(hip_angle + knee_angle) * 0.9)
	# Same sign convention as hip/knee above (positive = backward): the
	# old +20 was actually swinging the arms backward, not forward -
	# confirmed by isolated render against the face's own front-facing
	# side. Negative is the correct forward lean here.
	if rig.shoulder_l is not None:
		rig.shoulder_l.setP(-20)
	if rig.shoulder_r is not None:
		rig.shoulder_r.setP(-20)
	_blink(rig, t)
