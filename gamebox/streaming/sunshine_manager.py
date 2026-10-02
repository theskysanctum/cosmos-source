#!/usr/bin/env python3
"""Host side of CosmOS's "Play Together" system (see streaming/moonlight_manager.py
for the join/client side) - a from-source build of Sunshine (LizardByte/Sunshine),
paired with moonlight-embedded as the client, chosen over building native netplay
into RetroArch/Dolphin/PCSX2 individually (RetroArch has real netplay, Dolphin's
netplay is Qt-GUI-only with no CLI hook, PCSX2 has none at all upstream) - streaming
the host's whole screen+input works identically across all three with no per-
emulator code, per direct request.

Sunshine itself (streaming/bin/sunshine, built with SUNSHINE_ENABLE_TRAY=OFF so it
never pulls in Qt) is a background daemon, not a foreground fullscreen app the way
emulators are - started on demand when the user opens the "Host" side of the Play
Together screen (see app.py), not an always-on systemd service, matching this
project's "nothing running when the console isn't actually doing that thing"
approach elsewhere (e.g. cosmochat's own listener only runs when that widget is
active).

apps.json is deliberately kept to a single "CosmOS" entry with no `cmd` at all
(Sunshine's own default "Desktop" app shape) - it just mirrors whatever's already
on screen rather than launching anything itself, so a guest joins whatever the
host happens to be doing (RetroArch, Dolphin, PCSX2, or anything else) without
CosmOS needing a separate apps.json entry per emulator."""
import configparser
import json
import subprocess
import sys
import time
from pathlib import Path

import requests
from requests.auth import HTTPBasicAuth

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import debuglog  # noqa: E402
import paths  # noqa: E402

log = debuglog.setup("streaming.sunshine_manager")

REPO_ROOT = Path(__file__).resolve().parent.parent
BIN_PATH = REPO_ROOT / "streaming" / "bin" / "sunshine"
ASSETS_DIR = REPO_ROOT / "streaming" / "bin" / "assets"

STATE_DIR = paths.DATA_ROOT / "streaming" / "sunshine"
CONFIG_PATH = STATE_DIR / "sunshine.conf"
APPS_PATH = STATE_DIR / "apps.json"
CREDS_PATH = STATE_DIR / "sunshine_credentials.json"
API_AUTH_PATH = STATE_DIR / "api_auth.json"
PID_PATH = STATE_DIR / "sunshine.pid"
LOG_PATH = STATE_DIR / "sunshine.log"

# Sunshine's own well-known default port scheme (base port + fixed offsets,
# confirmed against src/config.cpp) - HTTPS web/REST API lives at base+1.
BASE_PORT = 47989
API_PORT = BASE_PORT + 1
API_BASE = f"https://127.0.0.1:{API_PORT}"
REQUEST_TIMEOUT_S = 8

DEFAULT_APPS = {
	"env": {"PATH": "$(PATH):$(HOME)/.local/bin"},
	"apps": [
		{"name": "CosmOS", "image-path": str(ASSETS_DIR / "cosmos.png")},
	],
}


def ensure_config():
	"""Writes sunshine.conf/apps.json fresh on every call (cheap - a
	handful of lines, same reasoning retroarch_config.py's own
	ensure_directories_cfg() already uses) - real per-install state
	(pairing keys, paired-client list, the admin credentials file) all
	live under STATE_DIR untouched by this, only the *paths* pointing at
	them are rewritten, so nothing here can go stale if the repo ever
	moves."""
	STATE_DIR.mkdir(parents=True, exist_ok=True)
	parser = configparser.ConfigParser()
	parser.optionxform = str
	config = {
		"sunshine_name": "CosmOS",
		"port": str(BASE_PORT),
		"pkey": str(STATE_DIR / "sunshine.key"),
		"cert": str(STATE_DIR / "sunshine.cert"),
		"log_path": str(LOG_PATH),
		"file_state": str(STATE_DIR / "sunshine_state.json"),
		"credentials_file": str(CREDS_PATH),
		"file_apps": str(APPS_PATH),
		"origin_web_ui_allowed": "lan",
	}
	with open(CONFIG_PATH, "w") as f:
		for key, value in config.items():
			f.write(f"{key} = {value}\n")

	if not APPS_PATH.exists():
		APPS_PATH.write_text(json.dumps(DEFAULT_APPS, indent=2))
	log.debug(f"wrote {CONFIG_PATH} and ensured {APPS_PATH}")


def has_credentials():
	return CREDS_PATH.exists()


def set_credentials(username, password):
	"""One-time (or replaceable) admin login for Sunshine's own REST API/
	web UI - distinct from a *paired device*'s PIN, this is what CosmOS's
	own code authenticates with to drive that API. Runs the real
	sunshine binary as a one-shot CLI action (its own `--creds` flag),
	not something this module reimplements."""
	ensure_config()
	log.info("set_credentials: setting Sunshine admin credentials")
	result = subprocess.run(
		[str(BIN_PATH), str(CONFIG_PATH), "--creds", username, password],
		capture_output=True, text=True, timeout=REQUEST_TIMEOUT_S,
	)
	if result.returncode != 0:
		log.error(f"set_credentials failed: {result.stderr.strip()}")
		return False, (username, password)
	API_AUTH_PATH.write_text(json.dumps({"username": username, "password": password}))
	return True, (username, password)


def ensure_bootstrapped():
	"""Generates a random, CosmOS-only admin login the very first time
	the Host screen is opened (see app.py) - this is an internal loopback
	credential for CosmOS's own code to drive Sunshine's REST API with,
	never shown to or typed by the person using the console (unlike a
	*paired device*'s PIN, which is - see submit_pin()), so there's
	nothing here for a user to configure. Returns the (username,
	password) tuple to authenticate with, generating+saving it only if
	API_AUTH_PATH doesn't already exist."""
	if API_AUTH_PATH.exists():
		saved = json.loads(API_AUTH_PATH.read_text())
		return saved["username"], saved["password"]
	import secrets
	username = "cosmos"
	password = secrets.token_urlsafe(24)
	ok, auth = set_credentials(username, password)
	log.info(f"ensure_bootstrapped: generated new Sunshine admin credentials (ok={ok})")
	return auth


_firewall = None


def _door(open_):
	"""Open or close the firewall door for hosting (CosmOS's firewall - config/firewalld/ - rejects everything from the network except named
	doors; this one is temporary: running firewall only, with an automatic expiry). A no-op where there is no such firewall module, e.g.
	GameBox on its own. Failures are logged, never raised: hosting still starts, guests just can't get in."""
	global _firewall
	if _firewall is None:
		try:
			for folder in ("/usr/lib/cosmos", "/usr/local/bin"):     # the cosmos-* packages, and the image's old location
				if folder not in sys.path:
					sys.path.append(folder)
			import cosmos_firewall
			_firewall = cosmos_firewall
		except ImportError:
			_firewall = False
	if not _firewall:
		return
	try:
		ok, message = _firewall.set_door("play-together", open_)
	except Exception:                                    # a broken firewall helper must never stop the game
		log.warning("firewall door change raised", exc_info=True)
		return
	if not ok:
		log.warning(f"firewall door for hosting could not be {'opened' if open_ else 'closed'}: {message}")


def is_running():
	return _read_pid() is not None


def _read_pid():
	if not PID_PATH.exists():
		return None
	try:
		pid = int(PID_PATH.read_text().strip())
	except ValueError:
		return None
	try:
		import os
		os.kill(pid, 0)
	except (ProcessLookupError, PermissionError):
		return None
	return pid


def start():
	"""Launches sunshine as a detached background daemon (not a fullscreen
	app - graphical.run_graphical() is for that; this stays behind the
	current CosmOS screen entirely)."""
	if is_running():
		log.debug("start: already running")
		_door(True)                                      # renews the door if its timeout ran out
		return True
	ensure_config()
	log.info(f"start: launching {BIN_PATH}")
	proc = subprocess.Popen(
		[str(BIN_PATH), str(CONFIG_PATH)],
		stdout=open(LOG_PATH, "a"), stderr=subprocess.STDOUT,
		start_new_session=True,
	)
	PID_PATH.write_text(str(proc.pid))
	time.sleep(1.5)  # give the HTTPS listener a moment to come up before any API call
	running = is_running()
	if running:
		_door(True)
	return running


def stop():
	_door(False)                                         # close it first, whatever state sunshine is in
	pid = _read_pid()
	if pid is None:
		return
	log.info(f"stop: killing sunshine pid={pid}")
	import os
	import signal
	try:
		os.kill(pid, signal.SIGTERM)
	except ProcessLookupError:
		pass
	PID_PATH.unlink(missing_ok=True)


def _api(method, path, auth, **kwargs):
	url = f"{API_BASE}{path}"
	try:
		response = requests.request(method, url, auth=auth, verify=False, timeout=REQUEST_TIMEOUT_S, **kwargs)
		response.raise_for_status()
		return True, response.json() if response.content else {}
	except Exception:
		log.exception(f"Sunshine API call failed: {method} {path}")
		return False, {}


def list_pending_pairings(auth):
	basic = HTTPBasicAuth(*auth)
	ok, data = _api("GET", "/api/pin", basic)
	return data.get("pairings", []) if ok else []


def submit_pin(pin, device_name, auth):
	"""Confirms a pairing request a joining Moonlight client already
	started (see moonlight_manager.pair()) - `auth` is (username,
	password) from set_credentials()/has_credentials().

	Sunshine's own /api/pin POST requires a `pairing_id` identifying
	*which* pending request this PIN belongs to (confirmed against its
	own confighttp.cpp - a bare pin+name isn't enough), fetched here via
	a GET first rather than pushed onto every caller - there's normally
	only one pending request at a time in CosmOS's own flow (one Join
	screen, one in-flight pair attempt), so the most recent one is used
	if there's ever more than one."""
	basic = HTTPBasicAuth(*auth)
	pending = list_pending_pairings(auth)
	if not pending:
		log.warning("submit_pin: no pending pairing request found")
		return False
	pairing_id = pending[-1]["id"]
	ok, data = _api("POST", "/api/pin", basic, json={"pairing_id": pairing_id, "pin": str(pin), "name": device_name})
	log.info(f"submit_pin: {'accepted' if ok else 'failed'}")
	return ok


def list_clients(auth):
	basic = HTTPBasicAuth(*auth)
	ok, data = _api("GET", "/api/clients/list", basic)
	return data.get("named_certs", []) if ok else []


def unpair_client(uuid, auth):
	basic = HTTPBasicAuth(*auth)
	ok, _ = _api("POST", "/api/clients/unpair", basic, json={"uuid": uuid})
	return ok


def unpair_all(auth):
	basic = HTTPBasicAuth(*auth)
	ok, _ = _api("POST", "/api/clients/unpair-all", basic)
	return ok
