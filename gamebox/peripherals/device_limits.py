#!/usr/bin/env python3
"""Caps how many EXTERNAL audio-output, audio-input (microphone), and
video (camera) devices CosmOS will actively use at once - 4/1/1
respectively, matching the console's own fixed 4-controller-port model
(input/inputd.py's own VIRTUAL_PAD_COUNT and _assign_slot()) applied to
these other peripheral categories too, wired or Bluetooth alike. A
device beyond its category's cap is left alone at the OS level - still
paired/connected, still visible to `pactl`/udev - but never becomes an
active default in CosmOS's own routing, the same "silently invisible,
not actively blocked" behavior the controller cap already uses (per
direct instruction).

Real per-device state (which sinks/sources this module itself has muted
for being over-cap) lives in DeviceLimiter, not module globals - a
device this module never touched keeps whatever mute state the user (or
anything else) set on it; this module only ever reverses its *own*
mute, never someone else's.

"External" is decided per-device from real bus/sysfs info, not assumed
from USB vs PCI/Bluetooth alone - confirmed live that a laptop's own
built-in webcam reports `device.bus`/subsystem "usb" exactly like a
real pluggable camera would, so USB-bus devices are further checked
against their own sysfs "removable" attribute (walked up from the
actual audio/video device's sysfs path) - "fixed" means built-in
regardless of bus, anything else counts. PCI-bus devices are always
built-in outright (no "removable" file to even check); Bluetooth
devices are always external."""
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import debuglog  # noqa: E402

log = debuglog.setup("peripherals.device_limits")

AUDIO_OUT_CAP = 4
AUDIO_IN_CAP = 1
VIDEO_CAP = 1

PACTL_TIMEOUT_S = 8


def _is_external(bus, sysfs_path):
	if bus == "bluetooth":
		return True
	if bus == "pci":
		return False
	if bus != "usb" or not sysfs_path:
		# Anything not on a bus this module recognizes is treated as
		# external (counts toward the cap) rather than silently exempted -
		# the safer default per this project's own "known cap, not a
		# guess" reasoning elsewhere (see PCSX2/Dolphin netplay writeups).
		return True
	path = Path(sysfs_path)
	for _ in range(8):
		removable = path / "removable"
		if removable.exists():
			return removable.read_text().strip() != "fixed"
		if path == path.parent:
			break
		path = path.parent
	return True


def _pactl_devices(kind):
	"""kind: "sinks" or "sources". Returns real hardware devices only -
	a source's own ".monitor" entry (PipeWire's loopback tap of an
	output sink, not a real microphone) is filtered out here rather than
	left for callers to remember to exclude."""
	try:
		result = subprocess.run(
			["pactl", "-f", "json", "list", kind], capture_output=True, text=True, timeout=PACTL_TIMEOUT_S
		)
		items = json.loads(result.stdout)
	except Exception:
		log.exception(f"pactl list {kind} failed")
		return []
	devices = []
	for item in items:
		name = item.get("name", "")
		if kind == "sources" and name.endswith(".monitor"):
			continue
		props = item.get("properties", {})
		devices.append({
			"name": name,
			"bus": props.get("device.bus"),
			"sysfs_path": props.get("sysfs.path"),
			"muted": item.get("mute", False),
		})
	return devices


def _video_devices():
	"""Real /sys/class/video4linux nodes, deduplicated to one entry per
	physical device - a single USB webcam commonly exposes several
	/dev/videoN nodes (capture + metadata) that all share one real
	underlying sysfs device, confirmed live against this dev machine's
	own built-in camera (HP Wide Vision HD Camera: two video nodes, one
	shared /sys/devices/.../usb3/3-6 parent)."""
	devices = []
	base = Path("/sys/class/video4linux")
	if not base.exists():
		return devices
	seen = set()
	for entry in sorted(base.iterdir()):
		device_link = entry / "device"
		if not device_link.exists():
			continue
		sysfs_path = str(device_link.resolve())
		if sysfs_path in seen:
			continue
		seen.add(sysfs_path)
		subsystem_link = device_link / "subsystem"
		bus = subsystem_link.resolve().name if subsystem_link.exists() else None
		name_file = entry / "name"
		name = name_file.read_text().strip() if name_file.exists() else entry.name
		devices.append({"name": name, "bus": bus, "sysfs_path": sysfs_path})
	return devices


def external_audio_outputs():
	return [d for d in _pactl_devices("sinks") if _is_external(d["bus"], d["sysfs_path"])]


def external_audio_inputs():
	return [d for d in _pactl_devices("sources") if _is_external(d["bus"], d["sysfs_path"])]


def external_video_devices():
	return [d for d in _video_devices() if _is_external(d["bus"], d["sysfs_path"])]


def _set_mute(kind, name, mute):
	verb = "set-sink-mute" if kind == "sinks" else "set-source-mute"
	subprocess.run(["pactl", verb, name, "1" if mute else "0"], capture_output=True, timeout=PACTL_TIMEOUT_S)


class DeviceLimiter:
	"""Tracks, across repeated enforce() calls, which external audio
	devices this module itself has muted for being over their category's
	cap - so a later enforce() only ever unmutes a device *this module*
	muted (a device already beyond the cap when it connects, or one the
	user separately muted on their own, is never touched either way).
	Slot order is first-come-first-served, mirroring inputd.py's own
	_assign_slot()/_rescan_gamepads() - a device that's been assigned a
	slot keeps it as long as it stays connected, even if the order
	devices were *discovered* changes between polls."""

	def __init__(self):
		self._slots = {"sinks": {}, "sources": {}}  # {name: slot_index}
		self._muted_by_us = {"sinks": set(), "sources": set()}

	def _enforce_kind(self, kind, devices, cap):
		current_names = {d["name"] for d in devices}
		for name in list(self._slots[kind]):
			if name not in current_names:
				del self._slots[kind][name]
				self._muted_by_us[kind].discard(name)

		used_slots = set(self._slots[kind].values())
		for device in devices:
			name = device["name"]
			if name not in self._slots[kind]:
				free = next((i for i in range(cap) if i not in used_slots), None)
				if free is not None:
					self._slots[kind][name] = free
					used_slots.add(free)
					log.info(f"{kind}: {name} assigned slot {free + 1}/{cap}")
				else:
					log.info(f"{kind}: {name} over cap ({cap}) - muting")

			over_cap = name not in self._slots[kind]
			already_muted_by_us = name in self._muted_by_us[kind]
			if over_cap and not already_muted_by_us:
				_set_mute(kind, name, True)
				self._muted_by_us[kind].add(name)
			elif not over_cap and already_muted_by_us:
				_set_mute(kind, name, False)
				self._muted_by_us[kind].discard(name)

	def enforce(self):
		"""One pass: re-check every external audio device against its
		category's cap, muting newly-over-cap devices and restoring
		anything this module previously muted that now has a free slot.
		Video devices have no mute-equivalent lever at the OS level (no
		app in this project consumes a camera yet) - external_video_devices()
		exists for a future caller to consult directly (first VIDEO_CAP
		entries = allowed), nothing to enforce here yet."""
		self._enforce_kind("sinks", external_audio_outputs(), AUDIO_OUT_CAP)
		self._enforce_kind("sources", external_audio_inputs(), AUDIO_IN_CAP)
