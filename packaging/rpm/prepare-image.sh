#!/bin/bash
# Gets the package repository ready for an image build: builds every cosmos-* RPM (build.py; the slow emulator build is kept between runs), and puts
# the repository, with the public signing key next to it, in packaging/rpm-repo/, which the Containerfile's `cosmos-rpms` stage copies. Bootc Studio
# runs this before each image build (the "pre_build" in .bootc-studio.json).
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
WORK=${COSMOS_RPM_WORK:-$HOME/.placements/rpm}
python3 "$ROOT/packaging/rpm/build.py"
rm -rf "$ROOT/packaging/rpm-repo"
mkdir -p "$ROOT/packaging/rpm-repo"
cp -a "$WORK/repo/." "$ROOT/packaging/rpm-repo/"
cp "$ROOT/packaging/repo-site/RPM-GPG-KEY-cosmos" "$ROOT/packaging/rpm-repo/"
echo "repository for the image: $ROOT/packaging/rpm-repo ($(du -sh "$ROOT/packaging/rpm-repo" | cut -f1))"
