#!/usr/bin/env python3
"""Login/registration for this CosmOS install's own online-services
account (Mesh Chat, Play Together lobbies, friends - see
theskysanctum/CosmOS-online). Registration is RA-gated by direct design
(see that repo's routes_register.py docstring): getting an account at
all requires proving ownership of a real RetroAchievements account
first, via whatever username+API key launcher/retroachievements.py
already has saved for this profile - no new credential type to enter
here. Login itself is a normal OAuth2 Device Authorization Grant against
Keycloak (RFC 8628) - the console shows a short code + URL, the player
finishes the actual login on a phone/PC, matching this project's
no-embedded-browser convention for anything gamepad-driven.

Tokens are stored per-profile via credentials_crypto.encrypt_json() at
profiles.cosmos_account_path() - never raises, "no tokens yet" and "a
corrupt/foreign-key file" are both just treated as logged out, same
contract every other credentials module here follows.

Both register() and poll_device_login() block on network calls - call
them from a background thread, same as every other network call in this
project (see app.py's run_async)."""
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
import server_config  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import debuglog  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "packaging"))
import credentials_crypto  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "launcher"))
import profiles  # noqa: E402
import retroachievements  # noqa: E402

log = debuglog.setup("cosmos_auth")

REQUEST_TIMEOUT_S = 15
_REFRESH_MARGIN_S = 60


def _token_endpoint():
	return f"{server_config.AUTH_BASE}/realms/{server_config.KEYCLOAK_REALM}/protocol/openid-connect/token"


def _device_endpoint():
	return f"{server_config.AUTH_BASE}/realms/{server_config.KEYCLOAK_REALM}/protocol/openid-connect/auth/device"


def _userinfo_endpoint():
	return f"{server_config.AUTH_BASE}/realms/{server_config.KEYCLOAK_REALM}/protocol/openid-connect/userinfo"


def load_tokens():
	"""Returns the saved {"access_token", "refresh_token", "expires_at",
	"cosmic_name"} dict, or None if never logged in / the file is
	unreadable."""
	path = profiles.cosmos_account_path()
	if path is None or not path.exists():
		return None
	return credentials_crypto.decrypt_json(path)


def _save_tokens(data):
	path = profiles.cosmos_account_path()
	if path is None:
		return
	credentials_crypto.encrypt_json(data, path)


def is_logged_in():
	return load_tokens() is not None


def logout():
	path = profiles.cosmos_account_path()
	if path and path.exists():
		path.unlink()
	log.info("logged out of CosmOS online account")


def register(password):
	"""Registers a new CosmOS online account using this profile's already-
	saved RetroAchievements username+API key as proof of identity.
	Returns {"success": True, "cosmic_name": ...} or
	{"success": False, "error": ...}. Does NOT log the new account in -
	call start_device_login()/poll_device_login() after."""
	creds = retroachievements.load_credentials()
	if not creds["username"] or not creds["api_key"]:
		return {"success": False, "error": "connect a RetroAchievements account first"}
	try:
		response = requests.post(
			f"{server_config.API_BASE}/v1/register",
			json={"ra_username": creds["username"], "ra_api_key": creds["api_key"], "password": password},
			timeout=REQUEST_TIMEOUT_S,
		)
	except Exception:
		log.exception("registration request failed")
		return {"success": False, "error": "network error"}
	if response.status_code != 200:
		try:
			detail = response.json().get("detail", response.text)
		except Exception:
			detail = response.text
		log.info(f"registration rejected: {response.status_code} {detail}")
		return {"success": False, "error": detail}
	data = response.json()
	log.info(f"registered CosmOS online account as {data['cosmic_name']!r}")
	return {"success": True, "cosmic_name": data["cosmic_name"]}


def start_device_login():
	"""Kicks off a device-grant login. Returns {"success": True,
	"verification_uri_complete", "user_code", "device_code", "interval",
	"expires_in"} or {"success": False, "error": ...}. The caller shows
	verification_uri_complete/user_code to the player, then calls
	poll_device_login(device_code, interval, expires_in)."""
	try:
		response = requests.post(
			_device_endpoint(),
			data={"client_id": server_config.KEYCLOAK_CLIENT_ID},
			timeout=REQUEST_TIMEOUT_S,
		)
		response.raise_for_status()
		data = response.json()
	except Exception:
		log.exception("failed to start device login")
		return {"success": False, "error": "network error"}
	return {
		"success": True,
		"verification_uri_complete": data.get("verification_uri_complete", data["verification_uri"]),
		"user_code": data["user_code"],
		"device_code": data["device_code"],
		"interval": data.get("interval", 5),
		"expires_in": data["expires_in"],
	}


def poll_device_login(device_code, interval=5, expires_in=600):
	"""Blocks until the player finishes logging in on another device or
	the code expires. Returns {"success": True, "cosmic_name": ...} or
	{"success": False, "error": ...}."""
	deadline = time.monotonic() + expires_in
	while time.monotonic() < deadline:
		time.sleep(interval)
		try:
			response = requests.post(
				_token_endpoint(),
				data={
					"grant_type": "urn:ietf:params:oauth:grant-type:device_code",
					"device_code": device_code,
					"client_id": server_config.KEYCLOAK_CLIENT_ID,
				},
				timeout=REQUEST_TIMEOUT_S,
			)
		except Exception:
			log.exception("device login poll failed")
			return {"success": False, "error": "network error"}
		data = response.json()
		if response.status_code == 200:
			return _store_login(data)
		error = data.get("error")
		if error == "authorization_pending":
			continue
		if error == "slow_down":
			interval += 5
			continue
		log.info(f"device login did not complete: {error}")
		return {"success": False, "error": error or "login failed"}
	return {"success": False, "error": "code expired"}


def _userinfo(token):
	try:
		response = requests.get(
			_userinfo_endpoint(),
			headers={"Authorization": f"Bearer {token}"},
			timeout=REQUEST_TIMEOUT_S,
		)
		response.raise_for_status()
		return response.json()
	except Exception:
		log.exception("failed to fetch userinfo from Keycloak")
		return {}


def _store_login(token_response):
	# preferred_username IS the Cosmic Name - permanently == the verified
	# RA username, see routes_register.py in CosmOS-online.
	cosmic_name = _userinfo(token_response["access_token"]).get("preferred_username", "")
	_save_tokens({
		"access_token": token_response["access_token"],
		"refresh_token": token_response["refresh_token"],
		"expires_at": time.time() + token_response["expires_in"],
		"cosmic_name": cosmic_name,
	})
	log.info(f"logged in as {cosmic_name!r}")
	return {"success": True, "cosmic_name": cosmic_name}


def access_token():
	"""Returns a valid access token, refreshing it first if it's near
	expiry, or None if never logged in / the refresh failed (treat that
	as logged out - callers shouldn't retry silently forever)."""
	tokens = load_tokens()
	if tokens is None:
		return None
	if tokens["expires_at"] - time.time() > _REFRESH_MARGIN_S:
		return tokens["access_token"]
	try:
		response = requests.post(
			_token_endpoint(),
			data={
				"grant_type": "refresh_token",
				"refresh_token": tokens["refresh_token"],
				"client_id": server_config.KEYCLOAK_CLIENT_ID,
			},
			timeout=REQUEST_TIMEOUT_S,
		)
		response.raise_for_status()
		data = response.json()
	except Exception:
		log.exception("failed to refresh CosmOS online session, treating as logged out")
		return None
	_store_login(data)
	return data["access_token"]
