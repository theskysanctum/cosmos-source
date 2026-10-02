"""CosmOS's own built-in gloves accessory - identical geometry to what
used to live inline in avatar3d.build_character() (the static path) - a
real, direct extraction, not a rewrite. Reads the same trait keys the
old inline version did ("gloves" membership in the shared "accessories"
list, "gloves_color" as a hex string or None).

Unlike wristband/socks, this is a genuinely PARTIAL extraction: the rig
path never drew a separate bolt-on box for gloves the way the static
path does - avatar3d._build_hand() recolors the palm IN PLACE instead
(`color_c = ... gloves_c if gloves else skin` - see that function's own
docstring on why: a glove isn't a separate object sitting on top of the
hand, it replaces the hand's own surface color). That recolor stays
inside _build_hand() as a real, core piece of hand-building - avatar3d_rig.
build_rig() resolves `gloves_color` from **extra_traits and threads the
computed `gloves_c` through to _build_hand() itself (same shape as
muzzle/snout_color's own treatment - see build_rig()'s own comment on
this), so this plugin's own build_animated() is a deliberate no-op:
there's no separate rig-side geometry for it to add."""


def _hex_to_rgba(hex_color):
	h = hex_color.lstrip("#")
	r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
	return (r, g, b, 1)


def build_portrait(vertex, normal, color, tris, idx, box, outlined_box, traits, metrics):
	if "gloves" not in metrics["accessories"]:
		return idx
	gloves_color = traits.get("gloves_color")
	gloves_c = _hex_to_rgba(gloves_color) if gloves_color else (0.1, 0.1, 0.12, 1)
	chest_half_w, limb_half_w, LEG_H = metrics["chest_half_w"], metrics["limb_half_w"], metrics["LEG_H"]
	for side in (-1, 1):
		outer = side * (chest_half_w + limb_half_w * 2)
		inner = side * chest_half_w
		gl_x0, gl_x1 = sorted((inner, outer))
		idx = outlined_box(
			vertex, normal, color, tris, idx,
			(gl_x0, -0.19, LEG_H - 0.03), (gl_x1, 0.19, LEG_H + 0.1), gloves_c,
		)
	return idx


def build_animated(rig, traits, metrics):
	# No-op on purpose - see this file's own docstring. The rig's own
	# "gloves" effect (recoloring the palm) happens inside
	# avatar3d._build_hand() itself, driven by a value avatar3d_rig.
	# build_rig() resolves from **extra_traits, not by this hook.
	return
