"""Small shared session state kept as files under $XDG_RUNTIME_DIR.

Two flags, both keyed on a live PID rather than the file merely existing, so a
process that dies without cleaning up can never leave the desktop wedged:

  cosmos-bar-hidden-by       written by launch-gamebox.sh while GameBox runs; the
                             bar and the launcher hide themselves while it is set...
  cosmos-home-shown          ...unless cosmos-wm has written this (no window is active:
                             the person raised the gridscreen with the Windows key / Guide).
  cosmos-gamepad-claimed-by  written by the launcher while it has keyboard focus
                             and reads controllers natively; cosmos-input stands
                             down while it is set so nothing is handled twice.
"""
import os

from gi.repository import GLib

RUNTIME_DIR = os.environ.get("XDG_RUNTIME_DIR") or "/tmp"
HIDE_REQUEST_PATH = os.path.join(RUNTIME_DIR, "cosmos-bar-hidden-by")
PAD_CLAIM_PATH = os.path.join(RUNTIME_DIR, "cosmos-gamepad-claimed-by")
POLL_MS = 500


def _live_pid_in(path):
    try:
        with open(path, encoding="utf-8") as fh:
            pid = int(fh.read().split()[0])
    except (OSError, ValueError, IndexError):
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        pass
    return True


HOME_SHOWN_PATH = os.path.join(RUNTIME_DIR, "cosmos-home-shown")


def hide_requested():
    """True while GameBox is running and on screen: launch-gamebox names a live process,
    unless cosmos-wm says the gridscreen is what is showing (the person pressed the Windows key / Guide
    to raise it while GameBox keeps running behind it)."""
    return _live_pid_in(HIDE_REQUEST_PATH) and not os.path.exists(HOME_SHOWN_PATH)


def watch_hide_request(on_change):
    """Calls on_change(hidden) whenever the request flips (and once at start
    if it is already set)."""
    state = {"hidden": False}

    def poll():
        want = hide_requested()
        if want != state["hidden"]:
            state["hidden"] = want
            on_change(want)
        return True

    poll()
    GLib.timeout_add(POLL_MS, poll)


def claim_pad():
    try:
        with open(PAD_CLAIM_PATH, "w", encoding="utf-8") as fh:
            fh.write(str(os.getpid()))
    except OSError:
        pass


def release_pad():
    """Removes the claim only if it is still ours."""
    try:
        with open(PAD_CLAIM_PATH, encoding="utf-8") as fh:
            mine = fh.read().split()[:1] == [str(os.getpid())]
        if mine:
            os.remove(PAD_CLAIM_PATH)
    except OSError:
        pass


def pad_claimed():
    return _live_pid_in(PAD_CLAIM_PATH)
