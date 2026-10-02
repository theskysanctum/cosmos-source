#!/usr/bin/env bash
set -euo pipefail

[[ -n "${TERM:-}" ]] && clear || true
echo "=============================================="
echo "   CosmOS — Build Container (bootc)"
echo "=============================================="
echo

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
IMAGE_TAG="${1:-localhost/cosmos:latest}"

# Cache janitor: prevents OOM-kill from page cache buildup
cache_janitor_pid=""
start_cache_janitor() {
    (
        while true; do
            sync
            echo 1 | sudo tee /proc/sys/vm/drop_caches >/dev/null
            sleep 15
        done
    ) &
    cache_janitor_pid=$!
}
stop_cache_janitor() {
    [ -n "$cache_janitor_pid" ] && kill "$cache_janitor_pid" 2>/dev/null || true
}
trap stop_cache_janitor EXIT
start_cache_janitor

echo "== Building $IMAGE_TAG from Containerfile (this takes a while) =="
mkdir -p "$REPO_ROOT/build"

sudo podman build -t "$IMAGE_TAG" -f "$REPO_ROOT/Containerfile" "$REPO_ROOT" 2>&1 \
    | tee "$REPO_ROOT/build/build.log"

echo
echo "== Done: $IMAGE_TAG =="
sudo podman images "$IMAGE_TAG"

echo
echo "=============================================="
echo "   Completed: Build Container (bootc)"
echo "=============================================="
echo

