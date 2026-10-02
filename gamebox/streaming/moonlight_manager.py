#!/usr/bin/env python3
"""Join side of CosmOS's "Play Together" system (see streaming/sunshine_manager.py
for the host side) - thin command-builder/output-parser wrapper around
streaming/bin/moonlight (a from-source build of moonlight-embedded, chosen over
moonlight-qt specifically to avoid pulling in Qt, matching this project's existing
no-Qt builds of RetroArch/Dolphin).

Deliberately just builds argv lists and parses plain-text output rather than
running subprocesses itself - app.py already has real machinery for that
(run_async for a single result, run_streamed for a pairing session's live PIN
output, graphical.run_graphical() for the actual fullscreen stream view), so this
module stays plain and testable instead of duplicating any of it."""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import debuglog  # noqa: E402
import paths  # noqa: E402

log = debuglog.setup("streaming.moonlight_manager")

REPO_ROOT = Path(__file__).resolve().parent.parent
BIN_PATH = REPO_ROOT / "streaming" / "bin" / "moonlight"

STATE_DIR = paths.DATA_ROOT / "streaming" / "moonlight"
CONFIG_PATH = STATE_DIR / "moonlight.conf"

# Confirmed against moonlight-embedded's own src/main.c: pairing prints
# this exact line once it's generated a PIN, and applist() prints
# "%d. %s" per app - both parsed here rather than guessed.
PIN_LINE_RE = re.compile(r"PIN on the target PC:\s*(\d{4})")
APP_LIST_LINE_RE = re.compile(r"^\d+\.\s*(.+)$")


def ensure_state_dir():
	"""moonlight's own `-config` flag only *loads* a file (confirmed
	live: it errors out - "Can't open configuration file" - rather than
	creating one), unlike Sunshine's own config handling, so a real,
	if empty (comments-only, same shape as its own bundled
	moonlight.conf template), file has to exist here before it's ever
	passed on the command line."""
	STATE_DIR.mkdir(parents=True, exist_ok=True)
	if not CONFIG_PATH.exists():
		CONFIG_PATH.write_text("# CosmOS-managed moonlight-embedded config\n")


def pair_command(host, device_name="CosmOS"):
	ensure_state_dir()
	return [str(BIN_PATH), "pair", "-config", str(CONFIG_PATH), host]


def parse_pin(line):
	"""Returns the 4-digit PIN moonlight just generated for a pair
	action, or None if this particular line doesn't contain one - call
	once per line while streaming a pair_command()'s output (see
	app.py's run_streamed)."""
	m = PIN_LINE_RE.search(line)
	return m.group(1) if m else None


def list_apps_command(host):
	ensure_state_dir()
	return [str(BIN_PATH), "list", "-config", str(CONFIG_PATH), host]


def parse_app_list(output):
	names = []
	for line in output.splitlines():
		m = APP_LIST_LINE_RE.match(line.strip())
		if m:
			names.append(m.group(1))
	return names


def stream_command(host, app="CosmOS"):
	"""A blocking, fullscreen-in-CosmOS's-own-X-session command - run it
	via graphical.run_graphical(), the same way library.py launches every
	emulator, not run_async/run_streamed (those are for background work,
	not a foreground window)."""
	ensure_state_dir()
	return [
		str(BIN_PATH), "stream",
		"-config", str(CONFIG_PATH),
		"-app", app,
		"-platform", "x11",
		"-1080",
		host,
	]


def unpair_command(host):
	ensure_state_dir()
	return [str(BIN_PATH), "unpair", "-config", str(CONFIG_PATH), host]
