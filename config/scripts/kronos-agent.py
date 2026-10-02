#!/usr/bin/env python3
"""kronos-agent - the desktop session's prompt for the admin passphrase.

It runs for the whole session (started by cosmos-session-apps), connects to the kronos daemon and waits. When a program asks for something
powerful the DAEMON sends it a question - what the action is and what the kernel says is asking - and this shows the window and sends the
passphrase straight back to the daemon. The passphrase never passes through the program that asked (which could be anything), and the
words in the window come from the daemon, not from that program. If this agent isn't running, admin actions that need the passphrase
can only be asked for in a terminal (over SSH) - they are refused on the desktop, not asked some weaker way.
"""
import json
import os
import socket
import sys
import threading
import time

import kronos_prompt_ui as ui  # noqa: E402  (sets the GTK version, so it comes before Gtk)
from gi.repository import GLib, Gtk  # noqa: E402

SOCKET = os.environ.get("KRONOS_SOCKET", "/run/kronos/kronos.sock")


def ask_in_window(question):
    """Show the window on the GTK thread and wait for the answer (text, or None if cancelled)."""
    done, holder = threading.Event(), []

    def show():
        win = ui.Prompt(question.get("mode", "unlock"), question.get("text", ""), question.get("error", ""), question.get("program", ""),
                        on_done=lambda value: (holder.append(value), done.set()))
        win.show_all()
        return False
    GLib.idle_add(show)
    done.wait()
    return holder[0]


def serve_once(path=SOCKET, ask=ask_in_window):
    """One connection's life. Returns when the daemon goes away."""
    with socket.socket(socket.AF_UNIX) as sock:
        sock.connect(path)
        sock.sendall(json.dumps({"op": "agent"}).encode() + b"\n")
        stream = sock.makefile("rw", encoding="utf-8")
        first = stream.readline()
        if not first or json.loads(first).get("event") != "agent-attached":
            return
        for line in stream:
            try:
                question = json.loads(line)
                answer = ask(question)
                reply = {"id": question.get("id"), "passphrase": answer}
            except ValueError:
                continue
            stream.write(json.dumps(reply) + "\n")
            stream.flush()


def serve_forever(path=SOCKET):
    while True:
        try:
            serve_once(path)
        except (OSError, ValueError):
            pass
        time.sleep(2)                                # the daemon restarted or isn't up yet: try again


def main():
    ui.install_css()
    threading.Thread(target=serve_forever, daemon=True).start()
    Gtk.main()


if __name__ == "__main__":
    sys.exit(main())
