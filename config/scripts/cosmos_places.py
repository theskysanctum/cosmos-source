"""What the CosmOS file manager (cosmos-files) is allowed to show - and nothing else.

The places are exactly three kinds:

  * the active profile's own files folder (never the rest of the profile's folder, which holds
    its PIN hash, and never the home directory);
  * USB drives and other removable media (USB sticks, SD cards, optical discs);
  * the machine's OTHER internal drives - every internal disk except the one the system runs
    from. A disk is "the system's" when anything on it is mounted at / or under the OS's own
    paths (/boot, /var, /usr, /home ...) or it holds swap, so neither / nor /boot nor the system's
    /home can ever appear, whichever partition a file happens to live on.

Everything a user can navigate to goes through Scope.locate(), which resolves symlinks and ".."
first (realpath) and only accepts a path that ends up inside a place's own root - a symlink on a
USB stick that points at / resolves outside and is refused.

The drive list comes from lsblk (the kernel's own view), not from Gio, so it can be tested with
recorded output; set COSMOS_LSBLK_JSON to a file to use that instead of running lsblk.
"""
import json
import os
import subprocess

# lsblk fstypes that are not a browsable filesystem.
NOT_FILESYSTEMS = {"swap", "LVM2_member", "crypto_LUKS", "linux_raid_member", "zfs_member", "bcache", "isw_raid_member"}
# A disk with anything mounted at (or under) these belongs to the running system.
SYSTEM_MOUNTS = ("/", "/boot", "/boot/efi", "/efi", "/usr", "/etc", "/var", "/home", "/sysroot", "/opt", "/root", "/ostree")
# A place is never allowed to be rooted at one of these, whatever lsblk says.
FORBIDDEN_ROOTS = {"/", "/boot", "/boot/efi", "/efi", "/usr", "/etc", "/var", "/home", "/sysroot", "/opt", "/root",
                   "/proc", "/sys", "/dev", "/run", "/tmp", "/srv", "/mnt", "/media", "/bin", "/lib", "/lib64", "/sbin"}
LSBLK_COLUMNS = "NAME,PATH,TYPE,RM,HOTPLUG,TRAN,FSTYPE,LABEL,SIZE,MOUNTPOINTS,MODEL,VENDOR"


class Place:
    def __init__(self, key, title, kind, path=None, device=None, fstype=None, size="", subtitle="", removable=False):
        self.key = key              # "profile" or "dev:/dev/sdb1"
        self.title = title
        self.kind = kind            # "profile" | "usb" | "disc" | "drive"
        self.path = path            # where it is mounted / lives; None while unmounted
        self.device = device
        self.fstype = fstype
        self.size = size
        self.subtitle = subtitle
        self.removable = removable

    @property
    def mounted(self):
        return bool(self.path)

    def __repr__(self):
        return f"Place({self.kind}, {self.title!r}, {self.path})"


def _truthy(value):
    return value in (True, 1, "1", "true", "True")


def read_lsblk(run=subprocess.run):
    """lsblk's JSON, from COSMOS_LSBLK_JSON when set (tests, demos), else the real thing."""
    override = os.environ.get("COSMOS_LSBLK_JSON")
    if override:
        try:
            with open(override, encoding="utf-8") as fh:
                return fh.read()
        except OSError:
            return ""
    try:
        done = run(["lsblk", "-J", "-o", LSBLK_COLUMNS], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return ""
    return done.stdout if done.returncode == 0 else ""


def parse_disks(text):
    try:
        return list(json.loads(text).get("blockdevices") or [])
    except (ValueError, AttributeError, TypeError):
        return []


def _mountpoints(node):
    raw = node.get("mountpoints")
    if raw is None and node.get("mountpoint"):
        raw = [node["mountpoint"]]
    return [m for m in (raw or []) if m and m != "[SWAP]"]


def _walk(node):
    yield node
    for child in node.get("children") or []:
        yield from _walk(child)


def _is_system_mount(mount):
    return mount in SYSTEM_MOUNTS or mount.startswith(("/sysroot", "/boot/", "/usr/", "/etc/", "/ostree", "/var/lib/"))


def is_system_disk(disk):
    """True when anything under this disk is (part of) the running system."""
    for node in _walk(disk):
        if node.get("fstype") == "swap" or any(_is_system_mount(m) for m in _mountpoints(node)):
            return True
    return False


def _is_removable(disk):
    return _truthy(disk.get("rm")) or _truthy(disk.get("hotplug")) or disk.get("tran") == "usb" or disk.get("type") == "rom"


def _title(node, disk):
    label = (node.get("label") or "").strip()
    if label:
        return label
    model = " ".join(part for part in ((disk.get("vendor") or "").strip(), (disk.get("model") or "").strip()) if part)
    return f"{model or 'Drive'} {node.get('size') or ''}".strip()


def find_places(disks):
    """The USB and internal-drive places from an lsblk disk list (system disk excluded)."""
    places = []
    for disk in disks:
        if disk.get("type") not in ("disk", "rom") or disk.get("name", "").startswith(("zram", "loop", "ram", "nbd", "dm-", "md")):
            continue
        if disk.get("type") == "disk" and disk.get("size") in ("0B", "0", None):
            continue
        if is_system_disk(disk):
            continue
        removable = _is_removable(disk)
        kind = "disc" if disk.get("type") == "rom" else "usb" if removable else "drive"
        for node in _walk(disk):
            fstype = node.get("fstype")
            if not fstype or fstype in NOT_FILESYSTEMS or node.get("type") in ("loop",):
                continue
            mounts = _mountpoints(node)
            path = mounts[0] if mounts else None
            if path and os.path.normpath(path) in FORBIDDEN_ROOTS:
                continue
            device = node.get("path") or f"/dev/{node.get('name')}"
            places.append(Place(f"dev:{device}", _title(node, disk), kind, path, device, fstype, node.get("size") or "",
                                subtitle=f"{node.get('size') or ''} {fstype}".strip(), removable=removable))
    order = {"usb": 0, "disc": 1, "drive": 2}
    return sorted(places, key=lambda p: (order[p.kind], p.title.lower()))


def profile_place(name, files_dir):
    os.makedirs(files_dir, exist_ok=True)
    return Place("profile", name or "My files", "profile", files_dir, subtitle="Your files")


def all_places(profile_name, files_dir, disks):
    return [profile_place(profile_name, files_dir)] + find_places(disks)


class OutOfScope(Exception):
    """A path outside every place the file manager is allowed to show."""


def _inside(root, real):
    root = os.path.realpath(root)
    return real == root or real.startswith(root.rstrip(os.sep) + os.sep)


class Scope:
    def __init__(self, places):
        self.places = places

    def mounted(self):
        return [p for p in self.places if p.mounted]

    def locate(self, path):
        """(place, real path) for a path inside a place, else None. Symlinks and .. are resolved
        first, so nothing can point its way out."""
        try:
            real = os.path.realpath(path)
        except (OSError, ValueError, TypeError):
            return None
        for place in self.mounted():
            if _inside(place.path, real):
                return place, real
        return None

    def check(self, path):
        found = self.locate(path)
        if not found:
            raise OutOfScope(path)
        return found[1]

    def relative_title(self, path):
        """A human path that starts at the place ("Photos stick / Holiday / 2024"), never an absolute one."""
        found = self.locate(path)
        if not found:
            return ""
        place, real = found
        rel = os.path.relpath(real, os.path.realpath(place.path))
        return place.title if rel == "." else place.title + " / " + rel.replace(os.sep, " / ")
