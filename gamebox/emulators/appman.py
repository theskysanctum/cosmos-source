#!/usr/bin/env python3
"""Thin wrapper around AppMan (github.com/ivan-hc/AM, the no-root "appman"
build) - the real backend that fetches/installs/updates emulator AppImages,
the same "drive a real, well-tested tool" approach used throughout this
project. This module never talks to GitHub releases itself; AppMan's own
curated catalog covers every AppMan-installed emulator this project has
ever needed. CosmOS currently has no standalone emulator left to
install through it at all - xemu (Xbox) and Supermodel (Sega Model 3),
the two it used to install this way, were both removed outright, per
direct request, not something the user cared about keeping - but this
module stays as real, generic infrastructure for whatever comes next
(Eden, below, is one already-wired example, even though nothing in
systems.json references it yet).

Confirmed directly (not assumed) against a real AppMan install: an
installed app's actual executable lives at
<EMULATORS_DIR>/<name>/<name> (a real ELF binary, the extracted AppImage
itself), with a symlink at $HOME/.local/bin/<name> pointing at it -
$HOME itself is overridden to /cosmos for every appman subprocess call
(see _env()), so that symlink lands at /cosmos/.local/bin/<name> in
practice, not the real user's home. That
<EMULATORS_DIR>/<name>/<name> path is what update()/rollback() below
back up and restore - AppMan's own per-app AM-updater script does keep a
numbered backup on update (`mv --backup=t`), but this project wants one
predictable single-file rollback target instead, matching how CosmOS
itself is meant to update (see the project's own AppImage-packaging
notes) - swap the file, `.old` to roll back.

One exception to "AppMan is the only fetch backend": Eden's real upstream
(git.eden-emu.dev) moved off GitHub after a DMCA takedown, and AppMan's
"eden"/"eden-nightly" catalog entries still point at the dead GitHub
release location (confirmed directly - both fail to download). Eden's own
release notes self-host a Forgejo API + a stable.eden-emu.dev CDN with a
predictable asset naming scheme, so install()/update() fetch it directly
for just this one name, landing the file in the exact same
<EMULATORS_DIR>/eden/eden + /cosmos/.local/bin/eden shape AppMan
itself would have used - indistinguishable from an AppMan-managed install
to every other function here, and to systems.json/library.py.

RetroArch is not handled here at all any more, by AppMan or otherwise -
see emulators/bin/retroarch's own presence (a from-source build, menu
and Qt stripped - CosmOS is the only menu this build ever needs, launched
directly via systems.json's launch_template, no Qt companion app either -
but networking/cheevos genuinely enabled, per direct request, for real
RetroAchievements tracking and netplay; --disable-ffmpeg/--disable-
discord/--disable-translate/--disable-sdl2 alongside --enable-networking
keep those two features from dragging in their own large, unrelated
auto-detected dependency trees (confirmed live: a first pass without
those four disables pulled in FFmpeg's entire codec/protocol stack -
even Samba and Tesseract OCR libs, transitively - none of it needed for
cheevos or netplay). Two real upstream bugs surfaced building this exact
--disable-menu + --enable-networking combination against the v1.22.2
release (both patched directly in the vendored source before building,
not tracked as separate patch files since the source tree itself isn't
committed): runloop.c nested several netplay-status locals inside an
`#ifdef HAVE_MENU` block despite using them later unconditionally
whenever HAVE_NETWORKING is on; runtime_file.c called
runloop_state_get_ptr() while only ever picking up its declaration
transitively through menu/menu_driver.h, itself only included under
HAVE_MENU - fixed with an explicit #include "runloop.h". See
emulators/retroachievements_sync.py and retroarch_config.py's own
cheevos_username/cheevos_token wiring for how a CosmOS profile's
RetroAchievements login actually reaches RetroArch now. Netplay itself
compiles and its CLI flags (--host/--connect/--nick) are real, but
nothing in this project's UI drives them yet - hosting/joining a session
is still a manual, direct-launch-template-editing affair for now.

emulators/bin/cores/ (bundled libretro cores) is committed straight into
the repo too, referenced directly via {repo}/emulators/bin/... in
systems.json's launch_template entries - no install step, no AppMan,
nothing under EMULATORS_DIR for RetroArch specifically. See
emulators/cores.py's own docstring for how to refresh the bundled cores
against a newer buildbot nightly."""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import debuglog  # noqa: E402
import paths  # noqa: E402

log = debuglog.setup("appman")

# Outside the repo entirely, same treatment as roms/, movies/, profiles/ -
# this is real per-machine installed software, not project source.
EMULATORS_DIR = paths.DATA_ROOT / "emulators"
APPMAN_BIN = paths.DATA_ROOT / ".local" / "bin" / "appman"
AM_INSTALLER_URL = "https://raw.githubusercontent.com/ivan-hc/AM/main/AM-INSTALLER"

# See the module docstring's "one exception" note - Eden bypasses AppMan
# entirely and is fetched straight from its own real upstream instead.
EDEN_RELEASES_API = "https://git.eden-emu.dev/api/v1/repos/eden-emu/eden/releases/latest"


def _env():
	"""appman_location alone only controls where installed apps' real
	files land (EMULATORS_DIR) - the appman binary itself, and every
	per-app ~/.local/bin/<name> symlink it creates, are resolved against
	the real $HOME env var, confirmed directly against a live install.
	Overriding HOME too is what actually gets both of those under
	/cosmos instead of the real user's home."""
	return {**os.environ, "HOME": str(paths.DATA_ROOT), "appman_location": str(EMULATORS_DIR)}


def ensure_installed():
	"""Bootstraps the appman binary itself if it isn't already present -
	safe to call every time (idempotent), matching ensure_seeded_profile()'s
	own "check first, only do the real work once" shape elsewhere in this
	project. appman_location is set before AppMan's own first-run check
	ever runs (see APP-MANAGER's _appman_check()), so this never prompts."""
	if APPMAN_BIN.exists():
		return True
	log.info("appman not found, bootstrapping via AM-INSTALLER")
	installer_path = Path("/tmp/AM-INSTALLER")
	try:
		subprocess.run(
			["bash", "-c", f"curl -s -Lo {installer_path} {AM_INSTALLER_URL} && chmod +x {installer_path}"],
			check=True,
		)
		subprocess.run(["bash", str(installer_path), "-i", "appman"], env=_env(), check=True)
	except subprocess.CalledProcessError:
		log.exception("failed to bootstrap appman")
		return False
	finally:
		installer_path.unlink(missing_ok=True)
	installed = APPMAN_BIN.exists()
	log.info(f"appman bootstrap {'succeeded' if installed else 'failed - binary still missing'}")
	return installed


def is_installed(name):
	return (paths.DATA_ROOT / ".local" / "bin" / name).exists()


def _fetch_eden():
	"""Downloads Eden's latest release straight from its own upstream (see
	the module docstring's "one exception" note) - the amd64 PGO AppImage,
	the build variant Eden's own release notes recommend over the
	"standard" one (~10-30% faster). Never raises; logs and returns False
	on any failure, matching install()/update()'s own contract."""
	try:
		response = requests.get(EDEN_RELEASES_API, timeout=15)
		response.raise_for_status()
		release = response.json()
	except requests.RequestException:
		log.exception("could not reach Eden's release API")
		return False
	asset_url = next(
		(
			a["browser_download_url"]
			for a in release.get("assets", [])
			if a["name"].endswith("amd64-clang-pgo.AppImage")
		),
		None,
	)
	if not asset_url:
		log.warning(f"no amd64 PGO AppImage found in Eden release {release.get('tag_name')}")
		return False
	dest_dir = EMULATORS_DIR / "eden"
	dest_dir.mkdir(parents=True, exist_ok=True)
	dest = dest_dir / "eden"
	log.info(f"downloading Eden {release.get('tag_name')} from {asset_url}")
	try:
		with requests.get(asset_url, stream=True, timeout=120) as r:
			r.raise_for_status()
			with open(dest, "wb") as f:
				for chunk in r.iter_content(chunk_size=1024 * 1024):
					f.write(chunk)
	except requests.RequestException:
		log.exception(f"failed to download Eden from {asset_url}")
		return False
	dest.chmod(0o755)
	bin_link = paths.DATA_ROOT / ".local" / "bin" / "eden"
	bin_link.parent.mkdir(parents=True, exist_ok=True)
	bin_link.unlink(missing_ok=True)
	bin_link.symlink_to(dest)
	log.info(f"installed eden {release.get('tag_name')}")
	return True


def install(name):
	"""Installs one catalog app by name (e.g. "cemu", "duckstation") -
	blocking, never raises; callers run this off the GTK thread, the same
	way every other Shop/download backend in this project expects to be
	run."""
	if name == "eden":
		return _fetch_eden()
	log.info(f"installing {name} via appman")
	try:
		result = subprocess.run([str(APPMAN_BIN), "-y", "-i", name], env=_env(), capture_output=True, text=True, timeout=1800)
	except subprocess.TimeoutExpired:
		log.warning(f"appman install timed out for {name}")
		return False
	if result.returncode != 0:
		log.warning(f"appman install failed for {name}: {result.stderr.strip()}")
		return False
	log.info(f"installed {name}")
	return True


def _app_executable(name):
	return EMULATORS_DIR / name / name


def _backup_path(name):
	return EMULATORS_DIR / name / f"{name}.AppImage.old"


def update(name):
	"""Backs up the currently-installed executable to a predictable
	<name>.AppImage.old before updating, so rollback(name) can restore it -
	AppMan's own AM-updater keeps a numbered backup too, but this gives one
	guaranteed, predictably-named rollback target instead of having to
	guess which numbered copy is the right one."""
	current = _app_executable(name)
	if current.exists():
		shutil.copy2(current, _backup_path(name))
		log.debug(f"backed up {current} -> {_backup_path(name)}")
	if name == "eden":
		return _fetch_eden()
	log.info(f"updating {name} via appman")
	try:
		result = subprocess.run([str(APPMAN_BIN), "-y", "-u", name], env=_env(), capture_output=True, text=True, timeout=1800)
	except subprocess.TimeoutExpired:
		log.warning(f"appman update timed out for {name}")
		return False
	if result.returncode != 0:
		log.warning(f"appman update failed for {name}: {result.stderr.strip()}")
		return False
	log.info(f"updated {name}")
	return True


def rollback(name):
	"""Restores the backup update() made - a no-op returning False if
	there isn't one (nothing installed yet, or never updated)."""
	backup = _backup_path(name)
	if not backup.exists():
		log.warning(f"no backup to roll back to for {name}")
		return False
	shutil.copy2(backup, _app_executable(name))
	log.info(f"rolled back {name} to its backed-up version")
	return True
