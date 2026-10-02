#!/usr/bin/env python3
"""Thin client for CosmOS's own online-services Directory API (see
theskysanctum/CosmOS-online) - friends/presence, Play Together lobby
discovery, the shared RetroAchievements cache, Astronar backup, and
minting a Matrix session for Mesh Chat. Every call needs a logged-in
session (cosmos_auth.access_token()) - returns None on any failure (not
logged in, network error, server error), same "log and return None,
never raise" contract as every other backend module here (e.g.
retroachievements.fetch_summary())."""
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cosmos_auth  # noqa: E402
import server_config  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import debuglog  # noqa: E402

log = debuglog.setup("cosmos_api_client")

REQUEST_TIMEOUT_S = 15


def _request(method, path, **kwargs):
	token = cosmos_auth.access_token()
	if token is None:
		log.debug(f"not logged in, skipping {method} {path}")
		return None
	headers = kwargs.pop("headers", {})
	headers["Authorization"] = f"Bearer {token}"
	try:
		response = requests.request(
			method, f"{server_config.API_BASE}{path}",
			headers=headers, timeout=REQUEST_TIMEOUT_S, **kwargs,
		)
		response.raise_for_status()
	except Exception:
		log.exception(f"{method} {path} failed")
		return None
	if response.status_code == 204 or not response.content:
		return {}
	return response.json()


# -- Friends / presence --------------------------------------------------

def list_friends():
	return _request("GET", "/v1/friends")


def request_friend(target_account_id):
	return _request("POST", f"/v1/friends/{target_account_id}")


def accept_friend(target_account_id):
	return _request("POST", f"/v1/friends/{target_account_id}/accept")


def set_presence(online):
	return _request("PUT", "/v1/friends/presence", params={"online": online})


# -- Play Together lobbies -------------------------------------------------

def list_lobbies():
	return _request("GET", "/v1/lobbies")


def create_lobby(host_address, game_title=None):
	return _request("POST", "/v1/lobbies", params={"host_address": host_address, "game_title": game_title})


def close_lobby(lobby_id):
	return _request("DELETE", f"/v1/lobbies/{lobby_id}")


# -- RetroAchievements cache ------------------------------------------------

def get_ra_game(ra_game_id):
	return _request("GET", f"/v1/retroachievements/game/{ra_game_id}")


# -- Astronar backup ---------------------------------------------------------

def upload_astronar_backup(picture_traits, gamegrubber_history):
	return _request("PUT", "/v1/astronar/backup", json={
		"picture_traits": picture_traits,
		"gamegrubber_history": gamegrubber_history,
	})


def download_astronar_backup():
	return _request("GET", "/v1/astronar/backup")


# -- IGDB (box art) -------------------------------------------------------

def igdb_query(endpoint, apicalypse):
	"""Asks the Directory API to run an IGDB query for this signed-in account (POST /v1/igdb/<endpoint>, the query as plain text) - the server
	holds the project's IGDB secret, so none has to ship in the image. Returns IGDB's JSON (a list) or None (not logged in, rate limited,
	server or IGDB trouble). endpoint is one of games, covers, platforms."""
	return _request("POST", f"/v1/igdb/{endpoint}", data=apicalypse.encode("utf-8"), headers={"Content-Type": "text/plain; charset=utf-8"})


# -- Matrix token bridge -------------------------------------------------------

def get_matrix_token():
	"""Mints a real Matrix session for this account via the Directory
	API's admin bridge - see online/matrix_client.py for why this needs
	no browser-based login. Returns {"user_id", "homeserver",
	"access_token", "device_id"} or None."""
	return _request("GET", "/v1/matrix/token")
