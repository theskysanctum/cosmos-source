#!/bin/bash
# Builds CosmOSDeploy.AppImage - the packaging/deploy_gui/ Tkinter tool.
# CosmOS itself is no longer packaged as an AppImage at all (per direct
# request - see packaging/stage_build.sh/cosmos_install.py instead), but
# this tool's own packaging is a separate, still-AppImage concern, left
# as-is. Simpler than the old CosmOS AppImage build used to be: Tkinter
# has no GTK3/GObject-Introspection layer to bundle - the python-appimage
# base build's own Tcl/Tk already works standalone, confirmed for real (a
# real Tk window created and rendered against a real X display from
# inside the bundled interpreter, no extra plugin needed).
#
# The tool still needs this repo's own packaging/cosmos_install.py
# (plus paths.py/debuglog.py, its own real dependencies) alongside it, so
# it stages the whole tracked source tree as extra data - the same
# "the whole repo bundled either way, just with a different entry point"
# approach packaging/stage_build.sh uses for CosmOS's own real deploy
# target.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD_DIR="${1:-$(mktemp -d /tmp/cosmos-deploy-gui-build.XXXXXX)}"
RECIPE_DIR="$REPO_ROOT/packaging/deploy_gui/recipe"
TOOLS_DIR="$BUILD_DIR/tools"
STAGE_DIR="$BUILD_DIR/stage/cosmos"
OUTPUT="$REPO_ROOT/CosmOSDeploy-x86_64.AppImage"

echo "== Build workspace: $BUILD_DIR =="
mkdir -p "$TOOLS_DIR" "$BUILD_DIR/stage"

echo "== Staging tracked source tree =="
if ! git -C "$REPO_ROOT" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
	echo "ERROR: $REPO_ROOT is not a real git checkout - run this from the" >&2
	echo "actual repo, not from inside an already-bundled AppImage." >&2
	exit 1
fi
rm -rf "$STAGE_DIR"
mkdir -p "$STAGE_DIR"
git -C "$REPO_ROOT" ls-files | tar -cf - -C "$REPO_ROOT" -T - | tar -xf - -C "$STAGE_DIR"

echo "== Compiling wm/consolewm (gitignored build artifact, not tracked) =="
gcc -O2 -Wall -o "$STAGE_DIR/wm/consolewm" "$REPO_ROOT/wm/consolewm.c" -lX11
chmod +x "$STAGE_DIR/wm/consolewm"

# .env (real API keys - IGDB, Dropbox) is gitignored on
# purpose - never tracked, never in `git ls-files` above - but the whole
# point of bundling this tool as a single AppImage is that it works on a
# fresh machine with zero setup, so it still needs to ship INSIDE the
# built artifact. Same "stage this real, gitignored file alongside the
# tracked tree" treatment wm/consolewm gets just above, for a config file
# instead of a compiled binary. Skipped with a clear warning (not a hard
# failure) when this dev machine's own .env doesn't exist - a legitimate
# case (every caller handles a missing key as a clean, logged failure).
if [ -f "$REPO_ROOT/.env" ]; then
	echo "== Bundling .env (gitignored build artifact, not tracked) =="
	cp "$REPO_ROOT/.env" "$STAGE_DIR/.env"
else
	echo "WARNING: no $REPO_ROOT/.env found - the built AppImage will have no" >&2
	echo "API keys baked in (Clone Repos/Push to GHCR use the 'gh auth login'" >&2
	echo "session of whatever machine runs it)." >&2
fi

echo "== Fetching appimagetool =="
cd "$TOOLS_DIR"
[ -f appimagetool ] || curl -sL -o appimagetool \
	"https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage"
chmod +x appimagetool

echo "== Bundling Python (python-appimage) - no pip dependencies needed =="
cd "$BUILD_DIR"
python3 -m pip install --quiet --user --upgrade python-appimage >/dev/null
python3 -m python_appimage build app -n cosmosdeploy --no-packaging "$RECIPE_DIR" -x "$STAGE_DIR"
# python-appimage names the output dir after the .desktop file's Name=
# field, not -n - found by globbing rather than hardcoding a path that
# would silently go stale the next time that field changes.
APPDIR="$(find "$BUILD_DIR" -maxdepth 1 -type d -name '*-x86_64' | head -1)"
if [ -z "$APPDIR" ]; then
	echo "Could not find the built AppDir under $BUILD_DIR" >&2
	exit 1
fi

echo "== Packaging =="
# appimagetool opens $OUTPUT directly for writing, which fails with ETXTBSY if
# that exact file is currently running as a process - build to a temp
# file and rename into place instead.
OUTPUT_TMP="$OUTPUT.new"
rm -f "$OUTPUT_TMP"
"$TOOLS_DIR/appimagetool" "$APPDIR" "$OUTPUT_TMP"
mv -f "$OUTPUT_TMP" "$OUTPUT"

echo "== Done: $OUTPUT =="
ls -la "$OUTPUT"
