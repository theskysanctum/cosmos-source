#!/usr/bin/env python3
"""Android (Waydroid) - set it up once, open it, and tune it for games. The window on top of cosmos_android.py.

  Set up Android     turns the Waydroid container on and downloads Android (asks for the admin passphrase once)
  Open Android       Android full screen in its own window (`waydroid show-full-ui`)
  Button mapper      put touch points on the screen and map controller buttons and keys to them (cosmos-touchmap-editor; cosmos_touchmap.py)
  Gaming settings    resolution, keeping the container awake, letting Android see your controllers, animations off, per-app touch/Wi-Fi tricks
                     (each setting is a Waydroid property from its own documentation - see cosmos_android.py); Save and apply, then restart Android
Comes with the Virtualization Expansion Pack.
"""
import subprocess
import sys
import threading

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402
import cosmos_display  # noqa: E402
import cosmos_settings_link  # noqa: E402

import cosmos_android as ca

BG, FG, ACCENT, BORDER = "#0a0e16", "#e8ecf4", "#ff0090", "#2a3a56"
CSS = f"""
window.cosmos-android {{ background-color: {BG}; }}
.an-title {{ color: {FG}; font-weight: bold; font-size: 22px; }}
.an-head {{ color: {FG}; font-weight: bold; font-size: 15px; }}
.an-text {{ color: {FG}; }}
.an-note {{ color: {FG}; opacity: 0.7; }}
.an-card {{ background-color: alpha({FG}, 0.04); border: 1px solid {BORDER}; border-radius: 8px; padding: 12px; }}
.an-button {{ background-color: {BG}; background-image: none; color: {FG}; border: 1px solid {BORDER}; box-shadow: none; font-weight: bold; padding: 6px 14px; }}
.an-button:focus, .an-button:hover {{ border-color: {ACCENT}; }}
.an-accent {{ background-color: {ACCENT}; background-image: none; color: {BG}; border: none; box-shadow: none; font-weight: bold; padding: 8px 18px; }}
entry, combobox button {{ background-color: {BG}; background-image: none; color: {FG}; border: 1px solid {BORDER}; }}
checkbutton label {{ color: {FG}; }}
textview, textview text {{ background-color: {BG}; color: {FG}; font-family: monospace; }}
"""


def label(text, css="an-text"):
    lab = Gtk.Label(label=text, xalign=0)
    lab.set_line_wrap(True)
    lab.get_style_context().add_class(css)
    return lab


def button(text, cb, accent=False):
    b = Gtk.Button(label=text)
    b.get_style_context().add_class("an-accent" if accent else "an-button")
    b.connect("clicked", lambda *_: cb())
    return b


def start_mapper():
    """The button mapper's daemon, when a profile is switched on. It runs once (it locks itself) and only acts while an Android window has focus."""
    import cosmos_touchmap
    settings = cosmos_touchmap.load_settings()
    if settings["enabled"] and settings["profile"]:
        try:
            subprocess.Popen(["cosmos-touchmapd"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError:
            pass


class Window(Gtk.Window):
    def __init__(self):
        super().__init__(title="Android")
        self.get_style_context().add_class("cosmos-android")
        cosmos_display.size_window(self, 760, 720)
        self.busy = False
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, margin=16)
        self.add(outer)
        outer.pack_start(label("Android", "an-title"), False, False, 0)
        self.status = label("", "an-note")
        outer.pack_start(self.status, False, False, 0)

        row = Gtk.Box(spacing=8)
        self.setup_button = button("Set up Android", self.on_setup, accent=True)
        self.open_button = button("Open Android", self.on_open)
        row.pack_start(self.setup_button, False, False, 0)
        row.pack_start(self.open_button, False, False, 0)
        row.pack_start(button("Restart Android", self.on_restart), False, False, 0)
        row.pack_start(button("Button mapper...", self.on_mapper), False, False, 0)
        outer.pack_start(row, False, False, 0)

        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        card.get_style_context().add_class("an-card")
        card.pack_start(label("Gaming settings", "an-head"), False, False, 0)
        grid = Gtk.Grid(column_spacing=10, row_spacing=6)
        self.resolution = Gtk.ComboBoxText()
        for key in ca.RESOLUTIONS:
            self.resolution.append(key, {"native": "Native (the screen's own size)", "1080p": "1080p (1920 x 1080)", "720p": "720p (1280 x 720) - fastest"}[key])
        grid.attach(label("Resolution", "an-note"), 0, 0, 1, 1)
        grid.attach(self.resolution, 1, 0, 1, 1)
        self.fake_touch = Gtk.Entry(hexpand=True, placeholder_text="e.g. com.rovio.*  - apps whose mouse counts as touch")
        self.fake_wifi = Gtk.Entry(hexpand=True, placeholder_text="e.g. com.gameloft.*  - apps that insist on Wi-Fi")
        grid.attach(label("Mouse as touch", "an-note"), 0, 1, 1, 1)
        grid.attach(self.fake_touch, 1, 1, 1, 1)
        grid.attach(label("Always Wi-Fi", "an-note"), 0, 2, 1, 1)
        grid.attach(self.fake_wifi, 1, 2, 1, 1)
        card.pack_start(grid, False, False, 0)
        self.keep_awake = Gtk.CheckButton(label="Keep Android awake between apps (no stall when a game resumes)")
        self.gamepads = Gtk.CheckButton(label="Let Android see my controllers directly (games with gamepad support just work)")
        self.animations = Gtk.CheckButton(label="Switch Android's animations off")
        for c in (self.keep_awake, self.gamepads, self.animations):
            card.pack_start(c, False, False, 0)
        card.pack_start(button("Save and apply", self.on_apply, accent=True), False, False, 4)
        card.pack_start(label("Most of these take effect the next time Android starts: press Restart Android after applying. Android has to have been "
                              "started once for the settings to apply.", "an-note"), False, False, 0)
        outer.pack_start(card, False, False, 0)

        scroll = Gtk.ScrolledWindow()
        scroll.set_vexpand(True)
        self.log_view = Gtk.TextView(editable=False, cursor_visible=False, wrap_mode=Gtk.WrapMode.WORD_CHAR)
        scroll.add(self.log_view)
        outer.pack_start(scroll, True, True, 0)
        self.connect("destroy", Gtk.main_quit)
        self.load()

    def load(self):
        s = ca.load_settings()
        self.resolution.set_active_id(s["resolution"])
        self.keep_awake.set_active(s["keep_awake"])
        self.gamepads.set_active(s["gamepads"])
        self.animations.set_active(s["animations_off"])
        self.fake_touch.set_text(s["fake_touch"])
        self.fake_wifi.set_text(s["fake_wifi"])
        self.refresh_status()

    def refresh_status(self):
        st = ca.status()
        if not st["installed"]:
            text = "Waydroid isn't installed. Install the Virtualization pack from the Shop."
        elif not st["initialized"]:
            text = "Android isn't set up yet. Press Set up Android."
        else:
            text = "Android is set up." + (f" Container: {st['container'].lower()}, session: {st['session'].lower()}." if st["container"] else "")
        self.status.set_text(text)
        self.setup_button.set_sensitive(st["installed"] and not self.busy)

    def log(self, text):
        def add():
            buf = self.log_view.get_buffer()
            buf.insert(buf.get_end_iter(), text if text.endswith("\n") else text + "\n")
            self.log_view.scroll_mark_onscreen(buf.get_insert())
            return False
        GLib.idle_add(add)

    def background(self, work):
        if self.busy:
            return
        self.busy = True
        self.setup_button.set_sensitive(False)

        def run():
            try:
                work()
            except (ca.AndroidError, OSError) as exc:
                self.log(f"Not done: {exc}")
            GLib.idle_add(self.finished)
        threading.Thread(target=run, daemon=True).start()

    def finished(self):
        self.busy = False
        self.refresh_status()
        return False

    def on_setup(self):
        import cosmos_kronos
        self.background(lambda: ca.setup(cosmos_kronos.ask, self.log, on_output=self.log))

    def on_open(self):
        try:
            subprocess.Popen(["waydroid", "show-full-ui"])
        except OSError as exc:
            self.log(f"Couldn't open Android: {exc}")
        start_mapper()

    def on_mapper(self):
        opener = getattr(self, "open_mapper", None)                  # set by Settings, which shows the mapper as a page of its own
        if opener:
            opener()
            return
        try:
            subprocess.Popen(["cosmos-touchmap-editor"])
        except OSError as exc:
            self.log(f"Couldn't open the button mapper: {exc}")

    def on_restart(self):
        def work():
            self.log("Stopping the Android session ... it starts again when you open Android.")
            rc, text = ca.default_run(["waydroid", "session", "stop"], 30)
            self.log("Stopped." if rc == 0 else text.strip() or "Couldn't stop it (it may not be running).")
        self.background(work)

    def on_apply(self):
        try:
            settings = ca.save_settings({"resolution": self.resolution.get_active_id() or "native", "keep_awake": self.keep_awake.get_active(),
                                         "gamepads": self.gamepads.get_active(), "animations_off": self.animations.get_active(),
                                         "fake_touch": self.fake_touch.get_text(), "fake_wifi": self.fake_wifi.get_text()})
        except ca.AndroidError as exc:
            self.log(str(exc))
            return

        def work():
            results = ca.apply_gaming(settings, log=self.log)
            failed = [w for w, ok, _m in results if not ok]
            self.log("All applied. Restart Android for them to take effect." if not failed else
                     "Saved. Some couldn't be applied yet (" + ", ".join(w.split(".")[-1] for w in failed) + ") - start Android once, then press Save and apply again.")
        self.background(work)


def main():
    cosmos_settings_link.redirect("android")
    provider = Gtk.CssProvider()
    provider.load_from_data(cosmos_display.scale_css(CSS).encode())
    Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    Window().show_all()
    Gtk.main()
    return 0


if __name__ == "__main__":
    sys.exit(main())
