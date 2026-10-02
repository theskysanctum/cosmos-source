#!/usr/bin/env python3
"""Stores IGDB (Twitch/IGDB API) credentials for library/boxart.py to use
- this module is only persistence, it makes no API calls itself.

load() falls back to DEFAULT_CLIENT_ID/DEFAULT_CLIENT_SECRET below - a
real, dedicated CosmOS-the-project credential (not any individual's
personal account), loaded from the repo root's gitignored .env file
(IGDB_CLIENT_ID=.../IGDB_CLIENT_SECRET=..., via packaging/
dotenv_config.py - the same shared parser packaging/dropbox_token.py
uses) rather than embedded directly in source - whenever no user
override is saved, so box art fetching works out of the box without
every install needing its own Twitch developer app, without that
project-owned credential living in tracked source. A user's own
credentials, entered via Settings > API > IGDB, always take priority
over this fallback the moment they're saved. If .env has no IGDB_*
entries, DEFAULT_CLIENT_ID/DEFAULT_CLIENT_SECRET are simply None and
load() falls back further to library/boxart.py's own pre-existing
"no client_id, give up gracefully" handling - a reduced-functionality,
not broken, state.

The user-entered override is encrypted at rest via
packaging/credentials_crypto.py (per direct instruction - "encrypted
files would be better... for everything API, Token, etc related")
rather than plain JSON - same read/write shape, just opaque on disk
now. DEFAULT_CLIENT_ID/DEFAULT_CLIENT_SECRET themselves still come from
.env, unchanged - that file's own gitignored-plaintext handling is a
separate, already-established mechanism this doesn't touch."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import debuglog  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "packaging"))
import credentials_crypto  # noqa: E402
import dotenv_config  # noqa: E402

log = debuglog.setup("igdb")

CREDENTIALS_PATH = Path(__file__).parent / "igdb_credentials.json"

DEFAULT_CLIENT_ID = dotenv_config.get("IGDB_CLIENT_ID")
DEFAULT_CLIENT_SECRET = dotenv_config.get("IGDB_CLIENT_SECRET")
if not (DEFAULT_CLIENT_ID and DEFAULT_CLIENT_SECRET):
	log.warning(f"No IGDB_CLIENT_ID/IGDB_CLIENT_SECRET in {dotenv_config.ENV_PATH} - box art fetching needs a user-entered credential (Settings > API > IGDB) instead.")


def load():
	"""A real user-entered credential pair (Settings > API > IGDB) if one
	has ever been saved, else CosmOS's own default project credential
	(see the module docstring) - never a blank/nonfunctional pair
	callers have to special-case, the way an empty {"client_id": ""}
	used to force library/boxart.py to just give up before this (unless
	.env itself has no default to fall back to - see this module's own
	docstring)."""
	credentials = credentials_crypto.decrypt_json(CREDENTIALS_PATH)
	if credentials.get("client_id") and credentials.get("client_secret"):
		log.debug("loaded user-entered IGDB credentials")
		return credentials
	log.debug("no user IGDB credentials saved, using CosmOS's own default")
	return {"client_id": DEFAULT_CLIENT_ID, "client_secret": DEFAULT_CLIENT_SECRET}


def load_user():
	"""Only a credential pair the user saved themselves (Settings > API > IGDB), or None. Unlike load() this never falls back to an
	.env default - library/boxart.py uses it to decide whether to ask the Directory API instead."""
	credentials = credentials_crypto.decrypt_json(CREDENTIALS_PATH)
	if credentials.get("client_id") and credentials.get("client_secret"):
		return credentials
	return None


def save(client_id, client_secret):
	# Deliberately never logging the actual secret value, only whether one
	# was provided - this file, like the credentials themselves, is a secret.
	id_state = "set" if client_id else "empty"
	secret_state = "set" if client_secret else "empty"
	log.info(f"saving IGDB credentials (client_id={id_state}, client_secret={secret_state})")
	credentials_crypto.encrypt_json({"client_id": client_id, "client_secret": client_secret}, CREDENTIALS_PATH)
