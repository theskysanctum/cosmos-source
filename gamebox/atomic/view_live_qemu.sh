#!/bin/bash
# Boots the already-installed bootc raw disk (see atomic/test_bootc_boot.sh,
# which creates it) with a REAL, visible QEMU window (-display gtk)
# instead of headless -display none - for actually watching CosmOS run
# live, not just checking pass/fail over serial. Doesn't install or
# verify anything itself - run "Test Install & Boot (local image)" first
# if the disk doesn't exist yet.
#
# Usage: view_live_qemu.sh [disk_image_path]
#   disk_image_path - defaults to the same path test_bootc_boot.sh uses
#
# Fire-and-forget: launches qemu in the background and returns immediately
# so this can be driven from deploy_gui.py without blocking its log
# thread on "close the window when done" the way a real interactive
# session would. Close the QEMU window itself (or its Machine > Quit
# menu) when done - this script doesn't tear anything down for you.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DISK_IMAGE="${1:-$REPO_ROOT/build/cosmos-boot-test-disk.raw}"

OVMF_CODE="/usr/share/edk2/ovmf/OVMF_CODE.fd"
OVMF_VARS_TEMPLATE="/usr/share/edk2/ovmf/OVMF_VARS.fd"

[ -f "$DISK_IMAGE" ] || {
	echo "$DISK_IMAGE not found - run 'Test Install & Boot (local image)' first to create it" >&2
	exit 1
}
command -v qemu-system-x86_64 >/dev/null || { echo "qemu-system-x86_64 not found - install it and retry" >&2; exit 1; }
[ -f "$OVMF_CODE" ] || { echo "$OVMF_CODE not found - install edk2-ovmf and retry" >&2; exit 1; }
[ -n "${DISPLAY:-}" ] || { echo "no DISPLAY set - this needs a real X session to show a window in" >&2; exit 1; }

WORK_DIR="$(mktemp -d /tmp/cosmos-live-view.XXXXXX)"
cp "$OVMF_VARS_TEMPLATE" "$WORK_DIR/ovmf_vars.fd"

echo "== Booting $DISK_IMAGE in a visible qemu window =="
sudo -E setsid nohup qemu-system-x86_64 \
	-m 4096 -smp 2 -enable-kvm \
	-drive if=pflash,format=raw,readonly=on,file="$OVMF_CODE" \
	-drive if=pflash,format=raw,file="$WORK_DIR/ovmf_vars.fd" \
	-drive file="$DISK_IMAGE",format=raw,if=virtio \
	-netdev user,id=net0 -device virtio-net-pci,netdev=net0 \
	-vga std -display gtk \
	> "$WORK_DIR/qemu.log" 2>&1 < /dev/null &
disown
sleep 2
echo "qemu window launching (logs at $WORK_DIR/qemu.log) - close its window when you're done looking."
