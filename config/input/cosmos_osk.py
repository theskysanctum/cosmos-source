#!/usr/bin/env python3
"""cosmos-osk - controller-driven on-screen keyboard for the CosmOS desktop.

Vendored from GameBox's launcher/osk/osk.py so a controller types the same
way in both: a never-focused GTK overlay (real keyboard focus stays on the field
being typed into), navigated by reading the gamepad directly rather than
through X, and typing through its own UInput keyboard system-wide.

  d-pad        move the highlighted key      A       press it
  X            backspace                     Y       space
  Start        close                         Select  close (cosmos-input toggles it)
  right stick  drag the keyboard around the screen

cosmos-input opens and closes this (Select) and stands down while it's open.
Restyled with CosmOS's own dark palette instead of GameBox's theme loader,
and the output device is injectable so it can be tested without emitting
real key events.
"""
import os
import select
import sys
import threading

import evdev
import gi
from evdev import ecodes

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GtkLayerShell", "0.1")
from gi.repository import Gdk, GLib, Gtk, GtkLayerShell  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
sys.path.append(os.path.join(os.path.dirname(os.path.realpath(__file__)), "..", "scripts"))     # a source checkout keeps cosmos_display.py next door
from cosmos_input import find_gamepads, normalize_abs  # noqa: E402
import cosmos_display  # noqa: E402

BG = "#0a0e16"
FG = "#e8ecf4"
ACCENT = "#ff0090"
BORDER = "#2a3a56"

CSS = f"""
window.cosmos-osk {{ background-color: {BG}; border: 2px solid {BORDER}; }}
.osk-preview {{ color: {FG}; background-color: #000000; border: 1px solid {BORDER};
    padding: 6px 10px; font-size: 16px; }}
.osk-key {{ color: {FG}; background-color: {BG}; border: 1px solid {BORDER};
    border-radius: 4px; padding: 6px 4px; font-size: 16px; }}
.osk-key-selected {{ color: {BG}; background-color: {ACCENT}; border-color: {ACCENT}; }}
"""

CLOSE_BUTTON = ecodes.BTN_START
CONFIRM_BUTTON = ecodes.BTN_SOUTH
BACKSPACE_BUTTON = ecodes.BTN_WEST
SPACE_BUTTON = ecodes.BTN_NORTH
DRAG_SENSITIVITY = 12
DRAG_DEADZONE = 4000
DRAG_TICK_MS = 30

LETTERS_ROWS = [
    list("qwertyuiop"),
    list("asdfghjkl"),
    ["SHIFT"] + list("zxcvbnm"),
    ["123", "SPACE", "DEL", "ENTER"],
]
SYMBOLS_ROWS = [
    list("1234567890"),
    list("!@#$%^&*()"),
    list("-_=+[]{};'"),
    list(':"`~\\|,./?'),
    ["ABC", "SPACE", "DEL", "ENTER"],
]
MODES = {"letters": LETTERS_ROWS, "symbols": SYMBOLS_ROWS}
ACTION_KEYS = {"SHIFT", "123", "ABC", "SPACE", "DEL", "ENTER"}

_UNSHIFTED_PUNCT = {
    "-": "MINUS", "=": "EQUAL", "[": "LEFTBRACE", "]": "RIGHTBRACE",
    ";": "SEMICOLON", "'": "APOSTROPHE", "`": "GRAVE", "\\": "BACKSLASH",
    ",": "COMMA", ".": "DOT", "/": "SLASH",
}
_SHIFTED_PUNCT = {
    "_": "MINUS", "+": "EQUAL", "{": "LEFTBRACE", "}": "RIGHTBRACE",
    ":": "SEMICOLON", '"': "APOSTROPHE", "~": "GRAVE", "|": "BACKSLASH",
    "<": "COMMA", ">": "DOT", "?": "SLASH",
}
_SHIFTED_DIGITS = {"!": "1", "@": "2", "#": "3", "$": "4", "%": "5", "^": "6", "&": "7", "*": "8", "(": "9", ")": "0"}

# Real character -> (evdev keycode, needs_shift) on a US layout.
CHAR_KEYS = {}
for _c in "abcdefghijklmnopqrstuvwxyz":
    CHAR_KEYS[_c] = (getattr(ecodes, f"KEY_{_c.upper()}"), False)
for _d in "0123456789":
    CHAR_KEYS[_d] = (getattr(ecodes, f"KEY_{_d}"), False)
for _symbol, _keyname in _UNSHIFTED_PUNCT.items():
    CHAR_KEYS[_symbol] = (getattr(ecodes, f"KEY_{_keyname}"), False)
for _symbol, _keyname in _SHIFTED_PUNCT.items():
    CHAR_KEYS[_symbol] = (getattr(ecodes, f"KEY_{_keyname}"), True)
for _symbol, _digit in _SHIFTED_DIGITS.items():
    CHAR_KEYS[_symbol] = (getattr(ecodes, f"KEY_{_digit}"), True)


class OutputDevice:
    """The keyboard the OSK types through. Tests substitute a recorder with
    the same key(code, value) method."""

    def __init__(self):
        keys = sorted({code for code, _ in CHAR_KEYS.values()}) + [
            ecodes.KEY_LEFTSHIFT, ecodes.KEY_SPACE, ecodes.KEY_BACKSPACE, ecodes.KEY_ENTER]
        self.ui = evdev.UInput({ecodes.EV_KEY: keys}, name="cosmos-osk-output")

    def key(self, code, value):
        self.ui.write(ecodes.EV_KEY, code, value)
        self.ui.syn()

    def close(self):
        self.ui.close()


def tap_raw(out, keycode, shift=False):
    if shift:
        out.key(ecodes.KEY_LEFTSHIFT, 1)
    out.key(keycode, 1)
    out.key(keycode, 0)
    if shift:
        out.key(ecodes.KEY_LEFTSHIFT, 0)


def tap_char(out, char, shift_active):
    """char is the grid's canonical form; SHIFT adds an extra shift for
    letters only - digits/punctuation already encode their own in CHAR_KEYS."""
    keycode, needs_shift = CHAR_KEYS[char]
    tap_raw(out, keycode, needs_shift or (shift_active and char.isalpha()))


class Osk(Gtk.Window):
    def __init__(self, out=None):
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        self.get_style_context().add_class("cosmos-osk")
        self.set_decorated(False)
        # A layer-shell OVERLAY surface that never takes keyboard focus (the
        # field being typed into keeps it). Positioned by its top/left margins,
        # which is also how the right stick drags it around (see place()).
        self.layered = GtkLayerShell.is_supported()
        self.pos = (0, 0)
        if self.layered:
            GtkLayerShell.init_for_window(self)
            GtkLayerShell.set_namespace(self, "cosmos-osk")
            GtkLayerShell.set_layer(self, GtkLayerShell.Layer.OVERLAY)
            GtkLayerShell.set_keyboard_mode(self, GtkLayerShell.KeyboardMode.NONE)
            GtkLayerShell.set_exclusive_zone(self, -1)
            GtkLayerShell.set_anchor(self, GtkLayerShell.Edge.TOP, True)
            GtkLayerShell.set_anchor(self, GtkLayerShell.Edge.LEFT, True)
        self.selected_row = 0
        self.selected_col = 0
        self.shift_active = False
        self.mode = "letters"
        self.rows = LETTERS_ROWS
        self.typed_text = ""
        self.stick_x = 0
        self.stick_y = 0
        self.running = True
        self.out = out if out is not None else OutputDevice()
        self.grid = None
        self.labels = []

        geometry = Gdk.Display.get_default().get_monitor(0).get_geometry()
        scale = cosmos_display.ui_factor(geometry.width, geometry.height)     # the same rule as every other CosmOS app (cosmos_display.py)
        px = lambda n: int(n * scale)  # noqa: E731
        width = min(px(700), geometry.width - px(40))
        self.set_default_size(width, px(260))
        self.place((geometry.width - width) // 2, geometry.height - px(280))
        self.set_app_paintable(True)

        self.box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=px(8))
        self.box.set_border_width(px(10))
        self.preview = Gtk.Label(label=" ", xalign=0)
        self.preview.get_style_context().add_class("osk-preview")
        self.box.pack_start(self.preview, False, False, 0)
        self.add(self.box)
        self.build_grid()
        GLib.timeout_add(DRAG_TICK_MS, self.drag_tick)

    def place(self, x, y):
        self.pos = (x, y)
        if self.layered:
            GtkLayerShell.set_margin(self, GtkLayerShell.Edge.LEFT, x)
            GtkLayerShell.set_margin(self, GtkLayerShell.Edge.TOP, y)
        else:
            self.move(x, y)

    def build_grid(self):
        if self.grid:
            self.box.remove(self.grid)
        self.grid = Gtk.Grid(row_spacing=6, column_spacing=6)
        self.grid.set_halign(Gtk.Align.CENTER)
        self.labels = []
        for r, row in enumerate(self.rows):
            row_labels = []
            for c, key in enumerate(row):
                label = Gtk.Label(label=self._display(key))
                label.get_style_context().add_class("osk-key")
                label.set_width_chars(8 if key == "SPACE" else max(3, len(key) + 1))
                self.grid.attach(label, c, r, 1, 1)
                row_labels.append(label)
            self.labels.append(row_labels)
        self.box.pack_start(self.grid, True, True, 0)
        self.box.show_all()
        self.update_highlight()

    def _display(self, key):
        if key in ACTION_KEYS:
            return "SHIFT*" if key == "SHIFT" and self.shift_active else key
        return key.upper() if self.shift_active and key.isalpha() else key

    def update_highlight(self):
        for r, row_labels in enumerate(self.labels):
            for c, label in enumerate(row_labels):
                style = label.get_style_context()
                style.remove_class("osk-key-selected")
                if r == self.selected_row and c == self.selected_col:
                    style.add_class("osk-key-selected")
                label.set_text(self._display(self.rows[r][c]))
        return False

    def update_preview(self):
        self.preview.set_text(self.typed_text if self.typed_text else " ")
        return False

    def move_selection(self, drow, dcol):
        self.selected_row = (self.selected_row + drow) % len(self.rows)
        self.selected_col = min(self.selected_col, len(self.rows[self.selected_row]) - 1)
        if dcol:
            self.selected_col = (self.selected_col + dcol) % len(self.rows[self.selected_row])
        GLib.idle_add(self.update_highlight)

    def set_mode(self, mode):
        self.mode = mode
        self.rows = MODES[mode]
        self.selected_row = 0
        self.selected_col = 0
        GLib.idle_add(self.build_grid)

    def press(self, key):
        """Types one grid key. Regular characters are deliberately never logged
        - this types Wi-Fi passwords and secrets."""
        if key == "SHIFT":
            self.shift_active = not self.shift_active
            GLib.idle_add(self.update_highlight)
        elif key in ("123", "ABC"):
            self.set_mode("symbols" if key == "123" else "letters")
        elif key == "SPACE":
            tap_raw(self.out, ecodes.KEY_SPACE)
            self.typed_text += " "
            GLib.idle_add(self.update_preview)
        elif key == "DEL":
            tap_raw(self.out, ecodes.KEY_BACKSPACE)
            self.typed_text = self.typed_text[:-1]
            GLib.idle_add(self.update_preview)
        elif key == "ENTER":
            tap_raw(self.out, ecodes.KEY_ENTER)
            self.typed_text = ""
            GLib.idle_add(self.update_preview)
        else:
            tap_char(self.out, key, self.shift_active)
            self.typed_text += key.upper() if self.shift_active else key
            GLib.idle_add(self.update_preview)

    def confirm(self):
        self.press(self.rows[self.selected_row][self.selected_col])

    def close_osk(self):
        self.running = False
        GLib.idle_add(Gtk.main_quit)

    def drag_tick(self):
        """Right stick drags the whole keyboard around the screen."""
        if not self.running:
            return False
        dx = int(self.stick_x / 32767 * DRAG_SENSITIVITY) if abs(self.stick_x) > DRAG_DEADZONE else 0
        dy = int(self.stick_y / 32767 * DRAG_SENSITIVITY) if abs(self.stick_y) > DRAG_DEADZONE else 0
        if dx or dy:
            x, y = self.pos
            width, height = self.get_size()
            geometry = Gdk.Display.get_default().get_monitor(0).get_geometry()
            self.place(max(0, min(geometry.width - width, x + dx)),
                       max(0, min(geometry.height - height, y + dy)))
        return True

    def handle(self, etype, code, value):
        if etype == ecodes.EV_KEY and value == 1:
            if code == CLOSE_BUTTON:
                self.close_osk()
            elif code == CONFIRM_BUTTON:
                self.confirm()
            elif code == BACKSPACE_BUTTON:
                self.press("DEL")
            elif code == SPACE_BUTTON:
                self.press("SPACE")
            elif code == ecodes.BTN_DPAD_UP:
                self.move_selection(-1, 0)
            elif code == ecodes.BTN_DPAD_DOWN:
                self.move_selection(1, 0)
            elif code == ecodes.BTN_DPAD_LEFT:
                self.move_selection(0, -1)
            elif code == ecodes.BTN_DPAD_RIGHT:
                self.move_selection(0, 1)
        elif etype == ecodes.EV_ABS:
            if code == ecodes.ABS_HAT0Y and value in (-1, 1):
                self.move_selection(value, 0)
            elif code == ecodes.ABS_HAT0X and value in (-1, 1):
                self.move_selection(0, value)
            elif code == ecodes.ABS_RX:
                self.stick_x = value
            elif code == ecodes.ABS_RY:
                self.stick_y = value


def input_loop(osk, devices):
    by_fd = {d.fd: d for d in devices}
    while osk.running:
        try:
            ready, _, _ = select.select(list(by_fd), [], [], 0.2)
        except (OSError, ValueError):
            break   # a controller vanished; the keyboard just stops listening
        for fd in ready:
            dev = by_fd[fd]
            try:
                events = list(dev.read())
            except OSError:
                by_fd.pop(fd, None)
                continue
            for ev in events:
                value = normalize_abs(dev, ev.code, ev.value) if ev.type == ecodes.EV_ABS and ev.code in (
                    ecodes.ABS_RX, ecodes.ABS_RY) else ev.value
                osk.handle(ev.type, ev.code, value)


def main():
    provider = Gtk.CssProvider()
    provider.load_from_data(cosmos_display.scale_css(CSS).encode())
    Gtk.StyleContext.add_provider_for_screen(
        Gdk.Screen.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_USER)
    devices = find_gamepads()
    osk = Osk()
    osk.show_all()
    threading.Thread(target=input_loop, args=(osk, devices), daemon=True).start()
    Gtk.main()
    osk.out.close()


if __name__ == "__main__":
    main()
