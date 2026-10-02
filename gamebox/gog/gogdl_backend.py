#!/usr/bin/env python3
"""Wraps `gogdl` (github.com/Heroic-Games-Launcher/heroic-gogdl, the same
GPL-3.0 download/launch engine Heroic itself uses for GOG) for the
standalone GOG client - a subprocess wrapper, matching this project's
existing style (nmcli, bluetoothctl, legendary via epic/legendary_backend.py).

gogdl is explicitly "not a user friendly cli... meant to be used by some
other application" (its own README) and, unlike legendary, has no
"list my library" command at all - browsing/downloading is something
Heroic's own frontend does directly against GOG's APIs, using the access
token gogdl manages. list_library() below does the same thing, calling
the same endpoints Heroic's gog/library.ts calls, with the same field
names - verified against Heroic's actual source rather than guessed."""
import configparser
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import debuglog  # noqa: E402
import paths  # noqa: E402

log = debuglog.setup("gogdl_backend")

GOGDL_BIN = "gogdl"
AUTH_CONFIG_PATH = paths.DATA_ROOT / "gog_store" / "auth.json"
GALAXY_LIBRARY_URL = "https://galaxy-library.gog.com"
GAMESDB_URL = "https://gamesdb.gog.com"
PROGRESS_RE = re.compile(r"Progress:\s*([\d.]+)\s")

WINE_PREFIX_ROOT = paths.DATA_ROOT / "gog-prefixes"
INSTALL_ROOT = paths.DATA_ROOT / "games" / "gog"
LAUNCH_OPTIONS_PATH = Path(__file__).parent / "launch_options.ini"
_ENV_VAR_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")

# The same well-known public GOG Galaxy client id/secret every third-party
# GOG client (Heroic, gogdl itself, minigalaxy...) uses - baked into
# gogdl's own source, not a secret specific to this project.
CLIENT_ID = "46899977096215655"
LOGIN_URL = (
	"https://auth.gog.com/auth?client_id=46899977096215655"
	"&redirect_uri=https%3A%2F%2Fembed.gog.com%2Fon_login_success%3Forigin%3Dclient"
	"&response_type=code&layout=galaxy"
)
LOGIN_SUCCESS_PREFIX = "https://embed.gog.com/on_login_success?"


def _run(args):
	AUTH_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
	cmd = [GOGDL_BIN, "--auth-config-path", str(AUTH_CONFIG_PATH), *args]
	log.debug(f"running: {cmd}")
	return subprocess.run(cmd, capture_output=True, text=True)


def _credentials():
	"""Runs `gogdl auth` with no --code, which prints the current
	credentials as JSON (refreshing them first if expired) or "null" if
	never logged in - gogdl's own refresh-on-expiry logic, not
	reimplemented here. Returns None if not authenticated or the call
	fails."""
	result = _run(["auth"])
	if result.returncode != 0:
		log.warning(f"gogdl auth failed: {result.stderr.strip()}")
		return None
	try:
		data = json.loads(result.stdout)
	except json.JSONDecodeError:
		log.warning(f"could not parse gogdl auth output: {result.stdout!r}")
		return None
	if not data or "access_token" not in data:
		return None
	return data


def is_authenticated():
	creds = _credentials()
	return (creds is not None), (creds.get("user_id") if creds else None)


def auth_via_code(code):
	"""code is the "code" query parameter from the redirect URL GOG sends
	the embedded login webview to once sign-in succeeds (see
	gog_app.py's login screen) - the same mechanism Heroic's own GOG
	login uses, just watching the URL directly rather than a JS bridge
	(GOG's OAuth flow, unlike Epic's, hands the code back as a plain
	redirect - no window.ue-style callback needed)."""
	result = _run(["auth", "--code", code])
	success = result.returncode == 0
	log.info(f"login via auth code: {'success' if success else 'failed'}")
	if not success:
		log.warning(f"gogdl auth stderr: {result.stderr.strip()}")
	return success


def _resolve_image_url(url_format, ext="jpg"):
	"""GamesDB image fields are a "url_format" template with literal
	{formatter}/{ext} placeholders, not a ready-to-fetch URL - same
	substitution Heroic's gogToUnifiedInfo() does (empty formatter, a
	real extension)."""
	if not url_format:
		return None
	return url_format.replace("{formatter}", "").replace("{ext}", ext)


def _fetch_galaxy_library(access_token, user_id, page_token=None):
	url = f"{GALAXY_LIBRARY_URL}/users/{user_id}/releases"
	params = {"page_token": page_token} if page_token else {}
	headers = {"Authorization": f"Bearer {access_token}"}
	response = requests.get(url, params=params, headers=headers, timeout=10)
	response.raise_for_status()
	data = response.json()
	items = data.get("items", [])
	next_token = data.get("next_page_token")
	if next_token:
		items = items + _fetch_galaxy_library(access_token, user_id, next_token)
	return items


def _fetch_gamesdb_entry(access_token, external_id, certificate):
	url = f"{GAMESDB_URL}/platforms/gog/external_releases/{external_id}"
	headers = {"Authorization": f"Bearer {access_token}"}
	if certificate:
		headers["X-GOG-Library-Cert"] = certificate
	response = requests.get(url, headers=headers, timeout=10)
	if response.status_code == 404:
		return None
	response.raise_for_status()
	return response.json()


def list_library():
	"""Returns [{app_name, app_title, cover_url}]. Empty list (never an
	exception) if not logged in or any request fails - same "show
	nothing rather than crash" contract as epic/legendary_backend.py's
	list_library(). gogdl has no equivalent command; this calls the same
	galaxy-library.gog.com + gamesdb.gog.com endpoints Heroic's own GOG
	library code calls, with the same headers/field names."""
	creds = _credentials()
	if not creds:
		log.debug("no GOG credentials, skipping library fetch")
		return []

	try:
		releases = _fetch_galaxy_library(creds["access_token"], creds["user_id"])
	except Exception:
		log.exception("failed to fetch GOG galaxy library")
		return []

	gog_releases = [r for r in releases if r.get("platform_id") == "gog" and r.get("owned")]
	log.debug(f"galaxy library: {len(gog_releases)} owned GOG release(s)")

	library = []
	for release in gog_releases:
		external_id = release.get("external_id")
		try:
			entry = _fetch_gamesdb_entry(creds["access_token"], external_id, release.get("certificate"))
		except Exception:
			log.exception(f"failed to fetch gamesdb entry for {external_id}, skipping")
			continue
		if not entry or entry.get("type") not in ("game", "mod"):
			continue
		game = entry.get("game") or {}
		if not game.get("visible_in_library", True):
			continue

		title = (entry.get("title") or {}).get("*") or (game.get("title") or {}).get("*") or "?"
		cover_url = _resolve_image_url((game.get("vertical_cover") or {}).get("url_format")) or _resolve_image_url(
			(game.get("logo") or {}).get("url_format")
		)

		library.append({"app_name": str(external_id), "app_title": title.strip(), "cover_url": cover_url})

	library.sort(key=lambda g: g["app_title"].lower())
	log.debug(f"library: {len(library)} game(s)")
	return library


def install(app_name, on_line):
	"""Runs `gogdl download` and streams each output line to
	on_line(line, percent_or_None) - blocking, call from a background
	thread. Installs under INSTALL_ROOT/<app_name> (gogdl needs an
	explicit --path, unlike legendary which manages its own directory)."""
	install_dir = INSTALL_ROOT / app_name
	install_dir.mkdir(parents=True, exist_ok=True)
	cmd = [GOGDL_BIN, "--auth-config-path", str(AUTH_CONFIG_PATH), "download", app_name, "--path", str(install_dir), "--platform", "windows"]
	log.info(f"installing {app_name}: {cmd}")
	proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
	for line in proc.stdout:
		line = line.rstrip("\n")
		match = PROGRESS_RE.search(line)
		percent = float(match.group(1)) if match else None
		on_line(line, percent)
	proc.wait()
	success = proc.returncode == 0
	log.info(f"install {app_name}: {'success' if success else f'failed (exit {proc.returncode})'}")
	return success


def is_installed(app_name):
	return (INSTALL_ROOT / app_name).is_dir() and any((INSTALL_ROOT / app_name).iterdir())


def find_proton():
	"""Same search as epic/legendary_backend.py's find_proton() - both
	clients launch Windows games through Steam-managed Proton builds, so
	this is deliberately identical rather than sharing a module neither
	client otherwise depends on. See that copy's own docstring for why
	the flatpak_home paths are here too - Steam itself runs as a
	system-level Flatpak now (packaging/flatpak_apps.py)."""
	flatpak_home = Path.home() / ".var" / "app" / "com.valvesoftware.Steam"
	search_dirs = [
		Path.home() / ".steam" / "steam" / "steamapps" / "common",
		Path.home() / ".local" / "share" / "Steam" / "steamapps" / "common",
		Path.home() / ".steam" / "root" / "compatibilitytools.d",
		flatpak_home / ".steam" / "steam" / "steamapps" / "common",
		flatpak_home / ".local" / "share" / "Steam" / "steamapps" / "common",
		flatpak_home / ".steam" / "root" / "compatibilitytools.d",
	]
	for search_dir in search_dirs:
		if not search_dir.is_dir():
			continue
		for candidate in sorted(search_dir.glob("*[Pp]roton*")):
			wine64 = candidate / "files" / "bin" / "wine64"
			if wine64.exists():
				log.debug(f"found Proton wine64 at {wine64}")
				return wine64
	log.warning("no Proton install found under any known Steam compatibility-tool location")
	return None


def get_launch_options(app_name):
	# interpolation=None - same reason as epic/legendary_backend.py's
	# get_launch_options(): values legitimately contain a literal "%"
	# (the %command% placeholder), which ConfigParser's default
	# interpolation raises on.
	parser = configparser.ConfigParser(interpolation=None)
	parser.read(LAUNCH_OPTIONS_PATH)
	return parser.get(app_name, "options", fallback="") if parser.has_section(app_name) else ""


def set_launch_options(app_name, options):
	parser = configparser.ConfigParser(interpolation=None)
	parser.read(LAUNCH_OPTIONS_PATH)
	if not parser.has_section(app_name):
		parser.add_section(app_name)
	parser.set(app_name, "options", options)
	with open(LAUNCH_OPTIONS_PATH, "w") as f:
		parser.write(f)
	log.info(f"saved launch options for {app_name}: {options!r}")


def _parse_launch_options(options_str):
	"""Same Steam-style syntax as epic/legendary_backend.py's parser
	(leading VAR=value env overrides, optional %command% wrapper split) -
	duplicated rather than shared since these are independent clients,
	see gog_app.py's module docstring. Unlike legendary, gogdl's `launch`
	has no way to pass extra arguments to the game executable itself
	(only --wrapper) - extra_args is still returned so callers can warn
	about anything that would silently do nothing, rather than the
	config UI and what actually happens quietly diverging."""
	if not options_str.strip():
		return {}, None, []
	try:
		tokens = shlex.split(options_str)
	except ValueError:
		log.warning(f"could not parse launch options {options_str!r} (unbalanced quotes?), ignoring")
		return {}, None, []

	env_overrides = {}
	i = 0
	while i < len(tokens) and _ENV_VAR_RE.match(tokens[i]):
		key, _, value = tokens[i].partition("=")
		env_overrides[key] = value
		i += 1
	remaining = tokens[i:]

	if "%command%" in remaining:
		idx = remaining.index("%command%")
		wrapper = " ".join(remaining[:idx]) or None
		extra_args = remaining[idx + 1 :]
	else:
		wrapper = None
		extra_args = remaining
	return env_overrides, wrapper, extra_args


def launch(app_name):
	"""Blocking - runs the game via Proton's bundled wine and waits for it
	to exit. Returns False without attempting anything if no Proton
	build is found or the game isn't installed."""
	install_dir = INSTALL_ROOT / app_name
	if not install_dir.is_dir():
		log.error(f"cannot launch {app_name}: not installed at {install_dir}")
		return False

	wine = find_proton()
	if wine is None:
		log.error(f"cannot launch {app_name}: no Proton install found")
		return False

	prefix = WINE_PREFIX_ROOT / app_name
	prefix.mkdir(parents=True, exist_ok=True)

	env_overrides, wrapper, extra_args = _parse_launch_options(get_launch_options(app_name))
	if extra_args:
		log.warning(
			f"launch options for {app_name} include extra arguments {extra_args} - "
			"gogdl's launch command has no way to pass these to the game, ignoring them"
		)

	cmd = [
		GOGDL_BIN, "--auth-config-path", str(AUTH_CONFIG_PATH), "launch",
		str(install_dir), app_name, "--platform", "windows",
		"--wine", str(wine), "--wine-prefix", str(prefix),
	]
	if wrapper:
		cmd += ["--wrapper", wrapper]

	env = {**os.environ, **env_overrides} if env_overrides else None
	log.info(f"launching {app_name}: {cmd} (env overrides: {env_overrides or 'none'})")
	result = subprocess.run(cmd, env=env)
	log.info(f"{app_name} exited (gogdl launch returncode {result.returncode})")
	return result.returncode == 0
