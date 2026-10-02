#!/usr/bin/env python3
"""CosmOS Admin activity (Settings) - what has been done with admin rights on this computer, and the admin passphrase.

  Recent activity   every request to kronos, newest first: what, which program asked, allowed or refused and why. Filter by outcome.
  Check the log     verifies the hash chain (a deleted or edited line is found).
  Lock admin now    ends this program's admin session; the next powerful action asks for the passphrase again.
  Change passphrase asks for the current one, then a new one.

It only reads and asks through cosmos_kronos.py; it has no admin rights of its own.
"""
import sys
import time

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gtk  # noqa: E402
import cosmos_display  # noqa: E402
import cosmos_settings_link  # noqa: E402

import cosmos_kronos as ck

BG, FG, ACCENT, BORDER = "#0a0e16", "#e8ecf4", "#ff0090", "#2a3a56"
CSS = f"""
window.cosmos-admin {{ background-color: {BG}; }}
.ad-title {{ color: {FG}; font-weight: bold; font-size: 22px; }}
.ad-text {{ color: {FG}; }}
.ad-note {{ color: {FG}; opacity: 0.7; }}
.ad-warn {{ color: {ACCENT}; font-weight: bold; }}
.ad-button {{ background-color: {BG}; background-image: none; color: {FG}; border: 1px solid {BORDER}; box-shadow: none; font-weight: bold; padding: 6px 14px; }}
.ad-button:focus, .ad-button:hover {{ border-color: {ACCENT}; }}
treeview {{ background-color: {BG}; color: {FG}; }}
treeview header button {{ background-image: none; background-color: {BG}; color: {FG}; border: 1px solid {BORDER}; }}
"""
FILTERS = (("Everything", None), ("Done automatically", "auto"), ("Done with the passphrase", "passphrase-ok"),
           ("Refused", "refused"), ("Asked for the passphrase", "passphrase-required"), ("Wrong passphrase", "passphrase-wrong"))


def describe(entry):
    """(when, what, who, outcome) for one audit record."""
    when = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(entry.get("ts", 0)))
    peer = entry.get("peer") or {}
    who = (peer.get("script") or peer.get("exe") or "?").rsplit("/", 1)[-1]
    outcome = entry.get("decision") or entry.get("event", "")
    if entry.get("event") == "result":
        outcome = "finished" + (" ok" if entry.get("exit") == 0 else f" (exit {entry.get('exit')})")
    return when, entry.get("action") or entry.get("event", ""), who, outcome, entry.get("reason", "")


class Window(Gtk.Window):
    def __init__(self):
        super().__init__(title="Admin activity")
        self.get_style_context().add_class("cosmos-admin")
        cosmos_display.size_window(self, 900, 600)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, margin=20)
        self.add(box)
        title = Gtk.Label(label="Admin activity", xalign=0)
        title.get_style_context().add_class("ad-title")
        box.pack_start(title, False, False, 0)
        self.status = Gtk.Label(label="", xalign=0)
        self.status.set_line_wrap(True)
        self.status.get_style_context().add_class("ad-note")
        box.pack_start(self.status, False, False, 0)
        row = Gtk.Box(spacing=8)
        box.pack_start(row, False, False, 0)
        self.filter = Gtk.ComboBoxText()
        for label, _ in FILTERS:
            self.filter.append_text(label)
        self.filter.set_active(0)
        self.filter.connect("changed", lambda *_: self.refresh())
        row.pack_start(self.filter, False, False, 0)
        for text, cb in (("Refresh", self.refresh), ("Check the log", self.verify), ("Lock admin now", self.lock), ("Change passphrase", self.change)):
            b = Gtk.Button(label=text)
            b.get_style_context().add_class("ad-button")
            b.connect("clicked", lambda _b, cb=cb: cb())
            row.pack_start(b, False, False, 0)
        self.store = Gtk.ListStore(str, str, str, str, str)
        view = Gtk.TreeView(model=self.store)
        for i, name in enumerate(("When", "What", "Program", "Outcome", "Why")):
            col = Gtk.TreeViewColumn(name, Gtk.CellRendererText(), text=i)
            col.set_resizable(True)
            view.append_column(col)
        scroll = Gtk.ScrolledWindow()
        scroll.add(view)
        box.pack_start(scroll, True, True, 0)
        self.connect("destroy", Gtk.main_quit)
        self.refresh()

    def refresh(self):
        wanted = FILTERS[max(self.filter.get_active(), 0)][1]
        state = ck.call({"op": "status"})
        if state.get("status") != "ok":
            self.status.set_text(state.get("reason", "The admin service isn't available."))
            self.store.clear()
            return
        self.status.set_text(f"Admin passphrase: {state['passphrase']}.  This window is {'unlocked' if state['unlocked'] else 'locked'}.")
        result = ck.call({"op": "audit", "limit": 300, **({"decision": wanted} if wanted else {})})
        self.store.clear()
        for entry in result.get("entries", []):
            self.store.append(list(describe(entry)))

    def verify(self):
        r = ck.call({"op": "audit_verify"})
        self.status.set_text("The log is intact - nothing was edited or removed." if r.get("intact") else
                             "The log has been TAMPERED with: " + "; ".join(r.get("problems", [r.get("reason", "unknown")])[:3]))

    def lock(self):
        ck.call({"op": "lock"})
        self.refresh()

    def change(self):
        state = ck.call({"op": "status"})
        old = None
        if state.get("passphrase") == "set":
            old = ck.default_prompt("unlock", "Type your current admin passphrase to change it.")
            if old is None:
                return
        new = ck.default_prompt("create", "Choose the new admin passphrase.")
        if new is None:
            return
        r = ck.call({"op": "set_passphrase", "new": new, **({"old": old} if old is not None else {})})
        self.status.set_text("The admin passphrase was changed." if r.get("status") == "ok" else r.get("reason", "Not changed."))


def main():
    cosmos_settings_link.redirect("admin")
    provider = Gtk.CssProvider()
    provider.load_from_data(cosmos_display.scale_css(CSS).encode())
    Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    Window().show_all()
    Gtk.main()


if __name__ == "__main__":
    sys.exit(main())
