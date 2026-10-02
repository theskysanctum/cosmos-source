#!/usr/bin/env python3
"""Shared gamepad/keyboard discovery, used by inputd.py (TUI navigation)
and the pause menu (which watches for its hotkey directly, independent
of whatever inputd.py is doing - evdev allows multiple non-exclusive
readers of the same device)."""
import sys
from pathlib import Path

import evdev
from evdev import ecodes

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import debuglog  # noqa: E402

log = debuglog.setup("gamepad")


# The virtual gamepads inputd.py's own bridge creates (see
# VIRTUAL_PAD_NAME_PREFIX there) report real BTN_SOUTH like any other
# gamepad - excluded here by name so the bridge's own output is never
# mistaken for a genuine 5th real controller by find_gamepads()' own
# callers (the rescan loop that assigns real controllers to virtual
# slots in the first place, in particular - which would otherwise try
# to feed the bridge's own output back into itself).
VIRTUAL_PAD_NAME_PREFIX = "CosmOS Pad "


def is_gamepad(device):
	if device.name.startswith(VIRTUAL_PAD_NAME_PREFIX):
		return False
	caps = device.capabilities()
	keys = caps.get(ecodes.EV_KEY, [])
	return ecodes.BTN_SOUTH in keys or ecodes.BTN_GAMEPAD in keys


def find_gamepads():
	devices = [evdev.InputDevice(p) for p in evdev.list_devices()]
	gamepads = [d for d in devices if is_gamepad(d)]
	names = [d.name for d in gamepads]
	log.debug(f"scanned {len(devices)} input device(s), {len(gamepads)} look like gamepads: {names}")
	return gamepads


# Real USB vendor ids (also preserved over Bluetooth HID) - the reliable
# way to identify a controller's actual manufacturer, rather than
# string-matching device.name (which varies a lot by driver/firmware -
# "Xbox Wireless Controller", "Microsoft X-Box One S pad", etc. all mean
# the same thing but no single substring covers every real string).
VENDOR_MICROSOFT = 0x045E
VENDOR_SONY = 0x054C
VENDOR_NINTENDO = 0x057E


def detect_brand():
	"""Which real controller brand (if any) is currently connected - see
	branding/generate_controller_translations.py's own three overlays,
	which this picks between. Returns "xbox"/"playstation"/"switch" for a
	recognized vendor id, or None for no gamepad at all (or one from an
	unrecognized vendor) - callers treat None as "show the plain,
	untranslated CosmOS layout", never guessing at a mapping we don't
	actually know is correct."""
	for device in find_gamepads():
		vendor = device.info.vendor
		if vendor == VENDOR_MICROSOFT:
			return "xbox"
		if vendor == VENDOR_SONY:
			return "playstation"
		if vendor == VENDOR_NINTENDO:
			return "switch"
	return None


def is_keyboard(device):
	"""Just checks for a real keyboard's Home key specifically, not
	general "is this a keyboard" - the one thing the pause menu (its
	only caller) actually needs a keyboard device for."""
	caps = device.capabilities()
	keys = caps.get(ecodes.EV_KEY, [])
	return ecodes.KEY_HOME in keys


def find_keyboards():
	devices = [evdev.InputDevice(p) for p in evdev.list_devices()]
	keyboards = [d for d in devices if is_keyboard(d)]
	names = [d.name for d in keyboards]
	log.debug(f"scanned {len(devices)} input device(s), {len(keyboards)} look like keyboards: {names}")
	return keyboards
