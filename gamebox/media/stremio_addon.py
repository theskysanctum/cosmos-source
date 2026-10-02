#!/usr/bin/env python3
"""Real Stremio add-on protocol client for CosmOS's native Video app
- replaces CosmOS's own custom add-on format for video specifically
(see media/addon_backend.py's own docstring - that format, and its
CosmOS-addons GitHub repo, are kept for Music/Photos only now).
Direct instruction: "we are just going to use stremio addons" - the
real, existing Stremio add-on ecosystem (Cinemeta, Torrentio, and many
others) already covers exactly what a custom CosmOS video add-on
format would have had to reinvent.

Protocol confirmed directly against the real, official spec
(github.com/Stremio/stremio-addon-sdk/blob/master/docs/protocol.md and
docs/api/responses/*.md) and verified live against two real, public
add-ons before writing any UI around this - Cinemeta
(https://v3-cinemeta.strem.io/manifest.json, catalogs+meta) and
Torrentio (https://torrentio.strem.fun/manifest.json, streams) both
responded exactly per spec, including a real HTTP 307 redirect on
Cinemeta's own catalog endpoint (requests follows this by default;
plain curl doesn't unless told to - confirmed both ways).

A minimal add-on serves:
	GET {base}/manifest.json                          - see fetch_manifest()
	GET {base}/catalog/{type}/{id}[/{extra}].json      - see fetch_catalog()
	GET {base}/stream/{type}/{videoId}.json            - see fetch_streams()
{base} is the manifest URL with the trailing "manifest.json" stripped -
every other endpoint is relative to it. {extra} (search/skip/genre/...)
is a URL-encoded query string, not a query string appended with "?" -
it's its own path segment (confirmed directly in the protocol doc)."""
import json
import sys
import urllib.parse
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import debuglog  # noqa: E402
import paths  # noqa: E402

log = debuglog.setup("stremio_addon")

INSTALLED_PATH = paths.DATA_ROOT / "stremio_addons.json"
REQUEST_TIMEOUT_S = 15

# A real, necessary fix, not defensive guesswork - confirmed live that
# requests' own default User-Agent ("python-requests/X.X.X") gets a
# flat 403 from Torrentio (a real, extremely popular add-on, almost
# certainly sitting behind Cloudflare's own bot filtering), while a
# plain browser-shaped one works. Applied to every request this module
# makes, not just Torrentio specifically, since any other add-on could
# have the same real-world front-end.
REQUEST_HEADERS = {
	"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}


def _base_url(manifest_url):
	return manifest_url.rsplit("/manifest.json", 1)[0]


def fetch_manifest(manifest_url):
	"""The raw manifest dict, or None on any failure (network, a 404,
	malformed JSON, or a URL that doesn't actually look like a Stremio
	add-on at all) - never raises."""
	try:
		response = requests.get(manifest_url, headers=REQUEST_HEADERS, timeout=REQUEST_TIMEOUT_S)
		response.raise_for_status()
		manifest = response.json()
	except (requests.RequestException, ValueError):
		log.exception(f"failed to fetch manifest from {manifest_url}")
		return None
	if not isinstance(manifest, dict) or "resources" not in manifest or "id" not in manifest:
		log.warning(f"{manifest_url} didn't return a real Stremio manifest")
		return None
	return manifest


def list_installed():
	"""[{"manifest_url", "manifest"}, ...] - the manifest is cached at
	install time (see install()) rather than re-fetched on every menu
	visit, since it rarely changes and a slow/offline add-on shouldn't
	make browsing the "Add-ons" list itself slow. Never raises - []
	if the file doesn't exist yet or is unreadable."""
	if not INSTALLED_PATH.exists():
		return []
	try:
		with open(INSTALLED_PATH) as f:
			return json.load(f)
	except (OSError, ValueError):
		log.exception(f"failed to read {INSTALLED_PATH}")
		return []


def _save(addons):
	INSTALLED_PATH.parent.mkdir(parents=True, exist_ok=True)
	with open(INSTALLED_PATH, "w") as f:
		json.dump(addons, f, indent="\t")


def install(manifest_url):
	"""Fetches and validates manifest_url, then saves it - blocking,
	callers run this off the GTK thread. Returns (ok: bool, message:
	str), the same shape disc_content.install_game()/addon_repo.install()
	already use. Re-installing an already-installed URL just refreshes
	its cached manifest in place."""
	manifest_url = manifest_url.strip()
	manifest = fetch_manifest(manifest_url)
	if manifest is None:
		return False, "Could not reach that add-on, or it isn't a real Stremio add-on manifest."
	addons = [a for a in list_installed() if a["manifest_url"] != manifest_url]
	addons.append({"manifest_url": manifest_url, "manifest": manifest})
	_save(addons)
	log.info(f"installed stremio add-on {manifest.get('name', manifest_url)!r} ({manifest_url})")
	return True, f"Installed {manifest.get('name', 'add-on')}."


def uninstall(manifest_url):
	_save([a for a in list_installed() if a["manifest_url"] != manifest_url])
	log.info(f"uninstalled stremio add-on {manifest_url}")


def _supports_resource(addon, resource_name, content_type):
	"""True if addon declares it handles resource_name for content_type
	- a resource entry can be a plain string (applies to every type in
	the manifest's own top-level "types") or an object with its own
	narrower "types" list (real spec - see manifest.md's own ADVANCED
	note), so both forms need checking, not just the common one."""
	for resource in addon["manifest"].get("resources", []):
		if resource == resource_name:
			return content_type in addon["manifest"].get("types", [])
		if isinstance(resource, dict) and resource.get("name") == resource_name:
			return content_type in resource.get("types", addon["manifest"].get("types", []))
	return False


def addons_with_resource(resource_name, content_type):
	return [a for a in list_installed() if _supports_resource(a, resource_name, content_type)]


def fetch_all_streams(content_type, video_id):
	"""Real Stremio behavior, not this project's own invention: browsing
	(catalog) and streaming (stream) are deliberately decoupled - an
	addon like Cinemeta provides rich catalogs but zero streams, while
	Torrentio (a completely different addon) provides streams for the
	exact same shared IMDB id with no catalog of its own at all.
	Confirmed live: asking Cinemeta itself for streams is a real, correct
	404 (it doesn't implement that resource), not a bug - the fix is
	asking every INSTALLED add-on that actually declares stream support
	for this content type, same as the real Stremio app does, not just
	whichever add-on's catalog the user happened to browse through.
	Each returned stream gets a "_addon_name" key (which installed
	add-on it came from) so the UI can label it."""
	streams = []
	for addon in addons_with_resource("stream", content_type):
		for stream in fetch_streams(addon, content_type, video_id):
			stream = dict(stream)
			stream["_addon_name"] = addon["manifest"].get("name", addon["manifest_url"])
			streams.append(stream)
	return streams


def list_catalogs(addon):
	"""The addon's own declared catalogs, straight from its manifest -
	[{"type", "id", "name", ...}, ...]. An add-on with no catalog
	resource at all (Torrentio, for example - stream-only) just has an
	empty list here; callers treat that as "nothing to browse, only
	useful when reached via another add-on's own catalog items"."""
	return addon["manifest"].get("catalogs", [])


def fetch_catalog(addon, content_type, catalog_id, extra=None):
	"""[Meta Preview Object, ...] (id/type/name/poster/description/...-
	see the real spec) for one catalog. extra, if given, is a dict of
	extra properties (search/skip/genre - see the catalog's own "extra"
	list in list_catalogs()' output for what it actually supports) -
	encoded as its own path segment per the real protocol, not a "?"
	query string. Never raises - [] on any failure."""
	base = _base_url(addon["manifest_url"])
	url = f"{base}/catalog/{content_type}/{catalog_id}"
	if extra:
		url += f"/{urllib.parse.urlencode(extra)}"
	url += ".json"
	try:
		response = requests.get(url, headers=REQUEST_HEADERS, timeout=REQUEST_TIMEOUT_S)
		response.raise_for_status()
		return response.json().get("metas", [])
	except (requests.RequestException, ValueError):
		log.exception(f"failed to fetch catalog {url}")
		return []


def fetch_streams(addon, content_type, video_id):
	"""[Stream Object, ...] for one video id (a catalog item's own "id"
	for a movie; a specific episode's own video id for a series - this
	project only surfaces movies/single items today, not per-episode
	navigation). Never raises - [] on any failure."""
	base = _base_url(addon["manifest_url"])
	url = f"{base}/stream/{content_type}/{video_id}.json"
	try:
		response = requests.get(url, headers=REQUEST_HEADERS, timeout=REQUEST_TIMEOUT_S)
		response.raise_for_status()
		return response.json().get("streams", [])
	except (requests.RequestException, ValueError):
		log.exception(f"failed to fetch streams {url}")
		return []


def resolve_stream_url(stream):
	"""A real, direct URL if the add-on provided one (stream["url"]) -
	the only kind libVLC can actually play today. Falls back to a real,
	valid magnet: URI built from stream["infoHash"] (plus any tracker
	"sources" and the filename, if given) when only a torrent hash was
	returned - deliberately still returned even though nothing in this
	project can actually play a magnet link yet (no BitTorrent/Real-
	Debrid integration exists - that's real, separate, future "Programs"
	work, not this add-on client's job), per direct instruction to show
	every stream an add-on returns rather than silently filtering
	torrents out. ytId falls back to a real, playable YouTube URL.
	None only if the stream object has none of these (malformed)."""
	if stream.get("url"):
		return stream["url"]
	if stream.get("infoHash"):
		magnet = f"magnet:?xt=urn:btih:{stream['infoHash']}"
		filename = stream.get("behaviorHints", {}).get("filename")
		if filename:
			magnet += f"&dn={urllib.parse.quote(filename)}"
		for source in stream.get("sources", []):
			if source.startswith("tracker:"):
				magnet += f"&tr={urllib.parse.quote(source[len('tracker:') :])}"
		return magnet
	if stream.get("ytId"):
		return f"https://www.youtube.com/watch?v={stream['ytId']}"
	return None
