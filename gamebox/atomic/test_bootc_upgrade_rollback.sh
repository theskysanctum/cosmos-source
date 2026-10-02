#!/bin/bash
# Real, automated end-to-end verification of the update ritual UI's own
# bootc commands (see launcher/app.py's _run_bootc_upgrade()/
# _run_bootc_rollback()) - installs a real image from a real registry
# ref onto a scratch disk, boots it in qemu, runs `bootc upgrade --apply`
# for real, confirms the new deployment actually boots with a genuinely
# different image digest, then runs `bootc rollback --apply` and
# confirms the original digest comes back. This is the one verification
# step this project's own plan notes flagged as still open after this
# session's other bootc work: the update-ritual UI's commands were
# proven correct by reading real `bootc status --format json`/`--help`
# output, never actually exercised through a real upgrade+rollback
# cycle end to end.
#
# Not a reimplementation of atomic/install_to_filesystem.py - this
# drives it (with its own new --source-imgref override) the same way
# packaging/deploy_gui/deploy_gui.py drives every other build/test
# script in this project, so a real upgrade/rollback cycle doesn't have
# to be typed out by hand in a terminal each time.
#
# Usage: test_bootc_upgrade_rollback.sh [source_imgref] [disk_image_path] [size]
#   source_imgref   - defaults to docker://ghcr.io/theskysanctum/cosmos:latest
#                     (a REAL registry ref, not the local containers-
#                     storage: default install_to_filesystem.py itself
#                     falls back to - bootc upgrade against a plain
#                     local source has no real "check for newer"
#                     semantics the way a registry ref does, so this
#                     test needs a real one)
#   disk_image_path - defaults to a fresh scratch file under REPO_ROOT/
#                     build/ (NOT /tmp - a real qemu install writes
#                     ~10GB+ of real content through this file, and a
#                     small RAM-backed /tmp can fill up and break the
#                     whole session, confirmed the hard way once already
#                     this project's own history)
#   size            - defaults to 50G (truncate(1) syntax) - real headroom
#                     above install_to_filesystem.py's own 40GiB
#                     ROOT_SIZE_MIB (a deliberate, NOT-yet-live-confirmed
#                     estimate as of this writing - see that script's own
#                     comment on ROOT_SIZE_MIB for why, and re-check this
#                     default stays proportionate once that number is
#                     re-measured for real. Was 80GiB/90G before Flatpak/
#                     Brave were removed from the image entirely; before
#                     that, 32GiB/90G, until a real `bootc upgrade
#                     --apply` failed outright here with "Insufficient
#                     free space" against a 32GiB root already 97% full
#                     from a single deployment alone)
#
# Requires: qemu-system-x86_64, OVMF (edk2-ovmf), sudo, podman, network
# access to pull from the real registry ref given, and a local
# localhost/cosmos-real:latest image already built (Build Bootc
# Image, or atomic/build_bootc_image.sh directly) - real installs run
# via `podman run --privileged` against THAT image's own baked-in
# install_to_filesystem.py, not the bare host (bootc/sgdisk are real
# requirements that script has, neither a host package here), so a
# rebuild is needed any time install_to_filesystem.py itself changes,
# same as every other change to what's baked into the image. Set
# BOOTC_IMAGE_TAG to use a different local tag. Takes real wall-clock
# time - each boot+bootc-upgrade/rollback-triggered-reboot cycle is
# several real minutes, not seconds; this script waits with generous
# (10 minute) timeouts per stage rather than guessing a shorter one and
# racing it.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SOURCE_IMGREF="${1:-docker://ghcr.io/theskysanctum/cosmos:latest}"
DISK_IMAGE="${2:-$REPO_ROOT/build/bootc-upgrade-test-disk.raw}"
SIZE="${3:-50G}"

OVMF_CODE="/usr/share/edk2/ovmf/OVMF_CODE.fd"
OVMF_VARS_TEMPLATE="/usr/share/edk2/ovmf/OVMF_VARS.fd"
WORK_DIR="$(mktemp -d /tmp/cosmos-bootc-upgrade-test.XXXXXX)"
OVMF_VARS="$WORK_DIR/ovmf_vars.fd"
SERIAL_LOG="$WORK_DIR/serial.log"
QEMU_LOG="$WORK_DIR/qemu.log"
MONITOR_SOCK="$WORK_DIR/monitor.sock"

for tool in qemu-system-x86_64 sudo losetup; do
	command -v "$tool" >/dev/null || { echo "$tool not found - install it and retry" >&2; exit 1; }
done
[ -f "$OVMF_CODE" ] || { echo "$OVMF_CODE not found - install edk2-ovmf and retry" >&2; exit 1; }

QEMU_PID=""
PTY_READER_PID=""
LOOP_DEV=""

cleanup() {
	local exit_code=$?
	[ -n "$LOOP_DEV" ] && sudo losetup -d "$LOOP_DEV" 2>/dev/null || true
	# Only tear down the qemu VM/pty reader and clean up WORK_DIR
	# (serial.log/qemu.log included) on a real success - a first real
	# run of this script found the hard way that killing the still-
	# running, still-INSTALLED guest and deleting its logs unconditionally
	# throws away exactly the live system a failure (e.g. a real bootc
	# upgrade timing out because nothing newer had been pushed to GHCR
	# YET) most needs: DISK_IMAGE itself is untouched either way, so a
	# retry could always reinstall from scratch, but being able to just
	# reboot and manually continue against the SAME already-installed
	# disk (or even the still-running VM, if it's still up) is far
	# cheaper than repeating the whole install.
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
# sudo, not plain truncate - a re-run against the same DISK_IMAGE path
# finds a root-owned file left over from the sudo losetup/podman/mount
# calls below (confirmed live: a plain unprivileged truncate against an
# existing root-owned file fails outright with Permission denied).
sudo truncate -s "$SIZE" "$DISK_IMAGE"
sudo chown "$(id -u):$(id -g)" "$DISK_IMAGE"

echo "== Installing $SOURCE_IMGREF onto a scratch disk =="
# Run install_to_filesystem.py INSIDE the built image, not on the bare
# host - `bootc`/`sgdisk` (both real requirements of that script) aren't
# host packages here, only present inside the image itself. This is the
# same real invocation this project's own testing already established:
# the image doubles as its own installer medium via `podman run
# --privileged`, with -v /dev:/dev required (confirmed live elsewhere
# in this project - --privileged alone does NOT give the container a
# live view of the host's /dev, so partition nodes never appear
# without this explicit bind mount).
IMAGE_TAG="${BOOTC_IMAGE_TAG:-localhost/cosmos-real:latest}"
sudo podman image exists "$IMAGE_TAG" || {
	echo "$IMAGE_TAG not found locally - build it first (Build Bootc Image)" >&2
	exit 1
}
# A real registry source_imgref needs real GHCR auth for `bootc install
# to-filesystem` (run inside the container below) to pull it -
# confirmed live: "unable to retrieve auth token: invalid username/
# password: unauthorized" against a private package with none. `man
# ostree-container-auth` (bootc's own real doc, not podman's): as root,
# bootc/ostree's own registry client checks /etc/ostree/auth.json, then
# /run/ostree/auth.json, then /usr/lib/ostree/auth.json - a genuinely
# DIFFERENT path convention than podman/skopeo's own containers/image
# auth.json locations (confirmed the hard way: mounting the host's
# cached podman auth.json to ITS OWN path inside the container had no
# effect at all - bootc never looked there). Reuses whatever
# credentials `sudo podman login ghcr.io` already cached on this host
# (containers-auth(5) is the same underlying file FORMAT either way,
# just a different location), bind-mounted read-only at bootc's own
# expected /run/ostree/auth.json path inside the container instead.
HOST_AUTH_FILE="/run/containers/0/auth.json"
BOOTC_AUTH_PATH="/run/ostree/auth.json"
AUTH_MOUNT_ARGS=()
if [[ "$SOURCE_IMGREF" == docker://* ]] && [ -f "$HOST_AUTH_FILE" ]; then
	AUTH_MOUNT_ARGS=(-v "$HOST_AUTH_FILE:$BOOTC_AUTH_PATH:ro")
elif [[ "$SOURCE_IMGREF" == docker://* ]]; then
	echo "WARNING: $SOURCE_IMGREF is a real registry ref but no cached auth found at $HOST_AUTH_FILE - run 'sudo podman login ghcr.io' first if the package is private." >&2
fi
LOOP_DEV="$(sudo losetup -fP --show "$DISK_IMAGE")"
sudo podman run --rm --privileged \
	-v /var/lib/containers/storage:/var/lib/containers/storage \
	-v /dev:/dev \
	"${AUTH_MOUNT_ARGS[@]}" \
	"$IMAGE_TAG" \
	python3 /var/lib/cosmos/boot/install_to_filesystem.py "$LOOP_DEV" \
	--yes-i-am-sure --source-imgref "$SOURCE_IMGREF"
sudo losetup -d "$LOOP_DEV"
LOOP_DEV=""

echo "== Patching the installed BLS entry for a real serial console =="
# bootupd's own default grub.cfg has no console= kernel argument (a
# known, documented gap - see install_to_filesystem.py's own module
# docstring), so nothing reaches serial without this: an offline mount,
# not a boot-time edit, since this needs to happen before qemu ever
# starts for the FIRST real console output to be captured at all.
MOUNT_DIR="$WORK_DIR/root-mount"
mkdir -p "$MOUNT_DIR"
LOOP_DEV="$(sudo losetup -fP --show "$DISK_IMAGE")"
sudo mount "${LOOP_DEV}p3" "$MOUNT_DIR"
BLS_ENTRY="$(sudo find "$MOUNT_DIR/boot/loader.1/entries" -type f | head -1)"
[ -n "$BLS_ENTRY" ] || { echo "no BLS entry found under $MOUNT_DIR/boot/loader.1/entries" >&2; exit 1; }
sudo sed -i 's/^options root=/options console=ttyS0,115200n8 console=tty0 root=/' "$BLS_ENTRY"

if [[ "$SOURCE_IMGREF" == docker://* ]] && [ -f "$HOST_AUTH_FILE" ]; then
	echo "== Provisioning GHCR auth for the booted system's own bootc upgrade/rollback calls =="
	# `man ostree-container-auth` (bootc's own real container-registry
	# auth doc, not guessed): as root, bootc/ostree checks
	# /etc/ostree/auth.json first. Written directly onto the target
	# disk's OWN real (per-deployment, ostree-committed) /etc here - NOT
	# baked into the shared image itself in atomic/Containerfile, which
	# would leak these credentials into a public image. This is a real
	# credential-provisioning step a genuine appliance install would need
	# its own real mechanism for (out of scope for this test script);
	# here it's just this test's own local install, so reusing whatever
	# this host already has cached is the pragmatic, real-enough choice.
	DEPLOY_DIR="$(sudo find "$MOUNT_DIR/ostree/deploy/default/deploy" -maxdepth 1 -type d -name '*.0' | head -1)"
	[ -n "$DEPLOY_DIR" ] || { echo "no ostree deployment found under $MOUNT_DIR/ostree/deploy/default/deploy" >&2; exit 1; }
	sudo mkdir -p "$DEPLOY_DIR/etc/ostree"
	sudo cp "$HOST_AUTH_FILE" "$DEPLOY_DIR/etc/ostree/auth.json"
fi

sudo umount "$MOUNT_DIR"
sudo losetup -d "$LOOP_DEV"
LOOP_DEV=""

boot_vm() {
	rm -f "$SERIAL_LOG" "$QEMU_LOG" "$MONITOR_SOCK"
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
	local pty
	pty="$(grep -oP '(?<=char device redirected to )/dev/pts/\d+' "$QEMU_LOG" | head -1)"
	[ -n "$pty" ] || { echo "qemu didn't report a serial pty - see $QEMU_LOG" >&2; cat "$QEMU_LOG" >&2; exit 1; }
	sudo chmod 666 "$pty"
	QEMU_PID="$(pgrep -f "qemu-system-x86_64.*$DISK_IMAGE" | tail -1)"
	sudo setsid nohup cat "$pty" > "$SERIAL_LOG" 2>&1 &
	disown
	PTY_READER_PID=$!
	SERIAL_PTY="$pty"
}

wait_for_login() {
	local timeout_s="${1:-600}"
	local waited=0
	while ! grep -q "login:" "$SERIAL_LOG" 2>/dev/null; do
		sleep 5
		waited=$((waited + 5))
		if [ "$waited" -ge "$timeout_s" ]; then
			echo "FAIL: timed out after ${timeout_s}s waiting for a login prompt - see $SERIAL_LOG" >&2
			exit 1
		fi
	done
}

send_cmd() {
	printf '%s\n' "$1" | sudo tee "$SERIAL_PTY" >/dev/null
}

read_bootc_digest() {
	# Runs `bootc status --format json` over the real serial console and
	# pulls out just the booted image digest - the one field that
	# genuinely changes across a real upgrade/rollback, so this is the
	# actual pass/fail signal for both stages below. `ls /boot` first is
	# not optional here - a real, open upstream bootc bug (bootc-dev/
	# bootc#2402, hit live running this exact test) makes any bootc
	# command touching /boot fail with an opaque "opendir(boot):
	# Operation not permitted" once the systemd-gpt-auto-generator
	# automount backing /boot has idled out (120s after boot) - bootc's
	# own access (re-exec'd under `unshare -m`) doesn't trigger that
	# automount the way a normal command does, confirmed live that this
	# `ls` immediately beforehand is the documented, working fix.
	send_cmd "ls /boot >/dev/null 2>&1"
	sleep 1
	local marker="BOOTC_STATUS_$$_$RANDOM"
	local before_lines
	before_lines="$(wc -l < "$SERIAL_LOG")"
	send_cmd "echo ${marker}_START; bootc status --format json; echo ${marker}_END"
	local waited=0
	while ! tail -n "+$((before_lines + 1))" "$SERIAL_LOG" | grep -q "${marker}_END"; do
		sleep 3
		waited=$((waited + 3))
		[ "$waited" -ge 60 ] && { echo "FAIL: bootc status never returned - see $SERIAL_LOG" >&2; exit 1; }
	done
	tail -n "+$((before_lines + 1))" "$SERIAL_LOG" \
		| sed -n "/${marker}_START/,/${marker}_END/p" \
		| grep -oP '"imageDigest":"sha256:[a-f0-9]+"' | head -1
}

echo "== Booting the freshly installed disk =="
boot_vm
wait_for_login 600
echo "-- boot reached login --"

INITIAL_DIGEST="$(read_bootc_digest)"
echo "-- initial booted digest: $INITIAL_DIGEST --"
[ -n "$INITIAL_DIGEST" ] || { echo "FAIL: couldn't read the initial bootc status digest" >&2; exit 1; }

echo "== Running bootc upgrade --apply (reboots automatically on success) =="
# ls /boot first - see read_bootc_digest()'s own comment on bootc-dev/
# bootc#2402 (same real bug, same fix, needed here too).
send_cmd "ls /boot >/dev/null 2>&1 && bootc upgrade --apply"
# --apply reboots the machine itself - the qemu PROCESS stays up (same
# virtual hardware, not a real power cycle) but the guest kernel goes
# down and back up, so this waits for a SECOND login prompt appended
# further down the same growing serial log, not a fresh boot_vm call.
BEFORE_UPGRADE_LINES="$(wc -l < "$SERIAL_LOG")"
waited=0
while [ "$(tail -n "+$((BEFORE_UPGRADE_LINES + 1))" "$SERIAL_LOG" | grep -c "login:")" -lt 1 ]; do
	sleep 10
	waited=$((waited + 10))
	if [ "$waited" -ge 600 ]; then
		echo "FAIL: timed out after 600s waiting for the post-upgrade reboot's login prompt - see $SERIAL_LOG" >&2
		exit 1
	fi
done
echo "-- post-upgrade reboot reached login --"

UPGRADED_DIGEST="$(read_bootc_digest)"
echo "-- post-upgrade booted digest: $UPGRADED_DIGEST --"
if [ "$UPGRADED_DIGEST" = "$INITIAL_DIGEST" ]; then
	echo "FAIL: digest unchanged after bootc upgrade --apply - either nothing newer was available at $SOURCE_IMGREF, or the upgrade didn't really apply. Push a new build to GHCR and re-run if you expected a real change." >&2
	exit 1
fi
echo "PASS: bootc upgrade --apply booted a genuinely different image ($INITIAL_DIGEST -> $UPGRADED_DIGEST)"

echo "== Running bootc rollback --apply (reboots automatically on success) =="
send_cmd "ls /boot >/dev/null 2>&1 && bootc rollback --apply"
BEFORE_ROLLBACK_LINES="$(wc -l < "$SERIAL_LOG")"
waited=0
while [ "$(tail -n "+$((BEFORE_ROLLBACK_LINES + 1))" "$SERIAL_LOG" | grep -c "login:")" -lt 1 ]; do
	sleep 10
	waited=$((waited + 10))
	if [ "$waited" -ge 600 ]; then
		echo "FAIL: timed out after 600s waiting for the post-rollback reboot's login prompt - see $SERIAL_LOG" >&2
		exit 1
	fi
done
echo "-- post-rollback reboot reached login --"

ROLLED_BACK_DIGEST="$(read_bootc_digest)"
echo "-- post-rollback booted digest: $ROLLED_BACK_DIGEST --"
if [ "$ROLLED_BACK_DIGEST" != "$INITIAL_DIGEST" ]; then
	echo "FAIL: rollback didn't restore the original digest (expected $INITIAL_DIGEST, got $ROLLED_BACK_DIGEST)" >&2
	exit 1
fi
echo "PASS: bootc rollback --apply correctly restored the original image ($ROLLED_BACK_DIGEST)"

echo "== ALL CHECKS PASSED: bootc upgrade --apply and bootc rollback --apply both verified end to end =="
