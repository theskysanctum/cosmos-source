#!/usr/bin/env python3
"""cosmos-input - use CosmOS with nothing but a game controller.

Reads real gamepads at the evdev level (independent of which window has
focus) and turns them into a virtual keyboard + mouse, injected
system-wide through UInput - so every app on the desktop, the greeter and
the lock screen respond to a controller without knowing it exists. The
button meanings are GameBox's own (its launcher/input/inputd.py), so a
controller feels the same in both:

  A            Enter            d-pad / left stick   arrow keys (held = repeat)
  B            Escape           right stick (up/down) scroll wheel
  X            Delete           Guide/Home           Help (opens help overlays)
  L1 / R1      [ and ]          Y                    Tab (next focus - CosmOS extra)
  Select       on-screen keyboard (toggle)
  hold R2      Alt: d-pad/left stick then cycle the window switcher (Alt+Tab;
               right/down = next, left/up = previous), releasing R2 commits, B
               cancels; X while holding R2 closes the focused window (Alt+X).
               Guide (Help) toggles the gridscreen (raises it over the windows / lowers it) in the desktop session
               (cosmos-wm) - the same as the Windows key.

Pointer mode - cosmos-wm's only keyboard shortcuts are Alt+Tab, Alt+X and the Windows key, so
the bar and windows need a mouse: HOLD L2 and the left stick
becomes the pointer, A is a left click and B a right click (release L2 to
go back to navigating). Held, not toggled: you can never get stuck with
the stick doing the wrong thing.

Two things it deliberately does NOT do:
  * It stands down while GameBox's own input daemon is running (GameBox
    runs its own copy of this same idea inside its own display), so a
    controller never types everything twice.
  * It stands down while a CosmOS surface reads controllers natively - the
    launcher writes $XDG_RUNTIME_DIR/cosmos-gamepad-claimed-by (its PID) for as
    long as it has keyboard focus, so its own navigation isn't doubled by the
    arrow keys this would send it.
  * It stands down while the on-screen keyboard is open - the keyboard
    reads the pad itself and would otherwise fight the arrow keys this
    sends to whatever is behind it.

Unlike GameBox's daemon there is no emulator virtual-pad bridge here; that
belongs to GameBox. This is only the desktop's controller layer.

Vendored from GameBox's launcher input code (inputd.py, gamepad.py,
stick_mode.py, osk_watcher.py) and trimmed/extended for the desktop;
config/input/ in this repo is the copy that ships in the image.
"""
import argparse
import glob
import logging
import os
import select
import subprocess
import sys
import time

try:
    import evdev
    from evdev import ecodes
except ImportError:  # keeps the logic importable (and testable) without evdev installed
    evdev = None
    ecodes = None

log = logging.getLogger("cosmos-input")

OUTPUT_NAME = "cosmos-virtual-input"
# GameBox's own virtual gamepads (see its inputd.py) report real BTN_SOUTH
# like any controller - never treat them as a physical pad.
VIRTUAL_NAME_PREFIXES = ("CosmOS Pad ", OUTPUT_NAME, "console-os-", "cosmos-osk-")
GAMEBOX_DAEMON_MARKER = "/input/run.py"

MOUSE_SENSITIVITY = 12
MOUSE_DEADZONE = 4000
NAV_THRESHOLD = 20000
SCROLL_DEADZONE = 12000
SCROLL_INTERVAL_S = 0.12
INITIAL_REPEAT_DELAY_S = 0.5
REPEAT_INTERVAL_S = 0.1
TICK_S = 0.03
RESCAN_INTERVAL_S = 2.0
STANDDOWN_CHECK_S = 1.0
STICK_MIN, STICK_MAX = -32768, 32767
TRIGGER_PRESSED_FRACTION = 0.6


def _key(name):
    return getattr(ecodes, name)


def action_map():
    """One-shot buttons -> the key they tap (GameBox's ACTION_BUTTON_MAP,
    minus L2/R2 - L2 is the pointer modifier here - plus Y as Tab)."""
    return {
        _key("BTN_SOUTH"): _key("KEY_ENTER"),
        _key("BTN_EAST"): _key("KEY_ESC"),
        _key("BTN_WEST"): _key("KEY_DELETE"),
        _key("BTN_NORTH"): _key("KEY_TAB"),
        _key("BTN_TL"): _key("KEY_LEFTBRACE"),
        _key("BTN_TR"): _key("KEY_RIGHTBRACE"),
        _key("BTN_MODE"): _key("KEY_HELP"),
    }


def nav_keys():
    return {"up": _key("KEY_UP"), "down": _key("KEY_DOWN"), "left": _key("KEY_LEFT"), "right": _key("KEY_RIGHT")}


def dpad_buttons():
    return {_key("BTN_DPAD_UP"): "up", _key("BTN_DPAD_DOWN"): "down",
            _key("BTN_DPAD_LEFT"): "left", _key("BTN_DPAD_RIGHT"): "right"}


def normalize_abs(device, code, value):
    """Rescale to the signed 16-bit range everything here assumes, using the
    device's own reported min/max (a real 8BitDo in DInput mode reports 0-255
    - see GameBox's inputd.py for the confirmed case)."""
    info = device.absinfo(code) if device is not None else None
    if info is None or info.max == info.min:
        return value
    return int(STICK_MIN + (value - info.min) / (info.max - info.min) * (STICK_MAX - STICK_MIN))


class Emitter:
    """The virtual keyboard+mouse. Real one wraps UInput; tests pass a recorder
    with the same three methods so no real input event ever leaves the test."""

    def __init__(self):
        keys = sorted(set(action_map().values()) | set(nav_keys().values()) |
                      {_key("BTN_LEFT"), _key("BTN_RIGHT"), _key("KEY_LEFTALT"), _key("KEY_LEFTSHIFT"),
                       _key("KEY_X")})
        caps = {ecodes.EV_KEY: keys,
                ecodes.EV_REL: [ecodes.REL_X, ecodes.REL_Y, ecodes.REL_WHEEL]}
        self.ui = evdev.UInput(caps, name=OUTPUT_NAME)

    def key(self, code, value):
        self.ui.write(ecodes.EV_KEY, code, value)
        self.ui.syn()

    def rel(self, dx=0, dy=0, wheel=0):
        if dx:
            self.ui.write(ecodes.EV_REL, ecodes.REL_X, dx)
        if dy:
            self.ui.write(ecodes.EV_REL, ecodes.REL_Y, dy)
        if wheel:
            self.ui.write(ecodes.EV_REL, ecodes.REL_WHEEL, wheel)
        self.ui.syn()

    def close(self):
        self.ui.close()


class Translator:
    """Pure controller-state -> output logic. Feed it events with on_event()
    (stick/trigger values already normalized) and call tick() regularly."""

    def __init__(self, out):
        self.out = out
        self.actions = action_map()
        self.navs = nav_keys()
        self.dpad_codes = dpad_buttons()
        self.reset()

    def reset(self):
        """Forget everything held - also what standing down calls, so nothing
        stays pressed while another input owner takes over."""
        if getattr(self, "mouse_down", None):
            for code in list(self.mouse_down):
                self.out.key(code, 0)
        self.x = self.y = self.rx = self.ry = 0
        self.hat_x = self.hat_y = 0
        self.dpad = {"up": False, "down": False, "left": False, "right": False}
        self.l2_button = False
        self.l2_axis = False
        if getattr(self, "chord", False):
            self.out.key(ecodes.KEY_LEFTALT, 0)
        self.chord = False        # R2 held = the Alt chord (window switcher)
        self.r2_button = False
        self.r2_axis = False
        self.mouse_down = set()
        self.next_repeat = {}
        self.next_scroll = 0.0

    @property
    def pointer_mode(self):
        return self.l2_button or self.l2_axis

    def tap(self, code):
        self.out.key(code, 1)
        self.out.key(code, 0)

    def on_event(self, etype, code, value):
        if etype == ecodes.EV_KEY:
            self._on_button(code, value)
        elif etype == ecodes.EV_ABS:
            if code == ecodes.ABS_X:
                self.x = value
            elif code == ecodes.ABS_Y:
                self.y = value
            elif code == ecodes.ABS_RX:
                self.rx = value
            elif code == ecodes.ABS_RY:
                self.ry = value
            elif code == ecodes.ABS_HAT0X:
                self.hat_x = value
            elif code == ecodes.ABS_HAT0Y:
                self.hat_y = value
            elif code == ecodes.ABS_Z:
                pressed = value > STICK_MIN + (STICK_MAX - STICK_MIN) * TRIGGER_PRESSED_FRACTION
                self._set_l2(axis=pressed)
            elif code == ecodes.ABS_RZ:
                pressed = value > STICK_MIN + (STICK_MAX - STICK_MIN) * TRIGGER_PRESSED_FRACTION
                self._set_r2(axis=pressed)

    def _set_r2(self, button=None, axis=None):
        if button is not None:
            self.r2_button = button
        if axis is not None:
            self.r2_axis = axis
        held = self.r2_button or self.r2_axis
        if held != self.chord:
            self.chord = held
            self.out.key(ecodes.KEY_LEFTALT, 1 if held else 0)

    def _set_l2(self, button=None, axis=None):
        before = self.pointer_mode
        if button is not None:
            self.l2_button = button
        if axis is not None:
            self.l2_axis = axis
        if before and not self.pointer_mode:
            self._release_mouse()

    def _release_mouse(self):
        for code in list(self.mouse_down):
            self.out.key(code, 0)
        self.mouse_down.clear()

    def _on_button(self, code, value):
        if code == ecodes.BTN_TL2:
            self._set_l2(button=bool(value))
            return
        if code == ecodes.BTN_TR2:
            self._set_r2(button=bool(value))
            return
        if self.chord and code == ecodes.BTN_WEST:   # Alt is held: X = Alt+X, close the window
            if value == 1:
                self.tap(ecodes.KEY_X)
            return
        if code in self.dpad_codes:
            self.dpad[self.dpad_codes[code]] = bool(value)
            return
        if self.pointer_mode and code in (ecodes.BTN_SOUTH, ecodes.BTN_EAST):
            mouse = ecodes.BTN_LEFT if code == ecodes.BTN_SOUTH else ecodes.BTN_RIGHT
            if value == 1:
                self.mouse_down.add(mouse)
                self.out.key(mouse, 1)
            elif value == 0 and mouse in self.mouse_down:
                self.mouse_down.discard(mouse)
                self.out.key(mouse, 0)
            return
        if value == 1 and code in self.actions:
            self.tap(self.actions[code])

    def _held(self):
        dp = {"up": self.hat_y == -1 or self.dpad["up"], "down": self.hat_y == 1 or self.dpad["down"],
              "left": self.hat_x == -1 or self.dpad["left"], "right": self.hat_x == 1 or self.dpad["right"]}
        if not self.pointer_mode:  # left stick = arrows only when it isn't the pointer
            dp["up"] = dp["up"] or self.y < -NAV_THRESHOLD
            dp["down"] = dp["down"] or self.y > NAV_THRESHOLD
            dp["left"] = dp["left"] or self.x < -NAV_THRESHOLD
            dp["right"] = dp["right"] or self.x > NAV_THRESHOLD
        return dp

    def step(self, direction):
        """One navigation press: an arrow key - or, with R2 held, Tab / Shift+Tab
        through the window switcher."""
        if not self.chord:
            self.tap(self.navs[direction])
        elif direction in ("right", "down"):
            self.tap(ecodes.KEY_TAB)
        else:
            self.out.key(ecodes.KEY_LEFTSHIFT, 1)
            self.tap(ecodes.KEY_TAB)
            self.out.key(ecodes.KEY_LEFTSHIFT, 0)

    def tick(self, now):
        for direction, active in self._held().items():
            if not active:
                self.next_repeat.pop(direction, None)
            elif direction not in self.next_repeat:
                self.step(direction)
                self.next_repeat[direction] = now + INITIAL_REPEAT_DELAY_S
            elif now >= self.next_repeat[direction]:
                self.step(direction)
                self.next_repeat[direction] = now + REPEAT_INTERVAL_S

        if self.pointer_mode:
            dx = int(self.x / STICK_MAX * MOUSE_SENSITIVITY) if abs(self.x) > MOUSE_DEADZONE else 0
            dy = int(self.y / STICK_MAX * MOUSE_SENSITIVITY) if abs(self.y) > MOUSE_DEADZONE else 0
            if dx or dy:
                self.out.rel(dx=dx, dy=dy)

        if abs(self.ry) > SCROLL_DEADZONE and now >= self.next_scroll:
            self.out.rel(wheel=1 if self.ry < 0 else -1)   # stick up = scroll up
            self.next_scroll = now + SCROLL_INTERVAL_S


def is_gamepad(device):
    if device.name.startswith(VIRTUAL_NAME_PREFIXES):
        return False
    keys = device.capabilities().get(ecodes.EV_KEY, [])
    return ecodes.BTN_SOUTH in keys or ecodes.BTN_GAMEPAD in keys


def find_gamepads():
    found = []
    for path in evdev.list_devices():
        try:
            dev = evdev.InputDevice(path)
        except OSError:
            continue
        if is_gamepad(dev):
            found.append(dev)
    return found


def ui_claim_path():
    return os.path.join(os.environ.get("XDG_RUNTIME_DIR") or "/tmp", "cosmos-gamepad-claimed-by")


def ui_claimed_pad():
    """True while a CosmOS surface has claimed the controllers (see the module
    docstring). Keyed on a live PID, so a crashed claimant can't strand us."""
    try:
        with open(ui_claim_path(), encoding="utf-8") as fh:
            pid = int(fh.read().split()[0])
    except (OSError, ValueError, IndexError):
        return False
    if pid == os.getpid():
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        pass
    return True


def gamebox_input_running():
    """True while GameBox's own input daemon (launcher .../input/run.py) is up.
    Its cmdline is scanned rather than a flag file so a crash can't leave the
    desktop's controller layer disabled forever."""
    me = os.getpid()
    for path in glob.glob("/proc/[0-9]*/cmdline"):
        try:
            pid = int(path.split("/")[2])
            if pid == me:
                continue
            with open(path, "rb") as fh:
                args = fh.read().split(b"\0")
        except (OSError, ValueError):
            continue
        # `python3 .../input/run.py` exactly - not any command line that merely
        # mentions the path (a shell, an editor).
        if (args and os.path.basename(args[0]).startswith(b"python")
                and any(a.endswith(GAMEBOX_DAEMON_MARKER.encode()) for a in args[1:3])):
            return True
    return False


class OskController:
    """Select toggles the on-screen keyboard process; while it's alive the
    daemon stands down (see the module docstring)."""

    def __init__(self, script):
        self.script = script
        self.proc = None

    @property
    def open(self):
        if self.proc is not None and self.proc.poll() is not None:
            self.proc = None
        return self.proc is not None

    def toggle(self):
        if self.open:
            log.info("Select: closing the on-screen keyboard")
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
            self.proc = None
        else:
            log.info("Select: opening the on-screen keyboard")
            self.proc = subprocess.Popen([sys.executable, self.script])

    def close(self):
        if self.open:
            self.proc.terminate()


def run(out, osk, once=False, osk_enabled=True):
    translator = Translator(out)
    by_path = {}
    next_rescan = 0.0
    next_check = 0.0
    standing_down = False
    gamebox_up = False
    while True:
        now = time.monotonic()
        if now >= next_rescan:
            current = {d.path: d for d in find_gamepads()}
            for path, dev in current.items():
                if path not in by_path:
                    log.info("gamepad connected: %s (%s)", dev.name, path)
                    by_path[path] = dev
            for path in list(by_path):
                if path not in current:
                    log.info("gamepad disconnected: %s", by_path[path].name)
                    del by_path[path]
            next_rescan = now + RESCAN_INTERVAL_S
        if now >= next_check:
            now_up = gamebox_input_running()
            if now_up != gamebox_up:
                log.info("GameBox's input daemon %s - %s", "is running" if now_up else "has stopped",
                         "standing down" if now_up else "taking over")
                gamebox_up = now_up
            next_check = now + STANDDOWN_CHECK_S

        devices = list(by_path.values())
        ready = []
        if devices:
            try:
                ready, _, _ = select.select(devices, [], [], TICK_S)
            except (OSError, ValueError):
                log.warning("a controller likely just disconnected")
        else:
            time.sleep(TICK_S)

        stand_down = gamebox_up or osk.open or ui_claimed_pad()
        if stand_down and not standing_down:
            translator.reset()
        standing_down = stand_down

        for dev in ready:
            try:
                events = list(dev.read())
            except OSError:
                by_path.pop(dev.path, None)
                continue
            if standing_down:
                continue   # still drained above, so nothing piles up
            for ev in events:
                if ev.type == ecodes.EV_KEY and ev.code == ecodes.BTN_SELECT and ev.value == 1:
                    if osk_enabled:
                        osk.toggle()
                    continue
                value = ev.value
                if ev.type == ecodes.EV_ABS and ev.code in (ecodes.ABS_X, ecodes.ABS_Y, ecodes.ABS_RX,
                                                            ecodes.ABS_RY, ecodes.ABS_Z, ecodes.ABS_RZ):
                    value = normalize_abs(dev, ev.code, ev.value)
                translator.on_event(ev.type, ev.code, value)

        if not standing_down:
            translator.tick(time.monotonic())
        if once:
            return


def main():
    ap = argparse.ArgumentParser(prog="cosmos-input", description=__doc__.split("\n\n")[0])
    ap.add_argument("--no-osk", action="store_true", help="don't open the on-screen keyboard on Select")
    ap.add_argument("--check", action="store_true", help="list detected gamepads and exit")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="cosmos-input: %(message)s", stream=sys.stderr)
    if evdev is None:
        sys.exit("cosmos-input: python evdev is not installed")
    if args.check:
        pads = find_gamepads()
        print(f"{len(pads)} gamepad(s)")
        for d in pads:
            print(f"  {d.name}  ({d.path}, vendor {d.info.vendor:04x})")
        return
    osk = OskController(os.path.join(os.path.dirname(os.path.realpath(__file__)), "cosmos_osk.py"))
    try:
        out = Emitter()
    except (OSError, PermissionError) as exc:
        sys.exit(f"cosmos-input: can't create the virtual input device ({exc}) - is /dev/uinput accessible?")
    log.info("started; hold L2 for pointer mode, Select for the on-screen keyboard")
    try:
        run(out, osk, osk_enabled=not args.no_osk)
    except KeyboardInterrupt:
        pass
    finally:
        osk.close()
        out.close()


if __name__ == "__main__":
    main()
