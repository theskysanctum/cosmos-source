#!/usr/bin/env python3
"""Stores this account's ScreenScraper.fr username/password - used by
library/skyscraper_scraper.py to scrape screenshots/wheels/marquees for
the game library via the vendored Skyscraper CLI (skyscraper/bin/Skyscraper).

Unlike settings/igdb.py's own DEFAULT_CLIENT_ID/DEFAULT_CLIENT_SECRET,
there's deliberately no equivalent "CosmOS's own default" here - a
ScreenScraper account enforces real per-account request-rate quotas/
thread limits, the same "this is inherently a personal account, not
something a single shared project credential could sensibly cover"
reasoning settings/realdebrid.py's own docstring already lays out for
Real-Debrid. load() returns ("", "") when nothing has been saved yet;
callers treat that exactly like "not configured".

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

log = debuglog.setup("screenscraper")

CREDENTIALS_PATH = Path(__file__).parent / "screenscraper_credentials.json"


def load():
	"""Returns (username, password), both "" if nothing has been saved
	yet - never raises, matching every other credentials module's own
	"missing means empty, not an error" contract."""
	credentials = credentials_crypto.decrypt_json(CREDENTIALS_PATH)
	return credentials.get("username", "") or "", credentials.get("password", "") or ""


def save(username, password):
	# Deliberately never logging the actual password value, only whether
	# one was provided - matching settings/realdebrid.py's own identical
	# reasoning (this file, like the credential itself, is a secret).
	state = "set" if password else "empty"
	log.info(f"saving ScreenScraper credentials (username={username!r}, password={state})")
	credentials_crypto.encrypt_json({"username": username or "", "password": password or ""}, CREDENTIALS_PATH)
