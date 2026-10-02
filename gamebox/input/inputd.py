#!/usr/bin/env python3
"""
Controller input daemon.

Reads raw joystick/gamepad evdev devices (independent of whatever window
or console currently has focus) and translates button/d-pad presses into
synthetic key presses on a virtual keyboard, injected system-wide via
evdev's UInput. This is what makes the existing curses TUI launcher
controller-navigable with zero changes to it: it already reads keyboard
input, so a synthetic KEY_UP/KEY_DOWN/KEY_ENTER looks identical to a real
keypress to it (or to any other app that has console/X input focus).

Both the d-pad (hat axis or discrete buttons) and the left stick (in
navigate mode - see below) tap once immediately on press, then - if
still held past INITIAL_REPEAT_DELAY_S - keep retapping every
REPEAT_INTERVAL_S, so scrolling a long list doesn't mean mashing a
direction over and over. RepeatTracker is the one place that logic
lives; both input sources just feed it their own "is this direction
currently held" state each tick. BTN_SOUTH/BTN_EAST (confirm/back) are
deliberately NOT repeat-tracked - those fire once per real press only,
since auto-repeating a confirm or back action would be actively harmful.

The left stick does one of two jobs, chosen by stick_mode.py's shared
flag: menu-navigation arrow-key taps (the default, repeat-tracked the
same way as the d-pad), or mouse-cursor movement, which only the browser
opts into for its own session - the d-pad keeps working as navigation
either way, since it's a separate physical control from the stick.

This module is deliberately just config/logic, not a process entry
point - run.py owns finding the gamepad, opening the UInput device, and
hot-reloading this module in place while both of those stay open across
edits. run()'s on_tick hook is how run.py drives that check without this
module needing to import hotreload.py itself; when on_tick() returns
True (a reload just happened), run() rebuilds its StickState/
RepeatTracker instances, since reload doesn't retroactively patch
those classes' behavior onto already-existing objects - see
hotreload.py's docstring for why."""
import os
import select
import sys
import time

import evdev
from evdev import ecodes

import gamepad
from gamepad import find_gamepads
import stick_mode
import debuglog

log = debuglog.setup("inputd")

# One-shot action buttons: single tap on press, never repeat-tracked.
# The shoulder buttons (L1/R1) are the first use of anything beyond
# face-button confirm/back - the quick-launch dashboard row's reorder
# gesture (see launcher/app.py) needed a real controller input, not a
# keyboard-only shortcut, and shoulder buttons are the standard console
# convention for "shift this" without colliding with menu navigation.
ACTION_BUTTON_MAP = {
	ecodes.BTN_SOUTH: ecodes.KEY_ENTER,
	# Escape, not a plain letter - every screen/app that treats this as
	# "back"/"quit" needs it to still work with a real text entry (Wi-Fi
	# password, IGDB credentials, a login WebView, music search) focused,
	# which only a non-printable key like Escape can do without either
	# colliding with real typed text or needing app code to special-case
	# "well, unless a controller sent this one" - confirmed the hard way:
	# this used to map to KEY_Q, which every screen had to gate behind
	# "only if nothing is focused for typing", silently doing nothing on
	# any text-entry/login screen.
	ecodes.BTN_EAST: ecodes.KEY_ESC,
	ecodes.BTN_TL: ecodes.KEY_LEFTBRACE,
	ecodes.BTN_TR: ecodes.KEY_RIGHTBRACE,
	# X (Xbox) / Square (PlayStation) - both the same physical position,
	# BTN_WEST - triggers a delete-confirm dialog wherever one exists
	# (see launcher/app.py's confirm()). Not repeat-tracked for the same
	# reason BTN_SOUTH/BTN_EAST aren't: auto-repeating a delete trigger
	# would be actively harmful.
	ecodes.BTN_WEST: ecodes.KEY_DELETE,
	# The Guide/Home button - the big branded button in a real
	# controller's own center, real name BTN_MODE ("button 13" in the
	# conventional SOUTH/EAST/C/NORTH/WEST/Z/TL/TR/TL2/TR2/SELECT/
	# START/MODE gamepad-button counting order). KEY_HELP, not some
	# arbitrary letter, so this can't collide with anything a real text
	# entry might need to type - opens launcher/app.py's own full-screen
	# Controller Help overlay, the CosmOS-native equivalent of what
	# this exact physical button already does on every real platform
	# (Xbox Guide, PS button, Switch Home all open a system-level
	# overlay from here).
	ecodes.BTN_MODE: ecodes.KEY_HELP,
	# The analog triggers (L2/R2) - real, digital EV_KEY events for any
	# controller that reports them that way (confirmed live: this
	# project's own virtual-gamepad passthrough already relies on
	# BTN_TL2/BTN_TR2 being real button codes - see VIRTUAL_PAD_BUTTONS
	# below). First real use: avatar3d_viewer.AvatarCustomizePage's own
	# rotate-the-live-preview control (see launcher/app.py's avatar_picker
	# key handling) - KEY_COMMA/KEY_PERIOD rather than a letter, so
	# there's zero chance of colliding with a real text entry anywhere
	# else in the app. KEY_DOT, not "KEY_PERIOD" (which doesn't exist in
	# the real Linux kernel's own input-event-codes.h/python-evdev's own
	# ecodes module - confirmed live, an AttributeError on import that
	# crashed input/run.py outright) - KEY_DOT is the kernel's actual
	# name for this physical key, and still resolves to the real
	# "period" X11 keysym app.py's own Gdk.KEY_period check expects.
	ecodes.BTN_TL2: ecodes.KEY_COMMA,
	ecodes.BTN_TR2: ecodes.KEY_DOT,
}

# D-pad reported as discrete buttons (some pads report this as well as, or
# instead of, a hat axis) - press/release tracked continuously so held
# buttons can repeat the same way the hat axis and stick do.
DPAD_BUTTON_CODES = {
	ecodes.BTN_DPAD_UP: "up",
	ecodes.BTN_DPAD_DOWN: "down",
	ecodes.BTN_DPAD_LEFT: "left",
	ecodes.BTN_DPAD_RIGHT: "right",
}

NAV_KEYS = {
	"up": ecodes.KEY_UP,
	"down": ecodes.KEY_DOWN,
	"left": ecodes.KEY_LEFT,
	"right": ecodes.KEY_RIGHT,
}

MOUSE_SENSITIVITY = 12
MOUSE_DEADZONE = 4000
NAV_THRESHOLD = 20000  # how decisively the stick must be pushed to count as a d-pad-equivalent press
INITIAL_REPEAT_DELAY_S = 0.5
REPEAT_INTERVAL_S = 0.1
TICK_S = 0.03

# The signed 16-bit range NAV_THRESHOLD/MOUSE_SENSITIVITY/MOUSE_DEADZONE
# above all already assume for ABS_X/Y (an Xbox-style controller's own
# native range) - real devices don't all agree on this. Confirmed live
# against a real 8BitDo Pro 2 in DInput mode: its own ABS_X/Y report a
# plain 0-255 byte, not this range at all - meaning NAV_THRESHOLD
# (20000) could never be reached from it, so the left stick would
# silently never navigate menus on that specific controller. Every
# real axis value gets rescaled through _normalize_abs_value() before
# anything else here ever sees it, using that device's own reported
# real min/max (evdev's own device.absinfo()) - not assumed.
STICK_MIN = -32768
STICK_MAX = 32767


def _normalize_abs_value(device, code, value):
	info = device.absinfo(code)
	if info is None or info.max == info.min:
		return value
	fraction = (value - info.min) / (info.max - info.min)
	return int(STICK_MIN + fraction * (STICK_MAX - STICK_MIN))


def make_virtual_keyboard():
	keys = sorted(set(ACTION_BUTTON_MAP.values()) | set(NAV_KEYS.values()))
	capabilities = {ecodes.EV_KEY: keys, ecodes.EV_REL: [ecodes.REL_X, ecodes.REL_Y]}
	log.debug(f"creating virtual keyboard/mouse UInput device, keys={sorted(keys)}")
	return evdev.UInput(capabilities, name="console-os-virtual-keyboard")


def tap(ui, keycode):
	log.debug(f"tap {ecodes.KEY[keycode]}")
	ui.write(ecodes.EV_KEY, keycode, 1)
	ui.syn()
	ui.write(ecodes.EV_KEY, keycode, 0)
	ui.syn()


def move_mouse(ui, dx, dy):
	if dx:
		ui.write(ecodes.EV_REL, ecodes.REL_X, dx)
	if dy:
		ui.write(ecodes.EV_REL, ecodes.REL_Y, dy)
	ui.syn()


class StickState:
	"""Tracks every raw axis/button source that feeds either the mouse or
	navigate paths, continuously - both need "what's the current state",
	not just discrete transition events."""

	def __init__(self):
		self.x = 0
		self.y = 0
		self.hat_x = 0
		self.hat_y = 0
		self.dpad_buttons = {"up": False, "down": False, "left": False, "right": False}

	def dpad_active(self):
		return {
			"up": self.hat_y == -1 or self.dpad_buttons["up"],
			"down": self.hat_y == 1 or self.dpad_buttons["down"],
			"left": self.hat_x == -1 or self.dpad_buttons["left"],
			"right": self.hat_x == 1 or self.dpad_buttons["right"],
		}

	def stick_nav_active(self):
		return {
			"up": self.y < -NAV_THRESHOLD,
			"down": self.y > NAV_THRESHOLD,
			"left": self.x < -NAV_THRESHOLD,
			"right": self.x > NAV_THRESHOLD,
		}


class RepeatTracker:
	"""Generic press-then-repeat-after-delay tracker for the four named
	directions - shared by the d-pad and the stick-as-nav input, which
	both want "tap once immediately, then keep tapping every
	REPEAT_INTERVAL_S once held past INITIAL_REPEAT_DELAY_S" rather than
	either a single tap or a flood of taps on every poll tick."""

	def __init__(self):
		self.next_repeat = {}

	def tick(self, ui, active, now):
		for direction, is_active in active.items():
			if not is_active:
				if direction in self.next_repeat:
					log.debug(f"{direction} released")
				self.next_repeat.pop(direction, None)
				continue
			if direction not in self.next_repeat:
				log.debug(f"{direction} pressed")
				tap(ui, NAV_KEYS[direction])
				self.next_repeat[direction] = now + INITIAL_REPEAT_DELAY_S
			elif now >= self.next_repeat[direction]:
				log.debug(f"{direction} repeat")
				tap(ui, NAV_KEYS[direction])
				self.next_repeat[direction] = now + REPEAT_INTERVAL_S


def handle_event(ui, event, stick, device):
	"""device is the real source InputDevice event was read from -
	needed to normalize its own ABS_X/Y range (see
	_normalize_abs_value()'s own docstring for why that can't just
	trust the raw value)."""
	if event.type == ecodes.EV_KEY:
		if event.code in ACTION_BUTTON_MAP and event.value == 1:
			tap(ui, ACTION_BUTTON_MAP[event.code])
		elif event.code in DPAD_BUTTON_CODES:
			direction = DPAD_BUTTON_CODES[event.code]
			log.debug(f"dpad button {direction} -> {bool(event.value)}")
			stick.dpad_buttons[direction] = bool(event.value)
	elif event.type == ecodes.EV_ABS:
		if event.code == ecodes.ABS_X:
			stick.x = _normalize_abs_value(device, event.code, event.value)
		elif event.code == ecodes.ABS_Y:
			stick.y = _normalize_abs_value(device, event.code, event.value)
		elif event.code == ecodes.ABS_HAT0X:
			stick.hat_x = event.value
		elif event.code == ecodes.ABS_HAT0Y:
			stick.hat_y = event.value


def mouse_tick(ui, stick):
	dx = int(stick.x / 32767 * MOUSE_SENSITIVITY) if abs(stick.x) > MOUSE_DEADZONE else 0
	dy = int(stick.y / 32767 * MOUSE_SENSITIVITY) if abs(stick.y) > MOUSE_DEADZONE else 0
	if dx or dy:
		move_mouse(ui, dx, dy)


GAMEPAD_RESCAN_INTERVAL_S = 2.0  # cheap - a plain /dev/input directory scan, not real I/O

VIRTUAL_PAD_COUNT = 4

# CosmOS's own fixed, self-defined gamepad shape - not copied from
# any one real device. Every emulator this project pre-configures (see
# emulators/controller_config.py) binds to exactly these capabilities
# once and never again needs to change no matter what real controller
# a given user actually owns - the entire reason this bridge exists.
VIRTUAL_PAD_BUTTONS = [
	ecodes.BTN_SOUTH, ecodes.BTN_EAST, ecodes.BTN_WEST, ecodes.BTN_NORTH,
	ecodes.BTN_TL, ecodes.BTN_TR, ecodes.BTN_TL2, ecodes.BTN_TR2,
	ecodes.BTN_SELECT, ecodes.BTN_START, ecodes.BTN_THUMBL, ecodes.BTN_THUMBR,
]
VIRTUAL_PAD_AXES = [ecodes.ABS_X, ecodes.ABS_Y, ecodes.ABS_RX, ecodes.ABS_RY, ecodes.ABS_HAT0X, ecodes.ABS_HAT0Y]
_STICK_ABS_INFO = evdev.AbsInfo(value=0, min=STICK_MIN, max=STICK_MAX, fuzz=0, flat=0, resolution=0)
_HAT_ABS_INFO = evdev.AbsInfo(value=0, min=-1, max=1, fuzz=0, flat=0, resolution=0)
VIRTUAL_PAD_CAPS = {
	ecodes.EV_KEY: VIRTUAL_PAD_BUTTONS,
	ecodes.EV_ABS: [
		(ecodes.ABS_X, _STICK_ABS_INFO),
		(ecodes.ABS_Y, _STICK_ABS_INFO),
		(ecodes.ABS_RX, _STICK_ABS_INFO),
		(ecodes.ABS_RY, _STICK_ABS_INFO),
		(ecodes.ABS_HAT0X, _HAT_ABS_INFO),
		(ecodes.ABS_HAT0Y, _HAT_ABS_INFO),
	],
}


def make_virtual_gamepads(count=VIRTUAL_PAD_COUNT):
	"""Created once, up front, and kept alive for run()'s whole life -
	always exactly `count` of these regardless of whether any real
	controller is even plugged in yet, so every pre-configured emulator
	always sees the same fixed set of devices to bind to (see
	gamepad.py's own VIRTUAL_PAD_NAME_PREFIX, excluded from
	find_gamepads() so the bridge never mistakes its own output for a
	5th real controller)."""
	pads = [evdev.UInput(VIRTUAL_PAD_CAPS, name=f"{gamepad.VIRTUAL_PAD_NAME_PREFIX}{i}") for i in range(1, count + 1)]
	log.info(f"created {len(pads)} virtual gamepad(s): {[p.name for p in pads]}")
	return pads


# Real per-model axis quirks, keyed by (vendor, product) - see
# gamepad.py's own VENDOR_* constants for brand-level ids; this table
# is for per-MODEL oddities those don't cover. The 8BitDo Pro 2 is the
# first real, confirmed entry (not guessed): in DInput mode it reports
# its right stick on ABS_Z/ABS_RZ, not the far more common ABS_RX/
# ABS_RY every virtual pad here actually exposes.
AXIS_QUIRKS = {
	(0x2DC8, 0x6006): {ecodes.ABS_Z: ecodes.ABS_RX, ecodes.ABS_RZ: ecodes.ABS_RY},
}


def _make_remapper():
	"""The per-virtual-pad button/stick remapping (cosmos_remap.py, edited by CosmOS's Controller
	Customizer). Optional: without the module (a GameBox checkout on its own) there is simply no
	remapping and every event is mirrored as before."""
	for directory in (os.environ.get("COSMOS_INPUT_DIR"), "/opt/cosmos-input", "/usr/lib/cosmos"):
		if directory and os.path.exists(os.path.join(directory, "cosmos_remap.py")) and directory not in sys.path:
			sys.path.append(directory)
	try:
		import cosmos_remap
		return cosmos_remap.Remapper()
	except Exception:
		log.debug("no cosmos_remap - virtual pads are not remapped", exc_info=True)
		return None


def mirror_event(virtual_pad, event, device, axis_quirk, remap=None):
	"""Replays one real gamepad event onto `virtual_pad` - real
	gameplay input, independent of (and in addition to) whatever
	handle_event() already did with the same event for menu-keyboard
	translation. Silently drops anything outside VIRTUAL_PAD_BUTTONS/
	VIRTUAL_PAD_AXES (BTN_DPAD_* - some pads report the d-pad as
	buttons rather than a hat axis - in particular) rather than
	raising: a real controller reporting something CosmOS's own
	virtual pad doesn't declare capabilities for isn't an error, just
	nothing to mirror.

	`remap` (a cosmos_remap.SlotRemap for this virtual pad, or None) is
	the player's own button/stick remapping, applied on the way out: a
	physical button can become another one or be dropped, the sticks can
	be swapped, and an axis can be inverted."""
	if event.type == ecodes.EV_KEY:
		code = remap.button(event.code) if remap else event.code
		if code is not None and code in VIRTUAL_PAD_BUTTONS:
			virtual_pad.write(ecodes.EV_KEY, code, event.value)
			virtual_pad.syn()
	elif event.type == ecodes.EV_ABS:
		code = axis_quirk.get(event.code, event.code)
		if code in VIRTUAL_PAD_AXES:
			value = event.value if code in (ecodes.ABS_HAT0X, ecodes.ABS_HAT0Y) else _normalize_abs_value(device, event.code, event.value)
			if remap:
				code, value = remap.axis(code, value)
			virtual_pad.write(ecodes.EV_ABS, code, value)
			virtual_pad.syn()


def _assign_slot(slots, device):
	"""First free virtual slot index (0-based, matching position in
	run()'s own virtual_pads list), in connection order - the same
	"first come, first served" a real console already uses for player
	order. A 5th+ real controller (more than VIRTUAL_PAD_COUNT already
	assigned) has nowhere to map to and is simply left unassigned - the
	same fixed port count a real console has."""
	used = set(slots.values())
	free = next((i for i in range(VIRTUAL_PAD_COUNT) if i not in used), None)
	if free is not None:
		slots[device.path] = free
		log.info(f"assigned {device.name} to virtual gamepad slot {free + 1}")
	else:
		log.warning(f"no free virtual gamepad slot for {device.name} - already {VIRTUAL_PAD_COUNT} assigned")


def _rescan_gamepads(by_path, slots):
	"""Adds any newly-connected gamepad, drops any that disappeared -
	keyed by each device's own stable /dev/input/eventN path, not fd:
	fds get reused by the kernel the instant a device closes, so
	comparing by fd could quietly conflate a genuinely new controller
	with a stale, already-gone one that happened to land on the same
	number. Mutates `by_path`/`slots` in place; run()'s own select()
	list is rebuilt fresh from `by_path` every tick, so nothing else
	needs to know a rescan just happened."""
	current_paths = set()
	for device in find_gamepads():
		current_paths.add(device.path)
		if device.path not in by_path:
			log.info(f"gamepad connected: {device.name} ({device.path})")
			by_path[device.path] = device
			_assign_slot(slots, device)
	for path in list(by_path):
		if path not in current_paths:
			log.info(f"gamepad disconnected: {by_path[path].name} ({path})")
			del by_path[path]
			slots.pop(path, None)


def run(gamepads, ui, virtual_pads, on_tick=None):
	"""on_tick, if given, is called once per loop iteration and should
	return True exactly when it just reloaded this module - see the
	module docstring for why that means rebuilding stick/the repeaters.

	`gamepads` is just the starting set, not a fixed one for the whole
	process's life - real hotplug support (plugging in a controller
	after this loop is already running, or unplugging one without taking
	the whole input daemon down with it) needs this loop to keep
	watching for both, not just read whatever was connected the moment
	it started. Confirmed live this was a real, not hypothetical, gap: a
	controller plugged in after this function was entered was silently
	never read at all, and unplugging an already-open one raised an
	uncaught OSError out of select()/read() that killed the process,
	losing ALL controller input system-wide (any second, still-connected
	controller included) until something restarted it by hand.

	Handles both: _rescan_gamepads() re-lists real gamepad devices every
	GAMEPAD_RESCAN_INTERVAL_S (cheap enough - a plain evdev device scan,
	not real I/O - to just always do on a timer rather than needing a
	real udev hotplug subscription) to pick up new connections and drop
	ones that vanished since the last rescan; select()/read() are also
	each wrapped individually for the narrower window between rescans
	where a device can disappear mid-tick - caught and dropped rather
	than left to crash the whole loop.

	`virtual_pads` (see make_virtual_gamepads()) is the fixed, always-
	present set of up to VIRTUAL_PAD_COUNT gameplay-facing pads real
	controllers get mirrored onto (see mirror_event()) - a completely
	separate concern from `ui`, the virtual keyboard menu-navigation
	alone still uses; both happen for every real event, independently.

	Honest limit on how "hot" this reload really is: this function is
	called once by run.py and its `while True` loop never returns, so
	it's permanently on the call stack running whatever bytecode was
	loaded when it started. Reassigning module-level functions/constants
	(handle_event, RepeatTracker, ACTION_BUTTON_MAP, TICK_S, ...) takes
	effect immediately because every reference to them inside this loop
	is a fresh global lookup each time, not a cached one - that covers
	the vast majority of real edits (tweak a mapping, fix a handler, add
	a button). Restructuring this loop's own control flow is the one
	thing that can't hot-swap, since nothing re-enters run() itself; that
	needs an actual process restart."""
	log.info(f"input loop starting with {len(gamepads)} gamepad(s): {[d.name for d in gamepads]}")
	by_path = {d.path: d for d in gamepads}
	slots = {}
	for device in gamepads:
		_assign_slot(slots, device)
	remapper = _make_remapper()
	stick = StickState()
	dpad_repeater = RepeatTracker()
	stick_repeater = RepeatTracker()
	next_rescan = time.monotonic()

	while True:
		now = time.monotonic()
		if now >= next_rescan:
			_rescan_gamepads(by_path, slots)
			next_rescan = now + GAMEPAD_RESCAN_INTERVAL_S

		devices = list(by_path.values())
		ready = []
		if devices:
			try:
				ready, _, _ = select.select(devices, [], [], TICK_S)
			except (OSError, ValueError):
				# A device vanished between the rescan above and this
				# select() call (an unplug mid-tick) - select() can't say
				# which one, so just skip this tick's read entirely; the
				# next rescan (at most GAMEPAD_RESCAN_INTERVAL_S away)
				# drops the dead one from by_path for real.
				log.warning("select() failed - a controller likely just disconnected", exc_info=True)
		else:
			time.sleep(TICK_S)

		for device in ready:
			try:
				events = list(device.read())
			except OSError:
				log.info(f"gamepad disconnected mid-read: {device.name} ({device.path})")
				by_path.pop(device.path, None)
				slots.pop(device.path, None)
				continue
			slot = slots.get(device.path)
			axis_quirk = AXIS_QUIRKS.get((device.info.vendor, device.info.product), {}) if slot is not None else None
			remap = remapper.for_slot(slot) if remapper and slot is not None else None
			for event in events:
				handle_event(ui, event, stick, device)
				if slot is not None:
					mirror_event(virtual_pads[slot], event, device, axis_quirk, remap)

		now = time.monotonic()
		dpad_repeater.tick(ui, stick.dpad_active(), now)
		if stick_mode.is_mouse_mode():
			mouse_tick(ui, stick)
		else:
			stick_repeater.tick(ui, stick.stick_nav_active(), now)

		if on_tick and on_tick():
			log.info("rebuilding StickState/RepeatTracker instances after hot reload")
			stick = StickState()
			dpad_repeater = RepeatTracker()
			stick_repeater = RepeatTracker()
