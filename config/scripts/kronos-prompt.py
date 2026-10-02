#!/usr/bin/env python3
"""kronos-prompt - a small window that asks for the admin passphrase and prints it (used by the Admin activity app and `kronos passphrase`).

  kronos-prompt --mode unlock --text "Type your current admin passphrase"     ask for the passphrase
  kronos-prompt --mode create --text "..."                                    choose one (asked twice)
  [--error "That passphrase is wrong."]                                       a line shown in red above the box

The passphrase is printed on standard output and the exit status is 0; Cancel (or Escape) exits 1 and prints nothing.

Not used when a program asks kronos for something powerful: then the kronos daemon asks through kronos-agent, so the passphrase goes to the
daemon and never through the program that asked. Never asked at boot or for everyday things (installing an app, updating, mounting a USB stick).
"""
import argparse
import sys

import kronos_prompt_ui as ui  # noqa: E402  (sets the GTK version, so it comes before Gtk)
from gi.repository import Gtk  # noqa: E402


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=("unlock", "create"), default="unlock")
    ap.add_argument("--text", default="An app asked for an admin action.")
    ap.add_argument("--error", default="")
    args = ap.parse_args(argv)
    ui.install_css()
    result = []
    win = ui.Prompt(args.mode, args.text, args.error, on_done=lambda v: (result.append(v), Gtk.main_quit()))
    win.show_all()
    Gtk.main()
    if not result or result[0] is None:
        return 1
    sys.stdout.write(result[0] + "\n")
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
