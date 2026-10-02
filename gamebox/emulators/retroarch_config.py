#!/usr/bin/env python3
"""Generates RetroArch's own base config file, at the one path RetroArch
already looks for it automatically on every launch with no CLI flag
needed: $HOME/.config/retroarch/retroarch.cfg - library.py's
launch_game() redirects $HOME to paths.EMULATOR_HOME for every
emulator, RetroArch included.

This is deliberately NOT folded into retroarch-overrides.cfg (the
static, git-committed file every launch already applies via
--appendconfig - see its own docstring): that file's own values never
depend on where this repo happens to be checked out, so it can be a
plain committed file. The directory settings here do - they point at
assets.py's own deploy-time-fetched bundle
({repo}/emulators/bin/<assets|autoconfig|...>) - and there's no
"{repo}" token substitution for the CONTENTS of a config file the way
library.expand() substitutes it in a launch_template's own command-line
arguments, so this file has to be generated fresh with the real,
resolved path baked in, not committed as a static file with an
unresolvable token in it.

Regenerated unconditionally on every call (cheap - a handful of config
lines) rather than only-if-missing: there's no RetroArch settings menu
in this build for a user to have customized it through in the first
place (--disable-menu - see the RetroArch bundling work in this
project's history), so there's nothing here to preserve, the same
reasoning brave_launcher.py's force_profile_preferences() already
uses for Brave's own prefs file."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import debuglog  # noqa: E402
import paths  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "launcher"))
import retroachievements  # noqa: E402

log = debuglog.setup("emulators.retroarch_config")

REPO_ROOT = Path(__file__).resolve().parent.parent
RETROARCH_CFG_PATH = paths.EMULATOR_HOME / ".config" / "retroarch" / "retroarch.cfg"

# config key -> the real directory under {repo}/emulators/bin/ that key
# should point at (see assets.py's own ASSET_PACKAGES for how each gets
# populated) - every key name here was confirmed directly against a
# real installed RetroArch's own generated default config, not guessed.
DIRECTORY_SETTINGS = {
	"assets_directory": "assets",
	"joypad_autoconfig_dir": "autoconfig",
	"cheat_database_path": "cheats",
	"content_database_path": "database",
	"libretro_info_path": "info",
	"overlay_directory": "overlays",
	"video_shader_dir": "shaders",
}


def ensure_directories_cfg():
	"""Writes RETROARCH_CFG_PATH with every DIRECTORY_SETTINGS key
	pointing at its real, resolved {repo}/emulators/bin/<name> path, plus
	the active CosmOS profile's RetroAchievements login (see
	launcher/retroachievements.py) - RetroArch's own cheevos_username/
	cheevos_token config keys (confirmed against its own configuration.c),
	reusing the exact same Connect API token
	emulators/retroachievements_sync.py already syncs into Dolphin/PCSX2,
	since it's one login token valid across any RA-integrated client, not
	something tied to a specific emulator. cheevos_password is
	deliberately left blank always - only the token (never the password
	itself) is ever persisted, the same rule every other RA-login path in
	this project follows. Hardcore mode defaults off (a bigger behavior
	change - disables save states/cheats - left as something to opt into
	later, not turned on silently here).

	Called from library.py's launch_game() before every launch - cheap
	enough not to need a "only if RetroArch" guard (other emulators
	simply never read this file)."""
	RETROARCH_CFG_PATH.parent.mkdir(parents=True, exist_ok=True)
	lines = [f'{key} = "{REPO_ROOT / "emulators" / "bin" / subdir}"' for key, subdir in DIRECTORY_SETTINGS.items()]

	creds = retroachievements.load_credentials()
	logged_in = bool(creds["username"] and creds["connect_token"])
	lines.append(f'cheevos_enable = "{"true" if logged_in else "false"}"')
	lines.append('cheevos_hardcore_mode_enable = "false"')
	lines.append(f'cheevos_username = "{creds["username"] if logged_in else ""}"')
	lines.append('cheevos_password = ""')
	lines.append(f'cheevos_token = "{creds["connect_token"] if logged_in else ""}"')

	RETROARCH_CFG_PATH.write_text("\n".join(lines) + "\n")
	log.debug(f"wrote {RETROARCH_CFG_PATH} with {len(lines)} setting(s) (cheevos logged_in={logged_in})")
