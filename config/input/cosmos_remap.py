"""Button remapping for the four virtual gamepads ("CosmOS Pad 1" .. "CosmOS Pad 4").

GameBox's input daemon (input/inputd.py) mirrors every real controller onto one of those four
fixed virtual pads, in connection order - the pads every emulator is configured against. This
module is the mapping applied on the way: it decides, per virtual pad ("Player N"), which button
a physical press turns into, which stick is which, and which axes are inverted. The Controller
Customizer (cosmos-controllers) edits the file; inputd.py imports Remapper and consults it for
every mirrored event, picking up edits within a second without a restart.

The mapping is per virtual pad, not per physical controller: it belongs to the player slot,
whichever controller currently sits in it.

File (per profile, controller-remap.json; only what differs from "no change" is stored):
    {"1": {"buttons": {"BTN_SOUTH": "BTN_EAST", "BTN_EAST": "BTN_SOUTH", "BTN_THUMBL": null},
           "swap_sticks": false, "invert": ["ABS_Y"]}, "2": {...}}
A button mapped to null is disabled. `invert` lists virtual-pad axes whose direction is reversed
(as the game sees them, after any stick swap).

Pure Python, no evdev import: the event codes below are kernel ABI numbers.
"""
import json
import os
import time

SLOTS = 4
RELOAD_S = 1.0

# (name, code, label): the twelve buttons the virtual pad has (VIRTUAL_PAD_BUTTONS in inputd.py).
BUTTONS = (
    ("BTN_SOUTH", 304, "Bottom face (A / Cross)"),
    ("BTN_EAST", 305, "Right face (B / Circle)"),
    ("BTN_WEST", 308, "Left face (X / Square)"),
    ("BTN_NORTH", 307, "Top face (Y / Triangle)"),
    ("BTN_TL", 310, "L1 / LB"),
    ("BTN_TR", 311, "R1 / RB"),
    ("BTN_TL2", 312, "L2 / LT"),
    ("BTN_TR2", 313, "R2 / RT"),
    ("BTN_SELECT", 314, "Select / Back / Share"),
    ("BTN_START", 315, "Start / Menu / Options"),
    ("BTN_THUMBL", 317, "Left stick click (L3)"),
    ("BTN_THUMBR", 318, "Right stick click (R3)"),
)
BUTTON_CODE = {name: code for name, code, _ in BUTTONS}
BUTTON_NAME = {code: name for name, code, _ in BUTTONS}
BUTTON_LABEL = {name: label for name, _, label in BUTTONS}

ABS_X, ABS_Y, ABS_RX, ABS_RY = 0, 1, 3, 4
AXES = (("ABS_X", ABS_X, "Left stick left/right"), ("ABS_Y", ABS_Y, "Left stick up/down"),
        ("ABS_RX", ABS_RX, "Right stick left/right"), ("ABS_RY", ABS_RY, "Right stick up/down"))
AXIS_CODE = {name: code for name, code, _ in AXES}
AXIS_NAME = {code: name for name, code, _ in AXES}
STICK_SWAP = {ABS_X: ABS_RX, ABS_RX: ABS_X, ABS_Y: ABS_RY, ABS_RY: ABS_Y}
STICK_MIN, STICK_MAX = -32768, 32767


def config_path():
    if os.environ.get("COSMOS_REMAP_FILE"):
        return os.environ["COSMOS_REMAP_FILE"]
    try:
        import cosmos_profiles
        return cosmos_profiles.profile_path("controller-remap.json")
    except Exception:
        return os.path.join(os.path.expanduser("~"), ".config", "cosmos", "controller-remap.json")


def normalize(cfg):
    """A clean per-slot config from whatever was in the file (unknown names are dropped)."""
    cfg = cfg if isinstance(cfg, dict) else {}
    buttons = {}
    raw = cfg.get("buttons")
    for src, dst in (raw.items() if isinstance(raw, dict) else ()):
        if src in BUTTON_CODE and (dst is None or dst in BUTTON_CODE) and dst != src:
            buttons[src] = dst
    invert = [a for a in (cfg.get("invert") or []) if a in AXIS_CODE] if isinstance(cfg.get("invert"), list) else []
    return {"buttons": buttons, "swap_sticks": bool(cfg.get("swap_sticks")), "invert": sorted(set(invert))}


def is_default(cfg):
    return not cfg["buttons"] and not cfg["swap_sticks"] and not cfg["invert"]


def load(path=None):
    """{slot number (1-4): normalized config} for the slots that have any change."""
    try:
        with open(path or config_path(), encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, ValueError):
        return {}
    out = {}
    for slot in range(1, SLOTS + 1):
        cfg = normalize(raw.get(str(slot))) if isinstance(raw, dict) else normalize(None)
        if not is_default(cfg):
            out[slot] = cfg
    return out


def save(slots, path=None):
    path = path or config_path()
    data = {str(n): cfg for n, cfg in slots.items() if 1 <= n <= SLOTS and not is_default(normalize(cfg))}
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=1, sort_keys=True)
    os.replace(tmp, path)


class SlotRemap:
    """The mapping for one virtual pad, in event codes."""

    def __init__(self, cfg):
        cfg = normalize(cfg)
        self.buttons = {BUTTON_CODE[s]: (BUTTON_CODE[d] if d else None) for s, d in cfg["buttons"].items()}
        self.swap_sticks = cfg["swap_sticks"]
        self.invert = {AXIS_CODE[a] for a in cfg["invert"]}

    def button(self, code):
        """The virtual button a physical button press becomes, or None to drop it."""
        return self.buttons.get(code, code)

    def axis(self, code, value):
        """(virtual axis, value) for a stick movement; d-pad axes and anything else pass through."""
        if self.swap_sticks:
            code = STICK_SWAP.get(code, code)
        if code in self.invert:
            value = max(STICK_MIN, min(STICK_MAX, -value))
        return code, value


IDENTITY = SlotRemap({})


class Remapper:
    """What inputd.py holds: the four slot mappings, re-read when the file changes."""

    def __init__(self, path=None):
        self.path = path
        self.mtime = None
        self.checked = 0.0
        self.slots = {}
        self.reload(force=True)

    def reload(self, force=False):
        now = time.monotonic()
        if not force and now - self.checked < RELOAD_S:
            return
        self.checked = now
        path = self.path or config_path()
        try:
            mtime = os.stat(path).st_mtime_ns
        except OSError:
            mtime = None
        if mtime != self.mtime or force:
            self.mtime = mtime
            self.slots = {n: SlotRemap(cfg) for n, cfg in load(path).items()}

    def for_slot(self, index):
        """The mapping for virtual pad `index` (0-based, as inputd.py numbers them)."""
        self.reload()
        return self.slots.get(index + 1, IDENTITY)
