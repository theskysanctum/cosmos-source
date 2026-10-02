#!/usr/bin/env python3
"""Fetches the Electron runtime webshell/main.js needs into
ELECTRON_DIR ({repo}/webshell/electron/) - the same "fetch a real
prebuilt release at deploy time, commit nothing to git" pattern
emulators/cores.py/assets.py/engines.py already use, chosen over
committing the runtime outright because (unlike RetroArch, which is
built from source with project-specific patches and genuinely IS this
project's own build artifact) Electron is an unmodified upstream binary
release - the same shape as engines.py's own NW.js/Ren'Py/TIC-80/AGS
downloads, just one artifact instead of five. At ~283MB unpacked this is
smaller than the cores/assets payload the project already re-fetches
per deploy, so the same tradeoff is already accepted at a larger scale.

RELEASE_URL confirmed live against the real GitHub release (`gh release
view v44.3.0 --repo electron/electron`, asset
electron-v44.3.0-linux-x64.zip, 122830582 bytes) - not guessed from the
version number alone."""
import shutil
import sys
import zipfile
from io import BytesIO
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import debuglog  # noqa: E402

log = debuglog.setup("webshell.electron_runtime")

ELECTRON_DIR = Path(__file__).resolve().parent / "electron"
ELECTRON_VERSION = "44.3.0"
RELEASE_URL = f"https://github.com/electron/electron/releases/download/v{ELECTRON_VERSION}/electron-v{ELECTRON_VERSION}-linux-x64.zip"
ELECTRON_BIN = ELECTRON_DIR / "electron"


def is_fetched():
	return ELECTRON_BIN.exists()


def fetch_electron(log_fn=None):
	"""Downloads and extracts the pinned Electron release into
	ELECTRON_DIR. Blocking, called from Deploy GUI's dependency-install
	step like cores.fetch_all_cores()/assets.fetch_all_assets()/
	engines.fetch_all_engines(). Never raises - returns True/False,
	matching those functions' own contract."""
	if is_fetched():
		if log_fn:
			log_fn(f"Electron {ELECTRON_VERSION} already fetched, skipping.")
		return True
	try:
		if log_fn:
			log_fn(f"Downloading Electron {ELECTRON_VERSION} ({RELEASE_URL})...")
		log.info(f"downloading {RELEASE_URL}")
		response = requests.get(RELEASE_URL, timeout=600)
		response.raise_for_status()
		if ELECTRON_DIR.exists():
			shutil.rmtree(ELECTRON_DIR)
		ELECTRON_DIR.mkdir(parents=True, exist_ok=True)
		# The official zip extracts flat (no wrapping top-level folder -
		# electron/version, electron/resources/, etc. all sit directly
		# at the zip's own root), confirmed against the real downloaded
		# release rather than assumed - unlike several of engines.py's
		# own archives, which DO nest under a versioned folder name and
		# need an extra rename step.
		with zipfile.ZipFile(BytesIO(response.content)) as zf:
			zf.extractall(ELECTRON_DIR)
		ELECTRON_BIN.chmod(0o755)
	except Exception:
		log.exception("failed to fetch Electron")
		if log_fn:
			log_fn("Failed to fetch Electron.")
		return False
	if log_fn:
		log_fn(f"Electron {ELECTRON_VERSION} fetched.")
	return True
