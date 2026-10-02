#!/bin/bash
# Builds the standalone emulators GameBox launches that are not bundled as binaries: xemu (Xbox) and Flycast (Dreamcast, Naomi, Atomiswave), and
# extract-xiso (turns a full Xbox disc dump into the plain XISO xemu reads; used by emulators/xiso.py), from
# source, unattended (no prompts, no GUI needed). RetroArch, Dolphin and PCSX2 are already bundled under emulators/bin/.
#   build-emulators.sh PREFIX [xemu] [flycast] [extract-xiso]     installs PREFIX/xemu/xemu, PREFIX/flycast/flycast, PREFIX/tools/extract-xiso (default: all)
# Build dependencies are in builddeps.txt and the libraries the results need at run time are in rundeps.txt (both are dnf package names). The
# image's Containerfile installs builddeps.txt in a throwaway stage, runs this, and copies only PREFIX into the final image.
# Versions are pinned: bump them here, deliberately.
set -euo pipefail
XEMU_TAG=v0.8.136
FLYCAST_TAG=v2.7
EXTRACT_XISO_TAG=build-202609111233      # the CMake build of extract-xiso 2.7.1 (the older tags bundle an FTP library that no longer compiles)
PREFIX=${1:?usage: build-emulators.sh PREFIX [xemu] [flycast]}
shift || true
WANT=${*:-xemu flycast extract-xiso}
SRC=${EMU_SRC:-/tmp/emulator-src}
JOBS=${EMU_JOBS:-$(nproc)}
mkdir -p "$SRC" "$PREFIX"

clone() {   # name url tag: a shallow clone of exactly that tag with its submodules
    rm -rf "$SRC/$1"
    git clone --quiet -c advice.detachedHead=false --depth 1 --branch "$3" --recurse-submodules --shallow-submodules -j8 "$2" "$SRC/$1"
}

build_flycast() {
    clone flycast https://github.com/flyinghead/flycast "$FLYCAST_TAG"
    cmake -S "$SRC/flycast" -B "$SRC/flycast/build" -G Ninja -DCMAKE_BUILD_TYPE=Release -DUSE_HOST_SDL=ON -DUSE_HOST_LIBZIP=ON -DUSE_LUA=OFF \
        -DCMAKE_INSTALL_PREFIX="$PREFIX/flycast" -DCMAKE_INSTALL_BINDIR=. -DCMAKE_INSTALL_DATADIR=share
    cmake --build "$SRC/flycast/build" -j"$JOBS"
    mkdir -p "$PREFIX/flycast"
    install -m0755 -s "$SRC/flycast/build/flycast" "$PREFIX/flycast/flycast"
}

build_xemu() {
    clone xemu https://github.com/xemu-project/xemu "$XEMU_TAG"
    (cd "$SRC/xemu" && ./build.sh -j"$JOBS" >"$SRC/xemu-build.log" 2>&1 || { tail -60 "$SRC/xemu-build.log"; exit 1; })
    mkdir -p "$PREFIX/xemu"
    install -m0755 -s "$SRC/xemu/dist/xemu" "$PREFIX/xemu/xemu"
}

build_extract_xiso() {
    clone extract-xiso https://github.com/XboxDev/extract-xiso "$EXTRACT_XISO_TAG"
    cmake -S "$SRC/extract-xiso" -B "$SRC/extract-xiso/build" -G Ninja -DCMAKE_BUILD_TYPE=Release >/dev/null
    cmake --build "$SRC/extract-xiso/build" -j"$JOBS" >/dev/null
    mkdir -p "$PREFIX/tools"
    install -m0755 -s "$SRC/extract-xiso/build/extract-xiso" "$PREFIX/tools/extract-xiso"
}

for name in $WANT; do
    case "$name" in
        flycast) build_flycast ;;
        xemu) build_xemu ;;
        extract-xiso) build_extract_xiso ;;
        *) echo "unknown emulator: $name" >&2; exit 2 ;;
    esac
done
