#!/usr/bin/env python3
"""Real per-profile cloud backup, via this project's own App-folder-
scoped Dropbox app (see packaging/dropbox_token.py) - each CosmOS
profile ("Astronar") gets its own folder in that one shared app space,
named after the profile, holding whatever local files can't otherwise
stay in sync across a reinstall or a move to a different machine: the
Astronar's own saved appearance (picture_traits.json - the full set of
customization choices that reconstructs it exactly, not a random seed;
avatar3d.build_character() hasn't been seed-random in a long time) and
GameGrubber's own per-profile download history (gamegrubber_history.json
- see shop/gamegrubber_backend.py's download_env()/gamegrubber/
cosmos_bridge.py's own RGSX_PROFILE_HISTORY_PATH handling for how that
file becomes per-profile in the first place).

BACKUP_FILES is the one place that lists what gets backed up - a direct
request ("other stuff I can't keep synced without an outside source")
means this list is expected to grow; adding a new (local_filename,
remote_filename) pair here is the only change needed to start backing
up something new, both upload and download already iterate it
generically.

App-folder access (confirmed live against the real account) means every
path here is automatically confined to this app's own sandboxed space
in the account's Dropbox - "/<profile_name>/<file>" can never reach
anything outside it, regardless of what's actually in that Dropbox
account otherwise."""
import json
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "packaging"))
import dropbox_token  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import debuglog  # noqa: E402

import profiles  # noqa: E402

log = debuglog.setup("dropbox_sync")

UPLOAD_URL = "https://content.dropboxapi.com/2/files/upload"
DOWNLOAD_URL = "https://content.dropboxapi.com/2/files/download"
REQUEST_TIMEOUT_S = 30

# (local filename under the profile's own directory, remote filename in
# its Dropbox folder) - see this module's own docstring on why this
# list is the one place to extend for "other stuff."
BACKUP_FILES = [
	("picture_traits.json", "picture_traits.json"),
	("gamegrubber_history.json", "gamegrubber_history.json"),
]


def _auth_headers():
	token = dropbox_token.get_access_token()
	if not token:
		return None
	return {"Authorization": f"Bearer {token}"}


def upload_profile_backup(profile_name=None):
	"""Uploads every BACKUP_FILES entry that exists locally for
	`profile_name` (the active profile if None) to that profile's own
	Dropbox folder - files that don't exist locally yet (e.g. a brand
	new profile with no GameGrubber downloads yet) are skipped, not
	uploaded as empty/missing. Returns {local_filename: True/False} -
	never raises, matching every other real backend module's own "log
	and return a result, don't crash the caller" contract."""
	headers = _auth_headers()
	if headers is None:
		log.warning("upload_profile_backup: no Dropbox credentials available")
		return {local: False for local, _ in BACKUP_FILES}

	pd = profiles.profile_dir(profile_name)
	if pd is None:
		log.warning("upload_profile_backup: no active profile")
		return {local: False for local, _ in BACKUP_FILES}
	name = profile_name or pd.name

	results = {}
	for local_filename, remote_filename in BACKUP_FILES:
		local_path = pd / local_filename
		if not local_path.exists():
			log.debug(f"upload_profile_backup: {local_path} doesn't exist yet, skipping")
			results[local_filename] = None
			continue
		try:
			data = local_path.read_bytes()
			dropbox_arg = json.dumps({"path": f"/{name}/{remote_filename}", "mode": "overwrite"})
			response = requests.post(
				UPLOAD_URL,
				headers={**headers, "Dropbox-API-Arg": dropbox_arg, "Content-Type": "application/octet-stream"},
				data=data,
				timeout=REQUEST_TIMEOUT_S,
			)
			response.raise_for_status()
			results[local_filename] = True
			log.debug(f"uploaded {local_path} -> /{name}/{remote_filename}")
		except Exception as e:
			log.error(f"upload_profile_backup: failed to upload {local_filename}: {e}")
			results[local_filename] = False
	return results


def download_profile_backup(profile_name=None):
	"""The restore side of upload_profile_backup() - pulls every
	BACKUP_FILES entry that exists in `profile_name`'s Dropbox folder
	down into its local profile directory, overwriting whatever's
	already there. A remote file that doesn't exist yet (nothing ever
	uploaded for it) is skipped, not treated as an error. Returns
	{local_filename: True/False/None} - None means "nothing to restore
	for this file," matching upload's own tri-state convention."""
	headers = _auth_headers()
	if headers is None:
		log.warning("download_profile_backup: no Dropbox credentials available")
		return {local: False for local, _ in BACKUP_FILES}

	pd = profiles.profile_dir(profile_name)
	if pd is None:
		log.warning("download_profile_backup: no active profile")
		return {local: False for local, _ in BACKUP_FILES}
	name = profile_name or pd.name

	results = {}
	for local_filename, remote_filename in BACKUP_FILES:
		try:
			dropbox_arg = json.dumps({"path": f"/{name}/{remote_filename}"})
			response = requests.post(
				DOWNLOAD_URL,
				headers={**headers, "Dropbox-API-Arg": dropbox_arg},
				timeout=REQUEST_TIMEOUT_S,
			)
			if response.status_code == 409:
				# path/not_found - nothing backed up for this file yet.
				log.debug(f"download_profile_backup: no remote {remote_filename} for {name} yet")
				results[local_filename] = None
				continue
			response.raise_for_status()
			pd.mkdir(parents=True, exist_ok=True)
			(pd / local_filename).write_bytes(response.content)
			results[local_filename] = True
			log.debug(f"downloaded /{name}/{remote_filename} -> {pd / local_filename}")
		except Exception as e:
			log.error(f"download_profile_backup: failed to download {local_filename}: {e}")
			results[local_filename] = False
	return results
