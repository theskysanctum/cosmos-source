#!/usr/bin/env bash
# The real boot-time entry point for real console-os hardware: exec this
# from whatever gets a login shell running on the target VT at boot (e.g.
# a regular user's own .bash_profile, via a getty auto-login) and it
# starts CosmOS on that VT, no arguments needed. The classic `startx`
# pattern - a shell on a VT execs this in place of itself, same as a
# desktop distro's .xinitrc would, except this VT number is detected
# rather than fixed, since nothing hands it one at boot.
#
# This script itself is deliberately NOT part of the swappable app tree
# under APP_ROOT - it's the stable boot supervisor that stays put across
# app updates and always execs whatever's currently installed there (see
# packaging/cosmos_install.py's own APP_ROOT - a fixed path that
# "current" always means, regardless of which build is actually there).
# CosmOS is no longer packaged as an AppImage at all (per direct
# request) - APP_ROOT is now just a plain, real source tree (see
# packaging/stage_build.sh), execed directly with python3 below rather
# than through an AppImage's own AppRun shim. Editing/updating CosmOS
# from here on means staging a fresh build and running install()/update()
# against it (see packaging/cosmos_install.py), not editing files
# under this repo checkout directly - those no longer run live once this
# points at APP_ROOT.
#
# Deliberately does NOT need root: X gets VT/input/GPU access dynamically
# from systemd-logind (the same device-ACL mechanism any regular desktop
# session's own Xorg already relies on, not a setuid Xorg binary) as long
# as this login session is the active one on its seat - confirmed
# directly on this project's own dev machine. There used to be one real
# exception (a setuid-root VT-switch helper, for DRM-direct entries that
# ran outside this X session entirely) - gone now that every real entry
# runs graphical, inside this one X session; nothing here needs any
# privileged VT-switching capability anymore.
#
# launcher/run_on_vt.sh is the other half of this: a manual, "sudo
# ./run_on_vt.sh [vt]" convenience for testing on a specific VT without
# disturbing a real desktop session already running on the default one -
# this script has no such use case (it only ever runs as whatever VT it's
# already on) and isn't meant to be invoked by hand.
#
# Respawns CosmOS if it ever exits (a crash, most likely) rather than
# dropping the VT back to a bare shell - this script is the thing
# staying alive as the supervisor, so it doesn't exec it directly.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
source "$SCRIPT_DIR/../debuglog.sh"

APP_ROOT="/cosmos/app/current"

# `tty` (not fgconsole - no extra package needed) reports this shell's own
# controlling terminal, which is exactly the VT a getty-spawned login
# shell is actually running on - not merely "whichever VT happens to be
# active right now", though the two always coincide in this boot flow.
TTY_PATH="$(tty)" || {
	echo "startx: not running on a real tty (got: ${TTY_PATH:-<none>}) - can't detect which VT to use" >&2
	exit 1
}
VT="${TTY_PATH##*tty}"
if [[ ! "$VT" =~ ^[0-9]+$ ]]; then
	echo "startx: couldn't parse a VT number out of controlling terminal '$TTY_PATH'" >&2
	exit 1
fi

# Once per real boot (not once per respawn below) - safe either way,
# since grow_userdata.sh does its own real disk-state check every time
# rather than assuming from history (see its own docstring), but there's
# no reason to re-check on every single CosmOS respawn. `|| true` on
# the whole pipeline: a growth failure is a real problem worth logging,
# but never a reason to keep CosmOS itself from starting.
debug_log "checking whether userdata needs to grow to fill this disk"
"$SCRIPT_DIR/../image/grow_userdata.sh" 2>&1 | while IFS= read -r line; do debug_log "$line"; done || true

# Below a real session's runtime, treated as evidence CosmOS is
# failing immediately (a startup bug, not a one-off crash after actually
# running a while) - respawning instantly in that case would just spin
# CPU and spam the log forever, so back off briefly instead.
MIN_HEALTHY_RUNTIME_S=5
RESPAWN_BACKOFF_S=3

while true; do
	debug_log "launching CosmOS on vt$VT (detected from $TTY_PATH) from $APP_ROOT"
	start_time=$(date +%s)
	# Not `exec` (that would replace this script, leaving nothing to
	# respawn with) and not a bare command either - under `set -e` that
	# would abort this whole script the instant it exits non-zero,
	# before ever reaching the loop body below. COSMOS_PACKAGED=1
	# (previously set by the AppImage's own AppRun shim) disables
	# launcher/run.py's hot-reload watcher - the real, deployed boot
	# path should never be silently reloading edited .py files out from
	# under itself, unlike every dev entry point (run_on_vt.sh, Deploy
	# GUI's own test button), which leave it unset on purpose.
	if COSMOS_PACKAGED=1 python3 "$APP_ROOT/launcher/run.py" "$VT"; then
		exit_code=0
	else
		exit_code=$?
	fi
	elapsed=$(( $(date +%s) - start_time ))
	debug_log "CosmOS (vt$VT) exited with code $exit_code after ${elapsed}s"

	if (( elapsed < MIN_HEALTHY_RUNTIME_S )); then
		debug_log "exited too quickly - waiting ${RESPAWN_BACKOFF_S}s before respawning"
		sleep "$RESPAWN_BACKOFF_S"
	fi
	debug_log "respawning CosmOS on vt$VT"
done
