"""Shared scanning for find-terminals and find-blocked-apps: find programs the app policy says may never be installed.

A "blocked" group in app-policy.json (terminal emulators, Heroic Games Launcher, ...) lists RPM names, Flatpak ids and AppImage names. This
looks for them where they'd actually be - desktop entries (system, Flatpak exports, the user's own), programs in the bin directories, the
x-terminal-emulator alternative - so a copy that arrived by any route (a dependency, a download, a Flatpak) is found even though the install
paths refused the known names. A desktop entry in the freedesktop category TerminalEmulator counts as a terminal whatever it is called.
"""
import fnmatch
import glob
import os
import re
import shlex
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import cosmos_app_policy as policy  # noqa: E402

BIN_DIRS = ("usr/local/bin", "usr/bin", "usr/local/sbin", "usr/sbin", "bin")
APP_DIRS = ("usr/share/applications", "usr/local/share/applications", "var/lib/flatpak/exports/share/applications")
USER_APP_DIRS = (".local/share/applications", ".local/share/flatpak/exports/share/applications")


def _matches(patterns, name, case_sensitive=False):
    if not case_sensitive:
        name = name.lower()
    return any(fnmatch.fnmatchcase(name, p if case_sensitive else p.lower()) for p in patterns or [])


def parse_desktop(path):
    entry, in_main = {}, False
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if line.startswith("["):
                    in_main = line == "[Desktop Entry]"
                elif in_main and "=" in line and not line.startswith("#"):
                    k, _, v = line.partition("=")
                    entry.setdefault(k.strip(), v.strip())
    except OSError:
        pass
    return entry


def exec_command(exec_line):
    """(program basename, flatpak app id or None) from an Exec= line."""
    try:
        words = shlex.split(exec_line)
    except ValueError:
        words = exec_line.split()
    words = [w for w in words if not re.fullmatch(r"%[A-Za-z%]", w)]
    i = 0
    while i < len(words) and (words[i] in ("env", "exec") or "=" in words[i].split("/")[0]):
        i += 1
    if i >= len(words):
        return "", None
    prog = os.path.basename(words[i])
    if prog == "flatpak":
        ids = [w for w in words[i + 1:] if re.fullmatch(r"[A-Za-z0-9_-]+(\.[A-Za-z0-9_-]+){2,}", w) and not w.startswith("-")]
        return prog, (ids[0] if ids else None)
    return prog, None


def owner(groups, kind, name, case_sensitive):
    """The name of the blocked group whose list matches `name`."""
    for g in groups:
        if _matches(g.get(kind), name, case_sensitive):
            return g.get("name", "")
    return ""


def scan(root="/", blocked=None, alt_terminal=False):
    """Everything on this system/root that matches the blocked groups given (default: all of them). Each finding says which group."""
    root = root.rstrip("/") or "/"
    blocked = policy.load_blocked() if blocked is None else blocked
    rpm_names = [n for g in blocked for n in g.get("rpm", [])] + [n for g in blocked for n in g.get("appimage", [])]
    flatpak_ids = [n for g in blocked for n in g.get("flatpak", [])]
    dirs = [os.path.join(root, d) for d in APP_DIRS]
    if root == "/":
        dirs += [os.path.join(os.path.expanduser("~"), d) for d in USER_APP_DIRS]
    found, seen = [], set()
    for d in dirs:
        for desktop in sorted(glob.glob(os.path.join(d, "*.desktop"))):
            entry = parse_desktop(desktop)
            prog, fp_id = exec_command(entry.get("Exec", ""))
            desktop_id = os.path.basename(desktop)[:-len(".desktop")]
            categories = [c for c in entry.get("Categories", "").split(";") if c]
            reasons = []
            group = None
            if "TerminalEmulator" in categories and any(g.get("scan", "terminal") == "terminal" for g in blocked):
                reasons.append("desktop category TerminalEmulator")
                group = "Terminal emulators"
            if fp_id and _matches(flatpak_ids, fp_id, case_sensitive=True):
                reasons.append("blocked Flatpak id")
                group = group or owner(blocked, "flatpak", fp_id, True)
            elif not fp_id and prog and _matches(rpm_names, prog):
                reasons.append("blocked program name")
                group = group or owner(blocked, "rpm", prog, False)
            if "/flatpak/" in desktop and _matches(flatpak_ids, desktop_id, case_sensitive=True) and "blocked Flatpak id" not in reasons:
                reasons.append("blocked Flatpak id")
                group = group or owner(blocked, "flatpak", desktop_id, True)
            if reasons:
                key = ("desktop", desktop_id)
                if key not in seen:
                    seen.add(key)
                    found.append({"kind": "flatpak" if (fp_id or "/flatpak/" in desktop) else "desktop", "id": fp_id or desktop_id,
                                  "desktop": desktop, "name": entry.get("Name", ""), "why": reasons, "group": group or ""})
    for bindir in BIN_DIRS:
        base = os.path.join(root, bindir)
        try:
            names = os.listdir(base)
        except OSError:
            continue
        for name in names:
            if _matches(rpm_names, name) and ("bin", name) not in seen:
                path = os.path.join(base, name)
                if os.path.isfile(path) or os.path.islink(path):
                    seen.add(("bin", name))
                    found.append({"kind": "program", "id": name, "desktop": None, "name": name, "why": ["blocked program present"],
                                  "group": owner(blocked, "rpm", name, False) or ""})
    alt = os.path.join(root, "etc/alternatives/x-terminal-emulator")
    if os.path.lexists(alt) and ("alt", "x") not in seen:
        found.append({"kind": "program", "id": "x-terminal-emulator", "desktop": None, "name": "x-terminal-emulator",
                      "why": ["x-terminal-emulator alternative is set"], "group": "Terminal emulators"})
    return found


def purge_flatpaks(found, run=subprocess.run):
    """Uninstall the Flatpak terminals in `found`; returns the ids removed."""
    removed = []
    for item in found:
        if item["kind"] != "flatpak" or not re.fullmatch(r"[A-Za-z0-9_.-]+", item["id"] or ""):
            continue
        for scope in ("--user", "--system"):
            try:
                done = run(["flatpak", "uninstall", scope, "--noninteractive", "-y", item["id"]], capture_output=True, text=True, timeout=300)
            except (OSError, subprocess.SubprocessError):
                continue
            if done.returncode == 0:
                removed.append(item["id"])
                break
    return removed


