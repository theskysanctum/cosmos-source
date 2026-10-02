#!/usr/bin/env python3
"""CosmOS recovery - a small controller-friendly window (not a shell) started from the "CosmOS recovery" boot entry (cosmos.recovery=1).
It runs as root with no password: whoever is at the machine with the disk in their hands can do all of this anyway, and it keeps the
"no terminal" rule even when something is broken. What it offers is in cosmos_recovery.py.

  cosmos-recovery          the window
"""
import subprocess
import sys

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gtk  # noqa: E402
import cosmos_display  # noqa: E402

import cosmos_recovery as rec

BG, FG, ACCENT, BORDER = "#0a0e16", "#e8ecf4", "#ff0090", "#2a3a56"
CSS = f"""
window.cosmos-recovery {{ background-color: {BG}; }}
.rc-title {{ color: {FG}; font-weight: bold; font-size: 24px; }}
.rc-text {{ color: {FG}; }}
.rc-note {{ color: {FG}; opacity: 0.7; }}
.rc-msg {{ color: {ACCENT}; font-weight: bold; }}
.rc-button {{ background-color: {BG}; background-image: none; color: {FG}; border: 1px solid {BORDER}; box-shadow: none; font-weight: bold; padding: 10px 18px; }}
.rc-button:focus, .rc-button:hover {{ border-color: {ACCENT}; }}
entry {{ background-color: {BG}; color: {FG}; }}
"""


class Window(Gtk.Window):
    def __init__(self):
        super().__init__(title="CosmOS recovery")
        cosmos_display.fit_on_show(self, scroll=False)      # text wraps and fixed sizes are capped, so it can never be wider than the screen
        self.get_style_context().add_class("cosmos-recovery")
        self.fullscreen()
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin=48)
        self.add(outer)
        outer.pack_start(self._label("CosmOS recovery", "rc-title"), False, False, 0)
        outer.pack_start(self._label("Nothing here needs a password. Nothing runs until you choose it.", "rc-note"), False, False, 0)
        self.msg = self._label("", "rc-msg")
        for text, note, cb in (
            ("Go back to the previous operating system", "Boots the version you had before the last update, next time it starts.", self.do_rollback),
            ("Reset the admin passphrase", "Forgets it, so the next admin action asks for a new one. The admin activity log is kept.", self.do_reset_pass),
            ("Clear a profile PIN", "For someone who forgot theirs. Their files are not touched.", self.do_pin),
            ("Reset my files to a fresh install", "Deletes everything in the home folder. You'll be asked to type RESET.", self.do_factory),
            ("Restart normally", "Leave recovery and start CosmOS.", self.do_restart),
        ):
            b = Gtk.Button(label=text)
            b.get_style_context().add_class("rc-button")
            b.set_tooltip_text(note)
            b.connect("clicked", lambda _b, cb=cb: cb())
            outer.pack_start(b, False, False, 0)
            outer.pack_start(self._label(note, "rc-note"), False, False, 0)
        outer.pack_start(self.msg, False, False, 8)
        self.connect("destroy", Gtk.main_quit)

    @staticmethod
    def _label(text, css):
        lab = Gtk.Label(label=text, xalign=0)
        lab.set_line_wrap(True)
        lab.get_style_context().add_class(css)
        return lab

    def ask(self, title, text, entry=False):
        dialog = Gtk.MessageDialog(transient_for=self, modal=True, message_type=Gtk.MessageType.QUESTION,
                                   buttons=Gtk.ButtonsType.OK_CANCEL, text=title, secondary_text=text)
        field = None
        if entry:
            field = Gtk.Entry()
            dialog.get_content_area().pack_start(field, False, False, 6)
        dialog.show_all()
        response = dialog.run()
        value = field.get_text() if field else ""
        dialog.destroy()
        return (response == Gtk.ResponseType.OK), value

    def attempt(self, fn, done):
        try:
            fn()
            self.msg.set_text(done)
        except (rec.RecoveryError, OSError) as exc:
            self.msg.set_text(f"Not done: {exc}")

    def do_rollback(self):
        ok, _ = self.ask("Go back to the previous version?", "It takes effect the next time CosmOS starts.")
        if ok:
            self.attempt(rec.rollback_os, "Done - the previous version starts next time.")

    def do_reset_pass(self):
        ok, _ = self.ask("Reset the admin passphrase?", "The next admin action will ask you to choose a new one.")
        if ok:
            self.attempt(rec.reset_kronos_passphrase, "The admin passphrase was reset.")

    def do_pin(self):
        rows = rec.list_profiles()
        with_pin = [r for r in rows if r[2]]
        if not with_pin:
            self.msg.set_text("No profile has a PIN.")
            return
        names = ", ".join(f"{name} ({slug})" for slug, name, _ in with_pin)
        ok, slug = self.ask("Clear whose PIN?", f"Type the profile's short name exactly. Profiles with a PIN: {names}", entry=True)
        if ok:
            self.attempt(lambda: rec.clear_profile_pin(slug.strip()), "The PIN was cleared.")

    def do_factory(self):
        ok, word = self.ask("Delete everything in your home folder?", f"This cannot be undone. Type {rec.CONFIRM_WORD} to go ahead.", entry=True)
        if ok:
            import pwd
            pw = pwd.getpwnam("user")
            self.attempt(lambda: rec.factory_reset_user_data(word.strip(), owner=(pw.pw_uid, pw.pw_gid)), "Your files were reset.")

    def do_restart(self):
        subprocess.run(["systemctl", "reboot"])


def main():
    provider = Gtk.CssProvider()
    provider.load_from_data(cosmos_display.scale_css(CSS).encode())
    Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    Window().show_all()
    Gtk.main()


if __name__ == "__main__":
    sys.exit(main())
