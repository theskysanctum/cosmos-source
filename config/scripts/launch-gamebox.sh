#!/bin/bash
# GameBox, started from the CosmOS desktop: runs GameBox - the copy baked into the image
# (gamebox-dir prepares the writable copy) - through gamebox-nested - the
# launcher inside a full-screen Xephyr, shown by cosmos-wm's XWayland shim (see that
# script for why nested). No install step (packaging/cosmos_install.py +
# stage_build.sh, which swaps a build into the fixed /cosmos/app/current path for a
# real deployed machine) - COSMOS_PACKAGED stays unset, so hot-reload stays on and
# this is the actual live checkout, edits and all, not a frozen build.
#
# A plain `git clone` alone is NOT enough for a fully-working GameBox, confirmed by
# reading its own code: RetroArch/dolphin-emu-nogui/pcsx2-qt/mkxp-z are committed
# straight into the repo so those work immediately, but libretro cores, the non-RGSS
# game engines (Ren'Py/Love2D/TIC-80/AGS/NW.js), misc assets, and the webshell/
# browser's own Electron runtime are all deliberately fetched at deploy time instead
# (normally via packaging/deploy_gui/deploy_gui.py's "Install deps" button).
# Deliberately NOT run automatically here, direct request - fetch_all_cores() alone
# hits the real libretro buildbot over the network just to list what's available,
# every single call, not a free local check - run `gamebox-fetch-assets` yourself
# (same PATH, see config/scripts/gamebox-fetch-assets.py) whenever you actually want
# that, instead of it firing on every launch.
#
# While GameBox runs, cosmos-bar and the launcher hide (see cosmos_session_state.py)
# so it has the whole screen - which is also why there is no tray icon to close it
# with any more: GameBox has its own exit.
set -euo pipefail

# GameBox's desktop entry runs this with no terminal (Terminal=false), so
# anything only printed to stderr would vanish silently - every failure
# path below also goes through notify-send (dunst, already installed) so
# a click on the icon never just does nothing.
notify_fail() {
	notify-send -u critical -i applications-games "GameBox" "$1" 2>/dev/null || true
}

# cosmos-bar hides itself for as long as this file names a live process (see
# cosmos-bar.py's hide_requested()) and comes back the moment GameBox closes -
# or, if this script were ever killed outright, as soon as its PID is gone.
BAR_HIDE_FILE="${XDG_RUNTIME_DIR:-/tmp}/cosmos-bar-hidden-by"
echo "$$" > "$BAR_HIDE_FILE"

trap '[ "$(cat "$BAR_HIDE_FILE" 2>/dev/null)" = "$$" ] && rm -f "$BAR_HIDE_FILE"' EXIT

# Give the bar and launcher a moment to notice the request and unmap (they poll twice
# a second) so the compositor has already given the screen back before Xephyr is
# sized to it.
sleep 1

# Everything GameBox prints is kept in a log (the last run only), and a failure popup says where and quotes the last line, so "GameBox exited
# unexpectedly" is never all there is to go on.
LOG_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/cosmos"
LOG="$LOG_DIR/gamebox-last.log"
mkdir -p "$LOG_DIR" 2>/dev/null || true
set +e
/usr/local/bin/gamebox-nested 2>&1 | tee "$LOG" >/dev/null
rc=${PIPESTATUS[0]}
set -e
last=$(grep -vE '^\s*(File |~|\^)|DEBUG|INFO' "$LOG" 2>/dev/null | grep -v '^\s*$' | tail -n 1 | cut -c1-200)
case "$rc" in
	0|143|137) ;;
	3) notify_fail "GameBox files not found - this image should carry them at /usr/share/gamebox (or set GAMEBOX_REPO_DIR to another copy)." ;;
	4) notify_fail "Xephyr isn't installed." ;;
	*) notify_fail "GameBox exited unexpectedly (code $rc): ${last:-no message}. Full log: $LOG" ;;
esac
