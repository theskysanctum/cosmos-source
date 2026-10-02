#!/usr/bin/env python3
"""Where each emulator's saves actually live, redirected via a plain
symlink swap on each emulator's real, default save directory rather than
per-emulator config-file/CLI-flag edits: RetroArch/Dolphin/PCSX2/RPCS3/
Cemu/etc. all disagree on how to override a save path, but they all just
read/write files at *some* real directory - replacing that directory with
a symlink works identically for all of them with one piece of code, as
long as each platform's actual default save directory is known
(systems.json's "save_dir" field).

By default that symlink now points into the active CosmOS profile's
own saves/<platform_id>/ (see profiles.py) - saves are profile-scoped
internal storage unless a user explicitly "assigns" a USB drive (still
supported, unchanged mechanism, just now recorded per-profile - see
save_assignment()/load_assignment()), in which case that profile's saves
redirect onto the drive instead, under <mount>/user/<platform_id>/, so
they travel with the stick between consoles. Assignment itself is a
per-profile choice (each profile picks internal-vs-USB independently),
but the USB-side layout is deliberately not further split by profile name
- the whole point of a save-USB is one physical drive's worth of saves
following its owner around, not a shared drive multiple profiles carve up.

sync_all_saves() has to re-run on every profile switch, not just when a
USB is assigned/removed - the real save symlink on disk needs to be
re-pointed at whichever profile just became active before any game can be
trusted to write to the right place. See app.py's activate_profile()."""
import json
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import debuglog  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "launcher"))
import profiles  # noqa: E402

log = debuglog.setup("usb_saves")

MOUNTPOINT = Path("/mnt/console-os-saves")
SYSTEMS_PATH = Path(__file__).resolve().parent.parent / "library" / "systems.json"


def list_removable_partitions(context=None):
	"""Returns a list of dicts describing currently-connected USB-bus
	partitions with a filesystem - the candidates a user could assign.
	context is injectable so tests can pass a fake pyudev-like object
	without needing real hardware."""
	if context is None:
		import pyudev

		context = pyudev.Context()

	partitions = []
	for device in context.list_devices(subsystem="block", DEVTYPE="partition"):
		if device.get("ID_BUS") != "usb":
			continue
		fs_type = device.get("ID_FS_TYPE")
		if not fs_type:
			continue
		serial = device.get("ID_FS_UUID") or device.get("ID_SERIAL")
		if not serial:
			continue
		partitions.append(
			{
				"device_node": device.device_node,
				"serial": serial,
				"label": device.get("ID_FS_LABEL") or "(unlabeled)",
				"fs_type": fs_type,
			}
		)
	log.debug(f"found {len(partitions)} removable USB partition(s)")
	return partitions


def load_assignment():
	assignment_path = profiles.usb_assignment_path()
	if assignment_path is None or not assignment_path.exists():
		return None
	with open(assignment_path) as f:
		return json.load(f)


def save_assignment(partition):
	assignment_path = profiles.usb_assignment_path()
	if assignment_path is None:
		return  # no active profile yet - nothing to persist to
	log.info(f"assigning USB partition {partition['device_node']} ({partition['label']}) for saves")
	with open(assignment_path, "w") as f:
		json.dump({"serial": partition["serial"], "label": partition["label"]}, f)


def find_assigned_partition(context=None):
	assignment = load_assignment()
	if not assignment:
		return None
	for partition in list_removable_partitions(context):
		if partition["serial"] == assignment["serial"]:
			return partition
	return None


def ensure_mounted(device_node, mountpoint=MOUNTPOINT):
	mountpoint.mkdir(parents=True, exist_ok=True)
	result = subprocess.run(["findmnt", "--noheadings", str(mountpoint)], capture_output=True, text=True)
	if device_node in result.stdout:
		log.debug(f"{device_node} already mounted at {mountpoint}")
		return mountpoint
	log.info(f"mounting {device_node} at {mountpoint}")
	subprocess.run(["mount", device_node, str(mountpoint)], check=True)
	return mountpoint


def redirect_save_dir(real_save_dir, target_dir):
	"""Makes real_save_dir a symlink pointing at target_dir (a USB-backed
	dir or, by default, the active profile's own internal saves/<platform_id>
	dir - see sync_all_saves()), moving any existing real save data onto
	target_dir the first time this runs (never silently drops saves), and
	leaving it alone if it's already correctly linked."""
	real_save_dir = Path(real_save_dir)
	target_dir = Path(target_dir)
	target_dir.parent.mkdir(parents=True, exist_ok=True)

	if real_save_dir.is_symlink():
		if real_save_dir.resolve() == target_dir.resolve():
			log.debug(f"{real_save_dir} already correctly linked to {target_dir}")
			return  # already correctly linked
		log.debug(f"{real_save_dir} is a symlink to somewhere else, replacing it")
		real_save_dir.unlink()

	if real_save_dir.exists():
		if target_dir.exists() and any(target_dir.iterdir()):
			# The destination already has saves for this platform - don't
			# overwrite them with whatever's on the console; just link in.
			log.info(f"{target_dir} already has saves, discarding console-local copy at {real_save_dir}")
			shutil.rmtree(real_save_dir)
		else:
			log.info(f"moving existing saves from {real_save_dir} to {target_dir}")
			target_dir.mkdir(parents=True, exist_ok=True)
			for item in real_save_dir.iterdir():
				shutil.move(str(item), str(target_dir / item.name))
			shutil.rmtree(real_save_dir)
	else:
		target_dir.mkdir(parents=True, exist_ok=True)

	real_save_dir.parent.mkdir(parents=True, exist_ok=True)
	real_save_dir.symlink_to(target_dir)
	log.debug(f"symlinked {real_save_dir} -> {target_dir}")


def sync_all_saves():
	"""Re-points every configured platform's real save directory (see
	redirect_save_dir()) at wherever it should live right now: the active
	profile's own assigned USB drive if it has one *and* that drive is
	actually connected right now, else that profile's own internal
	saves/<platform_id>/ folder (profiles.saves_dir()) - internal storage
	is the default, a USB is an explicit opt-in per profile (see
	assign_usb_row() in app.py), never the other way around.

	Called both right after a fresh USB assignment and on every profile
	switch (see app.py's activate_profile()) - the save symlink has to
	actually be re-pointed at the newly-active profile before any game can
	be trusted to write to the right place, not just have its *assignment
	record* be per-profile."""
	target_root = None
	assignment = load_assignment()
	if assignment:
		partition = find_assigned_partition()
		if partition:
			target_root = ensure_mounted(partition["device_node"]) / "user"
		else:
			log.warning(f"profile's assigned USB {assignment['label']!r} isn't connected - falling back to internal saves")
	if target_root is None:
		target_root = profiles.saves_dir()

	with open(SYSTEMS_PATH) as f:
		categories = json.load(f)["categories"]

	synced = []
	for category in categories:
		for manufacturer in category["manufacturers"]:
			# Not every "manufacturer" entry is really a platform group -
			# PC's own Steam/Epic/GOG entries are launcher shortcuts with a
			# "launch" key instead (systems.json's own shape, not something
			# to normalize here) - skip anything with nothing to sync.
			for platform in manufacturer.get("platforms", []):
				save_dir = platform.get("save_dir")
				if not save_dir:
					continue
				real_save_dir = Path(save_dir).expanduser()
				target_dir = target_root / platform["id"]
				redirect_save_dir(real_save_dir, target_dir)
				synced.append(platform["id"])
	log.info(f"synced save dirs for {len(synced)} platform(s) -> {target_root}: {synced}")
	return synced
