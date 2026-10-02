#!/usr/bin/env python3
"""Game library backend: loads the Category -> Manufacturer -> Platform
tree from systems.json, lists a platform's ROMs (resolving arcade set
names via dats.py), and launches a game through its platform's
graphical/console mode, with an optional CRT/LCD/LED filter overlay.
Pure data + launch logic, no UI - app.py's GTK screens are what walk
this tree and call into it."""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "launcher"))
import graphical  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "filters"))
import filters  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dats  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "emulators"))
import retroarch_config  # noqa: E402
import mkxp_config  # noqa: E402
import xemu_config  # noqa: E402
import controller_config  # noqa: E402
import retroachievements_sync  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "input"))
import stick_mode  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import debuglog  # noqa: E402
import paths  # noqa: E402

log = debuglog.setup("library")

SYSTEMS_PATH = Path(__file__).parent / "systems.json"
FILTER_ASSETS_DIR = Path(__file__).resolve().parent.parent / "filters" / "assets"
FILTER_NAMES = {"crt", "lcd", "led"}
REPO_ROOT = Path(__file__).resolve().parent.parent


def load_categories():
	log.debug(f"loading categories from {SYSTEMS_PATH}")
	with open(SYSTEMS_PATH) as f:
		categories = json.load(f)["categories"]
	log.debug(f"loaded {len(categories)} categories")
	return categories


# The emulators built from source in the image build (xemu, Flycast; see emulators/build/build-emulators.sh), as opposed to the ones bundled
# under {repo}/emulators/bin. A checkout being developed can point COSMOS_EMULATORS_DIR at its own build.
EMULATORS_DIR = os.environ.get("COSMOS_EMULATORS_DIR", "/opt/cosmos-emulators")


def list_rom_platforms(categories):
	"""Every ROM-scanning platform in systems.json (anything with its own roms_dir - the same set Games drills into), flattened out of its category/
	manufacturer nesting; category/manufacturer "launch" entries (Steam/Epic/GOG) have no roms_dir and are skipped. Returns [{..platform fields..,
	category, manufacturer}]. Game Disc's platform lookup and Storage's "Import to..." picker use it."""
	platforms = []
	for category in categories:
		for manufacturer in category.get("manufacturers", []):
			for platform in manufacturer.get("platforms", []):
				if "roms_dir" in platform:
					platforms.append({**platform, "category": category["name"], "manufacturer": manufacturer["name"]})
	log.debug(f"list_rom_platforms: {len(platforms)} platform(s)")
	return platforms


def expand(path):
	"""{repo}, if present, substitutes the real path this repo is checked
	out at - for systems.json entries that reference a file this project
	itself ships (retroarch-overrides.cfg, fbneo's dat file) rather than
	real user data, so they don't have to hardcode where the repo happens
	to live. Genuine user/runtime data paths are plain /cosmos/...
	literals now (see paths.py) - .expanduser() stays as a harmless no-op
	safety net for any stray ~ rather than anything load-bearing."""
	return str(Path(path.replace("{repo}", str(REPO_ROOT)).replace("{emu}", EMULATORS_DIR)).expanduser())


def _scan_roms_dir(roms_dir, dat_kind, fbneo_sets, source, rom_kind="file"):
	"""A game is normally a plain file (the common case), or - for a
	multi-disc RetroArch platform - a subfolder containing exactly one
	.m3u (see disc_content.install_game(), which is what actually
	creates these folders today), launched via that .m3u instead of the
	folder itself, with the folder's own name standing in for a stem.

	rom_kind="directory" (see systems.json's own rpgmaker_mv/renpy/ags/
	etc. entries) is a third, distinct convention for platforms whose
	real content unit is a whole game folder, not a single file - a
	Ren'Py/AGS/RPG-Maker-MV project, "rom" is a directory path, and
	every direct subdirectory of roms_dir is one game (no .m3u
	involved, and plain files directly under roms_dir are ignored)."""
	if not roms_dir.is_dir():
		log.debug(f"{roms_dir} does not exist, no games")
		return []
	games = []
	for entry in sorted(roms_dir.iterdir()):
		if rom_kind == "directory":
			if not entry.is_dir():
				continue
			rom_path, stem = entry, entry.name
		elif entry.is_file():
			rom_path, stem = entry, entry.stem
		elif entry.is_dir():
			m3u_files = sorted(entry.glob("*.m3u"))
			if len(m3u_files) != 1:
				continue
			rom_path, stem = m3u_files[0], entry.name
		else:
			continue
		if dat_kind == "mame":
			info = dats.resolve(dats.mame_listxml(set_name=stem), stem)
		elif dat_kind == "fbneo":
			info = dats.resolve(fbneo_sets, stem)
		else:
			info = {"description": stem, "year": "", "manufacturer": "", "orientation": "horizontal"}
		games.append({"rom": str(rom_path), "source": source, **info})
	return games


def list_games(platform, extra_roms_dir=None):
	"""extra_roms_dir, if given (see usb_content.usb_roms_dir()), is
	scanned the same way as the platform's own roms_dir and merged in,
	tagged "source": "usb" instead of "local" - lets a game be launched
	straight off a mounted USB drive without importing it first."""
	roms_dir = Path(expand(platform["roms_dir"]))
	log.debug(f"listing games for platform {platform.get('id')} in {roms_dir} (extra_roms_dir={extra_roms_dir})")

	dat_kind = platform.get("dat")
	fbneo_sets = dats.parse_dat_file(expand(platform["dat_path"])) if dat_kind == "fbneo" else None
	rom_kind = platform.get("rom_kind", "file")

	games = _scan_roms_dir(roms_dir, dat_kind, fbneo_sets, "local", rom_kind)
	if extra_roms_dir:
		games += _scan_roms_dir(Path(extra_roms_dir), dat_kind, fbneo_sets, "usb", rom_kind)
	log.debug(f"found {len(games)} game(s) for {platform.get('id')}")
	return games


def build_command(platform, rom_path):
	return [expand(part.replace("{rom}", rom_path)) for part in platform["launch_template"]]


def launch_game(platform, rom_path):
	"""Forces navigate mode (no cursor) for the whole run, even though
	that's already the system default - an emulator never wants a system
	cursor drifting over it, and this call site is unambiguously "an
	emulator is about to run", so it's worth being explicit here rather
	than trusting that nothing upstream left mouse mode on (e.g. a
	browser session that didn't clean up after a crash).

	HOME is redirected to paths.EMULATOR_HOME for every launch - every
	emulator this project uses (RetroArch, standalone Dolphin/PCSX2)
	otherwise reads/writes its own $HOME-relative config/save dirs
	wherever the real user's home happens to be, not anywhere
	CosmOS-owned (confirmed live: this is also where
	retroarch_config.py's own generated directory-settings file has to
	live, since RetroArch looks for its base config there automatically
	with no CLI flag needed - and confirmed live the same way for
	Dolphin's/PCSX2's own real default save-dir locations, see each
	platform's own "save_dir" in systems.json).
	PATH is prefixed with /cosmos/.local/bin for the same reason
	appman.py overrides HOME when *installing* - that's where AppMan's
	own per-app symlinks would live for any bare-command-name emulator a
	launch_template might reference. Nothing currently does (xemu for
	Xbox and Supermodel for Sega Model 3 - the two AppMan-installed,
	non-RetroArch emulators this project used to have - were both
	removed outright, per direct request, not something the user cared
	about keeping), so this is currently a harmless no-op; left in place
	as the right mechanism if a standalone emulator ever comes back.

	Also (re)generates RetroArch's own directory config (see
	retroarch_config.py) before every launch - cheap and unconditional,
	even for the two standalone exceptions in this category (GameCube/
	Dolphin, PS2/PCSX2, both built from source and replacing their old
	libretro cores this session - see systems.json): it's a harmless
	no-op for a launch_template that never invokes RetroArch at all.
	RPG Maker XP/VX/VX Ace platforms (rgss_version set - see systems.json)
	additionally get a fresh mkxp-z game config (mkxp_config.py) and a
	"cwd" (see systems.json's own "cwd" field) - mkxp-z reads its own
	config relative to its process's working directory, not a CLI
	argument, unlike every RetroArch-based platform's plain {rom}
	substitution.

	Also (re)binds every pre-configured emulator to CosmOS's own
	fixed virtual gamepads (see controller_config.py) - cheap (a few
	small file copies), unconditional and harmless for platforms that
	don't need it.

	Also (re)syncs the active profile's RetroAchievements login into
	Dolphin's/PCSX2's own config (see retroachievements_sync.py) -
	cheap and unconditional for the same reason as the directory config
	above, and needs to happen on every launch (not just once) so a
	profile switch is reflected the next time either emulator runs."""
	retroarch_config.ensure_directories_cfg()
	controller_config.ensure_retroarch_profiles()
	retroachievements_sync.ensure_achievements_config()
	if platform.get("xemu"):
		missing = xemu_config.missing_files()
		if missing:
			log.warning(f"xemu: BIOS files missing from {xemu_config.BIOS_DIR}: {', '.join(missing)}")
		xemu_config.ensure_config(rom_path)
	if "rgss_version" in platform:
		mkxp_config.ensure_game_config(rom_path, platform["rgss_version"])
	cmd = build_command(platform, rom_path)
	filter_name = platform.get("filter")
	filter_image = str(FILTER_ASSETS_DIR / f"filter-{filter_name}.png") if filter_name in FILTER_NAMES else None
	env_overrides = {
		"HOME": str(paths.EMULATOR_HOME),
		"PATH": f"{paths.DATA_ROOT / '.local' / 'bin'}:{os.environ.get('PATH', '')}",
	}
	cwd = expand(platform["cwd"]) if platform.get("cwd") else None
	log.info(f"launch_game: platform={platform.get('id')} rom={rom_path} cmd={cmd} filter={filter_name} cwd={cwd}")
	stick_mode.set_navigate_mode()
	try:
		run_fn = lambda: graphical.run_graphical(cmd, env_overrides=env_overrides, cwd=cwd)  # noqa: E731
		if filter_image:
			log.debug(f"launching with filter overlay: {filter_name}")
			filters.run_with_filter(run_fn, filter_image)
		else:
			log.debug("launching without a filter overlay")
			run_fn()
	finally:
		log.debug("launch_game: reasserting navigate mode after emulator exit")
		stick_mode.set_navigate_mode()
