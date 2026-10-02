#!/usr/bin/env python3
"""Runs a game with an optional full-screen CRT/LCD/LED screen-texture
filter composited on top of it for the game's whole duration - one extra
X client (draw_filter.py) alongside whatever the game itself needs,
inside the one X session the launcher already owns for its whole
lifetime (see graphical.run_graphical()'s docstring).

This used to also draw a photographed console/handheld bezel frame
behind the game, with the game's window positioned into a cutout rect
matching the frame's screen - that whole mechanism (and the cutout math
it needed) is gone; the filter now always covers the real display, and
the actual game launch is left entirely to graphical.run_graphical(),
which already does its own window-finding/positioning, on-screen-keyboard
watching, and pause-menu supervision - no need to duplicate any of that
here."""
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "launcher"))
import graphical  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import debuglog  # noqa: E402

log = debuglog.setup("filters")

DRAW_FILTER = str(Path(__file__).parent / "draw_filter.py")

RAISE_INTERVAL_S = 1.0


def _keep_raised(window_id, env, stop_event):
	"""draw_filter.py's own window is an override-redirect POPUP (see its
	own docstring - needed for real per-pixel alpha) - consolewm never
	sees it at all (override-redirect windows bypass MapRequest entirely,
	per ICCCM), so it has no way to keep it above whatever the game's own
	window does. Confirmed live: the game's window reliably ends up on
	top the moment it maps, since consolewm's own XRaiseWindow() on every
	new normal MapRequest reorders the whole shared X stacking order,
	override-redirect windows included, with nothing keeping the filter
	back on top afterward. Re-raising it here on a short interval for the
	game's whole run (not just once right after launch) also covers any
	later window the game itself opens (an in-game menu/dialog)."""
	while not stop_event.wait(RAISE_INTERVAL_S):
		subprocess.run(["xdotool", "windowraise", window_id], env=env, capture_output=True)


def run_with_filter(run_fn, filter_image, *, compositor_settle=1.0):
	"""Runs run_fn() - a zero-arg call already bound to
	graphical.run_graphical(cmd) at the call site - with a full-screen
	CRT/LCD/LED filter overlay composited on top for its whole duration.

	filter_image needs a real compositing manager to blend its
	transparency at all - a plain X11 window's alpha is otherwise ignored
	(see draw_filter.py's own docstring) - so picom is started before
	run_fn() and stopped again once it returns.

	The filter covers the real display geometry (graphical.
	get_display_geometry()) - the same "fill the whole screen" call
	brave_launcher.py/kiosk_launcher.py already use - rather than any
	per-platform cutout rect, since there's no bezel frame any more to
	match a cutout shape against.

	graphical.reset_cursor() in the finally block re-asserts the blank
	root cursor once torn down, for the same reason run_graphical() does
	the same thing for the browser: however picom/the filter's own
	session ends, X11's un-themed default cursor (a plain bitmap "X") can
	end up showing on the launcher afterward otherwise."""
	log.info(f"run_with_filter: filter_image={filter_image}")
	env = dict(os.environ)
	picom_proc = None
	filter_proc = None
	raise_thread = None
	raise_stop = threading.Event()
	try:
		log.debug("starting picom compositor for filter transparency")
		picom_proc = subprocess.Popen(["picom", "--backend", "xrender", "--no-vsync"], env=env)
		time.sleep(compositor_settle)

		width, height = graphical.get_display_geometry(env)
		filter_args = ["python3", DRAW_FILTER, filter_image, "0", "0", str(width), str(height)]
		log.debug(f"starting filter overlay: {filter_args}")
		filter_proc = subprocess.Popen(filter_args, env=env)

		filter_window_id = graphical.find_window_for_pid(filter_proc.pid, env)
		if filter_window_id:
			raise_thread = threading.Thread(target=_keep_raised, args=(filter_window_id, env, raise_stop), daemon=True)
			raise_thread.start()
		else:
			log.warning("never found the filter overlay's own window - it may end up hidden behind the game")

		run_fn()
	finally:
		log.debug("tearing down filter session")
		raise_stop.set()
		if raise_thread:
			raise_thread.join(timeout=5)
		if filter_proc:
			filter_proc.terminate()
			filter_proc.wait(timeout=5)
		if picom_proc:
			picom_proc.terminate()
			picom_proc.wait(timeout=5)
		graphical.reset_cursor(env)
