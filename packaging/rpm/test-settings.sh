#!/bin/bash
# config/scripts/test_settings.sh inside a clean Fedora container with the cosmos-* packages from build.py's repository and its compositor; screenshots go to
# ~/.placements/settings-shots.    test-settings.sh [PAGE...]
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
WORK=${COSMOS_RPM_WORK:-$HOME/.placements/rpm}
mkdir -p "$HOME/.placements/settings-shots"; rm -f "$HOME/.placements/settings-shots"/*.png
podman run --rm --cap-add=SYS_ADMIN --security-opt seccomp=unconfined --security-opt label=disable -v "$WORK/repo:/repo:ro,z" -v "$WORK/build/cosmos-wm:/wm/cosmos-wm:ro,z" \
    -v "$ROOT/config/scripts/test_settings.sh:/wm/test_settings.sh:ro,z" -v "$HOME/.placements/settings-shots:/shots:z" quay.io/fedora/fedora:44 bash -c '
dnf install -y -q --setopt=install_weak_deps=False --repofrompath=cosmos,/repo --setopt=cosmos.gpgcheck=0 --setopt=cosmos.repo_gpgcheck=0 cosmos-settings cosmos-controllers cosmos-updates cosmos-remote cosmos-android cosmos-winpack cosmos-vm-manager cosmos-kronos cosmos-shell cosmos-wm bubblewrap grim >/dev/null 2>&1
install -m 0755 /wm/cosmos-wm /usr/bin/cosmos-wm
bash /wm/test_settings.sh '"$*"
