#!/usr/bin/env python3
"""Generates mkxp-z's own game config (mkxp.json, right next to the
bundled binary at MKXPZ_DIR) before every RPG Maker XP/VX/VX Ace
launch - the RGSS engine mkxp-z reimplements, built from source and
committed into this repo (see [[project_cosmos_console_generation_cutoff]]
for why: no prebuilt Linux binary exists anywhere for it, unlike this
project's other bundled engines).

mkxp-z reads its own config from a bare "mkxp.json" resolved relative
to the CURRENT WORKING DIRECTORY at process start (confirmed directly
in its real source, src/config.cpp's Config::read() - CONF_FILE is a
plain relative filename, no CLI flag exists to point it elsewhere) -
this is why library.py's launch_game() sets cwd=MKXPZ_DIR for these
three platforms (via systems.json's own "cwd" field) rather than just
passing a {rom} argument the way every RetroArch-based platform does."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import debuglog  # noqa: E402

log = debuglog.setup("emulators.mkxp_config")

MKXPZ_DIR = Path(__file__).resolve().parent / "bin" / "mkxpz"
MKXPZ_CONFIG_PATH = MKXPZ_DIR / "mkxp.json"
STDLIB_DIR = MKXPZ_DIR / "stdlib"


def ensure_game_config(rom_path, rgss_version):
	"""rom_path is a directory (an RPG Maker project folder - see
	systems.json's own rpgmaker_xp/vx/vxace entries, all "rom_kind":
	"directory"). rgss_version is 1/2/3 for XP/VX/VX Ace respectively -
	mkxp-z can guess it from the game's own files, but being explicit
	per-platform is simpler and matches how this project already picks
	the exact right libretro core per platform elsewhere, rather than
	relying on autodetection.

	Regenerated on every launch (cheap - a few JSON lines), same
	"appliance owns its own config, nothing else writes it" reasoning
	retroarch_config.py already uses."""
	MKXPZ_CONFIG_PATH.write_text(
		json.dumps(
			{
				"gameFolder": str(rom_path),
				"rgssVersion": rgss_version,
				"rubyLoadpath": [str(STDLIB_DIR)],
			}
		)
	)
	log.debug(f"wrote {MKXPZ_CONFIG_PATH} (gameFolder={rom_path}, rgssVersion={rgss_version})")
