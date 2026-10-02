#!/usr/bin/env bash
set -euo pipefail

# Dry run mode: --dry-run or DRY_RUN=1
DRY_RUN=0
if [[ "${1:-}" == "--dry-run" ]]; then
    DRY_RUN=1
    shift || true
fi

[[ -n "${TERM:-}" ]] && clear || true
echo "=============================================="
echo "   CosmOS — Install in Disposable VM"
echo "=============================================="
echo

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUTPUT_DISK="${1:-$REPO_ROOT/build/test-disk.raw}"
SIZE="${2:-90G}"
IMAGE_TAG="${BOOTC_IMAGE_TAG:-localhost/cosmos:latest}"
SSH_PORT="${COSMOS_SAFE_INSTALL_SSH_PORT:-2222}"

run() {
    if [[ "$DRY_RUN" -eq 1 ]]; then
        echo "[dry-run] $*"
    else
        eval "$@"
    fi
}

echo "== DRY RUN MODE: $DRY_RUN =="

for tool in qemu-system-x86_64 qemu-img ssh-keygen curl; do
    command -v "$tool" >/dev/null || { echo "$tool not found - install it and retry" >&2; exit 1; }
done

ISOMAKER=""
command -v genisoimage >/dev/null && ISOMAKER=genisoimage
[ -z "$ISOMAKER" ] && command -v mkisofs >/dev/null && ISOMAKER=mkisofs
[ -n "$ISOMAKER" ] || { echo "genisoimage/mkisofs not found - install genisoimage and retry" >&2; exit 1; }

[ -f /usr/share/edk2/ovmf/OVMF_CODE.fd ] || { echo "edk2-ovmf not found (/usr/share/edk2/ovmf/OVMF_CODE.fd missing)" >&2; exit 1; }

sudo podman image exists "$IMAGE_TAG" || {
    echo "$IMAGE_TAG not found locally - build it first (build-bootc.sh)" >&2
    exit 1
}

echo "== Host memory before starting =="
free -h

mkdir -p "$REPO_ROOT/build"
WORK_DIR="$(mktemp -d "$REPO_ROOT/build/safe-install-work.XXXXXX")"
CLOUD_IMG="$WORK_DIR/fedora-cloud.qcow2"
SEED_ISO="$WORK_DIR/seed.iso"
VM_VARS="$WORK_DIR/vm_vars.fd"
SSH_KEY="$WORK_DIR/ssh_key"
CACHE_DIR="$REPO_ROOT/build/.safe-install-cache"
CACHED_CLOUD_IMG="$CACHE_DIR/fedora-cloud-base-44.qcow2"

QEMU_PID=""
cleanup() {
    local exit_code=$?
    if [[ "$DRY_RUN" -eq 0 ]]; then
        if [ -n "$QEMU_PID" ]; then
            kill "$QEMU_PID" 2>/dev/null || true
            for _ in $(seq 1 10); do kill -0 "$QEMU_PID" 2>/dev/null || break; sleep 1; done
            kill -9 "$QEMU_PID" 2>/dev/null || true
        fi
    fi
    rm -rf "$WORK_DIR"
    exit "$exit_code"
}
trap cleanup EXIT

mkdir -p "$CACHE_DIR"
if [ ! -f "$CACHED_CLOUD_IMG" ]; then
    echo "== Downloading Fedora Cloud Base image =="
    run curl -L -o "$CACHED_CLOUD_IMG" \
        https://download.fedoraproject.org/pub/fedora/linux/releases/44/Cloud/x86_64/images/Fedora-Cloud-Base-Generic-44-1.7.x86_64.qcow2
fi

run cp "$CACHED_CLOUD_IMG" "$CLOUD_IMG"
run qemu-img resize "$CLOUD_IMG" 150G >/dev/null

echo "== Generating a throwaway SSH keypair for this run only =="
rm -f "$SSH_KEY" "$SSH_KEY.pub"
ssh-keygen -t ed25519 -f "$SSH_KEY" -N "" -q -C "safe-install-throwaway"


cat >"$WORK_DIR/user-data" <<EOF
#cloud-config
growpart:
  mode: auto
  devices: ['/']
  ignore_growroot_disabled: false
resize_rootfs: true
users:
  - name: builder
    sudo: ALL=(ALL) NOPASSWD:ALL
    shell: /bin/bash
    ssh_authorized_keys:
      - $(cat "$SSH_KEY.pub")
packages:
  - podman
  - qemu-system-x86
  - edk2-ovmf
package_update: true
EOF

echo "instance-id: safe-install-$(date +%s)" >"$WORK_DIR/meta-data"

run "$ISOMAKER" -output "$SEED_ISO" -volid cidata -joliet -rock "$WORK_DIR/user-data" "$WORK_DIR/meta-data" >/dev/null

run cp /usr/share/edk2/ovmf/OVMF_VARS.fd "$VM_VARS"

echo "== Booting disposable installer VM =="
if [[ "$DRY_RUN" -eq 0 ]]; then
    qemu-system-x86_64 -name cosmos-safe-install \
        -m 6144 -smp 4 -enable-kvm -cpu host \
        -drive if=pflash,format=raw,readonly=on,file=/usr/share/edk2/ovmf/OVMF_CODE.fd \
        -drive if=pflash,format=raw,file="$VM_VARS" \
        -drive file="$CLOUD_IMG",if=virtio \
        -drive file="$SEED_ISO",if=virtio,media=cdrom \
        -netdev user,id=net0,hostfwd=tcp::"$SSH_PORT"-:22 -device virtio-net-pci,netdev=net0 \
        -display none -daemonize -pidfile "$WORK_DIR/qemu.pid"
fi

[[ "$DRY_RUN" -eq 0 ]] && QEMU_PID="$(cat "$WORK_DIR/qemu.pid")"

SSH_OPTS=(-i "$SSH_KEY" -p "$SSH_PORT" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=5 -o LogLevel=ERROR)
SCP_OPTS=(-i "$SSH_KEY" -P "$SSH_PORT" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=5 -o LogLevel=ERROR)

echo "== Waiting for SSH =="
if [[ "$DRY_RUN" -eq 0 ]]; then
    up=0
    for i in $(seq 1 60); do
        if ssh "${SSH_OPTS[@]}" builder@127.0.0.1 true 2>/dev/null; then
            up=1
            echo "SSH is up after ~$((i * 10))s"
            break
        fi
        sleep 10
    done
    [ "$up" -eq 1 ] || { echo "SSH never came up" >&2; exit 1; }
else
    echo "[dry-run] would wait for SSH"
fi

echo "== Waiting for cloud-init =="
run ssh "${SSH_OPTS[@]}" builder@127.0.0.1 'sudo cloud-init status --wait'

# Confirmed-live failure: resizing the qcow2 doesn't guarantee
# cloud-init's growpart actually grew the guest's root filesystem, and
# streaming a ~45GB image into a small root remounts it read-only
# mid-transfer and crashes podman load. Check before the long copy.
if [[ "$DRY_RUN" -eq 0 ]]; then
    AVAIL_KB="$(ssh "${SSH_OPTS[@]}" builder@127.0.0.1 "df --output=avail -k / | tail -1")"
    AVAIL_GB=$((AVAIL_KB / 1024 / 1024))
    echo "Guest root filesystem free space: ${AVAIL_GB}G"
    if [ "$AVAIL_GB" -lt 100 ]; then
        echo "FAIL: guest root only has ${AVAIL_GB}G free - growpart didn't grow it to the 150G disk" >&2
        ssh "${SSH_OPTS[@]}" builder@127.0.0.1 'lsblk; df -h /' >&2 || true
        exit 1
    fi
fi

echo "== Streaming bootc image into VM =="
if [[ "$DRY_RUN" -eq 0 ]]; then
    sudo podman save "$IMAGE_TAG" | ssh "${SSH_OPTS[@]}" builder@127.0.0.1 'sudo podman load'
else
    echo "[dry-run] would stream podman image"
fi

echo "== Copying test-boot.sh into VM =="
run scp "${SCP_OPTS[@]}" "$REPO_ROOT/test-boot.sh" builder@127.0.0.1:~/test-boot.sh

echo "== Running test-boot.sh inside VM =="
run ssh "${SSH_OPTS[@]}" builder@127.0.0.1 \
    "COSMOS_ALLOW_EFI_NVRAM_CHANGES=1 BOOTC_IMAGE_TAG='$IMAGE_TAG' bash test-boot.sh /home/builder/installed-disk.raw '$SIZE'"

echo "== Copying installed disk back out =="
# Sparse tar stream, not scp: scp writes every zero of the $SIZE disk
# (90G by default) to the host, and sends it over the wire too.
if [[ "$DRY_RUN" -eq 0 ]]; then
    mkdir -p "$(dirname "$OUTPUT_DISK")"
    rm -f "$OUTPUT_DISK"
    ssh "${SSH_OPTS[@]}" builder@127.0.0.1 'tar -C /home/builder -cSf - installed-disk.raw' \
        | tar -C "$(dirname "$OUTPUT_DISK")" -xSf - --transform "s#installed-disk.raw#$(basename "$OUTPUT_DISK")#"
else
    echo "[dry-run] would copy the installed disk back to $OUTPUT_DISK"
fi

echo
echo "=============================================="
echo "   Completed: Install in Disposable VM"
echo "   Output Disk: $OUTPUT_DISK"
echo "   Dry Run: $DRY_RUN"
echo "=============================================="
echo

