#!/usr/bin/env python3
"""Sets a blank cursor on one specific window, by X11 window ID (see
graphical.py's run_graphical(), blank_cursor_on_map param) - unlike
set_blank_cursor.py's root-window version, this targets a single
already-mapped client window directly via GdkX11's foreign-window
wrapper, since the root window's own blank cursor (see that script's
docstring) is only ever inherited by a window that hasn't set its own -
Brave always sets its own real cursor, so blanking root does nothing
for it.

This only needs to cover the brief gap between the browser's window
first appearing and dot_cursor_extension/ taking over (its content
script's own CSS `cursor: none` applies once the page starts painting,
not before) - a real report of a quick flash of the plain X11 arrow
during that gap is what this closes. Not a permanent override: once the
extension's CSS is active it keeps re-asserting "none" on every hover
the same way it always did, so there's nothing left for this one-time
call to fight past that point - it only ever needs to win the very
first moment.

Usage: set_window_blank_cursor.py <window_id>"""
import sys

import gi

gi.require_version("Gdk", "3.0")
gi.require_version("GdkX11", "3.0")
from gi.repository import Gdk, GdkX11  # noqa: E402

window_id = int(sys.argv[1])
display = Gdk.Display.get_default()
window = GdkX11.X11Window.foreign_new_for_display(display, window_id)
cursor = Gdk.Cursor.new_for_display(display, Gdk.CursorType.BLANK_CURSOR)
window.set_cursor(cursor)
