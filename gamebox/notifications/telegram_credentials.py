#!/usr/bin/env python3
"""Stores the Telegram API app registration (api_id/api_hash from
https://my.telegram.org - "API development tools") that Cosmochat's
Telethon client needs to talk to Telegram at all. This is a one-time,
global app-level registration, not a personal login - same api_id/
api_hash works for every CosmOS profile's own separate Telegram
login (see profiles.cosmochat_session_path()). Mirrors settings/igdb.py's
exact shape - same kind of thing (a developer API credential, not a
per-profile secret)."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import debuglog  # noqa: E402

log = debuglog.setup("telegram_credentials")

CREDENTIALS_PATH = Path(__file__).parent / "telegram_credentials.json"


def load():
	if not CREDENTIALS_PATH.exists():
		log.debug(f"{CREDENTIALS_PATH} does not exist, returning empty credentials")
		return {"api_id": "", "api_hash": ""}
	with open(CREDENTIALS_PATH) as f:
		credentials = json.load(f)
	log.debug(f"loaded credentials (api_id={'set' if credentials.get('api_id') else 'empty'})")
	return credentials


def save(api_id, api_hash):
	# Deliberately never logging the actual hash value, only whether one
	# was provided - this file, like the credentials themselves, is a secret.
	id_state = "set" if api_id else "empty"
	hash_state = "set" if api_hash else "empty"
	log.info(f"saving Telegram API credentials (api_id={id_state}, api_hash={hash_state})")
	with open(CREDENTIALS_PATH, "w") as f:
		json.dump({"api_id": api_id, "api_hash": api_hash}, f)
