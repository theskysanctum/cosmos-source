#!/bin/bash
# Prepares the two GameBox pieces that are generated, not copied, into $COSMOS_RPM_WORK/build/:
#   gamebox-stamp   the time of this build; gamebox-dir compares it with the writable copy's stamp to know when to refresh it from /usr/share/gamebox
#   consolewm       GameBox's own small X window manager, compiled in a clean Fedora container
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
WORK=${COSMOS_RPM_WORK:-$HOME/.placements/rpm}
mkdir -p "$WORK/build"
date +%s > "$WORK/build/gamebox-stamp"
podman run --rm -v "$ROOT/gamebox/wm:/src:ro,Z" -v "$WORK/build:/out:z" quay.io/fedora/fedora:44 bash -c '
set -e
dnf install -y -q gcc libX11-devel >/dev/null 2>&1
gcc -O2 -Wall -o /out/consolewm /src/consolewm.c -lX11
strip /out/consolewm'
ls -la "$WORK/build/consolewm" "$WORK/build/gamebox-stamp"
