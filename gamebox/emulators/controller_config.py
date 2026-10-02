#!/usr/bin/env python3
"""Binds emulators to CosmOS's own fixed virtual gamepads ("CosmOS
Pad 1"-"4" - see input/inputd.py's make_virtual_gamepads()) once, so
every emulator works correctly regardless of what real controller a
given user actually owns - the entire reason that bridge exists.

Real, current scope: RetroArch (every remaining console/handheld/
arcade platform except GameCube/PS2 - see [[project_cosmos_console_generation_cutoff]])
needs the udev autoconfig profiles below. Every SDL2-based indie engine
(mkxp-z, Love2D, TIC-80, AGS) needs nothing - confirmed live: SDL2
already auto-generates a correct GameController mapping for "CosmOS
Pad N" with zero configuration (a real, non-obvious result - SDL2 has
no built-in database entry for a vendor/product id this generic, but
its own fallback heuristic for a plausible standard-shaped gamepad
already produces the exact right button semantics). xemu (Xbox) and
Supermodel (Sega Model 3) used to be two more emulators needing
something of their own here - both removed outright along with their
respective platforms, per direct request, not something the user cared
about keeping.

GameCube (standalone Dolphin, built from source) and PS2 (standalone
PCSX2, built from source) replaced their RetroArch libretro cores this
session - **their own controller input behavior against a real
CosmOS Pad is NOT yet confirmed live** (no real controller was
available to test against in the build sandbox this session). Two real
signals worth noting, neither a substitute for a live test: PCSX2
ships its own `resources/game_controller_db.txt` (a real, standard SDL
GameControllerDB file), suggesting real SDL GameController mapping
support the same way the indie engines get it for free; Dolphin
historically supports an SDL-based `ciface` input backend alongside its
own evdev/udev one. If a real CosmOS Pad doesn't already work
correctly against both once tested, this file needs a new
`ensure_dolphin_profile()`/`ensure_pcsx2_profile()` (a committed
default config file copied into place, the same shape the removed
xemu TOML-patch precedent used), called from library.py's
launch_game() the same way ensure_retroarch_profiles() is."""
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import debuglog  # noqa: E402

log = debuglog.setup("emulators.controller_config")

REPO_ROOT = Path(__file__).resolve().parent.parent

RETROARCH_AUTOCONFIG_SRC = REPO_ROOT / "emulators" / "controller_profiles" / "retroarch_udev"
RETROARCH_AUTOCONFIG_DEST = REPO_ROOT / "emulators" / "bin" / "autoconfig" / "udev"


def ensure_retroarch_profiles():
	"""Copies this project's own 4 committed "CosmOS Pad N.cfg" udev
	autoconfig profiles (button/axis index mapping confirmed live - see
	their own file comments) into RETROARCH_AUTOCONFIG_DEST, the real
	directory RetroArch's own joypad_autoconfig_dir points at
	(retroarch_config.py) - that whole directory is deploy-time-fetched
	from the libretro buildbot (emulators/assets.py), not committed, so
	these 4 files need to land there fresh every launch rather than
	being part of the fetched set itself. Cheap (4 small file copies);
	overwrites unconditionally, same "appliance owns its own config"
	reasoning as retroarch_config.py's own ensure_directories_cfg().
	Verified live: RetroArch's real udev joypad driver does scan this
	exact directory and does open these exact files by name during its
	own device-matching pass (confirmed via strace against a real
	RetroArch process with real "CosmOS Pad" virtual devices
	present)."""
	if not RETROARCH_AUTOCONFIG_SRC.is_dir():
		log.warning(f"{RETROARCH_AUTOCONFIG_SRC} doesn't exist - nothing to copy")
		return
	RETROARCH_AUTOCONFIG_DEST.mkdir(parents=True, exist_ok=True)
	for src in RETROARCH_AUTOCONFIG_SRC.glob("*.cfg"):
		shutil.copyfile(src, RETROARCH_AUTOCONFIG_DEST / src.name)
	log.debug(f"copied {len(list(RETROARCH_AUTOCONFIG_SRC.glob('*.cfg')))} CosmOS Pad autoconfig profile(s)")
