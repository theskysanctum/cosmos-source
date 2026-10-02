#!/bin/bash
# Real, automated install+boot-only verification of the LOCAL bootc image
# (localhost/cosmos-real:latest) - installs it onto a scratch disk and
# confirms it actually boots to a real login prompt over a real serial
# console, then greps dmesg/journalctl for the nvidia kernel module's own
# load status and for any other boot-time errors.
#
# This is deliberately the install+boot half only, with none of
# test_bootc_upgrade_rollback.sh's own upgrade/rollback cycle (that one
# needs a real GHCR push to have something newer to upgrade to) - for
# confirming a freshly built local image boots cleanly after Containerfile
# changes (new firmware packages, the proprietary NVIDIA driver build,
# native Steam, the Flatpak fix, etc.) without needing to push anywhere
# first. Reuses the exact same disk-creation/install/BLS-patch/qemu
# mechanisms as that script - see its own comments for the reasoning
# behind each one (not repeated here).
#
# Usage: test_bootc_boot.sh [disk_image_path] [size]
# Requires: qemu-system-x86_64, OVMF (edk2-ovmf), sudo, podman, and a local
# localhost/cosmos-real:latest image already built (Build Bootc Image,
# or atomic/build_bootc_image.sh directly). Set BOOTC_IMAGE_TAG to use a
# different local tag.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DISK_IMAGE="${1:-$REPO_ROOT/build/cosmos-boot-test-disk.raw}"
SIZE="${2:-50G}"
IMAGE_TAG="${BOOTC_IMAGE_TAG:-localhost/cosmos-real:latest}"

OVMF_CODE="/usr/share/edk2/ovmf/OVMF_CODE.fd"
OVMF_VARS_TEMPLATE="/usr/share/edk2/ovmf/OVMF_VARS.fd"
WORK_DIR="$(mktemp -d /tmp/cosmos-boot-test.XXXXXX)"
OVMF_VARS="$WORK_DIR/ovmf_vars.fd"
SERIAL_LOG="$WORK_DIR/serial.log"
QEMU_LOG="$WORK_DIR/qemu.log"
MONITOR_SOCK="$WORK_DIR/monitor.sock"

for tool in qemu-system-x86_64 sudo losetup; do
	command -v "$tool" >/dev/null || { echo "$tool not found - install it and retry" >&2; exit 1; }
done
[ -f "$OVMF_CODE" ] || { echo "$OVMF_CODE not found - install edk2-ovmf and retry" >&2; exit 1; }
sudo podman image exists "$IMAGE_TAG" || {
	echo "$IMAGE_TAG not found locally - build it first (Build Bootc Image)" >&2
	exit 1
}

QEMU_PID=""
PTY_READER_PID=""
LOOP_DEV=""

cleanup() {
	local exit_code=$?
	[ -n "$LOOP_DEV" ] && sudo losetup -d "$LOOP_DEV" 2>/dev/null || true
	if [ "$exit_code" -eq 0 ]; then
		[ -n "$QEMU_PID" ] && sudo kill -9 "$QEMU_PID" 2>/dev/null || true
		[ -n "$PTY_READER_PID" ] && sudo kill -9 "$PTY_READER_PID" 2>/dev/null || true
		rm -rf "$WORK_DIR"
	else
		echo "Left running for manual debugging: qemu pid $QEMU_PID, logs at $WORK_DIR" >&2
	fi
}
trap cleanup EXIT

echo "== Creating blank $SIZE disk at $DISK_IMAGE =="
mkdir -p "$(dirname "$DISK_IMAGE")"
sudo truncate -s "$SIZE" "$DISK_IMAGE"
sudo chown "$(id -u):$(id -g)" "$DISK_IMAGE"

echo "== Installing $IMAGE_TAG onto the scratch disk =="
LOOP_DEV="$(sudo losetup -fP --show "$DISK_IMAGE")"
sudo podman run --rm --privileged \
	-v /var/lib/containers/storage:/var/lib/containers/storage \
	-v /dev:/dev \
	"$IMAGE_TAG" \
	python3 /var/lib/cosmos/boot/install_to_filesystem.py "$LOOP_DEV" --yes-i-am-sure
sudo losetup -d "$LOOP_DEV"
LOOP_DEV=""

echo "== Patching the installed BLS entry for a real serial console =="
MOUNT_DIR="$WORK_DIR/root-mount"
mkdir -p "$MOUNT_DIR"
LOOP_DEV="$(sudo losetup -fP --show "$DISK_IMAGE")"
sudo mount "${LOOP_DEV}p3" "$MOUNT_DIR"
BLS_ENTRY="$(sudo find "$MOUNT_DIR/boot/loader.1/entries" -type f | head -1)"
[ -n "$BLS_ENTRY" ] || { echo "no BLS entry found under $MOUNT_DIR/boot/loader.1/entries" >&2; exit 1; }
sudo sed -i 's/^options root=/options console=ttyS0,115200n8 console=tty0 root=/' "$BLS_ENTRY"
sudo umount "$MOUNT_DIR"
sudo losetup -d "$LOOP_DEV"
LOOP_DEV=""

echo "== Booting the freshly installed disk =="
cp "$OVMF_VARS_TEMPLATE" "$OVMF_VARS"
sudo setsid nohup qemu-system-x86_64 \
	-m 4096 -smp 2 -enable-kvm \
	-drive if=pflash,format=raw,readonly=on,file="$OVMF_CODE" \
	-drive if=pflash,format=raw,file="$OVMF_VARS" \
	-drive file="$DISK_IMAGE",format=raw,if=virtio \
	-netdev user,id=net0 -device virtio-net-pci,netdev=net0 \
	-vga std -display none \
	-monitor unix:"$MONITOR_SOCK",server,nowait \
	-serial pty \
	> "$QEMU_LOG" 2>&1 < /dev/null &
disown
sleep 2
PTY="$(grep -oP '(?<=char device redirected to )/dev/pts/\d+' "$QEMU_LOG" | head -1)"
[ -n "$PTY" ] || { echo "qemu didn't report a serial pty - see $QEMU_LOG" >&2; cat "$QEMU_LOG" >&2; exit 1; }
sudo chmod 666 "$PTY"
QEMU_PID="$(pgrep -f "qemu-system-x86_64.*$DISK_IMAGE" | tail -1)"
sudo setsid nohup cat "$PTY" > "$SERIAL_LOG" 2>&1 &
disown
PTY_READER_PID=$!

echo "== Waiting up to 600s for a login prompt =="
waited=0
while ! grep -q "login:" "$SERIAL_LOG" 2>/dev/null; do
	sleep 5
	waited=$((waited + 5))
	if [ "$waited" -ge 600 ]; then
		echo "FAIL: timed out after ${waited}s waiting for a login prompt - see $SERIAL_LOG" >&2
		tail -60 "$SERIAL_LOG" >&2
		exit 1
	fi
done
echo "PASS: reached login prompt after ${waited}s"

echo "== Checking dmesg/journalctl over serial for nvidia load status and boot errors =="
send_cmd() { printf '%s\n' "$1" | sudo tee "$PTY" >/dev/null; }
before_lines="$(wc -l < "$SERIAL_LOG")"
marker="BOOTCHECK_$$_$RANDOM"
send_cmd "ls /boot >/dev/null 2>&1"
sleep 1
send_cmd "echo ${marker}_START; echo ---NVIDIA---; dmesg | grep -i nvidia; echo ---FAILED-UNITS---; systemctl --failed --no-legend; echo ---ERRORS---; journalctl -p err -b --no-pager | tail -40; echo ${marker}_END"
waited=0
while ! tail -n "+$((before_lines + 1))" "$SERIAL_LOG" | grep -q "${marker}_END"; do
	sleep 3
	waited=$((waited + 3))
	[ "$waited" -ge 60 ] && { echo "FAIL: post-boot check never returned - see $SERIAL_LOG" >&2; exit 1; }
done
tail -n "+$((before_lines + 1))" "$SERIAL_LOG" | sed -n "/${marker}_START/,/${marker}_END/p"

# The most severe real bug this script has caught was NOT a failed
# systemd unit - it was launcher/run.py's own App() constructor raising
# an uncaught exception, which startx.sh's own respawn loop silently
# papered over by restarting the whole graphical chain every ~4.6s
# forever. getty@tty1.service stayed "active" the entire time (nothing
# above would have caught it), and the actual traceback only ever
# reached the raw tty1 console, never a log file or journalctl -p err.
# So: explicitly confirm the launcher process itself is alive, note its
# PID, wait past one full respawn-loop period, and confirm the SAME PID
# is still there - a crash-loop respawns with a NEW pid every cycle,
# a healthy launcher keeps the same one.
echo "== Checking launcher/run.py is alive and NOT crash-looping =="
# A bare numeric pgrep line, extracted with a `^\d+$` anchor, is fragile
# against this serial capture's own stray terminal control sequences
# (OSC prompt-marker codes, bracketed-paste toggles) that can land on
# the same line as real output - confirmed live this caused a false
# "not running" FAIL against a launcher that was actually alive and
# stable. A labeled key=value line tolerates leading noise on the line
# since \K only anchors the "PID=" prefix itself, not the whole line.
before_lines="$(wc -l < "$SERIAL_LOG")"
marker="LAUNCHERCHECK1_$$_$RANDOM"
send_cmd "echo ${marker}_START; echo LAUNCHERPID=\$(pgrep -f 'cosmos/launcher/run.py' | tail -1); echo ${marker}_END"
waited=0
while ! tail -n "+$((before_lines + 1))" "$SERIAL_LOG" | grep -q "${marker}_END"; do
	sleep 2
	waited=$((waited + 2))
	[ "$waited" -ge 30 ] && { echo "FAIL: launcher pid check never returned - see $SERIAL_LOG" >&2; exit 1; }
done
LAUNCHER_PID_1="$(tail -n "+$((before_lines + 1))" "$SERIAL_LOG" | sed -n "/${marker}_START/,/${marker}_END/p" | grep -oP 'LAUNCHERPID=\K\d+' | head -1)"
[ -n "$LAUNCHER_PID_1" ] || { echo "FAIL: launcher/run.py is not running at all - see $SERIAL_LOG" >&2; exit 1; }

sleep 10

before_lines="$(wc -l < "$SERIAL_LOG")"
marker="LAUNCHERCHECK2_$$_$RANDOM"
send_cmd "echo ${marker}_START; echo LAUNCHERPID=\$(pgrep -f 'cosmos/launcher/run.py' | tail -1); echo ${marker}_END"
waited=0
while ! tail -n "+$((before_lines + 1))" "$SERIAL_LOG" | grep -q "${marker}_END"; do
	sleep 2
	waited=$((waited + 2))
	[ "$waited" -ge 30 ] && { echo "FAIL: launcher pid re-check never returned - see $SERIAL_LOG" >&2; exit 1; }
done
LAUNCHER_PID_2="$(tail -n "+$((before_lines + 1))" "$SERIAL_LOG" | sed -n "/${marker}_START/,/${marker}_END/p" | grep -oP 'LAUNCHERPID=\K\d+' | head -1)"
[ -n "$LAUNCHER_PID_2" ] || { echo "FAIL: launcher/run.py died and did not stay up 10s later - see $SERIAL_LOG" >&2; exit 1; }
if [ "$LAUNCHER_PID_1" != "$LAUNCHER_PID_2" ]; then
	echo "FAIL: launcher/run.py pid changed ($LAUNCHER_PID_1 -> $LAUNCHER_PID_2) 10s apart - it's crash-looping, not just running" >&2
	exit 1
fi
echo "PASS: launcher/run.py stayed up (pid $LAUNCHER_PID_1) across a 10s window - not crash-looping"

echo "== ALL CHECKS PASSED: local bootc image installs and boots cleanly, launcher stable =="
