#!/usr/bin/env bash
# Wi-Fi backend via nmcli (NetworkManager), invoked by settings/wifi.py.
# Driven through nmcli's terse (-t) machine-readable output rather than
# screen-scraping the human-formatted table.
#
# Usage: wifi.sh is-enabled
#        wifi.sh set-enabled <on|off>
#        wifi.sh current-ssid
#        wifi.sh scan
#        wifi.sh connect <ssid> [password]
#        wifi.sh disconnect
set -uo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
source "$SCRIPT_DIR/../debuglog.sh"

strip_ansi() {
	sed -E 's/\x1b\[[0-9;]*[A-Za-z]//g'
}

is_enabled() {
	local state
	state=$(nmcli -t -f WIFI radio)
	debug_log "radio state: $state"
	[ "$state" = "enabled" ] && echo yes || echo no
}

is_online() {
	# A real internet-reachability probe (NetworkManager's own
	# connectivity-check URL), not just "is some interface up" - "full"
	# is the only state that means real internet; "limited"/"portal"/
	# "none" all mean shop/gamegrubber_backend.py's own catalog/download
	# features wouldn't actually work.
	local state
	state=$(nmcli networking connectivity check)
	debug_log "connectivity: $state"
	[ "$state" = "full" ] && echo yes || echo no
}

set_enabled() {
	debug_log "setting radio $1"
	nmcli radio wifi "$1"
}

current_ssid() {
	nmcli -t -f ACTIVE,SSID dev wifi | awk -F: '$1=="yes"{print $2; exit}'
}

scan() {
	debug_log "rescanning for wifi networks"
	nmcli dev wifi rescan >/dev/null 2>&1
	# Dedup by SSID (keep the strongest signal seen), strongest first -
	# the same shape as the original Python's dict-keyed-by-ssid dedup.
	nmcli -t -f SSID,SIGNAL,SECURITY dev wifi list | awk -F: '
		{
			ssid = $1; signal = $2 + 0
			security = $3
			if (ssid == "") next
			secured = (security != "" && security != "--") ? "secured" : "open"
			if (!(ssid in best) || signal > best[ssid]) {
				best[ssid] = signal
				line[ssid] = ssid "," signal "," secured
			}
		}
		END { for (s in line) print line[s] }
	' | sort -t, -k2,2 -rn
}

connect() {
	local ssid="$1"
	local password="${2:-}"
	local -a args=(dev wifi connect "$ssid")
	[ -n "$password" ] && args+=(password "$password")
	if [ -n "$password" ]; then debug_log "connecting to $ssid (password provided)"; else debug_log "connecting to $ssid (no password)"; fi
	local out
	out=$(nmcli "${args[@]}" 2>&1)
	local rc=$?
	echo "$out" | strip_ansi
	exit $rc
}

disconnect() {
	local ssid
	ssid=$(current_ssid)
	if [ -n "$ssid" ]; then
		debug_log "disconnecting from $ssid"
		nmcli con down "$ssid" >/dev/null 2>&1
	fi
}

case "${1:-}" in
	is-enabled) is_enabled ;;
	set-enabled) set_enabled "${2:?on or off required}" ;;
	current-ssid) current_ssid ;;
	scan) scan ;;
	connect) connect "${2:?ssid required}" "${3:-}" ;;
	disconnect) disconnect ;;
	is-online) is_online ;;
	*)
		echo "usage: $0 {is-enabled|set-enabled|current-ssid|scan|connect|disconnect|is-online} ..." >&2
		exit 1
		;;
esac
