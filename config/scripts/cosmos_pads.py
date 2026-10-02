"""Controller hardware for the Controller Customizer: what is plugged in and what can be changed.

Everything here talks to the kernel's own drivers - nothing is a vendor protocol:

  * lights   - the LED class (/sys/class/leds): the DualShock 4 / DualSense lightbar
               (hid-playstation, a multicolor LED), the player-indicator LEDs on DualSense and
               Switch controllers, the Switch Pro "home" LED, older hid-sony's separate
               red/green/blue LEDs, the Xbox ring where xpad exposes it;
  * rumble   - the input subsystem's force feedback (evdev FF_RUMBLE);
  * motion   - the controllers' gyroscope/accelerometer, which the kernel exposes as a second
               evdev device next to the pad ("... Motion Sensors" on PlayStation pads,
               "... (IMU)" on Switch ones);
  * battery  - /sys/class/power_supply, where the driver has one.

What the kernel does NOT offer: the DualSense mic-mute light and rebinding buttons (that is
cosmos-input's job). DualSense adaptive triggers and haptics are not kernel features either but are
done from userspace in cosmos_dualsense.py.

The sysfs root can be moved (COSMOS_SYSFS_ROOT) so the tests run against a fake tree.
"""
import colorsys
import glob
import json
import math
import os
import subprocess

try:
    import evdev
    from evdev import ecodes, ff
except ImportError:          # the tests and the settings code work without it
    evdev = ecodes = ff = None

VENDOR_SONY = 0x054C
VENDOR_NINTENDO = 0x057E
VENDOR_MICROSOFT = 0x045E

EFFECTS = ("solid", "breathe", "blink", "rainbow", "off")
LOW_BATTERY = 15
PRESETS = (
    ("Pink", (255, 0, 144)), ("Red", (255, 32, 32)), ("Orange", (255, 128, 0)),
    ("Yellow", (255, 220, 0)), ("Green", (32, 255, 64)), ("Cyan", (0, 220, 255)),
    ("Blue", (32, 64, 255)), ("Purple", (150, 50, 255)), ("White", (255, 255, 255)),
)
DEFAULT_SETTINGS = {"color": [255, 0, 144], "brightness": 100, "effect": "solid",
                    "players": None, "home": None, "triggers": None, "speed": 5, "battery_color": False}


def sysfs_root():
    return os.environ.get("COSMOS_SYSFS_ROOT") or "/sys"


# --------------------------------------------------------------------------- writing sysfs

def write_attr(path, value):
    """Write a sysfs attribute. udev makes the LED files group-writable for `input` (see the
    Containerfile); if this account can't write anyway that is all - this account has no sudo, and a light colour isn't worth admin rights."""
    text = str(value)
    try:
        with open(path, "w", encoding="ascii") as fh:
            fh.write(text)
        return True
    except OSError:
        pass
    return False


def read_attr(path, default=None):
    try:
        with open(path, encoding="ascii", errors="replace") as fh:
            return fh.read().strip()
    except OSError:
        return default


# --------------------------------------------------------------------------- LEDs

class Led:
    """One LED (or, for the RGB kinds, one light made of several)."""

    def __init__(self, kind, paths, name, max_brightness=255, multicolor=False):
        self.kind = kind                  # "rgb" | "player" | "home" | "single"
        self.paths = paths                # rgb legacy: [red, green, blue] dirs; else one dir
        self.name = name
        self.max = max_brightness
        self.multicolor = multicolor

    def __repr__(self):
        return f"Led({self.kind}, {self.name})"

    # -- RGB
    def set_rgb(self, r, g, b, level=1.0):
        """Show colour (r, g, b) at brightness `level` (0..1)."""
        level = max(0.0, min(1.0, level))
        chans = [max(0, min(255, int(c))) for c in (r, g, b)]
        if self.multicolor:
            d = self.paths[0]
            ok = write_attr(os.path.join(d, "multi_intensity"), " ".join(str(c) for c in chans))
            return write_attr(os.path.join(d, "brightness"), round(self.max * level)) and ok
        ok = True
        for d, c in zip(self.paths, chans):
            ok = write_attr(os.path.join(d, "brightness"), round(c / 255 * level * self.max)) and ok
        return ok

    def set_level(self, value):
        return write_attr(os.path.join(self.paths[0], "brightness"), max(0, min(self.max, int(value))))

    def level(self):
        try:
            return int(read_attr(os.path.join(self.paths[0], "brightness"), "0"))
        except ValueError:
            return 0


def _max_brightness(directory):
    try:
        return int(read_attr(os.path.join(directory, "max_brightness"), "255"))
    except ValueError:
        return 255


def find_leds(hid_dir):
    """The LEDs the kernel attached to this controller's device directory, sorted into kinds."""
    leds_dir = os.path.join(hid_dir, "leds")
    try:
        entries = sorted(os.listdir(leds_dir))
    except OSError:
        return []
    found, legacy = [], {}
    for entry in entries:
        d = os.path.join(leds_dir, entry)
        low = entry.lower()
        if os.path.exists(os.path.join(d, "multi_intensity")):
            found.append(Led("rgb", [d], entry, _max_brightness(d), multicolor=True))
        elif "player" in low:
            found.append(Led("player", [d], entry, _max_brightness(d)))
        elif low.endswith(":home") or low.endswith("home"):
            found.append(Led("home", [d], entry, _max_brightness(d)))
        elif low.rsplit(":", 1)[-1] in ("red", "green", "blue"):
            legacy[low.rsplit(":", 1)[-1]] = d       # older hid-sony: one LED per channel
        else:
            found.append(Led("single", [d], entry, _max_brightness(d)))
    if len(legacy) == 3:
        found.insert(0, Led("rgb", [legacy["red"], legacy["green"], legacy["blue"]], "lightbar",
                            _max_brightness(legacy["red"])))
    players = sorted((led for led in found if led.kind == "player"), key=lambda led: led.name)
    return [led for led in found if led.kind != "player"] + players


# --------------------------------------------------------------------------- controllers

FAMILIES = (
    (VENDOR_SONY, {0x05C4, 0x09CC, 0x0BA0}, "ds4", "DualShock 4"),
    (VENDOR_SONY, {0x0CE6, 0x0DF2}, "dualsense", "DualSense"),
    (VENDOR_NINTENDO, None, "switch", "Nintendo Switch"),
    (VENDOR_MICROSOFT, None, "xbox", "Xbox"),
)


def family_of(vendor, product):
    for v, products, key, label in FAMILIES:
        if vendor == v and (products is None or product in products):
            return key, label
    return "other", "Controller"


class Pad:
    def __init__(self, name, vendor, product, uniq, event_path, hid_dir):
        self.name = name
        self.vendor = vendor
        self.product = product
        self.uniq = uniq or ""
        self.event_path = event_path
        self.hid_dir = hid_dir
        self.family, self.family_label = family_of(vendor, product)
        self.leds = find_leds(hid_dir) if hid_dir else []
        self.motion_path = None
        self.can_rumble = False

    @property
    def key(self):
        """What its settings are stored under: this very controller when it has a unique id
        (its Bluetooth address), otherwise every controller of that model."""
        return self.uniq.lower() or f"{self.vendor:04x}:{self.product:04x}"

    @property
    def model_key(self):
        return f"{self.vendor:04x}:{self.product:04x}"

    def rgb(self):
        return next((led for led in self.leds if led.kind == "rgb"), None)

    def players(self):
        return [led for led in self.leds if led.kind == "player"]

    def home(self):
        return next((led for led in self.leds if led.kind == "home"), None)

    def battery(self):
        """(percent or None, status text) from the driver's power_supply, or None if it has none."""
        for d in glob.glob(os.path.join(self.hid_dir or "/nonexistent", "power_supply", "*")):
            cap = read_attr(os.path.join(d, "capacity"))
            status = read_attr(os.path.join(d, "status"), "") or ""
            try:
                return int(cap), status
            except (TypeError, ValueError):
                return None, status
        return None

    def capabilities(self):
        caps = []
        if self.rgb():
            caps.append("light colour")
        if self.players():
            caps.append("player lights")
        if self.home():
            caps.append("home light")
        if self.family == "dualsense":
            caps.append("adaptive triggers")
            caps.append("haptics (USB)")
        if self.can_rumble:
            caps.append("rumble")
        if self.motion_path:
            caps.append("gyro + accelerometer")
        return caps


def _hid_dir_of(event_name):
    """The device directory the input node hangs off (…/0003:054C:0CE6.0005), where the kernel
    also puts the LEDs and the battery."""
    node = os.path.join(sysfs_root(), "class", "input", event_name, "device")
    try:
        inputdir = os.path.realpath(node)
    except OSError:
        return None
    parent = os.path.dirname(inputdir)
    if os.path.basename(parent) == "input":
        return os.path.dirname(parent)
    return os.path.dirname(inputdir)


def _is_motion(dev):
    name = dev.name.lower()
    if "motion sensors" in name or "(imu)" in name or name.endswith(" imu"):
        return True
    try:
        return 6 in dev.input_props()               # INPUT_PROP_ACCELEROMETER
    except Exception:
        return False


def _is_gamepad(dev):
    caps = dev.capabilities()
    keys = caps.get(ecodes.EV_KEY, [])
    return ecodes.BTN_SOUTH in keys or ecodes.BTN_GAMEPAD in keys or ecodes.BTN_A in keys


def discover():
    """Every connected controller with what can be changed on it."""
    if evdev is None:
        return []
    pads, motions = {}, []
    for path in evdev.list_devices():
        try:
            dev = evdev.InputDevice(path)
        except OSError:
            continue
        event = os.path.basename(path)
        hid = _hid_dir_of(event)
        try:
            if _is_motion(dev):
                motions.append((hid, dev.uniq, path))
            elif _is_gamepad(dev):
                pad = Pad(dev.name, dev.info.vendor, dev.info.product, dev.uniq, path, hid)
                pad.can_rumble = ecodes.EV_FF in dev.capabilities()
                pads[path] = pad
        finally:
            dev.close()
    for hid, uniq, path in motions:
        for pad in pads.values():
            if pad.motion_path is None and ((hid and hid == pad.hid_dir) or (uniq and uniq == pad.uniq)):
                pad.motion_path = path
                break
    return sorted(pads.values(), key=lambda p: p.event_path)


# --------------------------------------------------------------------------- effects

def hsv(h, s=1.0, v=1.0):
    r, g, b = colorsys.hsv_to_rgb(h % 1.0, s, v)
    return round(r * 255), round(g * 255), round(b * 255)


def effect_period(speed):
    """Seconds for one cycle of breathe/rainbow at animation speed 1 (slow) .. 10 (fast); 5 = 4 s."""
    try:
        return 20.0 / max(1, min(10, int(speed)))
    except (TypeError, ValueError):
        return 4.0


def effect_frame(effect, color, t, speed=5):
    """(r, g, b, level) for `effect` at time `t` seconds."""
    r, g, b = color
    period = effect_period(speed)
    if effect == "off":
        return r, g, b, 0.0
    if effect == "breathe":
        return r, g, b, 0.15 + 0.85 * (0.5 - 0.5 * math.cos(2 * math.pi * t / period))
    if effect == "blink":               # on for the first half of each (quarter-length) cycle
        return r, g, b, 1.0 if (t % (period / 4)) < period / 8 else 0.0
    if effect == "rainbow":
        rr, gg, bb = hsv(t / (period * 2))
        return rr, gg, bb, 1.0
    return r, g, b, 1.0


def battery_look(battery):
    """(colour, effect) that shows the battery on the lightbar: red through green with the charge,
    breathing while charging, blinking red when nearly empty. None when there is no reading."""
    if not battery or battery[0] is None:
        return None
    pct, status = battery
    color = hsv(max(0, min(100, pct)) / 100 * 0.33)
    status = (status or "").lower()
    if status == "charging":
        return color, "breathe"
    if status != "full" and pct <= LOW_BATTERY:
        return (255, 0, 0), "blink"
    return color, "solid"


def resolve_look(pad, s, battery=None):
    """(colour, effect) to show right now: the saved ones, or the battery's when that is switched on
    (and the driver reports a battery)."""
    if s.get("battery_color"):
        look = battery_look(battery if battery is not None else pad.battery())
        if look:
            return look
    return tuple(s["color"]), s["effect"]


def player_mask(count, total):
    """The first `count` of `total` player lights on (0 = none)."""
    return [i < count for i in range(total)]


# --------------------------------------------------------------------------- settings

def settings_path():
    import cosmos_profiles
    return cosmos_profiles.profile_path("controllers.json")


def load_settings(path=None):
    try:
        with open(path or settings_path(), encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_settings(data, path=None):
    path = path or settings_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=1, sort_keys=True)
    os.replace(tmp, path)


def settings_for(data, pad):
    """This controller's settings: its own entry, else the one for its model, else defaults."""
    out = dict(DEFAULT_SETTINGS)
    entry = data.get(pad.key) or data.get(pad.model_key) or {}
    for k in DEFAULT_SETTINGS:
        if k in entry:
            out[k] = entry[k]
    try:
        out["speed"] = max(1, min(10, int(out["speed"])))
    except (TypeError, ValueError):
        out["speed"] = 5
    out["battery_color"] = bool(out["battery_color"])
    if out["effect"] not in EFFECTS:
        out["effect"] = "solid"
    try:
        out["color"] = [max(0, min(255, int(c))) for c in out["color"]][:3]
        assert len(out["color"]) == 3
    except (TypeError, ValueError, AssertionError):
        out["color"] = list(DEFAULT_SETTINGS["color"])
    try:
        out["brightness"] = max(0, min(100, int(out["brightness"])))
    except (TypeError, ValueError):
        out["brightness"] = 100
    return out


def apply_settings(pad, s, t=0.0, battery=None):
    """Put the saved look on the controller (one frame of it, for animated effects)."""
    ok = True
    rgb = pad.rgb()
    if rgb:
        color, effect = resolve_look(pad, s, battery)
        r, g, b, level = effect_frame(effect, color, t, s["speed"])
        ok = rgb.set_rgb(r, g, b, level * s["brightness"] / 100) and ok
    players = pad.players()
    if players and s["players"] is not None:
        for led, on in zip(players, player_mask(int(s["players"]), len(players))):
            ok = led.set_level(led.max if on else 0) and ok
    home = pad.home()
    if home and s["home"] is not None:
        ok = home.set_level(round(home.max * int(s["home"]) / 100)) and ok
    if pad.family == "dualsense":
        import cosmos_dualsense
        left, right = cosmos_dualsense.triggers_from_settings(s)
        if left is not None:
            ok = cosmos_dualsense.send_triggers(pad, left, right) and ok
    return ok


# --------------------------------------------------------------------------- rumble

class Rumbler:
    """Holds one controller's event node open (force-feedback effects live only as long as the
    file descriptor does) and plays timed rumble on it."""

    def __init__(self, pad):
        self.dev = None
        self.effect_id = -1
        if evdev is None or not pad.can_rumble:
            return
        try:
            self.dev = evdev.InputDevice(pad.event_path)
        except OSError:
            self.dev = None

    @property
    def usable(self):
        return self.dev is not None

    def play(self, strong=0.8, weak=0.8, ms=600):
        """Buzz once (the driver ends it after `ms`); True if the driver took the effect."""
        if not self.dev:
            return False
        try:
            effect = ff.Effect(ecodes.FF_RUMBLE, self.effect_id, 0, ff.Trigger(0, 0), ff.Replay(ms, 0),
                               ff.EffectType(ff_rumble_effect=ff.Rumble(strong_magnitude=int(0xFFFF * strong),
                                                                        weak_magnitude=int(0xFFFF * weak))))
            self.effect_id = self.dev.upload_effect(effect)
            self.dev.write(ecodes.EV_FF, self.effect_id, 1)
            return True
        except OSError:
            return False

    def close(self):
        if self.dev:
            try:
                if self.effect_id >= 0:
                    self.dev.erase_effect(self.effect_id)
                self.dev.close()
            except OSError:
                pass
            self.dev = None
