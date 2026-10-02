#!/usr/bin/env python3
"""Screenshots/wheels/marquees for the game library via the vendored
Skyscraper CLI (skyscraper/bin/Skyscraper, muldjord/skyscraper, built
from source and committed - see that directory's own LICENSE.upstream),
scraping ScreenScraper.fr (settings/screenscraper.py's own account
credentials - a real per-account service, not a project-owned default,
see that module's own docstring on why).

library/boxart.py already covers cover art via IGDB - this module is
deliberately scoped to the extra media types IGDB's own simple client
doesn't fetch: screenshot and wheel (the logo/title-wheel graphic).
ScreenScraper video scraping is NOT used - preview video comes from
YouTube instead (see library/preview_video.py), streamed live, never
cached.

Two real subprocess passes per ROM, mirroring library/dats.py's own
"run the CLI, then parse the artifact it wrote" shape (Skyscraper has
no structured-stdout output mode): a scrape pass (-s screenscraper,
populates Skyscraper's own raw resource cache) followed by a gamelist
export pass (-g/-o, composites and copies the actual per-game media
files out to a predictable location, parsed from the resulting
EmulationStation-format gamelist.xml - that format's own "marquee"
field is conventionally the wheel/logo graphic, not a real arcade
marquee, and it has no separate field for one at all, so true arcade
marquee art never makes it through this export path even though
Skyscraper itself can scrape it) - real per-account rate limits mean
this always runs one ROM at a time (see app.py's own queued fetch
pattern for boxart, same reasoning), never a folder-wide burst.

Never raises - no credentials configured, offline, no match, or an
unrecognized platform (see PLATFORM_MAP) all just mean "no extra art
for this game", the same degraded-not-broken contract every other
fetch module in this project (boxart.py, dropbox_token.py, ...)
already follows."""
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import debuglog  # noqa: E402
import paths  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "settings"))
import screenscraper as screenscraper_credentials  # noqa: E402

log = debuglog.setup("skyscraper_scraper")

REPO_ROOT = Path(__file__).resolve().parent.parent
SKYSCRAPER_BIN = REPO_ROOT / "skyscraper" / "bin" / "Skyscraper"

CACHE_DIR = paths.DATA_ROOT / "cache" / "skyscraper"
RAW_CACHE_DIR = CACHE_DIR / "_raw"  # Skyscraper's own -d resource cache, kept across runs
GAMELIST_DIR = CACHE_DIR / "_gamelist"  # scratch -g output, overwritten every run
MEDIA_DIR = CACHE_DIR / "_media"  # scratch -o output, overwritten every run

MEDIA_TYPES = ("screenshot", "wheel", "marquee")
REQUEST_TIMEOUT_S = 60

# CosmOS's own systems.json platform ids -> Skyscraper's own platform
# names, only where they differ (docs/CLIHELP.md's own -p option lists
# every name Skyscraper supports) - anything not listed here is assumed
# to already match 1:1.
PLATFORM_MAP = {
	"colecovision": "coleco",
	"lynx": "atarilynx",
	"ps1": "psx",
}


def _safe_stem(rom_stem):
	return re.sub(r"[^A-Za-z0-9._-]+", "_", rom_stem)


def cache_dir(platform_id, rom_stem):
	return CACHE_DIR / platform_id / _safe_stem(rom_stem)


def get_cached(platform_id, rom_stem):
	"""Non-blocking, no network - {"screenshot": path, "wheel": path,
	"marquee": path} for whichever media types were ever successfully
	fetched for this game before (missing types are just absent from
	the dict), or None if nothing has ever been cached at all."""
	directory = cache_dir(platform_id, rom_stem)
	found = {}
	for media_type in MEDIA_TYPES:
		for candidate in directory.glob(f"{media_type}.*"):
			found[media_type] = str(candidate)
			break
	return found or None


def _skyscraper_platform(platform_id):
	return PLATFORM_MAP.get(platform_id, platform_id)


def _run(args):
	cmd = [str(SKYSCRAPER_BIN), *args]
	log.debug(f"running {cmd}")
	try:
		result = subprocess.run(cmd, capture_output=True, text=True, timeout=REQUEST_TIMEOUT_S)
	except (subprocess.TimeoutExpired, OSError) as e:
		log.error(f"skyscraper invocation failed: {e}")
		return None
	if result.returncode != 0:
		log.warning(f"skyscraper exited {result.returncode}: {result.stderr.strip()[-500:]}")
	return result


def fetch(platform_id, rom_stem, rom_path):
	"""Blocking - two real Skyscraper invocations (scrape, then gamelist
	export), run this off the GTK thread (see app.py's run_async()).
	Returns the same shape as get_cached() on success (possibly a
	*subset* of MEDIA_TYPES if ScreenScraper simply has no wheel for an
	obscure game, and "marquee" never populates - see this module's own
	docstring on why), or None on any failure. Safe to call repeatedly;
	get_cached() above is the fast path once this has ever succeeded
	once."""
	cached = get_cached(platform_id, rom_stem)
	if cached:
		return cached

	username, password = screenscraper_credentials.load()
	if not password:
		log.debug("no ScreenScraper credentials configured, skipping")
		return None
	if not SKYSCRAPER_BIN.exists():
		log.error(f"{SKYSCRAPER_BIN} is missing - was skyscraper/bin/Skyscraper committed?")
		return None

	skyscraper_platform = _skyscraper_platform(platform_id)
	raw_cache = RAW_CACHE_DIR / platform_id
	raw_cache.mkdir(parents=True, exist_ok=True)

	scrape_result = _run(
		[
			"-p", skyscraper_platform,
			"-s", "screenscraper",
			"-u", f"{username}:{password}",
			"-d", str(raw_cache),
			"--flags", "unattend,nocovers",
			str(rom_path),
		]
	)
	if scrape_result is None:
		return None

	gamelist_dir = GAMELIST_DIR / platform_id
	media_dir = MEDIA_DIR / platform_id
	shutil.rmtree(gamelist_dir, ignore_errors=True)
	shutil.rmtree(media_dir, ignore_errors=True)
	gamelist_dir.mkdir(parents=True, exist_ok=True)
	media_dir.mkdir(parents=True, exist_ok=True)

	export_result = _run(
		[
			"-p", skyscraper_platform,
			"-d", str(raw_cache),
			"-g", str(gamelist_dir),
			"-o", str(media_dir),
			"-f", "emulationstation",
			"--flags", "unattend",
			str(rom_path),
		]
	)
	if export_result is None:
		return None

	gamelist_path = gamelist_dir / "gamelist.xml"
	if not gamelist_path.exists():
		log.warning(f"skyscraper produced no {gamelist_path} for {rom_path}")
		return None

	try:
		tree = ET.parse(gamelist_path)
	except ET.ParseError as e:
		log.error(f"could not parse {gamelist_path}: {e}")
		return None

	rom_name = Path(rom_path).name
	entry = next(
		(g for g in tree.getroot().findall("game") if Path(g.findtext("path", "")).name == rom_name), None
	)
	if entry is None:
		log.debug(f"no gamelist entry for {rom_path} - probably no ScreenScraper match")
		return None

	# EmulationStation's own gamelist schema calls the composited
	# screenshot element "image" and the logo/title-wheel graphic
	# "marquee" (its own, older naming - not Skyscraper's own "wheel"
	# resource-cache term, but what actually ends up in this XML).
	tag_map = {"screenshot": "image", "wheel": "marquee"}
	result = {}
	target = cache_dir(platform_id, rom_stem)
	for media_type, tag in tag_map.items():
		source_path = entry.findtext(tag, "")
		if not source_path:
			continue
		source_path = Path(source_path)
		if not source_path.is_absolute():
			source_path = gamelist_dir / source_path
		if not source_path.exists():
			continue
		target.mkdir(parents=True, exist_ok=True)
		dest = target / f"{media_type}{source_path.suffix}"
		shutil.copyfile(source_path, dest)
		result[media_type] = str(dest)

	shutil.rmtree(gamelist_dir, ignore_errors=True)
	shutil.rmtree(media_dir, ignore_errors=True)

	if not result:
		log.debug(f"no media types found in gamelist entry for {rom_path}")
		return None
	log.info(f"cached skyscraper media for {rom_path} ({platform_id}): {sorted(result)}")
	return result
