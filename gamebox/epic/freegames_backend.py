#!/usr/bin/env python3
"""Wraps vogler/free-games-claimer (github.com/vogler/free-games-claimer,
AGPL-3.0 - a real, already-solved Node.js + Playwright tool) to auto-claim
Epic's weekly free games. `legendary` (this project's own Epic backend,
see legendary_backend.py) has no purchase/claim support at all - it's a
library/install/launch client, not a store front end - and Epic's real
claim flow has no clean API of its own, the same reason epic_app.py's
login screen already has to embed a real browser instead of making a
plain API call.

Kept as an unmodified, arm's-length subprocess dependency (the same
"wrap a well-tested external tool" approach as ia/yt-dlp/legendary
elsewhere in this project) rather than vendored/patched source -
installed once to its own directory outside this git repo, matching
legendary_backend.py's own WINE_PREFIX_ROOT convention.

Deliberately left on the tool's own Firefox (Playwright's private,
sandboxed build - not the system's, which this project otherwise never
touches anywhere) rather than patched onto Brave: epic-games.js
hardcodes a Firefox user-agent and stealth evasions specifically tuned
to dodge Epic's own bot detection, and Chromium-based automation is
exactly what that detection is built to catch. Patching it to launch
Brave instead would mean forking and maintaining a third party's
login/detection-evasion code with no supported upstream path to build
on, for a dependency that's otherwise entirely invisible - it never
shows a window or a URL bar anyone actually looks at, unlike Brave,
which stays the browser for everything CosmOS's own UI opens
(Web Browser, quick launch, Gaming News articles)."""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import debuglog  # noqa: E402
import paths  # noqa: E402

log = debuglog.setup("freegames_backend")

REPO_URL = "https://github.com/vogler/free-games-claimer.git"
INSTALL_DIR = paths.DATA_ROOT / "free-games-claimer"
BROWSER_DIR = paths.DATA_ROOT / "epic-freegames-browser"
DB_PATH = INSTALL_DIR / "data" / "epic-games.json"
LAST_CHECK_PATH = paths.DATA_ROOT / "epic-freegames-last-check"

# Epic's free games rotate weekly - this just needs to keep a real ~10-60s
# Playwright subprocess from re-running on every dashboard refresh/hot-
# reload within a session, not to hit some precise cadence.
CHECK_INTERVAL_S = 20 * 60 * 60
CLAIM_TIMEOUT_S = 300
LOGIN_TIMEOUT_S = 600


def is_installed():
	return (INSTALL_DIR / "node_modules").is_dir()


def ensure_installed():
	"""Clones the tool and runs its own `npm install` (which also
	downloads Playwright's managed Firefox build) - a no-op once already
	done. Blocking (a real git clone + npm install), call from a
	background thread."""
	if is_installed():
		return True
	INSTALL_DIR.parent.mkdir(parents=True, exist_ok=True)
	if not INSTALL_DIR.exists():
		log.info(f"cloning {REPO_URL} to {INSTALL_DIR}")
		result = subprocess.run(["git", "clone", "--depth", "1", REPO_URL, str(INSTALL_DIR)], capture_output=True, text=True)
		if result.returncode != 0:
			log.error(f"git clone failed: {result.stderr.strip()}")
			return False
	log.info("running npm install for free-games-claimer (also downloads Playwright's own Firefox build - can take a while)")
	result = subprocess.run(["npm", "install"], cwd=INSTALL_DIR, capture_output=True, text=True)
	if result.returncode != 0:
		log.error(f"npm install failed: {result.stderr.strip()}")
		return False
	log.info("free-games-claimer installed")
	return True


def is_logged_in():
	"""Heuristic, not a guarantee (a session can still expire server-
	side) - a persistent browser profile only exists once
	run_interactive_login() has actually completed one, so this is
	enough to decide whether there's any point trying a headless claim
	run at all."""
	return BROWSER_DIR.is_dir() and any(BROWSER_DIR.iterdir())


def _env(**overrides):
	return {**os.environ, "BROWSER_DIR": str(BROWSER_DIR), **overrides}


def run_interactive_login():
	"""Runs the real script with SHOW=1 (non-headless), so a real Firefox
	window opens on whatever DISPLAY this process already has - the
	shared X session, same as any other CosmOS window - for the user
	to log into Epic by hand just this once, solving any CAPTCHA/2FA
	themselves. What that saves to BROWSER_DIR (a real Playwright
	persistent-context browser profile) is what every later headless
	claim_available() run reuses - the same "log in once, stay logged
	in" idea as any ordinary desktop browser, no password ever stored
	by this project. Blocking and needs a real visible window - call
	from a background thread, but expect the caller to be a foreground
	flow the user just explicitly triggered, not a passive check."""
	if not ensure_installed():
		return False
	env = _env(SHOW="1", LOGIN_TIMEOUT=str(LOGIN_TIMEOUT_S // 1))
	log.info("starting interactive Epic login (SHOW=1) - opening a real browser window")
	try:
		result = subprocess.run(["node", "epic-games.js"], cwd=INSTALL_DIR, env=env, timeout=LOGIN_TIMEOUT_S + 30)
	except subprocess.TimeoutExpired:
		log.warning("interactive login timed out")
		return False
	success = result.returncode == 0
	log.info(f"interactive login: {'success' if success else f'exit code {result.returncode}'}")
	return success


def _read_claimed_titles(since_ts):
	"""The tool keeps its own JSON database of every game it's ever seen,
	per Epic account, with a status ('claimed'/'existed'/'failed'/...)
	and a local-time timestamp string - diffing against since_ts (rather
	than trusting stdout text, which is meant for a human reading a
	terminal) is what tells claim_available() what's actually new from
	this one run."""
	if not DB_PATH.exists():
		return []
	try:
		data = json.loads(DB_PATH.read_text())
	except json.JSONDecodeError:
		log.warning(f"could not parse {DB_PATH}")
		return []
	titles = []
	for games in data.values():  # one sub-dict per Epic account tracked
		for game_id, info in games.items():
			if info.get("status") != "claimed":
				continue
			try:
				# The tool's own datetime() util formats local wall-clock
				# time in this shape (see its src/util.js) - naive
				# strptime/mktime treats it as local time too, so this
				# lines up correctly on the same machine.
				claimed_at = time.mktime(time.strptime(info["time"], "%Y-%m-%d %H:%M:%S.%f"))
			except (KeyError, ValueError):
				continue
			if claimed_at >= since_ts:
				titles.append(info.get("title", game_id))
	return titles


def claim_available():
	"""Runs the real script headless - claims whatever's currently free
	that isn't already owned, silently, no browser window shown. Returns
	the list of titles newly claimed just by this run (empty if nothing
	new was free, the account isn't logged in yet, or the run failed).
	Blocking (a real ~10-60s Playwright run) - call from a background
	thread."""
	if not is_logged_in():
		log.debug("not logged in yet (no saved browser session), skipping claim check")
		return []
	if not ensure_installed():
		return []
	# A few seconds of slack against the local-time round-tripping in
	# _read_claimed_titles() above, rather than relying on exact
	# millisecond alignment between this process's clock read and the
	# tool's own timestamp formatting.
	started_at = time.time() - 5
	log.info("checking for claimable free games")
	try:
		result = subprocess.run(
			["node", "epic-games.js"], cwd=INSTALL_DIR, env=_env(), capture_output=True, text=True, timeout=CLAIM_TIMEOUT_S
		)
	except subprocess.TimeoutExpired:
		log.warning("claim run timed out")
		return []
	if result.returncode != 0:
		log.warning(f"claim run exited {result.returncode}: {result.stderr.strip()[-500:]}")
	titles = _read_claimed_titles(started_at)
	if titles:
		log.info(f"claimed: {titles}")
	else:
		log.debug("nothing new to claim")
	return titles


def _check_due():
	if not LAST_CHECK_PATH.exists():
		return True
	try:
		last = float(LAST_CHECK_PATH.read_text().strip())
	except ValueError:
		return True
	return (time.time() - last) >= CHECK_INTERVAL_S


def _mark_checked():
	LAST_CHECK_PATH.parent.mkdir(parents=True, exist_ok=True)
	LAST_CHECK_PATH.write_text(str(time.time()))


def claim_if_due():
	"""The one entry point a passive background check should call - cheap
	(just directory/file-mtime checks) and a no-op until
	run_interactive_login() has ever completed once, so it's safe to
	call on every launcher session (or even every dashboard refresh)
	without worrying about hammering Epic's servers or spawning
	needless subprocesses on a system that's never opted in."""
	if not is_logged_in():
		return []
	if not _check_due():
		return []
	titles = claim_available()
	_mark_checked()
	return titles
