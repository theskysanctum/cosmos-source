#!/usr/bin/env python3
"""Stores this account's Real-Debrid API key - used OS-wide by anything
that needs to unlock premium-hosted download links, not just one
feature's own private copy. Currently gamegrubber/network.py's
download_from_1fichier() (previously kept its own separate
data/gamegrubber/RealDebridAPI.txt file plus its own load/save
machinery in gamegrubber/utils.py - both removed, see that module's own
docstring on why every real caller now reads/writes through here
instead), but any future feature needing Real-Debrid unlocking reads
the exact same one credential.

Unlike settings/igdb.py's own DEFAULT_CLIENT_ID/DEFAULT_CLIENT_SECRET,
there's deliberately no equivalent "CosmOS's own default" here - a
Real-Debrid account is inherently a personal, paid subscription (it
unlocks THIS user's own premium downloads against THEIR OWN account's
traffic allowance), not something a single shared project credential
could sensibly cover the way a read-only API client ID can. load()
returns "" when no key has ever been saved; callers treat that exactly
like a missing/absent key already did before this consolidation.

Encrypted at rest via packaging/credentials_crypto.py (per direct
instruction - "encrypted files would be better... for everything API,
Token, etc related") rather than plain JSON - same read/write shape,
just opaque on disk now."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import debuglog  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "packaging"))
import credentials_crypto  # noqa: E402

log = debuglog.setup("realdebrid")

CREDENTIALS_PATH = Path(__file__).parent / "realdebrid_credentials.json"


def load():
	"""Returns the saved Real-Debrid API key, or "" if none has been
	saved yet - never raises, matching every other credentials module's
	own "missing means empty, not an error" contract."""
	return credentials_crypto.decrypt_json(CREDENTIALS_PATH).get("api_key", "") or ""


def save(api_key):
	# Deliberately never logging the actual key value, only whether one
	# was provided - matching settings/igdb.py's own identical
	# reasoning (this file, like the credential itself, is a secret).
	state = "set" if api_key else "empty"
	log.info(f"saving Real-Debrid API key (api_key={state})")
	credentials_crypto.encrypt_json({"api_key": api_key or ""}, CREDENTIALS_PATH)
