#!/bin/bash
# Runs config/wm/test_scale.sh against the compositor build.py made, inside a clean Fedora container (the cosmos-wm package brings its libraries).
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
WORK=${COSMOS_RPM_WORK:-$HOME/.placements/rpm}
podman run --rm -v "$WORK/repo:/repo:ro,z" -v "$WORK/build/cosmos-wm:/wm/cosmos-wm:ro,z" -v "$ROOT/config/wm/test_scale.sh:/wm/test_scale.sh:ro,z" \
    -v "$ROOT/packaging/repo-site/RPM-GPG-KEY-cosmos:/key:ro,z" quay.io/fedora/fedora:44 bash -c '
dnf install -y -q --repofrompath=cosmos,/repo --setopt=cosmos.gpgkey=file:///key --setopt=cosmos.gpgcheck=0 --setopt=cosmos.repo_gpgcheck=0 cosmos-wm wayland-utils >/dev/null 2>&1
install -m 0755 /wm/cosmos-wm /usr/bin/cosmos-wm
bash /wm/test_scale.sh'
