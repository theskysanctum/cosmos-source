#!/usr/bin/env python3
"""The live, animated 3D Astronar customization screen - app.py's own
"Choose a Picture" / "Change Astronar" flow, replacing the old
avatar_gen.py space-creature 3x3 grid entirely. Built on the exact same
"drive an already-proven OffscreenRenderer as a frame loop, blit each
frame into a plain Gtk.Image on a GLib timer" technique confirmed live
against this project's own real X session - a genuine embedded GLX
window (WindowProperties().setParentWindow(), the same X11-reparenting
trick media/vlc_player.py uses for libVLC) was tried first and dropped:
it rendered a visibly wrong/blank surface both on the dev desktop and in
a clean, compositor-free Xephyr session, while this frame-loop approach
rendered correctly and animated smoothly in both. No X11 reparenting, no
GL-visual-matching, no compositor interaction at all - from the user's
side it looks and feels identical (a live, smoothly animating avatar
inside the app's own window).

CUSTOM_FIELDS is the single source of truth for what's customizable -
scoped to exactly what avatar3d_rig.build_rig() supports, which is now
nearly all of avatar3d.build_character()'s own richness (colors,
hairstyle/eye_shape/nose_shape/mouth_style/facial_hair/freckles/blush/
muzzle/whiskers/fur_pattern, every clothing top_style/pattern/pattern
style, every accessory (glasses/hat/backpack/satchel/purse/necklace/
earrings/tie/wristband/socks/gloves/headphones - see the "multi_bool"
kind below for how 12 rows share the one real "accessories" trait key),
paws/claws, animal/mythical features, accessibility options, body
sliders, gender, age). `bottom_style="skirt"/"dress"` is the one
deliberate scope cut left out of BOTTOM_STYLES_RIG below - see
build_rig()'s own docstring for why (no skirt/dress mesh in this rig,
only ever jointed leg geometry). avatar3d.render_portrait() - used for
every small-icon context elsewhere in the app (profile-picker tiles,
the Dashboard widget) - calls build_character() with this exact same
trait dict, so whatever a person picks here is exactly what shows up
everywhere else too, not a richer static picture they never actually
see live."""
import colorsys
import math
import sys
import time
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf, GLib, Gtk, Pango, PangoCairo  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import debuglog  # noqa: E402

import avatar3d  # noqa: E402
import avatar3d_anim  # noqa: E402
import avatar3d_pet  # noqa: E402
import avatar3d_pet_anim  # noqa: E402
import avatar3d_pet_rig  # noqa: E402
import avatar3d_rig  # noqa: E402
import avatar3d_thumbnail_cache  # noqa: E402
import avatar_plugin_backend  # noqa: E402
import controller_glyphs  # noqa: E402
import profiles  # noqa: E402
import scaling  # noqa: E402

log = debuglog.setup("avatar3d_viewer")

FPS = 12
PREVIEW_SIZE = scaling.px(360)
OPTION_TILE_SIZE = scaling.px(96)  # the options grid's own thumbnail cells - see AvatarCustomizePage._make_option_tile()
OPTIONS_SCROLLER_HEIGHT = scaling.px(230)  # a comfortable ~2 rows visible before scrolling, even for a 19-option field like "ears"
OUTFIT_TILE_SIZE = scaling.px(110)  # the saved-outfit slot row's own thumbnail cells - see AvatarCustomizePage._make_outfit_tile()
ROW_THUMB_SIZE = scaling.px(56)  # the right column's own compact collapsed field rows - see AvatarCustomizePage._make_field_row()

# Free-orbit/zoom camera controls, ported over from avatar3d_previewer.py's
# own standalone dev tool (WASD orbit, +/- zoom, Home reset - see that
# file's own self.hint_label text) per direct instruction ("add the same
# kb controls from the previewer to this"). Applied to the rig itself
# (setH/setP/setScale on self.rig.root/self.pet_rig.root), same as the
# existing heading-only rotate_left()/rotate_right() already did, NOT the
# shared OffscreenRenderer camera (see avatar3d.py's own OffscreenRenderer
# docstring - that one camera/buffer is reused by every render at
# PREVIEW_SIZE, so moving it here would visibly disturb any other frame
# rendered while this screen's tick loop is running).
HEADING_STEP = 15.0
PITCH_STEP = 8.0
PITCH_MIN, PITCH_MAX = -30.0, 30.0
ZOOM_STEP = 0.1
ZOOM_MIN, ZOOM_MAX = 0.7, 1.6
DEFAULT_HEADING = 20.0
MAX_OUTFIT_SLOTS = profiles.MAX_OUTFITS


def _draw_ring(widget, cr):
	"""The shared selection-ring paint used by both the options grid
	(_draw_option_ring()) and the saved-outfit row (_draw_outfit_ring())
	- identical Cairo drawing either way, only the guard clause deciding
	WHETHER to paint it differs per caller. Painted directly via Cairo,
	not CSS, per this project's own standing convention (GTK for UI,
	Python for logic/drawing, CSS only for bare-minimum styling) after
	CSS genuinely couldn't override GtkFlowBoxChild's own native focus
	rendering for the Dashboard's Astronar badge earlier this project."""
	width = widget.get_allocated_width()
	height = widget.get_allocated_height()
	line_width = scaling.px(4)
	radius = scaling.px(10)
	x, y = line_width / 2, line_width / 2
	w, h = width - line_width, height - line_width
	cr.save()
	cr.set_line_width(line_width)
	cr.set_source_rgba(1.0, 0.0, 0.565, 0.9)  # theme.css's own @accent_2 (#ff0090)
	cr.new_sub_path()
	cr.arc(x + w - radius, y + radius, radius, -math.pi / 2, 0)
	cr.arc(x + w - radius, y + h - radius, radius, 0, math.pi / 2)
	cr.arc(x + radius, y + h - radius, radius, math.pi / 2, math.pi)
	cr.arc(x + radius, y + radius, radius, math.pi, 3 * math.pi / 2)
	cr.close_path()
	cr.stroke()
	cr.restore()

WHEELCHAIR_COLORS = ["#2a2a3a", "#22252e", "#5c1414", "#1e3d2f", "#6b6b6b"]
WHEELCHAIR_TIRE_COLORS = ["#0f0f11", "#1a1a1a", "#5c1414", "#3355cc", "#f4f7ff"]
PROSTHETIC_CHOICES = [[], ["left_arm"], ["right_arm"], ["left_leg"], ["right_leg"]]
GENDERS = ["boy", "girl", "nonbinary"]

# Curated accessory-color palettes - same "small hand-picked list, not
# the full 16.7M-color picker" convention as WHEELCHAIR_COLORS/
# WHEELCHAIR_TIRE_COLORS above (this plain field-cursor UI can only
# step through a fixed list with Left/Right; avatar3d_previewer.py's
# own full RGB picker is the only place a swatch here isn't the final
# word on what's pickable).
ACCENT_COLORS = ["#1a1a1a", "#f4f7ff", "#5c1414", "#1e3d2f", "#3355cc", "#6b4423"]
# JEWELRY_COLORS used to live here too - now only referenced as a
# literal list inlined in avatar_plugins_builtin/bodypart/necklace/
# bodypart.json's own "options" (manifest fields are pure data, not
# Python - see that file's own comment on why the values are inlined
# rather than referencing a shared constant).
# bottom_style is deliberately narrower than avatar3d.BOTTOM_STYLES -
# "skirt"/"dress" stay out of this list on purpose (see
# avatar3d_rig.build_rig()'s own docstring: this rig has no skirt/dress
# mesh, only ever jointed leg geometry), so picking one here can never
# show a different silhouette live than what the confirmed picture
# actually renders - the same "what you pick here is exactly what shows
# up everywhere else" guarantee this module's own header docstring
# already promises for every other field.
BOTTOM_STYLES_RIG = ["pants", "shorts", "overalls"]

# Navigation order for category_prev()/category_next() (L1/R1 - see
# AvatarCustomizePage's own docstring on why flat Up/Down alone through
# ~90 fields needed this): body basics first (everything else's own
# proportions/silhouette depend on these), then face, then working
# outward - clothes, then what's worn over them, then the separate pet
# companion, then accessibility options last (the smallest, most
# situational group). No standalone "Creature" category any more - per
# direct instruction ("sort creature into body and face... it doesn't
# need to be it's own area"), every animal/mythical feature folded
# into whichever of Body/Face it actually belongs on instead (see
# _BASE_CUSTOM_FIELDS' own "Animal/mythical features" comment, and each
# relevant bodypart plugin's own manifest).
_BASE_CATEGORIES = ["Body", "Face", "Clothing", "Accessories", "Pet", "Accessibility"]

_BASE_CUSTOM_FIELDS = [
	# --- Body ---
	dict(category="Body", key="gender", label="Gender", kind="choice", options=GENDERS),
	dict(category="Body", key="weight", label="Weight", kind="slider", range=(0.0, 1.0, 0.1)),
	dict(category="Body", key="muscle", label="Muscle", kind="slider", range=(0.0, 1.0, 0.1)),
	dict(category="Body", key="shape", label="Shape", kind="slider", range=(0.0, 1.0, 0.1)),
	dict(category="Body", key="age", label="Age", kind="slider", range=(0.0, 1.0, 0.1)),
	# --- Face ---
	dict(category="Face", key="skin_color", label="Skin", kind="color", options=avatar3d.SKIN_TONES),
	dict(category="Face", key="hair_color", label="Hair", kind="color", options=avatar3d.HAIR_COLORS),
	dict(category="Face", key="hairstyle", label="Hairstyle", kind="choice", options=avatar3d.HAIRSTYLES),
	dict(category="Face", key="eyebrow_color", label="Eyebrows", kind="color_opt", options=avatar3d.HAIR_COLORS, none_label="match hair"),
	dict(category="Face", key="eye_color", label="Eyes", kind="color", options=avatar3d.EYE_COLORS),
	dict(category="Face", key="eye_color_right", label="Right eye", kind="color_opt", options=avatar3d.EYE_COLORS, none_label="match left"),
	dict(category="Face", key="eye_shape", label="Eye shape", kind="choice", options=avatar3d.EYE_SHAPES),
	dict(category="Face", key="nose_shape", label="Nose shape", kind="choice", options=avatar3d.NOSE_SHAPES),
	dict(category="Face", key="mouth_style", label="Mouth", kind="choice", options=avatar3d.MOUTH_STYLES),
	dict(category="Face", key="facial_hair", label="Facial hair", kind="choice", options=avatar3d.FACIAL_HAIR),
	dict(category="Face", key="freckles", label="Freckles", kind="bool"),
	dict(category="Face", key="blush", label="Blush", kind="bool"),
	dict(category="Face", key="iris_ring", label="Iris ring", kind="bool"),
	dict(category="Face", key="skin_patches", label="Skin patches", kind="bool"),
	# --- Clothing ---
	dict(category="Clothing", key="shirt_color", label="Shirt", kind="color", options=avatar3d.SHIRT_COLORS),
	dict(category="Clothing", key="pants_color", label="Pants", kind="color", options=avatar3d.PANTS_COLORS),
	dict(category="Clothing", key="shoe_color", label="Shoes", kind="color", options=avatar3d.SHOE_COLORS),
	dict(category="Clothing", key="top_style", label="Top style", kind="choice", options=avatar3d.TOP_STYLES),
	dict(category="Clothing", key="cropped", label="Cropped top", kind="bool"),
	dict(category="Clothing", key="hood_up", label="Hood up", kind="bool"),
	dict(category="Clothing", key="drawstring_color", label="Drawstring color", kind="color", options=ACCENT_COLORS),
	dict(category="Clothing", key="bottom_style", label="Bottom style", kind="choice", options=BOTTOM_STYLES_RIG),
	dict(category="Clothing", key="shorts_style", label="Shorts style", kind="choice", options=avatar3d.SHORTS_STYLES),
	dict(category="Clothing", key="pants_style", label="Pants style", kind="choice", options=avatar3d.PANTS_STYLES),
	dict(category="Clothing", key="shirt_pattern", label="Shirt pattern", kind="choice", options=list(avatar3d.FABRIC_PATTERNS)),
	dict(category="Clothing", key="pants_pattern", label="Pants pattern", kind="choice", options=list(avatar3d.FABRIC_PATTERNS)),
	dict(category="Clothing", key="leg_type", label="Leg type", kind="choice", options=avatar3d.LEG_TYPES),
	# --- Accessories - every row shares the ONE real "accessories" trait
	# key (a list build_rig()/build_character() both take as-is) rather
	# than each inventing its own synthetic trait name - see
	# _step_value's own "multi_bool" branch for how a row toggles just
	# its own `item` string's membership in that shared list. Each
	# item's own color field is a genuinely separate, real trait key
	# since color always needs its own slot regardless of how "on/off"
	# is stored. glasses/hat/backpack/satchel/purse/necklace/earrings/
	# tie/headphones/wristband/socks/gloves all moved to their own files
	# under avatar_plugins_builtin/bodypart/ - each one's own rows get
	# appended here at customizer build time by refresh_custom_fields(),
	# not declared inline anymore (see that function's own docstring).
	# --- Animal/mythical features - folded into Body/Face, not their own
	# "Creature" category, per direct instruction ("sort creature into
	# body and face... it doesn't need to be it's own area"). Head-area
	# features (ears, horns, halo, and every plugin-contributed face
	# feature - muzzle/whiskers/fur_pattern/extra_eyes/antennae) live in
	# Face; features elsewhere on the body (tail, wings, claws, paws)
	# live in Body instead - see each plugin's own bodypart.json for its
	# half of this same split.
	# label="Ears" (key stays "ears" - an internal identifier the
	# player never sees) - per direct instruction, the live player calls
	# this "Ears", not "Animal ears", and it stays the one single
	# unified list it already was (elf/alien included right alongside
	# cat/fox/wolf/etc.), not split into a separate category.
	dict(category="Face", key="ears", label="Ears", kind="choice", options=avatar3d.EARS),
	dict(category="Face", key="ear_scale", label="Ear size", kind="slider", range=(0.5, 2.0, 0.1)),
	dict(category="Face", key="ear_thickness", label="Ear thickness", kind="slider", range=(0.5, 2.0, 0.1)),
	dict(category="Face", key="ear_depth", label="Ear depth", kind="slider", range=(0.5, 2.0, 0.1)),
	# tail moved to avatar_plugins_builtin/bodypart/tail/ - its own rows
	# get appended here at customizer build time by refresh_custom_fields()
	# (see that function's own docstring). animal_color stays here, not
	# in tail's own manifest - it's a real shared field (ears, staying
	# in core, uses it too), read straight off metrics by the tail plugin.
	# Categorized under Face (not Body, where tail itself now lives) -
	# it's declared once, and "Ears" is the half of its own label that
	# comes first.
	dict(category="Face", key="animal_color", label="Ears/tail color", kind="color_opt", options=avatar3d.HAIR_COLORS, none_label="match hair"),
	# muzzle moved to avatar_plugins_builtin/bodypart/muzzle/ - its own
	# row gets appended here at customizer build time by
	# refresh_custom_fields() (see that function's own docstring). Unlike
	# tail/wings/antennae, `muzzle` the boolean stays partially special -
	# see avatar3d.py's own _build_face()/_build_head_bundle() docstrings
	# for why it's still resolved as a real gate value inside
	# build_character()/build_rig(), just no longer a NAMED parameter of
	# either.
	# whiskers/fur_pattern moved to avatar_plugins_builtin/bodypart/
	# whiskers|fur_pattern/ - their own rows get appended here the same
	# way.
	# paws moved to avatar_plugins_builtin/bodypart/paws/ - its own row
	# gets appended here at customizer build time by
	# refresh_custom_fields() (see that function's own docstring).
	# `paws` the boolean stays partially special, same shape as muzzle -
	# see avatar3d.py's own _build_hand() docstring for why it's still
	# resolved as a real gate value inside build_rig(), just no longer a
	# NAMED parameter. claws moved to avatar_plugins_builtin/bodypart/
	# claws/ the same way, with no such gate anywhere.
	dict(category="Face", key="horns", label="Horns", kind="choice", options=avatar3d.HORN_STYLES),
	dict(category="Face", key="horn_scale", label="Horn size", kind="slider", range=(0.5, 2.0, 0.1)),
	dict(category="Face", key="horn_thickness", label="Horn thickness", kind="slider", range=(0.5, 2.0, 0.1)),
	# wings moved to avatar_plugins_builtin/bodypart/wings/ - its own
	# rows get appended here at customizer build time by
	# refresh_custom_fields() (see that function's own docstring).
	# horns/halo stay real, core fields (unchanged) even though their
	# own geometry moved out to avatar_plugins_builtin/bodypart/horns|
	# halo/ - both still participate in the hairstyle-override check
	# inside build_character()/build_rig() themselves, so they stay
	# real named parameters of those functions rather than moving to
	# **extra_traits the way antennae (below) safely could.
	dict(category="Face", key="halo", label="Halo", kind="bool"),
	# extra_eyes/antennae moved to avatar_plugins_builtin/bodypart/
	# extra_eyes|antennae/ - their own rows get appended here at
	# customizer build time by refresh_custom_fields() (see that
	# function's own docstring).
	# --- Pet companion - "species + color only" by direct choice (a
	# real, deliberate scope cut vs. the person's own much deeper ear/
	# tail/accessory customization - see avatar3d_pet_rig.build_pet_rig's
	# own docstring). Stands beside the person, animated independently
	# (its own idle - see avatar3d_pet_anim.py), not attached to any of
	# the person's own joints.
	dict(category="Pet", key="pet_species", label="Pet", kind="choice", options=avatar3d_pet.PET_SPECIES),
	dict(category="Pet", key="pet_color", label="Pet color", kind="color", options=avatar3d.HAIR_COLORS),
	# --- Accessibility ---
	dict(category="Accessibility", key="missing_eye", label="Missing eye", kind="choice", options=avatar3d.LIMB_SIDES),
	dict(category="Accessibility", key="missing_arm", label="Missing arm", kind="choice", options=avatar3d.LIMB_SIDES),
	dict(category="Accessibility", key="missing_leg", label="Missing leg", kind="choice", options=avatar3d.LIMB_SIDES),
	dict(category="Accessibility", key="prosthetics", label="Prosthetics", kind="choice", options=PROSTHETIC_CHOICES),
	dict(category="Accessibility", key="wheelchair", label="Wheelchair", kind="bool"),
	dict(category="Accessibility", key="wheelchair_color", label="Wheelchair color", kind="color", options=WHEELCHAIR_COLORS),
	dict(category="Accessibility", key="wheelchair_tire_color", label="Wheelchair tires", kind="color", options=WHEELCHAIR_TIRE_COLORS),
]

assert {f["category"] for f in _BASE_CUSTOM_FIELDS} <= set(_BASE_CATEGORIES), "every field's category must be a real, listed category"
# A subset check, not the stricter `==` this used to be - "Accessories"
# no longer has ANY _BASE_CUSTOM_FIELDS row of its own now that
# glasses/hat/backpack/satchel/purse/necklace/earrings/tie/headphones/
# wristband/socks/gloves have all moved out to their own bodypart
# plugins (every remaining "Accessories" row gets appended here at
# customizer build time by refresh_custom_fields() instead - see that
# function's own docstring). The stricter `==` only ever held because
# some hardcoded Accessories row always happened to still exist before
# this extraction finished - it was never the actual invariant this
# assert's own message describes.

# The starting look for a brand new profile that has never picked a
# picture before (see profiles.get_picture_traits()'s own None case) -
# a real, complete, renderable trait set, not a partial dict relying on
# build_rig()/build_character()'s own defaults to fill every gap.
_BASE_CUSTOM_DEFAULTS = dict(
	skin_color="#f1c27d", hair_color="#3b2417", hairstyle="short", eyebrow_color=None,
	eye_color="#1e5fa8", eye_color_right=None, eye_shape="round", nose_shape="straight", mouth_style="neutral",
	facial_hair="none", freckles=False, blush=False,
	shirt_color="#ff0090", pants_color="#22252e", shoe_color="#1a1a1a",
	top_style="long_sleeve", cropped=False, hood_up=False, drawstring_color="#f4f7ff",
	bottom_style="pants", shorts_style="regular",
	pants_style="straight", shirt_pattern="solid", pants_pattern="solid",
	leg_type="human", wheelchair=False,
	wheelchair_color="#2a2a3a", wheelchair_tire_color="#0f0f11",
	pet_species="none", pet_color="#8b5e3c",
	ears="none", ear_scale=1.0, ear_thickness=1.0, ear_depth=1.0,
	animal_color=None,
	horns="none", horn_scale=1.0, horn_thickness=1.0,
	halo=False,
	iris_ring=False, skin_patches=False,
	missing_eye="none", missing_arm="none", missing_leg="none", prosthetics=[],
	gender="nonbinary", weight=0.5, muscle=0.5, shape=0.5, age=0.5,
	# Every accessory starts absent (an empty "accessories" list, the
	# one real shared trait key every "multi_bool" CUSTOM_FIELDS row
	# toggles membership in - see that kind's own comment above) with
	# each item's own color already set to a sensible real default so
	# turning one on for the first time never renders some undefined
	# color.
	accessories=[],
)

# The real, public "what's customizable" set every call site in this app
# reads (AvatarCustomizePage itself, avatar3d_previewer.py's dev tool,
# app.py's None-traits fallback) - the built-in fields above PLUS
# whatever "bodypart" plugins are
# currently installed (see avatar_plugin_backend.installed_bodypart_
# fields()/installed_bodypart_defaults()), letting a future Astronar
# Store install a plugin that adds real new customizer sliders/choices
# without any of those call sites needing to change.
#
# These three are mutated IN PLACE by refresh_custom_fields() below,
# never reassigned - every existing call site across this app already
# reads them either as `avatar3d_viewer.CUSTOM_FIELDS` or via a local
# alias captured once at import time (avatar3d_previewer.py does
# `CUSTOM_FIELDS = avatar3d_viewer.CUSTOM_FIELDS`) - reassigning these
# names to a brand new list/dict object would leave that alias pointing
# at a now-stale, disconnected copy, while mutating the SAME object
# in place (list[:] = ..., dict.clear()+update()) keeps every existing
# reference - direct or aliased - seeing the live, current set for free,
# with zero changes needed anywhere else in the app.
CUSTOM_FIELDS = []
CATEGORIES = []
CUSTOM_DEFAULTS = {}

# The shared "naked grey Astronar" backdrop every thumbnail in the
# customizer's own options grid renders against (see
# AvatarCustomizePage._rebuild_options_grid()/avatar3d_thumbnail_cache.py) -
# per direct instruction, so an option reads as genuinely comparable
# against every other option for that same trait, not against whatever
# the player's own current character already happens to look like.
# Mutated in place by refresh_custom_fields() below, same reasoning as
# CUSTOM_FIELDS/CATEGORIES/CUSTOM_DEFAULTS themselves - never reassigned.
NEUTRAL_TRAITS = {}
_NEUTRAL_GRAY = "#9a9a9a"

# Which trait keys a saved outfit slot actually covers (see
# AvatarCustomizePage's own outfit-row methods and profiles.save_outfit()) -
# per direct instruction, an outfit is only ever Clothing + Accessories;
# body shape, face, and creature features always stay the one character
# identity, never swapped per outfit. Derived from CATEGORIES/CUSTOM_FIELDS
# themselves, not hand-maintained as a separate literal list, so a future
# plugin-contributed Accessories field is automatically included with no
# extra work - mutated in place by refresh_custom_fields() below, same
# reasoning as CUSTOM_FIELDS/CATEGORIES/CUSTOM_DEFAULTS/NEUTRAL_TRAITS
# themselves.
OUTFIT_TRAIT_KEYS = set()
OUTFIT_CATEGORIES = ("Clothing", "Accessories")


def refresh_custom_fields():
	"""Rebuilds CUSTOM_FIELDS/CATEGORIES/CUSTOM_DEFAULTS/NEUTRAL_TRAITS/
	OUTFIT_TRAIT_KEYS in place from the built-in base set plus whatever
	"bodypart" plugins are currently installed - called once below, at
	import time, and again by AvatarCustomizePage.start() every time the
	customize screen is entered, so a plugin installed in an earlier
	session (or by some future Store install flow while the app is
	already running) shows up without needing a hot-reload or restart.

	NEUTRAL_TRAITS is derived from CUSTOM_DEFAULTS (already a complete,
	renderable trait set, including whatever keys a plugin contributes)
	rather than hand-enumerated - every hex-color value becomes a flat
	mid-grey, and every choice/bool field is left exactly as
	CUSTOM_DEFAULTS already has it, since a new profile's own defaults
	are already "none"/off for every creature feature and accessory -
	genuinely neutral already, just not color-neutral."""
	plugin_fields = avatar_plugin_backend.installed_bodypart_fields()
	CUSTOM_FIELDS[:] = _BASE_CUSTOM_FIELDS + plugin_fields
	extra_categories = sorted({f["category"] for f in plugin_fields} - set(_BASE_CATEGORIES))
	CATEGORIES[:] = _BASE_CATEGORIES + extra_categories
	CUSTOM_DEFAULTS.clear()
	CUSTOM_DEFAULTS.update(_BASE_CUSTOM_DEFAULTS)
	CUSTOM_DEFAULTS.update(avatar_plugin_backend.installed_bodypart_defaults())
	OUTFIT_TRAIT_KEYS.clear()
	OUTFIT_TRAIT_KEYS.update(f["key"] for f in CUSTOM_FIELDS if f["category"] in OUTFIT_CATEGORIES)
	NEUTRAL_TRAITS.clear()
	NEUTRAL_TRAITS.update(CUSTOM_DEFAULTS)
	for key, value in list(NEUTRAL_TRAITS.items()):
		if isinstance(value, str) and value.startswith("#"):
			NEUTRAL_TRAITS[key] = _NEUTRAL_GRAY


refresh_custom_fields()


BASIC_HUE_COUNT = 12


def _basic_hue_options():
	"""A basic, evenly-spaced hue spectrum (fixed high saturation/value)
	as flat hex swatches - per direct instruction ("just make colors
	show basic color box hues"), used as EVERY "color"/"color_opt"
	field's own option list instead of each field's own hand-curated
	palette (skin_color's own 6 tones, shirt_color's own 9, ...), so any
	color field offers real, even hue coverage instead of a handful of
	specific pre-chosen shades. Computed once and cached (module-level,
	same "mutate in place, never reassign" reasoning as CUSTOM_FIELDS
	etc. isn't needed here - it's a pure function of BASIC_HUE_COUNT
	alone, nothing installable ever changes it)."""
	hues = []
	for i in range(BASIC_HUE_COUNT):
		r, g, b = colorsys.hsv_to_rgb(i / BASIC_HUE_COUNT, 0.85, 0.95)
		hues.append(f"#{int(r * 255):02x}{int(g * 255):02x}{int(b * 255):02x}")
	return hues


def _hsv_to_hex(h, s, v):
	r, g, b = colorsys.hsv_to_rgb(h, s, v)
	return f"#{int(r * 255):02x}{int(g * 255):02x}{int(b * 255):02x}"


def _hex_to_hsv(hex_color):
	hex_color = hex_color.lstrip("#")
	r = int(hex_color[0:2], 16) / 255
	g = int(hex_color[2:4], 16) / 255
	b = int(hex_color[4:6], 16) / 255
	return colorsys.rgb_to_hsv(r, g, b)


# The custom color picker's own three sliders, in Up/Down order - see
# AvatarCustomizePage.toggle_color_picker()/picker_adjust(). Hue steps
# in 10-degree increments (36 stops around the wheel); saturation/value
# step in plain 5% increments - both fine enough to land on a precise
# shade without needing an impractical number of presses to cross the
# whole 0..1 range.
PICKER_SLIDERS = ("hue", "saturation", "value")
PICKER_HUE_STEP = 1 / 36
PICKER_SV_STEP = 0.05


def field_option_values(field):
	"""The full, ordered list of previewable values for one CUSTOM_FIELDS
	row - what the options grid renders one thumbnail per entry of, and
	what _step_value()'s own index math steps through. A `bool` field is
	`[False, True]`; `multi_bool` (an accessory's own on/off membership
	in the shared list its "key" points at) is also `[False, True]` -
	the real list mutation happens in _step_value()/_thumbnail_traits(),
	not here. `slider` discretizes its own existing (lo, hi, step) range
	- the exact same step _step_value() already used before this, just
	enumerated up front instead of computed one step at a time.
	`color`/`color_opt` both use the shared _basic_hue_options() spread
	rather than the field's own "options" key (`color_opt` keeps its own
	synthetic leading None entry - the "match X" fallback - in front of
	it). `choice` is its own options list, as-is."""
	kind = field["kind"]
	if kind in ("bool", "multi_bool"):
		return [False, True]
	if kind == "slider":
		lo, hi, step = field["range"]
		steps = round((hi - lo) / step)
		return [round(lo + i * step, 2) for i in range(steps + 1)]
	if kind == "color_opt":
		return [None] + _basic_hue_options()
	if kind == "color":
		return _basic_hue_options()
	return list(field["options"])  # "choice"


def _pil_to_pixbuf(image):
	"""Same in-memory PIL -> GdkPixbuf conversion as avatar3d_portrait_
	anim.py's own _pil_to_pixbuf() - duplicated (not imported)
	specifically to avoid a needless cross-module dependency for one
	small, truly self-contained utility, matching that module's own
	precedent for why this is duplicated rather than shared."""
	data = image.tobytes()
	return GdkPixbuf.Pixbuf.new_from_bytes(GLib.Bytes.new(data), GdkPixbuf.Colorspace.RGB, True, 8, image.width, image.height, image.width * 4)


def _field_value_text(field, value):
	if field["kind"] in ("bool", "multi_bool"):
		# Same "on"/"off" either way - a real, confirmed-live bug fix:
		# multi_bool's own `value` here is already the resolved
		# membership bool (see field_option_values()/
		# AvatarCustomizePage._current_field_value(), the two real
		# callers - neither ever passes the raw list this used to check
		# `field["item"] in value` against), so that old check crashed
		# (`in` against a bool) the first time a multi_bool field's own
		# value text was actually rendered.
		return "on" if value else "off"
	if field["kind"] == "color_opt" and value is None:
		return field["none_label"]
	if field["kind"] == "choice" and field["options"] and isinstance(field["options"][0], list):
		return value[0].replace("_", " ") if value else "none"
	return str(value)


NAME_BOX_HEIGHT = scaling.px_v(30)
# Black, 0.85 alpha - per direct instruction ("put them in black
# transparency 0.85 boxes"), the same value theme.css's own
# @bg_scrim_heavy token already is (see box.ceremony-scrim/box.
# controller-help-scrim). Duplicated as a literal here rather than
# looked up from theme.css: confirmed live that a plain CSS class
# (background-color + a Gtk.Label child) silently does not paint at
# all when the widget is a Gtk.Overlay *overlay* child specifically
# (its "draw" signal still fires - a raw Cairo test rectangle drawn in
# the same handler rendered fine - but the widget's own default class
# handler, which would normally call gtk_render_background() for CSS,
# never painted anything, nor did the child Label's own CSS color/
# font-size). Painting the box and its text directly via Cairo/
# PangoCairo instead sidesteps that entirely, and matches this
# project's own standing convention anyway (GTK for UI, Python for
# drawing, CSS only for bare-minimum *static* styling) - the same
# reasoning that already moved this screen's own selection ring off
# CSS (see _draw_ring()).
_NAME_BOX_BG = (0.0, 0.0, 0.0, 0.85)
_NAME_BOX_FG = (1.0, 1.0, 1.0, 1.0)


def _draw_name_box(widget, cr, text):
	width = widget.get_allocated_width()
	height = widget.get_allocated_height()
	cr.save()
	cr.set_source_rgba(*_NAME_BOX_BG)
	cr.rectangle(0, 0, width, height)
	cr.fill()
	side_pad = scaling.px_h(6)
	layout = PangoCairo.create_layout(cr)
	layout.set_text(text, -1)
	font = Pango.FontDescription()
	font.set_family("Rajdhani")
	font.set_absolute_size(scaling.px(16) * Pango.SCALE)
	layout.set_font_description(font)
	layout.set_width(int((width - 2 * side_pad) * Pango.SCALE))
	layout.set_ellipsize(Pango.EllipsizeMode.END)
	layout.set_alignment(Pango.Alignment.CENTER)
	_text_w, text_h = layout.get_pixel_size()
	cr.set_source_rgba(*_NAME_BOX_FG)
	cr.move_to(side_pad, (height - text_h) / 2)
	PangoCairo.show_layout(cr, layout)
	cr.restore()
	return False


def _make_named_thumbnail(image, text):
	"""Wraps `image` (already sized/populated) in a Gtk.Overlay with
	`text` sitting in a translucent black box anchored along the bottom
	edge - per direct instruction ("put them in black transparency 0.85
	boxes"). The box and its text are both Cairo/PangoCairo-drawn (see
	_draw_name_box()), not a CSS-styled Gtk.Label - see that function's
	own docstring for the confirmed-live reason a GTK-widget/CSS version
	of this didn't actually render at all."""
	# NOTE: the caller's own Gtk.FlowBoxChild must set
	# valign=Gtk.Align.START (see _make_option_tile()/_make_outfit_tile())
	# - a real, confirmed-live bug: GtkFlowBoxChild defaults to
	# valign=FILL, and a homogeneous FlowBox with only one short row
	# inside a taller-than-needed ScrolledWindow (see options_scroller's
	# own expand=True packing) stretches that one row - and every cell
	# in it - to fill the whole leftover height (~300px instead of the
	# tile's own ~64px) regardless of any vexpand flag set here; only
	# forcing the FlowBoxChild itself to START (not FILL) stops it.
	overlay = Gtk.Overlay()
	overlay.add(image)
	name_box = Gtk.Box()
	name_box.set_size_request(-1, NAME_BOX_HEIGHT)
	name_box.set_valign(Gtk.Align.END)
	name_box.set_halign(Gtk.Align.FILL)
	name_box.connect_after("draw", _draw_name_box, text)
	overlay.add_overlay(name_box)
	overlay.set_overlay_pass_through(name_box, True)
	return overlay


class AvatarCustomizePage(Gtk.Box):
	"""Construct once, reuse across every "Change Astronar"/"Choose a
	Picture" trip (start() resets it to whichever profile's traits are
	being edited each time) - the same reuse-one-page convention
	media/vlc_player.py's own PlayerPage already established."""

	def __init__(self, on_confirm):
		super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=scaling.px_v(14))
		self.on_confirm = on_confirm
		self.set_border_width(scaling.px_v(22))
		self.traits = dict(CUSTOM_DEFAULTS)
		self.field_cursor = 0
		self.model_heading = DEFAULT_HEADING
		self.model_pitch = 0.0
		self.model_zoom = 1.0
		self.rig = None
		self.pet_rig = None
		self._tick_source_id = None
		self._t0 = 0.0
		# Which real profile (if any) start()'s own traits belong to -
		# None for the brand new NEW_PROFILE flow (see app.py's
		# open_avatar_picker()), the one case with no real profile
		# directory yet to load/save outfits.json against (matching how
		# confirm_avatar_traits()'s own staged-traits handling treats that
		# same case - nothing persisted until the profile actually exists).
		# Saved outfits still work in-memory for that session either way,
		# just never touch disk until the profile is real.
		self.profile_name = None
		self._outfits = []
		# Whether keyboard focus is on the saved-outfit row instead of the
		# field/options grid - the two share this one screen's Up/Down/
		# Left/Right/Confirm/Delete keys, the same one-flat-input-model
		# every other screen in this app uses rather than real GTK widget
		# focus (see _make_option_tile()'s own comment on why). See
		# field_next()/field_prev() for the actual crossover.
		self.outfit_focused = False
		self._selected_outfit_index = 0
		# Whether the currently-highlighted field row is expanded into
		# its own full options grid, or just a compact collapsed row -
		# per direct feedback ("dropdowns that open on selection, then
		# close on the next selection of a dropdown would work a fuck
		# ton better"). See _open_dropdown()/close_dropdown().
		self.field_expanded = False
		self.field_row_widgets = []

		# A plain styled Gtk.Label, not app.py's own make_heading() - this
		# page is built and used the same way media/vlc_player.py's
		# PlayerPage is (constructed once by app.py, reused across every
		# trip), and that file's own precedent is to build its heading/
		# title locally rather than reach back into app.py for it, which
		# would be a real import cycle (app.py imports this module at the
		# top level).
		heading = Gtk.Label(label="Customize Astronar", xalign=0)
		heading.get_style_context().add_class("title")
		self.pack_start(heading, False, False, 0)

		# Three-column layout - left: category list, center: live preview
		# + saved-outfit row, right: current field's own options grid -
		# per your own Sims 3/4 Create-A-Sim reference screenshots and
		# corrections ("use this picture as a reference for the type of
		# menu layout i want", "though i want it on the right").
		content_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=scaling.px_h(24))
		self.pack_start(content_row, True, True, 0)

		# Left column: category list - a real vertical list (Body/Face/
		# Clothing/...) instead of the old horizontal tab row, per the
		# same reference screenshots. _refresh_tabs()/category_prev()/
		# category_next() need zero changes - they only ever toggle the
		# "avatar-tab-active" CSS class on a category label, regardless
		# of whether this box is laid out horizontally or vertically.
		self.tab_row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=scaling.px_v(8))
		self.tab_row.set_valign(Gtk.Align.CENTER)
		self.tab_labels = {}
		# CATEGORIES is already fully populated by the module-level
		# refresh_custom_fields() call at import time (before this class
		# is ever constructed - see app.py's own AvatarCustomizePage()
		# call site), so this sees every base AND plugin-contributed
		# category that exists as of process start. A "bodypart" plugin
		# that introduces a brand new category and gets installed WHILE
		# the app is already running (no real install flow exists yet to
		# do this) wouldn't get a tab here without restarting - start()'s
		# own refresh_custom_fields() call keeps CUSTOM_FIELDS/
		# CUSTOM_DEFAULTS current either way, this tab row just isn't
		# rebuilt on every entry the same way those are.
		for category in CATEGORIES:
			label = Gtk.Label(label=category, xalign=0)
			label.get_style_context().add_class("avatar-tab")
			self.tab_labels[category] = label
			self.tab_row.pack_start(label, False, False, 0)
		content_row.pack_start(self.tab_row, False, False, 0)

		# Center column: the live preview, large and dominant, with the
		# saved-outfit slot row packed underneath it.
		center_column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=scaling.px_v(14))
		center_column.set_halign(Gtk.Align.CENTER)
		content_row.pack_start(center_column, True, True, 0)

		self.image = Gtk.Image()
		self.image.set_size_request(PREVIEW_SIZE, PREVIEW_SIZE)
		self.image.set_halign(Gtk.Align.CENTER)
		center_column.pack_start(self.image, False, False, 0)

		# Saved outfit slots - up to 5, flat (no time-of-day/outfit-
		# category scheme, an explicit rejection of Sims 4's own
		# Everyday/Formal/Athletic/Sleepwear/Party row: "multiple
		# outfits are fine, 5 max. but not based on times of day or
		# anything"). Reached via Down once field_cursor is already on
		# the very last field (crossing over - see field_next()), Up
		# from here crosses back (field_prev()) - the same crossover
		# CONVENTION already proven this session for the Dashboard's own
		# Astronar badge, just expressed through this screen's own flat
		# field_cursor model instead of a second real FlowBox to move
		# within (this screen's grids never use real GTK focus at all -
		# see _make_option_tile()'s own comment on why).
		self.outfit_flow = Gtk.FlowBox()
		self.outfit_flow.set_selection_mode(Gtk.SelectionMode.NONE)
		self.outfit_flow.set_min_children_per_line(MAX_OUTFIT_SLOTS)
		self.outfit_flow.set_max_children_per_line(MAX_OUTFIT_SLOTS)
		self.outfit_flow.set_row_spacing(scaling.px_v(8))
		self.outfit_flow.set_column_spacing(scaling.px_h(8))
		self.outfit_flow.set_halign(Gtk.Align.CENTER)
		self.outfit_flow.set_can_focus(False)
		center_column.pack_start(self.outfit_flow, False, False, 0)

		# Right column: a scrollable list of compact, collapsed field
		# rows (one per field of the current category) - moved here from
		# directly under the preview, per direct correction ("though i
		# want it on the right"). Replaces the old "always show exactly
		# one field's own name + options grid" layout entirely, per
		# direct feedback that Up/Down-ing through the whole flat field
		# list one at a time (rebuilding a full grid every single press)
		# was "a pain in the ass" on both keyboard and controller -
		# see field_prev()/field_next()/_open_dropdown()'s own docstrings
		# for the new dropdown-per-field interaction model this enables.
		right_column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=scaling.px_v(10))
		right_column.set_size_request(scaling.px_h(420), -1)
		content_row.pack_start(right_column, False, False, 0)

		self.field_rows_scroller = Gtk.ScrolledWindow()
		self.field_rows_scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
		self.field_rows_scroller.set_size_request(-1, OPTIONS_SCROLLER_HEIGHT)
		self.field_rows_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=scaling.px_v(6))
		self.field_rows_scroller.add(self.field_rows_box)
		right_column.pack_start(self.field_rows_scroller, True, True, 0)

		# The options grid - a real thumbnail per possible value of the
		# current field, each one rendered with that value applied to
		# avatar3d_viewer.NEUTRAL_TRAITS's own shared "naked grey
		# Astronar" backdrop (see avatar3d_thumbnail_cache.py), replacing
		# the old plain "‹ value ›" text-only row entirely per direct
		# instruction ("everything has thumbnails... on a naked grey
		# Astronar"). A true multi-row Gtk.FlowBox (no
		# set_min/max_children_per_line - wraps naturally, same
		# convention app.py's own Games library cover-art grid uses),
		# not a single scrolling row.
		#
		# Built here but deliberately NOT packed into anything yet -
		# this one shared widget only ever gets pack_start()'d into
		# field_rows_box (directly under whichever row is currently
		# open) by _open_dropdown(), and remove()'d by close_dropdown().
		# Nothing about how it's built or how its own children work
		# needs to know where it currently lives.
		#
		# Left/Right (value_prev()/value_next()) still just step ONE
		# flat index through the field's own option list exactly like
		# before _step_value() ever grew a grid to show it in - the only
		# thing that changed is that index now visibly moves a
		# highlighted cell through this grid in raster (reading) order,
		# instead of silently changing invisible text. This deliberately
		# never asks GTK for real 2D (row-aware) arrow-key navigation -
		# nothing in this app has that proven safe (see
		# _move_within_flow()'s own comment on why native FlowBox
		# arrow-key handling isn't trusted here at all), so the grid is
		# purely a LAYOUT (cells wrap into rows), never asked to handle
		# its own selection/focus.
		#
		# The selection ring is painted directly via Cairo on each
		# cell's own "draw" signal (see _draw_option_ring()), not CSS -
		# per direct instruction ("we don't use css for drawing... css
		# for bare minimum"), matching how the Dashboard's own Astronar
		# badge focus ring is painted (see app.py's
		# _draw_astronar_badge_ring()) after CSS genuinely could not
		# override GtkFlowBoxChild's own native focus rendering there.
		self.options_scroller = Gtk.ScrolledWindow()
		self.options_scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
		self.options_scroller.set_size_request(-1, OPTIONS_SCROLLER_HEIGHT)
		self.options_flow = Gtk.FlowBox()
		self.options_flow.set_selection_mode(Gtk.SelectionMode.NONE)
		self.options_flow.set_homogeneous(True)
		self.options_flow.set_row_spacing(scaling.px_v(10))
		self.options_flow.set_column_spacing(scaling.px_h(10))
		self.options_flow.set_halign(Gtk.Align.CENTER)
		self.options_flow.set_can_focus(False)
		self.options_scroller.add(self.options_flow)
		self._selected_option_index = 0

		# The custom color picker - Delete on a color/color_opt field's
		# own open dropdown swaps the shared basic-hue grid out for this
		# instead (see toggle_color_picker()), reaching any color at all
		# rather than just the 12-hue spread, per direct request ("add
		# the custom color widget"). Three Cairo-drawn gradient bars
		# (Hue/Saturation/Value - see _draw_picker_bar()), not a 2D hue-
		# bar-plus-saturation/value-square the way avatar3d_previewer.py's
		# own standalone picker works - that tool is mouse-drag-driven;
		# this screen's own established "a short list of rows, Up/Down
		# picks one, Left/Right adjusts it" interaction (the exact shape
		# CUSTOM_FIELDS' own field list already uses) reuses proven
		# navigation instead of inventing 2D pointer-free square
		# navigation from scratch. Built once, reused across every field
		# the same way options_scroller itself is - never packed
		# anywhere at construction time, only via toggle_color_picker().
		self.color_picker_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=scaling.px_v(8))
		self.color_picker_hex_label = Gtk.Label(xalign=0.5)
		self.color_picker_hex_label.get_style_context().add_class("title")
		self.color_picker_box.pack_start(self.color_picker_hex_label, False, False, 0)
		self.picker_bars = {}
		for name, label_text in (("hue", "Hue"), ("saturation", "Saturation"), ("value", "Brightness")):
			slider_row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=scaling.px_v(2))
			slider_row.slider_name = name
			slider_label = Gtk.Label(label=label_text, xalign=0)
			slider_label.get_style_context().add_class("dim-label")
			slider_row.pack_start(slider_label, False, False, 0)
			bar = Gtk.DrawingArea()
			bar.set_size_request(-1, scaling.px_v(28))
			bar.name_key = name
			bar.connect("draw", self._draw_picker_bar)
			slider_row.pack_start(bar, False, False, 0)
			slider_row.connect_after("draw", self._draw_picker_slider_ring)
			self.picker_bars[name] = bar
			self.color_picker_box.pack_start(slider_row, False, False, 0)
		picker_hint = controller_glyphs.make_hint_row(
			"Up/Down: slider   Left/Right: adjust   ",
			controller_glyphs.DELETE,
			": back to swatches   ",
			controller_glyphs.CONFIRM,
			": use this color",
		)
		self.color_picker_box.pack_start(picker_hint, False, False, 0)
		self.color_picker_active = False
		self.picker_slider_index = 0
		self._picker_hue = 0.0
		self._picker_s = 0.0
		self._picker_v = 0.0

		self.position_label = Gtk.Label(xalign=0.5)
		self.position_label.get_style_context().add_class("dim-label")
		self.position_label.set_halign(Gtk.Align.CENTER)
		self.pack_start(self.position_label, False, False, 0)

		hint = controller_glyphs.make_hint_row(
			"Up/Down: trait   Left/Right: open/change   ",
			controller_glyphs.SHOULDER_LEFT,
			controller_glyphs.SHOULDER_RIGHT,
			": category   ",
			controller_glyphs.TRIGGER_LEFT,
			controller_glyphs.TRIGGER_RIGHT,
			": rotate   ",
			controller_glyphs.CONFIRM,
			": use this picture / choose outfit   ",
			controller_glyphs.DELETE,
			": save outfit   Backspace: cancel",
		)
		hint.set_halign(Gtk.Align.CENTER)
		self.pack_start(hint, False, False, 0)

	def start(self, initial_traits, profile_name=None):
		"""Called every time this page is entered (see app.py's
		open_avatar_picker()) - initial_traits is either an existing
		profile's own saved traits (profiles.get_picture_traits()) or
		CUSTOM_DEFAULTS for a brand new one, never None (callers resolve
		that fallback themselves, matching how every other picture-
		reading call site in app.py already treats a None traits dict as
		"nothing chosen yet, use the default look"). profile_name is None
		for the brand-new-profile flow (see this class's own profile_name
		docstring in __init__) - the one case saved outfits stay in-memory
		only for this session, since there's no real profile directory yet
		to persist outfits.json into.

		refresh_custom_fields() re-runs on every entry (cheap - a couple
		of list/dict rebuilds, not a real cost) so a "bodypart" plugin
		installed since this screen was last open (or since the app
		started) shows up here without needing a restart - see that
		function's own docstring. avatar3d_anim.refresh_plugin_motions()
		is the same idea for "motion" plugins/features - see its own
		docstring for why play() needs a cache at all."""
		refresh_custom_fields()
		avatar3d_anim.refresh_plugin_motions()
		self.traits = dict(initial_traits)
		self.field_cursor = 0
		self.model_heading = DEFAULT_HEADING
		self.model_pitch = 0.0
		self.model_zoom = 1.0
		self.profile_name = profile_name
		self.outfit_focused = False
		self._selected_outfit_index = 0
		self.field_expanded = False
		self.color_picker_active = False
		self._outfits = profiles.get_outfits(profile_name) if profile_name else []
		self._t0 = time.time()
		self._rebuild_rig()
		self._rebuild_field_rows()
		self._rebuild_outfit_row()
		if self._tick_source_id is None:
			self._tick_source_id = GLib.timeout_add(int(1000 / FPS), self._tick)

	def stop(self):
		if self._tick_source_id is not None:
			GLib.source_remove(self._tick_source_id)
			self._tick_source_id = None
		if self.rig is not None:
			self.rig.root.removeNode()
			self.rig = None
		if self.pet_rig is not None:
			self.pet_rig.root.removeNode()
			self.pet_rig = None

	def get_traits(self):
		return dict(self.traits)

	def rotate_left(self):
		self.model_heading -= HEADING_STEP
		self._apply_camera()

	def rotate_right(self):
		self.model_heading += HEADING_STEP
		self._apply_camera()

	def orbit_up(self):
		self.model_pitch = min(PITCH_MAX, self.model_pitch + PITCH_STEP)
		self._apply_camera()

	def orbit_down(self):
		self.model_pitch = max(PITCH_MIN, self.model_pitch - PITCH_STEP)
		self._apply_camera()

	def zoom_in(self):
		self.model_zoom = min(ZOOM_MAX, self.model_zoom + ZOOM_STEP)
		self._apply_camera()

	def zoom_out(self):
		self.model_zoom = max(ZOOM_MIN, self.model_zoom - ZOOM_STEP)
		self._apply_camera()

	def reset_camera(self):
		"""Home - ported straight from avatar3d_previewer.py's own
		reset_camera() (same key, same "snap back to the known-good
		framing in one press" reasoning), just resetting the rig's own
		heading/pitch/zoom here instead of that tool's free-flying
		camera - see this class's own _apply_camera()."""
		self.model_heading = DEFAULT_HEADING
		self.model_pitch = 0.0
		self.model_zoom = 1.0
		self._apply_camera()

	def _apply_camera(self):
		"""Re-orients/rescales the already-built rig/pet_rig nodes in
		place (no rebuild - none of heading/pitch/zoom ever change the
		mesh itself) - orbit/zoom key handlers call this directly, and
		_rebuild_rig() calls it once after building fresh nodes so a
		camera change set before a value change (which does rebuild)
		isn't lost. Was _apply_heading() (setH only) before WASD orbit/
		zoom were ported over from avatar3d_previewer.py's own standalone
		dev tool per direct instruction."""
		for root in (self.rig.root if self.rig else None, self.pet_rig.root if self.pet_rig else None):
			if root is not None:
				root.setH(self.model_heading)
				root.setP(self.model_pitch)
				root.setScale(self.model_zoom)

	def field_prev(self):
		"""Up - closes any open dropdown (see close_dropdown()) and moves
		to the previous field WITHIN THE CURRENT CATEGORY ONLY (wraps
		there, never crosses into another category - L1/R1 stay the only
		way to do that, see category_prev()/category_next()); cheap, just
		moves which row's ring is drawn, no thumbnail/grid work at all
		unless a dropdown needs closing first. Moving off the saved-
		outfit row back onto the field list always lands back on
		field_cursor's own last value, never a real decrement of it -
		field_cursor never actually changed while outfit_focused was
		True, only which region was highlighted did."""
		if self.outfit_focused:
			self.outfit_focused = False
			self._refresh_outfit_focus()
			return
		if self.field_expanded:
			self.close_dropdown()
		positions = [i for i, _f in self._category_fields(CUSTOM_FIELDS[self.field_cursor]["category"])]
		pos = (positions.index(self.field_cursor) - 1) % len(positions)
		self.field_cursor = positions[pos]
		self._update_row_selection()

	def field_next(self):
		"""Down - the mirror of field_prev(): closes any open dropdown,
		then crosses over to the saved-outfit row once already on the
		CURRENT CATEGORY's own last row instead of wrapping back to that
		category's own first row - the same directional-crossover
		convention already proven this session for the Dashboard's own
		Astronar badge, now reachable from every category's own end
		rather than being buried behind one arbitrary globally-last
		field. A further Down press while already on the outfit row is a
		no-op - there's no third region below it."""
		if self.outfit_focused:
			return
		if self.field_expanded:
			self.close_dropdown()
		positions = [i for i, _f in self._category_fields(CUSTOM_FIELDS[self.field_cursor]["category"])]
		pos = positions.index(self.field_cursor)
		if pos == len(positions) - 1:
			self.outfit_focused = True
			self._refresh_outfit_focus()
			return
		self.field_cursor = positions[pos + 1]
		self._update_row_selection()

	def category_prev(self):
		if self.field_expanded:
			self.close_dropdown()
		self.outfit_focused = False
		self._jump_category(-1)
		self._refresh_outfit_focus()

	def category_next(self):
		if self.field_expanded:
			self.close_dropdown()
		self.outfit_focused = False
		self._jump_category(1)
		self._refresh_outfit_focus()

	def _jump_category(self, direction):
		"""Jumps field_cursor to the first field of the previous/next
		CATEGORY (L1/R1 - see this class's own hint row) - CUSTOM_FIELDS
		is one flat ~90-field list (Up/Down alone would mean stepping
		through all of them one at a time to get anywhere), grouped into
		CATEGORIES-ordered runs, so "first field whose own category
		differs from the current one, walking in `direction`" is exactly
		"jump to the start of the next/previous section" without needing
		a second, separately-maintained index structure."""
		current_category = CUSTOM_FIELDS[self.field_cursor]["category"]
		n = len(CUSTOM_FIELDS)
		i = self.field_cursor
		# Walk off the current category first (a field_cursor already
		# sitting mid-category, not just group-start, must not just
		# re-land on its own group's own start again).
		while CUSTOM_FIELDS[(i + direction) % n]["category"] == current_category:
			i += direction
		i += direction
		target_category = CUSTOM_FIELDS[i % n]["category"]
		# Walk to the actual start of that category when moving forward
		# (direction=1 already lands there); moving backward lands on
		# its own LAST field first, so walk further back to that
		# category's own first field instead - "previous category"
		# should mean "the start of it", not "its last field".
		if direction < 0:
			while CUSTOM_FIELDS[(i - 1) % n]["category"] == target_category:
				i -= 1
		self.field_cursor = i % n
		self._rebuild_field_rows()

	def value_prev(self):
		if not self.field_expanded:
			self._open_dropdown()
		self._step_value(-1)

	def value_next(self):
		if not self.field_expanded:
			self._open_dropdown()
		self._step_value(1)

	def confirm(self):
		self.on_confirm(self.get_traits())

	def _step_value(self, delta):
		"""Steps the current field's value by one position, wrapping at
		either end - `multi_bool` stays its own real special case (it
		toggles membership in the SHARED list its "key" points at, not a
		scalar replacement), every other kind now routes through the
		same field_option_values() list the options grid itself renders
		one thumbnail per entry of, so the two can never disagree about
		what "next option" means."""
		field = CUSTOM_FIELDS[self.field_cursor]
		key, kind = field["key"], field["kind"]
		if kind == "multi_bool":
			item = field["item"]
			active = list(self.traits[key])
			if item in active:
				active.remove(item)
			else:
				active.append(item)
			self.traits[key] = active
		else:
			options = field_option_values(field)
			try:
				index = options.index(self.traits[key])
			except ValueError:
				index = 0
			self.traits[key] = options[(index + delta) % len(options)]
		self._rebuild_rig()
		self._update_selection()
		self._refresh_current_row()

	def _rebuild_rig(self):
		if self.rig is not None:
			self.rig.root.removeNode()
		if self.pet_rig is not None:
			self.pet_rig.root.removeNode()
			self.pet_rig = None
		log.debug(f"AvatarCustomizePage rebuild: {self.traits}")
		# avatar3d_rig.build_rig() doesn't take pet_species/pet_color at
		# all - the pet is a fully separate PetRig (see avatar3d_pet_rig's
		# own docstring on why: it needs its own independent idle
		# animation, not something baked into the person's one static
		# mesh the way build_character()'s own pet handling works) -
		# popped from a COPY so self.traits itself (what confirm() hands
		# back to profiles.set_picture_traits(), and what render_portrait()
		# elsewhere in the app reads) keeps both keys.
		human_traits = {k: v for k, v in self.traits.items() if k not in ("pet_species", "pet_color")}
		self.rig = avatar3d_rig.build_rig(**human_traits)
		self.rig.root.reparentTo(avatar3d.get_renderer().render)
		pet_species = self.traits.get("pet_species", "none")
		if pet_species != "none":
			self.pet_rig = avatar3d_pet_rig.build_pet_rig(species=pet_species, color=self.traits.get("pet_color", "#8b5e3c"))
			self.pet_rig.root.reparentTo(avatar3d.get_renderer().render)
			# Same hip_half_w formula build_character() itself derives
			# (see that function's own identical local) - recomputed here
			# rather than threaded out of build_rig(), the same "duplicate
			# the one relevant derived number" tradeoff avatar3d_rig.py's
			# own finger/thumb pivot math already accepts - so the pet
			# stands just clear of the person's own hips regardless of
			# weight/shape/gender/age, not a fixed offset that could clip
			# into a wide character or leave a gap next to a narrow one.
			gmod = avatar3d.GENDER_MODS.get(self.traits["gender"], avatar3d.GENDER_MODS["nonbinary"])
			weight_scale = 0.82 + self.traits["weight"] * 0.55
			_head_scale, body_scale = avatar3d._age_body_scale(self.traits["age"])
			hip_half_w = (0.28 + self.traits["shape"] * 0.14 + gmod["hip"]) * weight_scale * body_scale
			self.pet_rig.root.setX(hip_half_w + 0.45)
		self._apply_camera()

	def _category_fields(self, category):
		"""[(global_index, field), ...] for every CUSTOM_FIELDS entry in
		`category`, in original list order - the basis for both the
		compact row list (_rebuild_field_rows()) and category-scoped
		Up/Down movement (field_prev()/field_next(), via the INDICES
		only - never comparing field dicts by value, so there's no
		ambiguity even if two fields ever happened to be equal by
		content)."""
		return [(i, f) for i, f in enumerate(CUSTOM_FIELDS) if f["category"] == category]

	def _current_field_value(self, field):
		if field["kind"] == "multi_bool":
			return field["item"] in self.traits[field["key"]]
		return self.traits[field["key"]]

	def _update_position_label(self):
		category = CUSTOM_FIELDS[self.field_cursor]["category"]
		positions = [i for i, _f in self._category_fields(category)]
		self.position_label.set_text(f"{positions.index(self.field_cursor) + 1} / {len(positions)}")

	def _rebuild_field_rows(self):
		"""Tears down and rebuilds every compact row in the right column
		for field_cursor's own current category - called on start(), a
		category jump, or after loading a saved outfit (outfit_confirm()
		can silently change several Clothing/Accessories fields' own
		values, so any rows currently shown for them need to catch up).
		Closes any open dropdown first - a fresh row list always starts
		collapsed. Replaces the old _refresh_labels()'s job of rebuilding
		the one active field's own grid - see _open_dropdown() for where
		that now happens instead."""
		if self.field_expanded:
			self.close_dropdown()
		for child in list(self.field_rows_box.get_children()):
			self.field_rows_box.remove(child)
		self.field_row_widgets = []
		field = CUSTOM_FIELDS[self.field_cursor]
		category = field["category"]
		self._refresh_tabs(category)
		for _i, f in self._category_fields(category):
			row = self._make_field_row(f)
			self.field_row_widgets.append(row)
			self.field_rows_box.pack_start(row, False, False, 0)
		self.field_rows_box.show_all()
		self._update_position_label()
		GLib.idle_add(self._scroll_selected_row_into_view)

	def _update_row_selection(self):
		"""The lightweight sibling of _rebuild_field_rows() (mirrors
		_update_selection()'s own relationship to _rebuild_options_grid())
		- the field SET shown hasn't changed, only which one is
		highlighted has (plain Up/Down within the same category), so
		this just moves the ring and the position label, no rebuild and
		no thumbnail work at all."""
		for row in self.field_row_widgets:
			row.queue_draw()
		self._update_position_label()
		GLib.idle_add(self._scroll_selected_row_into_view)

	def _make_field_row(self, field):
		"""One compact collapsed row - a small thumbnail of the field's
		own CURRENT value (avatar3d_thumbnail_cache.get_thumbnail(), the
		same neutral-base cache the options grid's own tiles already
		use, just resized smaller) plus its label and current value text
		(_field_value_text(), the same helper the black-box thumbnail
		captions already use). row.field stores the field dict (like
		cell.index already does on grid tiles) so _draw_field_row_ring()/
		_refresh_current_row()/_scroll_selected_row_into_view() can find
		their own row again without a separately-maintained index."""
		row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=scaling.px_h(10))
		row.field = field
		thumb = Gtk.Image()
		thumb.set_size_request(ROW_THUMB_SIZE, ROW_THUMB_SIZE)
		row.thumb_widget = thumb
		self._set_row_thumbnail(row)
		row.pack_start(thumb, False, False, 0)
		text_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
		label = Gtk.Label(label=field["label"], xalign=0)
		value_label = Gtk.Label(xalign=0)
		value_label.get_style_context().add_class("dim-label")
		value_label.set_text(_field_value_text(field, self._current_field_value(field)))
		row.value_label = value_label
		text_box.pack_start(label, False, False, 0)
		text_box.pack_start(value_label, False, False, 0)
		row.pack_start(text_box, True, True, 0)
		row.connect_after("draw", self._draw_field_row_ring)
		return row

	def _set_row_thumbnail(self, row):
		value = self._current_field_value(row.field)
		thumbnail = avatar3d_thumbnail_cache.get_thumbnail(row.field, value)
		if thumbnail is not None:
			resized = thumbnail.resize((ROW_THUMB_SIZE, ROW_THUMB_SIZE))
			row.thumb_widget.set_from_pixbuf(_pil_to_pixbuf(resized))

	def _draw_field_row_ring(self, widget, cr):
		if self.outfit_focused or widget.field is not CUSTOM_FIELDS[self.field_cursor]:
			return False
		_draw_ring(widget, cr)
		return False

	def _refresh_current_row(self):
		"""Updates field_cursor's own row's thumbnail/value text in
		place - called after a value actually changes (_step_value()) or
		a dropdown closes (close_dropdown()), so the collapsed row shows
		what was just picked without a full _rebuild_field_rows()."""
		field = CUSTOM_FIELDS[self.field_cursor]
		for row in self.field_row_widgets:
			if row.field is field:
				self._set_row_thumbnail(row)
				row.value_label.set_text(_field_value_text(field, self._current_field_value(field)))
				break

	def _scroll_selected_row_into_view(self):
		"""Same purely-Python scroll-into-view technique as
		_scroll_selected_into_view() (the options grid's own version) -
		this grid's rows are never really focused at all either. When
		this row's own dropdown is open, the visible span includes the
		options grid packed in right after it too - opening a dropdown
		on a row near the bottom of a long list must scroll far enough
		to actually reveal the grid, not just the row header above it."""
		field = CUSTOM_FIELDS[self.field_cursor]
		row = next((r for r in self.field_row_widgets if r.field is field), None)
		if row is None:
			return False
		alloc = row.get_allocation()
		height = alloc.height
		if self.field_expanded and self.options_scroller.get_parent() is self.field_rows_box:
			height += self.options_scroller.get_allocation().height
		vadj = self.field_rows_scroller.get_vadjustment()
		if alloc.y < vadj.get_value():
			vadj.set_value(alloc.y)
		elif alloc.y + height > vadj.get_value() + vadj.get_page_size():
			vadj.set_value(alloc.y + height - vadj.get_page_size())
		return False

	def _open_dropdown(self):
		"""Expands field_cursor's own row into the full options grid,
		inserted directly below it in field_rows_box - the "open" half
		of the open+step gesture (see value_prev()/value_next(), the
		only call sites). Reuses _rebuild_options_grid() entirely
		unmodified - it only ever touches options_flow's own children
		and never assumes anything about options_scroller's parent."""
		field = CUSTOM_FIELDS[self.field_cursor]
		self.field_expanded = True
		self._rebuild_options_grid(field)
		if self.options_scroller.get_parent() is not None:
			self.options_scroller.get_parent().remove(self.options_scroller)
		self.field_rows_box.pack_start(self.options_scroller, False, False, 0)
		row = next((r for r in self.field_row_widgets if r.field is field), None)
		if row is not None:
			children = self.field_rows_box.get_children()
			self.field_rows_box.reorder_child(self.options_scroller, children.index(row) + 1)
		self.options_scroller.show_all()
		GLib.idle_add(self._scroll_selected_row_into_view)

	def close_dropdown(self):
		"""Collapses an open dropdown back to a compact row (see
		_open_dropdown()'s own docstring) - public (not `_`-prefixed)
		since app.py's own Confirm-while-open case needs to call this
		directly (see on_key's avatar_picker dispatch). Cleans up the
		custom color picker too if that's what's actually showing (see
		toggle_color_picker()) - closing the whole dropdown while it's
		open must not leave it attached to a row that no longer exists
		once a different field/category is reached."""
		if self.color_picker_active:
			if self.color_picker_box.get_parent() is not None:
				self.field_rows_box.remove(self.color_picker_box)
			self.color_picker_active = False
		elif self.options_scroller.get_parent() is not None:
			self.field_rows_box.remove(self.options_scroller)
		self.field_expanded = False
		self._refresh_current_row()

	def field_is_color(self):
		"""Whether field_cursor's own field can offer the custom color
		picker at all (Delete on its open dropdown - see
		toggle_color_picker()) - only "color"/"color_opt" fields have
		one; app.py's own on_key checks this before wiring Delete to it,
		the same way it already checks page.outfit_focused before
		wiring Delete to outfit_save()."""
		return CUSTOM_FIELDS[self.field_cursor]["kind"] in ("color", "color_opt")

	def toggle_color_picker(self):
		"""Delete on a color/color_opt field's own open dropdown - swaps
		the shared basic-hue grid out for a real H/S/V picker (three
		Cairo-drawn gradient bars - see _draw_picker_bar()) reaching any
		color at all, not just the 12-hue spread, and back again on a
		second press. Per direct request ("add the custom color
		widget")."""
		field = CUSTOM_FIELDS[self.field_cursor]
		row = next((r for r in self.field_row_widgets if r.field is field), None)
		leaving_widget = self.color_picker_box if self.color_picker_active else self.options_scroller
		entering_widget = self.options_scroller if self.color_picker_active else self.color_picker_box
		if leaving_widget.get_parent() is not None:
			self.field_rows_box.remove(leaving_widget)
		self.color_picker_active = not self.color_picker_active
		if self.color_picker_active:
			options = field_option_values(field)
			non_none = [o for o in options if o is not None]
			current = self._current_field_value(field) or (non_none[0] if non_none else "#ffffff")
			self._picker_hue, self._picker_s, self._picker_v = _hex_to_hsv(current)
			self.picker_slider_index = 0
			self._refresh_color_picker()
		else:
			self._rebuild_options_grid(field)
		self.field_rows_box.pack_start(entering_widget, False, False, 0)
		if row is not None:
			children = self.field_rows_box.get_children()
			self.field_rows_box.reorder_child(entering_widget, children.index(row) + 1)
		entering_widget.show_all()
		GLib.idle_add(self._scroll_selected_row_into_view)

	def picker_slider_prev(self):
		self.picker_slider_index = (self.picker_slider_index - 1) % len(PICKER_SLIDERS)
		self._refresh_color_picker()

	def picker_slider_next(self):
		self.picker_slider_index = (self.picker_slider_index + 1) % len(PICKER_SLIDERS)
		self._refresh_color_picker()

	def picker_adjust(self, delta):
		"""Left/Right while the custom color picker is open - steps
		whichever of Hue/Saturation/Value is currently selected
		(picker_slider_index/PICKER_SLIDERS), applies the resulting hex
		straight to the live character the same way _step_value() always
		has (every value change in this whole screen previews live)."""
		name = PICKER_SLIDERS[self.picker_slider_index]
		if name == "hue":
			self._picker_hue = (self._picker_hue + delta * PICKER_HUE_STEP) % 1.0
		elif name == "saturation":
			self._picker_s = max(0.0, min(1.0, self._picker_s + delta * PICKER_SV_STEP))
		else:
			self._picker_v = max(0.0, min(1.0, self._picker_v + delta * PICKER_SV_STEP))
		field = CUSTOM_FIELDS[self.field_cursor]
		self.traits[field["key"]] = _hsv_to_hex(self._picker_hue, self._picker_s, self._picker_v)
		self._rebuild_rig()
		self._refresh_color_picker()
		self._refresh_current_row()

	def _refresh_color_picker(self):
		self.color_picker_hex_label.set_text(_hsv_to_hex(self._picker_hue, self._picker_s, self._picker_v))
		for bar in self.picker_bars.values():
			bar.queue_draw()

	def _draw_picker_bar(self, widget, cr):
		"""Paints one H/S/V slider as a real gradient (24 flat steps,
		not a true multi-stop Cairo gradient - plenty smooth at this
		widget's own size, and keeps this in the same "plain per-pixel-
		ish Cairo fill" style _draw_ring()/_draw_name_box() already use)
		plus a white-over-black marker line at the slider's current
		value, the same two-tone marker styling avatar3d_previewer.py's
		own hue bar/SV square markers settled on for staying visible
		against light AND dark backgrounds alike."""
		name = widget.name_key
		width = widget.get_allocated_width()
		height = widget.get_allocated_height()
		steps = 24
		for i in range(steps):
			t = i / (steps - 1)
			if name == "hue":
				h, s, v = t, 1.0, 1.0
			elif name == "saturation":
				h, s, v = self._picker_hue, t, self._picker_v
			else:
				h, s, v = self._picker_hue, self._picker_s, t
			r, g, b = colorsys.hsv_to_rgb(h, s, v)
			cr.set_source_rgb(r, g, b)
			x0 = i / steps * width
			x1 = (i + 1) / steps * width
			cr.rectangle(x0, 0, x1 - x0 + 1, height)
			cr.fill()
		value = {"hue": self._picker_hue, "saturation": self._picker_s, "value": self._picker_v}[name]
		x = value * width
		cr.set_source_rgb(0, 0, 0)
		cr.rectangle(x - 3, 0, 6, height)
		cr.fill()
		cr.set_source_rgb(1, 1, 1)
		cr.rectangle(x - 1.5, 0, 3, height)
		cr.fill()
		return False

	def _draw_picker_slider_ring(self, widget, cr):
		if widget.slider_name != PICKER_SLIDERS[self.picker_slider_index]:
			return False
		_draw_ring(widget, cr)
		return False

	def _rebuild_options_grid(self, field):
		"""Tears down and rebuilds every cell in the options grid for
		`field` - real work (per-cell thumbnail lookup/generation, see
		avatar3d_thumbnail_cache.get_thumbnail()), so this only runs
		when the active field itself actually changes, not on every
		single Left/Right step within the same field (see
		_update_selection() for that path).

		Detaches (not destroys) the live preview's own rig/pet_rig from
		the shared OffscreenRenderer scene first, reattaching once every
		thumbnail is generated - avatar3d.render_portrait() (what
		avatar3d_thumbnail_cache.get_thumbnail() calls on a cache miss)
		reparents its own fresh character into that exact same shared
		scene graph (avatar3d.py's OffscreenRenderer is a real process-
		wide singleton, one shared `render` root - see _rebuild_rig()'s
		own identical reparentTo() target), so without this the live
		preview's own character and whichever thumbnail is being
		generated would both actually be present in the scene and
		render into the same captured frame together - a real,
		confirmed-live bug ("the grey model and my astronar are
		bleeding together"), not a data/trait problem at all (the
		traits passed to each thumbnail render were always correctly
		neutral - the SCENE just had two characters in it at once).
		Safe against _tick()'s own GLib.timeout_add firing mid-detach:
		this whole method runs synchronously on the one GTK/GLib main
		loop thread, so no other callback can interleave with it."""
		for child in list(self.options_flow.get_children()):
			self.options_flow.remove(child)
		options = field_option_values(field)
		current_value = self._current_field_value(field)
		try:
			self._selected_option_index = options.index(current_value)
		except ValueError:
			self._selected_option_index = 0
		if self.rig is not None:
			self.rig.root.detachNode()
		if self.pet_rig is not None:
			self.pet_rig.root.detachNode()
		try:
			for i, option_value in enumerate(options):
				self.options_flow.add(self._make_option_tile(field, option_value, i))
		finally:
			if self.rig is not None:
				self.rig.root.reparentTo(avatar3d.get_renderer().render)
			if self.pet_rig is not None:
				self.pet_rig.root.reparentTo(avatar3d.get_renderer().render)
		self.options_flow.show_all()
		GLib.idle_add(self._scroll_selected_into_view)

	def _update_selection(self):
		"""The lighter-weight sibling of _rebuild_options_grid() -
		field_cursor hasn't moved, only the current field's own value
		has (_step_value()), so the SAME set of cells is still correct;
		this just moves which one is highlighted and asks each cell to
		redraw (see _draw_option_ring(), which reads
		self._selected_option_index fresh on every draw rather than
		this method needing to know which two cells actually changed)."""
		field = CUSTOM_FIELDS[self.field_cursor]
		options = field_option_values(field)
		current_value = self._current_field_value(field)
		try:
			self._selected_option_index = options.index(current_value)
		except ValueError:
			self._selected_option_index = 0
		for cell in self.options_flow.get_children():
			cell.get_child().queue_draw()
		GLib.idle_add(self._scroll_selected_into_view)

	def _make_option_tile(self, field, value, index):
		"""One thumbnail cell - a plain Gtk.Image wrapped in a Box whose
		own "draw" signal paints the selection ring (see
		_draw_option_ring()). can_focus is off and the FlowBox itself is
		SelectionMode.NONE (see __init__) - this grid never uses GTK's
		own focus/selection machinery at all, only the plain Python
		self._selected_option_index every cell's draw handler compares
		its own `index` against, per direct instruction (drawing is
		Python's job here, not CSS's, the same reasoning behind
		app.py's own _draw_astronar_badge_ring())."""
		child = Gtk.FlowBoxChild()
		child.set_can_focus(False)
		child.set_valign(Gtk.Align.START)
		image = Gtk.Image()
		image.set_size_request(OPTION_TILE_SIZE, OPTION_TILE_SIZE)
		thumbnail = avatar3d_thumbnail_cache.get_thumbnail(field, value)
		if thumbnail is not None:
			resized = thumbnail.resize((OPTION_TILE_SIZE, OPTION_TILE_SIZE))
			image.set_from_pixbuf(_pil_to_pixbuf(resized))
		cell = Gtk.Box()
		cell.index = index
		cell.pack_start(_make_named_thumbnail(image, _field_value_text(field, value)), False, False, 0)
		cell.connect_after("draw", self._draw_option_ring)
		child.add(cell)
		return child

	def _draw_option_ring(self, widget, cr):
		# Exactly one of the options grid / outfit row ever shows a ring
		# at a time - whichever region self.outfit_focused says currently
		# has focus (see _draw_outfit_ring(), the outfit row's own mirror
		# of this same guard).
		if self.outfit_focused or widget.index != self._selected_option_index:
			return False
		_draw_ring(widget, cr)
		return False

	def _scroll_selected_into_view(self):
		"""Purely Python-driven scroll-into-view, not GTK's own focus-
		follows-scroll behavior (this grid's cells are never really
		focused at all - see _make_option_tile()'s own comment) - reads
		the selected cell's real post-layout allocation and adjusts the
		ScrolledWindow's own vertical adjustment directly, the same
		"don't trust a native widget behavior this app hasn't already
		proven" caution used throughout this codebase's own navigation
		code. GLib.idle_add since a freshly-added cell's allocation
		isn't valid until after the next layout pass."""
		children = self.options_flow.get_children()
		if not (0 <= self._selected_option_index < len(children)):
			return False
		cell = children[self._selected_option_index]
		alloc = cell.get_allocation()
		vadj = self.options_scroller.get_vadjustment()
		if alloc.y < vadj.get_value():
			vadj.set_value(alloc.y)
		elif alloc.y + alloc.height > vadj.get_value() + vadj.get_page_size():
			vadj.set_value(alloc.y + alloc.height - vadj.get_page_size())
		return False

	def _refresh_tabs(self, active_category):
		for category, label in self.tab_labels.items():
			ctx = label.get_style_context()
			if category == active_category:
				ctx.add_class("avatar-tab-active")
			else:
				ctx.remove_class("avatar-tab-active")

	def _outfit_at(self, index):
		"""None for an empty slot - either past the end of the saved list
		entirely, or a real None placeholder profiles.get_outfits() can
		return for a slot that was never itself saved but got padded past
		(see profiles.save_outfit()'s own docstring on why - saving slot 3
		before slots 0-2 exist pads them with None, not a real entry)."""
		if 0 <= index < len(self._outfits):
			return self._outfits[index]
		return None

	def _refresh_outfit_focus(self):
		"""Redraws both the options grid's and the outfit row's own
		selection ring - exactly one of the two is ever visible at a time
		(see _draw_option_ring()/_draw_outfit_ring()), so any time
		self.outfit_focused itself changes without either grid being
		fully rebuilt, both need to be told to repaint."""
		for cell in self.options_flow.get_children():
			cell.get_child().queue_draw()
		for cell in self.outfit_flow.get_children():
			cell.get_child().queue_draw()

	def _rebuild_outfit_row(self):
		"""Tears down and rebuilds all MAX_OUTFIT_SLOTS tiles - cheap (at
		most 5 on-demand renders, never disk-cached the way the neutral-
		base options grid is - see this class's own outfit-row docstring
		in __init__) so this just runs in full any time a slot's own
		content could have changed, the same "rebuild is cheap enough,
		skip a lighter partial-update path" tradeoff _rebuild_rig() itself
		already makes."""
		for child in list(self.outfit_flow.get_children()):
			self.outfit_flow.remove(child)
		for index in range(MAX_OUTFIT_SLOTS):
			self.outfit_flow.add(self._make_outfit_tile(index))
		self.outfit_flow.show_all()

	def _make_outfit_tile(self, index):
		"""A filled slot renders ON DEMAND (not through
		avatar3d_thumbnail_cache.py's disk cache - only ever up to
		MAX_OUTFIT_SLOTS renders, cheap enough to do synchronously) the
		player's OWN current traits with that slot's saved Clothing/
		Accessories traits overlaid - a "what does this look like on ME"
		preview, deliberately different from the options grid's neutral-
		base comparison thumbnails. An empty slot is a plain "+"
		placeholder, matching the established None-means-add-tile
		convention used elsewhere in this app (quick-launch, widgets,
		profile picker)."""
		child = Gtk.FlowBoxChild()
		child.set_can_focus(False)
		child.set_valign(Gtk.Align.START)
		image = Gtk.Image()
		image.set_size_request(OUTFIT_TILE_SIZE, OUTFIT_TILE_SIZE)
		outfit = self._outfit_at(index)
		if outfit is not None:
			preview_traits = {**self.traits, **outfit["traits"]}
			try:
				rendered = avatar3d.render_portrait(preview_traits, size=OUTFIT_TILE_SIZE)
				image.set_from_pixbuf(_pil_to_pixbuf(rendered))
			except Exception:
				log.exception(f"failed to render outfit slot {index}")
			content = _make_named_thumbnail(image, outfit["name"])
		else:
			# An empty slot has nothing to name - just the bare "+"
			# placeholder, no black name box under it.
			image.set_from_icon_name("list-add-symbolic", Gtk.IconSize.DIALOG)
			content = image
		cell = Gtk.Box()
		cell.index = index
		cell.pack_start(content, False, False, 0)
		cell.connect_after("draw", self._draw_outfit_ring)
		child.add(cell)
		return child

	def _draw_outfit_ring(self, widget, cr):
		if not self.outfit_focused or widget.index != self._selected_outfit_index:
			return False
		_draw_ring(widget, cr)
		return False

	def outfit_prev(self):
		self._selected_outfit_index = (self._selected_outfit_index - 1) % MAX_OUTFIT_SLOTS
		self._refresh_outfit_focus()

	def outfit_next(self):
		self._selected_outfit_index = (self._selected_outfit_index + 1) % MAX_OUTFIT_SLOTS
		self._refresh_outfit_focus()

	def outfit_confirm(self):
		"""Confirm on a filled slot loads it - merges just its saved
		Clothing/Accessories traits into the live character (body/face/
		creature-feature traits are never part of an outfit at all - see
		OUTFIT_TRAIT_KEYS - so they're never touched here either); a
		no-op on an empty slot, the same "activating an empty tile does
		nothing" convention quick-launch/widgets already use."""
		outfit = self._outfit_at(self._selected_outfit_index)
		if outfit is None:
			return
		self.traits.update(outfit["traits"])
		self._rebuild_rig()
		self._rebuild_field_rows()
		self._rebuild_outfit_row()

	def outfit_save(self):
		"""Delete on any slot (empty or filled) saves the CURRENT
		Clothing/Accessories trait values into it, matching this app's
		own established "activating a tile does the default thing,
		Delete does the secondary/management action" split (File
		Manager, quick-launch, widgets, the Dashboard's own Astronar
		badge itself). Only persisted to disk for a real profile - see
		this class's own profile_name docstring in __init__."""
		outfit_traits = {k: v for k, v in self.traits.items() if k in OUTFIT_TRAIT_KEYS}
		if self.profile_name:
			profiles.save_outfit(self._selected_outfit_index, outfit_traits, self.profile_name)
		while len(self._outfits) <= self._selected_outfit_index:
			self._outfits.append(None)
		self._outfits[self._selected_outfit_index] = {
			"name": f"Outfit {self._selected_outfit_index + 1}", "traits": outfit_traits,
		}
		self._rebuild_outfit_row()

	def selected_outfit(self):
		"""The currently-highlighted slot's own outfit dict, or None for
		an empty one - app.py reads this to decide whether Confirm on the
		outfit row should open the action popup (Change into it/Delete)
		at all, without reaching into self._selected_outfit_index
		directly."""
		return self._outfit_at(self._selected_outfit_index)

	def selected_outfit_index(self):
		"""The plain slot number selected_outfit() itself belongs to -
		app.py's own open_outfit_action_popup() needs the index too (to
		later call outfit_delete(index) if Delete is chosen), without
		reaching into self._selected_outfit_index directly."""
		return self._selected_outfit_index

	def outfit_delete(self, index):
		"""Clears slot `index` back to empty - the real removal action
		reached via the outfit action popup's own "Delete" choice (see
		app.py's outfit_action_scrim), distinct from the physical Delete
		KEY (outfit_save(), which overwrites a slot with the current
		traits instead - a different action bound to a different key
		that happens to share the English word "delete"). Only persisted
		to disk for a real profile, same as outfit_save()."""
		if self.profile_name:
			profiles.delete_outfit(index, self.profile_name)
		if 0 <= index < len(self._outfits):
			self._outfits[index] = None
		self._rebuild_outfit_row()

	def _tick(self):
		if self.rig is None:
			return True
		t = ((time.time() - self._t0) % 2.0) / 2.0
		avatar3d_anim.play(self.rig, "idle", t)
		if self.pet_rig is not None:
			avatar3d_pet_anim.play(self.pet_rig, "idle", t)
		renderer = avatar3d.get_renderer()
		_buffer, tex, _cam = renderer.buffer_for_size(PREVIEW_SIZE)
		renderer.graphicsEngine.renderFrame()
		renderer.graphicsEngine.renderFrame()
		raw = bytes(tex.getRamImageAs("RGBA"))
		pixbuf = GdkPixbuf.Pixbuf.new_from_bytes(
			GLib.Bytes.new(raw), GdkPixbuf.Colorspace.RGB, True, 8, PREVIEW_SIZE, PREVIEW_SIZE, PREVIEW_SIZE * 4,
		)
		# OpenGL textures are bottom-to-top; GdkPixbuf/GTK images are
		# top-to-bottom - skipping this renders every frame upside down
		# (confirmed live the first time this technique was tried). A
		# real, confirmed-live bug in this exact fix's first version:
		# GdkPixbuf.Pixbuf.flip()'s bool parameter is named `horizontal`
		# (confirmed via introspection) - flip(True) mirrors left-right,
		# not the vertical (upside-down) flip actually needed, leaving
		# the image still upside down AND now also mirrored. flip(False)
		# is the real vertical flip.
		self.image.set_from_pixbuf(pixbuf.flip(False))
		return True
