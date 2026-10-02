#!/usr/bin/env python3
"""Fetches the indie/homebrew game-engine runtimes systems.json's "Game
Engines" manufacturer (see library/systems.json's PC category) needs -
RPG Maker MV/MZ (NW.js), Love2D, Ren'Py, TIC-80, and Adventure Game
Studio - into ENGINES_DIR ({repo}/emulators/bin/engines/<name>/), the
same "fetch a real prebuilt release at deploy time, commit nothing to
git" pattern emulators/cores.py and emulators/assets.py already use for
RetroArch - every one of these ships a real official prebuilt Linux
binary already, so (unlike mkxp-z - see systems.json's own rpgmaker_xp/
vx/vxace comments) none of them need building from source.

Each entry below is a real, version-pinned download URL (confirmed live
against the actual upstream release, not guessed) plus a small
extract/finalize step, since every one of these ships a slightly
different archive format (an .AppImage, a .tar.bz2, a .deb, two plain
.tar.gz's) and none of them are a flat "one .so file" the way a
libretro core is."""
import shutil
import subprocess
import sys
import tarfile
import zipfile
from io import BytesIO
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import debuglog  # noqa: E402

log = debuglog.setup("emulators.engines")

ENGINES_DIR = Path(__file__).resolve().parent / "bin" / "engines"

# Real launch entry point for each engine, relative to
# ENGINES_DIR/<name>/ - systems.json's own launch_template entries
# reference these exact paths.
LAUNCHERS = {
	"love2d": "AppRun",
	"renpy": "renpy.sh",
	"tic80": "tic80",
	"ags": "data/ags64",
	"nwjs": "nw",
}


def _download(url):
	log.info(f"downloading {url}")
	response = requests.get(url, timeout=300)
	response.raise_for_status()
	return response.content


def _fetch_love2d():
	"""Official Linux AppImage - extracted once at fetch time (not run
	as an AppImage at launch time) so a game launch never depends on
	FUSE being available, the same reasoning packaging/build_appimage.sh
	already uses APPIMAGE_EXTRACT_AND_RUN=1 for."""
	dest = ENGINES_DIR / "love2d"
	data = _download("https://github.com/love2d/love/releases/download/11.5/love-11.5-x86_64.AppImage")
	appimage_path = ENGINES_DIR / "love2d.AppImage"
	ENGINES_DIR.mkdir(parents=True, exist_ok=True)
	appimage_path.write_bytes(data)
	appimage_path.chmod(0o755)
	if dest.exists():
		shutil.rmtree(dest)
	subprocess.run([str(appimage_path), "--appimage-extract"], cwd=ENGINES_DIR, check=True, capture_output=True)
	(ENGINES_DIR / "squashfs-root").rename(dest)
	appimage_path.unlink()
	return True


def _fetch_renpy():
	"""The official SDK tarball (~150MB, one archive covers every real
	platform) - only the Linux-relevant pieces are worth keeping, but
	extracting the whole thing is simpler and more robust than trying to
	cherry-pick paths out of a tar.bz2 by name. renpy.sh (see LAUNCHERS)
	is the SDK's own generic "run any project directory" launcher -
	confirmed directly in its real argument parser (renpy/arguments.py):
	`renpy.sh <basedir> [command]`, command defaulting to "run" - this is
	what makes a single bundled SDK able to launch ANY Ren'Py game
	folder, unlike a game's own self-exported release (which bundles a
	renamed, single-purpose copy of this same SDK)."""
	dest = ENGINES_DIR / "renpy"
	data = _download("https://www.renpy.org/dl/8.5.3/renpy-8.5.3-sdk.tar.bz2")
	ENGINES_DIR.mkdir(parents=True, exist_ok=True)
	if dest.exists():
		shutil.rmtree(dest)
	with tarfile.open(fileobj=BytesIO(data), mode="r:bz2") as tf:
		tf.extractall(ENGINES_DIR)
	(ENGINES_DIR / "renpy-8.5.3-sdk").rename(dest)
	return True


def _fetch_tic80():
	"""The official release is a .deb - extracted directly (ar + tar)
	rather than installed via dpkg, matching this project's own "no
	system package manager involvement for bundled runtimes" convention
	(the same reason RetroArch/Wine are committed binaries, not distro
	packages)."""
	dest = ENGINES_DIR / "tic80"
	data = _download("https://github.com/nesbox/TIC-80/releases/download/v1.1.2837/tic80-v1.1-linux.deb")
	ENGINES_DIR.mkdir(parents=True, exist_ok=True)
	deb_path = ENGINES_DIR / "tic80.deb"
	deb_path.write_bytes(data)
	work_dir = ENGINES_DIR / "_tic80_extract"
	if work_dir.exists():
		shutil.rmtree(work_dir)
	work_dir.mkdir()
	subprocess.run(["ar", "x", str(deb_path)], cwd=work_dir, check=True, capture_output=True)
	data_tar = next(work_dir.glob("data.tar.*"))
	with tarfile.open(data_tar) as tf:
		tf.extractall(work_dir)
	if dest.exists():
		shutil.rmtree(dest)
	dest.mkdir()
	shutil.move(str(work_dir / "usr" / "bin" / "tic80"), str(dest / "tic80"))
	(dest / "tic80").chmod(0o755)
	shutil.rmtree(work_dir)
	deb_path.unlink()
	return True


def _fetch_ags():
	"""The official Linux release - a self-contained tarball with both
	32/64-bit engine binaries and their own bundled shared libraries.
	Uses the raw engine binary (data/ags64) directly rather than the
	bundled "startgame" wrapper script - that wrapper is meant for a
	game's own release (copy the game INTO its data/ dir first), but the
	real engine binary itself already accepts a game file or directory
	as a plain argument (confirmed via its own --help: "Usage: ags
	[OPTIONS] [GAMEFILE or DIRECTORY]") - the same "point it straight at
	the rom" shape every other platform in this project already uses,
	with no copy-into-place step needed."""
	dest = ENGINES_DIR / "ags"
	data = _download("https://github.com/adventuregamestudio/ags/releases/download/v3.6.2.21/ags_3.6.2.21_linux.tar.gz")
	ENGINES_DIR.mkdir(parents=True, exist_ok=True)
	if dest.exists():
		shutil.rmtree(dest)
	dest.mkdir()
	with tarfile.open(fileobj=BytesIO(data), mode="r:gz") as tf:
		tf.extractall(dest)
	(dest / "data" / "ags64").chmod(0o755)
	return True


def _fetch_nwjs():
	"""The official Linux runtime (~160MB) - what actually runs an RPG
	Maker MV/MZ game's own www/ folder (real, standard convention: `nw
	<folder containing package.json>`), instead of falling back to Wine
	for these two like WolfRPG/GameMaker need to."""
	dest = ENGINES_DIR / "nwjs"
	data = _download("https://dl.nwjs.io/v0.90.0/nwjs-v0.90.0-linux-x64.tar.gz")
	ENGINES_DIR.mkdir(parents=True, exist_ok=True)
	if dest.exists():
		shutil.rmtree(dest)
	with tarfile.open(fileobj=BytesIO(data), mode="r:gz") as tf:
		tf.extractall(ENGINES_DIR)
	(ENGINES_DIR / "nwjs-v0.90.0-linux-x64").rename(dest)
	(dest / "nw").chmod(0o755)
	return True


ENGINE_FETCHERS = {
	"love2d": _fetch_love2d,
	"renpy": _fetch_renpy,
	"tic80": _fetch_tic80,
	"ags": _fetch_ags,
	"nwjs": _fetch_nwjs,
}


def is_fetched(name):
	return (ENGINES_DIR / name / LAUNCHERS[name]).exists()


def fetch_all_engines(log_fn=None):
	"""ENGINE_FETCHERS[name]() for every engine not already present.
	Blocking, called from Deploy GUI's dependency-install step like
	cores.fetch_all_cores()/assets.fetch_all_assets(). Returns the list
	of names that failed (empty on full success)."""
	failed = []
	for name, fetch_fn in ENGINE_FETCHERS.items():
		if is_fetched(name):
			if log_fn:
				log_fn(f"Engine {name!r} already fetched, skipping.")
			continue
		if log_fn:
			log_fn(f"Fetching engine {name!r}...")
		try:
			fetch_fn()
		except Exception:
			log.exception(f"failed to fetch engine {name}")
			failed.append(name)
			if log_fn:
				log_fn(f"Failed to fetch engine {name!r}.")
	return failed
