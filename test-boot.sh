#!/usr/bin/env bash
# Real, automated install+boot verification of the LOCAL bootc image
# (localhost/cosmos:latest) - installs it onto a scratch disk,
# confirms the real deployment actually contains every package this
# spin is supposed to ship, then boots it over a real serial console
# and confirms it reaches a login prompt. Stops at the login prompt:
# this image's one account, user, has its password locked at build
# time on purpose (see README.md), so there's nothing to log in as.
#
# Usage: test-boot.sh [--dry-run] [--boot-only] [disk_image_path] [size]
#   --dry-run    run the prerequisite checks, print the plan, exit
#   --boot-only  skip the install - just boot an already-installed disk
#                (the one install_in_disposable_vm.sh copies back out)
#                and wait for its login prompt. Booting never touches
#                host UEFI, so this is safe to run directly on the host.
# Set BOOTC_IMAGE_TAG to use a different local tag.
#
# IMPORTANT - real UEFI hardware: the install step runs a --privileged
# container whose bootupd efibootmgr calls write straight to this
# machine's real UEFI NVRAM (confirmed live - it overwrote this host's
# real "Fedora" boot entry once; --security-opt mask=... doesn't survive
# --privileged). So a full install refuses to run unless
# COSMOS_ALLOW_EFI_NVRAM_CHANGES=1 is set, which
# install_in_disposable_vm.sh does inside its throwaway VM (its own
# private OVMF_VARS - the write there is expected, and required for
# bootupd to populate the ESP at all). Never set it on real hardware.
set -euo pipefail

DRY_RUN=0
BOOT_ONLY=0
while [[ "${1:-}" == --* ]]; do
    case "$1" in
        --dry-run) DRY_RUN=1 ;;
        --boot-only) BOOT_ONLY=1 ;;
        *) echo "Unknown option: $1" >&2; exit 1 ;;
    esac
    shift
done

# No TERM when run over ssh inside the disposable VM
[[ -n "${TERM:-}" ]] && clear || true
echo "=============================================="
echo "   CosmOS — Test Boot (bootc image)"
echo "=============================================="
echo

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DISK_IMAGE="${1:-$REPO_ROOT/build/test-disk.raw}"
SIZE="${2:-90G}"
IMAGE_TAG="${BOOTC_IMAGE_TAG:-localhost/cosmos:latest}"

OVMF_CODE="/usr/share/edk2/ovmf/OVMF_CODE.fd"
OVMF_VARS_TEMPLATE="/usr/share/edk2/ovmf/OVMF_VARS.fd"

for tool in qemu-system-x86_64 sudo losetup podman; do
    command -v "$tool" >/dev/null || { echo "$tool not found - install it and retry" >&2; exit 1; }
done
[ -f "$OVMF_CODE" ] || { echo "$OVMF_CODE not found - install edk2-ovmf and retry" >&2; exit 1; }
[ -f "$OVMF_VARS_TEMPLATE" ] || { echo "$OVMF_VARS_TEMPLATE not found - install edk2-ovmf and retry" >&2; exit 1; }

if [ "$BOOT_ONLY" -eq 1 ]; then
    [ -f "$DISK_IMAGE" ] || {
        echo "$DISK_IMAGE doesn't exist - run \"Install in disposable VM\" first" >&2
        exit 1
    }
else
    sudo podman image exists "$IMAGE_TAG" || {
        echo "$IMAGE_TAG not found locally - build it first (build-bootc.sh)" >&2
        exit 1
    }
    if [ "${COSMOS_ALLOW_EFI_NVRAM_CHANGES:-0}" != "1" ]; then
        echo "Refusing to install: this step writes to real UEFI NVRAM on this machine." >&2
        echo "Use \"Install in disposable VM\" instead (it runs this script inside a VM with" >&2
        echo "COSMOS_ALLOW_EFI_NVRAM_CHANGES=1), then --boot-only to boot the result." >&2
        exit 1
    fi
fi

if [ "$DRY_RUN" -eq 1 ]; then
    echo "[dry-run] prerequisites OK"
    [ "$BOOT_ONLY" -eq 0 ] && echo "[dry-run] would install $IMAGE_TAG onto a fresh $SIZE disk at $DISK_IMAGE, verify its contents and patch in console=ttyS0"
    echo "[dry-run] would boot $DISK_IMAGE under OVMF and wait up to 300s for a serial login prompt"
    exit 0
fi

WORK_DIR="$(mktemp -d /tmp/cosmos-boot-test.XXXXXX)"
OVMF_VARS="$WORK_DIR/ovmf_vars.fd"
SERIAL_LOG="$WORK_DIR/serial.log"

QEMU_PID=""
LOOP_DEV=""
MOUNT_DIR=""

# Always stops the VM (a leftover qemu would keep holding the disk and
# RAM after the menu moves on); keeps the logs if anything failed.
cleanup() {
    local exit_code=$?
    [ -n "$MOUNT_DIR" ] && sudo umount "$MOUNT_DIR" 2>/dev/null || true
    [ -n "$LOOP_DEV" ] && sudo losetup -d "$LOOP_DEV" 2>/dev/null || true
    [ -n "$QEMU_PID" ] && sudo kill "$QEMU_PID" 2>/dev/null || true
    if [ "$exit_code" -eq 0 ]; then
        sudo rm -rf "$WORK_DIR"
    else
        echo "Logs kept for debugging at $WORK_DIR" >&2
    fi
}
trap cleanup EXIT

if [ "$BOOT_ONLY" -eq 0 ]; then
    echo "== Creating blank $SIZE disk at $DISK_IMAGE =="
    mkdir -p "$(dirname "$DISK_IMAGE")"
    sudo rm -f "$DISK_IMAGE"
    sudo truncate -s "$SIZE" "$DISK_IMAGE"
    sudo chown "$(id -u):$(id -g)" "$DISK_IMAGE"

    echo "== Installing $IMAGE_TAG onto the scratch disk =="
    LOOP_DEV="$(sudo losetup -fP --show "$DISK_IMAGE")"
    sudo podman run --rm --privileged --pid=host \
        -v /var/lib/containers/storage:/var/lib/containers/storage \
        -v /dev:/dev \
        "$IMAGE_TAG" \
        bootc install to-disk --wipe --filesystem ext4 "$LOOP_DEV"

    echo "== Verifying every package/config this spin ships landed on the real deployment =="
    MOUNT_DIR="$WORK_DIR/root-mount"
    mkdir -p "$MOUNT_DIR"
    sudo mount "${LOOP_DEV}p3" "$MOUNT_DIR"
    DEPLOY_DIR="$(sudo find "$MOUNT_DIR/ostree/deploy/default/deploy" -maxdepth 1 -type d -name '*.0')"
    [ -n "$DEPLOY_DIR" ] || { echo "FAIL: no ostree deployment found under $MOUNT_DIR" >&2; exit 1; }

    # `-e`/`-L` together, not a bare `-e`: several of these (steam, real,
    # confirmed live) are absolute symlinks (e.g. /usr/bin/steam ->
    # /usr/lib/steam/bin_steam.sh) - since this check runs on the test
    # RUNNER's own root, not chrooted into DEPLOY_DIR, a bare `-e` follows
    # that symlink against the runner's own filesystem instead of the
    # mounted deployment's, and reports missing even when the target is
    # genuinely present inside the deployment. `-L` catches the symlink
    # entry itself regardless of where its target resolves.
    for bin in lightdm steam brave-browser git gh wget curl yt-dlp; do
        { sudo test -e "$DEPLOY_DIR/usr/bin/$bin" || sudo test -L "$DEPLOY_DIR/usr/bin/$bin"; } || {
            echo "FAIL: /usr/bin/$bin missing from the installed deployment" >&2
            exit 1
        }
    done
    # The developer tools (IDEs, editors, toolchains) are Expansion Packs now, not part of the image, so they are
    # deliberately not checked here. The CosmOS programs and the Shop's own dependencies are.
    for path in usr/local/bin/cosmos-wm usr/local/bin/cosmos-session usr/local/bin/cosmos-autologin \
        usr/local/bin/cosmos-greeter usr/local/bin/cosmos-profiles usr/local/bin/cosmos-lockscreen \
        usr/local/bin/cosmos-vm-manager usr/local/bin/cosmos-launcher \
        usr/local/bin/cosmos-shop opt/am/APP-MANAGER; do
        sudo test -x "$DEPLOY_DIR/$path" || {
            echo "FAIL: /$path missing (or not executable) from the installed deployment" >&2
            exit 1
        }
    done
    # Imported by cosmos-bar/cosmos-launcher, never run directly - so it
    # ships 0644 and gets a plain existence check, not the -x one above.
    sudo test -f "$DEPLOY_DIR/usr/local/bin/cosmos_favorites.py" || {
        echo "FAIL: /usr/local/bin/cosmos_favorites.py missing from the installed deployment" >&2
        exit 1
    }
    # cosmos-wm's own xsessions entry, cosmos-greeter's own xgreeters
    # entry, and the lightdm.conf.d drop-in defaulting to both (see
    # Containerfile's own comment on all three) - lightdm-gtk-greeter's own
    # xgreeters entry is deliberately NOT checked for here, its presence/
    # absence isn't this spin's own doing either way (i3's own former
    # package-provided xsessions entry no longer exists at all - i3 itself
    # was removed entirely, see the Containerfile's own comment on that).
    sudo test -e "$DEPLOY_DIR/usr/share/xsessions/cosmos.desktop" || {
        echo "FAIL: /usr/share/xsessions/cosmos.desktop missing from the installed deployment" >&2
        exit 1
    }
    sudo test -e "$DEPLOY_DIR/usr/share/xgreeters/cosmos-greeter.desktop" || {
        echo "FAIL: /usr/share/xgreeters/cosmos-greeter.desktop missing from the installed deployment" >&2
        exit 1
    }
    sudo grep -q '^user-session=cosmos$' "$DEPLOY_DIR/etc/lightdm/lightdm.conf.d/50-cosmos-session.conf" || {
        echo "FAIL: lightdm.conf.d/50-cosmos-session.conf doesn't default to the cosmos session" >&2
        exit 1
    }
    sudo grep -q '^greeter-session=cosmos-greeter$' "$DEPLOY_DIR/etc/lightdm/lightdm.conf.d/50-cosmos-session.conf" || {
        echo "FAIL: lightdm.conf.d/50-cosmos-session.conf doesn't default to cosmos-greeter" >&2
        exit 1
    }
    sudo test -e "$DEPLOY_DIR/etc/pam.d/cosmos-lockscreen" || {
        echo "FAIL: /etc/pam.d/cosmos-lockscreen missing from the installed deployment" >&2
        exit 1
    }
    # ia/legendary land under /usr/local/bin as pip console-scripts (a
    # root-run `pip3 install` with no --user and no venv, confirmed live on
    # this exact Python: see sysconfig.get_path('scripts')), not /usr/bin
    # like the dnf-installed binaries above. gogdl lands there too but
    # isn't a pip package at all (confirmed live it doesn't exist on PyPI)
    # - it's upstream's own prebuilt GitHub-release binary, curl'd straight
    # to that same path. yt-dlp itself moved to the dnf-tool check above -
    # Fedora 44 now packages it natively (a Weak Dependency pulled in by
    # the sound-and-video group, confirmed live via `dnf repoquery
    # --installed --qf '%{reason}'`), which pip's own "Requirement already
    # satisfied" no-op then silently skips creating a console-script for -
    # confirmed live this exact interaction left /usr/local/bin/yt-dlp
    # missing even though `pip3 install yt-dlp` never errored.
    for bin in ia legendary gogdl; do
        { sudo test -e "$DEPLOY_DIR/usr/local/bin/$bin" || sudo test -L "$DEPLOY_DIR/usr/local/bin/$bin"; } || {
            echo "FAIL: /usr/local/bin/$bin missing from the installed deployment" >&2
            exit 1
        }
    done
    sudo test -f "$DEPLOY_DIR/etc/brave/policies/managed/extensions.json" || {
        echo "FAIL: Bitwarden's Brave managed-policy file didn't land on the installed deployment" >&2
        exit 1
    }
    # The one that actually matters: the stock default here points at a
    # background file this image never ships, which segfaults the greeter
    # on session start (confirmed live) rather than just looking wrong -
    # a plain existence check wouldn't catch a REGRESSION back to that
    # stock default, so this greps for the real fix's own content. Used to
    # be a literal solid color (`background=#000000`) before this session's
    # theming pass replaced it with the real, always-shipped
    # cosmos-galaxy.jpg background - either form is a real fix for the
    # actual segfault (a real file/color vs. the missing stock default),
    # so this just needs to confirm it's NOT the stock default any more,
    # not one exact literal value.
    sudo grep -qE '^background=(#[0-9a-fA-F]{6}|/usr/share/backgrounds/cosmos-galaxy\.jpg)$' "$DEPLOY_DIR/etc/lightdm/lightdm-gtk-greeter.conf" || {
        echo "FAIL: lightdm-gtk-greeter.conf doesn't have the background override - the stock default here segfaults the greeter on session start (see config/lightdm/lightdm-gtk-greeter.conf's own comment)" >&2
        exit 1
    }
    sudo grep -q '^user:' "$DEPLOY_DIR/etc/passwd" || {
        echo "FAIL: the user account didn't land on the installed deployment" >&2
        exit 1
    }
    # An empty password hash is the whole point (one shared "user" account,
    # logged in automatically by cosmos-autologin; people are profiles, not
    # accounts, and no plaintext password is baked in) - a real hash here would
    # mean the Containerfile's own `usermod -p ''` regressed, and a locked
    # one ("!") would make the automatic login fall back to the login screen.
    sudo awk -F: '$1 == "user" { print $2 }' "$DEPLOY_DIR/etc/shadow" | grep -qE '^$' || {
        echo "FAIL: user's password hash isn't empty - cosmos-autologin depends on this" >&2
        exit 1
    }
    # The home dir itself lives under the shared /var area (Fedora's stock
    # /home -> var/home symlink), not inside this per-deployment DEPLOY_DIR
    # - so anything checked in the user's home is looked for under var/, not under this DEPLOY_DIR.
    sudo test -f "$MOUNT_DIR/ostree/deploy/default/var/home/user/.config/dunst/dunstrc" || {
        echo "FAIL: user's home dir wasn't seeded from /etc/skel (useradd -m ran before the skel COPY steps?)" >&2
        exit 1
    }
    for symlink_target in cosmos:/var/lib/cosmos userdata:/var/userdata; do
        name="${symlink_target%%:*}"
        target="${symlink_target#*:}"
        link="$(sudo readlink "$DEPLOY_DIR/$name")"
        [ "$link" = "$target" ] || {
            echo "FAIL: /$name should symlink to $target, found '$link' instead" >&2
            exit 1
        }
    done
    sudo test -L "$DEPLOY_DIR/etc/systemd/system/display-manager.service" || {
        echo "FAIL: display-manager.service isn't enabled (lightdm) on the installed deployment" >&2
        exit 1
    }
    sudo readlink "$DEPLOY_DIR/etc/systemd/system/display-manager.service" | grep -q lightdm.service || {
        echo "FAIL: display-manager.service doesn't point at lightdm.service" >&2
        exit 1
    }
    sudo readlink "$DEPLOY_DIR/etc/systemd/system/default.target" | grep -q graphical.target || {
        echo "FAIL: default.target isn't set to graphical.target" >&2
        exit 1
    }
    echo "PASS: every package, dotfile, and service enablement this spin ships is present on the installed disk"

    echo "== Patching the installed BLS entry for a real serial console =="
    # Confirmed-live gotcha: GRUB's own output reaches the serial console,
    # but the booted KERNEL's doesn't unless console=ttyS0 is on its
    # command line - without this the boot goes silent past the
    # bootloader menu, which looks identical to a hang. Patched into the
    # disk itself, so a later --boot-only run of this same disk gets it too.
    BLS_ENTRY="$(sudo find "$MOUNT_DIR/boot/loader.1/entries" -type f | head -1)"
    [ -n "$BLS_ENTRY" ] || { echo "no BLS entry found under $MOUNT_DIR/boot/loader.1/entries" >&2; exit 1; }
    sudo sed -i 's/^options root=/options console=ttyS0,115200n8 console=tty0 root=/' "$BLS_ENTRY"
    sudo umount "$MOUNT_DIR"
    MOUNT_DIR=""
    sudo losetup -d "$LOOP_DEV"
    LOOP_DEV=""
fi

echo "== Booting $DISK_IMAGE under OVMF =="
cp "$OVMF_VARS_TEMPLATE" "$OVMF_VARS"
sudo qemu-system-x86_64 -name cosmos-test-boot \
    -m 4096 -smp 2 -enable-kvm -cpu host \
    -drive if=pflash,format=raw,readonly=on,file="$OVMF_CODE" \
    -drive if=pflash,format=raw,file="$OVMF_VARS" \
    -drive file="$DISK_IMAGE",format=raw,if=virtio \
    -netdev user,id=net0 -device virtio-net-pci,netdev=net0 \
    -vga std -display none \
    -serial file:"$SERIAL_LOG" \
    -daemonize -pidfile "$WORK_DIR/qemu.pid"
QEMU_PID="$(sudo cat "$WORK_DIR/qemu.pid")"

echo "== Waiting up to 300s for a login prompt =="
waited=0
while ! sudo grep -q "login:" "$SERIAL_LOG" 2>/dev/null; do
    sleep 5
    waited=$((waited + 5))
    if [ "$waited" -ge 300 ]; then
        echo "FAIL: timed out after ${waited}s waiting for a login prompt - see $SERIAL_LOG" >&2
        sudo tail -60 "$SERIAL_LOG" >&2
        exit 1
    fi
done
echo "PASS: reached login prompt after ${waited}s"

echo
echo "=============================================="
echo "   Completed: Test Boot (bootc image)"
echo "   Disk: $DISK_IMAGE"
echo "=============================================="
echo
