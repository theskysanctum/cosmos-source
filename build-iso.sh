#!/usr/bin/env bash
set -euo pipefail

[[ -n "${TERM:-}" ]] && clear || true
echo "=============================================="
echo "   CosmOS — Build Installer ISO"
echo "=============================================="
echo

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
IMAGE_REF="localhost/cosmos:latest"
BUILDER_IMAGE="quay.io/centos-bootc/bootc-image-builder:latest"
OUTPUT_DIR="$REPO_ROOT/build/iso-output"

for tool in podman sudo; do
    command -v "$tool" >/dev/null 2>&1 || { echo "$tool not found - install it and retry" >&2; exit 1; }
done

sudo podman image exists "$IMAGE_REF" || {
    echo "$IMAGE_REF not found in local podman storage - run build-bootc.sh first" >&2
    exit 1
}

mkdir -p "$OUTPUT_DIR"

AVAIL_GB="$(df --output=avail -BG "$REPO_ROOT/build" | tail -1 | tr -dc '0-9')"
if [ "$AVAIL_GB" -lt 20 ]; then
    echo "Only ${AVAIL_GB}G free under $REPO_ROOT/build - need at least 20G for a safe margin" >&2
    exit 1
fi

echo "== Building an Anaconda-based installer ISO from $IMAGE_REF =="
echo "== Output directory: $OUTPUT_DIR =="
echo

sudo podman run \
    --rm \
    --privileged \
    --security-opt label=type:unconfined_t \
    -v "$OUTPUT_DIR:/output" \
    -v "$REPO_ROOT/iso-config.toml:/config.toml:ro" \
    -v /var/lib/containers/storage:/var/lib/containers/storage \
    "$BUILDER_IMAGE" \
    --type anaconda-iso \
    --rootfs ext4 \
    --chown "$(id -u):$(id -g)" \
    "$IMAGE_REF"

ISO_PATH="$(find "$OUTPUT_DIR" -iname '*.iso' | head -1)"
if [ -z "$ISO_PATH" ]; then
    echo "Build finished but no .iso file was found under $OUTPUT_DIR - check the output above" >&2
    exit 1
fi

echo "== Done: $ISO_PATH =="
echo "Drop this file onto your Ventoy drive like any other ISO — no dd/flash step needed."

echo
echo "=============================================="
echo "   Completed: Build Installer ISO"
echo "   ISO: $ISO_PATH"
echo "=============================================="
echo

