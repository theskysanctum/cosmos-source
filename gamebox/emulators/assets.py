#!/usr/bin/env python3
"""Fetches RetroArch's own official asset packages (gamepad autoconfig
profiles, cheats, the game database, on-screen overlays, slang shaders,
per-core info files) from the same buildbot cores.py fetches cores
from, into ASSETS_DIR ({repo}/emulators/bin/) - retroarch_config.py
points RetroArch's own directory settings at these exact folders.

Real buildbot listing (https://buildbot.libretro.com/assets/frontend/,
confirmed live): assets.zip, autoconfig.zip, cheats.zip,
database-cursors.zip, database-rdb.zip, glui_minimal_assets.zip,
info.zip, overlays.zip, shaders_cg.zip, shaders_glsl.zip,
shaders_slang.zip. ASSET_PACKAGES below deliberately fetches 7 of the
10, not all - the other 3 are real, but genuinely useless for this
project's own build, not a curation call:
- glui_minimal_assets.zip and database-cursors.zip are exclusively for
  RetroArch's own menu screens (GLUI's own assets; the menu-only
  database "Explore" facet browser) - this build is compiled with
  --disable-menu (see the RetroArch bundling work in this project's own
  history), so nothing can ever read either one. Fetching them would be
  real disk space spent on files that can never be loaded by anything,
  not "one fewer feature".
- shaders_cg.zip and shaders_glsl.zip are the legacy Cg and GLSL shader
  dialects - this project already made a deliberate, explicit call to
  keep slang support (and therefore Vulkan/GL-core) rather than the
  legacy-GL-only path slang cutting would have implied (see the
  RetroArch build-size tradeoffs earlier in this project's history) -
  bundling the legacy dialects back in as "assets" would contradict
  that same call for no real benefit, since every shader preset this
  project's own filters reference is a .slangp."""
import sys
import zipfile
from io import BytesIO
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import debuglog  # noqa: E402

log = debuglog.setup("emulators.assets")

ASSETS_DIR = Path(__file__).resolve().parent / "bin"
BUILDBOT_URL = "https://buildbot.libretro.com/assets/frontend"

# package name (the real buildbot zip, minus ".zip") -> the real
# directory retroarch_config.py's generated retroarch.cfg points its
# matching setting at. Each zip extracts flat into its own directory
# (confirmed live - no wrapper folder to strip), so the destination
# below IS this project's RETROARCH_DIRECTORIES value for that setting.
ASSET_PACKAGES = {
	"autoconfig": "autoconfig",
	"cheats": "cheats",
	"database-rdb": "database",
	"info": "info",
	"overlays": "overlays",
	"shaders_slang": "shaders",
	"assets": "assets",
}


def is_fetched(package):
	# A directory with anything at all in it counts as "already fetched"
	# - cheap enough (these are only checked at deploy time, not per
	# launch) and avoids needing to track a separate marker file.
	dest = ASSETS_DIR / ASSET_PACKAGES[package]
	return dest.is_dir() and any(dest.iterdir())


def fetch_package(package):
	"""Downloads and unzips one asset package (e.g. "overlays") into its
	real destination directory. Blocking, never raises."""
	url = f"{BUILDBOT_URL}/{package}.zip"
	dest = ASSETS_DIR / ASSET_PACKAGES[package]
	log.info(f"fetching asset package {package} from {url}")
	try:
		response = requests.get(url, timeout=120)
		response.raise_for_status()
	except requests.RequestException:
		log.exception(f"failed to download asset package {package}")
		return False
	dest.mkdir(parents=True, exist_ok=True)
	try:
		with zipfile.ZipFile(BytesIO(response.content)) as z:
			z.extractall(dest)
	except zipfile.BadZipFile:
		log.exception(f"downloaded asset package {package} was not a valid zip")
		return False
	log.info(f"fetched asset package {package} -> {dest}")
	return True


def fetch_all_assets(log_fn=None):
	"""fetch_package() for every real, useful asset package (see the
	module docstring for which 3 of the real 10 are deliberately
	skipped and why), skipping ones already present. Same call-site
	shape as cores.py's fetch_all_cores() - called from Deploy GUI's
	on_check_deps(). Returns the list of package names that failed."""
	failed = []
	for package in ASSET_PACKAGES:
		if is_fetched(package):
			if log_fn:
				log_fn(f"Asset package {package!r} already fetched, skipping.")
			continue
		if log_fn:
			log_fn(f"Fetching asset package {package!r}...")
		if not fetch_package(package):
			failed.append(package)
			if log_fn:
				log_fn(f"Failed to fetch asset package {package!r}.")
	return failed
