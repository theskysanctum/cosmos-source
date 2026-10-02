#!/usr/bin/env python3
"""The one way into Settings from the old per-app commands. `cosmos-controllers`, `cosmos-remote`, `cosmos-updates`, `cosmos-android` and the rest are pages of
CosmOS Settings now; each command's main() calls redirect(PAGE, argv) first, which opens (or brings forward) Settings on that page and never returns. A
command run with --standalone keeps its own window, for developing the page itself; daemons (`--daemon`, `--tray`) never redirect."""
import os
import shutil
import sys
import subprocess


def settings_command(page):
    program = shutil.which("cosmos-settings") or os.path.join(os.path.dirname(os.path.realpath(__file__)), "cosmos-settings.py")
    return [program, "--page", page]


def open_page(page):
    """Starts (or raises) Settings on `page` without waiting for it. True when it could be started."""
    try:
        subprocess.Popen(settings_command(page), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        return True
    except OSError:
        return False


def redirect(page, argv=None):
    """Replaces this process with Settings on `page`, unless --standalone was given (then it returns, and the caller shows its own window)."""
    argv = sys.argv[1:] if argv is None else argv
    if "--standalone" in argv:
        return
    command = settings_command(page)
    try:
        os.execv(command[0], command)
    except OSError:
        return              # no Settings here: the command falls back to its own window
