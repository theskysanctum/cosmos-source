"""The typed actions kronos will perform - and nothing else (docs/kronos-design.md, section 4).

An action is: a name, a schema (validate), a tier (automatic or needs the admin passphrase), and a *plan*: the
exact commands or file operations to perform, built from validated data only. There are no shell strings anywhere -
every command is an argv list - so what a request says can never turn into something it didn't ask for.

This module is pure: it decides and builds, it never runs anything. kronos_core hands the plan to a runner (the
root helper in production, a fake in tests).

Tiers: "auto" (routine, logged) and "passphrase" (rare and powerful: needs an unlocked admin session).
"""
import os
import posixpath
import re
from dataclasses import dataclass, field

AUTO, PASSPHRASE = "auto", "passphrase"

PKG_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,127}$")
FLATPAK_ID = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_-]*(\.[A-Za-z0-9_-]+){2,}$")
FLATPAK_REMOTES = ("flathub",)
UNIT = re.compile(r"^[A-Za-z0-9_@:][A-Za-z0-9:_.@-]{0,119}\.(service|socket|timer|target|path|mount)$")
IMAGE_REF = re.compile(r"^(docker://)?[a-z0-9]([a-z0-9.-]*[a-z0-9])?(:[0-9]{1,5})?(/[a-z0-9._-]+)+(:[A-Za-z0-9._-]{1,128}|@sha256:[0-9a-f]{64})$")
MAX_PACKAGES = 50
MAX_FILE_BYTES = 1_048_576
MAX_ARGV = 64
MAX_ARG_LEN = 4096
SERVICE_VERBS_AUTO = ("start", "stop", "restart", "reload", "status")
SERVICE_VERBS_ALL = SERVICE_VERBS_AUTO + ("enable", "disable")
# Interactive shells and ways of getting one: `run` never starts these, even with the passphrase.
FORBIDDEN_PROGRAMS = {"sh", "bash", "dash", "zsh", "fish", "csh", "tcsh", "ksh", "ash", "busybox", "su", "sudo", "doas", "pkexec",
                      "login", "agetty", "getty", "sulogin", "nsenter", "chroot", "unshare", "script", "screen", "tmux", "machinectl",
                      "systemd-run", "runuser", "setpriv"}

DEFAULT_POLICY = {
    "auto_services": ["NetworkManager.service", "bluetooth.service", "cups.service", "avahi-daemon.service", "firewalld.service",
                      "chronyd.service", "systemd-timesyncd.service", "wpa_supplicant.service", "libvirtd.service", "sshd.service"],
    "protected_units": ["kronos.service", "kronos.socket", "sudo.service", "polkit.service", "dbus.service", "dbus-broker.service",
                        "systemd-logind.service", "systemd-journald.service", "auditd.service"],
    "read_files": ["/etc/hosts", "/etc/os-release", "/etc/fstab", "/etc/cosmos/*", "/etc/kronos/*", "/var/log/kronos/*"],
    "write_files": ["/etc/hosts", "/etc/cosmos/*"],
    "tier_overrides": {},
}


class Refused(Exception):
    """The request is not acceptable; str() is the reason, fit to show."""


@dataclass
class Step:
    """One thing to do. `alternatives` are argv lists tried in order until one succeeds (e.g. apply live, else stage);
    kind "write_file"/"read_file" are done by the helper itself, not by a command."""
    kind: str = "exec"
    alternatives: list = field(default_factory=list)
    path: str = ""
    data: str = ""
    stdin: str = None
    cwd: str = None
    allow_fail: bool = False
    note: str = ""


@dataclass
class Plan:
    action: str
    tier: str
    description: str
    steps: list
    args: dict


# --------------------------------------------------------------------------- validation helpers

def _require(cond, message):
    if not cond:
        raise Refused(message)


def _str(args, key, required=True, pattern=None, choices=None, default=None):
    value = args.get(key, default)
    if value is None and not required:
        return None
    _require(isinstance(value, str) and value != "", f"'{key}' is required.")
    _require("\0" not in value and "\n" not in value, f"'{key}' contains characters that aren't allowed.")
    if pattern is not None:
        _require(pattern.fullmatch(value), f"'{key}' isn't valid: {value[:60]!r}.")
    if choices is not None:
        _require(value in choices, f"'{key}' must be one of {', '.join(choices)}.")
    return value


def _names(args, key="packages"):
    names = args.get(key)
    _require(isinstance(names, list) and names, f"'{key}' must be a list with at least one name.")
    _require(len(names) <= MAX_PACKAGES, f"At most {MAX_PACKAGES} packages at a time.")
    out = []
    for n in names:
        _require(isinstance(n, str) and PKG_NAME.fullmatch(n), f"Not a valid package name: {str(n)[:60]!r}.")
        out.append(n)
    return sorted(set(out))


def _glob_match(patterns, path):
    import fnmatch
    return any(fnmatch.fnmatchcase(path, p) for p in patterns)


def clean_path(path):
    """An absolute, normalized path with no tricks, or Refused."""
    _require(isinstance(path, str) and path.startswith("/"), "The path must be absolute.")
    _require("\0" not in path and "\n" not in path, "The path contains characters that aren't allowed.")
    norm = posixpath.normpath(path)
    _require(".." not in path.split("/"), "The path can't contain '..'.")
    return norm


# --------------------------------------------------------------------------- the actions
# Each builder: (args, ctx) -> (normalized_args, tier, description, steps). ctx has .policy (dict) and .app_check.

def pkg_install(args, ctx):
    names = _names(args)
    for n in names:
        ok, why = ctx.app_check("rpm", n)
        _require(ok, why)
    steps = [Step(alternatives=[["rpm-ostree", "install", "--idempotent", "--apply-live", *names],
                                ["rpm-ostree", "install", "--idempotent", *names]], note="apply live if possible, else stage for the next boot")]
    return {"packages": names}, AUTO, f"Install {', '.join(names)}", steps


def pkg_remove(args, ctx):
    names = _names(args)
    return {"packages": names}, AUTO, f"Remove {', '.join(names)}", [Step(alternatives=[["rpm-ostree", "uninstall", *names]])]


def pkg_upgrade(args, ctx):
    return {}, AUTO, "Upgrade the layered packages", [Step(alternatives=[["rpm-ostree", "upgrade"]])]


# /boot is an automount; bootc's own access doesn't wake it (bootc-dev/bootc#2402), so anything that reaches the boot files touches it first.
WAKE_BOOT = Step(alternatives=[["ls", "/boot"]], allow_fail=True, note="wake the /boot automount (bootc-dev/bootc#2402)")


def os_upgrade(args, ctx):
    """`check` only looks for an update; `apply` also restarts into it. Both are bootc's own flags, so neither is a way to run anything else."""
    method = _str(args, "method", required=False, choices=("bootc", "rpm-ostree"), default="bootc")
    check, apply = args.get("check", False), args.get("apply", False)
    _require(isinstance(check, bool) and isinstance(apply, bool) and not (check and apply), "'check' and 'apply' must be true/false, and not both.")
    _require(method == "bootc" or not (check or apply), "'check' and 'apply' are for bootc.")
    argv = ["bootc", "upgrade"] + (["--check"] if check else []) + (["--apply"] if apply else []) if method == "bootc" else ["rpm-ostree", "upgrade"]
    norm = {"method": method, **({"check": True} if check else {}), **({"apply": True} if apply else {})}
    what = "Check for an operating system update" if check else "Upgrade the operating system" + (" and restart" if apply else "")
    return norm, AUTO, what, ([WAKE_BOOT] if method == "bootc" else []) + [Step(alternatives=[argv])]


def os_rollback(args, ctx):
    apply = args.get("apply", False)
    _require(isinstance(apply, bool), "'apply' must be true or false.")
    return ({"apply": True} if apply else {}), PASSPHRASE, "Roll back to the previous operating system" + (" and restart" if apply else ""), \
        [WAKE_BOOT, Step(alternatives=[["bootc", "rollback"] + (["--apply"] if apply else [])])]


def os_switch(args, ctx):
    ref = _str(args, "ref", pattern=IMAGE_REF)
    return {"ref": ref}, PASSPHRASE, f"Switch the operating system to {ref}", [WAKE_BOOT, Step(alternatives=[["bootc", "switch", ref]])]


def _flatpak_id(args, ctx):
    fid = _str(args, "id", pattern=FLATPAK_ID)
    ok, why = ctx.app_check("flatpak", fid)
    _require(ok, why)
    return fid


def flatpak_install(args, ctx):
    fid = _flatpak_id(args, ctx)
    remote = _str(args, "remote", required=False, choices=FLATPAK_REMOTES, default="flathub")
    steps = [Step(alternatives=[["flatpak", "install", "--system", "-y", "--noninteractive", remote, fid]]),
             # a terminal the name lists didn't know is removed again (exit 1 means "found and removed")
             Step(alternatives=[["/usr/local/bin/find-terminals", "--purge-flatpaks"]], note="remove any terminal emulator that arrived", allow_fail=True)]
    return {"id": fid, "remote": remote}, AUTO, f"Install the Flatpak {fid}", steps


def flatpak_update(args, ctx):
    fid = _str(args, "id", required=False, pattern=FLATPAK_ID)
    argv = ["flatpak", "update", "--system", "-y", "--noninteractive"] + ([fid] if fid else [])
    return ({"id": fid} if fid else {}), AUTO, f"Update {fid or 'all Flatpaks'}", [Step(alternatives=[argv])]


def flatpak_remove(args, ctx):
    fid = _str(args, "id", pattern=FLATPAK_ID)
    return {"id": fid}, AUTO, f"Remove the Flatpak {fid}", [Step(alternatives=[["flatpak", "uninstall", "--system", "-y", "--noninteractive", fid]])]


POWER_VERBS = ("reboot", "poweroff")


def system_power(args, ctx):
    """Restarting or shutting down the whole machine. AUTO, not PASSPHRASE, on purpose: it needs no admin passphrase on the login screen either (a
    polkit rule scoped to the greeter account grants it there with no prompt at all, config/polkit/50-cosmos-greeter-power.rules) - going through
    kronos for the desktop account's own copy of that same everyday action is strictly MORE accountable than what the login screen already does
    unprompted, since every kronos decision is written to the audit log regardless of tier, not less."""
    verb = _str(args, "verb", choices=POWER_VERBS)
    what = "Restart the computer" if verb == "reboot" else "Shut the computer down"
    return {"verb": verb}, AUTO, what, [Step(alternatives=[["systemctl", verb]])]


def service_control(args, ctx):
    unit = _str(args, "unit", pattern=UNIT)
    verb = _str(args, "verb", choices=SERVICE_VERBS_ALL)
    _require(unit not in ctx.policy["protected_units"] or verb == "status", f"{unit} can't be changed through kronos.")
    tier = AUTO if (unit in ctx.policy["auto_services"] and verb in SERVICE_VERBS_AUTO) else PASSPHRASE
    argv = ["systemctl", verb, "--no-pager", "--", unit] if verb == "status" else ["systemctl", verb, "--", unit]
    return {"unit": unit, "verb": verb}, tier, f"{verb.capitalize()} {unit}", [Step(alternatives=[argv], allow_fail=(verb == "status"))]


def file_read(args, ctx):
    path = clean_path(args.get("path"))
    _require(_glob_match(ctx.policy["read_files"], path), f"{path} can't be read through kronos.")
    return {"path": path}, AUTO, f"Read {path}", [Step(kind="read_file", path=path)]


def file_write(args, ctx):
    path = clean_path(args.get("path"))
    _require(_glob_match(ctx.policy["write_files"], path), f"{path} can't be written through kronos.")
    content = args.get("content")
    _require(isinstance(content, str), "'content' must be text.")
    _require(len(content.encode("utf-8")) <= MAX_FILE_BYTES, f"The file is too large (limit {MAX_FILE_BYTES // 1024} KB).")
    _require("\0" not in content, "The content contains a NUL byte.")
    return {"path": path, "content": content}, PASSPHRASE, f"Write {path}", [Step(kind="write_file", path=path, data=content)]


def log_read(args, ctx):
    unit = _str(args, "unit", required=False, pattern=UNIT)
    lines = args.get("lines", 200)
    _require(isinstance(lines, int) and not isinstance(lines, bool) and 1 <= lines <= 2000, "'lines' must be between 1 and 2000.")
    boot = bool(args.get("boot", False))
    argv = ["journalctl", "--no-pager", "-n", str(lines)] + (["-b"] if boot else []) + (["-u", unit] if unit else [])
    return ({"unit": unit, "lines": lines, "boot": boot} if unit else {"lines": lines, "boot": boot}), AUTO, f"Read the log{' of ' + unit if unit else ''}", [Step(alternatives=[argv])]


# The doors in the CosmOS firewall (config/scripts/cosmos_firewall.py; test_kronos_actions checks the two tables agree).
FIREWALL_DOORS = {"ssh": "ssh", "webtop": "cosmos-webtop", "play-together": "cosmos-play-together",
                  "steam-remote-play": "cosmos-steam-remote-play", "steam-lan-transfer": "cosmos-steam-lan-transfer"}
FIREWALL_TEMPORARY = {"play-together", "steam-remote-play", "steam-lan-transfer"}          # running firewall only, expires by itself, and opening it needs no passphrase
FIREWALL_TIMEOUT = "6h"
FIREWALL_TIMEOUTS = {"steam-remote-play": "1h", "steam-lan-transfer": "1h"}     # a door's own timeout overrides the default (its watcher renews it)
FIREWALL_ZONE = "cosmos"


def firewall_door(args, ctx):
    """Open or close one named door in the firewall. Closing is always fine; OPENING a permanent door is a passphrase action. The
    temporary door (hosting a game session) is added to the running firewall only, with a timeout, so it needs no passphrase."""
    door = _str(args, "door", choices=tuple(FIREWALL_DOORS))
    opening = args.get("open")
    _require(isinstance(opening, bool), "'open' must be true or false.")
    verb = "--add-service" if opening else "--remove-service"
    service = FIREWALL_DOORS[door]
    if door in FIREWALL_TEMPORARY:
        argv = ["firewall-cmd", f"--zone={FIREWALL_ZONE}", f"{verb}={service}"] + ([f"--timeout={FIREWALL_TIMEOUTS.get(door, FIREWALL_TIMEOUT)}"] if opening else [])
        return {"door": door, "open": opening}, AUTO, f"{'Open' if opening else 'Close'} the {door} door in the firewall", [Step(alternatives=[argv])]
    steps = [Step(alternatives=[["firewall-cmd", f"--zone={FIREWALL_ZONE}", f"{verb}={service}"]], allow_fail=True),
             Step(alternatives=[["firewall-cmd", "--permanent", f"--zone={FIREWALL_ZONE}", f"{verb}={service}"]])]
    return {"door": door, "open": opening}, (PASSPHRASE if opening else AUTO), f"{'Open' if opening else 'Close'} the {door} door in the firewall", steps


# Reading a keyboard or mouse lets a program see every key typed - the admin passphrase included - so the desktop account is NOT in the `input`
# group. The one legitimate need is passing a host keyboard/mouse through to a virtual machine (the VM manager); it gets a temporary ACL on that
# one device, after the passphrase, and gives it back when the VM stops.
INPUT_BY_ID = re.compile(r"^/dev/input/by-id/[A-Za-z0-9_.:+-]{1,150}-event-(kbd|mouse)$")
INPUT_EVENT = re.compile(r"^/dev/input/event[0-9]{1,4}$")
INPUT_GRANT_USER = "user"                                                       # the one desktop account


def _input_device(args):
    path = _str(args, "path", pattern=INPUT_BY_ID)
    real = os.path.realpath(path)
    _require(INPUT_EVENT.match(real) is not None, f"{path} isn't a keyboard or mouse device.")
    return path, real


def input_grant(args, ctx):
    path, real = _input_device(args)
    return {"path": path}, PASSPHRASE, f"Let the desktop account read the keyboard or mouse {os.path.basename(path)} (to pass it to a virtual machine)", \
        [Step(alternatives=[["setfacl", "-m", f"u:{INPUT_GRANT_USER}:r", real]])]


def input_revoke(args, ctx):
    path, real = _input_device(args)
    return {"path": path}, AUTO, f"Take back read access to {os.path.basename(path)}", \
        [Step(alternatives=[["setfacl", "-x", f"u:{INPUT_GRANT_USER}", real]], allow_fail=True)]


def run(args, ctx):
    argv = args.get("argv")
    _require(isinstance(argv, list) and argv, "'argv' must be a non-empty list of strings (no shell).")
    _require(len(argv) <= MAX_ARGV and all(isinstance(a, str) and len(a) <= MAX_ARG_LEN and "\0" not in a for a in argv), "Too many or too long arguments.")
    program = posixpath.basename(argv[0])
    _require(program and program not in FORBIDDEN_PROGRAMS, f"{program or 'that'} can't be started through kronos (no shells, no way to one).")
    check_program = getattr(ctx.app_check, "programs", ctx.app_check)         # the blocked list only: running a program is not installing one
    ok_rpm, why = check_program("rpm", program)
    _require(ok_rpm, why)                                     # not a terminal emulator either
    _require(argv[0].startswith("/") or "/" not in argv[0], "Use a bare command name or an absolute path.")
    cwd = args.get("cwd")
    if cwd is not None:
        cwd = clean_path(cwd)
    stdin = args.get("stdin")
    _require(stdin is None or (isinstance(stdin, str) and len(stdin.encode("utf-8")) <= MAX_FILE_BYTES), "'stdin' must be text under 1 MB.")
    norm = {"argv": argv}
    if cwd:
        norm["cwd"] = cwd
    if stdin is not None:
        norm["stdin"] = stdin
    return norm, PASSPHRASE, "Run " + " ".join(a if len(a) < 40 else a[:37] + "..." for a in argv[:6]) + (" ..." if len(argv) > 6 else ""), \
        [Step(alternatives=[list(argv)], stdin=stdin, cwd=cwd)]


ACTIONS = {
    "pkg.install": pkg_install, "pkg.remove": pkg_remove, "pkg.upgrade": pkg_upgrade,
    "os.upgrade": os_upgrade, "os.rollback": os_rollback, "os.switch": os_switch,
    "flatpak.install": flatpak_install, "flatpak.update": flatpak_update, "flatpak.remove": flatpak_remove,
    "input.grant": input_grant, "input.revoke": input_revoke, "service.control": service_control, "system.power": system_power, "firewall.door": firewall_door, "file.read": file_read, "file.write": file_write, "log.read": log_read, "run": run,
}


class Context:
    def __init__(self, policy=None, app_check=None):
        merged = dict(DEFAULT_POLICY)
        merged.update({k: v for k, v in (policy or {}).items() if k in DEFAULT_POLICY})
        self.policy = merged
        self.app_check = app_check or (lambda kind, name: (True, ""))


def plan(action, args, ctx):
    """A Plan for a request, or raises Refused. The tier is the policy's override if it has one."""
    _require(isinstance(action, str) and action in ACTIONS, f"Unknown action: {str(action)[:60]!r}.")
    _require(isinstance(args, dict), "The arguments must be an object.")
    normalized, tier, description, steps = ACTIONS[action](args, ctx)
    override = ctx.policy.get("tier_overrides", {}).get(action)
    if override in (AUTO, PASSPHRASE):
        tier = PASSPHRASE if (override == PASSPHRASE or tier == PASSPHRASE) else AUTO       # an override may only make it stricter
    return Plan(action, tier, description, steps, normalized)
