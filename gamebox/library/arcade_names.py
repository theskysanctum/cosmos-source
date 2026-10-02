#!/usr/bin/env python3
"""Resolves MAME/FBNeo-style short ROM set codes ("sf2", "mvsc", "1943")
to real, human-readable game titles, for GameGrubber's own game list
labels (see gamegrubber_backend.py) - MAME/FBNeo ROM archives are named
after their internal driver "set name", never the actual game title, so
without this GameGrubber's Shop listing shows raw codes rather than
anything a human would recognize (confirmed live: "Final Burn Neo
(Archive)" lists 7500+ games named things like "1943b2.7z", "mvsc.7z").

Reuses CosmOS's own existing library/dats.py exactly the way
library/library.py already does for the real Games library - MAME via
the installed mame binary's own -listxml, FBNeo via its official
bundled dat file at library/data/fbneo-arcade.dat - rather than
reinventing a second name database. Same real data, same resolution
logic, just applied to GameGrubber's own arcade-coded platforms instead
of locally-owned ROMs.

MAME's own full -listxml covers ~50k machines (a broad fallback -
confirmed live it resolves a handful of things FBNeo's own dat doesn't,
e.g. some older or non-FBNeo-specific sets), while FBNeo's own dat is
more specific/accurate for FBNeo's actual supported romset naming and
region/revision variants (confirmed live: 7487 of 7545 real FBNeo
Archive entries already resolve from its dat alone) - so FBNeo's dat
wins wherever both have an entry. A small number of unofficial bootleg/
hack romsets resolve from neither and simply keep their raw name - a
real, expected case (see dats.resolve()'s own docstring), not a bug.

Building the combined map means running MAME's own full -listxml (a few
real seconds) once - cached to disk afterward, since this practically
never changes between GameGrubber sessions, so it's a one-time cost,
not a per-visit one."""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dats  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import debuglog  # noqa: E402

log = debuglog.setup("gamegrubber_arcade_names")

FBNEO_DAT_PATH = Path(__file__).resolve().parent / "data" / "fbneo-arcade.dat"
# a cache the machine rebuilds if it is missing (GameBox's own tree is read-only on the image, so it lives in the user's cache folder)
CACHE_PATH = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "cosmos" / "arcade_names_cache.json"

# GameGrubber platform ids whose game list is raw MAME/FBNeo set-name
# codes rather than real titles - the only one confirmed live today is
# its Final Burn Neo archive; easy to extend here if another arcade-
# coded platform (a "MAME" one, if GameGrubber's own source ever adds
# one) shows up later.
ARCADE_CODE_PLATFORMS = {"Final Burn Neo (Archive)"}

_name_map = None  # lazily built/loaded once, kept in-process for the rest of this run


def _build_name_map():
	try:
		mame_sets = dats.mame_listxml()
	except Exception:
		log.exception("mame -listxml failed - falling back to FBNeo's own dat only")
		mame_sets = {}
	fbneo_sets = dats.parse_dat_file(FBNEO_DAT_PATH) if FBNEO_DAT_PATH.is_file() else {}

	merged = {name: info["description"] for name, info in mame_sets.items() if info["description"]}
	merged.update({name: info["description"] for name, info in fbneo_sets.items() if info["description"]})
	return merged


def _load_name_map():
	global _name_map
	if _name_map is not None:
		return _name_map

	if CACHE_PATH.is_file():
		try:
			_name_map = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
			log.debug(f"loaded {len(_name_map)} cached arcade name(s) from {CACHE_PATH}")
			return _name_map
		except (OSError, ValueError):
			log.exception(f"failed to read {CACHE_PATH} - rebuilding")

	_name_map = _build_name_map()
	try:
		CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
		CACHE_PATH.write_text(json.dumps(_name_map), encoding="utf-8")
		log.info(f"cached {len(_name_map)} arcade name(s) to {CACHE_PATH}")
	except OSError:
		log.exception(f"failed to write {CACHE_PATH} - continuing without a cache")
	return _name_map


def needs_resolution(platform_id):
	return platform_id in ARCADE_CODE_PLATFORMS


def pretty_name(raw_filename, fallback):
	"""raw_filename is the game's real .zip/.7z filename (its ROM set
	name plus extension); fallback is whatever GameGrubber's own
	display_name already was, returned unchanged if this set isn't in
	either database."""
	stem = Path(raw_filename).stem
	return _load_name_map().get(stem, fallback)
