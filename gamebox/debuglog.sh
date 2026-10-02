#!/usr/bin/env bash
# Shared verbose-debugging helper for the project's bash scripts, mirroring
# debuglog.py's Python equivalent. Source this, then call debug_log "msg".
# Verbose (stderr output) is the default - set CONSOLE_OS_DEBUG=0 to quiet
# it, matching the same env var the Python side and consolewm.c both
# already respect.

debug_log() {
	if [ "${CONSOLE_OS_DEBUG:-1}" != "0" ]; then
		echo "$(date +%H:%M:%S) DEBUG   $(basename "$0"): $*" >&2
	fi
}
