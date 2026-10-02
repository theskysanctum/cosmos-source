#!/usr/bin/env bash
set -u

[[ -n "${TERM:-}" ]] && clear || true
echo "=============================================="
echo "   CosmOS — Boot Test Disk Creator"
echo "=============================================="
echo

read -rp "Enter disk size (example: 4G, 8G, 512M): " SIZE

if [[ -z "$SIZE" ]]; then
    echo "Error: size cannot be empty."
    exit 1
fi

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUTPUT="$REPO_ROOT/build/test-disk.raw"

mkdir -p "$REPO_ROOT/build"

# Same path "Install in disposable VM" writes its installed disk to -
# don't silently wipe a disk that took a full install run to produce.
if [[ -e "$OUTPUT" ]]; then
    read -rp "$OUTPUT already exists (possibly an installed test disk). Overwrite? [y/N] " answer
    [[ "$answer" == [yY]* ]] || { echo "Left it alone."; exit 1; }
fi

echo
echo "Creating raw disk image: $OUTPUT"
echo "Size: $SIZE"
echo

qemu-img create -f raw "$OUTPUT" "$SIZE" || { echo "Error: qemu-img failed."; exit 1; }

echo
echo "=============================================="
echo "   Disk created successfully"
echo "   File: $OUTPUT"
echo "   Size: $SIZE"
echo "=============================================="
echo

