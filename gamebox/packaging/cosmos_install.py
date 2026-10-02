#!/usr/bin/env python3
"""Install/update/rollback for CosmOS's own real, deployed copy - the
same swap-the-file model emulators/appman.py already uses for emulator
AppImages (place at a fixed, permanent path; back up the previous tree
before replacing it; roll back by swapping the backup back into place),
just for a whole directory tree instead of a single executable.

CosmOS is no longer packaged as an AppImage at all, per direct
request - GTK3/PyGObject and every pip dependency now install as real
host packages wherever CosmOS actually runs (packaging/host_deps.py,
atomic/Containerfile's own dnf/pip RUN step) instead of being bundled,
so the one thing left to "install" is a plain, already-runnable source
tree - see packaging/stage_build.sh, which produces exactly that (a
`git ls-files` copy plus a freshly-compiled wm/consolewm). install()/
update() below take that staged directory directly and move it into
place; everything (the real live tty2 boot chain, notifications/
cosmochat.service, etc.) runs directly out of that fixed, permanent
location. Because APP_ROOT (not a versioned path) is what actually gets
swapped, anything that references it externally only ever needs to name
APP_ROOT once, permanently - it never has to change across updates.

APP_ROOT/BACKUP_ROOT are deliberately hardcoded under /cosmos, not
built from paths.DATA_ROOT - the app layer itself is system-partition
resident (survives a userdata wipe *and* stays put if DATA_ROOT itself
ever moves again), while DATA_ROOT names the separate /userdata
partition real user data lives on (see paths.py's own docstring). This
directory-swap mechanism is still worth keeping even on the bootc/ostree
platform, which already gives its own atomic, rollback-capable updates
for the whole OS image (see atomic/Containerfile) - that's the real
target for an actual deployed machine, but this dev-machine-local swap
is a much faster, lower-risk loop for iterating on a CosmOS build
without a full image rebuild each time, and the two aren't mutually
exclusive."""
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import debuglog  # noqa: E402

log = debuglog.setup("cosmos_install")

APP_ROOT = Path("/cosmos/app/current")
BACKUP_ROOT = Path("/cosmos/app/current.old")

COSMOCHAT_SERVICE_DEST = Path.home() / ".config" / "systemd" / "user" / "cosmochat.service"


def _install_cosmochat_service():
	"""Substitutes @@APP_ROOT@@ in APP_ROOT's own cosmochat.service
	template and installs the result as a real systemd --user unit, so the
	background Telegram relay always points at whichever build is
	actually current. A no-op if this build doesn't ship notifications/
	at all.

	APP_ROOT itself is what @@APP_ROOT@@ substitutes to now - no more
	extra "cosmos/" nesting a level below it (that was solely
	build_appimage.sh's own python-appimage -x/--extra-data staging
	convention; stage_build.sh writes the tracked tree straight into its
	output directory with no such nesting)."""
	template = APP_ROOT / "notifications" / "cosmochat.service"
	if not template.exists():
		return
	unit_text = template.read_text().replace("@@APP_ROOT@@", str(APP_ROOT))
	COSMOCHAT_SERVICE_DEST.parent.mkdir(parents=True, exist_ok=True)
	COSMOCHAT_SERVICE_DEST.write_text(unit_text)
	subprocess.run(["systemctl", "--user", "daemon-reload"], check=False)
	log.info(f"installed {COSMOCHAT_SERVICE_DEST} pointing at {APP_ROOT}")


def install(staged_dir):
	"""First-ever install - fails if APP_ROOT already exists (use
	update() instead). `staged_dir` (see packaging/stage_build.sh) is
	COPIED into place, not moved - the caller keeps ownership of it, so
	the same staged build can be installed, tested, and reinstalled
	again without re-staging every time. Returns True on success, never
	raises."""
	if APP_ROOT.exists():
		log.error(f"{APP_ROOT} already exists - use update() instead")
		return False
	staged_dir = Path(staged_dir)
	if not staged_dir.is_dir():
		log.error(f"{staged_dir} is not a directory - did stage_build.sh actually succeed?")
		return False
	APP_ROOT.parent.mkdir(parents=True, exist_ok=True)
	shutil.copytree(staged_dir, APP_ROOT)
	_install_cosmochat_service()
	log.info(f"installed {staged_dir} to {APP_ROOT}")
	return True


def update(staged_dir):
	"""Backs up the current APP_ROOT to BACKUP_ROOT before replacing it -
	mirrors appman.py's update()/_backup_path() shape exactly, just for a
	directory tree instead of one file. Same copy-not-move contract as
	install() above."""
	staged_dir = Path(staged_dir)
	if not staged_dir.is_dir():
		log.error(f"{staged_dir} is not a directory - did stage_build.sh actually succeed?")
		return False
	if APP_ROOT.exists():
		shutil.rmtree(BACKUP_ROOT, ignore_errors=True)
		shutil.move(str(APP_ROOT), str(BACKUP_ROOT))
		log.debug(f"backed up {APP_ROOT} -> {BACKUP_ROOT}")
	shutil.copytree(staged_dir, APP_ROOT)
	_install_cosmochat_service()
	log.info(f"updated {APP_ROOT} from {staged_dir}")
	return True


def rollback():
	"""Restores the backup update() made - a no-op returning False if
	there isn't one, matching appman.py's rollback() contract exactly."""
	if not BACKUP_ROOT.exists():
		log.warning("no backup to roll back to")
		return False
	shutil.rmtree(APP_ROOT, ignore_errors=True)
	shutil.move(str(BACKUP_ROOT), str(APP_ROOT))
	_install_cosmochat_service()
	log.info(f"rolled back {APP_ROOT}")
	return True
