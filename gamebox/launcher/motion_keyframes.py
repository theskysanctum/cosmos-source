"""Shared keyframe interpolation for Astronar Studio's Motion mode -
used both by astronar_studio.py's own live "Play Loop" preview and by
every "motion" plugin it generates, so what you see in the editor is
exactly what plays in-game, not a second reimplementation that could
quietly disagree with it.

Deliberately NOT Blender/mocap-driven (a direct request: motion
creation "shouldn't require Blender") - a motion here is just a handful
of full-body poses (a dict of {joint: (h, p, r)}, plus a "root": (x, y,
z) entry for torso sway/lean/bob - avatar3d_anim.py's own established
convention for root as the one translation DOF this rig's motions use)
set directly on the live rig preview, looped by linear interpolation
between them. Every existing built-in motion (avatar3d_anim.py) is
hand-written procedural math instead - this is a second, simpler
authoring path for the same pose(rig, t) plugin contract, not a
replacement for it."""


def pose_at(rig, keyframes, joints, t):
	"""Poses `rig` for one frame of a closed loop of `keyframes` (each a
	dict as described above - any joint/root a given keyframe omits is
	treated as (0,0,0), i.e. rest), interpolating linearly between
	whichever two keyframes `t` (a normalized [0,1) loop phase) falls
	between. `joints` is the real list of non-root joint names any
	keyframe dict may use - passed in rather than hardcoded so this
	file never needs to change if the rig's own joint set ever does.
	Caller's responsibility to reset the rig to a neutral pose first
	(avatar3d_anim._rest_pose(rig)) - the same "every motion poses
	joints as an ABSOLUTE angle, not a delta" contract every hand-
	written motion function already follows, so switching motions can
	never leave a joint stuck from whatever played before it."""
	n = len(keyframes)
	if n == 0:
		return
	if n == 1:
		kf0 = kf1 = keyframes[0]
		local_t = 0.0
	else:
		seg = (t % 1.0) * n
		i0 = int(seg) % n
		i1 = (i0 + 1) % n
		local_t = seg - int(seg)
		kf0, kf1 = keyframes[i0], keyframes[i1]

	def _lerp3(a, b, u):
		return tuple(a[i] + (b[i] - a[i]) * u for i in range(3))

	root0 = kf0.get("root", (0.0, 0.0, 0.0))
	root1 = kf1.get("root", (0.0, 0.0, 0.0))
	rig.root.setPos(*_lerp3(root0, root1, local_t))

	for joint in joints:
		part = getattr(rig, joint, None)
		if part is None:
			continue
		hpr0 = kf0.get(joint, (0.0, 0.0, 0.0))
		hpr1 = kf1.get(joint, (0.0, 0.0, 0.0))
		part.setHpr(*_lerp3(hpr0, hpr1, local_t))
