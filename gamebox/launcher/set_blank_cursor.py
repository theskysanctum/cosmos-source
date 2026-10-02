#!/usr/bin/env python3
"""Sets a blank cursor on the X root window, once, right after the X
session comes up (see graphical.py's start_x()).

The launcher and every game/emulator here are built for gamepad input
and never want a visible mouse pointer at all (see input/stick_mode.py's
own "no cursor by construction" philosophy for menus/emulators) - this
makes that the actual default for the whole display, on the root
window every other window inherits its cursor from, rather than relying
on each individual window to remember to hide its own. Only the browser
explicitly wants a real cursor while it has focus (see
input/stick_mode.py's set_mouse_mode()), and does that itself - a
window that sets its own cursor always overrides whatever it would
otherwise inherit from its parent, so this needs no special-casing for
that. Without this, returning from the browser to the launcher showed
X11's own default cursor glyph (a plain bitmap "X" - not a stylistic
choice, just what nothing-configured looks like) instead of the launcher
correctly showing no cursor at all.

Fire-and-forget: the cursor setting lives on the X server/root window
itself, independent of this process's own lifetime, so it only has to
run once and can exit immediately after."""
import gi

gi.require_version("Gdk", "3.0")
from gi.repository import Gdk  # noqa: E402

display = Gdk.Display.get_default()
cursor = Gdk.Cursor.new_for_display(display, Gdk.CursorType.BLANK_CURSOR)
Gdk.get_default_root_window().set_cursor(cursor)
