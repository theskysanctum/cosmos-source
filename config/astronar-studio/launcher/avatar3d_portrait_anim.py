#!/usr/bin/env python3
"""Animated Astronar pictures - the profile picker, Dashboard "Profile"
widget, and the Astronar badge shown on all three home screens all want
a real looping animation instead of a single static avatar3d.
render_portrait() frame, without paying for a live Panda3D render per
widget per tick the way AvatarCustomizePage's one-avatar-at-a-time
preview does - this app can have several of these pictures on screen
at once (every profile tile in the picker simultaneously, plus a badge
on every home screen), and avatar3d.OffscreenRenderer only ever holds
one character in its shared scene graph at a time (build -> render ->
capture -> removeNode(), see render_character_to_image()), so "just do
what the customizer does" for all of them together isn't actually
available here.

Real 3D rendering happens exactly once per distinct trait-set: a short
"idle" loop (avatar3d_anim's own resting-pose motion - breathing bob,
blink, slow head drift, the exact motion AvatarCustomizePage's own live
preview already plays) is rendered to a small list of in-memory RGBA
frames at one canonical size and cached, keyed by the traits themselves
(json-serialized) - the same trait dict in always produces the same
cached frames out, so a trait change is automatically a cache miss with
no separate invalidation step needed, matching every other picture call
site's own "regenerate fresh from traits, never a stale saved file"
convention (see profiles.picture_traits_path()'s own docstring). A real
render+encode of one full loop was confirmed live to take well under
200ms even on modest offscreen hardware - cheap enough to pay for once
per profile on first display, not something that needs spreading across
frames/ticks.

Everywhere those frames are actually displayed shares ONE process-wide
GLib.timeout_add "heartbeat" (see bind()) instead of a separate timer
per picture - each bound widget just looks up the current phase's
pre-rendered frame and does a cheap PIL resize to its own current pixel
size each tick (real 3D rendering only ever happens up front, per
profile, never per tick/per widget)."""
import json
import time

from gi.repository import GLib

import avatar3d
import avatar3d_anim
import avatar3d_pet_anim
import avatar3d_pet_rig
import avatar3d_rig
import debuglog

log = debuglog.setup("avatar3d_portrait_anim")

FPS = 12
LOOP_SECONDS = 2.0  # matches the built-in "idle" motion's own natural loop period (avatar_plugins_builtin/motion/idle/)
FRAME_COUNT = int(FPS * LOOP_SECONDS)
RENDER_SIZE = 256  # one canonical render size - every display site scales this down/up to its own actual pixel size, same "thumbnail from one shared source" tradeoff render_portrait() itself doesn't need (a static render is cheap enough to redo per exact size) but a 24-frame loop is worth only rendering once

_MAX_CACHE_ENTRIES = 12  # a handful of profiles' worth - plenty for a personal console, capped so switching through many profiles over a long session can't grow this unbounded
_frame_cache = {}  # traits-json -> list[PIL.Image RGBA], insertion-ordered for simple oldest-first eviction

_registered = []  # list of _BoundPortrait currently on screen somewhere
_tick_source_id = None


def _human_traits(traits):
	return {k: v for k, v in traits.items() if k not in ("pet_species", "pet_color")}


def _render_loop_frames(traits):
	renderer = avatar3d.get_renderer()
	rig = avatar3d_rig.build_rig(**_human_traits(traits))
	rig.root.reparentTo(renderer.render)
	rig.root.setH(20)
	pet_rig = None
	pet_species = traits.get("pet_species", "none")
	if pet_species != "none":
		# Same separate-PetRig + side-of-hips placement AvatarCustomizePage's
		# own _rebuild_rig() already establishes - see that method's own
		# comment on why the pet can't just be baked into the person's rig.
		pet_rig = avatar3d_pet_rig.build_pet_rig(species=pet_species, color=traits.get("pet_color", "#8b5e3c"))
		pet_rig.root.reparentTo(renderer.render)
		gmod = avatar3d.GENDER_MODS.get(traits["gender"], avatar3d.GENDER_MODS["nonbinary"])
		weight_scale = 0.82 + traits["weight"] * 0.55
		_head_scale, body_scale = avatar3d._age_body_scale(traits["age"])
		hip_half_w = (0.28 + traits["shape"] * 0.14 + gmod["hip"]) * weight_scale * body_scale
		pet_rig.root.setX(hip_half_w + 0.45)
		pet_rig.root.setH(20)
	_buffer, tex, _cam = renderer.portrait_buffer_for_size(RENDER_SIZE)
	frames = []
	try:
		for i in range(FRAME_COUNT):
			t = i / FRAME_COUNT
			avatar3d_anim.play(rig, "idle", t)
			if pet_rig is not None:
				avatar3d_pet_anim.play(pet_rig, "idle", t)
			renderer.graphicsEngine.renderFrame()
			renderer.graphicsEngine.renderFrame()
			frames.append(avatar3d.texture_to_pil(tex))
	finally:
		rig.root.removeNode()
		if pet_rig is not None:
			pet_rig.root.removeNode()
	return frames


def get_loop_frames(traits):
	"""The cached RGBA frame list (one full idle loop) for this exact
	trait dict, rendering it fresh (once) on a cache miss."""
	key = json.dumps(traits, sort_keys=True)
	frames = _frame_cache.get(key)
	if frames is not None:
		return frames
	log.debug(f"rendering a new idle-loop frame set ({FRAME_COUNT} frames) for traits={traits}")
	frames = _render_loop_frames(traits)
	_frame_cache[key] = frames
	if len(_frame_cache) > _MAX_CACHE_ENTRIES:
		oldest_key = next(iter(_frame_cache))
		del _frame_cache[oldest_key]
	return frames


def _pil_to_pixbuf(image):
	"""Same in-memory PIL -> GdkPixbuf conversion as app.py's own
	_pil_to_pixbuf() - duplicated (not imported) specifically to avoid a
	circular import (app.py imports this module), matching the project's
	existing tolerance for duplicating one small, truly self-contained
	utility over introducing a new cross-module dependency for it."""
	from gi.repository import GdkPixbuf, GLib as _GLib

	data = image.tobytes()
	return GdkPixbuf.Pixbuf.new_from_bytes(_GLib.Bytes.new(data), GdkPixbuf.Colorspace.RGB, True, 8, image.width, image.height, image.width * 4)


class _BoundPortrait:
	__slots__ = ("image", "traits", "current_size")

	def __init__(self, image, traits, size):
		self.image = image
		self.traits = traits
		self.current_size = size


def bind(image, traits, size):
	"""Attaches a Gtk.Image to the shared animation heartbeat and shows
	its first frame immediately (no blank widget while waiting for the
	next tick). Returns (render_at_size, unbind):

	- render_at_size(size) has the exact same shape every zoomable tile
	  in this app already expects (see app.py's animate_tile_zoom()/
	  wire_tile_zoom()) - an animated portrait tile drops into that
	  existing stepped-zoom machinery unchanged, it just changes what
	  size the NEXT tick renders at instead of redrawing synchronously.
	- unbind() stops animating this image and drops it from the shared
	  tick - callers should tie this to the widget's own "destroy" signal
	  (or call it directly when done, e.g. before rebuilding a widget
	  that held one) so removed/replaced pictures don't keep ticking
	  forever."""
	entry = _BoundPortrait(image, traits, size)
	_registered.append(entry)
	_ensure_tick()
	_apply_frame(entry, _phase_index())

	def render_at_size(new_size):
		entry.current_size = new_size

	def unbind():
		if entry in _registered:
			_registered.remove(entry)

	return render_at_size, unbind


def _phase_index():
	t = (time.time() % LOOP_SECONDS) / LOOP_SECONDS
	return int(t * FRAME_COUNT) % FRAME_COUNT


def _apply_frame(entry, idx):
	frames = get_loop_frames(entry.traits)
	frame = frames[idx % len(frames)]
	size = entry.current_size
	if frame.size != (size, size):
		frame = frame.resize((size, size))
	entry.image.set_from_pixbuf(_pil_to_pixbuf(frame))


def _tick():
	idx = _phase_index()
	for entry in list(_registered):
		_apply_frame(entry, idx)
	return True


def _ensure_tick():
	global _tick_source_id
	if _tick_source_id is None:
		_tick_source_id = GLib.timeout_add(int(1000 / FPS), _tick)
