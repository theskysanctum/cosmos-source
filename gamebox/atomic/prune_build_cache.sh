#!/bin/bash
set -euo pipefail

# Every Containerfile/AppImage rebuild cycle through this project's own
# build scripts leaves the previous image's now-unreferenced layers
# behind as dangling (<none>) images - confirmed live this accumulates
# fast: a single session of normal fix-rebuild iteration left 59GB of
# 100% reclaimable podman storage. `podman image prune` (no -a) only
# removes genuinely dangling images, never a currently-tagged one
# (localhost/cosmos-real:latest, the pulled GHCR copy, base images,
# ...) - safe to run any time, including with other builds in flight
# elsewhere on the machine.
echo "== Before =="
sudo podman system df

echo
echo "== Pruning dangling images =="
sudo podman image prune -f

echo
echo "== After =="
sudo podman system df
