#!/usr/bin/env python3
"""cosmos-task - run a command in a small window that shows what it prints.

CosmOS has no terminal emulator. Things that used to open one - installs from the Shop,
asset downloads, updates - run here instead: a window with the
command's output as it arrives, a status line, a one-line box to answer a question the
command asks ("Continue? [y/N]", a code prompt), and a Stop/Close button. It is a log
viewer with a reply box, not a shell: nothing here starts a shell for you or lets you run
other commands.

    cosmos-task [--title TITLE] [--shell] [--] COMMAND [ARGS...]
    cosmos-task --shell "some | shell pipeline"

Exit status is the command's own (so a caller that waits can tell success from failure);
the window stays open after it finishes so the last lines can be read - Escape or the Close
button ends it.
"""
import argparse
import os
import signal
import subprocess
import sys
import threading

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402
import cosmos_display  # noqa: E402

BG = "#0a0e16"
FG = "#e8ecf4"
ACCENT = "#ff0090"
BORDER = "#2a3a56"

CSS = f"""
window.cosmos-task {{ background-color: {BG}; }}
.ct-title {{ color: {FG}; font-weight: bold; font-size: 20px; }}
.ct-status {{ color: {FG}; opacity: 0.75; }}
.ct-ok {{ color: #5ee08a; font-weight: bold; }}
.ct-fail {{ color: {ACCENT}; font-weight: bold; }}
textview.ct-log, textview.ct-log text {{ background-color: #05070c; color: {FG}; }}
.ct-entry {{ background-color: {BG}; background-image: none; color: {FG}; caret-color: {FG};
            border: 1px solid {BORDER}; box-shadow: none; padding: 8px 10px; }}
.ct-entry:focus {{ border-color: {ACCENT}; }}
.ct-button {{ background-color: {BG}; background-image: none; color: {FG}; border: 1px solid {BORDER};
             box-shadow: none; font-weight: bold; padding: 8px 20px; }}
.ct-button:focus, .ct-button:hover {{ border-color: {ACCENT}; }}
.ct-accent {{ background-color: {ACCENT}; background-image: none; color: {BG}; border: none;
             box-shadow: none; font-weight: bold; padding: 8px 20px; }}
"""


def command_from(args):
    """The argv to run: the words given, or `bash -c` for --shell."""
    if args.shell:
        return ["bash", "-c", " ".join(args.command)]
    return list(args.command)


class TaskWindow(Gtk.Window):
    def __init__(self, title, argv):
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        self.set_title(title)
        cosmos_display.size_window(self, 900, 560)
        self.get_style_context().add_class("cosmos-task")
        self.argv = argv
        self.returncode = None
        self.process = None

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        root.set_border_width(16)
        self.add(root)

        heading = Gtk.Label(label=title, xalign=0)
        heading.get_style_context().add_class("ct-title")
        root.pack_start(heading, False, False, 0)

        self.log = Gtk.TextView()
        self.log.set_editable(False)
        self.log.set_cursor_visible(False)
        self.log.set_monospace(True)
        self.log.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.log.get_style_context().add_class("ct-log")
        scroller = Gtk.ScrolledWindow()
        scroller.add(self.log)
        scroller.set_vexpand(True)
        root.pack_start(scroller, True, True, 0)
        self.scroller = scroller

        self.status = Gtk.Label(label="Running...", xalign=0)
        self.status.get_style_context().add_class("ct-status")
        root.pack_start(self.status, False, False, 0)

        row = Gtk.Box(spacing=8)
        self.reply = Gtk.Entry()
        self.reply.set_placeholder_text("Type a reply if it asks a question, then press Enter")
        self.reply.get_style_context().add_class("ct-entry")
        self.reply.connect("activate", self.on_reply)
        row.pack_start(self.reply, True, True, 0)
        send = Gtk.Button(label="Send")
        send.get_style_context().add_class("ct-button")
        send.connect("clicked", self.on_reply)
        row.pack_start(send, False, False, 0)
        self.stop = Gtk.Button(label="Stop")
        self.stop.get_style_context().add_class("ct-accent")
        self.stop.connect("clicked", self.on_stop)
        row.pack_start(self.stop, False, False, 0)
        root.pack_start(row, False, False, 0)

        self.connect("key-press-event", self.on_key)
        self.connect("destroy", self.on_destroy)
        GLib.idle_add(self.start)

    # ---- running the command --------------------------------------------------
    def start(self):
        self.append(f"$ {' '.join(self.argv)}\n\n")
        env = dict(os.environ, TERM="dumb", NO_COLOR="1", PYTHONUNBUFFERED="1")
        try:
            self.process = subprocess.Popen(
                self.argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1, env=env, start_new_session=True,
            )
        except OSError as exc:
            self.append(f"Could not start: {exc}\n")
            self.finish(127)
            return False
        threading.Thread(target=self.pump, daemon=True).start()
        self.reply.grab_focus()
        return False

    def pump(self):
        for line in self.process.stdout:
            GLib.idle_add(self.append, line)
        code = self.process.wait()
        GLib.idle_add(self.finish, code)

    def append(self, text):
        buffer = self.log.get_buffer()
        buffer.insert(buffer.get_end_iter(), text)
        adjustment = self.scroller.get_vadjustment()
        GLib.idle_add(adjustment.set_value, adjustment.get_upper())
        return False

    def finish(self, code):
        self.returncode = code
        ctx = self.status.get_style_context()
        ctx.remove_class("ct-status")
        ctx.add_class("ct-ok" if code == 0 else "ct-fail")
        self.status.set_text("Finished." if code == 0 else f"Failed (exit code {code}).")
        self.reply.set_sensitive(False)
        self.stop.set_label("Close")
        self.stop.grab_focus()
        return False

    # ---- input --------------------------------------------------------------
    def on_reply(self, _widget):
        text = self.reply.get_text()
        self.reply.set_text("")
        if self.process and self.process.poll() is None and self.process.stdin:
            try:
                self.process.stdin.write(text + "\n")
                self.process.stdin.flush()
                self.append(f"> {text}\n")
            except OSError:
                pass

    def on_stop(self, _widget):
        if self.returncode is None:
            self.terminate()
        else:
            self.destroy()

    def on_key(self, _widget, event):
        if event.keyval == Gdk.KEY_Escape:
            self.on_stop(None)
            return True
        return False

    def terminate(self):
        if self.process and self.process.poll() is None:
            try:
                os.killpg(self.process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass

    def on_destroy(self, _widget):
        self.terminate()
        Gtk.main_quit()


def main(argv=None):
    parser = argparse.ArgumentParser(prog="cosmos-task", description=__doc__.split("\n\n")[0])
    parser.add_argument("--title", default="Task")
    parser.add_argument("--shell", action="store_true", help="run COMMAND through bash -c")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    if args.command and args.command[0] == "--":
        args.command = args.command[1:]
    if not args.command:
        parser.error("no command given")
    provider = Gtk.CssProvider()
    provider.load_from_data(cosmos_display.scale_css(CSS).encode())
    Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_USER)
    win = TaskWindow(args.title, command_from(args))
    win.show_all()
    Gtk.main()
    return win.returncode if win.returncode is not None else 130


if __name__ == "__main__":
    sys.exit(main())
