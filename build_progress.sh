#!/bin/bash
# Checks on a `build-bootc.sh` run without needing to ask - direct
# request ("can you make script i can run to check instead"). Reads the
# fixed log path that script always tees its output to
# (build/build.log), not a one-off path tied to any particular way it
# was started (foreground, nohup'd, from another terminal, etc).
#
# Usage: ./build_progress.sh          # one-shot status
#        ./build_progress.sh -f       # follow live, like tail -f
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG="$REPO_ROOT/build/build.log"

if [ ! -f "$LOG" ]; then
	echo "No build log yet at $LOG - run ./build-bootc.sh first."
	exit 1
fi

if [ "${1:-}" = "-f" ] || [ "${1:-}" = "--follow" ]; then
	exec tail -f "$LOG"
fi

# pgrep on the actual podman build invocation, not build-bootc.sh
# itself - that wrapper's own cache-janitor background loop (see its own
# comment) keeps a process alive for the whole build, but podman build
# is the thing that actually indicates whether work is still happening.
if pgrep -f "podman build .*Containerfile" >/dev/null 2>&1; then
	STATUS="RUNNING"
else
	STATUS="NOT RUNNING"
fi

# Every Containerfile step logs a real "STEP N/M" line as it starts -
# the last one seen so far is exactly how far the build has gotten,
# no separate progress tracking needed.
LAST_STEP="$(grep -oE 'STEP [0-9]+/[0-9]+' "$LOG" | tail -1 || true)"

echo "== Build status: $STATUS =="
if [ -n "$LAST_STEP" ]; then
	CURRENT="${LAST_STEP#STEP }"
	CURRENT="${CURRENT%/*}"
	TOTAL="${LAST_STEP#*/}"
	PERCENT="$((CURRENT * 100 / TOTAL))"
	echo "Progress: $LAST_STEP (~${PERCENT}%)"
fi

if [ "$STATUS" = "NOT RUNNING" ]; then
	if tail -5 "$LOG" | grep -q "Successfully tagged"; then
		echo "Finished successfully."
	else
		echo "Not running and no 'Successfully tagged' line - it likely failed or was interrupted. Last lines:"
	fi
fi

echo
echo "== Last 15 lines of $LOG =="
tail -15 "$LOG"
