#!/usr/bin/env python3
"""Developer tool, not part of the shipped app - a live, on-screen
preview of an avatar3d_rig.Rig driven directly by avatar3d_anim's
play(rig, name, t) motion dispatch, for reviewing new traits/animations
at full camera control and full motion variety, well beyond what the
real in-app "Customize Astronar" screen (avatar3d_viewer.AvatarCustomizePage)
deliberately shows (a fixed angle, idle-only). Run directly:
`python3 launcher/avatar3d_previewer.py [motion_name]`. No PNG frames,
no ffmpeg, no external video player - a real Panda3D window (not
OffscreenRenderer) whose per-frame task computes t from real elapsed
time and calls the current motion function directly, live instead of
captured.

The right-hand side of the window is a real, always-visible DirectGUI
menu (a real, direct request - "the player needs an actual menu with
sliders and stuff", replacing an earlier version of this tool where
customization was a keyboard-only up/down cursor stepping through one
field's value at a time via z/x with the current field/value only
readable as on-screen text). It's a scrollable list of every
build_rig()-supported trait - avatar3d_viewer.CUSTOM_FIELDS (imported,
not duplicated here - the exact same fields the real in-app customize
screen offers, kept in sync automatically rather than needing a second
copy updated by hand every time that list changes) is still the single
source of truth for what's customizable - with a real DirectCheckButton
for each bool field, a real DirectSlider for each slider field, a real
DirectOptionMenu (a dropdown, not cycling one option at a time) for
every plain "choice" field, and - a real, direct correction: colors
originally reused that same curated-preset dropdown too, and that's not
what was asked for ("colors shouldn't be presets, they should be 100%
customizable on a color gradient and square slider") - every color/
color_opt field instead gets a clickable swatch button that opens a
floating picker: a real hue gradient bar plus a saturation/value square,
both directly draggable, reaching any of the 16.7M RGB colors rather
than a short curated list (color_opt fields also get a small "auto"
checkbox next to the swatch, for the "None - match some other field's
color" state a plain color field doesn't have). A motion dropdown and
Save/Prev/Next-seed/Reset buttons sit above the scrollable field list,
in the same panel.

While a color picker is open, WASD and the arrow keys drive it instead
of their usual jobs (camera orbit, motion cycling) - another real,
direct correction ("color slider should be controlled by WASD and
Arrows", after the picker first shipped mouse-drag-only): A/D move the
saturation/value square left/right (saturation), W/S move it up/down
(value), and the arrow keys nudge hue (Left/Right) or value (Up/Down)
the same continuous, held-key way WASD already orbits the camera. Esc
closes the picker (and only quits the whole previewer when no picker is
open); +/- zoom keeps working either way, since it doesn't overlap
anything the picker itself uses.

Left/Right arrow keys still also cycle through every playable motion
(avatar_plugin_backend.installed_motions() - built-in AND any built-in-
root/installed motion plugin) - kept alongside the new motion dropdown (each
updates the other) since a keyboard shortcut is still faster than a
mouse trip to the dropdown when just flipping through motions one at a
time. Escape/q quits. WASD orbits the camera around the character (A/D =
heading, W/S = pitch, held-down for smooth continuous rotation) - added
per direct request to inspect the model from any angle directly, rather
than relying on whatever fixed angle a still render happened to use
(which had already missed/hidden real issues more than once). +/- (plain
keyboard row or numpad, held for smooth continuous zoom, same convention
as WASD) zoom the camera in/out; Home resets heading/pitch/zoom back to
the original framing in one press, for getting back to a known-good view
after orbiting/zooming somewhere awkward instead of hunting for it by
hand.

F5 saves the current full trait set (including age) as a new named seed to
avatar3d_previewer_seeds.json next to this script (gitignored - real
throwaway dev-session data, never committed); F8/F9 cycle backward/
forward through every saved seed, loading it live - same as the panel's
own Save/Prev/Next buttons, just reachable without a mouse trip too.
Seeds are plain JSON - a dict of every CUSTOM_FIELDS value the
customizer can reach, not a random-number seed (this generator's own
stated design rule is "every choice is explicit, NOT seed-random" -
avatar3d.build_character's own docstring says so directly - so a "seed"
here means a saved snapshot of those explicit choices, not an RNG seed
the way the old avatar_gen.py profile pictures it replaced used the
word)."""
import colorsys
import json
import os
import sys
from pathlib import Path

from direct.showbase.ShowBase import ShowBase
from direct.task import Task
from panda3d.core import AmbientLight, DirectionalLight, PNMImage, TextNode, Texture, loadPrcFileData

loadPrcFileData("", "audio-library-name null")
# Widened from a plain square so the always-visible menu panel has real
# room on the right without eating into the 3D view on the left -
# window-title unchanged. win-fixed-size is required here (confirmed
# live: without it, this desktop's own window manager silently ignored
# win-size entirely and opened the window near-fullscreen instead,
# throwing off every panel coordinate below, which assumes the
# requested 1150x720 aspect ratio).
loadPrcFileData("", "win-size 1150 720")
loadPrcFileData("", "win-fixed-size true")
loadPrcFileData("", "window-title CosmOS Astronar Previewer")

import avatar3d_anim
import avatar3d_pet_anim
import avatar3d_pet_rig
import avatar3d_rig
import avatar_plugin_backend

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import debuglog  # noqa: E402

log = debuglog.setup("avatar3d_previewer")

# avatar3d.py (pulled in via avatar3d_rig/avatar3d_viewer) sets
# "window-type offscreen" at its own module-import time, for
# OffscreenRenderer's sake - that silently overrides the settings above
# and made ShowBase() create an invisible offscreen buffer instead of a
# real window (confirmed live: the process ran fine and burned CPU, but
# no window ever appeared anywhere - not in wmctrl, not in xdotool).
# Force it back explicitly, after that import, so this previewer
# actually gets an onscreen window. Deferred until after avatar3d_viewer
# (imported below, for its CUSTOM_FIELDS/CUSTOM_DEFAULTS) has already
# pulled avatar3d in - same reasoning, just one more importer of it now.
import avatar3d_viewer  # noqa: E402

loadPrcFileData("", "window-type onscreen")

from direct.gui.DirectGui import (  # noqa: E402
	DGG, DirectButton, DirectCheckButton, DirectFrame, DirectLabel,
	DirectOptionMenu, DirectScrolledFrame, DirectSlider,
)
from direct.gui.OnscreenImage import OnscreenImage  # noqa: E402

import avatar3d  # noqa: E402

# Named distinctly from the real app's own picture_traits.json (per-
# profile, real user data) - this is throwaway dev-session data,
# gitignored, never confused with a real saved avatar.
SEEDS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "avatar3d_previewer_seeds.json")

# The exact same trait list/defaults the real in-app "Customize Astronar"
# screen offers (avatar3d_viewer.AvatarCustomizePage) - imported, not
# duplicated, so this previewer can never quietly drift out of sync
# with it the way avatar3d.py's own build_character/build_rig tail-
# building code once did (see avatar3d_rig.py's own comment on that).
CUSTOM_FIELDS = avatar3d_viewer.CUSTOM_FIELDS
CUSTOM_DEFAULTS = avatar3d_viewer.CUSTOM_DEFAULTS
_field_value_text = avatar3d_viewer._field_value_text

LOOP_SECONDS = 2.0
ORBIT_SPEED = 90.0  # degrees/second while a rotate key is held
ZOOM_SPEED = 6.0  # units/second while a zoom key is held
MIN_CAM_DIST = 1.5
MAX_CAM_DIST = 20.0
CAM_DIST = 6.5
LOOK_Z = 1.45
START_HEADING = 20.0
START_PITCH = 0.0

# Panel geometry, in aspect2d units - win-size 1150x720 gives an
# aspect2d x range of roughly -1.6..1.6 (aspectRatio = 1150/720), so the
# panel occupies the right ~1/3 of the window, leaving the rest for the
# 3D view. Tuned by direct visual check (screenshot), not computed from
# first principles - DirectGUI's own coordinate/sizing conventions don't
# give an exact row width/height from font scale alone.
PANEL_LEFT = 0.55
PANEL_RIGHT = 1.55
PANEL_TOP = 0.97
SCROLL_TOP = 0.38
SCROLL_BOTTOM = -0.97
ROW_H = 0.062
LABEL_SCALE = 0.032
SWATCH_HALF = 0.022

# The color-picker popup, opened by clicking a color/color_opt field's
# swatch button - a real, direct correction: color fields originally
# reused the same curated-preset DirectOptionMenu every choice field
# uses, and that's not what was asked for ("colors shouldn't be
# presets, they should be 100% customizable on a color gradient and
# square slider") - every color field now opens this instead: a hue
# gradient bar plus a saturation/value square, both directly draggable,
# reaching any of the 16.7M RGB colors, not just a short curated list.
# Positioned over the 3D view (left of the panel, which starts at
# PANEL_LEFT) as a fixed floating overlay, not anchored to whichever
# row's swatch opened it - simpler, and the row itself scrolls (it's
# inside the DirectScrolledFrame's canvas) while this popup can't be.
POPUP_POS = (-0.7, 0, 0.0)
POPUP_HALF_W = 0.5
POPUP_HALF_H = 0.48
HUE_BAR_W = 0.8
HUE_BAR_H = 0.09
HUE_BAR_Z = 0.30
SV_SIZE = 0.66
SV_Z = -0.08
HUE_TEX_W, HUE_TEX_H = 180, 8
SV_TEX_SIZE = 48


def _hue_bar_texture():
	"""A 180x8 strip cycling through every hue at full saturation/value -
	regenerated only once per popup open (hue itself doesn't change what
	this texture looks like, only where the marker sits on it), unlike
	the saturation/value square below, which depends on the CURRENTLY
	selected hue and so is regenerated every time that hue changes."""
	img = PNMImage(HUE_TEX_W, HUE_TEX_H)
	for x in range(HUE_TEX_W):
		r, g, b = colorsys.hsv_to_rgb(x / HUE_TEX_W, 1.0, 1.0)
		for y in range(HUE_TEX_H):
			img.setXelA(x, y, r, g, b, 1.0)
	tex = Texture("hue_bar")
	tex.load(img)
	tex.setMagfilter(Texture.FTLinear)
	tex.setMinfilter(Texture.FTLinear)
	return tex


def _sv_square_texture(hue):
	"""Saturation (x, 0 at left..1 at right) by value (y, 0/black at the
	bottom..1 at the top) for one specific hue - regenerated every time
	the hue bar's own selection moves, so the square always shows real
	achievable colors for whatever hue is currently picked."""
	img = PNMImage(SV_TEX_SIZE, SV_TEX_SIZE)
	for xi in range(SV_TEX_SIZE):
		s = xi / (SV_TEX_SIZE - 1)
		for yi in range(SV_TEX_SIZE):
			v = 1.0 - yi / (SV_TEX_SIZE - 1)
			r, g, b = colorsys.hsv_to_rgb(hue, s, v)
			img.setXelA(xi, yi, r, g, b, 1.0)
	tex = Texture("sv_square")
	tex.load(img)
	tex.setMagfilter(Texture.FTLinear)
	tex.setMinfilter(Texture.FTLinear)
	return tex


def _hex_to_hsv(hexval):
	r, g, b, _a = avatar3d._hex_to_rgba(hexval)
	return colorsys.rgb_to_hsv(r, g, b)


def _load_seeds():
	if os.path.exists(SEEDS_PATH):
		with open(SEEDS_PATH) as f:
			return json.load(f)
	return {}


def _field_widget_key(field):
	"""Unique per-ROW key for self.field_widgets, distinct from
	field["key"] itself - every other kind has a 1:1 field:trait-key
	relationship, but every "multi_bool" accessory row shares the SAME
	real "accessories" trait key (see avatar3d_viewer.CUSTOM_FIELDS's
	own comment on why), so field["key"] alone would collide across all
	12 of them and each row's widget would silently overwrite the
	previous one's entry. Appending field["item"] (unique per row for
	multi_bool, absent for every other kind) fixes that; every other
	kind's widget key is unchanged (still just its own real, already-
	unique field["key"])."""
	return f'{field["key"]}:{field["item"]}' if field["kind"] == "multi_bool" else field["key"]


def _option_values_and_labels(field):
	"""(values, labels) for a "choice" field's dropdown - values[i] is
	the real value build_rig() should receive if labels[i] is picked.
	Only ever called for kind=="choice" - color/color_opt fields don't
	use a curated preset list at all anymore (see POPUP_POS's own
	comment on why), so they never reach this. Labels reuse
	_field_value_text - the exact same "how do I show this value as
	text" logic the field-cursor text readout already used, just
	applied to every option up front instead of only the currently-
	selected one."""
	values = list(field["options"])
	labels = [_field_value_text(field, v) for v in values]
	return values, labels


class LivePreview(ShowBase):
	def __init__(self, motions, start_index=0):
		super().__init__()
		# ShowBase ties the camera to Panda's own default mouse-drag
		# orbit/pan/zoom control unless told not to - since this window
		# already has its own full WASD+/-/Home camera controller below,
		# that default control was still live at the same time,
		# fighting it (a stray mouse movement over the window could
		# yank the camera on top of whatever WASD had it doing). Turned
		# off per direct request ("disable the mouse in the viewer") -
		# this only removes Panda's own camera-control mouse binding,
		# not mouse input generally (the DirectGUI panel below still
		# gets real mouse clicks/drags - disableMouse() only detaches
		# the mouse from the CAMERA transform, not from aspect2d).
		self.disableMouse()
		self.motions = motions  # [(name, fn), ...]
		self.index = start_index
		self.rig = None
		self.pet_rig = None
		self.custom = dict(CUSTOM_DEFAULTS)
		self.seed_ids = sorted(_load_seeds().keys())
		self.seed_cursor = -1  # not currently viewing a saved seed
		# Guards widget->rebuild callbacks while a seed load/reset is
		# mass-updating every widget's displayed value in one go -
		# without it, loading a seed would rebuild the rig once per
		# field (43 rebuilds for one seed load) instead of once total.
		self._suppress_rebuild = False
		self.field_widgets = {}  # key -> the control widget for that field
		self.swatches = {}  # key -> the clickable color-swatch DirectButton, for color/color_opt fields
		self.hex_labels = {}  # key -> the DirectLabel showing that field's current hex/none_label text
		self.auto_checks = {}  # key -> the "auto" DirectCheckButton, for color_opt fields only
		# The color-picker popup (see POPUP_POS's own comment) - at most
		# one open at a time; color_popup is that popup's own root frame
		# (or None when closed), _picker_key is which field it's editing,
		# _drag_target is "hue"/"sv"/None depending on which part (if
		# any) of the popup the mouse button is currently held down over,
		# and _last_picked remembers each color_opt field's last real
		# (non-None) color so unchecking "auto" restores it instead of
		# always resetting to some arbitrary default.
		self.color_popup = None
		self._picker_key = None
		self._drag_target = None
		self._last_picked = {}

		# Orbit camera state - heading/pitch around a fixed look-at
		# point, rather than rotating the character itself, so this
		# never fights with any motion (e.g. "spin") that poses
		# rig.root's own heading directly. cam_dist joins heading/pitch
		# here (instead of staying the module-level CAM_DIST constant)
		# so +/- zoom can change it per-instance without touching the
		# original default Home resets back to.
		self.cam_heading = START_HEADING
		self.cam_pitch = START_PITCH
		self.cam_dist = CAM_DIST
		self.keys = {
			"a": False, "d": False, "w": False, "s": False,
			"left": False, "right": False, "up": False, "down": False,
			"zoom_in": False, "zoom_out": False,
		}
		self._last_time = 0.0
		self._update_camera()

		alight = AmbientLight("alight")
		alight.setColor((0.4, 0.4, 0.45, 1))
		self.render.setLight(self.render.attachNewNode(alight))
		dlight = DirectionalLight("dlight")
		dlight.setColor((1, 1, 0.95, 1))
		dlnp = self.render.attachNewNode(dlight)
		dlnp.setHpr(-35, -55, 0)
		self.render.setLight(dlnp)

		from direct.gui.OnscreenText import OnscreenText
		# Only the camera hints now - motion/seed state moved into the
		# real panel below, where they're shown by the widgets
		# themselves (the dropdown's own selection, the seed label)
		# rather than a second, separately-maintained text readout.
		self.hint_label = OnscreenText(
			text="WASD=orbit  +/-=zoom  Home=reset view  <-/->=motion  Esc/q=quit\n"
			"(color picker open: WASD=saturation/value  arrows=hue/value  Esc=close)",
			pos=(-1.55, -0.88), scale=0.028, fg=(1, 1, 1, 1), shadow=(0, 0, 0, 1), align=TextNode.ALeft,
		)

		# Arrow keys (like WASD just below) are dual-purpose - a real,
		# direct correction: the color picker originally only responded
		# to mouse drags ("color slider should be controlled by WASD and
		# Arrows"). Held while a color picker is open, WASD moves the
		# saturation/value square (A/D = saturation, W/S = value) and
		# Left/Right/Up/Down nudge the hue bar/value the same way (see
		# _update_picker_keys) instead of doing their normal jobs
		# (camera orbit, motion cycling) - _on_arrow_* below check
		# self.color_popup to decide which job an arrow tap does on
		# press; the "-up" release bindings always just clear the held-
		# state flag regardless, same as WASD's own release handling.
		self.accept("arrow_right", self._on_arrow, ["right"])
		self.accept("arrow_left", self._on_arrow, ["left"])
		self.accept("arrow_up", self._on_arrow, ["up"])
		self.accept("arrow_down", self._on_arrow, ["down"])
		for arrow in ("left", "right", "up", "down"):
			self.accept(f"arrow_{arrow}-up", self.keys.__setitem__, [arrow, False])
		self.accept("f5", self.save_seed)
		self.accept("f8", self.seed_prev)
		self.accept("f9", self.seed_next)
		# Escape closes an open color picker first (matching how it's
		# usually the "back out of this" key everywhere else, rather
		# than quitting the whole previewer out from under an open
		# popup); with no popup open it still quits, same as q always
		# does regardless.
		self.accept("escape", self._on_escape)
		self.accept("q", sys.exit)
		self.accept("home", self.reset_camera)
		# Ends a color-picker drag no matter where the mouse released -
		# see _apply_drag's own docstring on why this can't just be a
		# bind() on the hue bar/SV square themselves (dragging routinely
		# carries the cursor outside whichever one started the drag).
		self.accept("mouse1-up", self._picker_release)
		for key in ("a", "d", "w", "s"):
			self.accept(key, self.keys.__setitem__, [key, True])
			self.accept(f"{key}-up", self.keys.__setitem__, [key, False])
		# Bound this widely (both the plain-keyboard row and numpad, and
		# both the shifted "+" and its unshifted "=" position) since
		# which of these actually fires depends on keyboard layout/
		# numlock state - covering all of them means the zoom keys just
		# work regardless, rather than only on one specific keyboard.
		for zoom_key in ("+", "=", "kp_add"):
			self.accept(zoom_key, self.keys.__setitem__, ["zoom_in", True])
			self.accept(f"{zoom_key}-up", self.keys.__setitem__, ["zoom_in", False])
		for zoom_key in ("-", "kp_subtract"):
			self.accept(zoom_key, self.keys.__setitem__, ["zoom_out", True])
			self.accept(f"{zoom_key}-up", self.keys.__setitem__, ["zoom_out", False])

		self._build_panel()
		self._rebuild()
		self.taskMgr.add(self._update, "update-pose")

	def _update_camera(self):
		import math
		h_rad = math.radians(self.cam_heading)
		p_rad = math.radians(self.cam_pitch)
		x = self.cam_dist * math.sin(h_rad) * math.cos(p_rad)
		y = -self.cam_dist * math.cos(h_rad) * math.cos(p_rad)
		z = LOOK_Z + self.cam_dist * math.sin(p_rad)
		self.cam.setPos(x, y, z)
		self.cam.lookAt(0, 0, LOOK_Z)

	def reset_camera(self):
		"""Home - snaps heading/pitch/zoom back to the original framing
		in one press, so getting back to a known-good view after
		orbiting/zooming somewhere awkward doesn't mean hunting for it
		by hand with WASD/+/- one small step at a time."""
		self.cam_heading = START_HEADING
		self.cam_pitch = START_PITCH
		self.cam_dist = CAM_DIST
		self._update_camera()

	def _on_escape(self):
		if self.color_popup is not None:
			self._close_color_picker()
		else:
			sys.exit()

	def _on_arrow(self, direction):
		"""One arrow key's press - while a color picker is open, arrow
		keys are the picker's own keyboard control (see
		_update_picker_keys) instead of their normal job, so this just
		marks the key held for that continuous per-frame update; Up/Down
		otherwise do nothing (there's nothing else bound to them), and
		Left/Right otherwise still cycle motions exactly as before."""
		if self.color_popup is not None:
			self.keys[direction] = True
			return
		if direction == "right":
			self.next_motion()
		elif direction == "left":
			self.prev_motion()

	# ---- the DirectGUI menu panel ----

	def _build_panel(self):
		"""Builds the always-visible right-hand menu once - every field
		gets a real widget here (never rebuilt/recreated afterward);
		self._sync_all_widgets() below is what keeps their displayed
		values in sync with self.custom (on a seed load, a reset, or a
		motion-key press changing the motion dropdown), not rebuilding
		this panel itself."""
		# Guards the same widget->rebuild callbacks as _sync_all_widgets
		# - DirectOptionMenu/DirectCheckButton fire their command once
		# at construction time with their own initial value, and every
		# widget here is constructed already matching self.custom's own
		# default, so those firings would otherwise trigger one wasted
		# rig rebuild per field (over 40 of them) before the single
		# real, intentional _rebuild() call after this method returns.
		self._suppress_rebuild = True
		DirectFrame(
			frameColor=(0.06, 0.06, 0.09, 0.92),
			frameSize=(PANEL_LEFT - 0.02, PANEL_RIGHT + 0.02, SCROLL_BOTTOM - 0.02, PANEL_TOP + 0.02),
			pos=(0, 0, 0),
		)
		DirectLabel(
			text="CosmOS Astronar Previewer", text_scale=0.045, text_fg=(1, 1, 1, 1),
			relief=None, text_align=TextNode.ALeft,
			pos=(PANEL_LEFT + 0.02, 0, PANEL_TOP - 0.06),
		)

		# Motion dropdown - kept in sync with Left/Right arrow-key
		# cycling both ways (see next_motion/prev_motion/_sync_motion).
		DirectLabel(
			text="Motion", text_scale=LABEL_SCALE, text_fg=(1, 1, 1, 1), relief=None,
			text_align=TextNode.ALeft, pos=(PANEL_LEFT + 0.02, 0, PANEL_TOP - 0.16),
		)
		motion_names = [name for name, _fn in self.motions]
		self.motion_menu = DirectOptionMenu(
			items=motion_names, initialitem=self.index, command=self._on_motion_menu,
			scale=0.045, text_fg=(0, 0, 0, 1), pos=(PANEL_LEFT + 0.32, 0, PANEL_TOP - 0.16),
		)

		# Seed row - Save/Prev/Next mirror F5/F8/F9 exactly, for anyone
		# working mouse-only; self.seed_label shows which (if any)
		# saved seed the current traits match.
		btn_kwargs = dict(text_scale=0.9, scale=0.045, frameColor=(0.2, 0.2, 0.26, 1), text_fg=(1, 1, 1, 1))
		DirectButton(text="Save seed (F5)", command=self.save_seed, pos=(PANEL_LEFT + 0.14, 0, PANEL_TOP - 0.25), **btn_kwargs)
		DirectButton(text="< Seed (F8)", command=self.seed_prev, pos=(PANEL_LEFT + 0.55, 0, PANEL_TOP - 0.25), **btn_kwargs)
		DirectButton(text="Seed > (F9)", command=self.seed_next, pos=(PANEL_LEFT + 0.88, 0, PANEL_TOP - 0.25), **btn_kwargs)
		self.seed_label = DirectLabel(
			text="", text_scale=LABEL_SCALE, text_fg=(0.7, 0.85, 1, 1), relief=None,
			text_align=TextNode.ALeft, pos=(PANEL_LEFT + 0.02, 0, PANEL_TOP - 0.31),
		)
		DirectButton(
			text="Reset to defaults", command=self.reset_defaults,
			pos=(PANEL_LEFT + 0.22, 0, PANEL_TOP - 0.37), **btn_kwargs,
		)

		scroll_height = SCROLL_TOP - SCROLL_BOTTOM
		panel_width = PANEL_RIGHT - PANEL_LEFT
		canvas_height = len(CUSTOM_FIELDS) * ROW_H + 0.05
		self.scroll = DirectScrolledFrame(
			frameColor=(0.09, 0.09, 0.12, 1),
			frameSize=(0, panel_width - 0.06, -scroll_height, 0),
			canvasSize=(0, panel_width - 0.10, -canvas_height, 0),
			scrollBarWidth=0.035,
			pos=(PANEL_LEFT, 0, SCROLL_TOP),
		)
		canvas = self.scroll.getCanvas()

		for i, field in enumerate(CUSTOM_FIELDS):
			row_z = -i * ROW_H - 0.03
			DirectLabel(
				parent=canvas, text=field["label"], text_scale=LABEL_SCALE, text_fg=(1, 1, 1, 1),
				relief=None, text_align=TextNode.ALeft, pos=(0.01, 0, row_z),
			)
			control_x = 0.62
			kind = field["kind"]
			widget_key = _field_widget_key(field)
			if kind == "bool":
				widget = DirectCheckButton(
					parent=canvas, text="", indicatorValue=self.custom[field["key"]],
					command=self._make_bool_cb(field["key"]),
					scale=0.032, pos=(control_x, 0, row_z),
				)
			elif kind == "multi_bool":
				# Same widget as "bool" above, just reading/writing one
				# specific item's OWN membership in the shared list value
				# at self.custom[field["key"]] instead of a plain bool -
				# see avatar3d_viewer.CUSTOM_FIELDS's own "multi_bool"
				# comment for why every accessory row shares that one
				# real "accessories" trait key rather than inventing its
				# own (build_rig()/build_character() both take it as-is).
				widget = DirectCheckButton(
					parent=canvas, text="", indicatorValue=field["item"] in self.custom[field["key"]],
					command=self._make_multi_bool_cb(field["key"], field["item"]),
					scale=0.032, pos=(control_x, 0, row_z),
				)
			elif kind == "slider":
				lo, hi, step = field["range"]
				widget = DirectSlider(
					parent=canvas, range=(lo, hi), value=self.custom[field["key"]], pageSize=step,
					command=self._make_slider_cb(field["key"], step), scale=0.16,
					pos=(control_x + 0.14, 0, row_z), thumb_frameColor=(0.4, 0.7, 1, 1),
				)
			elif kind == "choice":
				values, labels = _option_values_and_labels(field)
				start = values.index(self.custom[field["key"]]) if self.custom[field["key"]] in values else 0
				widget = DirectOptionMenu(
					parent=canvas, items=labels, initialitem=start,
					command=self._make_choice_cb(field["key"], values, labels),
					scale=0.045, text_fg=(0, 0, 0, 1), pos=(control_x, 0, row_z),
				)
				widget._preview_values = values
				widget._preview_labels = labels
			else:  # "color" / "color_opt" - a clickable swatch that opens the real color-gradient/SV-square picker, not a curated preset dropdown
				key = field["key"]
				value = self.custom[key]
				swatch = DirectButton(
					parent=canvas, text="", frameColor=self._swatch_color(field, value),
					frameSize=(-SWATCH_HALF, SWATCH_HALF, -SWATCH_HALF, SWATCH_HALF),
					command=self._make_open_picker_cb(key), pos=(control_x + SWATCH_HALF, 0, row_z),
				)
				hex_label = DirectLabel(
					parent=canvas, text=_field_value_text(field, value), text_scale=LABEL_SCALE * 0.85,
					text_fg=(0.85, 0.85, 0.9, 1), relief=None, text_align=TextNode.ALeft,
					pos=(control_x + 0.08, 0, row_z),
				)
				self.swatches[key] = swatch
				self.hex_labels[key] = hex_label
				widget = swatch
				if kind == "color_opt":
					auto_check = DirectCheckButton(
						parent=canvas, text="auto", text_scale=LABEL_SCALE * 0.85, text_fg=(1, 1, 1, 1),
						indicatorValue=(value is None), command=self._make_auto_cb(key),
						scale=0.03, pos=(control_x + 0.24, 0, row_z),
					)
					self.auto_checks[key] = auto_check
			self.field_widgets[widget_key] = widget
		# DirectScrolledFrame otherwise defaulted to a non-zero starting
		# scroll position (confirmed live: the field list opened already
		# scrolled roughly halfway down, to "Wheelchair color" instead
		# of the actual first field "Skin") - setting this only takes
		# once the canvas actually has its real rows in it (set any
		# earlier, before the loop above, and something inside
		# DirectScrolledFrame quietly reset it back to that same
		# non-zero default anyway).
		self.scroll.verticalScroll["value"] = 0.0
		self._suppress_rebuild = False

	def _field_by_key(self, key):
		return next(f for f in CUSTOM_FIELDS if f["key"] == key)

	def _swatch_color(self, field, value):
		hexval = value if value is not None else "#000000"
		return avatar3d._hex_to_rgba(hexval) if value is not None else (0, 0, 0, 0)

	def _refresh_color_row(self, key):
		"""Updates a color/color_opt row's own swatch/hex-label/auto-
		checkbox display from self.custom[key] - called after the
		picker (a drag) or the auto checkbox changes that value out from
		under the row's widgets, same idea as _sync_all_widgets but for
		just the one row that actually changed instead of all 43."""
		field = self._field_by_key(key)
		value = self.custom[key]
		self.swatches[key]["frameColor"] = self._swatch_color(field, value)
		self.hex_labels[key]["text"] = _field_value_text(field, value)
		if key in self.auto_checks:
			self.auto_checks[key]["indicatorValue"] = value is None
			self.auto_checks[key].setIndicatorValue()

	def _make_bool_cb(self, key):
		def cb(status):
			if self._suppress_rebuild:
				return
			self.custom[key] = bool(status)
			self._on_custom_changed()
		return cb

	def _make_multi_bool_cb(self, key, item):
		def cb(status):
			if self._suppress_rebuild:
				return
			active = list(self.custom[key])
			if status and item not in active:
				active.append(item)
			elif not status and item in active:
				active.remove(item)
			self.custom[key] = active
			self._on_custom_changed()
		return cb

	def _make_slider_cb(self, key, step):
		def cb():
			if self._suppress_rebuild:
				return
			raw = self.field_widgets[key]["value"]
			self.custom[key] = round(round(raw / step) * step, 2)
			self._on_custom_changed()
		return cb

	def _make_choice_cb(self, key, values, labels):
		def cb(selected_label):
			if self._suppress_rebuild:
				return
			self.custom[key] = values[labels.index(selected_label)]
			self._on_custom_changed()
		return cb

	def _make_open_picker_cb(self, key):
		def cb():
			self._open_color_picker(key)
		return cb

	def _make_auto_cb(self, key):
		"""The color_opt-only "auto" checkbox - checked means the field
		stays None (build_rig()'s own default for that trait, e.g.
		eyebrow_color matching hair_color), unchecked means it's a real
		picked color again. _last_picked remembers the last real color
		so un-checking "auto" restores it instead of resetting to some
		arbitrary default every time."""
		def cb(status):
			if self._suppress_rebuild:
				return
			field = self._field_by_key(key)
			if status:
				if self.custom[key] is not None:
					self._last_picked[key] = self.custom[key]
				self.custom[key] = None
			else:
				self.custom[key] = self._last_picked.get(key, field["options"][0])
			self._refresh_color_row(key)
			self._on_custom_changed()
		return cb

	# ---- the color-gradient/SV-square picker popup ----

	def _open_color_picker(self, key):
		"""Opens the picker for one color/color_opt field - clicking a
		color_opt field's swatch while it's "auto" (None) first gives it
		a real starting color (its last picked one, or its curated
		list's first entry the very first time) exactly like un-checking
		"auto" would, since there's no color to point a hue/SV picker at
		otherwise; the checkbox itself then reflects that via
		_refresh_color_row, so this and un-checking "auto" always agree."""
		field = self._field_by_key(key)
		if self.custom[key] is None:
			self.custom[key] = self._last_picked.get(key, field["options"][0])
			self._refresh_color_row(key)
			self._on_custom_changed()

		self._close_color_picker()
		self._picker_key = key
		h, s, v = _hex_to_hsv(self.custom[key])
		self._picker_hue, self._picker_s, self._picker_v = h, s, v

		root = DirectFrame(
			frameColor=(0.05, 0.05, 0.08, 0.97),
			frameSize=(-POPUP_HALF_W, POPUP_HALF_W, -POPUP_HALF_H, POPUP_HALF_H),
			pos=POPUP_POS,
		)
		self.color_popup = root
		DirectLabel(
			parent=root, text=field["label"], text_scale=0.042, text_fg=(1, 1, 1, 1),
			relief=None, pos=(0, 0, POPUP_HALF_H - 0.07),
		)
		self._picker_hex_label = DirectLabel(
			parent=root, text=self.custom[key], text_scale=0.034, text_fg=(0.8, 0.9, 1, 1),
			relief=None, pos=(0, 0, POPUP_HALF_H - 0.13),
		)

		OnscreenImage(image=_hue_bar_texture(), parent=root, pos=(0, 0, HUE_BAR_Z), scale=(HUE_BAR_W / 2, 1, HUE_BAR_H / 2))
		hue_hit = DirectButton(
			parent=root, relief=None, text="",
			frameSize=(-HUE_BAR_W / 2, HUE_BAR_W / 2, -HUE_BAR_H / 2, HUE_BAR_H / 2),
			pos=(0, 0, HUE_BAR_Z),
		)
		hue_hit.bind(DGG.B1PRESS, self._start_drag_hue)
		# A real, direct correction: a plain white marker all but
		# disappeared against the hue bar/SV square's own light regions
		# (confirmed live - "you can't see the dot on the square except
		# for the edges") - every marker is now a black outline around a
		# white fill (two nested frames, black one drawn first/bigger so
		# a thin ring of it shows around the white), which stays visible
		# against light AND dark backgrounds alike, unlike either color
		# alone.
		hue_pos = (-HUE_BAR_W / 2 + h * HUE_BAR_W, 0, HUE_BAR_Z)
		self._hue_marker_outer = DirectFrame(
			parent=root, frameColor=(0, 0, 0, 1),
			frameSize=(-0.008, 0.008, -HUE_BAR_H / 2 - 0.014, HUE_BAR_H / 2 + 0.014),
			pos=hue_pos,
		)
		self._hue_marker = DirectFrame(
			parent=root, frameColor=(1, 1, 1, 1),
			frameSize=(-0.004, 0.004, -HUE_BAR_H / 2 - 0.010, HUE_BAR_H / 2 + 0.010),
			pos=hue_pos,
		)

		self._sv_image = OnscreenImage(image=_sv_square_texture(h), parent=root, pos=(0, 0, SV_Z), scale=(SV_SIZE / 2, 1, SV_SIZE / 2))
		sv_hit = DirectButton(
			parent=root, relief=None, text="",
			frameSize=(-SV_SIZE / 2, SV_SIZE / 2, -SV_SIZE / 2, SV_SIZE / 2),
			pos=(0, 0, SV_Z),
		)
		sv_hit.bind(DGG.B1PRESS, self._start_drag_sv)
		sv_pos = (-SV_SIZE / 2 + s * SV_SIZE, 0, SV_Z - SV_SIZE / 2 + v * SV_SIZE)
		self._sv_marker_outer = DirectFrame(
			parent=root, frameColor=(0, 0, 0, 1),
			frameSize=(-0.016, 0.016, -0.016, 0.016),
			pos=sv_pos,
		)
		self._sv_marker = DirectFrame(
			parent=root, frameColor=(1, 1, 1, 1),
			frameSize=(-0.010, 0.010, -0.010, 0.010),
			pos=sv_pos,
		)

		DirectButton(
			parent=root, text="Close", text_scale=0.9, scale=0.045,
			frameColor=(0.2, 0.2, 0.26, 1), text_fg=(1, 1, 1, 1),
			command=self._close_color_picker,
			pos=(0, 0, -POPUP_HALF_H + 0.07),
		)

	def _start_drag_hue(self, *_args):
		self._drag_target = "hue"
		self._apply_drag()

	def _start_drag_sv(self, *_args):
		self._drag_target = "sv"
		self._apply_drag()

	def _picker_release(self):
		self._drag_target = None

	def _apply_drag(self):
		"""Called once from the hue/SV press handlers (so a plain click
		with no drag still moves the marker) and then again every frame
		from _update while a button is held (see __init__'s own
		"mouse1-up" binding - drag position tracking can't just be a
		bind() on the hue bar/SV square themselves, since dragging
		routinely carries the mouse outside whichever one the drag
		started over, and DirectGUI only calls a frame's own bound
		handler while the cursor is actually over that frame)."""
		if self.color_popup is None or self._drag_target is None:
			return
		if not self.mouseWatcherNode.hasMouse():
			return
		mx, mz = self.mouseWatcherNode.getMouseX(), self.mouseWatcherNode.getMouseY()
		local_x, local_z = mx - POPUP_POS[0], mz - POPUP_POS[2]

		if self._drag_target == "hue":
			self._picker_hue = max(0.0, min(1.0, (local_x + HUE_BAR_W / 2) / HUE_BAR_W))
		else:
			self._picker_s = max(0.0, min(1.0, (local_x + SV_SIZE / 2) / SV_SIZE))
			self._picker_v = max(0.0, min(1.0, (local_z - (SV_Z - SV_SIZE / 2)) / SV_SIZE))
		self._commit_picker_value()

	# Fraction of the 0..1 hue/saturation/value range covered per second
	# while a picker key is held - slowed down from an original 0.8
	# (full range in ~1.2s) per direct feedback that it moved too fast
	# to land on a precise value; a full sweep now takes ~4s, closer to
	# fine-tuning speed while still reaching either end in a reasonable
	# hold.
	PICKER_KEY_SPEED = 0.25

	def _update_picker_keys(self, dt):
		"""The keyboard half of the color picker ("color slider should
		be controlled by WASD and Arrows") - WASD moves the saturation/
		value square (A/D = saturation, W/S = value, matching the
		square's own x/y layout), Left/Right/Up/Down nudge the hue bar/
		value the same way, all held-key continuous exactly like the
		camera's own WASD orbit is when no picker is open (see
		_update's own branch on self.color_popup deciding which job
		these same keys do each frame)."""
		if self.color_popup is None:
			return
		changed = False
		if self.keys["a"]:
			self._picker_s = max(0.0, self._picker_s - self.PICKER_KEY_SPEED * dt)
			changed = True
		if self.keys["d"]:
			self._picker_s = min(1.0, self._picker_s + self.PICKER_KEY_SPEED * dt)
			changed = True
		if self.keys["w"] or self.keys["up"]:
			self._picker_v = min(1.0, self._picker_v + self.PICKER_KEY_SPEED * dt)
			changed = True
		if self.keys["s"] or self.keys["down"]:
			self._picker_v = max(0.0, self._picker_v - self.PICKER_KEY_SPEED * dt)
			changed = True
		if self.keys["left"]:
			self._picker_hue = (self._picker_hue - self.PICKER_KEY_SPEED * dt) % 1.0
			changed = True
		if self.keys["right"]:
			self._picker_hue = (self._picker_hue + self.PICKER_KEY_SPEED * dt) % 1.0
			changed = True
		if changed:
			self._commit_picker_value()

	def _commit_picker_value(self):
		"""Applies self._picker_hue/_s/_v - however they just changed,
		a mouse drag (_apply_drag) or a keyboard nudge
		(_update_picker_keys) - to both markers' positions, the SV
		square's own texture (regenerated for the current hue; cheap
		enough at SV_TEX_SIZE that redoing it even on a pure S/V-only
		change, where it wouldn't actually look any different, isn't
		worth special-casing around), the row's swatch/hex label, and a
		real rig rebuild - the single shared "a new color was picked"
		tail both input paths funnel through."""
		h, s, v = self._picker_hue, self._picker_s, self._picker_v
		hue_pos = (-HUE_BAR_W / 2 + h * HUE_BAR_W, 0, HUE_BAR_Z)
		self._hue_marker.setPos(hue_pos)
		self._hue_marker_outer.setPos(hue_pos)
		sv_pos = (-SV_SIZE / 2 + s * SV_SIZE, 0, SV_Z - SV_SIZE / 2 + v * SV_SIZE)
		self._sv_marker.setPos(sv_pos)
		self._sv_marker_outer.setPos(sv_pos)
		self._sv_image.setImage(_sv_square_texture(h))
		# setImage() just above rebuilds the OnscreenImage's own internal
		# card geometry from scratch - freshly (re)created, it lands at
		# the END of root's children, so it drew ON TOP of (hiding) the
		# marker frames, which had been added earlier and so were no
		# longer last - a real, direct correction: "it disappears the
		# moment i press a key" (every keyboard nudge regenerates this
		# texture, even a pure saturation/value one that wouldn't
		# otherwise touch it at all - see this method's own reasoning
		# above for why regenerating it unconditionally is still fine).
		# Reparenting the markers back onto the popup root puts them at
		# the end of ITS child list again, restoring them to the front.
		self._sv_marker_outer.reparentTo(self.color_popup)
		self._sv_marker.reparentTo(self.color_popup)
		r, g, b = colorsys.hsv_to_rgb(h, s, v)
		hexval = "#%02x%02x%02x" % (round(r * 255), round(g * 255), round(b * 255))
		key = self._picker_key
		self.custom[key] = hexval
		self._last_picked[key] = hexval
		self._picker_hex_label["text"] = hexval
		self._refresh_color_row(key)
		self._on_custom_changed()

	def _close_color_picker(self):
		if self.color_popup is not None:
			self.color_popup.destroy()
			self.color_popup = None
		self._drag_target = None
		self._picker_key = None

	def _on_custom_changed(self):
		# A saved-seed snapshot no longer matches once it's been edited -
		# clear the indicator rather than keep showing a seed id next to
		# values that have since diverged from it.
		self.seed_cursor = -1
		self._refresh_seed_label()
		self._rebuild()

	def _sync_all_widgets(self):
		"""Pushes self.custom into every field widget's displayed value
		- used after a seed load or a Reset, where self.custom changes
		out from under the widgets rather than via one of their own
		commands. Guarded by _suppress_rebuild so setting 43 widgets in
		a row triggers zero rig rebuilds instead of 43 (see __init__'s
		own comment on this)."""
		self._suppress_rebuild = True
		for field in CUSTOM_FIELDS:
			key = field["key"]
			widget = self.field_widgets[_field_widget_key(field)]
			value = self.custom[key]
			if field["kind"] == "bool":
				widget["indicatorValue"] = value
				widget.setIndicatorValue()
			elif field["kind"] == "multi_bool":
				widget["indicatorValue"] = field["item"] in value
				widget.setIndicatorValue()
			elif field["kind"] == "slider":
				widget["value"] = value
			elif field["kind"] == "choice":
				values = widget._preview_values
				widget.set(values.index(value) if value in values else 0)
			else:  # "color" / "color_opt"
				self._refresh_color_row(key)
		self._suppress_rebuild = False

	def _refresh_seed_label(self):
		if 0 <= self.seed_cursor < len(self.seed_ids):
			self.seed_label["text"] = f"seed: {self.seed_ids[self.seed_cursor]}"
		else:
			self.seed_label["text"] = "seed: (unsaved)"

	def _on_motion_menu(self, selected_name):
		names = [name for name, _fn in self.motions]
		self.index = names.index(selected_name)

	def _sync_motion_menu(self):
		self.motion_menu.set(self.index)

	# ---- rig rebuild ----

	def _rebuild(self):
		"""Rebuilds the whole rig from self.custom - the only
		place that ever calls avatar3d_rig.build_rig(), so every
		customization change (age included - it's a normal field in
		self.custom now) stays a single, consistent code path. Verbose
		debug logging (this project's own hot-reload/debug standard) -
		logs the exact kwargs going into build_rig every time, since a
		live customizer changing 44 fields is exactly the kind of thing
		that needs to be traceable in the log when a combination looks
		wrong."""
		if self.rig is not None:
			self.rig.root.removeNode()
		if self.pet_rig is not None:
			self.pet_rig.root.removeNode()
			self.pet_rig = None
		log.debug(f"rebuild: custom={self.custom}")
		# avatar3d_rig.build_rig() doesn't take pet_species/pet_color -
		# same split as avatar3d_viewer.AvatarCustomizePage._rebuild_rig()
		# (see its own comment for why the pet is a fully separate
		# PetRig, not folded into build_rig() itself).
		human_custom = {k: v for k, v in self.custom.items() if k not in ("pet_species", "pet_color")}
		self.rig = avatar3d_rig.build_rig(**human_custom)
		self.rig.root.reparentTo(self.render)
		self.rig.root.setH(20)
		pet_species = self.custom.get("pet_species", "none")
		if pet_species != "none":
			self.pet_rig = avatar3d_pet_rig.build_pet_rig(species=pet_species, color=self.custom.get("pet_color", "#8b5e3c"))
			self.pet_rig.root.reparentTo(self.render)
			self.pet_rig.root.setH(20)
			# Same hip_half_w recompute as avatar3d_viewer's own identical
			# comment explains.
			gmod = avatar3d.GENDER_MODS.get(self.custom["gender"], avatar3d.GENDER_MODS["nonbinary"])
			weight_scale = 0.82 + self.custom["weight"] * 0.55
			_head_scale, body_scale = avatar3d._age_body_scale(self.custom["age"])
			hip_half_w = (0.28 + self.custom["shape"] * 0.14 + gmod["hip"]) * weight_scale * body_scale
			self.pet_rig.root.setX(hip_half_w + 0.45)

	def reset_defaults(self):
		self.custom = dict(CUSTOM_DEFAULTS)
		self.seed_cursor = -1
		self._sync_all_widgets()
		self._refresh_seed_label()
		self._rebuild()

	def next_motion(self):
		self.index = (self.index + 1) % len(self.motions)
		self._sync_motion_menu()

	def prev_motion(self):
		self.index = (self.index - 1) % len(self.motions)
		self._sync_motion_menu()

	def save_seed(self):
		"""F5 / the panel's "Save seed" button - saves the current trait
		set (age included - a normal field in self.custom now) as a new
		named seed (seed_001, seed_002, ...) to
		avatar3d_previewer_seeds.json. Never overwrites an existing id -
		always finds the next free number - so this is safe to press
		repeatedly without losing earlier saves."""
		seeds = _load_seeds()
		n = 1
		while f"seed_{n:03d}" in seeds:
			n += 1
		seed_id = f"seed_{n:03d}"
		seeds[seed_id] = dict(self.custom)
		with open(SEEDS_PATH, "w") as f:
			json.dump(seeds, f, indent=2)
		self.seed_ids = sorted(seeds.keys())
		self.seed_cursor = self.seed_ids.index(seed_id)
		log.info(f"saved {seed_id} -> {SEEDS_PATH}")
		self._refresh_seed_label()

	def _seed_step(self, delta):
		seeds = _load_seeds()
		self.seed_ids = sorted(seeds.keys())
		if not self.seed_ids:
			log.info("no saved seeds yet - press F5 to save one")
			return
		self.seed_cursor = (self.seed_cursor + delta) % len(self.seed_ids)
		seed_id = self.seed_ids[self.seed_cursor]
		data = seeds[seed_id]
		# Filter to known keys rather than trusting the file wholesale -
		# a seed saved by an older/newer version of CUSTOM_FIELDS could
		# otherwise hand build_rig() a kwarg it no longer accepts.
		self.custom.update({k: v for k, v in data.items() if k in CUSTOM_DEFAULTS})
		log.info(f"loaded {seed_id}")
		self._sync_all_widgets()
		self._refresh_seed_label()
		self._rebuild()

	def seed_next(self):
		self._seed_step(1)

	def seed_prev(self):
		self._seed_step(-1)

	def _update(self, task):
		dt = max(0.0, task.time - self._last_time)
		self._last_time = task.time

		# WASD/arrows drive the color picker instead of the camera/
		# motion cycling while one's open (see _update_picker_keys's own
		# docstring) - only +/- zoom stays live either way, since it
		# doesn't overlap anything the picker uses.
		if self.color_popup is not None:
			self._update_picker_keys(dt)
		else:
			moved = False
			if self.keys["a"]:
				self.cam_heading -= ORBIT_SPEED * dt
				moved = True
			if self.keys["d"]:
				self.cam_heading += ORBIT_SPEED * dt
				moved = True
			if self.keys["w"]:
				self.cam_pitch = min(80.0, self.cam_pitch + ORBIT_SPEED * dt)
				moved = True
			if self.keys["s"]:
				self.cam_pitch = max(-80.0, self.cam_pitch - ORBIT_SPEED * dt)
				moved = True
			if moved:
				self._update_camera()
		if self.keys["zoom_in"]:
			self.cam_dist = max(MIN_CAM_DIST, self.cam_dist - ZOOM_SPEED * dt)
			self._update_camera()
		if self.keys["zoom_out"]:
			self.cam_dist = min(MAX_CAM_DIST, self.cam_dist + ZOOM_SPEED * dt)
			self._update_camera()

		# Continues an in-progress color-picker drag every frame - see
		# _apply_drag's own docstring on why press alone isn't enough.
		if self._drag_target is not None:
			self._apply_drag()

		t = (task.time % LOOP_SECONDS) / LOOP_SECONDS
		name, _fn = self.motions[self.index]
		avatar3d_anim.play(self.rig, name, t)
		if self.pet_rig is not None:
			# The pet only ever has "idle" (see avatar3d_pet_anim.MOTIONS'
			# own comment on why no walk/run/etc. exist yet) - plays that
			# regardless of whatever motion the person is currently
			# cycled to, rather than erroring on a KeyError for every
			# motion name the pet doesn't have.
			avatar3d_pet_anim.play(self.pet_rig, "idle", t)
		return Task.cont


def main():
	# avatar_plugin_backend.installed_motions() (built-ins AND any
	# built-in-root/installed motion plugin, e.g. "wave" - see
	# avatar_plugins_builtin/motion/wave/) rather than the raw
	# avatar3d_anim.MOTIONS dict alone - a real, confirmed gap the first
	# version had: MOTIONS alone silently dropped "wave" from this
	# dev tool's own cycling list the moment it moved out to a plugin
	# file, even though the live app's own avatar3d_anim.play() still
	# resolves it correctly (see that function's own docstring).
	all_motions = avatar_plugin_backend.installed_motions()
	order = list(all_motions.keys())
	motions = [(name, all_motions[name]) for name in order]

	start_index = 0
	if len(sys.argv) > 1 and sys.argv[1] in all_motions:
		start_index = order.index(sys.argv[1])

	app = LivePreview(motions, start_index)
	app.run()


if __name__ == "__main__":
	main()
