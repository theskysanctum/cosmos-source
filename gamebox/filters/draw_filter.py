#!/usr/bin/env python3
"""Real per-pixel-transparent overlay window showing a screen filter (CRT
scanlines / LCD grid / LED dot-matrix) on top of the game, sized to
cover the whole real display.

This can't be done with a plain SDL2/pygame-style window: blitting an
alpha image onto an ordinary window flattens it to fully opaque before
it ever reaches X, so nothing behind it would show through (confirmed
empirically, not assumed - see the plan notes for milestone 5). GTK3,
given a compositor's RGBA visual and told to paint with Cairo's OVER
operator instead of clearing to opaque first, keeps the real per-pixel
alpha - also verified against a live compositor (picom) before wiring
this in.

Requires a compositing manager (picom) running on the same X session -
without one, X does not blend ARGB windows at all, they'd just be
opaque. filters.py starts picom itself for exactly this reason whenever
a filter is requested.
"""
import sys
from pathlib import Path

import gi

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import debuglog  # noqa: E402

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf, Gtk  # noqa: E402

log = debuglog.setup("draw_filter")

CAIRO_OPERATOR_SOURCE = 1
CAIRO_OPERATOR_OVER = 2


def on_draw(widget, cr):
	cr.set_operator(CAIRO_OPERATOR_SOURCE)
	cr.set_source_rgba(0, 0, 0, 0)
	cr.paint()
	cr.set_operator(CAIRO_OPERATOR_OVER)
	Gdk.cairo_set_source_pixbuf(cr, widget.filter_pixbuf, 0, 0)
	cr.paint()
	return False


def on_screen_changed(widget, _old_screen):
	visual = widget.get_screen().get_rgba_visual()
	if visual:
		widget.set_visual(visual)
		log.debug("RGBA visual set")
	else:
		log.warning("no RGBA visual available - filter will render opaque without a running compositor")


def main():
	image_path, x, y, width, height = sys.argv[1], *(int(v) for v in sys.argv[2:6])
	log.info(f"starting, image_path={image_path} rect=({x},{y},{width},{height})")

	win = Gtk.Window(type=Gtk.WindowType.POPUP)
	win.set_default_size(width, height)
	win.move(x, y)
	win.set_app_paintable(True)
	win.connect("screen-changed", on_screen_changed)
	on_screen_changed(win, None)
	win.connect("draw", on_draw)

	win.filter_pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(image_path, width, height, False)
	log.debug(f"loaded filter image {image_path}")

	win.show_all()
	log.debug("window shown, entering Gtk.main()")
	Gtk.main()


if __name__ == "__main__":
	main()
