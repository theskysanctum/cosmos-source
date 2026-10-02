#!/usr/bin/env bash
# Installs the bundled Rajdhani font files (launcher/fonts/*.ttf) into the
# user's font directory and refreshes the fontconfig cache, if not already
# registered. Called once by launcher/run.py at startup, before the theme
# loads - a font install is a System operation, so it lives in a bash
# script rather than being done from Python, per the project's
# architecture (see the project_cosmos_architecture memory).
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
source "$SCRIPT_DIR/../debuglog.sh"

FONT_DIR="$HOME/.local/share/fonts/cosmos"

# No pipe into `grep -q` anywhere here, on purpose - confirmed by testing
# (not just a theoretical risk) that this bites twice over: grep -q exits
# the instant it finds a match, which SIGPIPEs whatever's feeding it, and
# under `pipefail` (set above) that SIGPIPE'd exit status wins over grep's
# own success. Capturing `fc-list` into a variable first only moves the
# problem to the next pipe (`echo "$var" | grep -q`) - still broken, still
# reports "not found" when it was found. Bash's own glob-match operator
# needs no pipe at all, sidestepping the whole bug class.
installed_fonts="$(fc-list)"
if [[ "$installed_fonts" == *"Rajdhani"* ]]; then
	debug_log "Rajdhani already registered, skipping install"
	exit 0
fi

debug_log "installing bundled fonts to $FONT_DIR"
mkdir -p "$FONT_DIR"
cp "$SCRIPT_DIR"/fonts/*.ttf "$FONT_DIR/"
fc-cache -f "$FONT_DIR" >/dev/null
debug_log "font cache refreshed"
