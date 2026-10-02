#!/usr/bin/env python3
"""Button and key to touch mapper for Android games (Waydroid).

Android games are made for a touch screen. A *profile* says where on the screen each controller button or keyboard key should tap, and
which stick drives a virtual joystick or looks around. The engine here turns button, key and stick events into touch points; the daemon
(`cosmos-touchmapd`, below) feeds it from the controllers and from the compositor, and sends the touches back to the compositor, which
puts them into the focused Android window. Positions are fractions (0..1) of that window, so they hold at any resolution.

Profile file (JSON, ~/.config/cosmos/android-maps/NAME.json):
    {"name": "Shooter", "aspect": 1.78, "elements": [
        {"type": "tap",   "inputs": ["BTN_SOUTH", "KEY_SPACE"], "x": 0.86, "y": 0.72},
        {"type": "stick", "stick": "left",  "keys": {"up": "KEY_W", "down": "KEY_S", "left": "KEY_A", "right": "KEY_D"}, "x": 0.18, "y": 0.68, "radius": 0.14},
        {"type": "look",  "stick": "right", "x": 0.70, "y": 0.45, "radius": 0.22, "sensitivity": 1.0}]}
Input names are evdev names (BTN_SOUTH, KEY_W, ...) plus L2, R2 and DPAD_UP/DOWN/LEFT/RIGHT for the triggers and the d-pad.
"""
import glob
import json
import os
import re
import socket
import sys
import time

CONFIG_DIR = os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"), "cosmos")
PROFILE_DIR = os.path.join(CONFIG_DIR, "android-maps")
SETTINGS_PATH = os.path.join(CONFIG_DIR, "android-mapper.json")   # {"enabled": bool, "profile": "NAME"}

MAX_TOUCHES = 10
ELEMENT_TYPES = ("tap", "stick", "look")
DIRECTIONS = ("up", "down", "left", "right")
STICKS = ("left", "right")
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._-]{0,39}$")
INPUT_RE = re.compile(r"^[A-Z][A-Z0-9_]{1,31}$")
DEADZONE = 0.25
LOOK_SPEED = 1.6                  # radii per second at full deflection, times the sensitivity
MATCH = ("waydroid",)             # a focused window whose app id or title contains one of these is an Android window


# ---- profiles -------------------------------------------------------------------------------------------------------------------------

def _num(value, low, high, default):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if number != number:          # NaN
        return default
    return min(high, max(low, number))


def _input(value):
    return value if isinstance(value, str) and INPUT_RE.match(value) else None


def clean_profile(raw):
    """A profile from anything: unknown fields dropped, numbers clamped, bad elements skipped."""
    raw = raw if isinstance(raw, dict) else {}
    name = raw.get("name") if isinstance(raw.get("name"), str) and NAME_RE.match(raw["name"]) else "Untitled"
    elements = []
    for item in raw.get("elements") if isinstance(raw.get("elements"), list) else []:
        if not isinstance(item, dict) or item.get("type") not in ELEMENT_TYPES:
            continue
        element = {"type": item["type"], "x": _num(item.get("x"), 0, 1, 0.5), "y": _num(item.get("y"), 0, 1, 0.5)}
        if item["type"] == "tap":
            inputs = [i for i in (_input(v) for v in (item.get("inputs") if isinstance(item.get("inputs"), list) else [])) if i]
            if not inputs:
                continue
            element["inputs"] = inputs[:6]
        else:
            stick = item.get("stick") if item.get("stick") in STICKS else None
            keys = {}
            if isinstance(item.get("keys"), dict):
                keys = {d: _input(item["keys"].get(d)) for d in DIRECTIONS if _input(item["keys"].get(d))}
            if not stick and not keys:
                continue
            element["stick"] = stick
            element["keys"] = keys
            element["radius"] = _num(item.get("radius"), 0.02, 0.5, 0.15)
            if item["type"] == "look":
                element["sensitivity"] = _num(item.get("sensitivity"), 0.1, 5, 1.0)
        elements.append(element)
    return {"name": name, "aspect": _num(raw.get("aspect"), 0.5, 4, 16 / 9), "elements": elements[:40]}


def profile_path(name):
    if not isinstance(name, str) or not NAME_RE.match(name) or ".." in name:
        raise ValueError("a profile name is letters, digits, spaces, dots, dashes and underscores")
    return os.path.join(PROFILE_DIR, name + ".json")


def list_profiles():
    return sorted(os.path.basename(p)[:-5] for p in glob.glob(os.path.join(PROFILE_DIR, "*.json")))


def load_profile(name):
    with open(profile_path(name), encoding="utf-8") as fh:
        profile = clean_profile(json.load(fh))
    profile["name"] = name
    return profile


def save_profile(profile):
    profile = clean_profile(profile)
    path = profile_path(profile["name"])
    os.makedirs(PROFILE_DIR, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(profile, fh, indent=2)
    os.replace(tmp, path)
    return profile


def delete_profile(name):
    try:
        os.remove(profile_path(name))
    except FileNotFoundError:
        pass


def load_settings():
    try:
        with open(SETTINGS_PATH, encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, ValueError):
        raw = {}
    raw = raw if isinstance(raw, dict) else {}
    profile = raw.get("profile") if isinstance(raw.get("profile"), str) and NAME_RE.match(raw["profile"]) else ""
    return {"enabled": bool(raw.get("enabled", False)), "profile": profile}


def save_settings(settings):
    settings = {"enabled": bool(settings.get("enabled")), "profile": settings.get("profile") if NAME_RE.match(settings.get("profile") or "") else ""}
    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(SETTINGS_PATH, "w", encoding="utf-8") as fh:
        json.dump(settings, fh)
    return settings


# ---- the engine -----------------------------------------------------------------------------------------------------------------------

class Mapper:
    """Turns inputs into touch commands. `send(line)` receives "touch down|move|up ID [X Y]" lines. Call on_input / on_axis as events
    arrive and tick(dt) regularly (about 60 times a second)."""

    def __init__(self, profile, send):
        self.profile = clean_profile(profile)
        self.aspect = self.profile["aspect"]
        self.send = send
        self.elements = self.profile["elements"]
        self.held = set()
        self.axes = {"left": (0.0, 0.0), "right": (0.0, 0.0)}
        self.slot = {}       # element index -> touch id
        self.pos = {}        # element index -> last position sent (x, y)
        self.offset = {}     # look elements: where the finger is, relative to the centre, in radii

    # touches
    def _down(self, index, x, y):
        used = set(self.slot.values())
        free = [i for i in range(MAX_TOUCHES) if i not in used]
        if not free:
            return False
        self.slot[index] = free[0]
        self._put("down", index, x, y)
        return True

    def _put(self, verb, index, x, y):
        x, y = min(1.0, max(0.0, x)), min(1.0, max(0.0, y))
        self.pos[index] = (x, y)
        self.send("touch %s %d %.4f %.4f" % (verb, self.slot[index], x, y))

    def _up(self, index):
        if index in self.slot:
            self.send("touch up %d" % self.slot.pop(index))
        self.pos.pop(index, None)
        self.offset.pop(index, None)

    def release_all(self):
        for index in list(self.slot):
            self._up(index)
        self.held.clear()
        self.axes = {"left": (0.0, 0.0), "right": (0.0, 0.0)}

    # events
    def on_input(self, name, pressed):
        if pressed == (name in self.held):
            return
        (self.held.add if pressed else self.held.discard)(name)
        for index, element in enumerate(self.elements):
            if element["type"] == "tap" and name in element["inputs"]:
                down = any(i in self.held for i in element["inputs"])
                if down and index not in self.slot:
                    self._down(index, element["x"], element["y"])
                elif not down:
                    self._up(index)

    def on_axis(self, stick, x, y):
        if stick in self.axes:
            self.axes[stick] = (max(-1.0, min(1.0, x)), max(-1.0, min(1.0, y)))

    def _vector(self, element):
        vx = vy = 0.0
        if element.get("stick"):
            vx, vy = self.axes[element["stick"]]
        keys = element.get("keys", {})
        vx += (keys.get("right") in self.held) - (keys.get("left") in self.held) if keys else 0
        vy += (keys.get("down") in self.held) - (keys.get("up") in self.held) if keys else 0
        length = (vx * vx + vy * vy) ** 0.5
        return (vx / length, vy / length) if length > 1 else (vx, vy)

    def _point(self, element, ox, oy):
        """The screen position for an offset (in radii) from the element's centre; the radius is a fraction of the height."""
        return element["x"] + ox * element["radius"] / self.aspect, element["y"] + oy * element["radius"]

    def tick(self, dt):
        for index, element in enumerate(self.elements):
            if element["type"] == "tap":
                continue
            vx, vy = self._vector(element)
            live = (vx * vx + vy * vy) ** 0.5 > DEADZONE
            if not live:
                self._up(index)
            elif element["type"] == "stick":
                x, y = self._point(element, vx, vy)
                if index not in self.slot:
                    if self._down(index, element["x"], element["y"]):
                        self._put("move", index, x, y)
                elif (x, y) != self.pos.get(index):
                    self._put("move", index, x, y)
            else:
                self._look(index, element, vx, vy, dt)

    def _look(self, index, element, vx, vy, dt):
        """A finger that drifts in the direction of the stick; at the edge it is lifted and put back in the middle, like swiping again."""
        ox, oy = self.offset.get(index, (0.0, 0.0))
        step = LOOK_SPEED * element["sensitivity"] * dt
        ox, oy = ox + vx * step, oy + vy * step
        if index in self.slot and (ox * ox + oy * oy) ** 0.5 > 1:
            self._up(index)
            ox = oy = 0.0
        if index not in self.slot:
            if not self._down(index, element["x"], element["y"]):
                return
        self.offset[index] = (ox, oy)
        self._put("move", index, *self._point(element, ox, oy))


# ---- talking to the compositor ---------------------------------------------------------------------------------------------------------

def socket_path():
    return os.path.join(os.environ.get("XDG_RUNTIME_DIR") or "/tmp", "cosmos-wm.sock")


def wm_command(line, timeout=1.0):
    """One command on its own connection (the compositor answers and closes)."""
    with socket.socket(socket.AF_UNIX) as s:
        s.settimeout(timeout)
        s.connect(socket_path())
        s.sendall(line.encode() + b"\n")
        data = b""
        while not data.endswith(b"\n"):
            chunk = s.recv(4096)
            if not chunk:
                break
            data += chunk
    return data.decode(errors="replace")


def focused_window():
    """(app id, title) of the focused window, or ("", "")."""
    try:
        line = wm_command("focused").rstrip("\n")
    except OSError:
        return "", ""
    app, _, title = line.partition("\t")
    return app, title


def is_android(app, title, match=MATCH):
    text = (app + " " + title).lower()
    return any(m in text for m in match)


class Subscription:
    """The long-lived connection: keys arrive on it, touches go out on it."""

    def __init__(self, app_id):
        self.sock = socket.socket(socket.AF_UNIX)
        self.sock.connect(socket_path())
        self.buf = b""
        self.sock.sendall(b"subscribe keys " + app_id.encode() + b"\n")
        self.sock.setblocking(False)

    def fileno(self):
        return self.sock.fileno()

    def send(self, line):
        try:
            self.sock.sendall(line.encode() + b"\n")
        except BlockingIOError:
            pass    # a full buffer means the compositor is not keeping up; dropping one move is harmless

    def read_keys(self):
        """[(evdev key code, pressed)] that arrived; raises EOFError if the compositor hung up."""
        try:
            data = self.sock.recv(4096)
        except BlockingIOError:
            return []
        if not data:
            raise EOFError
        self.buf += data
        keys = []
        *lines, self.buf = self.buf.split(b"\n")
        for line in lines:
            parts = line.split()
            if len(parts) == 3 and parts[0] == b"key" and parts[1].isdigit():
                keys.append((int(parts[1]), parts[2] == b"1"))
        return keys

    def close(self):
        self.sock.close()


# ---- controllers ----------------------------------------------------------------------------------------------------------------------

HAT = {"ABS_HAT0X": ("DPAD_LEFT", "DPAD_RIGHT"), "ABS_HAT0Y": ("DPAD_UP", "DPAD_DOWN")}
STICK_AXES = {"ABS_X": ("left", 0), "ABS_Y": ("left", 1), "ABS_RX": ("right", 0), "ABS_RY": ("right", 1)}
TRIGGERS = {"ABS_Z": "L2", "ABS_RZ": "R2"}


def code_name(code, kind="key"):
    """'BTN_SOUTH' or 'KEY_W' for an evdev code."""
    from evdev import ecodes
    name = ecodes.bytype[ecodes.EV_KEY if kind == "key" else ecodes.EV_ABS].get(code)
    if isinstance(name, (list, tuple)):
        name = sorted(name, key=lambda n: (not n.startswith("BTN_"), len(n)))[0]
    return name or ""


class PadReader:
    """Reads controllers with evdev and feeds a Mapper. Buttons, triggers as buttons, d-pad hats and both sticks."""

    def __init__(self, mapper, on_guide=None):
        self.mapper = mapper
        self.on_guide = on_guide
        self.devices = []

    def open(self):
        import evdev
        from evdev import ecodes
        for path in evdev.list_devices():
            try:
                dev = evdev.InputDevice(path)
            except OSError:
                continue
            keys = dev.capabilities().get(ecodes.EV_KEY, [])
            if ecodes.BTN_SOUTH in keys or ecodes.BTN_GAMEPAD in keys:
                if not dev.name.startswith(("cosmos", "GameBox", "Virtual")):
                    self.devices.append(dev)
        return self.devices

    def close(self):
        for dev in self.devices:
            dev.close()
        self.devices = []

    def handle(self, dev, event):
        from evdev import ecodes
        if event.type == ecodes.EV_KEY:
            name = code_name(event.code)
            if name == "BTN_MODE":
                if event.value == 1 and self.on_guide:
                    self.on_guide()
            elif name and event.value in (0, 1):
                self.mapper.on_input(name, bool(event.value))
        elif event.type == ecodes.EV_ABS:
            name = code_name(event.code, "abs")
            info = dev.absinfo(event.code)
            span = (info.max - info.min) or 1
            if name in HAT:
                low, high = HAT[name]
                self.mapper.on_input(low, event.value < 0)
                self.mapper.on_input(high, event.value > 0)
            elif name in TRIGGERS:
                self.mapper.on_input(TRIGGERS[name], (event.value - info.min) / span > 0.5)
            elif name in STICK_AXES:
                stick, axis = STICK_AXES[name]
                value = 2 * (event.value - info.min) / span - 1
                current = list(self.mapper.axes[stick])
                current[axis] = value
                self.mapper.on_axis(stick, *current)


# ---- the daemon -----------------------------------------------------------------------------------------------------------------------

def claim_path():
    return os.path.join(os.environ.get("XDG_RUNTIME_DIR") or "/tmp", "cosmos-gamepad-claimed-by")


def _claim():
    """Tell cosmos-input to leave the controllers alone (it also stands down for the launcher this way)."""
    try:
        with open(claim_path(), "x", encoding="utf-8") as fh:
            fh.write(str(os.getpid()))
        return True
    except OSError:
        return False


def _unclaim(mine):
    if mine:
        try:
            os.remove(claim_path())
        except OSError:
            pass


class Daemon:
    """Watches which window has focus. While it is an Android window and a profile is chosen, it takes the keys and controllers and maps
    them to touches; the moment focus leaves, everything is handed back."""

    def __init__(self, poll=0.4, tick=1 / 60):
        self.poll = poll
        self.tick = tick
        self.sub = None
        self.mapper = None
        self.pads = None
        self.claimed = False
        self.app = None

    def settings(self):
        return load_settings()

    def activate(self, app):
        settings = self.settings()
        if not settings["enabled"] or not settings["profile"]:
            return False
        try:
            profile = load_profile(settings["profile"])
            self.sub = Subscription(app)
        except (OSError, ValueError):
            return False
        self.app = app
        self.mapper = Mapper(profile, self.sub.send)
        self.claimed = _claim()
        self.pads = PadReader(self.mapper, on_guide=lambda: wm_command("home"))
        try:
            self.pads.open()
        except Exception:
            self.pads.devices = []
        return True

    def deactivate(self):
        if self.mapper:
            self.mapper.release_all()
        if self.pads:
            self.pads.close()
        if self.sub:
            self.sub.close()
        _unclaim(self.claimed)
        self.sub = self.mapper = self.pads = self.app = None
        self.claimed = False

    def step(self, now, next_poll):
        """One pass; returns the time of the next focus check."""
        import select
        if now >= next_poll:
            app, title = focused_window()
            want = bool(app) and is_android(app, title)
            if self.sub and (not want or app != self.app):
                self.deactivate()
            if want and not self.sub:
                self.activate(app)
            next_poll = now + self.poll
        if not self.sub:
            time.sleep(self.poll / 2)
            return next_poll
        sources = [self.sub] + self.pads.devices
        try:
            ready, _, _ = select.select(sources, [], [], self.tick)
        except (OSError, ValueError):
            self.deactivate()          # a controller was unplugged: reopen the rest on the next pass
            return 0
        try:
            for source in ready:
                if source is self.sub:
                    for code, pressed in self.sub.read_keys():
                        self.mapper.on_input(code_name(code), pressed)
                else:
                    for event in source.read():
                        self.pads.handle(source, event)
        except (EOFError, OSError):
            self.deactivate()
            return 0
        self.mapper.tick(self.tick)
        return next_poll

    def run(self):
        next_poll = 0.0
        try:
            while True:
                next_poll = self.step(time.monotonic(), next_poll)
        finally:
            self.deactivate()


def main():
    import fcntl
    lock = open(os.path.join(os.environ.get("XDG_RUNTIME_DIR") or "/tmp", "cosmos-touchmapd.lock"), "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return 0        # already running
    try:
        Daemon().run()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
