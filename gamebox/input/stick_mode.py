#!/usr/bin/env python3
"""Shared flag file telling inputd.py whether the left stick should drive
mouse-cursor movement or menu-navigation arrow-key taps right now. A
plain flag file rather than a socket/signal: inputd.py is a long-lived
daemon and the sessions that need mouse mode (currently just the
browser) are short-lived, unrelated processes, so a file both can
check/touch without a live IPC channel between them is the simplest
thing that works.

Navigate mode (arrow-key taps, no cursor) is the default and is simply
the flag file's absence - nothing has to remember to ask for it, and an
emulator or the launcher's own menus never show a cursor by construction
rather than by remembering to suppress one. Only the browser explicitly
opts into mouse mode, for the duration of its own session."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import debuglog  # noqa: E402

log = debuglog.setup("stick_mode")

MOUSE_MODE_PATH = Path("/tmp/console-os-mouse-mode")


def set_mouse_mode():
	log.info("switching to mouse mode")
	MOUSE_MODE_PATH.touch()


def set_navigate_mode():
	log.info("switching to navigate mode")
	MOUSE_MODE_PATH.unlink(missing_ok=True)


def is_mouse_mode():
	# Not logged: inputd.py's input loop calls this ~33 times/second, so
	# logging every call would drown out everything else in the log file
	# for no benefit - set_mouse_mode/set_navigate_mode above already log
	# every actual state *change*, which is what's worth knowing.
	return MOUSE_MODE_PATH.exists()
