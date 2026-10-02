#!/usr/bin/env bash
# One-time provisioning: installs Steam and gamescope via dnf, if not
# already present. Not called from launcher/run.py's every-boot startup
# path (unlike install_fonts.sh) - a package install is a heavier, sudo-
# gated, real-download operation, not something to silently repeat-check
# on every launch. Run this once (or after a fresh image) instead.
#
# Steam itself comes from RPM Fusion nonfree (already enabled on this
# system); gamescope is in Fedora's own repos. Both are native RPMs, not
# Flatpak - keeps gamescope's DRM/KMS access and controller/input handling
# talking directly to the same system Steam runs under, no sandbox layer
# to route through.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
source "$SCRIPT_DIR/../debuglog.sh"

need_install=()
for pkg in steam gamescope; do
	if ! rpm -q "$pkg" >/dev/null 2>&1; then
		need_install+=("$pkg")
	fi
done

if [ "${#need_install[@]}" -eq 0 ]; then
	debug_log "steam and gamescope already installed, skipping"
	exit 0
fi

debug_log "installing: ${need_install[*]}"
# root comes from the kronos admin service on CosmOS (no sudo there), sudo anywhere else
if command -v kronos >/dev/null 2>&1; then admin=(kronos exec --); else admin=(sudo); fi
"${admin[@]}" dnf install -y "${need_install[@]}"
debug_log "install complete"
