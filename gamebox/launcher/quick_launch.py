#!/usr/bin/env python3
"""Backend for the main menu's quick-launch row (see build_quick_launch_row()
in app.py) - user-added website shortcuts (streaming services, etc.),
opened via Brave in kiosk mode with Widevine pre-enabled (reusing
browser/brave_launcher.py's already-verified Chromium-profile seeding, an
unrelated use of the word "profile" from this file's own - not
reimplementing it). Order is persisted in the active CosmOS profile's
own quick_launch.ini (see profiles.py), one section per entry, in display
order - ConfigParser preserves section order the same way config.ini's
menu entries already rely on.

Used to carry a per-section "home" field (CosmOS had three home screens
at the time - Games/Entertainment/Dashboard) that list_entries()/
move_entry() filtered/reordered within - retired along with the rest of
that split (there's only one home screen, Dashboard, left - see app.py's
HOME_SCREEN_NAMES comment), so this is just one flat ordered list now.
An old quick_launch.ini written before this still has a "home" key on
every section - harmlessly ignored, never read.

Favicon discovery (real <link rel=icon> parsing via BeautifulSoup, with
a plain /favicon.ico fallback) was verified against real sites
(youtube.com, netflix.com, hulu.com) before relying on it here, not
assumed to work from documentation alone."""
import configparser
import sys
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import debuglog  # noqa: E402
import paths  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
import profiles  # noqa: E402

log = debuglog.setup("quick_launch")

ICON_CACHE_DIR = paths.DATA_ROOT / "cache" / "quick-launch-icons"
REQUEST_TIMEOUT_S = 8
REQUEST_HEADERS = {"User-Agent": "Mozilla/5.0 (CosmOS quick-launch icon fetcher)"}
ICON_LINK_RELS = ("icon", "shortcut icon", "apple-touch-icon")


def _load():
	parser = configparser.ConfigParser(interpolation=None)
	path = profiles.quick_launch_ini_path()
	if path is not None:  # None before any profile has ever been created
		parser.read(path)
	return parser


def _save(parser):
	path = profiles.quick_launch_ini_path()
	if path is None:
		return  # no active profile yet - nothing to persist to
	with open(path, "w") as f:
		parser.write(f)


def list_entries():
	"""Returns every saved quick-launch entry as [{name, url, icon_path}],
	in display order. icon_path is None if no icon was ever successfully
	cached for that entry - callers should fall back to a text-only
	tile, same as Epic/GOG's own missing-art handling."""
	parser = _load()
	entries = []
	for name in parser.sections():
		icon_file = parser.get(name, "icon", fallback="")
		icon_path = str(ICON_CACHE_DIR / icon_file) if icon_file and (ICON_CACHE_DIR / icon_file).exists() else None
		entries.append({"name": name, "url": parser.get(name, "url", fallback=""), "icon_path": icon_path})
	log.debug(f"list_entries: {len(entries)} entr{'y' if len(entries) == 1 else 'ies'}")
	return entries


def _discover_icon_url(page_url, html):
	soup = BeautifulSoup(html, "html.parser")
	for rel in ICON_LINK_RELS:
		tag = soup.find("link", rel=lambda v, rel=rel: v and rel in v.lower())
		href = tag.get("href") if tag else None
		if href and not href.startswith("data:"):
			return urljoin(page_url, href)
	# No usable <link rel=icon> - the plain root-relative fallback every
	# browser itself tries.
	parsed = urlparse(page_url)
	return f"{parsed.scheme}://{parsed.netloc}/favicon.ico"


def add_entry(url):
	"""Fetches the page once to get both its <title> (used as the display
	name) and its icon, caches the icon locally, appends a new section,
	and saves. Returns the new entry dict, or None if the URL couldn't be
	fetched at all (a bad/unreachable URL) - a failed icon fetch alone
	still adds the entry with icon_path=None rather than blocking the
	whole thing over a missing picture."""
	if not url.startswith(("http://", "https://")):
		url = f"https://{url}"

	try:
		response = requests.get(url, timeout=REQUEST_TIMEOUT_S, headers=REQUEST_HEADERS)
		response.raise_for_status()
	except Exception:
		log.exception(f"could not fetch {url} to add it")
		return None

	# The page <title> was tried first here and dropped: real sites are
	# wildly inconsistent about "Brand - Tagline" vs "Tagline | Brand"
	# ordering (confirmed against real fetches - youtube.com's title is
	# clean, netflix.com's is a long SEO string, hulu.com's puts the
	# brand name *after* the separator), so no fixed split heuristic
	# reliably extracts just the brand name. The domain's own label is
	# shorter and consistent instead, even if it means "Youtube" rather
	# than "YouTube" for correct-capitalization sticklers.
	domain = urlparse(url).netloc.removeprefix("www.")
	name = domain.split(".")[0].capitalize() if domain else url

	icon_url = _discover_icon_url(url, response.text)
	icon_filename = None
	try:
		icon_response = requests.get(icon_url, timeout=REQUEST_TIMEOUT_S, headers=REQUEST_HEADERS)
		icon_response.raise_for_status()
		ICON_CACHE_DIR.mkdir(parents=True, exist_ok=True)
		suffix = Path(urlparse(icon_url).path).suffix or ".ico"
		icon_filename = f"{urlparse(url).netloc}{suffix}"
		(ICON_CACHE_DIR / icon_filename).write_bytes(icon_response.content)
	except Exception:
		log.warning(f"could not fetch icon for {url} from {icon_url}, adding without one", exc_info=True)

	parser = _load()
	# A name collision (adding the same site twice, or two sites with the
	# same page title) would otherwise silently overwrite the earlier
	# section - de-duplicate rather than lose an entry.
	unique_name = name
	suffix_n = 2
	while parser.has_section(unique_name):
		unique_name = f"{name} ({suffix_n})"
		suffix_n += 1

	parser.add_section(unique_name)
	parser.set(unique_name, "url", url)
	if icon_filename:
		parser.set(unique_name, "icon", icon_filename)
	_save(parser)
	log.info(f"added quick-launch entry {unique_name!r} -> {url}")

	return {
		"name": unique_name,
		"url": url,
		"icon_path": str(ICON_CACHE_DIR / icon_filename) if icon_filename else None,
	}


def remove_entry(name):
	parser = _load()
	if parser.has_section(name):
		parser.remove_section(name)
		_save(parser)
		log.info(f"removed quick-launch entry {name!r}")


def move_entry(name, direction):
	"""direction is -1 (move left/earlier) or +1 (move right/later). A
	no-op (not an error) if the entry is already at that end - swapping
	off the end would just silently do nothing useful, so this simply
	declines rather than raising."""
	parser = _load()
	all_sections = parser.sections()
	if name not in all_sections:
		return
	index = all_sections.index(name)
	target = index + direction
	if not (0 <= target < len(all_sections)):
		return
	other_name = all_sections[target]
	all_sections[index], all_sections[target] = all_sections[target], all_sections[index]

	reordered = configparser.ConfigParser(interpolation=None)
	for section in all_sections:
		reordered.add_section(section)
		for key, value in parser.items(section):
			reordered.set(section, key, value)
	_save(reordered)
	log.debug(f"moved {name!r} {'left' if direction < 0 else 'right'} (swapped with {other_name!r})")
