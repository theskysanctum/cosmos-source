"""Tests for the Matrix client (matrix_client.py) against a fake homeserver - no network. Run: python3 test_matrix_client.py"""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cosmos_api_client
import matrix_client as mc

ROOM = "!abc123:example.org"
SESSION = {"user_id": "@me:example.org", "homeserver": "https://chat.example.org/", "access_token": "SECRET-TOKEN", "device_id": "D"}


class Resp:
	def __init__(self, status=200, body=None):
		self.status_code, self._body = status, body

	def json(self):
		if self._body is None:
			raise ValueError("no json")
		return self._body


class FakeHTTP:
	"""Stands in for requests.Session: records calls, answers from `routes` {(method, path-prefix): Resp}."""

	def __init__(self, routes=None):
		self.headers, self.calls, self.routes = {}, [], routes or {}

	def request(self, method, url, timeout=None, **kw):
		self.calls.append((method, url, kw, timeout))
		for (m, prefix), resp in self.routes.items():
			if m == method and prefix in url:
				if isinstance(resp, Exception):
					raise resp
				return resp
		return Resp(404, {"errcode": "M_NOT_FOUND"})


class Base(unittest.TestCase):
	def setUp(self):
		self._orig = cosmos_api_client.get_matrix_token
		cosmos_api_client.get_matrix_token = lambda: dict(SESSION)

	def tearDown(self):
		cosmos_api_client.get_matrix_token = self._orig


class Sending(Base):
	def test_sends_one_text_message_with_the_token_and_a_fresh_transaction_id(self):
		http = FakeHTTP({("PUT", "/send/m.room.message/"): Resp(200, {"event_id": "$e"})})
		self.assertTrue(mc.send_message(ROOM, "hello there", _http=http))
		method, url, kw, timeout = http.calls[0]
		self.assertEqual(method, "PUT")
		self.assertTrue(url.startswith("https://chat.example.org/_matrix/client/v3/rooms/%21abc123%3Aexample.org/send/m.room.message/"))
		self.assertEqual(kw["json"], {"msgtype": "m.text", "body": "hello there"})
		self.assertEqual(http.headers["Authorization"], "Bearer SECRET-TOKEN")
		self.assertEqual(timeout, mc.TIMEOUT_S)
		mc.send_message(ROOM, "again", _http=http)
		self.assertNotEqual(http.calls[0][1], http.calls[1][1])                 # a new message is a new transaction

	def test_the_250_character_cap_rejects_without_any_network_call(self):
		http = FakeHTTP()
		self.assertTrue(mc.send_message(ROOM, "x" * 250, _http=FakeHTTP({("PUT", "/send/"): Resp(200, {"event_id": "$e"})})))
		self.assertFalse(mc.send_message(ROOM, "x" * 251, _http=http))
		self.assertEqual(http.calls, [])
		for bad in ("", "   \n", None, 5):
			self.assertFalse(mc.send_message(ROOM, bad, _http=http))

	def test_a_bad_room_id_is_refused_before_it_reaches_a_url(self):
		http = FakeHTTP()
		for bad in ("abc:example.org", "!a/../../x:example.org", "!a..b:example.org", "!..:example.org", "!a b:example.org", "!abc:example.org/../..", "", None, "!abc:ex ample.org"):
			self.assertFalse(mc.send_message(bad, "hi", _http=http))
			self.assertIsNone(mc.recent_messages(bad, _http=http))
		self.assertEqual(http.calls, [])

	def test_both_room_id_shapes_are_accepted(self):
		for good in ("!abc123:example.org", "!abc123:example.org:8448", "!Yq3_-Zx9RoomV12IdWithoutServer"):
			http = FakeHTTP({("PUT", "/send/"): Resp(200, {"event_id": "$e"})})
			self.assertTrue(mc.send_message(good, "hi", _http=http), good)

	def test_failures_return_false_and_never_raise_or_log_the_token(self):
		import requests
		for resp in (Resp(500, {}), Resp(401, {}), Resp(200, None), Resp(200, {"no": "event id"}), requests.ConnectionError("boom")):
			http = FakeHTTP({("PUT", "/send/"): resp})
			self.assertFalse(mc.send_message(ROOM, "hi", _http=http))

	def test_no_session_means_false_and_no_call(self):
		cosmos_api_client.get_matrix_token = lambda: None
		http = FakeHTTP()
		self.assertFalse(mc.send_message(ROOM, "hi", _http=http))
		cosmos_api_client.get_matrix_token = lambda: {"homeserver": "ftp://x", "access_token": "t"}
		self.assertFalse(mc.send_message(ROOM, "hi", _http=http))
		self.assertEqual(http.calls, [])


class Reading(Base):
	def events(self):
		# the server returns newest first when dir=b
		return {"chunk": [
			{"type": "m.room.message", "sender": "@b:example.org", "content": {"msgtype": "m.text", "body": "second"}},
			{"type": "m.room.message", "sender": "@a:example.org", "content": {"msgtype": "m.image", "body": "pic.png"}},
			{"type": "m.room.message", "sender": "@a:example.org", "content": {"msgtype": "m.text", "body": "first"}},
			{"type": "m.room.message", "sender": "@c:example.org", "content": {"msgtype": "m.text", "body": "z" * 400}},
			{"type": "m.room.message", "sender": "@d:example.org", "content": "garbage"},
			"not even a dict"]}

	def test_returns_text_messages_oldest_first_and_clips_long_ones(self):
		http = FakeHTTP({("GET", "/messages"): Resp(200, self.events())})
		got = mc.recent_messages(ROOM, 20, _http=http)
		self.assertEqual([m["sender"] for m in got], ["@c:example.org", "@a:example.org", "@b:example.org"])     # reversed to oldest first, image and junk skipped
		self.assertEqual(got[1]["body"], "first")
		self.assertEqual(len(got[0]["body"]), mc.MAX_MESSAGE_LENGTH)
		self.assertTrue(got[0]["body"].endswith("…"))
		kw = http.calls[0][2]
		self.assertEqual((kw["params"]["dir"], kw["params"]["limit"]), ("b", 20))
		self.assertEqual(json.loads(kw["params"]["filter"]), {"types": ["m.room.message"]})

	def test_the_limit_is_bounded(self):
		http = FakeHTTP({("GET", "/messages"): Resp(200, {"chunk": []})})
		mc.recent_messages(ROOM, 100000, _http=http)
		mc.recent_messages(ROOM, 0, _http=http)
		mc.recent_messages(ROOM, "lots", _http=http)
		self.assertEqual([c[2]["params"]["limit"] for c in http.calls], [100, 1, 20])

	def test_failure_is_none(self):
		self.assertIsNone(mc.recent_messages(ROOM, _http=FakeHTTP({("GET", "/messages"): Resp(500, {})})))
		self.assertIsNone(mc.recent_messages(ROOM, _http=FakeHTTP({("GET", "/messages"): Resp(200, {"chunk": "x"})})))


class Rooms(Base):
	def test_lists_joined_rooms_with_names_and_falls_back_to_the_id(self):
		http = FakeHTTP({("GET", "/joined_rooms"): Resp(200, {"joined_rooms": [ROOM, "!def:example.org", "not a room id"]}),
						("GET", "/rooms/%21abc123%3Aexample.org/state/m.room.name"): Resp(200, {"name": "General"})})
		self.assertEqual(mc.list_rooms(_http=http), [{"room_id": ROOM, "name": "General"}, {"room_id": "!def:example.org", "name": "!def:example.org"}])

	def test_failure_is_none(self):
		self.assertIsNone(mc.list_rooms(_http=FakeHTTP({("GET", "/joined_rooms"): Resp(500, {})})))
		self.assertIsNone(mc.list_rooms(_http=FakeHTTP({("GET", "/joined_rooms"): Resp(200, {"joined_rooms": "x"})})))


if __name__ == "__main__":
	unittest.main()
