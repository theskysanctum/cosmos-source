#!/usr/bin/env bash
# Display resolution backend via xrandr, invoked by settings/display.py.
# Resolution-level only (not per-refresh-rate) - the granularity that
# actually matters for a console hooked to one TV/monitor; xrandr picks
# a sensible default rate for a mode given just its resolution.
#
# Usage: display.sh primary-output
#        display.sh list-modes [output]
#        display.sh set-mode <mode> [output]
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
source "$SCRIPT_DIR/../debuglog.sh"

primary_output() {
	local output
	output=$(xrandr | awk '/ connected/ {print $1; exit}')
	debug_log "primary output: ${output:-<none>}"
	printf '%s' "$output"
}

list_modes() {
	# Plain string-prefix matching (not a regex) so output names with
	# regex-special characters (e.g. "HDMI-1") can't misbehave.
	local output="${1:-}"
	[ -z "$output" ] && output=$(primary_output)
	[ -z "$output" ] && return 0

	local in_output=0
	while IFS= read -r line; do
		if [[ "$line" == "$output "* ]]; then
			in_output=1
			continue
		fi
		if [ "$in_output" = "1" ]; then
			[[ "$line" == " "* ]] || break
			if [[ "$line" =~ ^[[:space:]]+([0-9]+x[0-9]+) ]]; then
				local mode="${BASH_REMATCH[1]}"
				local current="not-current"
				[[ "$line" == *"*"* ]] && current="current"
				echo "$mode,$current"
			fi
		fi
	done < <(xrandr)
}

set_mode() {
	local mode="$1"
	local output="${2:-}"
	[ -z "$output" ] && output=$(primary_output)
	debug_log "setting $output to $mode"
	xrandr --output "$output" --mode "$mode"
}

case "${1:-}" in
	primary-output) primary_output ;;
	list-modes) list_modes "${2:-}" ;;
	set-mode) set_mode "${2:?mode required}" "${3:-}" ;;
	*)
		echo "usage: $0 {primary-output|list-modes|set-mode} ..." >&2
		exit 1
		;;
esac
