#!/bin/bash
# Stages a real, runnable copy of CosmOS's tracked source tree - the one
# thing every install path needs (a dev-machine `/cosmos/app/current`
# swap via cosmos_install.py, or atomic/build_bootc_image.sh's own COPY
# into the Containerfile build context), now that there's no more AppImage
# to build around it: CosmOS is no longer packaged as one at all, per
# direct request - GTK3/PyGObject and every pip dependency now install as
# real host packages wherever CosmOS actually runs (packaging/host_deps.py,
# atomic/Containerfile's own dnf/pip RUN step) instead of being bundled.
#
# Just the two real, still-needed steps packaging/build_appimage.sh used to
# do before handing off to linuxdeploy/appimagetool (both gone now, along
# with that whole bundling toolchain):
#   1. `git ls-files | tar` - stage exactly the tracked tree, nothing more
#      (gitignored cruft like __pycache__/, build/, emulators/bin/'s
#      deploy-time-fetched cores/assets, webshell/electron/ never included).
#      RetroArch (emulators/bin/retroarch, emulators/bin/cores/) and Wine
#      (wine/bin/) are both committed straight into the repo, so this one
#      step already carries them along with everything else - no separate
#      install step for either.
#   2. Compile wm/consolewm.c fresh into the staged tree - it's gitignored
#      (a real build artifact, correctly not tracked) so this is the one
#      place it has to exist. Harmless that atomic/Containerfile's own RUN
#      step recompiles this again from the same staged .c file for the
#      bootc image specifically - a host-compiled binary can't safely be
#      assumed ABI-compatible with a different base image's own libc/libX11,
#      so that recompile is real, deliberate defense, not redundant waste.
#
# Usage: stage_build.sh [output_dir] - output_dir defaults to a fresh
# mktemp directory (matching build_appimage.sh's own old BUILD_DIR
# convention); the caller owns cleaning it up once done with it.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD_CACHE_DIR="${XDG_CACHE_HOME:-$HOME/.cache}"
mkdir -p "$BUILD_CACHE_DIR"
OUTPUT_DIR="${1:-$(mktemp -d "$BUILD_CACHE_DIR/cosmos-stage.XXXXXX")}"

if ! git -C "$REPO_ROOT" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
	echo "ERROR: $REPO_ROOT is not a real git checkout." >&2
	echo "This usually means this script is running from inside a bundled" >&2
	echo "CosmOSDeploy AppImage (its own real repo's source is staged as" >&2
	echo "plain extra data, with no .git) rather than a real checkout on" >&2
	echo "disk - staging needs a real one. Run this from the actual repo" >&2
	echo "instead, e.g.: cd ~/os && python3 packaging/deploy_gui/deploy_gui.py" >&2
	exit 1
fi

echo "== Staging tracked source tree into $OUTPUT_DIR =="
rm -rf "$OUTPUT_DIR"
mkdir -p "$OUTPUT_DIR"
git -C "$REPO_ROOT" ls-files | tar -cf - -C "$REPO_ROOT" -T - | tar -xf - -C "$OUTPUT_DIR"

echo "== Compiling wm/consolewm (gitignored build artifact, not tracked) =="
gcc -O2 -Wall -o "$OUTPUT_DIR/wm/consolewm" "$REPO_ROOT/wm/consolewm.c" -lX11
chmod +x "$OUTPUT_DIR/wm/consolewm"

echo "== Staged: $OUTPUT_DIR =="
