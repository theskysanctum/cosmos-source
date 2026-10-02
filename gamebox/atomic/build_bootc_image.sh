#!/bin/bash
# Thin wrapper around the real bootc/Atomic image build - matches every
# other pipeline step in this project being its own standalone script
# deploy_gui.py just orchestrates, rather than an inline command only
# that GUI knows about. Stages a fresh copy of the tracked source tree
# first (packaging/stage_build.sh, into a fixed path so atomic/
# Containerfile's own COPY has somewhere stable to reference) - CosmOS
# is no longer packaged as an AppImage at all, per direct request, so
# there's no separate build artifact to check for any more.
#
# Usage: build_bootc_image.sh [image_tag]
#   image_tag - defaults to localhost/cosmos-real:latest
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
IMAGE_TAG="${1:-localhost/cosmos-real:latest}"
STAGE_DIR="$REPO_ROOT/build/bootc-stage"

echo "== Staging the tracked source tree (packaging/stage_build.sh) =="
"$REPO_ROOT/packaging/stage_build.sh" "$STAGE_DIR"

echo "== Building $IMAGE_TAG from atomic/Containerfile (this takes a while) =="
sudo podman build -t "$IMAGE_TAG" -f "$REPO_ROOT/atomic/Containerfile" "$REPO_ROOT"
echo "== Done: $IMAGE_TAG =="
sudo podman images "$IMAGE_TAG"
