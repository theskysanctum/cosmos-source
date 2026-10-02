#!/bin/bash
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
WORK=${COSMOS_RPM_WORK:-$HOME/.placements/rpm}
podman run --rm --cap-add=SYS_ADMIN --security-opt seccomp=unconfined --security-opt label=disable -v "$WORK/repo:/repo:ro,z" -v "$WORK/build/cosmos-wm:/wm/cosmos-wm:ro,z" \
    -v "$ROOT/config/wm/test_buffer_size_guard.sh:/wm/test_buffer_size_guard.sh:ro,z" quay.io/fedora/fedora:44 bash -c '
dnf install -y -q --setopt=install_weak_deps=False --repofrompath=cosmos,/repo --setopt=cosmos.gpgcheck=0 --setopt=cosmos.repo_gpgcheck=0 cosmos-shell cosmos-wm bubblewrap gobject-introspection >/dev/null 2>&1
install -m 0755 /wm/cosmos-wm /usr/bin/cosmos-wm
bash /wm/test_buffer_size_guard.sh'
