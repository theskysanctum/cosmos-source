#!/usr/bin/env python3
"""Cosmoplex Mesh Chat - a small client for the CosmOS online-services Matrix homeserver (Tuwunel, see theskysanctum/CosmOS-online), written
against Matrix's plain HTTP client-server API with `requests` (already installed here) instead of the matrix-nio library.

Never touches a browser or any login form: connect() fetches a real, already-authenticated session from
cosmos_api_client.get_matrix_token() (the Directory API's admin-bridge-minted token) and uses its access token directly - see that repo's
matrix_admin.py for why this needs no OIDC/SSO flow at all.

Everything here is synchronous and follows the same rule as every other network module in this project: "log and return None/False on failure,
never raise", so app.py's run_async can call these functions directly. Each call is one or two ordinary HTTP requests - no /sync, no background
loop. A chat screen that wants live updates polls recent_messages() every few seconds.

  list_rooms()                  [{"room_id", "name"}] for the rooms this account has joined
  recent_messages(room_id, n)   the newest n text messages, oldest first: [{"sender", "body"}]
  send_message(room_id, text)   True/False; text over MAX_MESSAGE_LENGTH is rejected, never cut short
"""
import re
import sys
import uuid
from pathlib import Path
from urllib.parse import quote

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cosmos_api_client  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import debuglog  # noqa: E402

log = debuglog.setup("matrix_client")

# Direct requirement, not a Matrix/Tuwunel-side setting: "Cap each message at 250 characters." Enforced here, client-side, since Tuwunel has no
# plugin/module system (unlike Synapse's, which this project deliberately moved away from) for validating arbitrary message content
# server-side - this is a real limitation, not an oversight: a different Matrix client talking to the same homeserver could still send a longer
# message (recent_messages() clips what it returns to the same cap for that reason). If that ever matters, revisit with a real server-side
# enforcement mechanism instead of assuming this holds.
MAX_MESSAGE_LENGTH = 250
TIMEOUT_S = 15
API = "/_matrix/client/v3"
# "!opaque:server" (older room versions) or just "!opaque" (v12+, no server part); no "/" and no ".." ever
ROOM_ID = re.compile(r"^![A-Za-z0-9._~=+-]{1,200}(:[A-Za-z0-9.-]{1,253}(:[0-9]{1,5})?)?$")


class _Session:
	"""One authenticated Matrix connection: a base URL, a bearer token and a requests.Session."""

	def __init__(self, homeserver, token, http=None):
		self.base = homeserver.rstrip("/") + API
		self.http = http or requests.Session()
		self.http.headers.update({"Authorization": f"Bearer {token}"})

	def request(self, method, path, **kwargs):
		"""The decoded JSON body, or None on any failure (logged without the token)."""
		try:
			response = self.http.request(method, self.base + path, timeout=TIMEOUT_S, **kwargs)
		except requests.RequestException as e:
			log.error(f"Matrix {method} {path.split('?')[0]} failed: {type(e).__name__}")
			return None
		if response.status_code == 401:
			log.error("Matrix says the session is no longer valid (401) - the token bridge will mint a new one on the next call")
			return None
		if response.status_code >= 400:
			log.error(f"Matrix {method} {path.split('?')[0]} answered {response.status_code}")
			return None
		try:
			return response.json()
		except ValueError:
			log.error(f"Matrix {method} {path.split('?')[0]} sent something that isn't JSON")
			return None


def _connect(http=None):
	"""A _Session from the token bridge, or None if not possible (not logged into CosmOS online, or the bridge call failed)."""
	session = cosmos_api_client.get_matrix_token()
	if not isinstance(session, dict) or not session.get("homeserver") or not session.get("access_token"):
		log.debug("no Matrix session available (not logged in, or the bridge call failed)")
		return None
	if not re.match(r"^https?://", session["homeserver"]):
		log.error("the token bridge returned a homeserver address that isn't http(s)")
		return None
	return _Session(session["homeserver"], session["access_token"], http)


def _room_path(room_id):
	"""'/rooms/<id>' with the id percent-encoded, or None if it isn't a room id (nothing from outside goes into a URL unchecked)."""
	if not isinstance(room_id, str) or not ROOM_ID.match(room_id) or ".." in room_id:
		log.error("not a Matrix room id")
		return None
	return "/rooms/" + quote(room_id, safe="")


def list_rooms(_http=None):
	"""Returns [{"room_id", "name"}, ...] for every room this account has joined, or None on failure."""
	session = _connect(_http)
	if session is None:
		return None
	joined = session.request("GET", "/joined_rooms")
	if not isinstance(joined, dict) or not isinstance(joined.get("joined_rooms"), list):
		return None
	rooms = []
	for room_id in joined["joined_rooms"]:
		path = _room_path(room_id)
		if path is None:
			continue
		name = session.request("GET", path + "/state/m.room.name")            # 404 (no name set) is normal: fall back to the id
		title = name.get("name") if isinstance(name, dict) else None
		rooms.append({"room_id": room_id, "name": title if isinstance(title, str) and title else room_id})
	return rooms


def send_message(room_id, text, _http=None):
	"""Sends a plain-text message to room_id. Returns True/False, never raises. Rejects (returns False, no network call) rather than truncates a
	message over MAX_MESSAGE_LENGTH - silently mangling what someone typed is worse than telling the caller/UI to reject it outright. Empty or
	whitespace-only messages are rejected too."""
	if not isinstance(text, str) or not text.strip():
		return False
	if len(text) > MAX_MESSAGE_LENGTH:
		log.info(f"message rejected: {len(text)} chars exceeds the {MAX_MESSAGE_LENGTH}-char cap")
		return False
	path = _room_path(room_id)
	if path is None:
		return False
	session = _connect(_http)
	if session is None:
		return False
	# the transaction id makes a retried PUT the same message, not a second one
	result = session.request("PUT", f"{path}/send/m.room.message/{uuid.uuid4().hex}", json={"msgtype": "m.text", "body": text})
	return isinstance(result, dict) and "event_id" in result


def recent_messages(room_id, limit=20, _http=None):
	"""Returns the most recent up-to-`limit` text messages in room_id as [{"sender", "body"}, ...] (oldest first), or None on failure. A body
	longer than MAX_MESSAGE_LENGTH (sent by some other client) is clipped so it can't flood the chat screen."""
	path = _room_path(room_id)
	if path is None:
		return None
	limit = max(1, min(int(limit), 100)) if isinstance(limit, int) else 20
	session = _connect(_http)
	if session is None:
		return None
	# dir=b walks backwards from the newest event, so one request gets the latest messages without a /sync
	result = session.request("GET", f"{path}/messages", params={"dir": "b", "limit": limit, "filter": '{"types":["m.room.message"]}'})
	if not isinstance(result, dict) or not isinstance(result.get("chunk"), list):
		return None
	messages = []
	for event in reversed(result["chunk"]):
		content = event.get("content") if isinstance(event, dict) else None
		if isinstance(content, dict) and content.get("msgtype") == "m.text" and isinstance(content.get("body"), str):
			body = content["body"]
			messages.append({"sender": str(event.get("sender", "")), "body": body if len(body) <= MAX_MESSAGE_LENGTH else body[:MAX_MESSAGE_LENGTH - 1] + "…"})
	return messages
