#!/usr/bin/env python3
"""Disk-backed cache of small static portrait renders, one per (trait
field, value) pair, each showing that value applied to avatar3d_viewer.
NEUTRAL_TRAITS's own shared "naked grey Astronar" backdrop - what the
Customize Astronar screen's own options grid (avatar3d_viewer.py's
AvatarCustomizePage, replacing a plain Left/Right text-value stepper
per direct instruction) shows one thumbnail per option of, so options
are genuinely comparable against each other rather than against
whatever the player's own current character already looks like.

Persisted to real PNG files under paths.DATA_ROOT - not the in-memory-
only, rebuilt-every-process-start cache avatar3d_portrait_anim.py's own
live per-profile portraits use. The neutral base template essentially
never changes, unlike a live profile's own picture, so paying the real
render cost more than once per (field, value) pair for the life of an
install would be pure waste.

Lazy, not eager: a field's own thumbnails are only ever generated the
first time that field is actually browsed (see AvatarCustomizePage.
_rebuild_options_grid()) - rendering is synchronous (avatar3d.py's one
process-wide OffscreenRenderer is thread-affine to Panda3D's ShowBase,
so there's no safe way to push this to a background thread the way
run_async() handles I/O-bound work elsewhere), so eagerly rendering
every option of every one of ~92 fields up front would visibly freeze
the GTK main loop on first launch. A field with many options (e.g.
"ears", 19 values) pays a brief, one-time render cost the first time
it's ever viewed; every visit after that - same session, same field
revisited, or a totally fresh process later - is an instant disk read."""
import hashlib
import json
import sys
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import debuglog  # noqa: E402
import paths  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
import avatar3d  # noqa: E402

log = debuglog.setup("avatar3d_thumbnail_cache")

CACHE_DIR = paths.DATA_ROOT / "avatar_thumbnail_cache"
RENDER_SIZE = 320  # bumped up from a plain thumbnail size specifically so a cropped region (see CROP_REGIONS below) still has real detail left after cropping, before the final display-time resize down to the actual tile size

# Per direct correction ("the current astronar bleeds into the grey..
# only use the grey for body shapes, sizes, and body parts") - a true
# standalone item render (zero body at all) isn't feasible without a
# whole new isolated-mesh rendering pipeline (avatar3d.py's own
# build_character()/build_torso()/etc. bake the whole body, clothing,
# and accessories into ONE combined procedural mesh - there's no
# separate "shirt object" to render alone). Cropping the same neutral-
# base render down to just the relevant body region is what actually
# ships instead - still the grey Astronar underneath, but zoomed in
# enough that the item/feature being previewed is what you actually
# see, not a tiny detail lost on a small full-body render. Real body-
# shape/size/body-part fields (gender, weight/muscle/shape/age, leg
# type, wheelchair, missing limbs/prosthetics) are the one case that
# stays a full, uncropped render - those need the whole silhouette to
# mean anything at all. Fractions measured directly against a real
# 320x320 neutral-base render - not guessed, a real numpy pass over its
# actual alpha channel confirmed: head spans y=0.125-0.33 (x=0.29-0.70
# wide), shoulders/torso widen to x=0.29-0.70 from y=0.35-0.62, legs
# narrow to x=0.39-0.61 from y=0.65-0.90, feet are a real thin sliver
# at the very bottom, y=0.90-0.935.
CROP_REGIONS = {
	"full": (0.0, 0.0, 1.0, 1.0),
	"head": (0.27, 0.08, 0.73, 0.36),
	"torso": (0.22, 0.32, 0.78, 0.64),
	"legs": (0.32, 0.60, 0.68, 0.90),
	"feet": (0.28, 0.78, 0.72, 0.94),
}

# Fields whose own preview is fundamentally about the WHOLE body's
# shape/size/silhouette, not one isolated region - these are the only
# ones that stay a full, uncropped render (see CROP_REGIONS' own
# docstring above). Everything else gets classified by keyword (see
# _crop_region() below). A field - base or plugin - can always declare
# its own crop region explicitly via a "thumbnail_crop" key (one of
# CROP_REGIONS' own keys) directly in its CUSTOM_FIELDS row/bodypart.json
# manifest entry, checked first; the keyword lists below are only the
# FALLBACK for a field that doesn't bother, so a brand new field (a
# future base field, or a "bodypart" plugin installed later) still gets
# a reasonable region automatically instead of silently defaulting to
# "torso" for anything unrecognized. Per direct instruction ("give the
# fields... the ability to grow too") - this is what actually lets the
# system grow: a plugin author who cares about precision sets
# thumbnail_crop once, in pure JSON, no code change needed here at all.
_FULL_BODY_FIELDS = {"gender", "weight", "muscle", "shape", "age", "leg_type", "wheelchair", "missing_arm", "missing_leg", "prosthetics", "pet_species", "pet_color"}
_FEET_KEYWORDS = ("shoe", "sock")
_LEGS_KEYWORDS = ("pants", "bottom_style", "shorts", "leg")
_HEAD_KEYWORDS = (
	"skin", "hair", "eye", "nose", "mouth", "facial_hair", "freckles", "blush", "iris", "skin_patches",
	"ear", "horn", "halo", "glasses", "hat", "necklace", "earrings", "headphones", "muzzle", "whiskers",
	"extra_eyes", "antennae",
)


def _crop_region(field):
	"""Which CROP_REGIONS key this field's own thumbnails should crop
	to - field["thumbnail_crop"] if the field declares one explicitly,
	else a keyword-based guess (see this module's own docstring on
	why the explicit key is the real extensibility mechanism, and the
	keyword lists are only ever the fallback for a field that skips
	it)."""
	explicit = field.get("thumbnail_crop")
	if explicit in CROP_REGIONS:
		return explicit
	key = field["key"]
	if key in _FULL_BODY_FIELDS:
		return "full"
	haystack = f"{key} {field.get('item', '')}".lower()
	if any(kw in haystack for kw in _FEET_KEYWORDS):
		return "feet"
	if any(kw in haystack for kw in _LEGS_KEYWORDS):
		return "legs"
	if any(kw in haystack for kw in _HEAD_KEYWORDS):
		return "head"
	return "torso"  # shirt/top/drawstring/backpack/satchel/purse/tie/wristband/gloves/tail/wings default here - the chest/hip region every remaining clothing/accessory field is worn on or near


def _crop_image(image, region):
	x0f, y0f, x1f, y1f = CROP_REGIONS[region]
	w, h = image.size
	box = (int(x0f * w), int(y0f * h), int(x1f * w), int(y1f * h))
	return image.crop(box)


def _field_identity(field):
	"""A cache-key component unique per real CUSTOM_FIELDS row, not just
	per trait key - several "multi_bool" rows (one per accessory) all
	share the same field["key"] (the real shared list trait, e.g.
	"accessories"), distinguished only by their own field["item"], so
	that has to be part of the identity too or two different
	accessories' thumbnails would collide in the same cache file."""
	if field["kind"] == "multi_bool":
		return f"{field['key']}:{field['item']}"
	return field["key"]


# Bumped whenever what a cached thumbnail file actually contains changes
# (e.g. adding the crop-region behavior here did) - part of the cache
# key so an install that already has thumbnails cached from an older
# version of this module never gets served a stale, no-longer-matching
# image instead of a real re-render.
CACHE_FORMAT_VERSION = 2


def _cache_path(field, value):
	raw = json.dumps({"v": CACHE_FORMAT_VERSION, "field": _field_identity(field), "value": value}, sort_keys=True)
	digest = hashlib.sha1(raw.encode()).hexdigest()
	return CACHE_DIR / f"{digest}.png"


def _thumbnail_traits(field, value):
	"""avatar3d_viewer.NEUTRAL_TRAITS with exactly this one field
	overridden to `value` - imported lazily (not at module load) since
	avatar3d_viewer.py is what constructs AvatarCustomizePage and would
	otherwise form an import cycle with this module."""
	sys.path.insert(0, str(Path(__file__).resolve().parent))
	import avatar3d_viewer

	traits = dict(avatar3d_viewer.NEUTRAL_TRAITS)
	if field["kind"] == "multi_bool":
		key, item = field["key"], field["item"]
		accessories = list(traits.get(key, []))
		if value and item not in accessories:
			accessories.append(item)
		elif not value and item in accessories:
			accessories.remove(item)
		traits[key] = accessories
	else:
		traits[field["key"]] = value
	return traits


def get_thumbnail(field, value):
	"""Returns a PIL RGBA Image for this one (field, value) pair - from
	disk if already cached, else rendered fresh against the neutral
	base, cropped to whichever body region this field's own thumbnails
	should focus on (see _crop_region()/CROP_REGIONS), and saved
	already-cropped for every future lookup (any profile, any future
	session) to reuse instead of re-rendering. Never raises - a render/
	cache failure logs and returns None, the same "missing means
	nothing to show yet" contract this project's own image-loading
	call sites already treat a missing thumbnail as."""
	path = _cache_path(field, value)
	if path.is_file():
		try:
			return Image.open(path).convert("RGBA")
		except (OSError, ValueError):
			log.warning(f"cached thumbnail at {path} is unreadable, re-rendering", exc_info=True)
	try:
		image = avatar3d.render_portrait(_thumbnail_traits(field, value), size=RENDER_SIZE)
		image = _crop_image(image, _crop_region(field))
		CACHE_DIR.mkdir(parents=True, exist_ok=True)
		image.save(path)
		return image
	except Exception:
		log.exception(f"failed to render thumbnail for {_field_identity(field)}={value!r}")
		return None
