"""The window that asks for the admin passphrase, shared by kronos-prompt (a command-line prompt used by the Admin activity app and
`kronos passphrase`) and kronos-agent (the desktop session's prompt agent, which the kronos daemon asks directly)."""
import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gtk  # noqa: E402
import cosmos_display  # noqa: E402

BG = "#0a0e16"
FG = "#e8ecf4"
ACCENT = "#ff0090"
BORDER = "#2a3a56"
CSS = f"""
window.kronos-prompt {{ background-color: {BG}; }}
.kp-title {{ color: {FG}; font-weight: bold; font-size: 20px; }}
.kp-text {{ color: {FG}; font-size: 15px; }}
.kp-note {{ color: {FG}; opacity: 0.7; }}
.kp-error {{ color: {ACCENT}; font-weight: bold; }}
entry.kp-entry {{ background-color: {BG}; color: {FG}; border: 1px solid {BORDER}; padding: 8px; font-size: 16px; }}
entry.kp-entry:focus {{ border-color: {ACCENT}; }}
.kp-button {{ background-color: {BG}; background-image: none; color: {FG}; border: 1px solid {BORDER}; box-shadow: none; font-weight: bold; padding: 8px 18px; }}
.kp-button:focus, .kp-button:hover {{ border-color: {ACCENT}; }}
.kp-accent {{ background-color: {ACCENT}; background-image: none; color: {BG}; border: none; box-shadow: none; font-weight: bold; padding: 8px 18px; }}
"""


class Prompt(Gtk.Window):
    def __init__(self, mode, text, error, program="", on_done=None):
        super().__init__(title="Admin passphrase")
        self.mode, self.result, self.on_done, self.finished = mode, None, on_done, False
        self.get_style_context().add_class("kronos-prompt")
        cosmos_display.size_window(self, 480, -1)
        self.set_position(Gtk.WindowPosition.CENTER)
        self.set_keep_above(True)
        self.set_resizable(False)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin=24)
        self.add(box)
        box.pack_start(self._label("Choose an admin passphrase" if mode == "create" else "Admin passphrase needed", "kp-title"), False, False, 0)
        box.pack_start(self._label(text, "kp-text"), False, False, 0)
        if program:
            box.pack_start(self._label("Asked by: " + program, "kp-note"), False, False, 0)
        if mode == "create":
            box.pack_start(self._label("It is asked only for rare, powerful things - never at sign-in or for everyday ones. At least 6 characters; "
                                       "if you forget it, the recovery app can reset it.", "kp-note"), False, False, 0)
        if error:
            box.pack_start(self._label(error, "kp-error"), False, False, 0)
        self.first = self._entry("Passphrase")
        box.pack_start(self.first, False, False, 0)
        self.second = None
        if mode == "create":
            self.second = self._entry("Again")
            box.pack_start(self.second, False, False, 0)
        self.note = self._label("", "kp-error")
        box.pack_start(self.note, False, False, 0)
        row = Gtk.Box(spacing=8)
        cancel = Gtk.Button(label="Cancel")
        cancel.get_style_context().add_class("kp-button")
        cancel.connect("clicked", lambda *_: self.finish(None))
        ok = Gtk.Button(label="Set passphrase" if mode == "create" else "Unlock")
        ok.get_style_context().add_class("kp-accent")
        ok.connect("clicked", lambda *_: self.accept())
        row.pack_end(ok, False, False, 0)
        row.pack_end(cancel, False, False, 0)
        box.pack_start(row, False, False, 4)
        self.connect("key-press-event", self._key)
        self.connect("delete-event", lambda *_: (self.finish(None), True)[1])
        self.first.grab_focus()
        self.present()

    @staticmethod
    def _label(text, css):
        lab = Gtk.Label(label=text, xalign=0)
        lab.set_line_wrap(True)
        lab.set_max_width_chars(52)
        lab.get_style_context().add_class(css)
        return lab

    def _entry(self, placeholder):
        entry = Gtk.Entry(placeholder_text=placeholder, visibility=False, input_purpose=Gtk.InputPurpose.PASSWORD)
        entry.get_style_context().add_class("kp-entry")
        entry.connect("activate", lambda *_: self.accept())
        return entry

    def _key(self, _w, event):
        if event.keyval == Gdk.KEY_Escape:
            self.finish(None)
            return True
        return False

    def accept(self):
        phrase = self.first.get_text()
        if not phrase:
            self.note.set_text("Type the passphrase first.")
            return
        if self.second is not None and self.second.get_text() != phrase:
            self.note.set_text("The two entries don't match.")
            return
        self.finish(phrase)

    def finish(self, value):
        if self.finished:
            return
        self.finished = True
        self.result = value
        self.hide()
        self.destroy()
        if self.on_done:
            self.on_done(value)


def install_css():
    provider = Gtk.CssProvider()
    provider.load_from_data(cosmos_display.scale_css(CSS).encode())
    Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)


