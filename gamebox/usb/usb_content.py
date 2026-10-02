#!/usr/bin/env python3
"""A USB drive as a ROM/game/media source - importing content from it
into /cosmos's own library, or browsing/playing straight off it
without copying first. Deliberately separate from usb_saves.py: saves
are always-on symlink redirection with a persisted per-profile
assignment, this is an on-demand "is a drive currently serving as a
content source" concern with no assignment record at all - whichever
drive was last mounted here is it, until explicitly stopped or the
launcher restarts. Reuses usb_saves.py's own drive detection/mount
primitives directly (list_removable_partitions(), ensure_mounted())
rather than duplicating them, the same "import the real thing" approach
kiosk_launcher.py already uses for brave_launcher.py's profile-seeding
helpers.

CONTENT_MOUNTPOINT is deliberately a different fixed path than
usb_saves.MOUNTPOINT, so the same physical drive can be assigned for
saves *and* set as a content source at the same time without the two
mounts colliding - mounting one block device at two separate
mountpoints is ordinary, unproblematic Linux behavior.

Content on the drive itself is expected to mirror /cosmos's own
top-level layout - roms/<platform_id>/, movies/, tv/, music/, photos/ -
so importing is a straight directory copy and browsing off the drive is
a straight extra scan root, no separate convention to invent."""
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from usb_saves import ensure_mounted  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import debuglog  # noqa: E402
import paths  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "filemanager"))
import files  # noqa: E402

log = debuglog.setup("usb_content")

CONTENT_MOUNTPOINT = Path("/mnt/console-os-content")

MEDIA_DIRS = {
	"movies": paths.DATA_ROOT / "movies",
	"tv": paths.DATA_ROOT / "tv",
	"music": paths.DATA_ROOT / "music",
	"photos": paths.DATA_ROOT / "photos",
}


def mount_content_drive(partition):
	"""Mounts partition as the active content source, returning the
	mountpoint. Blocking, never raises - callers run this off the GTK
	thread the same way appman.py's install()/update() expect to be run."""
	log.info(f"mounting {partition['device_node']} ({partition['label']}) as content source")
	try:
		return ensure_mounted(partition["device_node"], CONTENT_MOUNTPOINT)
	except (subprocess.CalledProcessError, OSError):
		log.exception(f"failed to mount {partition['device_node']} as content source")
		return None


def unmount_content_drive():
	if not active_content_root():
		return True
	log.info(f"unmounting content source at {CONTENT_MOUNTPOINT}")
	try:
		subprocess.run(["umount", str(CONTENT_MOUNTPOINT)], check=True)
		return True
	except subprocess.CalledProcessError:
		log.exception(f"failed to unmount {CONTENT_MOUNTPOINT}")
		return False


def active_content_root():
	"""CONTENT_MOUNTPOINT if something's actually mounted there right now,
	else None - what library.py polls to know whether there's a USB
	content source active at all. CosmOS's own native Video/Music/
	Photos add-ons (see media/addons/) don't use this yet - each type's
	built-in Local Files add-on only scans media_backend.py's own real
	folders for now; a USB-backed add-on watching this the way Kodi's
	sources.xml once did would be a real, separate future add-on, not
	something core to the built-in one."""
	return CONTENT_MOUNTPOINT if active_content_device() else None


def active_content_device():
	"""The device_node currently mounted at CONTENT_MOUNTPOINT, or None -
	lets app.py's per-drive actions page tell "this exact drive is the
	active content source" apart from "some other drive is, or none is"."""
	result = subprocess.run(["findmnt", "--noheadings", "--output", "SOURCE", str(CONTENT_MOUNTPOINT)], capture_output=True, text=True)
	device = result.stdout.strip()
	return device or None


def usb_roms_dir(platform_id):
	"""None if no content source is active - the same "nothing to scan"
	signal library.list_games() already treats a missing roms_dir as."""
	content_root = active_content_root()
	return (content_root / "roms" / platform_id) if content_root else None


def import_roms(platform_id, dest_roms_dir):
	"""Copies every entry from the active content drive's roms/<platform_id>/
	into dest_roms_dir (the real platform's own roms_dir, already
	/cosmos-rooted), skipping anything already present by name - cheap
	dedup, no checksumming, matching this project's existing copy logic
	elsewhere (filemanager/files.py has no conflict resolution either).
	Returns (copied_count, skipped_count); never raises."""
	src_dir = usb_roms_dir(platform_id)
	if src_dir is None or not src_dir.is_dir():
		log.warning(f"import_roms: no USB roms/{platform_id} found")
		return 0, 0
	dest_roms_dir = Path(dest_roms_dir)
	dest_roms_dir.mkdir(parents=True, exist_ok=True)
	copied = skipped = 0
	for entry in src_dir.iterdir():
		if (dest_roms_dir / entry.name).exists():
			skipped += 1
			continue
		try:
			files.copy(entry, dest_roms_dir)
			copied += 1
		except OSError:
			log.exception(f"failed to import {entry}")
	log.info(f"import_roms({platform_id}): copied {copied}, skipped {skipped}")
	return copied, skipped


def import_media(kind):
	"""kind is one of MEDIA_DIRS's keys (movies/tv/music/photos). Same
	shape as import_roms() - flat copy, skip-if-exists, no recursion into
	existing destination subfolders beyond what shutil.copytree already
	does for a whole show/album directory."""
	content_root = active_content_root()
	if content_root is None:
		log.warning(f"import_media({kind}): no USB content source active")
		return 0, 0
	src_dir = content_root / kind
	if not src_dir.is_dir():
		log.warning(f"import_media({kind}): no USB {kind}/ found")
		return 0, 0
	dest_dir = MEDIA_DIRS[kind]
	dest_dir.mkdir(parents=True, exist_ok=True)
	copied = skipped = 0
	for entry in src_dir.iterdir():
		if (dest_dir / entry.name).exists():
			skipped += 1
			continue
		try:
			files.copy(entry, dest_dir)
			copied += 1
		except OSError:
			log.exception(f"failed to import {entry}")
	log.info(f"import_media({kind}): copied {copied}, skipped {skipped}")
	return copied, skipped
