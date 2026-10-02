#!/usr/bin/env python3
"""CosmOS Files - the file manager, scoped: your profile's files, USB drives, and the machine's
other internal drives. Never / and never anything else (see cosmos_places.py for exactly what and
how that is enforced, and why Nautilus could not be restricted this way).

  cosmos-files              open on your profile's files
  cosmos-files PATH|URI     open a folder (or a file's folder) if it is inside one of those places;
                            anything else opens the place list with a note - this is what
                            "open folder" from other apps ends up as (inode/directory)
  cosmos-files --demo       pretend drives, for looking at it

The window itself is cosmos_files_ui.py, shared with the file dialog other apps get
(cosmos-portal-filechooser).
"""
import sys

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gtk  # noqa: E402

import cosmos_files_ui as ui


def main(argv):
    ui.install_css()
    demo = ui.make_demo() if "--demo" in argv else None
    args = [a for a in argv if not a.startswith("--")]
    win = ui.Window(args[0] if args else None, demo)
    win.show_all()
    Gtk.main()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
