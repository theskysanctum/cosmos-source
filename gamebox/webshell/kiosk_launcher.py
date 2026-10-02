#!/usr/bin/env python3
"""Launches a single URL fullscreen, no chrome, via the Electron webshell
app (main.js) - replaces the old browser/kiosk_launcher.py (which
imported a `brave_launcher.PROFILE_DIR` name that never existed there
and called two of its functions with the wrong arity - broken as
written, confirmed directly, not modeled on here). Used by the main
menu's quick-launch row (user-added website shortcuts) and Gaming News
article links - both still just do
`launch_subprocess({"mode": "graphical", "command": ["python3", KIOSK_LAUNCHER_PATH, url]})`
in launcher/app.py, now pointed at this file instead.

Run directly: `python3 webshell/kiosk_launcher.py <url>`.

None of the old file's Brave-profile-JSON-fighting logic
(ensure_seeded_profile's Widevine Local State seeding,
force_profile_preferences's window_placement/exit_type/theme/
restore_on_startup/QUIET_BRAVE_PREFS patching) has any analog here -
main.js controls its own window (kiosk mode, explicit userData path)
directly instead of fighting a general browser's own persisted profile
state, and DRM no longer matters for this project at all (Widevine was
the entire reason Brave was chosen originally).

Verified for real (a real WM session, xdotool/xprop/a real SIGKILL-the-
process-group exit against real running instances - not assumed from
Electron's own docs), same standard brave_launcher.py's own docstring
held itself to: WM_CLASS comes back exactly "cosmos-webshell";
PID-based window lookup finds the real window directly (unlike
Chromium, no fork/daemonize disconnect); `kiosk: true` alone fills the
real screen with no forced resize; the cursor-hiding CSS/JS shows no
visible flash; a `target=_blank` link is cleanly denied, no orphaned
second window; a process-group SIGKILL leaves zero orphaned GPU/
renderer/zygote/network children. One real bug found and fixed in the
process: Chromium's own cookie store batches writes to disk rather than
flushing per-set, so a login cookie set on a quick-launch site could be
silently lost to this project's own abrupt-kill exit model before ever
reaching disk - main.js now explicitly flushes the cookie store on
every dom-ready plus a periodic timer (see its own comment), confirmed
live to survive a real kill-and-relaunch round trip. Still not checked
against the real target console-OS session/WM specifically (this was
run against a real MATE/Marco desktop and a bare headless Xvfb with no
WM) - if window sizing/lookup ever misbehaves there, see run_graphical's
own call below for what to add back."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "launcher"))
import graphical  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "input"))
import stick_mode  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import debuglog  # noqa: E402
import paths  # noqa: E402

import electron_runtime  # noqa: E402

log = debuglog.setup("webshell.kiosk_launcher")

# App code (main.js/preload.js/cursor.css/js/package.json/the staged
# Electron binary) - __file__-relative, moves with the swappable
# APP_ROOT tree (see packaging/cosmos_install.py) like the rest of
# CosmOS's app code.
WEBSHELL_APP_DIR = Path(__file__).resolve().parent
ELECTRON_BIN = electron_runtime.ELECTRON_BIN

# A single shared profile (paths.DATA_ROOT-relative, NOT __file__-
# relative - see main.js's own comment on why: APP_ROOT is replaced
# wholesale on every update, profile data living there would be lost),
# not one per CosmOS profile - same reasoning
# brave_launcher.py's own BRAVE_HOME used (a quick-launch site's login
# session should persist across separate opens, and across the
# quick-launch tile / Gaming News article paths both using this same
# launcher).
PROFILE_DIR = paths.DATA_ROOT / "webshell" / "profile"


def main():
	if len(sys.argv) < 2:
		log.error("usage: kiosk_launcher.py <url>")
		sys.exit(1)
	url = sys.argv[1]

	if not ELECTRON_BIN.exists():
		log.error(f"Electron runtime not found at {ELECTRON_BIN} - run Deploy GUI's dependency-install step first")
		return

	log.info(f"starting webshell for {url}")
	PROFILE_DIR.mkdir(parents=True, exist_ok=True)
	stick_mode.set_mouse_mode()
	log.debug("stick mode set to mouse for this session")
	try:
		graphical.run_graphical(
			[
				str(ELECTRON_BIN),
				str(WEBSHELL_APP_DIR),
				f"--url={url}",
				f"--user-data-dir={PROFILE_DIR}",
			],
			# Deliberately NOT passing window_geometry/window_class/
			# blank_cursor_on_map - confirmed live (a real WM session,
			# xdotool/xprop against a real running instance, before this
			# was ever trusted) that none of the three are actually
			# needed the way they were for Brave: unlike Chromium,
			# Electron's own subprocess.Popen-reported pid DOES own its
			# real mapped window (find_window_for_pid works directly, no
			# timeout, despite Electron also forking a separate GPU/
			# renderer process tree - the fork/daemonize disconnect
			# find_window_for_pid's own docstring documents for Chromium
			# just doesn't happen here); `kiosk: true` alone reliably
			# filled the real screen (confirmed via xprop:
			# _NET_WM_STATE_FULLSCREEN, geometry exactly matching
			# get_display_geometry()) with no forced resize needed; and
			# the cursor-hiding CSS/JS main.js injects on every dom-ready
			# showed no visible flash needing blank_cursor_on_map's
			# X11-level one-shot fix. If a real console-OS session (a
			# different WM/compositor than what this was checked
			# against) ever shows otherwise, re-add whichever of the
			# three is actually needed - don't restore all three
			# preemptively.
		)
	finally:
		stick_mode.set_navigate_mode()
		log.debug("stick mode reverted to navigate")


if __name__ == "__main__":
	main()
