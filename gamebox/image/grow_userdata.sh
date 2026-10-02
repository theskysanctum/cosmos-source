#!/bin/bash
# Grows the userdata partition (and its ext4 filesystem) to fill
# whatever real disk it landed on, if it doesn't already - useful for an
# install onto a drive larger than the userdata partition was originally
# sized for at install time.
#
# Deliberately does its own real check every boot rather than gating on
# a marker file - growpart/resize2fs are already idempotent and fast to
# no-op when there's genuinely nothing to grow (confirmed by growpart's
# own "NOCHANGE" report), so trusting real state each time is simpler
# and safer than tracking "have I already done this" separately. Same
# style as this project's own find_existing_userdata()/is_installed_id()
# - check reality, don't assume from history.
#
# Never touches the root/system partition - only ever acts on the
# partition labeled cosmos-data (see atomic/install_to_filesystem.py's
# own USERDATA_LABEL), found fresh each run rather than assumed to be a
# fixed partition number.
set -euo pipefail

USERDATA_PARTITION="$(blkid -L cosmos-data || true)"
if [ -z "$USERDATA_PARTITION" ]; then
	echo "grow_userdata: no cosmos-data partition found - nothing to do" >&2
	exit 0
fi

# Strip the trailing partition number to get the whole-disk device and
# that partition's own number - same job installer.py's own
# live_medium_disk()/launcher/app.py's own _current_boot_disk() already
# do, just also needing the number itself here (growpart wants it
# separately, not as part of the device path).
if [[ "$USERDATA_PARTITION" =~ ^(/dev/(sd[a-z]+|nvme[0-9]+n[0-9]+|mmcblk[0-9]+|loop[0-9]+))p?([0-9]+)$ ]]; then
	DISK="${BASH_REMATCH[1]}"
	PART_NUMBER="${BASH_REMATCH[3]}"
else
	echo "grow_userdata: couldn't parse disk/partition number out of $USERDATA_PARTITION - nothing to do" >&2
	exit 0
fi

echo "grow_userdata: checking $USERDATA_PARTITION (partition $PART_NUMBER on $DISK)"
GROWPART_OUTPUT="$(sudo growpart "$DISK" "$PART_NUMBER" 2>&1)" && GROWPART_STATUS=0 || GROWPART_STATUS=$?
echo "grow_userdata: $GROWPART_OUTPUT"

if [[ "$GROWPART_OUTPUT" == CHANGED:* ]]; then
	echo "grow_userdata: partition grew - resizing the filesystem"
	sudo resize2fs "$USERDATA_PARTITION"
	echo "grow_userdata: done"
elif [ "$GROWPART_STATUS" -ne 0 ] && [[ "$GROWPART_OUTPUT" != NOCHANGE:* ]]; then
	echo "grow_userdata: growpart failed unexpectedly - leaving the filesystem as-is" >&2
	exit 1
else
	echo "grow_userdata: already fills the disk - nothing to do"
fi
