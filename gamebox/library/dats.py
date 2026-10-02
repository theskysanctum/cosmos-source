#!/usr/bin/env python3
"""
Resolves arcade ROM set names (e.g. "sf2", "mslug") to their proper
display name/manufacturer/year using each emulator's *official* dat
data, rather than guessing from the raw filename:

- MAME: its own `-listxml` output, generated from the actually-installed
  MAME binary. This is deliberately *not* a downloaded static dat file -
  MAME's supported set list changes between versions, so the only
  source that's guaranteed to match what this MAME build can actually
  run is the binary itself.
- FBNeo: the official dat files FBNeo's own team publishes at
  https://github.com/libretro/FBNeo/tree/master/dats (ClrMame Pro XML /
  logiqx datafile format), one per category (Arcade, Neogeo, etc).

Both formats share the same shape (just `<machine>` vs `<game>` as the
per-set element), so one streaming parser handles both without loading
a multi-hundred-MB MAME listxml dump into memory at once.
"""
import io
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import debuglog  # noqa: E402

log = debuglog.setup("dats")


def _parse_datfile_stream(source):
	sets = {}
	for _, elem in ET.iterparse(source):
		if elem.tag not in ("machine", "game"):
			continue
		name = elem.get("name")
		if name:
			# MAME's own -listxml carries a <display rotate="0|90|180|270">
			# child per machine - 90/270 means the cabinet's monitor is
			# physically rotated (a "tate"/vertical game). FBNeo's dat files
			# are plain ClrMamePro/logiqx datafiles with no <display>
			# element at all, so this is always "horizontal" for those -
			# there's no real per-game orientation data available for FBNeo,
			# so this is always "horizontal" rather than guessing.
			display = elem.find("display")
			rotate = display.get("rotate") if display is not None else None
			sets[name] = {
				"description": (elem.findtext("description") or "").strip(),
				"year": (elem.findtext("year") or "").strip(),
				"manufacturer": (elem.findtext("manufacturer") or "").strip(),
				"orientation": "vertical" if rotate in ("90", "270") else "horizontal",
			}
		elem.clear()
	log.debug(f"parsed {len(sets)} set(s) from dat stream")
	return sets


def parse_dat_file(path):
	log.debug(f"parsing dat file {path}")
	with open(path, "rb") as f:
		return _parse_datfile_stream(f)


def mame_listxml(mame_binary="mame", set_name=None):
	"""Run the installed MAME binary's own -listxml (optionally scoped to
	one set_name, much faster for lookups than a full dump) and parse it."""
	args = [mame_binary, "-listxml"]
	if set_name:
		args.append(set_name)
	log.debug(f"running {args}")
	result = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=True)
	log.debug(f"{args} returned {len(result.stdout)} bytes")
	return _parse_datfile_stream(io.BytesIO(result.stdout))


def resolve(sets, rom_name):
	"""Look up one rom_name's metadata from an already-parsed sets dict,
	falling back to the raw name if it isn't found (e.g. a bad/unknown dump,
	non-arcade set, or a hack/homebrew not in the official dat)."""
	info = sets.get(rom_name)
	if info is None:
		log.debug(f"no dat entry for {rom_name!r}, falling back to raw name")
		return {"description": rom_name, "year": "", "manufacturer": "", "orientation": "horizontal"}
	return info
