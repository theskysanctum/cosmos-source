"""CosmOS's own built-in "wave" motion - identical to what used to live
in avatar3d_anim.py's own MOTIONS dict (a real, direct extraction, not
a rewrite), moved out as the second feature (alongside bodypart/
backpack/) to prove the extension system holds up for existing,
already-shipped content, not just new plugin content.

Imports avatar3d_anim directly for _blink()/_mouth_flap() - the same
shared idle-detail helpers every other motion (built-in or not) layers
on top of its own gesture - rather than the plugin contract needing to
pass them in as extra arguments; a "motion" plugin's own pose(rig, t)
signature stays exactly the same either way."""
import math

import avatar3d_anim


def pose(rig, t):
	"""One arm (the right) raised and waving side to side overhead;
	everything else - the other arm, legs, blink, mouth, head - stays
	in a normal idle state, matching how a real wave is a one-limb
	gesture layered on an otherwise-relaxed stance."""
	phase = 2 * math.pi * t
	if rig.shoulder_r is not None:
		# Raised most of the way up, then a fast side-to-side wag via
		# heading on top of the raised pitch (H and P combine on the
		# same pivot - the arm ends up raised AND swinging left/right).
		rig.shoulder_r.setP(-140)
		rig.shoulder_r.setH(22 * math.sin(phase * 6))
	if rig.shoulder_l is not None:
		rig.shoulder_l.setP(6 * math.sin(2 * math.pi * t))
	rig.root.setZ(0.015 * math.sin(2 * math.pi * t))
	if rig.neck is not None:
		rig.neck.setH(8 * math.sin(2 * math.pi * t))
	avatar3d_anim._blink(rig, t)
	avatar3d_anim._mouth_flap(rig, t, toggles_per_loop=1, duty=0.3)
