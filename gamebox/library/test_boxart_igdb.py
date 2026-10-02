"""Tests for how box art asks IGDB: user credentials go direct, otherwise the Directory API proxy, otherwise a developer .env - and nothing
about the query can be changed by a game's title. Run: python3 test_boxart_igdb.py"""
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "online"))
import boxart  # noqa: E402
import cosmos_api_client  # noqa: E402


class Ask(unittest.TestCase):
	def setUp(self):
		self._saved = (boxart.igdb_credentials.load_user, cosmos_api_client.igdb_query, boxart._search_direct)
		self.proxy_calls, self.direct_calls = [], []
		self.proxy_answer = [{"cover": {"image_id": "proxied"}}]
		self.direct_answer = [{"cover": {"image_id": "direct"}}]
		self.user = None

		boxart.igdb_credentials.load_user = lambda: self.user

		def proxy(endpoint, query):
			self.proxy_calls.append((endpoint, query))
			return self.proxy_answer
		cosmos_api_client.igdb_query = proxy

		def direct(query):
			self.direct_calls.append(query)
			return self.direct_answer
		boxart._search_direct = direct

	def tearDown(self):
		boxart.igdb_credentials.load_user, cosmos_api_client.igdb_query, boxart._search_direct = self._saved

	def test_without_own_credentials_the_directory_api_answers_and_no_key_is_needed(self):
		self.assertEqual(boxart._find_cover_url("Comix Zone"), "https://images.igdb.com/igdb/image/upload/t_cover_big/proxied.jpg")
		self.assertEqual(self.proxy_calls, [("games", 'search "Comix Zone"; fields cover.image_id; limit 1;')])
		self.assertEqual(self.direct_calls, [])

	def test_a_users_own_credentials_are_used_directly_and_the_proxy_is_not_asked(self):
		self.user = {"client_id": "a", "client_secret": "b"}
		self.assertIn("direct", boxart._find_cover_url("Comix Zone"))
		self.assertEqual(self.proxy_calls, [])

	def test_when_the_proxy_has_nothing_to_say_the_direct_path_is_the_fallback(self):
		self.proxy_answer = None                                   # not signed in, rate limited, server down
		self.assertIn("direct", boxart._find_cover_url("Comix Zone"))
		self.assertEqual(len(self.direct_calls), 1)

	def test_an_empty_result_is_no_cover_not_an_error(self):
		self.proxy_answer = []
		self.assertIsNone(boxart._find_cover_url("Some Obscure Homebrew"))
		self.proxy_answer, self.direct_answer = None, None
		self.assertIsNone(boxart._find_cover_url("Anything"))

	def test_a_title_cannot_change_the_query(self):
		boxart._find_cover_url('Evil"; fields *; limit 500; where "1"="1')
		query = self.proxy_calls[0][1]
		self.assertEqual(query.count('"'), 2)                          # only the search string's own quotes
		self.assertTrue(query.endswith('fields cover.image_id; limit 1;'))
		boxart._find_cover_url("back\\slash")
		self.assertNotIn("\\", self.proxy_calls[1][1])


class Request(unittest.TestCase):
	"""cosmos_api_client.igdb_query itself: what goes over the wire."""

	def setUp(self):
		self._saved = (cosmos_api_client.cosmos_auth.access_token, cosmos_api_client.requests.request)

	def tearDown(self):
		cosmos_api_client.cosmos_auth.access_token, cosmos_api_client.requests.request = self._saved

	def test_the_query_is_posted_as_text_with_the_account_token(self):
		seen = {}

		class Resp:
			status_code, content = 200, b"[1]"

			def raise_for_status(self):
				pass

			def json(self):
				return [{"id": 1}]

		def fake(method, url, **kw):
			seen.update(method=method, url=url, **kw)
			return Resp()
		cosmos_api_client.cosmos_auth.access_token = lambda: "ACCOUNT-TOKEN"
		cosmos_api_client.requests.request = fake
		self.assertEqual(cosmos_api_client.igdb_query("games", 'search "x"; fields name;'), [{"id": 1}])
		self.assertEqual((seen["method"], seen["url"].endswith("/v1/igdb/games")), ("POST", True))
		self.assertEqual(seen["data"], b'search "x"; fields name;')
		self.assertEqual(seen["headers"]["Authorization"], "Bearer ACCOUNT-TOKEN")
		self.assertTrue(seen["headers"]["Content-Type"].startswith("text/plain"))

	def test_not_signed_in_means_none_and_no_request(self):
		cosmos_api_client.cosmos_auth.access_token = lambda: None
		cosmos_api_client.requests.request = lambda *a, **k: self.fail("a request was made")
		self.assertIsNone(cosmos_api_client.igdb_query("games", "x"))


if __name__ == "__main__":
	unittest.main()
