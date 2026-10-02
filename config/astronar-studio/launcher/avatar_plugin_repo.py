#!/usr/bin/env python3
"""Client for github.com/theskysanctum/CosmOS-avatar-plugins - real
avatar generator plugins (see avatar_plugin_backend.py's own docstring
for the two real plugin types and how they actually run). Same "one
clear backend, log and return, never raise" shape as media/addon_repo.py,
which this deliberately mirrors closely - callers run everything here
off the GTK thread (see app.py's run_async()).

Public repo, read anonymously - addon_repo.py's _AUTH_HEADERS is empty
(no token is embedded anywhere in this project)."""
import shutil
import sys
import tempfile
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "media"))
import addon_repo  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import debuglog  # noqa: E402

log = debuglog.setup("avatar_plugin_repo")

REPO_OWNER = "theskysanctum"
REPO_NAME = "CosmOS-avatar-plugins"
REPO_BRANCH = "main"
INDEX_URL = f"https://raw.githubusercontent.com/{REPO_OWNER}/{REPO_NAME}/{REPO_BRANCH}/index.json"
CONTENTS_API = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/contents"
REQUEST_TIMEOUT_S = 15

_AUTH_HEADERS = addon_repo._AUTH_HEADERS


def fetch_index():
	"""Returns the repo's own index.json - a list of {"id", "type",
	"name", "description", "path"} dicts. "type" is "motion" or
	"bodypart" for anything this backend can actually load (see
	avatar_plugin_backend.py) - the real external repo's own index.json
	may still list a "showcase"-typed entry from before that type was
	removed as unnecessary, but nothing here loads/runs one anymore, so
	list_available()/install() treat it like any other unrecognized
	type: listed if asked for by name, otherwise just inert on disk if
	ever installed. Returns [] on any failure (network, missing repo,
	malformed JSON). Never raises."""
	try:
		response = requests.get(INDEX_URL, headers=_AUTH_HEADERS, timeout=REQUEST_TIMEOUT_S)
		response.raise_for_status()
		return response.json()
	except (requests.RequestException, ValueError):
		log.exception("failed to fetch avatar plugin repo index")
		return []


def list_available(plugin_type=None):
	"""fetch_index() filtered to plugin_type (if given) and to plugins
	not already installed locally (matched by id)."""
	sys.path.insert(0, str(Path(__file__).resolve().parent))
	import avatar_plugin_backend  # noqa: E402
	installed_ids = {p["id"] for p in avatar_plugin_backend.list_installed()}
	available = [
		entry for entry in fetch_index()
		if (plugin_type is None or entry.get("type") == plugin_type) and entry.get("id") not in installed_ids
	]
	log.debug(f"list_available({plugin_type}): {[e['id'] for e in available]}")
	return available


def _list_repo_files(repo_path):
	"""Recursively walks repo_path via the GitHub contents API, returning
	[(path_relative_to_repo_path, download_url), ...] for every real
	file underneath it."""
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
	a temp dir first, then atomically replaces
	avatar_plugins/<type>/<id>/ with it. Returns (ok: bool, message:
	str). Never raises."""
	sys.path.insert(0, str(Path(__file__).resolve().parent))
	import avatar_plugin_backend  # noqa: E402

	plugin_type, plugin_id = entry["type"], entry["id"]
	display_name = entry.get("name", plugin_id)
	log.info(f"installing avatar plugin {plugin_id!r} ({plugin_type}) from {entry['path']}")

	try:
		files = _list_repo_files(entry["path"])
	except requests.RequestException:
		log.exception(f"failed to list files for avatar plugin {plugin_id!r}")
		return False, f"Could not reach the avatar plugin repository for {display_name}."
	if not files:
		log.warning(f"avatar plugin {plugin_id!r} has no files at {entry['path']!r}")
		return False, f"{display_name} has no files in the repository."

	tmp_dir = Path(tempfile.mkdtemp(prefix="cosmos_avatar_plugin_"))
	try:
		for rel_path, download_url in files:
			file_response = requests.get(download_url, headers=_AUTH_HEADERS, timeout=30)
			file_response.raise_for_status()
			dest_file = tmp_dir / rel_path
			dest_file.parent.mkdir(parents=True, exist_ok=True)
			dest_file.write_bytes(file_response.content)
	except requests.RequestException:
		log.exception(f"failed to download avatar plugin {plugin_id!r}")
		shutil.rmtree(tmp_dir, ignore_errors=True)
		return False, f"Download failed for {display_name}."

	dest_dir = avatar_plugin_backend.PLUGINS_ROOT / plugin_type / plugin_id
	dest_dir.parent.mkdir(parents=True, exist_ok=True)
	if dest_dir.exists():
		shutil.rmtree(dest_dir)
	shutil.move(str(tmp_dir), str(dest_dir))
	log.info(f"installed avatar plugin {plugin_id!r} ({plugin_type}) to {dest_dir}")
	return True, f"{display_name} installed."
