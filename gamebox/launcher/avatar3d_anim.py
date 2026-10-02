"""Pose functions for an avatar_rig.Rig - pure functions of a normalized
phase `t` in [0, 1) that directly set joint rotations/root offsets for
one frame. Plain imperative per-frame posing (not Panda3D Sequence/
Interval/taskMgr) - this drives an offscreen batch-render loop, not a
live interactive app, so setting a pose then rendering one frame is
simpler and fully controllable.

HPR convention (matching avatar3d's own Y=depth/Z=up coordinate
rule): forward/back limb swing and wheel spin are both pure setP()
(pitch = rotation about local X) - both the walk-cycle swing and the
wheel's own axle rotation happen in the Y-Z plane. A hanging arm's rest
direction is "straight down" in its own local frame, so pitching a
shoulder a full 180 degrees sweeps it all the way from hanging down,
forward, to pointing straight up - the only rotational DOF this rig's
shoulders/hips have, but enough range for a real overhead raise, not
just a forward/back swing. setH() (heading = rotate about Z) is for
head turns/shakes and a full-body spin; root setX/setY/setZ stand in
for torso sway/lean/bob (no separate torso pivot in this rig's scope).

Every joint on a Rig may be None (a missing limb, or legs replaced by
wheels under a wheelchair) - every function here checks before posing.
Every motion is EXACTLY periodic (t=0 and t->1 map to the same pose) so
consecutive loop iterations never pop."""
import math


def _blink(rig, t, center=0.5, width=0.02):
	"""Shared blink helper - shows rig.eyes_closed in a short window
	around `center` (as a fraction of the loop), otherwise hides it. A
	real blink is ~100-150ms; at a 2s loop that's roughly a +/-0.02
	window, a near-instant close/open rather than a slow fade."""
	if rig.eyes_closed is None:
		return
	d = abs(t - center)
	d = min(d, 1.0 - d)  # wrap-around distance, so a window near t=0/1 still works
	if d <= width:
		rig.eyes_closed.show()
	else:
		rig.eyes_closed.hide()


def _mouth_flap(rig, t, toggles_per_loop=4, duty=0.4):
	"""Shared "mouth moving" helper - opens/closes rig.mouth_open
	`toggles_per_loop` times across the loop, open for `duty` of each
	toggle. Not an attempt at real lip-sync, just a real demonstration
	that the mouth can move at all."""
	if rig.mouth_open is None:
		return
	phase = (t * toggles_per_loop) % 1.0
	if phase < duty:
		rig.mouth_open.show()
	else:
		rig.mouth_open.hide()


MAX_WHEELCHAIR_PUSH = 24
# The largest shoulder-pitch swing the push motion can use before the
# hand visibly touches/clips into the wheel - confirmed via direct
# render sweeping amplitude 18/22/28/35: 35 hid the hand entirely
# behind the wheel (touching or overlapping), 28 still showed a bare
# sliver right at the wheel's edge, 22-24 gave clean, confirmed
# separation. Every call below is clamped to this so a future caller
# asking for a "bigger" push can't silently reintroduce the clipping -
# the sine's own natural peak-then-reverse shape already gives the
# "stop right at the wheel and reverse" motion the amplitude was tuned
# to land on; this constant is what keeps that peak from overshooting
# past the actual wheel surface.


def _rest_pose(rig):
	"""Resets every joint a motion function might pose - shoulders,
	elbows, hips, knees, ankles, neck, wheels, root, and the mouth/blink
	overlays - back to its neutral value. Every motion function below
	only sets the joints IT cares about (each as an absolute angle, not
	a delta - see play()'s own docstring), so a joint no motion touches
	just keeps whatever some earlier motion last left it at, forever -
	a real, reported bug: flex's raised arms (shoulder_l/r) stayed
	raised after switching to idle, since idle - like nod/shake_head/
	laugh/spin/sad's legs/every non-locomotion motion - never itself
	touches shoulder_l/r. Called once per frame by play(), before the
	actual motion function runs, so it always starts from a clean
	baseline that the motion is then free to override.

	Deliberately leaves rig.waist alone - its own baseline forward
	stoop (age posture) is set once at rig-build time, not per frame
	(see build_rig's own docstring on that), so resetting it here every
	frame would erase that baseline for every motion that doesn't
	itself set waist.setP(), not just fix the stuck-joint bug."""
	for joint in (
		rig.shoulder_l, rig.shoulder_r, rig.elbow_l, rig.elbow_r,
		rig.hip_l, rig.hip_r, rig.knee_l, rig.knee_r,
		rig.ankle_l, rig.ankle_r, rig.wheel_l, rig.wheel_r,
	):
		if joint is not None:
			joint.setP(0)
	if rig.shoulder_l is not None:
		rig.shoulder_l.setH(0)
	if rig.shoulder_r is not None:
		rig.shoulder_r.setH(0)
	if rig.neck is not None:
		rig.neck.setP(0)
		rig.neck.setH(0)
	rig.root.setH(0)
	rig.root.setX(0)
	rig.root.setZ(0)
	if rig.mouth_open is not None:
		rig.mouth_open.hide()
	if rig.eyes_closed is not None:
		rig.eyes_closed.hide()


def _wheelchair_locomotion(rig, t, speed=1.0, push_amplitude=35):
	"""Wheelchair equivalent of a leg-swing locomotion motion - the
	wheelchair is a leg REPLACEMENT, not a separate accessory/animation
	category, so every leg-locomotion motion below calls this (when
	wheels exist) instead of just leaving the character motionless.
	Spins the wheels `speed` full rotations per loop and drives the
	arms in a push-rim motion synced to that same rate (overriding
	whatever balance-swing arm motion the calling function already set
	for a standing character - a wheelchair user's arms are busy
	pushing the rims, not swinging for balance). `speed` scales both the
	wheel spin and the push cadence together, so a "faster" wheelchair
	motion (matching a run's own faster leg cadence) rolls faster AND
	pushes more often, not just one or the other.

	A real, direct correction: growing the wheels (per separate direct
	feedback that they read as "too small") made them wide/tall enough
	to physically overlap where a straight-down-hanging arm/hand sits -
	confirmed via direct render, the hand was clipping into/behind the
	wheel through most of the push cycle. The real fix isn't just
	geometric bookkeeping - it's that a real wheelchair push motion
	ALSO swings the arm outward to reach the rim from the side, not
	straight down through the chair's own centerline, so shoulder.setH()
	(an outward lateral splay, on top of the existing forward/back
	push pitch) is the anatomically-correct fix, not a coincidental
	workaround.

	A second, real follow-up bug found via direct render: setH() and
	setP() on the SAME pivot don't just add independently - confirmed
	by isolating each rotation alone (both looked like a normal arm
	swing) versus combined (the arm swept up and behind the head, an
	unnatural hyperextended-looking pose - "arms bend too far back").
	This is a real compound-rotation interaction for a limb whose rest
	orientation points straight down, not a simple two-axis sum. Testing
	confirmed the effect only becomes severe above roughly 20 degrees of
	heading splay at this pitch range - 15 degrees still clears the
	wheel (confirmed via render) without triggering it."""
	if rig.wheel_l is not None:
		rig.wheel_l.setP(360 * speed * t)
	if rig.wheel_r is not None:
		rig.wheel_r.setP(360 * speed * t)
	if rig.wheel_l is None and rig.wheel_r is None:
		# A real, confirmed-live bug: this push/elbow override used to run
		# unconditionally whenever a shoulder/elbow existed, with no check
		# for wheels at all - clobbering the caller's own (correctly
		# alternating) arm swing on EVERY standing walk/run/march/skip
		# call, not just wheelchair ones ("arms don't alternate" - a real
		# report, confirmed by both shoulders landing on the exact same
		# world position under the caller's own opposite-phase values).
		# Only a wheelchair user's arms are busy pushing the rims; a
		# standing character's arms should keep whatever swing the caller
		# already set.
		return
	phase = 2 * math.pi * speed * t
	push = min(push_amplitude, MAX_WHEELCHAIR_PUSH) * math.sin(phase)
	if rig.shoulder_l is not None:
		rig.shoulder_l.setP(push)
		rig.shoulder_l.setH(-15)
	if rig.shoulder_r is not None:
		rig.shoulder_r.setP(push)
		rig.shoulder_r.setH(15)
	elbow_bend = -15 - 20 * max(0.0, math.sin(phase + math.pi / 2))
	if rig.elbow_l is not None:
		rig.elbow_l.setP(elbow_bend)
	if rig.elbow_r is not None:
		rig.elbow_r.setP(elbow_bend)



# Every named motion - idle/walk/run/march/skip/jump/dance/cheer/flex/
# nod/shake_head/laugh/sad/crouch/spin/bow/wave, all 17 of them - now
# lives in its own file under avatar_plugins_builtin/motion/<name>/
# (motion.json + motion.py, each providing pose(rig, t)), extracted via
# inspect.getsource() against this file's own then-current functions
# (byte-for-byte identical, not a retype) per a direct request to
# "separate all the different items and animations, one per py file...
# using the extension system we made". "roll" was already gone before
# that (a thin wheelchair-only alias for "walk" - see this project's own
# git history for that particular cleanup's own reasoning; nothing here
# references it anymore).
#
# MOTIONS itself is deliberately empty now, not deleted outright - a
# genuine "not necessarily make anything for it but able to expand it"
# seam (the same principle the "bodypart" plugin type was built under):
# a future built-in motion that, for whatever reason, wants to live
# directly in this file again (rather than its own avatar_plugins_builtin/
# motion/ file) still has somewhere to register itself, and every real
# consumer already goes through installed_motions() (which starts from
# a copy of this dict) or play() (which checks it first, before falling
# back to the plugin-resolved cache), not this dict in isolation.
MOTIONS = {}


_plugin_motion_cache = {}


def refresh_plugin_motions():
	"""Clears play()'s own cache of resolved plugin-provided motion
	functions (see its own docstring for why one exists at all) - call
	this whenever the installed motion set might have changed (a future
	Store install/uninstall action, or - matching avatar3d_viewer.
	refresh_custom_fields()'s own same defensive habit -
	AvatarCustomizePage.start() calling this on every entry) so an
	edited/reinstalled motion takes effect on its very next use instead
	of being stuck on whatever got cached from an earlier resolution."""
	_plugin_motion_cache.clear()


def play(rig, name, t):
	"""The one real entry point a driver (avatar3d_viewer.py's live
	customize preview, avatar3d_previewer.py's dev tool) should call to
	pose a rig each frame, instead of indexing MOTIONS[name] and
	calling the function directly. Calls _rest_pose(rig) first, then
	the named motion - every motion function poses the specific joints
	it cares about as an ABSOLUTE angle each frame (not a delta from
	wherever that joint happened to be), so resetting everything else
	to neutral first, then letting the motion override its own joints,
	is exactly equivalent to the motion running from a freshly-built
	rig - it just means a switch to a new motion can never leave a
	joint stuck at whatever some earlier, no-longer-playing motion set
	it to (the exact bug reported with flex's arms staying raised after
	switching to idle).

	`name` isn't always in MOTIONS itself - "wave" (see
	avatar_plugins_builtin/motion/wave/) is the first real example of a
	motion that ISN'T, extracted out through the same "motion" plugin
	hook a real third-party plugin would use. A cache miss there falls
	back to avatar_plugin_backend.installed_motions() (deferred import -
	that module already imports this one, at its own module level, to
	build avatar3d_anim.MOTIONS's own merge, so this can only import it
	back lazily, at call time) and CACHES the resolved function - play()
	runs every single rendered frame (12-24/sec) for whatever motion is
	currently selected, so re-scanning every installed/built-in motion
	plugin's own manifest and re-exec'ing its module from scratch on
	every single frame (installed_motions()'s own real cost, deliberate
	everywhere else this project loads a plugin, for real hot-reload)
	would be a real, visible stutter here specifically - refresh_plugin_
	motions() is the escape hatch back to "fresh on next use" once
	something might have actually changed."""
	_rest_pose(rig)
	motion_fn = MOTIONS.get(name) or _plugin_motion_cache.get(name)
	if motion_fn is None:
		import avatar_plugin_backend
		motion_fn = avatar_plugin_backend.installed_motions()[name]
		_plugin_motion_cache[name] = motion_fn
	motion_fn(rig, t)
