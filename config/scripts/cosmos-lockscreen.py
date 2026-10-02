#!/usr/bin/env python3
"""CosmOS's own screen locker - direct requirement ("I don't
want i3lock anymore either"), a real GTK3 app matching cosmos-bar's/
cosmos-greeter's own dark theme.

How locking works on Wayland: running `cosmos-lockscreen` just asks
cosmos-wm to lock ("lock" on its control socket). The compositor is what
actually locks - it hides every window, panel and popup, pins keyboard focus
to the lock screen and ignores its own shortcuts - and it then starts this
program again as `cosmos-lockscreen --locked`, which is the UI: a full-screen
OVERLAY layer surface (namespace "cosmos-lockscreen") with exclusive keyboard
focus. If this UI dies while the screen is locked the compositor starts a new
one, so a crash can never unlock the session. Only a correct password sends
"unlock".

There is no Unix password to check (the one account is logged in automatically), so
unlocking is by the active profile's optional PIN (cosmos_profiles.py): a profile with a
PIN asks for it; a profile without one unlocks with a button press / Enter, so the lock
still keeps out a stray touch of the keyboard but is not a barrier. The PIN is checked
here, in this process, against the salted hash stored in the profile.

Threat model, stated plainly: the control socket is per-user, so any process
already running as this user could send "unlock" without knowing the password.
The lock protects a session from someone at the keyboard, not from code that is
already running as the user (which could equally just read their files) - and, with a
PIN that is 4-12 digits, not from someone who wants in badly: it is a courtesy between
people who share a machine.
Deliberately no Escape-to-dismiss handler anywhere in this file - Escape
closing a lock screen would defeat its entire purpose."""
import os
import pwd
import sys
import time

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GtkLayerShell", "0.1")
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk, GtkLayerShell  # noqa: E402
import cosmos_display  # noqa: E402

import cosmos_profiles
import cosmos_wm_ipc

BG = "#0a0e16"
FG = "#e8ecf4"
ACCENT = "#ff0090"
BORDER = "#2a3a56"
BACKGROUND_IMAGE = "/usr/share/backgrounds/cosmos-galaxy.jpg"
LOGO_PATH = "/opt/astronar-studio/launcher/icons/cosmos_logo.png"
AVATAR_SIZE = cosmos_display.px(96)

CSS = f"""
window.cosmos-lockscreen {{
    background-color: {BG};
}}
.cosmos-lockscreen-card {{
    background-color: {BG};
    border: 2px solid {BORDER};
    border-radius: 6px;
}}
.cosmos-lockscreen-label {{
    color: {FG};
    font-weight: bold;
}}
.cosmos-lockscreen-error {{
    color: {ACCENT};
    font-weight: bold;
}}
.cosmos-lockscreen-entry {{
    background-color: {BG};
    background-image: none;
    color: {FG};
    caret-color: {FG};
    border: 1px solid {BORDER};
    box-shadow: none;
    font-weight: bold;
    padding: 10px 12px;
}}
.cosmos-lockscreen-entry:focus {{
    border-color: {ACCENT};
    box-shadow: none;
}}
.cosmos-lockscreen-button-accent {{
    background-color: {ACCENT};
    background-image: none;
    color: {BG};
    border: 1px solid {ACCENT};
    box-shadow: none;
    font-weight: bold;
    padding: 8px 14px;
}}
.cosmos-lockscreen-button-accent:hover {{
    background-color: {FG};
    background-image: none;
    border-color: {FG};
}}
"""


def check_unlock(profile, entered):
    """True if `entered` unlocks the profile: its PIN matches, or it has none."""
    return cosmos_profiles.verify_pin(profile, entered)


class LockScreen(Gtk.Window):
    def __init__(self):
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        cosmos_display.fit_on_show(self, scroll=False)      # text wraps and fixed sizes are capped, so it can never be wider than the screen
        self.set_title("cosmos-lockscreen")
        self.set_decorated(False)
        self.get_style_context().add_class("cosmos-lockscreen")
        GtkLayerShell.init_for_window(self)
        GtkLayerShell.set_namespace(self, "cosmos-lockscreen")
        GtkLayerShell.set_layer(self, GtkLayerShell.Layer.OVERLAY)
        for edge in (GtkLayerShell.Edge.TOP, GtkLayerShell.Edge.BOTTOM, GtkLayerShell.Edge.LEFT, GtkLayerShell.Edge.RIGHT):
            GtkLayerShell.set_anchor(self, edge, True)
        GtkLayerShell.set_exclusive_zone(self, -1)
        GtkLayerShell.set_keyboard_mode(self, GtkLayerShell.KeyboardMode.EXCLUSIVE)

        # The name shown is the active profile's (the Unix account is the same for everyone).
        self.profile = cosmos_profiles.active_profile()
        self.display_name = self.profile["name"] if self.profile else pwd.getpwuid(os.getuid()).pw_gecos.split(",")[0] or "CosmOS"
        self.has_pin = cosmos_profiles.has_pin(self.profile)
        self.unlocked = False

        overlay = Gtk.Overlay()
        self.add(overlay)

        background = Gtk.Image()
        try:
            pixbuf = GdkPixbuf.Pixbuf.new_from_file(BACKGROUND_IMAGE)
            background.set_from_pixbuf(pixbuf)
        except GLib.Error:
            pass
        overlay.add(background)

        center = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        center.set_halign(Gtk.Align.CENTER)
        center.set_valign(Gtk.Align.CENTER)

        # Direct request: "space between everything so it's not so
        # crowded and looks professional" (same fix cosmos-greeter.py's
        # own card just needed) - nothing (avatar, entry, button) should
        # touch the card's own edge at all.
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        card.get_style_context().add_class("cosmos-lockscreen-card")
        card.set_border_width(32)

        # Diamond-clipped avatar, same technique as cosmos-bar.py's own
        # build_avatar_widget() and cosmos-greeter.py's own
        # on_draw_avatar() - direct request ("use the diamond in login
        # and lock like in the bottom bar" / "use the same type of
        # logic in lock like we do login greeter"), a plain
        # Gtk.DrawingArea painting the pixbuf through a real Cairo clip
        # path rather than a rectangular Gtk.Image.
        # Explicit per-widget margins, not just card.set_border_width()
        # above - real, live bug this fixes (confirmed via a real
        # screenshot: the Unlock button's own left/right/bottom edges
        # exactly matched the card's own border pixel-for-pixel, zero
        # visible gap, despite border_width being set). Belt-and-
        # suspenders on every child here, not just the one that was
        # actually observed touching, since the same underlying box-
        # sizing negotiation could just as easily bite any of the others
        # depending on font metrics/content length.
        CARD_MARGIN = 24

        self.avatar_pixbuf = None
        self.avatar_scale = cosmos_display.monitor_scale()      # the avatar is loaded at this scale times its size, so it is sharp on a scaled output
        self.avatar_area = Gtk.DrawingArea()
        self.avatar_area.set_halign(Gtk.Align.CENTER)
        self.avatar_area.set_size_request(AVATAR_SIZE, AVATAR_SIZE)
        self.avatar_area.set_margin_top(CARD_MARGIN)
        self.avatar_area.set_margin_start(CARD_MARGIN)
        self.avatar_area.set_margin_end(CARD_MARGIN)
        self.avatar_area.connect("draw", self.on_draw_avatar)
        card.pack_start(self.avatar_area, False, False, 0)

        name_label = Gtk.Label(label=self.display_name)
        name_label.get_style_context().add_class("cosmos-lockscreen-label")
        name_label.set_margin_start(CARD_MARGIN)
        name_label.set_margin_end(CARD_MARGIN)
        card.pack_start(name_label, False, False, 0)

        self.password_entry = Gtk.Entry()
        self.password_entry.set_visibility(False)
        self.password_entry.set_input_purpose(Gtk.InputPurpose.PIN)
        self.password_entry.set_placeholder_text("PIN")
        self.password_entry.set_width_chars(24)
        self.password_entry.get_style_context().add_class("cosmos-lockscreen-entry")
        self.password_entry.connect("activate", self.on_unlock_clicked)
        self.password_entry.set_margin_start(CARD_MARGIN)
        self.password_entry.set_margin_end(CARD_MARGIN)
        self.password_entry.set_no_show_all(True)     # only for a profile that has a PIN
        card.pack_start(self.password_entry, False, False, 0)

        self.error_label = Gtk.Label(label="")
        self.error_label.get_style_context().add_class("cosmos-lockscreen-error")
        self.error_label.set_margin_start(CARD_MARGIN)
        self.error_label.set_margin_end(CARD_MARGIN)
        card.pack_start(self.error_label, False, False, 0)

        unlock_button = self.unlock_button = Gtk.Button(label="Unlock")
        unlock_button.get_style_context().add_class("cosmos-lockscreen-button-accent")
        unlock_button.connect("clicked", self.on_unlock_clicked)
        unlock_button.set_margin_start(CARD_MARGIN)
        unlock_button.set_margin_end(CARD_MARGIN)
        unlock_button.set_margin_bottom(CARD_MARGIN)
        card.pack_start(unlock_button, False, False, 0)

        center.pack_start(card, False, False, 0)
        overlay.add_overlay(center)

        self.load_avatar()

    def load_avatar(self):
        face_path = os.path.expanduser("~/.face")
        try:
            pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(face_path, AVATAR_SIZE * self.avatar_scale, AVATAR_SIZE * self.avatar_scale, False)
        except GLib.Error:
            try:
                pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(LOGO_PATH, AVATAR_SIZE * self.avatar_scale, AVATAR_SIZE * self.avatar_scale, True)
            except GLib.Error:
                pixbuf = None
        self.avatar_pixbuf = pixbuf
        self.avatar_area.queue_draw()

    def on_draw_avatar(self, _widget, cr):
        if self.avatar_pixbuf is None:
            return False
        half = AVATAR_SIZE / 2
        cr.move_to(half, 0)
        cr.line_to(AVATAR_SIZE, half)
        cr.line_to(half, AVATAR_SIZE)
        cr.line_to(0, half)
        cr.close_path()
        cr.clip()
        cosmos_display.paint_pixbuf(cr, self.avatar_pixbuf, self.avatar_scale)
        return False

    def on_unlock_clicked(self, _widget):
        entered = self.password_entry.get_text()
        self.error_label.set_text("")
        if check_unlock(self.profile, entered):
            self.unlocked = True
            Gtk.main_quit()
        else:
            self.error_label.set_text("Wrong PIN")
            self.password_entry.set_text("")
            self.password_entry.grab_focus()


def main():
    if "--locked" not in sys.argv[1:]:
        # Ask the compositor to lock; it starts the UI (below) itself.
        reply = cosmos_wm_ipc.request("lock")
        if reply is None or not reply.startswith("ok"):
            print("cosmos-lockscreen: cosmos-wm did not answer, not locking", flush=True)
            return 1
        return 0

    provider = Gtk.CssProvider()
    provider.load_from_data(cosmos_display.scale_css(CSS).encode())
    Gtk.StyleContext.add_provider_for_screen(
        Gdk.Screen.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_USER
    )

    win = LockScreen()
    win.show_all()
    if win.has_pin:
        win.password_entry.show()
    else:
        win.password_entry.hide()
    # Blank cursor - direct request ("don't have any mouse on login greeter or
    # lockscreen"): keyboard-only, like everything else on this screen.
    win.get_window().set_cursor(
        Gdk.Cursor.new_for_display(Gdk.Display.get_default(), Gdk.CursorType.BLANK_CURSOR)
    )
    (win.password_entry if win.has_pin else win.unlock_button).grab_focus()
    Gtk.main()

    if win.unlocked:
        cosmos_wm_ipc.request("unlock")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
