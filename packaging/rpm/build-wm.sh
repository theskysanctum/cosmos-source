#!/bin/bash
# Compiles cosmos-wm in a clean Fedora container (so the result depends on nothing installed on this machine) into $COSMOS_RPM_WORK/build/cosmos-wm.
# The pkg-config name pins the wlroots API version (0.20 on Fedora 44): a release whose wlroots has moved on fails here, loudly, instead of building
# against the wrong API.
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
WORK=${COSMOS_RPM_WORK:-$HOME/.placements/rpm}
mkdir -p "$WORK/build"
podman run --rm -v "$ROOT/config/wm:/src:ro,Z" -v "$WORK/build:/out:z" quay.io/fedora/fedora:44 bash -c '
set -e
dnf install -y -q gcc pkgconf-pkg-config wlroots-devel wayland-devel wayland-protocols-devel wlr-protocols-devel libxkbcommon-devel pango-devel cairo-devel pixman-devel libdrm-devel >/dev/null 2>&1
mkdir -p /work && cd /work && cp /src/cosmos-wm.c /src/output_scale.h /src/motion.h .
wayland-scanner server-header "$(pkg-config --variable=pkgdatadir wayland-protocols)/stable/xdg-shell/xdg-shell.xml" xdg-shell-protocol.h
wayland-scanner server-header /usr/share/wlr-protocols/unstable/wlr-layer-shell-unstable-v1.xml wlr-layer-shell-unstable-v1-protocol.h
gcc -O2 -Wall -Wno-unused-parameter -DWLR_USE_UNSTABLE -I. -o /out/cosmos-wm cosmos-wm.c $(pkg-config --cflags --libs wlroots-0.20 wayland-server xkbcommon pangocairo pixman-1 libdrm) -lm
strip /out/cosmos-wm'
ls -la "$WORK/build/cosmos-wm"
