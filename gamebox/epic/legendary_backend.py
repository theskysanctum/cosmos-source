#!/usr/bin/env python3
"""Wraps the `legendary` CLI (github.com/derrod/legendary, MIT-licensed
open-source Epic Games Store client) for the standalone Epic client - a
subprocess wrapper rather than importing legendary as a library, matching
this project's existing style of shelling out to well-tested CLIs
(nmcli, bluetoothctl, xrandr) rather than depending on their internals.

Epic's own login has no CLI-only flow (a captcha makes that impossible,
per legendary's own source comments) - epic_app.py's login screen embeds
Epic's real login page in a WebKit2 view and captures the resulting
exchange code/session id automatically via injected JS, the same
mechanism legendary's own pywebview-based login and the real Epic Games
Launcher both use. auth_via_exchange_token()/auth_via_sid() below are
what that screen calls once it has one."""
import configparser
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import debuglog  # noqa: E402
import paths  # noqa: E402

log = debuglog.setup("legendary_backend")

LEGENDARY_BIN = "legendary"
PROGRESS_RE = re.compile(r"Progress:\s*([\d.]+)%")

WINE_PREFIX_ROOT = paths.DATA_ROOT / "epic-prefixes"
LAUNCH_OPTIONS_PATH = Path(__file__).parent / "launch_options.ini"
_ENV_VAR_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


def _run(args):
	cmd = [LEGENDARY_BIN, *args]
	log.debug(f"running: {cmd}")
	return subprocess.run(cmd, capture_output=True, text=True)


def is_authenticated():
	result = _run(["status", "--offline", "--json"])
	if result.returncode != 0:
		log.warning(f"legendary status failed: {result.stderr.strip()}")
		return False, None
	try:
		data = json.loads(result.stdout)
	except json.JSONDecodeError:
		log.warning(f"could not parse legendary status output: {result.stdout!r}")
		return False, None
	account = data.get("account", "")
	authenticated = bool(account) and account != "<not logged in>"
	log.debug(f"authenticated={authenticated} account={account if authenticated else '<none>'}")
	return authenticated, (account if authenticated else None)


def auth_via_exchange_token(exchange_code):
	"""Completes login using the exchange code Epic's own login page hands
	back automatically to an embedded webview via its window.ue.
	signinprompt.requestexchangecodesignin(...) JS callback (see
	epic_app.py's login screen) - the same mechanism legendary's own
	pywebview-based login and the real Epic Games Launcher both use.
	This is the primary, fully-automated login path - there is no manual
	copy-paste step; a captcha and Epic's own anti-automation checks make
	a plain non-webview CLI flow unreliable in practice."""
	result = _run(["auth", "--token", exchange_code])
	success = result.returncode == 0
	log.info(f"login via exchange token: {'success' if success else 'failed'}")
	if not success:
		log.warning(f"legendary auth stderr: {result.stderr.strip()}")
	return success


def auth_via_sid(sid):
	"""Fallback path for when Epic's page triggers registersignincomplete
	callback instead of the direct exchange-code callback - see
	epic_app.py's login screen for how the SID is fetched via an
	additional in-page XHR once this fires."""
	result = _run(["auth", "--sid", sid])
	success = result.returncode == 0
	log.info(f"login via SID: {'success' if success else 'failed'}")
	if not success:
		log.warning(f"legendary auth stderr: {result.stderr.strip()}")
	return success


def logout():
	result = _run(["auth", "--delete"])
	log.info(f"logout: {'success' if result.returncode == 0 else 'failed'}")
	return result.returncode == 0


# Epic's catalog metadata includes a "keyImages" list of {type, url}
# entries - legendary passes this through unmodified (it doesn't parse
# cover art itself), so this picks the first available image whose type
# looks like real cover/box art, falling back to whatever image exists
# rather than assuming a specific type is always present.
_COVER_TYPE_PREFERENCE = ("DieselGameBoxTall", "Thumbnail", "DieselGameBox", "OfferImageTall")


def _cover_url(metadata):
	images = (metadata or {}).get("keyImages") or []
	by_type = {img.get("type"): img.get("url") for img in images if img.get("url")}
	for wanted in _COVER_TYPE_PREFERENCE:
		if wanted in by_type:
			return by_type[wanted]
	return next(iter(by_type.values()), None)


def list_library():
	"""Returns [{app_name, app_title, cover_url}]. Empty list (never an
	exception) if not logged in or the request fails - callers should
	treat that as "show nothing", same as retroachievements.py's
	fetch_summary() treats a failed fetch as None rather than crashing."""
	result = _run(["list", "--json"])
	if result.returncode != 0:
		log.warning(f"legendary list failed: {result.stderr.strip()}")
		return []
	try:
		games = json.loads(result.stdout)
	except json.JSONDecodeError:
		log.warning("could not parse legendary list output")
		return []
	library = [
		{
			"app_name": g.get("app_name", ""),
			"app_title": g.get("app_title", "?"),
			"cover_url": _cover_url(g.get("metadata")),
		}
		for g in games
	]
	log.debug(f"library: {len(library)} game(s)")
	return library


def list_installed():
	result = _run(["list-installed", "--json"])
	if result.returncode != 0:
		log.warning(f"legendary list-installed failed: {result.stderr.strip()}")
		return set()
	try:
		games = json.loads(result.stdout)
	except json.JSONDecodeError:
		return set()
	return {g.get("app_name") for g in games if g.get("app_name")}


def install(app_name, on_line):
	"""Runs `legendary install` and streams each output line to
	on_line(line, percent_or_None) as it happens - install is a slow,
	real-time operation, not something to run silently and report only
	at the end. Blocking; call from a background thread."""
	cmd = [LEGENDARY_BIN, "-y", "install", app_name]
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


def find_proton():
	"""Looks for a Proton build under Steam's usual locations (both the
	compatibilitytools.d path for custom/GE-Proton builds, and Proton
	versions Steam itself downloaded as a regular "app") and returns the
	wine64 binary inside it - that's what legendary's --wine flag
	actually wants, not the top-level `proton` script (that's meant to be
	driven by Steam itself with STEAM_COMPAT_* env vars this doesn't set
	up). None if nothing is found yet - e.g. Steam is installed but no
	compatibility tool has been downloaded through it.

	Steam itself runs as a real, system-level Flatpak now (see
	packaging/flatpak_apps.py), launched with no HOME override - Flatpak
	remaps $HOME as Steam itself sees it to ~/.var/app/<app-id>, so
	anything Steam writes to what it thinks is ~/.steam/... or
	~/.local/share/Steam/... really lands under that real, unsandboxed
	path instead. Checked alongside the plain (real-system-Steam)
	locations, not instead of them, so this still works either way."""
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
	# interpolation=None: options legitimately contain literal "%" (the
	# %command% placeholder) - ConfigParser's default interpolation
	# treats "%" as its own %(name)s syntax and raises on anything else,
	# confirmed by an earlier test crashing on exactly this value.
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
	"""Steam-style launch options string: leading VAR=value tokens become
	environment overrides; an optional %command% placeholder marks where
	the real launch invocation goes - anything before it (that isn't an
	env var) becomes a wrapper command (e.g. "gamemoderun %command%"),
	anything after becomes extra arguments passed to the game itself. No
	%command%? The whole remainder is just extra game arguments (e.g.
	"-windowed -novideo"), the common case. Returns (env_overrides dict,
	wrapper str-or-None, extra_args list) - never raises; a malformed
	string just yields no overrides/wrapper/args rather than blocking a
	launch over a typo."""
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
	to exit, the same "block until the game is done" shape
	graphical.run_graphical uses for everything else. Returns
	False without attempting anything if no Proton build is found -
	Epic's library is overwhelmingly Windows-only titles, so there's
	nothing useful to do without one."""
	wine = find_proton()
	if wine is None:
		log.error(f"cannot launch {app_name}: no Proton install found")
		return False

	prefix = WINE_PREFIX_ROOT / app_name
	prefix.mkdir(parents=True, exist_ok=True)

	env_overrides, wrapper, extra_args = _parse_launch_options(get_launch_options(app_name))

	cmd = [LEGENDARY_BIN, "launch", app_name, "--wine", str(wine), "--wine-prefix", str(prefix)]
	if wrapper:
		cmd += ["--wrapper", wrapper]
	cmd += extra_args

	env = {**os.environ, **env_overrides} if env_overrides else None
	log.info(f"launching {app_name}: {cmd} (env overrides: {env_overrides or 'none'})")
	result = subprocess.run(cmd, env=env)
	log.info(f"{app_name} exited (legendary launch returncode {result.returncode})")
	return result.returncode == 0
