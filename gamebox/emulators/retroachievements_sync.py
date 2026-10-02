#!/usr/bin/env python3
"""Writes the active CosmOS profile's RetroAchievements Connect API login
(see launcher/retroachievements.py's login_connect_api()/save_credentials())
into Dolphin's and PCSX2's own real config files, so in-game achievement
tracking actually follows whichever profile is currently active - the
same "resolve fresh, every call" approach profiles.py's own docstring
already uses for saves/quick-launch/dashboard widgets, now genuinely true
for RetroAchievements too. RetroArch is deliberately NOT handled here -
its own cheevos login goes through retroarch_config.py's
ensure_directories_cfg() instead (a plain retroarch.cfg key, not a
dedicated ini the way Dolphin/PCSX2 need - see emulators/appman.py's
docstring for how RetroArch's own build got cheevos/networking enabled).

Both emulators' real, confirmed-live config layouts (checked directly
against files each writes when its Achievements login/settings are used
for real, and against each project's own source):

- Dolphin: a *dedicated* ini, RetroAchievements.ini, holding both
  Username and ApiToken together under one [Achievements] section
  (confirmed against dolphin-emu's own AchievementSettings, PR #11669/
  #12029) - unlike PCSX2 below, there's no separate secrets file.
- PCSX2: the token is deliberately split out of the main PCSX2.ini into
  its own secrets.ini (PCSX2/pcsx2@63affdb, "Achievements: Separate
  token into its own secrets.ini file" - confirmed against our own
  built pcsx2-qt binary, which does contain that file's name as a
  string) - Username/LoginTimestamp/Enabled stay in PCSX2.ini itself,
  only Token moves to secrets.ini's own [Achievements] section.

Called from library.py's launch_game() unconditionally, before every
launch, the same way retroarch_config.ensure_directories_cfg() already
is - cheap (a couple of small ini merges) and a harmless no-op for every
platform that isn't GameCube/PS2."""
import configparser
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import debuglog  # noqa: E402
import paths  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "launcher"))
import retroachievements  # noqa: E402

log = debuglog.setup("emulators.retroachievements_sync")

DOLPHIN_RA_INI_PATH = paths.EMULATOR_HOME / ".config" / "dolphin-emu" / "RetroAchievements.ini"
PCSX2_INI_PATH = paths.EMULATOR_HOME / ".config" / "PCSX2" / "inis" / "PCSX2.ini"
PCSX2_SECRETS_INI_PATH = paths.EMULATOR_HOME / ".config" / "PCSX2" / "inis" / "secrets.ini"


def _merge_ini(path, section, values):
	"""Reads whatever's already at `path` (if anything), overlays
	`values` onto `section` only, and writes it back - every other
	section/key already in the file (graphics settings, controller
	bindings, memory card paths, ...) is preserved untouched."""
	path.parent.mkdir(parents=True, exist_ok=True)
	parser = configparser.ConfigParser()
	parser.optionxform = str  # preserve each ini's own key casing (PascalCase for both emulators here)
	if path.exists():
		parser.read(path)
	if not parser.has_section(section):
		parser.add_section(section)
	for key, value in values.items():
		parser.set(section, key, value)
	with open(path, "w") as f:
		parser.write(f, space_around_delimiters=True)


def ensure_achievements_config():
	"""Syncs the active profile's RetroAchievements login into both
	Dolphin's and PCSX2's own config - logged in with Username+ApiToken/
	Token and Enabled=True if the active profile has a stored Connect
	API token, logged out (Enabled=False, no stale username/token left
	behind) otherwise, so switching CosmOS profiles - or having no
	RetroAchievements login at all - can never leave a previous
	profile's account still active in either emulator."""
	creds = retroachievements.load_credentials()
	username = creds["username"]
	token = creds["connect_token"]
	logged_in = bool(username and token)

	_merge_ini(
		DOLPHIN_RA_INI_PATH,
		"Achievements",
		{
			"Enabled": "True" if logged_in else "False",
			"Username": username if logged_in else "",
			"ApiToken": token if logged_in else "",
		},
	)

	_merge_ini(
		PCSX2_INI_PATH,
		"Achievements",
		{
			"Enabled": "true" if logged_in else "false",
			"Username": username if logged_in else "",
			"LoginTimestamp": str(int(time.time())) if logged_in else "",
		},
	)
	_merge_ini(
		PCSX2_SECRETS_INI_PATH,
		"Achievements",
		{"Token": token if logged_in else ""},
	)

	log.debug(f"ensure_achievements_config: logged_in={logged_in} username={'set' if username else 'empty'}")
