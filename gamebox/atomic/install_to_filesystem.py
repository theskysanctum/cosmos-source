#!/usr/bin/env python3
"""Real installer for the Fedora Atomic/bootc-based CosmOS image.

`bootc install to-disk <device>` always wipes the ENTIRE target device
(confirmed via its own --help: "the entire device will be wiped", no
flag to preserve an existing partition) - it can't be used for
"reinstall the OS, keep userdata" the way image/installer.py's own
recreate_system_partitions() did for the superseded Debian pipeline.
The real mechanism for that is the OTHER bootc subcommand, `bootc
install to-filesystem <root_path>`: "the root filesystem... prepared
and mounted by an external tool or script" - meaning WE partition the
disk (preserving an existing userdata-labeled partition exactly like
installer.py's own recreate_system_partitions() already did) and hand
bootc only the already-formatted, already-mounted root to install into.

Only ONE root partition here, unlike installer.py's own two-slot A/B
scheme: bootc/ostree's own deployment model already gives atomic,
rollback-capable updates on a SINGLE root partition (two ostree
"deployments" coexist inside it, swapped via grub/systemd-boot the same
way GRUB's own saved_entry did for the Debian pipeline) - a second
physical slot would be pure duplication here. userdata still needs its
own real, separate partition, for exactly the same reason installer.py's
own did: so a reinstall (or a bad update) never has to touch it.

bootupd (the tool `bootc install` itself always runs to handle
bootloader installation - see bootc.dev/bootc/bootc-install.html)
installs BOTH BIOS and UEFI boot code automatically, given a real
bios_grub + ESP partition already exist on the disk - this script's own
job is only to create those two partitions correctly, not to install
GRUB itself the way installer.py's own install_bootloader_shared() had
to.

The root partition needs the real Discoverable Partitions Specification
type GUID for x86-64 (SD_GPT_ROOT_X86_64,
4f68bce3-e8cd-4db1-96e7-fbcaf984b709 - confirmed against the real UAPI
Group spec, not guessed) - this is what lets systemd's own
systemd-gpt-auto-generator find and mount root automatically at boot,
with no kernel `root=` cmdline argument or /etc/fstab entry needed at
all (bootc's own docs explicitly recommend avoiding a fstab entry for
`/` in favor of this).

Must run inside THIS SAME bootc-based container, --privileged (bootc
install's own requirement) - the built image doubles as its own
installer medium this way:

    sudo podman run --rm --privileged \\
        -v /var/lib/containers/storage:/var/lib/containers/storage \\
        -v /dev:/dev \\
        localhost/cosmos-real:latest \\
        python3 /var/lib/cosmos/boot/install_to_filesystem.py /dev/sdX

`-v /dev:/dev` is required, not optional - confirmed live that
`--privileged` alone does NOT give the container a live view of the
host's /dev (partition nodes created by `partprobe`/`udevadm settle`
inside the container never appeared without this explicit bind mount).

Verified for real end to end in qemu, not just compiled: install()
against a blank 40GB virtual disk correctly partitioned it (bios_grub +
ESP + a DPS-typed root + userdata), ran `bootc install to-filesystem`
successfully (bootupd installed GRUB), wrote and enabled the userdata
mount unit, and booted standalone afterward (no container, no
install-time tooling attached) straight to a real, working CosmOS
session - confirmed via qemu screendump showing the real "Add Profile"
first-boot screen. The var-userdata.mount unit and its multi-user.target.
wants enablement symlink were both independently confirmed present and
correct via an offline mount of the installed disk afterward.

Two real, non-obvious things fixed getting there, worth remembering:
`bootc install to-filesystem`'s own "Finalizing filesystem root" step
remounts its target read-only as part of completing successfully - the
userdata unit has to be written with an explicit `mount -o remount,rw`
first, done AFTER the bootc call, not before. And `/mnt` (like `/root`)
is a `/var`-backed ostree symlink, dangling in a plain, non-deployed
container run - this script's own scratch mountpoint lives under `/run`
instead, which is always a real, plain tmpfs.

Known, not-yet-fixed gap: the resulting grub.cfg (bootupd's own default)
has no `console=` kernel argument, so nothing appears on a serial
console during/after boot (only a real display, or qemu's own monitor
`screendump`, shows anything) - the same kind of minor, cosmetic gap
already noted in the superseded Debian pipeline's own hand-written GRUB
template, not something this script controls directly since bootupd
writes this file, not this script."""
import argparse
import subprocess
import sys
from pathlib import Path

ROOT_TYPE_GUID_X86_64 = "4f68bce3-e8cd-4db1-96e7-fbcaf984b709"  # DPS SD_GPT_ROOT_X86_64
USERDATA_LABEL = "cosmos-data"
SOURCE_IMGREF = "containers-storage:localhost/cosmos-real:latest"

PART_BIOS_GRUB = 1
PART_ESP = 2
PART_ROOT = 3
PART_USERDATA = 4

ESP_END_MIB = 514
# The last real, confirmed-live measurement (before Flatpak was removed
# from this image entirely): a single deployment used 29GB of a 32GiB
# root (`df /sysroot` showing 97% full) - nowhere near enough room for
# the SECOND deployment ostree keeps around during a real upgrade
# (confirmed live: `bootc upgrade --apply` failed outright with
# "Insufficient free space... required: 3.2 GB" against the ~1GB
# actually left). Flatpak's own separate content store under
# /var/lib/flatpak was the single biggest reason a deployment was this
# large - Brave (the last real Flatpak dependency) is gone now, replaced
# by webshell/, and Flatpak itself has been dropped from this image
# entirely, so a fresh deployment should be meaningfully smaller than
# that 29GB figure.
#
# 40GiB below is a deliberate, NOT-yet-live-confirmed estimate, not a
# re-measured number - picked as "the old 29GB floor, plus real headroom
# for a second deployment during upgrades, with margin for not knowing
# exactly how much smaller a Flatpak-free deployment actually is yet".
# Re-measure for real (`df /sysroot` after a fresh install, the same way
# the 29GB figure above was obtained) before trusting this size on real
# hardware - this comment must be updated with that real number once
# that's done, the same standard every other number in this file holds
# to.
ROOT_SIZE_MIB = 40 * 1024
ROOT_END_MIB = ESP_END_MIB + ROOT_SIZE_MIB


def _run(cmd, check=True, **kwargs):
	print(f"+ {' '.join(cmd)}", file=sys.stderr)
	return subprocess.run(cmd, check=check, **kwargs)


def _partition_path(device, number):
	"""/dev/sda + 3 -> /dev/sda3; /dev/vda + 3 -> /dev/vda3; /dev/nvme0n1
	+ 3 -> /dev/nvme0n1p3 - same real device-naming rule image/
	installer.py's own _partition_path() already uses."""
	return f"{device}p{number}" if device[-1].isdigit() else f"{device}{number}"


def find_existing_userdata(device):
	"""Same real technique as image/installer.py's own function of the
	same name: identify by filesystem LABEL, scoped to THIS device so a
	cosmos-data-labeled partition on some OTHER attached disk is never
	mistaken for this one's own."""
	result = _run(["blkid", "-t", f"LABEL={USERDATA_LABEL}", "-o", "device"], check=False, capture_output=True, text=True)
	for line in result.stdout.splitlines():
		line = line.strip()
		if line.startswith(device):
			return line
	return None


def confirm_erase(device, will_preserve_userdata, assume_yes):
	if assume_yes:
		print(f"--yes-i-am-sure given - skipping interactive confirmation for {device}", file=sys.stderr)
		return True
	if will_preserve_userdata:
		print(f"\nThis will reinstall CosmOS on {device}.")
		print("The existing userdata partition (profiles/saves/games/media) will be PRESERVED, untouched.")
	else:
		print(f"\nThis will COMPLETELY ERASE {device} and everything on it, including any userdata.")
	print(f"Type the device path ({device}) to confirm, or anything else to cancel:")
	return input("> ").strip() == device


def create_partitions_fresh(device):
	_run(["parted", "--script", device, "mklabel", "gpt"])
	_run(["parted", "--script", device, "mkpart", "bios_grub", "1MiB", "2MiB"])
	_run(["parted", "--script", device, "set", str(PART_BIOS_GRUB), "bios_grub", "on"])
	_run(["parted", "--script", device, "mkpart", "ESP", "fat32", "2MiB", f"{ESP_END_MIB}MiB"])
	_run(["parted", "--script", device, "set", str(PART_ESP), "esp", "on"])
	_run(["parted", "--script", device, "mkpart", "cosmos-root", "ext4", f"{ESP_END_MIB}MiB", f"{ROOT_END_MIB}MiB"])
	_run(["parted", "--script", device, "mkpart", USERDATA_LABEL, "ext4", f"{ROOT_END_MIB}MiB", "100%"])
	_run(["partprobe", device])
	_run(["udevadm", "settle"])
	_run(["sgdisk", f"--typecode={PART_ROOT}:{ROOT_TYPE_GUID_X86_64}", device])
	return {
		"esp": _partition_path(device, PART_ESP),
		"root": _partition_path(device, PART_ROOT),
		"userdata": _partition_path(device, PART_USERDATA),
	}


def recreate_root_partition(device, existing_userdata_partition):
	"""Rebuilds bios_grub/ESP/root, leaving the existing userdata
	partition completely untouched - the same `parted rm <N>`-not-
	`mklabel gpt` technique image/installer.py's own
	recreate_system_partitions() already verified for real (a real GPT
	partition-ENTRY removal, not a whole-table rewrite, leaves an
	untouched partition's own entry - and therefore its UUID - genuinely
	unaffected), plus the same hard UUID-equality assertion before ever
	touching mkfs."""
	pre_uuid = _run(["blkid", "-s", "UUID", "-o", "value", existing_userdata_partition], capture_output=True, text=True).stdout.strip()
	for number in (PART_BIOS_GRUB, PART_ESP, PART_ROOT):
		_run(["parted", "--script", device, "rm", str(number)], check=False)
	_run(["parted", "--script", device, "mkpart", "bios_grub", "1MiB", "2MiB"])
	_run(["parted", "--script", device, "set", str(PART_BIOS_GRUB), "bios_grub", "on"])
	_run(["parted", "--script", device, "mkpart", "ESP", "fat32", "2MiB", f"{ESP_END_MIB}MiB"])
	_run(["parted", "--script", device, "set", str(PART_ESP), "esp", "on"])
	_run(["parted", "--script", device, "mkpart", "cosmos-root", "ext4", f"{ESP_END_MIB}MiB", f"{ROOT_END_MIB}MiB"])
	_run(["partprobe", device])
	_run(["udevadm", "settle"])
	post_uuid = _run(["blkid", "-s", "UUID", "-o", "value", existing_userdata_partition], capture_output=True, text=True).stdout.strip()
	if pre_uuid != post_uuid:
		raise RuntimeError(f"userdata partition UUID changed unexpectedly ({pre_uuid} -> {post_uuid}) - aborting before any mkfs")
	_run(["sgdisk", f"--typecode={PART_ROOT}:{ROOT_TYPE_GUID_X86_64}", device])
	return {
		"esp": _partition_path(device, PART_ESP),
		"root": _partition_path(device, PART_ROOT),
		"userdata": existing_userdata_partition,
	}


def write_userdata_mount_unit(root_mount, userdata_uuid):
	"""bootc-image-builder's own config.toml customizations.filesystem
	mechanism generates a real systemd .mount unit for an extra
	partition (confirmed live - see the plan notes) - but that only
	works at IMAGE build time, where it already knows the partition's
	UUID because it just created it. Here the userdata partition is
	created fresh on REAL, unique hardware at INSTALL time, so the unit
	has to be generated fresh here instead, written into the real
	ostree deployment `to-filesystem` just produced (its own real,
	per-deployment /etc - not root_mount/etc directly, which is a
	different, mostly-empty top-level directory on an ostree root).

	Where=/var/userdata, NOT /userdata - confirmed live (a real boot,
	not just reasoning about it) that systemd's own .mount unit
	machinery refuses outright to even attempt a Where= path that
	contains a symlink component: "Mount path /userdata is not
	canonical (contains a symlink)", Result: resources, unit left
	permanently failed. This is a real, stricter check than the plain
	mount(2) syscall itself has (a standalone `mount --bind` onto a
	symlinked target does resolve through it and succeed, confirmed
	separately - only systemd's own unit activation adds this
	canonical-path requirement on top). /userdata is a symlink to
	/var/userdata in the image (see atomic/Containerfile, same pattern
	as /cosmos -> /var/lib/cosmos) specifically so bootc-image-
	builder's own mountpoint allowlist (which rejects a real top-level
	path outright - only /var-rooted ones) could be satisfied; mounting
	the real partition at the real, non-symlinked /var/userdata instead
	means every actual file access through the app's own hardcoded
	/userdata path still transparently reaches it, without hitting
	systemd's stricter check at all. The unit's own filename must match
	systemd's escaping of its Where= path (var-userdata.mount for
	/var/userdata, not userdata.mount) - systemd derives the unit name
	from the mountpoint and refuses a mismatched one the same way it
	refused the symlinked path."""
	deploy_dirs = sorted((root_mount / "ostree" / "deploy" / "default" / "deploy").glob("*.0"))
	if not deploy_dirs:
		raise RuntimeError(f"no ostree deployment found under {root_mount}/ostree/deploy/default/deploy - did `bootc install to-filesystem` actually succeed?")
	deploy_dir = deploy_dirs[-1]

	systemd_dir = deploy_dir / "etc" / "systemd" / "system"
	systemd_dir.mkdir(parents=True, exist_ok=True)
	unit_path = systemd_dir / "var-userdata.mount"
	unit_path.write_text(
		"[Unit]\nDefaultDependencies=True\n\n"
		"[Mount]\n"
		f"What=/dev/disk/by-uuid/{userdata_uuid}\n"
		"Where=/var/userdata\n"
		"Type=ext4\n"
		"Options=defaults\n\n"
		"[Install]\nWantedBy=multi-user.target\n"
	)
	wants_dir = systemd_dir / "multi-user.target.wants"
	wants_dir.mkdir(parents=True, exist_ok=True)
	symlink_path = wants_dir / "var-userdata.mount"
	symlink_path.unlink(missing_ok=True)
	symlink_path.symlink_to("../var-userdata.mount")
	print(f"wrote {unit_path} (userdata UUID={userdata_uuid})", file=sys.stderr)


def install(device, assume_yes=False, source_imgref=SOURCE_IMGREF):
	existing_userdata = find_existing_userdata(device)
	if not confirm_erase(device, will_preserve_userdata=bool(existing_userdata), assume_yes=assume_yes):
		print("cancelled - device not touched", file=sys.stderr)
		return False

	if existing_userdata:
		paths = recreate_root_partition(device, existing_userdata)
		format_userdata = False
	else:
		paths = create_partitions_fresh(device)
		format_userdata = True

	_run(["mkfs.vfat", "-F", "32", "-n", "ESP", paths["esp"]])
	_run(["mkfs.ext4", "-F", "-L", "cosmos-root", paths["root"]])
	if format_userdata:
		_run(["mkfs.ext4", "-F", "-L", USERDATA_LABEL, paths["userdata"]])

	userdata_uuid = _run(["blkid", "-s", "UUID", "-o", "value", paths["userdata"]], capture_output=True, text=True).stdout.strip()

	# /run, not /mnt - confirmed live that /mnt is unusable here the same
	# way /root was in the getty/autologin work (a dangling /var-backed
	# ostree symlink, this time on a plain, non-deployed container run
	# where /var/mnt was never created). /run is always a real, plain
	# tmpfs, never symlinked.
	root_mount = Path("/run/cosmos-install-root")
	root_mount.mkdir(parents=True, exist_ok=True)
	_run(["mount", paths["root"], str(root_mount)])
	try:
		esp_mount = root_mount / "boot" / "efi"
		esp_mount.mkdir(parents=True, exist_ok=True)
		_run(["mount", paths["esp"], str(esp_mount)])
		try:
			_run(["bootc", "install", "to-filesystem", "--source-imgref", source_imgref, str(root_mount)])
			# `bootc install to-filesystem`'s own "Finalizing filesystem
			# root" step remounts the target read-only as part of
			# completing successfully (confirmed live: writing the
			# userdata unit right after it, with no remount, failed with
			# "Read-only file system") - harmless to remount rw again
			# here just for this one write, since a real boot sets up
			# the deployment's own mount state fresh regardless of
			# whatever this leaves it as.
			_run(["mount", "-o", "remount,rw", str(root_mount)])
			write_userdata_mount_unit(root_mount, userdata_uuid)
		finally:
			_run(["umount", "-R", str(esp_mount)], check=False)
	finally:
		_run(["umount", "-R", str(root_mount)], check=False)

	print(f"install succeeded - userdata partition preserved at {paths['userdata']}", file=sys.stderr)
	return True


def main():
	parser = argparse.ArgumentParser(description=__doc__)
	parser.add_argument("device", help="target disk to install to, e.g. /dev/sda (whole disk, not a partition)")
	parser.add_argument(
		"--yes-i-am-sure",
		action="store_true",
		help="skip the interactive re-type-the-device-path confirmation - for scripted/tested use only",
	)
	parser.add_argument(
		"--source-imgref",
		default=SOURCE_IMGREF,
		help=(
			f"bootc source image reference to install (default: {SOURCE_IMGREF}, the locally-built "
			"image). Pass a real registry ref (e.g. docker://ghcr.io/<owner>/cosmos:latest) to "
			"install straight from GHCR instead - needed for a real bootc upgrade/rollback test, "
			"since bootc upgrade against a plain containers-storage: source has no real 'check for "
			"newer' semantics the way a registry ref does."
		),
	)
	args = parser.parse_args()
	ok = install(args.device, assume_yes=args.yes_i_am_sure, source_imgref=args.source_imgref)
	print(f"install {'succeeded' if ok else 'failed or was cancelled'}", file=sys.stderr)
	sys.exit(0 if ok else 1)


if __name__ == "__main__":
	main()
