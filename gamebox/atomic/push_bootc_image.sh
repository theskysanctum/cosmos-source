#!/bin/bash
# Pushes the locally-built bootc image (see build_bootc_image.sh) to
# GHCR - the same registry/tag convention .github/workflows/build-image.yml
# uses (ghcr.io/theskysanctum/cosmos:latest + :YYYYMMDD), so a manual
# local push and a CI push are indistinguishable to a real machine's own
# `bootc upgrade` later. CI authenticates via its own ephemeral
# secrets.GITHUB_TOKEN; this script instead uses GH_TOKEN if set, else the
# token of the manual `gh auth login` session - nothing is embedded.
#
# Usage: push_bootc_image.sh [local_image_tag]
#   local_image_tag - defaults to localhost/cosmos-real:latest (matches
#   build_bootc_image.sh's own default)
set -euo pipefail

GH_TOKEN="${GH_TOKEN:-$(gh auth token 2>/dev/null || true)}"
: "${GH_TOKEN:?not signed in to GitHub - run gh auth login (or set GH_TOKEN)}"

LOCAL_TAG="${1:-localhost/cosmos-real:latest}"
REGISTRY="ghcr.io/theskysanctum"
IMAGE_NAME="cosmos"
DATE_TAG="$(date -u +%Y%m%d)"

# sudo, matching build_bootc_image.sh's own sudo podman build - same
# root-owned podman storage the local image actually lives in.
echo "== Logging in to $REGISTRY =="
echo "$GH_TOKEN" | sudo podman login ghcr.io -u theskysanctum --password-stdin

echo "== Tagging $LOCAL_TAG as $REGISTRY/$IMAGE_NAME:latest and :$DATE_TAG =="
sudo podman tag "$LOCAL_TAG" "$REGISTRY/$IMAGE_NAME:latest"
sudo podman tag "$LOCAL_TAG" "$REGISTRY/$IMAGE_NAME:$DATE_TAG"

echo "== Pushing $REGISTRY/$IMAGE_NAME:latest =="
sudo podman push "$REGISTRY/$IMAGE_NAME:latest"
echo "== Pushing $REGISTRY/$IMAGE_NAME:$DATE_TAG =="
sudo podman push "$REGISTRY/$IMAGE_NAME:$DATE_TAG"

echo "== Done: pushed $REGISTRY/$IMAGE_NAME:latest and :$DATE_TAG =="
