#!/usr/bin/env python3
"""Client for github.com/theskysanctum/CosmOS-addons - CosmOS's own
add-on format (see media/addon_backend.py - Music/Photos only now,
Video moved to the real Stremio add-on protocol instead, see
media/stremio_addon.py). Same "one clear backend, log and return, never
raise" shape as emulators/cores.py/shop/gamegrubber_backend.py -
callers run everything here off the GTK thread (see app.py's
run_async()).

The repo is public, so requests here are anonymous - no token is
embedded anywhere in this project (a manual `gh auth login` is the
only GitHub credential, and only for the tools that ask for it)."""
import shutil
import sys
import tempfile
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
import addon_backend  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import debuglog  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "packaging"))

log = debuglog.setup("addon_repo")

REPO_OWNER = "theskysanctum"
REPO_NAME = "CosmOS-addons"
REPO_BRANCH = "master"
INDEX_URL = f"https://raw.githubusercontent.com/{REPO_OWNER}/{REPO_NAME}/{REPO_BRANCH}/index.json"
CONTENTS_API = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/contents"
REQUEST_TIMEOUT_S = 15

_AUTH_HEADERS = {}


def fetch_index():
	"""Returns the repo's own index.json - a list of {"id", "type",
	"name", "description", "path"} dicts - or [] on any failure
	(network, missing repo, malformed JSON). Never raises."""
	try:
		response = requests.get(INDEX_URL, headers=_AUTH_HEADERS, timeout=REQUEST_TIMEOUT_S)
		response.raise_for_status()
		return response.json()
	except (requests.RequestException, ValueError):
		log.exception("failed to fetch add-on repo index")
		return []


def list_available(media_type):
	"""fetch_index() filtered to media_type and to add-ons not already
	installed locally (matched by id - see addon_backend.list_addons(),
	which already excludes local_files itself)."""
	installed_ids = {a["id"] for a in addon_backend.list_addons(media_type)}
	available = [entry for entry in fetch_index() if entry.get("type") == media_type and entry.get("id") not in installed_ids]
	log.debug(f"list_available({media_type}): {[e['id'] for e in available]}")
	return available


def _list_repo_files(repo_path):
	"""Recursively walks repo_path via the GitHub contents API, returning
	[(path_relative_to_repo_path, download_url), ...] for every real
	file underneath it (addon.json/addon.py today, but not hardcoded to
	just those two - a future add-on with its own extra resource files
	just works)."""
	files = []
	response = requests.get(f"{CONTENTS_API}/{repo_path}", headers=_AUTH_HEADERS, timeout=REQUEST_TIMEOUT_S)
	response.raise_for_status()
	for entry in response.json():
		if entry["type"] == "file":
			files.append((entry["name"], entry["download_url"]))
		elif entry["type"] == "dir":
			for rel_path, download_url in _list_repo_files(f"{repo_path}/{entry['name']}"):
				files.append((f"{entry['name']}/{rel_path}", download_url))
	return files


def install(entry):
	"""Downloads entry["path"]'s whole directory tree from the repo into
	a temp dir first, then atomically replaces media/addons/<type>/<id>/
	with it (so a failed/partial download never leaves a half-installed
	add-on addon_backend.list_addons() might pick up). Returns
	(ok: bool, message: str), the same shape disc_content.install_game()
	already uses. Never raises."""
	media_type, addon_id = entry["type"], entry["id"]
	display_name = entry.get("name", addon_id)
	log.info(f"installing add-on {addon_id!r} ({media_type}) from {entry['path']}")

	try:
		files = _list_repo_files(entry["path"])
	except requests.RequestException:
		log.exception(f"failed to list files for add-on {addon_id!r}")
		return False, f"Could not reach the add-on repository for {display_name}."
	if not files:
		log.warning(f"add-on {addon_id!r} has no files at {entry['path']!r}")
		return False, f"{display_name} has no files in the repository."

	tmp_dir = Path(tempfile.mkdtemp(prefix="cosmos_addon_"))
	try:
		for rel_path, download_url in files:
			file_response = requests.get(download_url, headers=_AUTH_HEADERS, timeout=30)
			file_response.raise_for_status()
			dest_file = tmp_dir / rel_path
			dest_file.parent.mkdir(parents=True, exist_ok=True)
			dest_file.write_bytes(file_response.content)
	except requests.RequestException:
		log.exception(f"failed to download add-on {addon_id!r}")
		shutil.rmtree(tmp_dir, ignore_errors=True)
		return False, f"Download failed for {display_name}."

	dest_dir = addon_backend.ADDONS_ROOT / media_type / addon_id
	dest_dir.parent.mkdir(parents=True, exist_ok=True)
	if dest_dir.exists():
		shutil.rmtree(dest_dir)
	shutil.move(str(tmp_dir), str(dest_dir))
	log.info(f"installed add-on {addon_id!r} ({media_type}) -> {dest_dir}")
	return True, f"Installed {display_name}."
