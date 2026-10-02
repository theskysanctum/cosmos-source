#!/bin/bash
# Builds xemu, Flycast and extract-xiso from source in a clean Fedora container (gamebox/emulators/build/build-emulators.sh does the work, the same
# script the image build stage uses) into $COSMOS_RPM_WORK/build/emulators/. It takes a long time, so the result is kept: it is rebuilt only when
# the build script or its dependency lists change (or with FORCE=1).
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
WORK=${COSMOS_RPM_WORK:-$HOME/.placements/rpm}
SRC="$ROOT/gamebox/emulators/build"
OUT="$WORK/build/emulators"
KEY=$(cat "$SRC/build-emulators.sh" "$SRC/builddeps.txt" | sha256sum | cut -c1-16)
if [ -z "${FORCE:-}" ] && [ "$(cat "$OUT/.key" 2>/dev/null)" = "$KEY" ] && [ -x "$OUT/xemu/xemu" ] && [ -x "$OUT/flycast/flycast" ] && [ -x "$OUT/tools/extract-xiso" ]; then
    echo "emulators: up to date ($KEY)"; exit 0
fi
rm -rf "$OUT"; mkdir -p "$OUT"
podman run --rm -e EMU_JOBS="${EMU_JOBS:-6}" -v "$SRC:/build:ro,Z" -v "$OUT:/out:z" quay.io/fedora/fedora:44 bash -c '
set -e
dnf install -y -q $(cat /build/builddeps.txt) >/dev/null
/build/build-emulators.sh /out'
echo "$KEY" > "$OUT/.key"
ls -la "$OUT"/xemu/xemu "$OUT"/flycast/flycast "$OUT"/tools/extract-xiso
