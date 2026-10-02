#!/usr/bin/env python3
"""Real box art for the Games list (see app.py's build_games_page()),
fetched from IGDB (Twitch's IGDB API v4) - settings/igdb.py only stores
the Client ID/Secret, this module is what actually calls the API: an
OAuth2 client-credentials token exchange, then a search for a game by a
cleaned-up title, resolving to a real cover image URL, downloaded once
and cached locally forever after (same "fetch once, cache to a real
file, check the cache first" shape as quick_launch.py's own favicon
fetching).

Never raises - every real failure mode (no credentials configured yet,
the network being down, IGDB returning zero results for an obscure/
homebrew title) just means "no box art for this game", the same way a
quick-launch entry with no fetchable icon falls back to a text-only
tile rather than blocking anything."""
import colorsys
import re
import sys
import time
from pathlib import Path

import requests
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import debuglog  # noqa: E402
import paths  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "settings"))
import igdb as igdb_credentials  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "online"))
import cosmos_api_client  # noqa: E402

log = debuglog.setup("boxart")

TOKEN_URL = "https://id.twitch.tv/oauth2/token"
API_BASE = "https://api.igdb.com/v4"
REQUEST_TIMEOUT_S = 10

CACHE_DIR = paths.DATA_ROOT / "cache" / "boxart"

# The real IGDB image size token their own docs recommend for a
# library-browsing tile (264x374) - not the full-resolution original,
# which would be real overkill for a TV-viewed grid tile and a much
# bigger, slower download for no visible benefit at that size.
COVER_SIZE = "cover_big"

# In-process only, never written to disk - a real token is valid for
# ~60 days (nowhere near worth persisting across launcher restarts, but
# real enough to not refetch on every single game's own cover lookup
# within one run).
_token_cache = {"access_token": None, "expires_at": 0.0}

# Strips the real No-Intro/Redump/TOSEC bracketed tags a ROM's own
# ["description"] (see library.py's _scan_roms_dir() - it's just the
# bare filename stem, untouched, for every non-arcade platform) always
# carries - region, revision, version, dump-quality flags, disc number
# - none of which IGDB's own search matches against. "Comix Zone (USA)"
# -> "Comix Zone", "Final Fantasy VII (USA) (Disc 1)" -> "Final Fantasy VII".
_TAG_RE = re.compile(r"\s*[\(\[][^\)\]]*[\)\]]\s*")


def clean_title(description):
	return _TAG_RE.sub(" ", description).strip()


def _get_token():
	credentials = igdb_credentials.load()
	if not credentials.get("client_id") or not credentials.get("client_secret"):
		return None
	if _token_cache["access_token"] and time.time() < _token_cache["expires_at"]:
		return _token_cache["access_token"]
	try:
		response = requests.post(
			TOKEN_URL,
			params={
				"client_id": credentials["client_id"],
				"client_secret": credentials["client_secret"],
				"grant_type": "client_credentials",
			},
			timeout=REQUEST_TIMEOUT_S,
		)
		response.raise_for_status()
		data = response.json()
	except requests.RequestException:
		log.exception("failed to fetch an IGDB/Twitch OAuth token")
		return None
	_token_cache["access_token"] = data["access_token"]
	_token_cache["expires_at"] = time.time() + data["expires_in"] - 60  # a minute of slack, not the exact edge
	log.info("fetched a fresh IGDB/Twitch OAuth token")
	return _token_cache["access_token"]


def _search_direct(query):
	"""IGDB's answer to an Apicalypse query on /games using credentials on this machine (a user-entered pair, else a developer build's .env
	default), or None."""
	token = _get_token()
	if token is None:
		return None
	credentials = igdb_credentials.load()
	headers = {"Client-ID": credentials["client_id"], "Authorization": f"Bearer {token}"}
	try:
		response = requests.post(f"{API_BASE}/games", headers=headers, data=query, timeout=REQUEST_TIMEOUT_S)
		response.raise_for_status()
		return response.json()
	except requests.RequestException:
		log.exception("IGDB search failed")
		return None


def _search(query):
	"""IGDB's answer to a /games query, or None. In order: the user's own credentials (Settings > API > IGDB) directly; else the CosmOS
	Directory API, which holds the project's IGDB secret so none ships in the image or the ISO (needs a signed-in account); else the
	credentials in a developer build's .env, if it has any."""
	if igdb_credentials.load_user() is None:
		proxied = cosmos_api_client.igdb_query("games", query)
		if proxied is not None:
			return proxied
	return _search_direct(query)


def _find_cover_url(title):
	# IGDB's own Apicalypse query language, not JSON - a plain request body, not params. image_id (not the full cover.url) is what lets the
	# actual download URL be built with our own chosen COVER_SIZE below rather than whatever size IGDB's default url field returns.
	# Quotes and backslashes in the title are escaped so a game name can't change the query.
	safe_title = title.replace("\\", " ").replace('"', " ")
	query = f'search "{safe_title}"; fields cover.image_id; limit 1;'
	results = _search(query)
	image_id = results[0].get("cover", {}).get("image_id") if results else None
	if not image_id:
		log.debug(f"no IGDB cover found for {title!r}")
		return None
	return f"https://images.igdb.com/igdb/image/upload/t_{COVER_SIZE}/{image_id}.jpg"


def cache_path(platform_id, rom_stem):
	# The same "sanitize to a safe filename" concern quick_launch.py's
	# own icon caching has, just keyed by platform+rom instead of by
	# domain - a rom stem can contain slashes/parens/etc. that are fine
	# in a display string but not in a filename.
	safe_stem = re.sub(r"[^A-Za-z0-9._-]+", "_", rom_stem)
	return CACHE_DIR / platform_id / f"{safe_stem}.jpg"


def hue_path(platform_id, rom_stem):
	"""Sidecar file next to a cached cover image, storing its own
	pre-computed dominant hue (see _extract_dominant_hue()) - a plain
	float in degrees, or the literal text "none" if the cover turned
	out to have no real hue worth extracting (see below). Computed once
	per game, not on every content-adaptive-theme lookup (see app.py's
	Games grid) - real image decoding is too slow to redo on every
	arrow-key focus change."""
	safe_stem = re.sub(r"[^A-Za-z0-9._-]+", "_", rom_stem)
	return CACHE_DIR / platform_id / f"{safe_stem}.hue"


def _extract_dominant_hue(image_path):
	"""Best-effort dominant hue (0-360), weighted toward vibrant/
	saturated pixels so a cover with a lot of flat white/black/gray
	background or border (very common in real box art) doesn't wash
	out to a meaningless average - only pixels with real saturation and
	brightness get to vote. None if the whole image is essentially
	grayscale (nothing worth extracting) - callers fall back to the
	active theme's own fixed accent hue in that case rather than a
	washed-out, near-colorless tint."""
	try:
		img = Image.open(image_path).convert("RGB")
	except (OSError, ValueError):
		log.warning(f"could not decode {image_path} for hue extraction", exc_info=True)
		return None
	img = img.resize((32, 32))
	hue_weights = {}
	for r, g, b in img.getdata():
		h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
		if s < 0.15 or v < 0.15:
			continue
		bucket = round(h * 36)  # 10-degree buckets, so near-identical hues vote together
		hue_weights[bucket] = hue_weights.get(bucket, 0.0) + s * v
	if not hue_weights:
		return None
	best_bucket = max(hue_weights, key=hue_weights.get)
	return (best_bucket / 36) * 360


def get_cached_hue(platform_id, rom_stem):
	"""Fast, cache-only lookup (a small text file read, no image
	decoding) - None if never computed yet, or if the cover had no real
	hue to extract. Callers (see app.py's content-adaptive accent
	wiring) call ensure_hue_cached() in the background to backfill it
	the first time a game with already-cached-but-not-yet-hued art is
	focused."""
	path = hue_path(platform_id, rom_stem)
	if not path.exists():
		return None
	text = path.read_text().strip()
	if not text or text == "none":
		return None
	try:
		return float(text)
	except ValueError:
		log.warning(f"{path} does not contain a real hue value")
		return None


def ensure_hue_cached(platform_id, rom_stem):
	"""Computes and caches the hue for a cover that's already on disk
	(this game's real art fetch happened before this feature existed,
	or without this function ever having run for it yet) - a no-op if
	already cached, or if there's no cover image to read at all. Purely
	local decoding, no network - still worth running off the GTK thread
	via run_async() since a real image decode isn't instant."""
	if hue_path(platform_id, rom_stem).exists():
		return
	image_path = cache_path(platform_id, rom_stem)
	if not image_path.exists():
		return
	_cache_hue(platform_id, rom_stem, image_path)


def _cache_hue(platform_id, rom_stem, image_path):
	hue = _extract_dominant_hue(image_path)
	path = hue_path(platform_id, rom_stem)
	path.parent.mkdir(parents=True, exist_ok=True)
	path.write_text(str(hue) if hue is not None else "none")


def get_cached(platform_id, rom_stem):
	"""Non-blocking, no network - the cached path if box art was ever
	successfully fetched for this game before, else None. Callers show
	a placeholder immediately and call fetch() in the background only
	when this comes back empty (see app.py's build_games_page())."""
	path = cache_path(platform_id, rom_stem)
	return str(path) if path.exists() else None


def fetch(platform_id, rom_stem, description):
	"""Blocking - real network calls, run this off the GTK thread (see
	app.py's run_async()). Returns the cached local path on success, or
	None on any failure (not configured, offline, no match) - never
	raises. Safe to call repeatedly for the same game; get_cached()
	above is the fast path once this has ever succeeded once."""
	cached = get_cached(platform_id, rom_stem)
	if cached:
		return cached
	title = clean_title(description)
	cover_url = _find_cover_url(title)
	if cover_url is None:
		return None
	try:
		response = requests.get(cover_url, timeout=REQUEST_TIMEOUT_S)
		response.raise_for_status()
	except requests.RequestException:
		log.exception(f"failed to download cover art from {cover_url}")
		return None
	path = cache_path(platform_id, rom_stem)
	path.parent.mkdir(parents=True, exist_ok=True)
	with open(path, "wb") as f:
		f.write(response.content)
	log.info(f"cached box art for {description!r} ({platform_id}) -> {path}")
	_cache_hue(platform_id, rom_stem, path)
	return str(path)
