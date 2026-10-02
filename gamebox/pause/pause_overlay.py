#!/usr/bin/env python3
"""Resume/Quit overlay shown when the pause hotkey is pressed during
gameplay. Controller-navigable for free: inputd.py already translates
d-pad/face-button presses into synthetic Up/Down/Enter key events
system-wide (same trick that makes the TUI controller-navigable), so
this window just needs to listen for ordinary GTK key-press events.

Writes its result ("resume" or "quit") to the given file path and exits
- it's launched as its own short-lived process by pause_menu.py, which
reads that file back."""
import sys
from pathlib import Path

import gi

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import debuglog  # noqa: E402

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gtk  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "launcher"))
from theme import load_theme  # noqa: E402
import scaling  # noqa: E402

log = debuglog.setup("pause_overlay")

OPTIONS = ["Resume", "Quit"]


class PauseOverlay(Gtk.Window):
	def __init__(self, result_path):
		super().__init__(type=Gtk.WindowType.POPUP)
		self.result_path = result_path
		self.selected = 0

		geometry = Gdk.Display.get_default().get_monitor(0).get_geometry()
		width, height = scaling.px(260), scaling.px(140)
		self.set_default_size(width, height)
		self.move((geometry.width - width) // 2, (geometry.height - height) // 2)
		self.set_app_paintable(True)

		box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=scaling.px(10))
		box.set_border_width(scaling.px(20))
		title = Gtk.Label(label="Paused")
		title.get_style_context().add_class("title")
		box.pack_start(title, False, False, scaling.px(4))

		self.labels = []
		for option in OPTIONS:
			label = Gtk.Label(label=option)
			self.labels.append(label)
			box.pack_start(label, False, False, 0)
		self.add(box)

		self.connect("key-press-event", self.on_key)
		self.update_highlight()

	def update_highlight(self):
		for i, label in enumerate(self.labels):
			text = OPTIONS[i]
			label.set_markup(f"<b>&gt; {text}</b>" if i == self.selected else f"  {text}")

	def on_key(self, _widget, event):
		if event.keyval in (Gdk.KEY_Up, Gdk.KEY_k):
			self.selected = (self.selected - 1) % len(OPTIONS)
			self.update_highlight()
		elif event.keyval in (Gdk.KEY_Down, Gdk.KEY_j):
			self.selected = (self.selected + 1) % len(OPTIONS)
			self.update_highlight()
		elif event.keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
			self.finish(OPTIONS[self.selected].lower())

	def finish(self, choice):
		log.info(f"choice: {choice}")
		with open(self.result_path, "w") as f:
			f.write(choice)
		Gtk.main_quit()


def main():
	result_path = sys.argv[1]
	log.debug(f"starting, result_path={result_path}")
	load_theme()
	win = PauseOverlay(result_path)
	win.show_all()
	Gtk.main()


if __name__ == "__main__":
	main()
