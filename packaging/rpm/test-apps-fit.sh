#!/bin/bash
# Checks that no CosmOS app runs off the edge of a small screen: config/scripts/test_apps_fit.sh inside a clean Fedora container, with the compositor
# build.py made and the cosmos-* packages from its repository.   test-apps-fit.sh [APP...]
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
WORK=${COSMOS_RPM_WORK:-$HOME/.placements/rpm}
podman run --rm --cap-add=SYS_ADMIN --security-opt seccomp=unconfined --security-opt label=disable -v "$WORK/repo:/repo:ro,z" -v "$WORK/build/cosmos-wm:/wm/cosmos-wm:ro,z" \
    -v "$ROOT/config/scripts/test_apps_fit.sh:/wm/test_apps_fit.sh:ro,z" quay.io/fedora/fedora:44 bash -c '
dnf install -y -q --setopt=install_weak_deps=False --repofrompath=cosmos,/repo --setopt=cosmos.gpgcheck=0 --setopt=cosmos.repo_gpgcheck=0 cosmos-settings cosmos-controllers cosmos-updates cosmos-remote cosmos-android cosmos-winpack cosmos-vm-manager cosmos-kronos cosmos-recovery cosmos-files cosmos-shop cosmos-shell cosmos-wm bubblewrap >/dev/null 2>&1
install -m 0755 /wm/cosmos-wm /usr/bin/cosmos-wm
bash /wm/test_apps_fit.sh '"$*"
