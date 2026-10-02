#!/usr/bin/env python3
"""CosmOS Controller Customizer - lights, rumble and motion for the controllers plugged in.

  cosmos-controllers            open the window
  cosmos-controllers --daemon   run in the background: puts each controller's saved look on it
                                whenever it connects and plays the animated effects (breathe,
                                rainbow). Started by the session; the window starts it if needed.
  cosmos-controllers --demo     the window with pretend controllers (for looking at it)

Per controller (DualSense, DualShock 4, Switch Pro / Joy-Con, Xbox, anything else): light colour,
brightness, speed and effect (solid / breathe / blink / rainbow / off), or the battery level shown on it,
the button remapper for the four GameBox virtual pads, on the lightbar where there is one, the
player-number lights, the Switch home light, a rumble test, battery, and a live view of the gyroscope
and accelerometer. Settings are saved in the active profile (controllers.json) and follow the
controller by its Bluetooth address, or by model when it has none. What the kernel drivers do not
offer (the DualSense mic light) is not here - see cosmos_pads.py.
"""
import fcntl
import math
import os
import subprocess
import sys
import tempfile
import time

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402
import cosmos_display  # noqa: E402
import cosmos_settings_link  # noqa: E402

import cosmos_dualsense as cds
import cosmos_pads as cp


def _import_remap():
    here = os.path.dirname(os.path.abspath(__file__))
    for directory in (os.environ.get("COSMOS_INPUT_DIR"), "/opt/cosmos-input", "/usr/lib/cosmos", os.path.join(here, "..", "input")):
        if directory and os.path.exists(os.path.join(directory, "cosmos_remap.py")):
            if directory not in sys.path:
                sys.path.insert(0, directory)
            import cosmos_remap
            return cosmos_remap
    return None


cr = _import_remap()

BG = "#0a0e16"
FG = "#e8ecf4"
ACCENT = "#ff0090"
BORDER = "#2a3a56"
RESCAN_MS = 2000
SETTINGS_POLL_MS = 400
ANIM_MS = 60
BATTERY_POLL_S = 10

CSS = f"""
window.cosmos-pads {{ background-color: {BG}; }}
.cp-title {{ color: {FG}; font-weight: bold; font-size: 22px; }}
.cp-head {{ color: {FG}; font-weight: bold; font-size: 16px; }}
.cp-text {{ color: {FG}; }}
.cp-note {{ color: {FG}; opacity: 0.7; }}
.cp-card {{ background-color: alpha({FG}, 0.04); border: 1px solid {BORDER}; border-radius: 8px; padding: 12px; }}
list.cp-pads, list.cp-pads row {{ background-color: {BG}; color: {FG}; }}
list.cp-pads row {{ border: 1px solid {BORDER}; border-radius: 8px; padding: 8px 12px; margin-bottom: 6px; }}
list.cp-pads row:selected {{ background-color: alpha({ACCENT}, 0.25); border-color: {ACCENT}; }}
.cp-button {{ background-color: {BG}; background-image: none; color: {FG}; border: 1px solid {BORDER};
             box-shadow: none; font-weight: bold; padding: 8px 16px; }}
.cp-button:focus, .cp-button:hover, .cp-button:checked {{ border-color: {ACCENT}; }}
.cp-button:checked {{ background-color: alpha({ACCENT}, 0.35); }}
.cp-small {{ padding: 6px 10px; }}
.cp-swatch {{ min-width: 44px; min-height: 44px; border-radius: 22px; border: 3px solid {BORDER};
             background-image: none; box-shadow: none; padding: 0; }}
.cp-swatch:focus, .cp-swatch:hover {{ border-color: {FG}; }}
stackswitcher button {{ background-color: {BG}; background-image: none; color: {FG}; border: 1px solid {BORDER};
                       box-shadow: none; padding: 8px 12px; }}
stackswitcher button:checked {{ background-color: alpha({ACCENT}, 0.35); border-color: {ACCENT}; }}
scale trough {{ background-color: {BORDER}; }}
scale highlight {{ background-color: {ACCENT}; }}
scale value, scale marks {{ color: {FG}; }}
combobox, combobox button {{ color: {FG}; }}
combobox button {{ background-color: {BG}; background-image: none; border: 1px solid {BORDER}; box-shadow: none; }}
.cp-hit {{ color: {ACCENT}; font-weight: bold; }}
scrolledwindow.cp-scroll, scrolledwindow.cp-scroll viewport {{ background-color: transparent; }}
"""


# --------------------------------------------------------------------------- the background applier

def runtime_dir():
    return os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir()


def daemon_running():
    """True while a daemon holds the lock (the lock dies with its process)."""
    try:
        fd = os.open(os.path.join(runtime_dir(), "cosmos-controllers.lock"), os.O_RDWR | os.O_CREAT, 0o600)
    except OSError:
        return False
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    except OSError:
        return True
    finally:
        os.close(fd)


def start_daemon():
    if not daemon_running():
        try:
            # This file, not sys.argv[0]: inside CosmOS Settings argv[0] is cosmos-settings, which would start another Settings for every change made.
            subprocess.Popen([os.path.realpath(__file__), "--daemon"], start_new_session=True,
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError:
            pass


class Applier:
    """Keeps the saved look on whatever is connected: applies it when a controller appears or its
    settings change, and steps the animated effects."""

    def __init__(self, discover=cp.discover, settings_file=None):
        self.discover = discover
        self.settings_file = settings_file
        self.pads = {}
        self.mtime = None
        self.settings = {}
        self.last = {}
        self.batteries = {}          # event path -> (read at, reading): sysfs is not polled at animation rate
        self.t0 = time.monotonic()

    def _load(self):
        path = self.settings_file or cp.settings_path()
        try:
            mtime = os.stat(path).st_mtime_ns
        except OSError:
            mtime = None
        changed = mtime != self.mtime
        if changed:
            self.mtime = mtime
            self.settings = cp.load_settings(path)
        return changed

    def rescan(self):
        current = {p.event_path: p for p in self.discover()}
        new = [p for path, p in current.items() if path not in self.pads]
        self.pads = current
        for pad in new:
            cp.apply_settings(pad, cp.settings_for(self.settings, pad))

    def poll_settings(self):
        if self._load():
            for pad in self.pads.values():
                cp.apply_settings(pad, cp.settings_for(self.settings, pad))

    def battery(self, pad, now):
        at, reading = self.batteries.get(pad.event_path, (None, None))
        if at is None or now - at > BATTERY_POLL_S:
            reading = pad.battery()
            self.batteries[pad.event_path] = (now, reading)
        return reading

    def animate(self):
        now = time.monotonic()
        t = now - self.t0
        for pad in self.pads.values():
            s = cp.settings_for(self.settings, pad)
            rgb = pad.rgb()
            if not rgb:
                continue
            battery = self.battery(pad, now) if s["battery_color"] else None
            color, effect = cp.resolve_look(pad, s, battery or (None, ""))
            animated = effect in ("breathe", "rainbow", "blink")
            if not animated and not s["battery_color"]:
                continue                      # a plain saved look was applied when it was set
            r, g, b, level = cp.effect_frame(effect, color, t, s["speed"])
            level = level * s["brightness"] / 100
            frame = (r, g, b, round(level * 50))
            if self.last.get(pad.event_path) != frame:     # each write is a USB/Bluetooth report
                self.last[pad.event_path] = frame
                rgb.set_rgb(r, g, b, level)


def run_daemon():
    lock = os.open(os.path.join(runtime_dir(), "cosmos-controllers.lock"), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return 0                      # one is already running
    applier = Applier()
    applier._load()
    applier.rescan()
    GLib.timeout_add(RESCAN_MS, lambda: (applier.rescan(), True)[1])
    GLib.timeout_add(SETTINGS_POLL_MS, lambda: (applier.poll_settings(), True)[1])
    GLib.timeout_add(ANIM_MS, lambda: (applier.animate(), True)[1])
    GLib.MainLoop().run()
    return 0


# --------------------------------------------------------------------------- motion view

class MotionView(Gtk.DrawingArea):
    """Live accelerometer (tilt bubble + bars) and gyroscope (bars) from the controller's motion
    device. `Zero` takes the current reading as the resting position (display only)."""

    AXES_ACC = ("ABS_X", "ABS_Y", "ABS_Z")
    AXES_GYRO = ("ABS_RX", "ABS_RY", "ABS_RZ")

    def __init__(self):
        super().__init__()
        self.set_size_request(420, 240)
        self.dev = None
        self.watch = None
        self.values = {}
        self.ranges = {}
        self.zero = {}
        self.demo = False
        self.connect("draw", self.on_draw)

    def open(self, path):
        self.close()
        if not path or cp.evdev is None:
            return False
        try:
            self.dev = cp.evdev.InputDevice(path)
        except OSError:
            return False
        for code, info in self.dev.capabilities().get(cp.ecodes.EV_ABS, []):
            name = cp.ecodes.ABS.get(code)
            if isinstance(name, list):
                name = name[0]
            self.values[name] = info.value
            self.ranges[name] = max(abs(info.min), abs(info.max), 1)
        self.watch = GLib.io_add_watch(self.dev.fd, GLib.IO_IN, self.on_readable)
        return True

    def close(self):
        if self.watch:
            GLib.source_remove(self.watch)
            self.watch = None
        if self.dev:
            self.dev.close()
            self.dev = None
        self.values, self.ranges, self.zero = {}, {}, {}

    def on_readable(self, *_):
        try:
            for ev in self.dev.read():
                if ev.type == cp.ecodes.EV_ABS:
                    name = cp.ecodes.ABS.get(ev.code)
                    if isinstance(name, list):
                        name = name[0]
                    self.values[name] = ev.value
        except (OSError, BlockingIOError):
            pass
        self.queue_draw()
        return True

    def set_demo(self, on):
        first = on and not self.demo
        self.demo = on
        if first:
            self.ranges = {n: 32768 for n in self.AXES_ACC + self.AXES_GYRO}
            GLib.timeout_add(50, self._demo_tick)

    def _demo_tick(self):
        t = time.monotonic()
        self.ranges = {n: 32768 for n in self.AXES_ACC + self.AXES_GYRO}
        self.values = {"ABS_X": 9000 * math.sin(t), "ABS_Y": 5000 * math.cos(t * 0.7), "ABS_Z": 26000,
                       "ABS_RX": 14000 * math.sin(t * 2), "ABS_RY": 8000 * math.sin(t * 1.3),
                       "ABS_RZ": 3000 * math.cos(t * 3)}
        self.queue_draw()
        return self.get_mapped() or True

    def set_zero(self):
        self.zero = {k: v for k, v in self.values.items()}

    def norm(self, name):
        v = self.values.get(name, 0) - self.zero.get(name, 0)
        return max(-1.0, min(1.0, v / self.ranges.get(name, 1)))

    def on_draw(self, widget, cr):
        w, h = self.get_allocated_width(), self.get_allocated_height()
        fg = Gdk.RGBA()
        fg.parse(FG)
        acc = Gdk.RGBA()
        acc.parse(ACCENT)
        border = Gdk.RGBA()
        border.parse(BORDER)
        if not self.values:
            cr.set_source_rgba(fg.red, fg.green, fg.blue, 0.7)
            cr.move_to(20, h / 2)
            cr.show_text("No motion data from this controller.")
            return False
        # tilt bubble: gravity direction from the accelerometer
        cx, cy, radius = 20 + h * 0.4, h / 2, h * 0.38
        cr.set_source_rgba(border.red, border.green, border.blue, 1)
        cr.set_line_width(2)
        cr.arc(cx, cy, radius, 0, 2 * math.pi)
        cr.stroke()
        cr.move_to(cx - radius, cy)
        cr.line_to(cx + radius, cy)
        cr.move_to(cx, cy - radius)
        cr.line_to(cx, cy + radius)
        cr.stroke()
        ax, ay = self.norm("ABS_X"), self.norm("ABS_Y")
        cr.set_source_rgba(acc.red, acc.green, acc.blue, 1)
        cr.arc(cx + ax * radius * 0.9, cy - ay * radius * 0.9, 12, 0, 2 * math.pi)
        cr.fill()
        # bars
        x0, bar_w = cx + radius + 40, w - (cx + radius + 40) - 20
        y = 24
        for label, names in (("Accelerometer", self.AXES_ACC), ("Gyroscope", self.AXES_GYRO)):
            cr.set_source_rgba(fg.red, fg.green, fg.blue, 1)
            cr.move_to(x0, y)
            cr.show_text(label)
            y += 16
            for axis, name in zip("XYZ", names):
                n = self.norm(name)
                cr.set_source_rgba(border.red, border.green, border.blue, 1)
                cr.rectangle(x0 + 14, y, bar_w - 14, 14)
                cr.fill()
                cr.set_source_rgba(acc.red, acc.green, acc.blue, 1)
                mid = x0 + 14 + (bar_w - 14) / 2
                cr.rectangle(mid if n >= 0 else mid + n * (bar_w - 14) / 2, y, abs(n) * (bar_w - 14) / 2, 14)
                cr.fill()
                cr.set_source_rgba(fg.red, fg.green, fg.blue, 1)
                cr.move_to(x0, y + 12)
                cr.show_text(axis)
                y += 20
            y += 14
        return False


# --------------------------------------------------------------------------- the window

def swatch_button(rgb, on_click, tip):
    btn = Gtk.Button()
    btn.get_style_context().add_class("cp-swatch")
    prov = Gtk.CssProvider()
    prov.load_from_data(f".cp-swatch {{ background-color: rgb({rgb[0]},{rgb[1]},{rgb[2]}); }}".encode())
    btn.get_style_context().add_provider(prov, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    btn.set_tooltip_text(tip)
    btn.connect("clicked", lambda *_: on_click(rgb))
    return btn


def label(text, css="cp-text", wrap=False):
    lab = Gtk.Label(label=text, xalign=0)
    lab.get_style_context().add_class(css)
    lab.set_line_wrap(wrap)
    return lab


class Window(Gtk.Window):
    def __init__(self, demo=False):
        super().__init__(title="Controller Customizer")
        self.get_style_context().add_class("cosmos-pads")
        cosmos_display.size_window(self, 960, 640)
        self.demo = demo
        self.pads = []
        self.pad = None
        self.data = cp.load_settings(self.settings_file())
        self.rumbler = None
        self.pending = None
        self.building = False

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin=18)
        self.add(root)
        root.pack_start(label("Controller Customizer", "cp-title"), False, False, 0)
        body = Gtk.Box(spacing=16)
        root.pack_start(body, True, True, 0)

        left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        left.set_size_request(240, -1)
        body.pack_start(left, False, False, 0)
        left.pack_start(label("Controllers", "cp-head"), False, False, 0)
        self.listbox = Gtk.ListBox()
        self.listbox.get_style_context().add_class("cp-pads")
        self.listbox.connect("row-selected", self.on_row)
        left.pack_start(self.listbox, True, True, 0)
        if cr:
            rb = Gtk.Button(label="Button remapper (Player 1-4)")
            rb.get_style_context().add_class("cp-button")
            rb.connect("clicked", self.show_remapper)
            left.pack_start(rb, False, False, 0)

        self.right = Gtk.Stack()
        body.pack_start(self.right, True, True, 0)
        self.empty = label("No controller found.\nPlug one in or pair it over Bluetooth "
                           "(Bluetooth settings are in the tray).", "cp-note", wrap=True)
        self.right.add_named(self.empty, "empty")
        self.right.add_named(self.build_detail(), "detail")
        self.remap_watches = []
        self.in_remap = False
        self.remap_slot = 1
        self.remap_data = cr.load(self.remap_file()) if cr else {}
        if cr:
            self.right.add_named(self.build_remapper(), "remap")

        self.refresh()
        if not demo:
            GLib.timeout_add(RESCAN_MS, self.refresh)
        self.connect("destroy", self.on_destroy)

    def remap_file(self):
        return os.path.join(tempfile.gettempdir(), "cosmos-remap-demo.json") if self.demo else None

    def settings_file(self):
        return getattr(self, "_file", None) or (None if not self.demo else os.path.join(tempfile.gettempdir(), "cosmos-pads-demo.json"))

    # -- layout
    def build_detail(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.title = label("", "cp-head")
        self.caps = label("", "cp-note", wrap=True)
        self.battery = label("", "cp-note")
        box.pack_start(self.title, False, False, 0)
        box.pack_start(self.caps, False, False, 0)
        box.pack_start(self.battery, False, False, 0)
        self.tabs = Gtk.Stack()
        sw = Gtk.StackSwitcher(stack=self.tabs)
        box.pack_start(sw, False, False, 0)
        box.pack_start(self.tabs, True, True, 0)

        # lighting
        light = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        light.get_style_context().add_class("cp-card")
        self.light_none = label("This controller has no light the kernel can change.", "cp-note")
        light.pack_start(self.light_none, False, False, 0)
        self.light_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        light.pack_start(self.light_box, False, False, 0)
        self.light_box.pack_start(label("Colour"), False, False, 0)
        row = Gtk.Box(spacing=8)
        for name, rgb in cp.PRESETS:
            row.pack_start(swatch_button(rgb, self.set_color, name), False, False, 0)
        self.custom = Gtk.ColorButton()
        self.custom.set_title("Custom colour")
        self.custom.connect("color-set", self.on_custom)
        row.pack_start(self.custom, False, False, 6)
        self.light_box.pack_start(row, False, False, 0)
        self.light_box.pack_start(label("Brightness"), False, False, 0)
        self.bright = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 100, 5)
        self.bright.set_draw_value(True)
        self.bright.connect("value-changed", lambda s: self.change(brightness=int(s.get_value())))
        self.light_box.pack_start(self.bright, False, False, 0)
        self.light_box.pack_start(label("Effect"), False, False, 0)
        row = Gtk.Box(spacing=8)
        self.effect_buttons = {}
        group = None
        for eff in cp.EFFECTS:
            b = Gtk.RadioButton.new_with_label_from_widget(group, eff.capitalize())
            b.set_mode(False)
            b.get_style_context().add_class("cp-button")
            group = group or b
            b.connect("toggled", lambda w, e=eff: w.get_active() and self.change(effect=e))
            self.effect_buttons[eff] = b
            row.pack_start(b, False, False, 0)
        self.light_box.pack_start(row, False, False, 0)
        self.light_box.pack_start(label("Animation speed (breathe, blink, rainbow)"), False, False, 0)
        self.speed = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 1, 10, 1)
        self.speed.set_digits(0)
        self.speed.connect("value-changed", lambda w: self.change(speed=int(w.get_value())))
        self.light_box.pack_start(self.speed, False, False, 0)
        self.battery_check = Gtk.CheckButton(label="Show the battery level on the light "
                                                   "(green to red; breathes while charging, blinks red when nearly empty)")
        self.battery_check.get_style_context().add_class("cp-text")
        self.battery_check.connect("toggled", lambda w: self.change(battery_color=w.get_active()))
        self.light_box.pack_start(self.battery_check, False, False, 6)
        self.tabs.add_titled(light, "light", "Lights")

        # players
        pl = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        pl.get_style_context().add_class("cp-card")
        self.player_none = label("This controller has no player-number lights.", "cp-note")
        pl.pack_start(self.player_none, False, False, 0)
        self.player_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        pl.pack_start(self.player_box, False, False, 0)
        self.player_box.pack_start(label("Player number (how many indicator lights are lit; the first choice leaves them alone)"), False, False, 0)
        self.player_row = Gtk.Box(spacing=8)
        self.player_box.pack_start(self.player_row, False, False, 0)
        self.home_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.home_box.pack_start(label("Home light brightness"), False, False, 0)
        self.home_scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 100, 10)
        self.home_scale.connect("value-changed", lambda s: self.change(home=int(s.get_value())))
        self.home_box.pack_start(self.home_scale, False, False, 0)
        pl.pack_start(self.home_box, False, False, 0)
        self.tabs.add_titled(pl, "players", "Player lights")

        # rumble
        rb = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        rb.get_style_context().add_class("cp-card")
        self.rumble_none = label("This controller does not report rumble.", "cp-note")
        rb.pack_start(self.rumble_none, False, False, 0)
        self.rumble_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        rb.pack_start(self.rumble_box, False, False, 0)
        self.rumble_box.pack_start(label("Strong motor (low rumble)"), False, False, 0)
        self.strong = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 100, 5)
        self.strong.set_value(80)
        self.rumble_box.pack_start(self.strong, False, False, 0)
        self.rumble_box.pack_start(label("Weak motor (high buzz)"), False, False, 0)
        self.weak = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 100, 5)
        self.weak.set_value(80)
        self.rumble_box.pack_start(self.weak, False, False, 0)
        test = Gtk.Button(label="Test rumble")
        test.get_style_context().add_class("cp-button")
        test.connect("clicked", self.on_test_rumble)
        self.rumble_box.pack_start(test, False, False, 6)
        self.tabs.add_titled(rb, "rumble", "Rumble")

        # motion
        mo = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        mo.get_style_context().add_class("cp-card")
        self.motion_none = label("This controller has no gyroscope or accelerometer the kernel exposes.", "cp-note")
        mo.pack_start(self.motion_none, False, False, 0)
        self.motion = MotionView()
        mo.pack_start(self.motion, True, True, 0)
        self.zero_btn = Gtk.Button(label="Zero the view (hold the controller still)")
        self.zero_btn.get_style_context().add_class("cp-button")
        self.zero_btn.connect("clicked", lambda *_: self.motion.set_zero())
        mo.pack_start(self.zero_btn, False, False, 0)
        self.tabs.add_titled(mo, "motion", "Motion")

        # adaptive triggers (DualSense)
        self.trig = {}
        tr = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        tr.get_style_context().add_class("cp-card")
        tr.pack_start(label("Squeeze a trigger after changing a setting to feel it. Games and Steam can override these "
                            "while they run.", "cp-note", wrap=True), False, False, 0)
        cols = Gtk.Box(spacing=24)
        tr.pack_start(cols, True, True, 0)
        for side in ("left", "right"):
            col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            cols.pack_start(col, True, True, 0)
            col.pack_start(label(f"{side.capitalize()} trigger", "cp-head"), False, False, 0)
            modes = Gtk.Box(spacing=6)
            col.pack_start(modes, False, False, 0)
            widgets = {"modes": {}, "scales": {}}
            group = None
            for mode, text in (("off", "Off"), ("feedback", "Resist"), ("weapon", "Weapon"), ("vibration", "Buzz")):
                b = Gtk.RadioButton.new_with_label_from_widget(group, text)
                b.set_mode(False)
                b.get_style_context().add_class("cp-button")
                b.get_style_context().add_class("cp-small")
                group = group or b
                b.connect("toggled", lambda w, side=side, mode=mode: w.get_active() and self.change_trigger(side, mode=mode))
                widgets["modes"][mode] = b
                modes.pack_start(b, False, False, 0)
            for key, text, lo, hi in (("start", "Starts at", 0, 9), ("end", "Click at (weapon)", 3, 8),
                                      ("strength", "Strength", 1, 8), ("amplitude", "Buzz strength", 1, 8),
                                      ("frequency", "Buzz speed (Hz)", 1, 120)):
                lab = label(text)
                sc = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, lo, hi, 1)
                sc.set_digits(0)
                sc.connect("value-changed", lambda w, side=side, key=key: self.change_trigger(side, **{key: int(w.get_value())}))
                col.pack_start(lab, False, False, 0)
                col.pack_start(sc, False, False, 0)
                widgets["scales"][key] = (lab, sc)
            self.trig[side] = widgets
        self.tabs.add_titled(tr, "triggers", "Triggers")

        # haptics (DualSense over USB)
        hp = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        hp.get_style_context().add_class("cp-card")
        hp.pack_start(label("The DualSense's haptic motors are driven as audio (channels 3-4 of its USB sound device). "
                            "This works with the USB cable connected; over Bluetooth Linux cannot use them.", "cp-note", wrap=True),
                      False, False, 0)
        hb = Gtk.Button(label="Play haptic test")
        hb.get_style_context().add_class("cp-button")
        hb.connect("clicked", self.on_haptics)
        hp.pack_start(hb, False, False, 6)
        self.haptic_status = label("", "cp-note", wrap=True)
        hp.pack_start(self.haptic_status, False, False, 0)
        self.tabs.add_titled(hp, "haptics", "Haptics")
        return box

    # -- button remapper (the four virtual pads)
    def build_remapper(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.pack_start(label("Button remapper", "cp-head"), False, False, 0)
        box.pack_start(label("For games run through GameBox: each controller is mirrored onto one of four virtual pads "
                             "(CosmOS Pad 1-4) in the order they connect, and this changes what that player's pad sends. "
                             "Press a button on a controller to find its row. Desktop navigation is not affected.",
                             "cp-note", wrap=True), False, False, 0)
        row = Gtk.Box(spacing=6)
        group = None
        self.slot_buttons = {}
        for n in range(1, cr.SLOTS + 1):
            b = Gtk.RadioButton.new_with_label_from_widget(group, f"Player {n}")
            b.set_mode(False)
            b.get_style_context().add_class("cp-button")
            group = group or b
            b.connect("toggled", lambda w, n=n: w.get_active() and self.pick_slot(n))
            self.slot_buttons[n] = b
            row.pack_start(b, False, False, 0)
        reset = Gtk.Button(label="Reset this player")
        reset.get_style_context().add_class("cp-button")
        reset.connect("clicked", self.reset_slot)
        row.pack_end(reset, False, False, 0)
        box.pack_start(row, False, False, 0)

        scroll = Gtk.ScrolledWindow()
        scroll.get_style_context().add_class("cp-scroll")
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        card.get_style_context().add_class("cp-card")
        scroll.add(card)
        box.pack_start(scroll, True, True, 0)
        card.pack_start(label("When this physical button is pressed ... the game sees", "cp-note"), False, False, 0)
        grid = Gtk.Grid(column_spacing=16, row_spacing=6)
        card.pack_start(grid, False, False, 0)
        self.remap_rows = {}
        targets = [name for name, _c, _l in cr.BUTTONS]
        for i, (name, _code, text) in enumerate(cr.BUTTONS):
            lab = label(text)
            lab.set_size_request(240, -1)
            combo = Gtk.ComboBoxText()
            for t in targets:
                combo.append(t, cr.BUTTON_LABEL[t])
            combo.append("", "Nothing (disabled)")
            combo.connect("changed", lambda w, name=name: self.on_map(name, w))
            grid.attach(lab, 0, i, 1, 1)
            grid.attach(combo, 1, i, 1, 1)
            self.remap_rows[name] = (lab, combo)
        card.pack_start(label("Sticks", "cp-head"), False, False, 6)
        self.swap_check = Gtk.CheckButton(label="Swap the left and right sticks")
        self.swap_check.connect("toggled", lambda w: self.on_stick_option("swap_sticks", w.get_active()))
        card.pack_start(self.swap_check, False, False, 0)
        self.invert_checks = {}
        for axis, _c, text in cr.AXES:
            if axis in ("ABS_Y", "ABS_RY"):
                chk = Gtk.CheckButton(label=f"Invert {text.lower()}")
                chk.connect("toggled", lambda w, axis=axis: self.on_stick_option("invert", (axis, w.get_active())))
                card.pack_start(chk, False, False, 0)
                self.invert_checks[axis] = chk
        for chk in [self.swap_check] + list(self.invert_checks.values()):
            chk.get_style_context().add_class("cp-text")
        return box

    def slot_cfg(self):
        return cr.normalize(self.remap_data.get(self.remap_slot))

    def store_slot(self, cfg):
        cfg = cr.normalize(cfg)
        if cr.is_default(cfg):
            self.remap_data.pop(self.remap_slot, None)
        else:
            self.remap_data[self.remap_slot] = cfg
        cr.save(self.remap_data, self.remap_file())

    def load_slot_widgets(self):
        cfg = self.slot_cfg()
        self.building = True
        for name, (_lab, combo) in self.remap_rows.items():
            combo.set_active_id("" if name in cfg["buttons"] and cfg["buttons"][name] is None else cfg["buttons"].get(name, name))
        self.swap_check.set_active(cfg["swap_sticks"])
        for axis, chk in self.invert_checks.items():
            chk.set_active(axis in cfg["invert"])
        self.building = False

    def pick_slot(self, n):
        self.remap_slot = n
        self.load_slot_widgets()

    def on_map(self, name, combo):
        if self.building:
            return
        cfg = self.slot_cfg()
        target = combo.get_active_id()
        if target == name:
            cfg["buttons"].pop(name, None)
        else:
            cfg["buttons"][name] = target or None
        self.store_slot(cfg)

    def on_stick_option(self, key, value):
        if self.building:
            return
        cfg = self.slot_cfg()
        if key == "swap_sticks":
            cfg["swap_sticks"] = bool(value)
        else:
            axis, on = value
            inv = set(cfg["invert"])
            inv.add(axis) if on else inv.discard(axis)
            cfg["invert"] = sorted(inv)
        self.store_slot(cfg)

    def reset_slot(self, *_):
        self.remap_data.pop(self.remap_slot, None)
        cr.save(self.remap_data, self.remap_file())
        self.load_slot_widgets()

    def show_remapper(self, *_):
        self.in_remap = True
        self.listbox.unselect_all()
        self.pad = None
        self.motion.close()
        self.right.set_visible_child_name("remap")
        self.load_slot_widgets()
        self.start_detect()

    def start_detect(self):
        """While the remapper is showing, a press on any connected controller lights its row."""
        self.stop_detect()
        if self.demo or cp.evdev is None:
            return
        for pad in self.pads:
            try:
                dev = cp.evdev.InputDevice(pad.event_path)
            except OSError:
                continue
            watch = GLib.io_add_watch(dev.fd, GLib.IO_IN, self.on_detect, dev)
            self.remap_watches.append((watch, dev))

    def stop_detect(self):
        for watch, dev in self.remap_watches:
            GLib.source_remove(watch)
            dev.close()
        self.remap_watches = []

    def on_detect(self, _fd, _cond, dev):
        try:
            for ev in dev.read():
                if ev.type == cp.ecodes.EV_KEY and ev.value == 1 and ev.code in cr.BUTTON_NAME:
                    self.flash_row(cr.BUTTON_NAME[ev.code])
        except (OSError, BlockingIOError):
            pass
        return True

    def flash_row(self, name):
        lab = self.remap_rows[name][0]
        lab.get_style_context().add_class("cp-hit")
        GLib.timeout_add(700, lambda: (lab.get_style_context().remove_class("cp-hit"), False)[1])

    # -- state
    def refresh(self):
        pads = cp.discover() if not self.demo else self.pads or demo_pads()
        sig = [(p.event_path, p.name) for p in pads]
        if sig == [(p.event_path, p.name) for p in self.pads] and self.pads:
            self.update_battery()
            return True
        self.pads = pads
        keep = self.pad.event_path if self.pad else None
        for child in self.listbox.get_children():
            self.listbox.remove(child)
        select = None
        for pad in pads:
            row = Gtk.ListBoxRow()
            row.pad = pad
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            box.pack_start(label(pad.name, "cp-text"), False, False, 0)
            box.pack_start(label(pad.family_label, "cp-note"), False, False, 0)
            row.add(box)
            self.listbox.add(row)
            if pad.event_path == keep or select is None:
                select = row
        self.listbox.show_all()
        if select and self.in_remap:
            pass                                    # the remapper is showing: don't pull the user out of it
        elif select:
            self.listbox.select_row(select)
        else:
            self.pad = None
            self.right.set_visible_child_name("empty")
        return True

    def on_row(self, _lb, row):
        if not row:
            return
        self.stop_detect()
        self.in_remap = False
        self.pad = row.pad
        self.right.set_visible_child_name("detail")
        self.load_pad()

    def load_pad(self):
        pad, s = self.pad, cp.settings_for(self.data, self.pad)
        self.building = True
        self.title.set_text(pad.name)
        self.caps.set_text("Can change: " + (", ".join(pad.capabilities()) or "nothing the kernel exposes"))
        self.update_battery()
        has_rgb = pad.rgb() is not None
        self.light_none.set_visible(not has_rgb)
        self.light_box.set_visible(has_rgb)
        self.bright.set_value(s["brightness"])
        self.effect_buttons[s["effect"]].set_active(True)
        self.speed.set_value(s["speed"])
        self.battery_check.set_active(s["battery_color"])
        c = Gdk.RGBA()
        c.red, c.green, c.blue, c.alpha = (s["color"][0] / 255, s["color"][1] / 255, s["color"][2] / 255, 1)
        self.custom.set_rgba(c)
        players = pad.players()
        self.player_none.set_visible(not players and not pad.home())
        self.player_box.set_visible(bool(players))
        for child in self.player_row.get_children():
            self.player_row.remove(child)
        group = None
        for n in [None] + list(range(len(players) + 1)):
            b = Gtk.RadioButton.new_with_label_from_widget(group, "As the controller has it" if n is None else "Off" if n == 0 else str(n))
            b.set_mode(False)
            b.get_style_context().add_class("cp-button")
            group = group or b
            if s["players"] == n:
                b.set_active(True)
            b.connect("toggled", lambda w, n=n: w.get_active() and self.change(players=n))
            self.player_row.pack_start(b, False, False, 0)
        self.player_row.show_all()
        self.home_box.set_visible(pad.home() is not None)
        self.home_scale.set_value(s["home"] if s["home"] is not None else 100)
        self.rumble_none.set_visible(not pad.can_rumble)
        self.rumble_box.set_visible(pad.can_rumble)
        self.motion.close()
        has_motion = bool(pad.motion_path) or self.demo
        self.motion_none.set_visible(not has_motion)
        self.motion.set_visible(has_motion)
        self.zero_btn.set_visible(has_motion)
        if self.demo:
            self.motion.set_demo(True)
        elif pad.motion_path:
            self.motion.open(pad.motion_path)
        is_ds = pad.family == "dualsense"
        for name in ("triggers", "haptics"):
            self.tabs.get_child_by_name(name).set_visible(is_ds)
        if is_ds:
            left, right = cds.triggers_from_settings(s)
            for side, t in (("left", left), ("right", right)):
                t = t or dict(cds.DEFAULT_TRIGGER)
                w = self.trig[side]
                w["modes"][t["mode"]].set_active(True)
                for key, (_lab, sc) in w["scales"].items():
                    sc.set_value(t[key])
                self.show_trigger_scales(side, t["mode"])
            self.haptic_status.set_text("")
        if not is_ds and self.tabs.get_visible_child_name() in ("triggers", "haptics"):
            self.tabs.set_visible_child_name("light")
        self.building = False

    def show_trigger_scales(self, side, mode):
        wanted = {"off": (), "feedback": ("start", "strength"), "weapon": ("start", "end", "strength"),
                  "vibration": ("start", "amplitude", "frequency")}[mode]
        for key, (lab, sc) in self.trig[side]["scales"].items():
            lab.set_visible(key in wanted)
            sc.set_visible(key in wanted)

    def change_trigger(self, side, **fields):
        if self.building or not self.pad:
            return
        s = cp.settings_for(self.data, self.pad)
        trig = s.get("triggers") if isinstance(s.get("triggers"), dict) else {}
        trig = {k: dict(v) for k, v in trig.items() if isinstance(v, dict)}
        trig.setdefault("left", dict(cds.DEFAULT_TRIGGER))
        trig.setdefault("right", dict(cds.DEFAULT_TRIGGER))
        trig[side].update(fields)
        if "mode" in fields:
            self.show_trigger_scales(side, fields["mode"])
        self.change(triggers=trig)

    def on_haptics(self, *_):
        if self.demo:
            self.haptic_status.set_text("(demo) nothing played.")
            return
        ok, msg = cds.play_haptics(self.pad)
        self.haptic_status.set_text(msg)

    def update_battery(self):
        b = self.pad.battery() if self.pad else None
        if b is None:
            self.battery.set_text("")
        else:
            pct, status = b
            self.battery.set_text(f"Battery: {pct}%  {status}" if pct is not None else f"Battery: {status}")

    # -- changes
    def set_color(self, rgb):
        self.change(color=list(rgb))
        c = Gdk.RGBA()
        c.red, c.green, c.blue, c.alpha = rgb[0] / 255, rgb[1] / 255, rgb[2] / 255, 1
        self.custom.set_rgba(c)

    def on_custom(self, btn):
        c = btn.get_rgba()
        self.change(color=[round(c.red * 255), round(c.green * 255), round(c.blue * 255)])

    def change(self, **fields):
        if self.building or not self.pad:
            return
        entry = dict(self.data.get(self.pad.key) or cp.settings_for(self.data, self.pad))
        for k, v in fields.items():
            entry[k] = v
        entry["name"] = self.pad.name
        self.data[self.pad.key] = entry
        if self.pending:
            GLib.source_remove(self.pending)
        self.pending = GLib.timeout_add(150, self.commit)

    def commit(self):
        self.pending = None
        cp.save_settings(self.data, self.settings_file())
        if self.pad and not self.demo:
            cp.apply_settings(self.pad, cp.settings_for(self.data, self.pad))
            start_daemon()
        return False

    def on_test_rumble(self, *_):
        if not self.pad:
            return
        if self.rumbler is None or not self.rumbler.usable:
            self.rumbler = cp.Rumbler(self.pad)
        self.rumbler.play(self.strong.get_value() / 100, self.weak.get_value() / 100, 700)

    def on_destroy(self, *_):
        self.stop_detect()
        self.motion.close()
        if self.rumbler:
            self.rumbler.close()
        if self.pending:
            self.commit()
        Gtk.main_quit()


def demo_pads():
    root = tempfile.mkdtemp(prefix="cosmos-pads-demo-")
    pads = []
    for i, (name, vid, pid, leds) in enumerate((
            ("Wireless Controller", 0x054C, 0x0CE6, ["input9:rgb:indicator", "input9:white:player-1", "input9:white:player-2",
                                                      "input9:white:player-3", "input9:white:player-4", "input9:white:player-5"]),
            ("Nintendo Switch Pro Controller", 0x057E, 0x2009, ["input10:green:player-1", "input10:green:player-2",
                                                                "input10:green:player-3", "input10:green:player-4", "input10:green:home"]))):
        hid = os.path.join(root, f"pad{i}")
        for led in leds:
            d = os.path.join(hid, "leds", led)
            os.makedirs(d)
            for f, v in (("brightness", "0"), ("max_brightness", "255"))[:2]:
                open(os.path.join(d, f), "w").write(v)
            if "rgb" in led:
                open(os.path.join(d, "multi_intensity"), "w").write("0 0 0")
        pad = cp.Pad(name, vid, pid, f"aa:bb:cc:dd:ee:0{i}", f"/dev/input/event{20 + i}", hid)
        pad.can_rumble = True
        pad.motion_path = "demo"
        pads.append(pad)
    return pads


def main(argv):
    if "--daemon" in argv:
        return run_daemon()
    cosmos_settings_link.redirect("controllers", argv)      # the window is a page of Settings; --standalone keeps this one
    provider = Gtk.CssProvider()
    provider.load_from_data(cosmos_display.scale_css(CSS).encode())
    Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    demo = "--demo" in argv
    win = Window(demo=demo)
    win.show_all()
    if not demo:
        start_daemon()
    Gtk.main()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
