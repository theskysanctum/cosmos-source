#!/usr/bin/env python3
"""Per-player save-file cloud sync via each profile's own connected
Dropbox account (see packaging/dropbox_token.py's own "Saves app"
docstring for the auth model - each player authorizes their own
account, so storage counts against their own quota, not a project-owned
one, unlike the Astronar-backup Dropbox app). Builds on top of the
per-profile save-directory layout usb/usb_saves.py already establishes
(profiles.saves_dir()/<platform_id>/ - see that module's own docstring,
and library/systems.json's own save_dir fields for which 30 platforms
have real save coverage at all; a real, pre-existing path bug in that
file - save_dir pointing at the wrong, unused /cosmos/emulator-home
tree instead of the real /userdata/emulator-home one library.py
actually launches emulators with - was fixed as a direct prerequisite
for this module, confirmed live: saves genuinely weren't per-profile at
all before that fix).

Connection state (refresh token, chosen platforms, auto-sync toggle)
lives at profiles.dropbox_saves_path() - genuinely per-profile, not a
shared/project credential the way the
Astronar-backup Dropbox app's own DROPBOX_BACKUP_* .env values are.
Direct request: platform selection is a real per-profile picker (not
every save_dir platform unconditionally), and syncing is a manual
action with an optional auto-sync toggle, not silently automatic by
default."""
import json
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "packaging"))
import dropbox_token  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import debuglog  # noqa: E402

import profiles  # noqa: E402

log = debuglog.setup("save_sync")

UPLOAD_URL = "https://content.dropboxapi.com/2/files/upload"
DOWNLOAD_URL = "https://content.dropboxapi.com/2/files/download"
LIST_FOLDER_URL = "https://api.dropboxapi.com/2/files/list_folder"
REQUEST_TIMEOUT_S = 30

SYSTEMS_PATH = Path(__file__).resolve().parent.parent / "library" / "systems.json"

_DEFAULT_CONFIG = {"refresh_token": "", "platforms": [], "auto_sync": False}


def list_syncable_platforms():
	"""[(platform_id, platform_name), ...] for every library/systems.json
	platform with a real save_dir - the same 30-platform set
	usb/usb_saves.py's own sync_all_saves() covers, sorted by name for a
	real picker UI to show."""
	with open(SYSTEMS_PATH) as f:
		categories = json.load(f)["categories"]
	result = []
	for category in categories:
		for manufacturer in category["manufacturers"]:
			for platform in manufacturer.get("platforms", []):
				if platform.get("save_dir"):
					result.append((platform["id"], platform.get("name", platform["id"])))
	result.sort(key=lambda p: p[1].lower())
	return result


def load_config():
	"""This profile's own save-sync config - {"refresh_token", "platforms",
	"auto_sync"} - all empty/False defaults when nothing's been saved
	yet, never a missing-key surprise for callers."""
	path = profiles.dropbox_saves_path()
	if not path or not path.exists():
		return dict(_DEFAULT_CONFIG)
	try:
		with open(path) as f:
			data = json.load(f)
		return {**_DEFAULT_CONFIG, **data}
	except Exception as e:
		log.error(f"Error reading {path}: {e}")
		return dict(_DEFAULT_CONFIG)


def save_config(config):
	path = profiles.dropbox_saves_path()
	if not path:
		return False
	try:
		path.parent.mkdir(parents=True, exist_ok=True)
		with open(path, "w") as f:
			json.dump(config, f)
		return True
	except Exception as e:
		log.error(f"Error writing {path}: {e}")
		return False


def is_connected():
	return bool(load_config().get("refresh_token"))


def connect(code):
	"""Exchanges a real, freshly-pasted-in authorization code (see
	dropbox_token.saves_authorize_url()) for this profile's own refresh
	token, saving it into its own config. Returns True/False, never
	raises - a bad/expired/already-used code is a normal, expected
	failure here (Dropbox's own codes are single-use and short-lived),
	not a crash."""
	result = dropbox_token.exchange_saves_code(code)
	if not result or "refresh_token" not in result:
		return False
	config = load_config()
	config["refresh_token"] = result["refresh_token"]
	save_config(config)
	log.info("connected a new Dropbox account for save sync")
	return True


def disconnect():
	config = load_config()
	config["refresh_token"] = ""
	return save_config(config)


def set_platforms(platform_ids):
	config = load_config()
	config["platforms"] = list(platform_ids)
	return save_config(config)


def set_auto_sync(enabled):
	config = load_config()
	config["auto_sync"] = bool(enabled)
	return save_config(config)


def _auth_headers(config):
	token = dropbox_token.get_saves_access_token(config.get("refresh_token"))
	if not token:
		return None
	return {"Authorization": f"Bearer {token}"}


def _remote_folder(profile_name, platform_id):
	return f"/{profile_name}/saves/{platform_id}"


def upload_saves():
	"""Uploads every file under this profile's own saves/<platform_id>/
	folder, for each platform the profile has selected, to their own
	connected Dropbox account. Returns {platform_id: True/False/None} -
	None for a platform with no local save files to upload yet (not an
	error, a fresh profile that's never played that platform). Never
	raises."""
	config = load_config()
	headers = _auth_headers(config)
	pd = profiles.profile_dir()
	if headers is None or pd is None:
		return {pid: False for pid in config.get("platforms", [])}
	name = pd.name

	results = {}
	for platform_id in config.get("platforms", []):
		local_dir = pd / "saves" / platform_id
		if not local_dir.is_dir() or not any(local_dir.rglob("*")):
			results[platform_id] = None
			continue
		ok = True
		for local_file in local_dir.rglob("*"):
			if not local_file.is_file():
				continue
			rel = local_file.relative_to(local_dir)
			remote_path = f"{_remote_folder(name, platform_id)}/{rel.as_posix()}"
			try:
				dropbox_arg = json.dumps({"path": remote_path, "mode": "overwrite"})
				response = requests.post(
					UPLOAD_URL,
					headers={**headers, "Dropbox-API-Arg": dropbox_arg, "Content-Type": "application/octet-stream"},
					data=local_file.read_bytes(),
					timeout=REQUEST_TIMEOUT_S,
				)
				response.raise_for_status()
			except Exception as e:
				log.error(f"upload_saves: failed to upload {local_file}: {e}")
				ok = False
		results[platform_id] = ok
		if ok:
			log.debug(f"uploaded saves for {platform_id}")
	return results


def download_saves():
	"""The restore side of upload_saves() - pulls every file this
	profile has previously backed up, for each selected platform, down
	into the local saves/<platform_id>/ directory (overwriting whatever
	is already there locally). Returns {platform_id: True/False/None} -
	None when nothing's been uploaded for that platform yet. Never
	raises."""
	config = load_config()
	headers = _auth_headers(config)
	pd = profiles.profile_dir()
	if headers is None or pd is None:
		return {pid: False for pid in config.get("platforms", [])}
	name = pd.name

	results = {}
	for platform_id in config.get("platforms", []):
		remote_folder = _remote_folder(name, platform_id)
		try:
			response = requests.post(
				LIST_FOLDER_URL,
				headers={**headers, "Content-Type": "application/json"},
				json={"path": remote_folder, "recursive": True},
				timeout=REQUEST_TIMEOUT_S,
			)
			if response.status_code == 409:
				# path/not_found - nothing backed up for this platform yet.
				results[platform_id] = None
				continue
			response.raise_for_status()
			entries = response.json().get("entries", [])
		except Exception as e:
			log.error(f"download_saves: failed to list {platform_id}: {e}")
			results[platform_id] = False
			continue

		ok = True
		local_dir = pd / "saves" / platform_id
		for entry in entries:
			if entry.get(".tag") != "file":
				continue
			remote_path = entry["path_display"]
			try:
				rel = Path(remote_path).relative_to(remote_folder)
			except ValueError:
				rel = Path(Path(remote_path).name)
			local_path = local_dir / rel
			try:
				dropbox_arg = json.dumps({"path": remote_path})
				dl_response = requests.post(
					DOWNLOAD_URL,
					headers={**headers, "Dropbox-API-Arg": dropbox_arg},
					timeout=REQUEST_TIMEOUT_S,
				)
				dl_response.raise_for_status()
				local_path.parent.mkdir(parents=True, exist_ok=True)
				local_path.write_bytes(dl_response.content)
			except Exception as e:
				log.error(f"download_saves: failed to download {remote_path}: {e}")
				ok = False
		results[platform_id] = ok
		if ok:
			log.debug(f"downloaded saves for {platform_id}")
	return results
