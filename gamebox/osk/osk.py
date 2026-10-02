#!/usr/bin/env python3
"""On-screen keyboard: a never-focused GTK overlay, controller-navigable
via direct evdev reads rather than X focus/GTK key events - the whole
point is that real X focus must stay on whatever text field the OSK is
typing into (e.g. Brave's address bar), so the OSK itself cannot be the
focused window and still receive input the normal GTK way.

Styled after the PS3 system software keyboard: a dense grid of key
tiles with a highlighted selected key, a typed-text preview bar, and
separate SHIFT/123/ABC mode keys rather than a plain single-case QWERTY
strip - lets a controller actually type a real Wi-Fi password (mixed
case, punctuation), not just lowercase+digits.

The right stick drags the OSK window itself around the screen while
shown (clamped to stay on-monitor) - there's no mouse cursor control
from here; this is a controller-only virtual keyboard, not a pointer.

Confirming a key emits the real character via its own UInput virtual
keyboard device, system-wide - landing wherever real X focus already is.
The close button exits without submitting; the grid's own ENTER key taps
a real Enter (e.g. to fire a Gtk.Entry's "activate"/submit) without
closing the OSK - two separate, deliberately different actions.
"""
import select
import sys
import threading
from pathlib import Path

import evdev
import gi
from evdev import ecodes

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "input"))
from gamepad import find_gamepads  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "launcher"))
from theme import load_theme  # noqa: E402
import scaling  # noqa: E402
import debuglog  # noqa: E402

log = debuglog.setup("osk")

CLOSE_BUTTON = ecodes.BTN_START
CONFIRM_BUTTON = ecodes.BTN_SOUTH
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

# Real character -> (evdev keycode, needs_shift) on a standard US layout.
# Letters/digits are the unshifted base form; SHIFT mode adds an extra
# shift on top of a letter's own entry at tap time (see tap_char).
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


def make_output_device():
	keys = sorted({keycode for keycode, _ in CHAR_KEYS.values()}) + [
		ecodes.KEY_LEFTSHIFT,
		ecodes.KEY_SPACE,
		ecodes.KEY_BACKSPACE,
		ecodes.KEY_ENTER,
	]
	capabilities = {ecodes.EV_KEY: keys}
	return evdev.UInput(capabilities, name="console-os-osk-output")


def tap_raw(ui, keycode, shift=False):
	if shift:
		ui.write(ecodes.EV_KEY, ecodes.KEY_LEFTSHIFT, 1)
		ui.syn()
	ui.write(ecodes.EV_KEY, keycode, 1)
	ui.syn()
	ui.write(ecodes.EV_KEY, keycode, 0)
	ui.syn()
	if shift:
		ui.write(ecodes.EV_KEY, ecodes.KEY_LEFTSHIFT, 0)
		ui.syn()


def tap_char(ui, char, shift_active):
	"""char is always the grid's canonical (lowercase-letter/base-symbol)
	form; the SHIFT toggle adds an extra shift on top for letters only -
	digits/punctuation already encode their own shift need in CHAR_KEYS."""
	keycode, needs_shift = CHAR_KEYS[char]
	tap_raw(ui, keycode, needs_shift or (shift_active and char.isalpha()))


class Osk(Gtk.Window):
	def __init__(self):
		super().__init__(type=Gtk.WindowType.POPUP)
		self.selected_row = 0
		self.selected_col = 0
		self.shift_active = False
		self.mode = "letters"
		self.rows = LETTERS_ROWS
		self.typed_text = ""
		self.stick_x = 0
		self.stick_y = 0
		self.running = True
		self.ui = make_output_device()
		self.grid = None
		self.labels = []

		geometry = Gdk.Display.get_default().get_monitor(0).get_geometry()
		width = min(scaling.px(700), geometry.width - scaling.px(40))
		self.set_default_size(width, scaling.px(260))
		self.move((geometry.width - width) // 2, geometry.height - scaling.px(280))
		self.set_app_paintable(True)

		self.box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=scaling.px(8))
		self.box.set_border_width(scaling.px(10))

		self.preview = Gtk.Label(label=" ", xalign=0)
		self.preview.get_style_context().add_class("osk-preview")
		self.box.pack_start(self.preview, False, False, 0)

		self.add(self.box)
		self.build_grid()

		GLib.timeout_add(DRAG_TICK_MS, self.drag_tick)

	def build_grid(self):
		"""(Re)builds the key grid for the current mode - letters and
		symbols modes have different row counts/shapes, so the whole grid
		widget is replaced rather than reflowed in place."""
		if self.grid:
			self.box.remove(self.grid)

		self.grid = Gtk.Grid(row_spacing=scaling.px(6), column_spacing=scaling.px(6))
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
		log.debug(f"switching mode {self.mode} -> {mode}")
		self.mode = mode
		self.rows = MODES[mode]
		self.selected_row = 0
		self.selected_col = 0
		GLib.idle_add(self.build_grid)

	def confirm(self):
		# Deliberately not logging the actual character for regular key
		# taps below (only the action name for SHIFT/mode/SPACE/DEL/ENTER)
		# - this thing types Wi-Fi passwords and API secrets, and a debug
		# log file is not where those belong, even at DEBUG level.
		key = self.rows[self.selected_row][self.selected_col]
		if key == "SHIFT":
			self.shift_active = not self.shift_active
			log.debug(f"shift toggled: {self.shift_active}")
			GLib.idle_add(self.update_highlight)
		elif key in ("123", "ABC"):
			self.set_mode("symbols" if key == "123" else "letters")
		elif key == "SPACE":
			log.debug("space")
			tap_raw(self.ui, ecodes.KEY_SPACE)
			self.typed_text += " "
			GLib.idle_add(self.update_preview)
		elif key == "DEL":
			log.debug("backspace")
			tap_raw(self.ui, ecodes.KEY_BACKSPACE)
			self.typed_text = self.typed_text[:-1]
			GLib.idle_add(self.update_preview)
		elif key == "ENTER":
			log.debug("enter (submit)")
			tap_raw(self.ui, ecodes.KEY_ENTER)
			self.typed_text = ""
			GLib.idle_add(self.update_preview)
		else:
			log.debug(f"character key tapped (length now {len(self.typed_text) + 1})")
			tap_char(self.ui, key, self.shift_active)
			self.typed_text += key.upper() if self.shift_active else key
			GLib.idle_add(self.update_preview)

	def close_osk(self):
		log.info("closing OSK")
		self.running = False
		GLib.idle_add(Gtk.main_quit)

	def drag_tick(self):
		"""Right stick drags the whole OSK window around the screen - a
		deliberate trade rather than an addition: this stick used to move
		a system mouse cursor while the OSK was open, but there's nowhere
		else to put "reposition the keyboard" without a second physical
		control, so cursor control is simply unavailable while typing."""
		if not self.running:
			return False
		dx = int(self.stick_x / 32767 * DRAG_SENSITIVITY) if abs(self.stick_x) > DRAG_DEADZONE else 0
		dy = int(self.stick_y / 32767 * DRAG_SENSITIVITY) if abs(self.stick_y) > DRAG_DEADZONE else 0
		if dx or dy:
			x, y = self.get_position()
			width, height = self.get_size()
			geometry = Gdk.Display.get_default().get_monitor(0).get_geometry()
			new_x = max(0, min(geometry.width - width, x + dx))
			new_y = max(0, min(geometry.height - height, y + dy))
			self.move(new_x, new_y)
		return True


def handle_event(osk, event):
	if event.type == ecodes.EV_KEY and event.value == 1:
		if event.code == CLOSE_BUTTON:
			osk.close_osk()
		elif event.code == CONFIRM_BUTTON:
			osk.confirm()
	elif event.type == ecodes.EV_ABS:
		if event.code == ecodes.ABS_HAT0Y:
			if event.value == -1:
				osk.move_selection(-1, 0)
			elif event.value == 1:
				osk.move_selection(1, 0)
		elif event.code == ecodes.ABS_HAT0X:
			if event.value == -1:
				osk.move_selection(0, -1)
			elif event.value == 1:
				osk.move_selection(0, 1)
		elif event.code == ecodes.ABS_RX:
			osk.stick_x = event.value
		elif event.code == ecodes.ABS_RY:
			osk.stick_y = event.value


def input_loop(osk, devices):
	device_by_fd = {d.fd: d for d in devices}
	while osk.running:
		ready, _, _ = select.select(device_by_fd, [], [], 0.2)
		for fd in ready:
			for event in device_by_fd[fd].read():
				handle_event(osk, event)


def main():
	log.info("starting")
	devices = find_gamepads()
	log.debug(f"found {len(devices)} gamepad(s) for OSK input")
	load_theme()
	osk = Osk()
	osk.show_all()

	thread = threading.Thread(target=input_loop, args=(osk, devices), daemon=True)
	thread.start()

	Gtk.main()
	log.info("Gtk.main() returned, closing")
	osk.ui.close()


if __name__ == "__main__":
	main()
