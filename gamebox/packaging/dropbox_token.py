#!/usr/bin/env python3
"""Dropbox API v2 credentials - TWO real, separate apps, different
trust models, never to be confused with each other:

- "Backup" app (BACKUP_APP_KEY/BACKUP_APP_SECRET/BACKUP_REFRESH_TOKEN) -
  this project's own App-folder-scoped app, one project-owned Dropbox
  account holding every local profile's own Astronar/download-history
  backup folder, keyed by profile name (see launcher/dropbox_sync.py).
  Same "single embedded project credential" shape as packaging/
  dotenv_config.py/settings/igdb.py - get_backup_access_token() mints a
  fresh short-lived access token from the one long-lived refresh token,
  cached in memory until shortly before it expires.
- "Saves" app (SAVES_APP_KEY/SAVES_APP_SECRET) - a genuinely different
  trust model: EACH PLAYER connects their OWN Dropbox account (a real
  per-profile OAuth authorization, not a project-owned one), so save
  files count against their own storage quota, not this project's
  shared one. There's no single SAVES_REFRESH_TOKEN here - every
  profile that connects gets its own, stored per-profile (see
  launcher/save_sync.py). This module only holds the app-level key/
  secret (needed to build the authorize URL and exchange a code/refresh
  a token, regardless of which player's account it's for) plus the
  three functions any per-user OAuth flow needs: build the authorize
  URL, exchange a one-time code for that player's own tokens, and mint
  a fresh access token from whichever refresh token the caller hands
  in.

Both apps' credentials load from packaging/dotenv_config.py (the repo
root's gitignored .env) - real, deliberately embedded project-level
values (the app key/secret pairs are inherently project-owned even for
the per-user Saves app; only the per-user refresh tokens the Saves app
produces are NOT embedded here, see launcher/save_sync.py for where
those actually live)."""
import sys
import time
from pathlib import Path
from urllib.parse import urlencode

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dotenv_config  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import debuglog  # noqa: E402

log = debuglog.setup("dropbox_token")

AUTHORIZE_URL = "https://www.dropbox.com/oauth2/authorize"
TOKEN_URL = "https://api.dropboxapi.com/oauth2/token"
REQUEST_TIMEOUT_S = 15
# Real access tokens last 14400s (4h, confirmed live) - refresh a good
# margin early rather than racing an in-flight API call against the
# exact expiry second.
_REFRESH_MARGIN_S = 300

# -- Backup app (project-owned, one shared account) -------------------

BACKUP_APP_KEY = dotenv_config.get("DROPBOX_BACKUP_APP_KEY")
BACKUP_APP_SECRET = dotenv_config.get("DROPBOX_BACKUP_APP_SECRET")
BACKUP_REFRESH_TOKEN = dotenv_config.get("DROPBOX_BACKUP_REFRESH_TOKEN")
if not (BACKUP_APP_KEY and BACKUP_APP_SECRET and BACKUP_REFRESH_TOKEN):
	log.warning(f"Missing DROPBOX_BACKUP_APP_KEY/DROPBOX_BACKUP_APP_SECRET/DROPBOX_BACKUP_REFRESH_TOKEN in {dotenv_config.ENV_PATH} - Astronar/history cloud backup will be unavailable.")

_backup_cached_access_token = None
_backup_cached_expires_at = 0.0


def get_backup_access_token():
	"""Returns a real, currently-valid access token for the Backup app,
	minting a fresh one via BACKUP_REFRESH_TOKEN if the cached one is
	missing or close to expiring. None (never raises) if credentials are
	missing or the refresh request itself fails - every real caller here
	already has an established "log and return a failure result"
	contract (see launcher/dropbox_sync.py)."""
	global _backup_cached_access_token, _backup_cached_expires_at
	if not (BACKUP_APP_KEY and BACKUP_APP_SECRET and BACKUP_REFRESH_TOKEN):
		return None
	if _backup_cached_access_token and time.time() < _backup_cached_expires_at - _REFRESH_MARGIN_S:
		return _backup_cached_access_token
	try:
		response = requests.post(
			TOKEN_URL,
			data={
				"grant_type": "refresh_token",
				"refresh_token": BACKUP_REFRESH_TOKEN,
				"client_id": BACKUP_APP_KEY,
				"client_secret": BACKUP_APP_SECRET,
			},
			timeout=REQUEST_TIMEOUT_S,
		)
		response.raise_for_status()
		data = response.json()
		_backup_cached_access_token = data["access_token"]
		_backup_cached_expires_at = time.time() + float(data.get("expires_in", 14400))
		log.debug("minted a fresh Dropbox Backup access token")
		return _backup_cached_access_token
	except Exception as e:
		log.error(f"Failed to refresh Dropbox Backup access token: {e}")
		return None


# -- Saves app (per-player, each connects their own account) ----------

SAVES_APP_KEY = dotenv_config.get("DROPBOX_SAVES_APP_KEY")
SAVES_APP_SECRET = dotenv_config.get("DROPBOX_SAVES_APP_SECRET")
if not (SAVES_APP_KEY and SAVES_APP_SECRET):
	log.warning(f"Missing DROPBOX_SAVES_APP_KEY/DROPBOX_SAVES_APP_SECRET in {dotenv_config.ENV_PATH} - save-file cloud sync will be unavailable.")


def saves_authorize_url():
	"""The URL a player opens in a browser to connect THEIR OWN Dropbox
	account for save sync - no redirect_uri (the code is displayed on
	dropbox.com for them to copy back into CosmOS, same manual flow
	confirmed live for the Backup app's own one-time setup), and
	token_access_type=offline so the resulting refresh token doesn't
	expire."""
	params = {"client_id": SAVES_APP_KEY, "response_type": "code", "token_access_type": "offline"}
	return f"{AUTHORIZE_URL}?{urlencode(params)}"


def exchange_saves_code(code):
	"""One-time exchange of the authorization code a player pastes back
	in for THEIR OWN {access_token, refresh_token, expires_in} - the
	refresh_token is what launcher/save_sync.py saves per-profile
	(settings/dropbox_saves.py); the access_token here is thrown away,
	get_saves_access_token() below mints fresh ones from the saved
	refresh_token as needed. Returns None (never raises) on any
	failure - a bad/expired/already-used code included, since Dropbox's
	own codes are single-use and short-lived."""
	try:
		response = requests.post(
			TOKEN_URL,
			data={
				"grant_type": "authorization_code",
				"code": code,
				"client_id": SAVES_APP_KEY,
				"client_secret": SAVES_APP_SECRET,
			},
			timeout=REQUEST_TIMEOUT_S,
		)
		response.raise_for_status()
		return response.json()
	except Exception as e:
		log.error(f"Failed to exchange Dropbox Saves authorization code: {e}")
		return None


def get_saves_access_token(refresh_token):
	"""Mints a fresh access token from a SPECIFIC player's own refresh
	token (passed in, not read from .env - there is no single shared one
	for the Saves app, see this module's own docstring). No in-memory
	caching here (unlike get_backup_access_token()) - callers that need
	to avoid re-minting on every request should cache the result
	themselves, keyed by profile, since this module has no per-profile
	state of its own. None (never raises) on any failure."""
	if not (SAVES_APP_KEY and SAVES_APP_SECRET and refresh_token):
		return None
	try:
		response = requests.post(
			TOKEN_URL,
			data={
				"grant_type": "refresh_token",
				"refresh_token": refresh_token,
				"client_id": SAVES_APP_KEY,
				"client_secret": SAVES_APP_SECRET,
			},
			timeout=REQUEST_TIMEOUT_S,
		)
		response.raise_for_status()
		return response.json()["access_token"]
	except Exception as e:
		log.error(f"Failed to refresh a Dropbox Saves access token: {e}")
		return None
