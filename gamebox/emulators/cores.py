#!/usr/bin/env python3
"""Fetches libretro cores from the official buildbot into CORES_DIR
({repo}/emulators/bin/cores/) - systems.json's launch_template entries
reference every core there identically, whether it was fetched last
week or a minute ago.

Nothing here is committed to git any more (see .gitignore's own entry
for CORES_DIR): "one emulator, every core" means every real core in the
buildbot, not a hand-curated subset small enough to push - RetroArch's
own full core set is well over a gigabyte, so this project bundles only
the from-source retroarch binary itself in git and fetches every core
(and every real asset - see assets.py) at deploy time instead, the same
place packaging/deploy_gui/deploy_gui.py already does host dependency
and Flatpak installs. Re-run fetch_all_cores() any time to pick up
newly-added cores or refresh existing ones that changed upstream -
is_fetched() skips anything already on disk, so it's always safe and
cheap to call again."""
import re
import sys
import zipfile
from io import BytesIO
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import debuglog  # noqa: E402

log = debuglog.setup("emulators.cores")

CORES_DIR = Path(__file__).resolve().parent / "bin" / "cores"
BUILDBOT_URL = "https://buildbot.libretro.com/nightly/linux/x86_64/latest"

# The buildbot's own directory index (h5ai) - confirmed live, real -
# lists every current core as "<name>.so.zip"; this is scraped fresh
# rather than kept as a static list here, so a newly-added upstream
# core (or one renamed/removed) is picked up automatically with no
# code change needed - the actual meaning of "every core".
_ZIP_NAME_RE = re.compile(r'href="[^"]*/([A-Za-z0-9_.+-]+)\.so\.zip"')


def list_available_cores():
	"""Every core name (e.g. "nestopia_libretro") currently in the
	buildbot's directory listing, sorted. Never raises - an empty list
	on any network/parse failure, same "no crash, just nothing to do"
	contract as boxart.py/quick_launch.py's own fetch helpers."""
	try:
		response = requests.get(f"{BUILDBOT_URL}/", timeout=30)
		response.raise_for_status()
	except requests.RequestException:
		log.exception("failed to list the libretro buildbot's core directory")
		return []
	names = sorted(set(_ZIP_NAME_RE.findall(response.text)))
	log.debug(f"buildbot lists {len(names)} core(s)")
	return names


def is_fetched(name):
	return (CORES_DIR / f"{name}.so").exists()


def fetch_core(name):
	"""Downloads and unzips one core (e.g. "nestopia_libretro") into
	CORES_DIR. Blocking, never raises - callers run this off the GTK
	thread, the same way every other Shop/download backend in this
	project expects to be run."""
	url = f"{BUILDBOT_URL}/{name}.so.zip"
	log.info(f"fetching core {name} from {url}")
	try:
		response = requests.get(url, timeout=60)
		response.raise_for_status()
	except requests.RequestException:
		log.exception(f"failed to download core {name}")
		return False
	CORES_DIR.mkdir(parents=True, exist_ok=True)
	try:
		with zipfile.ZipFile(BytesIO(response.content)) as z:
			z.extractall(CORES_DIR)
	except zipfile.BadZipFile:
		log.exception(f"downloaded core {name} was not a valid zip")
		return False
	core_path = CORES_DIR / f"{name}.so"
	if not core_path.exists():
		log.warning(f"{name}.so.zip didn't contain {name}.so after extraction")
		return False
	core_path.chmod(0o755)
	log.info(f"fetched core {name}")
	return True


def fetch_all_cores(log_fn=None):
	"""fetch_core() for every core the buildbot currently lists, skipping
	ones already present - called from Deploy GUI's own dependency-
	install step (see deploy_gui.py's on_check_deps()), the real "deploy
	time" this project's already-established pattern for a host
	dependency happens at. log_fn, if given, mirrors host_deps.install()'s
	own optional progress-line callback. Returns the list of names that
	failed (empty on full success)."""
	names = list_available_cores()
	if not names:
		if log_fn:
			log_fn("Could not reach the libretro buildbot - no cores fetched.")
		return ["<buildbot unreachable>"]
	failed = []
	for i, name in enumerate(names, 1):
		if is_fetched(name):
			continue
		if log_fn:
			log_fn(f"Fetching core {i}/{len(names)}: {name}...")
		if not fetch_core(name):
			failed.append(name)
	return failed
