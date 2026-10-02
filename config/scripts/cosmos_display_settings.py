#!/usr/bin/env python3
"""Reading and writing the display choices (Settings > Display): the compositor's own control socket gives the current state and applies a change at
once, and ~/.config/cosmos/display.conf makes it stick (cosmos-wm reads it whenever an output appears). Nothing here needs a display connection.

display.conf lines this writes:    mode = 1920x1080        scale = 1.5        animations = off
Lines it does not write (a per-output `mode NAME = ...`, comments) are kept exactly as they were."""
import os
import re
import tempfile

SCALES = ("1", "1.25", "1.5", "1.75", "2", "2.5", "3")
CONF_PATH = os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"), "cosmos", "display.conf")


def parse_modes(reply):
    """[{"width", "height", "hz", "state"}] from the compositor's `modes` reply ("1920x1080<TAB>60.00<TAB>current|preferred|")."""
    modes = []
    for line in (reply or "").splitlines():
        parts = line.split("\t")
        match = re.fullmatch(r"(\d+)x(\d+)", parts[0]) if parts else None
        if not match or len(parts) < 2:
            continue
        try:
            hz = float(parts[1])
        except ValueError:
            continue
        modes.append({"width": int(match.group(1)), "height": int(match.group(2)), "hz": hz, "state": parts[2] if len(parts) > 2 else ""})
    return modes


def sizes(modes):
    """The distinct sizes, largest first, each with the mode to use for it (the rate nearest 60 Hz): [(width, height, mode)]."""
    best = {}
    for m in modes:
        key = (m["width"], m["height"])
        if key not in best or abs(m["hz"] - 60) < abs(best[key]["hz"] - 60):
            best[key] = m
    return [(w, h, best[(w, h)]) for (w, h) in sorted(best, key=lambda k: (-k[0] * k[1], -k[0]))]


def current_size(modes):
    for m in modes:
        if m["state"] == "current":
            return m["width"], m["height"]
    return None


def parse_scale_reply(reply):
    """(name, (pixel_w, pixel_h), scale, (logical_w, logical_h)) of the first output from a `scale` reply, or None."""
    for line in (reply or "").splitlines():
        match = re.fullmatch(r"(\S+) (\d+)x(\d+) ([0-9.]+) (\d+)x(\d+)", line.strip())
        if match:
            g = match.groups()
            return g[0], (int(g[1]), int(g[2])), float(g[3]), (int(g[4]), int(g[5]))
    return None


def scale_label(value):
    return "Automatic" if value == "auto" else f"{int(float(value) * 100)}%"


def read_conf(path=None):
    """{"mode": "1920x1080" or None, "scale": "1.5" or None, "animations": "on"/"off" or None} - the general lines only."""
    found = {"mode": None, "scale": None, "animations": None}
    try:
        with open(path or CONF_PATH, encoding="utf-8") as fh:
            for line in fh:
                match = re.fullmatch(r"\s*(mode|scale|animations)\s*=\s*(\S+)\s*", line)
                if match:
                    found[match.group(1)] = match.group(2)
    except OSError:
        pass
    return found


def write_conf(mode="keep", scale="keep", animations="keep", path=None):
    """Sets (a value), removes (None) or keeps ("keep") the general `mode`, `scale` and `animations` lines; every other line is left as it is.
    Written atomically."""
    path = path or CONF_PATH
    try:
        with open(path, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
    except OSError:
        lines = []
    for key, value in (("mode", mode), ("scale", scale), ("animations", animations)):
        if value == "keep":
            continue
        lines = [l for l in lines if not re.fullmatch(rf"\s*{key}\s*=\s*\S+\s*", l)]
        if value is not None:
            lines.append(f"{key} = {value}")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".display.conf.")
    with os.fdopen(handle, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + ("\n" if lines else ""))
    os.replace(temporary, path)
