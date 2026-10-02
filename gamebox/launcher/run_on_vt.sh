#!/usr/bin/env bash
# Convenience wrapper for running the CosmOS launcher on a specific VT
# for real-hardware testing - just `./run_on_vt.sh [vt]`, no sudo needed.
# Console-mode (DRM-direct) entries land one VT over automatically (see
# run.py's own runtime_vt comment), so this only ever needs the one
# number. Games and the browser run inside the launcher's own VT/X
# session directly. Defaults to vt1 (graphical.LAUNCHER_TTY), matching
# run.py's own default when no VT is given.
#
# Doesn't need root - X gets VT/input/GPU access dynamically from
# systemd-logind, the same way any regular desktop session's own Xorg
# already does, as long as this shell's own login session is the active
# one on its seat. That's the real constraint this convenience loses by
# not requiring root any more: run this from an actual login on the VT
# you're passing in (matching startx.sh's own real boot-time usage), not
# via sudo from an unrelated desktop terminal targeting some other VT -
# logind won't grant device access for a VT this session isn't the
# active one on.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
source "$SCRIPT_DIR/../debuglog.sh"

VT="${1:-1}"

if [[ ! "$VT" =~ ^[0-9]+$ ]]; then
	echo "usage: $(basename "$0") [vt]" >&2
	echo "  vt: virtual terminal number to run the launcher on (default 1)" >&2
	exit 1
fi

debug_log "launching CosmOS on vt$VT (runtime apps -> vt$((VT + 1)))"
exec python3 "$SCRIPT_DIR/run.py" "$VT"
