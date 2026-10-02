#!/usr/bin/env bash
# Bluetooth backend, driven non-interactively by piping commands into
# bluetoothctl's stdin - it has no batch/scripting flag of its own, so
# this is the standard way to script it. Invoked by settings/bluetooth.py.
#
# Usage: bluetooth.sh is-powered
#        bluetooth.sh set-powered <on|off>
#        bluetooth.sh paired-devices
#        bluetooth.sh scan [duration_seconds]
#        bluetooth.sh pair-and-connect <mac>
#        bluetooth.sh remove <mac>
set -uo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
source "$SCRIPT_DIR/../debuglog.sh"

strip_ansi() {
	sed -E 's/\x1b\[[0-9;]*[A-Za-z]//g'
}

parse_devices() {
	# Anchored to the START of the line on purpose: bluetoothctl's own
	# clean "devices"/"paired-devices" listing lines look exactly like
	# "Device AA:BB:CC:DD:EE:FF Some Name" with nothing before them, but
	# its *live* scan-event lines for the same device ("[CHG] Device
	# AA:BB:CC:DD:EE:FF RSSI: 0xffffffbc (-68)", "[NEW] Device ...") are
	# always prefixed with the interactive "[bluetoothctl]> " prompt (or
	# a "[TAG]" marker) first - verified against real captured output,
	# not assumed. An unanchored match picks up both, and RSSI-update
	# noise can win the earlier sort-based dedup over the real name.
	grep -E '^Device (([0-9A-F]{2}:){5}[0-9A-F]{2}) .+' | sed -E 's/^Device (([0-9A-F]{2}:){5}[0-9A-F]{2}) (.+)$/\1,\3/'
}

bt_run() {
	debug_log "bluetoothctl <- $*"
	printf '%s\n' "$@" | timeout "${BT_TIMEOUT:-10}" bluetoothctl | strip_ansi
}

is_powered() {
	bt_run "show" | grep -q "Powered: yes" && echo yes || echo no
}

set_powered() {
	debug_log "setting power $1"
	bt_run "power $1" >/dev/null
}

paired_devices() {
	bt_run "paired-devices" | parse_devices
}

scan() {
	local duration="${1:-5}"
	debug_log "scanning for ${duration}s"
	{
		echo "scan on"
		sleep "$duration"
		echo "devices"
		echo "scan off"
		echo "quit"
	} | timeout "$((duration + 10))" bluetoothctl | strip_ansi | parse_devices | sort -u -t, -k1,1
}

pair_and_connect() {
	local mac="$1"
	local out
	out=$(bt_run "pair $mac" "trust $mac" "connect $mac")
	echo "$out"
	echo "$out" | grep -qE 'Connection successful|Connected: yes|Already connected'
}

remove_device() {
	debug_log "removing $1"
	bt_run "remove $1" >/dev/null
}

case "${1:-}" in
	is-powered) is_powered ;;
	set-powered) set_powered "${2:?on or off required}" ;;
	paired-devices) paired_devices ;;
	scan) scan "${2:-5}" ;;
	pair-and-connect) pair_and_connect "${2:?mac required}" ;;
	remove) remove_device "${2:?mac required}" ;;
	*)
		echo "usage: $0 {is-powered|set-powered|paired-devices|scan|pair-and-connect|remove} ..." >&2
		exit 1
		;;
esac
