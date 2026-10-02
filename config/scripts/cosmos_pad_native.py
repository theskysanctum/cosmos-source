"""Native controller input for CosmOS's own GTK surfaces (the launcher).

Reads gamepads straight from evdev, on the GLib main loop, and feeds them through
cosmos-input's own Translator - so the buttons mean exactly what they mean
everywhere else in CosmOS - but into an emitter that calls the widget code
directly instead of injecting a virtual keyboard. `enabled()` says whether this
surface should be handling the pad right now (it has focus); while it is not,
events are drained and dropped, so nothing piles up and nothing fires late.

Pointer MODE (hold L2 to reinterpret A/B as mouse clicks) is not offered here:
the launcher is fully navigable without a pointer, and L2 held down would
otherwise swallow A/B as mouse clicks. The cursor itself still moves on the
right stick, always, independently of that mode - a real report that nothing
in the gridscreen could be reached without one confirmed the "fully navigable
without a pointer" assumption doesn't hold everywhere. tick() drives this
directly off the shared Translator's own rx/ry state (already normalized by
feed() regardless of pointer mode) rather than through Translator.tick()'s own
pointer_mode-gated rel() call, so A/B keep meaning confirm/back here no matter
what - only cursor movement is unconditional, never clicks.
"""
import logging
import os
import sys
import time

from gi.repository import GLib

log = logging.getLogger("cosmos-pad-native")

RESCAN_MS = 2000
TICK_MS = 30
MOUSE_SENSITIVITY = 12
MOUSE_DEADZONE = 4000
STICK_MAX = 32767


def _import_cosmos_input():
    here = os.path.dirname(os.path.abspath(__file__))
    for directory in (os.environ.get("COSMOS_INPUT_DIR"), "/opt/cosmos-input", "/usr/lib/cosmos",
                      os.path.join(here, "..", "input")):
        if directory and os.path.exists(os.path.join(directory, "cosmos_input.py")):
            if directory not in sys.path:
                sys.path.insert(0, directory)
            import cosmos_input
            return cosmos_input
    raise ImportError("cosmos_input.py not found (set COSMOS_INPUT_DIR)")


class NativePads:
    def __init__(self, emitter, enabled, on_select=None, on_r2=None):
        self.ci = _import_cosmos_input()
        self.ecodes = self.ci.ecodes
        self.emitter = emitter
        self.translator = self.ci.Translator(emitter)
        self.enabled = enabled
        self.on_select = on_select
        self.on_r2 = on_r2        # R2 pressed: the launcher goes back to the last window
        self.r2_down = False
        self.devices = {}    # path -> (device, watch id)
        self.was_enabled = False

    def start(self):
        if self.ci.evdev is None:
            log.warning("python evdev not installed - controller input disabled")
            return
        self.rescan()
        GLib.timeout_add(RESCAN_MS, self.rescan)
        GLib.timeout_add(TICK_MS, self.tick)

    def rescan(self):
        current = {d.path: d for d in self.ci.find_gamepads()}
        for path, dev in current.items():
            if path not in self.devices:
                watch = GLib.io_add_watch(dev.fd, GLib.PRIORITY_DEFAULT, GLib.IO_IN | GLib.IO_HUP | GLib.IO_ERR,
                                          self.on_readable, path)
                self.devices[path] = (dev, watch)
        for path in list(self.devices):
            if path not in current:
                self.drop(path)
        return True

    def drop(self, path):
        dev, watch = self.devices.pop(path)
        GLib.source_remove(watch)
        try:
            dev.close()
        except OSError:
            pass

    def on_readable(self, _fd, condition, path):
        entry = self.devices.get(path)
        if entry is None:
            return False
        dev = entry[0]
        if condition & (GLib.IO_HUP | GLib.IO_ERR):
            self.drop(path)
            return False
        try:
            events = list(dev.read())
        except OSError:
            self.drop(path)
            return False
        if self.enabled():
            for ev in events:
                self.feed(dev, ev)
        return True

    def feed(self, dev, ev):
        e = self.ecodes
        if ev.type == e.EV_KEY and ev.code == e.BTN_SELECT and ev.value == 1:
            if self.on_select:
                self.on_select()
            return
        if ev.code in (e.BTN_TL2, e.ABS_Z):   # no pointer mode here
            return
        if ev.code in (e.BTN_TR2, e.ABS_RZ):  # cosmos-input's window-switch chord is a no-op here;
            if ev.code == e.BTN_TR2:           # R2 instead means "back to the last window"
                pressed = ev.value == 1
            else:
                pressed = self.ci.normalize_abs(dev, ev.code, ev.value) > 0.6 * self.ci.STICK_MAX
            if pressed and not self.r2_down and self.on_r2:
                self.on_r2()
            self.r2_down = pressed
            return
        value = ev.value
        if ev.type == e.EV_ABS and ev.code in (e.ABS_X, e.ABS_Y, e.ABS_RX, e.ABS_RY):
            value = self.ci.normalize_abs(dev, ev.code, ev.value)
        self.translator.on_event(ev.type, ev.code, value)

    def tick(self):
        now_enabled = bool(self.enabled())
        if self.was_enabled and not now_enabled:
            self.translator.reset()      # nothing stays "held" across a focus change
        self.was_enabled = now_enabled
        if now_enabled:
            self.translator.tick(time.monotonic())
            self._tick_cursor()
        return True

    def _tick_cursor(self):
        """Right stick always moves a real cursor here, regardless of pointer_mode (which stays
        permanently off in this context - see this module's own docstring on why A/B must keep
        meaning confirm/back, never clicks). Reads the shared Translator's own rx/ry - already
        normalized and reset-on-focus-change by it - rather than tracking a second copy; only the
        motion itself bypasses Translator.tick()'s pointer_mode gate. Same deadzone/sensitivity
        curve as cosmos_input's own pointer mode, for one consistent feel everywhere a stick
        drives a cursor in CosmOS."""
        rx, ry = self.translator.rx, self.translator.ry
        dx = int(rx / STICK_MAX * MOUSE_SENSITIVITY) if abs(rx) > MOUSE_DEADZONE else 0
        dy = int(ry / STICK_MAX * MOUSE_SENSITIVITY) if abs(ry) > MOUSE_DEADZONE else 0
        if dx or dy:
            self.emitter.rel(dx=dx, dy=dy)
