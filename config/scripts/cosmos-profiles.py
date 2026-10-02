#!/usr/bin/env python3
"""CosmOS's profile picker - "who's using the machine?", shown at the start of every
session (replacing the Unix login screen and the old first-login wizard).

There is no login here: the one Unix account is logged in automatically. This screen
only chooses a *profile* (see cosmos_profiles.py): a name, a picture, an optional PIN
and that person's own favorites, wallpaper and settings. It is full screen and has no
way out except choosing (or creating) a profile, and it is built for a controller
first - every tile is a focusable button (d-pad / arrows move, A / Enter chooses,
Select opens the on-screen keyboard for a name or PIN, X / Delete removes a profile,
Guide / F2 flips "start in GameBox" (each tile says what it starts: Gridscreen or GameBox)).

  cosmos-profiles          show the picker; exit 0 once a profile is active
  cosmos-profiles --env    print `export` lines for the active profile (COSMOS_PROFILE,
                           COSMOS_PROFILE_DIR, COSMOS_START_IN) for the session script
"""
import glob
import os
import pwd
import shlex
import sys

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk  # noqa: E402
import cosmos_display  # noqa: E402

import cosmos_kronos
import cosmos_profiles as profiles

BG = "#0a0e16"
FG = "#e8ecf4"
ACCENT = "#ff0090"
BORDER = "#2a3a56"
AVATAR_DIR = "/usr/share/cosmos/avatars"
TILE_AVATAR = cosmos_display.px(128)
PICK_AVATAR = cosmos_display.px(88)

CSS = f"""
window.cosmos-profiles {{ background-color: {BG}; }}
.cp-title {{ color: {FG}; font-weight: bold; font-size: 30px; }}
.cp-sub {{ color: {FG}; opacity: 0.7; }}
.cp-tile {{ background-image: none; background-color: {BG}; border: 3px solid {BORDER}; border-radius: 8px;
           padding: 10px; box-shadow: none; }}
.cp-tile:hover, .cp-tile:focus {{ border-color: {ACCENT}; background-color: alpha({ACCENT}, 0.18); }}
.cp-tile label {{ color: {FG}; font-weight: bold; font-size: 16px; }}
.cp-avatar {{ background-image: none; background-color: {BG}; border: 3px solid {BORDER}; border-radius: 6px;
             padding: 4px; box-shadow: none; }}
.cp-avatar:focus {{ border-color: {FG}; }}
.cp-avatar-on {{ border-color: {ACCENT}; background-color: {ACCENT}; }}
.cp-entry {{ background-color: {BG}; background-image: none; color: {FG}; caret-color: {FG};
            border: 1px solid {BORDER}; box-shadow: none; font-weight: bold; font-size: 18px;
            padding: 10px 14px; }}
.cp-entry:focus {{ border-color: {ACCENT}; }}
.cp-button {{ background-color: {BG}; background-image: none; color: {FG}; border: 1px solid {BORDER};
             box-shadow: none; font-weight: bold; padding: 10px 24px; }}
.cp-button:focus, .cp-button:hover {{ border-color: {ACCENT}; }}
.cp-accent {{ background-color: {ACCENT}; background-image: none; color: {BG}; font-weight: bold;
             border: none; box-shadow: none; padding: 10px 24px; }}
.cp-accent:focus {{ box-shadow: 0 0 0 3px {FG}; }}
.cp-error {{ color: {BG}; background-color: {ACCENT}; font-weight: bold; padding: 8px 14px; border-radius: 6px; }}
.cp-hint {{ color: {FG}; opacity: 0.55; }}
.cp-mode {{ color: {ACCENT}; font-weight: bold; font-size: 13px; }}
"""


def avatar_files():
    return sorted(glob.glob(os.path.join(AVATAR_DIR, "*.png")))


def avatar_image(path, size):
    """A Gtk.Image of an avatar file at `size` (sharp on scaled outputs), or the generic avatar icon when the file can't be read."""
    image = Gtk.Image()
    try:
        cosmos_display.set_image_from_file(image, path, size, size)
        return image
    except (GLib.Error, TypeError):
        return Gtk.Image.new_from_icon_name("avatar-default", Gtk.IconSize.DIALOG)


class Picker(Gtk.Window):
    def __init__(self):
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        self.set_title("cosmos-profiles")
        self.set_decorated(False)
        self.get_style_context().add_class("cosmos-profiles")
        self.fullscreen()
        cosmos_display.size_window(self, 1920, 1080)      # also makes it unable to be bigger than the screen
        self.chosen = None
        self.pending = None            # profile waiting for its PIN
        self.avatar_choice = None
        self.avatar_buttons = []
        self.tile_profiles = {}        # tile button -> profile

        self.stack = Gtk.Stack()
        self.add(self.stack)
        self.pick_page = self.build_pick_page()
        self.pin_page = self.build_pin_page()
        self.create_page = self.build_create_page()
        for name, page in (("pick", self.pick_page), ("pin", self.pin_page), ("create", self.create_page)):
            self.stack.add_named(page, name)
        self.connect("key-press-event", self.on_key)
        self.connect("delete-event", self.on_delete)

        existing = profiles.list_profiles()
        if not existing:
            profiles.migrate_legacy(self.gecos_name())   # an install from before profiles
            existing = profiles.list_profiles()
        if existing:
            self.show_pick()
        else:
            self.show_create(first_run=True)

    # ---- helpers ---------------------------------------------------------------
    @staticmethod
    def gecos_name():
        try:
            return pwd.getpwuid(os.getuid()).pw_gecos.split(",")[0] or "Default"
        except KeyError:
            return "Default"

    @staticmethod
    def page(spacing=18):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=spacing)
        box.set_valign(Gtk.Align.CENTER)
        box.set_halign(Gtk.Align.CENTER)
        return box

    @staticmethod
    def label(text, css):
        lab = Gtk.Label(label=text)
        lab.get_style_context().add_class(css)
        lab.set_line_wrap(True)
        lab.set_justify(Gtk.Justification.CENTER)
        return lab

    def button(self, text, css="cp-button", handler=None):
        b = Gtk.Button(label=text)
        b.get_style_context().add_class(css)
        if handler:
            b.connect("clicked", handler)
        return b

    # ---- page: pick ----------------------------------------------------------
    def build_pick_page(self):
        page = self.page(20)
        page.pack_start(self.label("Who's using CosmOS?", "cp-title"), False, False, 0)
        page.pack_start(self.label("Pick your profile", "cp-sub"), False, False, 0)
        self.grid = Gtk.Grid(column_spacing=20, row_spacing=20)
        self.grid.set_halign(Gtk.Align.CENTER)
        page.pack_start(self.grid, False, False, 0)
        self.mode_labels = {}          # tile -> the "Starts: ..." label under its name
        self.syncing = False
        self.mode_check = Gtk.CheckButton(label="Start in GameBox instead of the gridscreen  (Guide / F2)")
        self.mode_check.connect("toggled", self.on_mode_toggled)
        self.mode_check.get_style_context().add_class("cp-hint")
        self.mode_check.set_halign(Gtk.Align.CENTER)
        page.pack_start(self.mode_check, False, False, 0)
        self.pick_error = self.label("", "cp-error")
        self.pick_error.set_no_show_all(True)
        page.pack_start(self.pick_error, False, False, 0)
        page.pack_start(self.label("Arrows / d-pad to move  ·  Enter / A to choose  ·  Delete / X removes a profile",
                                   "cp-hint"), False, False, 0)
        # This is the first screen of the session (cosmos-session-apps runs the picker before anything else), so there is
        # no earlier session to cancel back to - restarting or shutting down is the only real way to "exit" it, the same
        # as the login screen offers (cosmos-greeter.py). Goes through kronos (system.power), not a raw systemctl call:
        # the desktop account otherwise has no path to power actions at all, and kronos audits it either way.
        power_row = Gtk.Box(spacing=12)
        power_row.set_halign(Gtk.Align.CENTER)
        restart_button = Gtk.Button(label="(R)estart")
        restart_button.get_style_context().add_class("cp-button")
        restart_button.connect("clicked", lambda _b: self.power("reboot"))
        shutdown_button = Gtk.Button(label="(S)hutdown")
        shutdown_button.get_style_context().add_class("cp-button")
        shutdown_button.connect("clicked", lambda _b: self.power("poweroff"))
        power_row.pack_start(restart_button, False, False, 0)
        power_row.pack_start(shutdown_button, False, False, 0)
        page.pack_start(power_row, False, False, 0)
        return page

    def power(self, verb):
        result = cosmos_kronos.ask("system.power", {"verb": verb})
        if result.get("status") != "ok":
            self.pick_error.set_text(result.get("reason") or "That didn't work.")
            self.pick_error.show()

    def show_pick(self):
        for child in self.grid.get_children():
            self.grid.remove(child)
        self.tile_profiles = {}
        self.mode_labels = {}
        entries = profiles.list_profiles()
        per_row = 4
        first = None
        for index, profile in enumerate(entries):
            tile = self.build_tile(profile)
            self.tile_profiles[tile] = profile
            self.grid.attach(tile, index % per_row, index // per_row, 1, 1)
            first = first or tile
        add = Gtk.Button()
        add.get_style_context().add_class("cp-tile")
        add.set_size_request(TILE_AVATAR + 40, TILE_AVATAR + 60)
        add.add(self.label("+\nAdd profile", "cp-title"))
        add.connect("clicked", lambda _b: self.show_create())
        n = len(entries)
        self.grid.attach(add, n % per_row, n // per_row, 1, 1)
        self.grid.show_all()
        self.pick_error.hide()
        self.stack.set_visible_child_name("pick")
        (first or add).grab_focus()
        self.sync_mode(first)

    def build_tile(self, profile):
        tile = Gtk.Button()
        tile.get_style_context().add_class("cp-tile")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.pack_start(avatar_image(profile.get("avatar") or "", TILE_AVATAR), False, False, 0)
        name = profile["name"] + ("  (PIN)" if profiles.has_pin(profile) else "")
        box.pack_start(Gtk.Label(label=name), False, False, 0)
        mode_label = Gtk.Label(label=self.mode_text(profile.get("start_in")))
        mode_label.get_style_context().add_class("cp-mode")
        box.pack_start(mode_label, False, False, 0)
        self.mode_labels[tile] = mode_label
        tile.add(box)
        tile.connect("clicked", self.on_tile_clicked)
        tile.connect("focus-in-event", lambda t, _e: self.sync_mode(t))
        return tile

    @staticmethod
    def mode_text(start_in):
        """What choosing this profile will open - shown on every tile."""
        return "Starts: GameBox" if start_in == "gamebox" else "Starts: Gridscreen"

    def sync_mode(self, tile):
        profile = self.tile_profiles.get(tile)
        self.syncing = True
        self.mode_check.set_active(bool(profile and profile.get("start_in") == "gamebox"))
        self.syncing = False

    def on_mode_toggled(self, check):
        """The tick box changes what the focused profile starts in - reflected on its tile at once."""
        if self.syncing:
            return
        tile = self.get_focus()
        profile = self.tile_profiles.get(tile)
        if profile is None:
            return
        profile["start_in"] = "gamebox" if check.get_active() else "desktop"
        self.mode_labels[tile].set_text(self.mode_text(profile["start_in"]))
        profiles.save(profile)         # remembered for next time, whichever profile gets picked

    def on_tile_clicked(self, tile):
        profile = self.tile_profiles[tile]
        if profiles.has_pin(profile):
            self.pending = profile
            self.pin_entry.set_text("")
            self.pin_error.hide()
            self.pin_title.set_text(f"PIN for {profile['name']}")
            self.stack.set_visible_child_name("pin")
            self.pin_entry.grab_focus()
        else:
            self.finish(profile)

    def finish(self, profile):
        profiles.set_active(profile["slug"])
        self.chosen = profile
        self.destroy()

    # ---- page: PIN ----------------------------------------------------------------
    def build_pin_page(self):
        page = self.page()
        self.pin_title = self.label("PIN", "cp-title")
        page.pack_start(self.pin_title, False, False, 0)
        self.pin_entry = Gtk.Entry()
        self.pin_entry.set_visibility(False)
        self.pin_entry.set_input_purpose(Gtk.InputPurpose.PIN)
        self.pin_entry.set_placeholder_text("PIN")
        self.pin_entry.set_width_chars(16)
        self.pin_entry.set_alignment(0.5)
        self.pin_entry.get_style_context().add_class("cp-entry")
        self.pin_entry.connect("activate", self.on_pin_submit)
        page.pack_start(self.pin_entry, False, False, 0)
        self.pin_error = self.label("Wrong PIN - try again", "cp-error")
        self.pin_error.set_no_show_all(True)
        page.pack_start(self.pin_error, False, False, 0)
        row = Gtk.Box(spacing=12)
        row.set_halign(Gtk.Align.CENTER)
        row.pack_start(self.button("Back", handler=lambda _b: self.show_pick()), False, False, 0)
        row.pack_start(self.button("OK", "cp-accent", self.on_pin_submit), False, False, 0)
        page.pack_start(row, False, False, 0)
        page.pack_start(self.label("Select / Y opens the on-screen keyboard  ·  Esc / B goes back", "cp-hint"), False, False, 0)
        return page

    def on_pin_submit(self, _w):
        if self.pending and profiles.verify_pin(self.pending, self.pin_entry.get_text()):
            self.finish(self.pending)
        else:
            self.pin_entry.set_text("")
            self.pin_error.show()

    # ---- page: create -----------------------------------------------------------------
    def build_create_page(self):
        page = self.page(14)
        self.create_title = self.label("Create your profile", "cp-title")
        page.pack_start(self.create_title, False, False, 0)
        page.pack_start(self.label("A name and a picture - your favorites, wallpaper and settings are kept "
                                   "under it.", "cp-sub"), False, False, 0)
        self.avatar_grid = Gtk.Grid(column_spacing=12, row_spacing=12)
        self.avatar_grid.set_halign(Gtk.Align.CENTER)
        for index, path in enumerate(avatar_files()):
            b = Gtk.Button()
            b.get_style_context().add_class("cp-avatar")
            b.add(avatar_image(path, PICK_AVATAR))
            b.connect("clicked", lambda _b, p=path: self.select_avatar(p))
            self.avatar_buttons.append((path, b))
            self.avatar_grid.attach(b, index % 4, index // 4, 1, 1)
        page.pack_start(self.avatar_grid, False, False, 0)
        self.name_entry = Gtk.Entry()
        self.name_entry.set_placeholder_text("Your name")
        self.name_entry.set_width_chars(28)
        self.name_entry.get_style_context().add_class("cp-entry")
        self.name_entry.connect("activate", self.on_create)
        page.pack_start(self.name_entry, False, False, 0)
        self.new_pin_entry = Gtk.Entry()
        self.new_pin_entry.set_visibility(False)
        self.new_pin_entry.set_input_purpose(Gtk.InputPurpose.DIGITS)
        self.new_pin_entry.set_placeholder_text("PIN (optional, 4-12 digits) - asked when picking this profile and to unlock")
        self.new_pin_entry.set_width_chars(28)
        self.new_pin_entry.get_style_context().add_class("cp-entry")
        self.new_pin_entry.connect("activate", self.on_create)
        page.pack_start(self.new_pin_entry, False, False, 0)
        self.new_gamebox = Gtk.CheckButton(label="Start in GameBox instead of the gridscreen")
        self.new_gamebox.get_style_context().add_class("cp-hint")
        self.new_gamebox.set_halign(Gtk.Align.CENTER)
        page.pack_start(self.new_gamebox, False, False, 0)
        self.create_error = self.label("", "cp-error")
        self.create_error.set_no_show_all(True)
        page.pack_start(self.create_error, False, False, 0)
        row = Gtk.Box(spacing=12)
        row.set_halign(Gtk.Align.CENTER)
        self.create_back = self.button("Back", handler=lambda _b: self.show_pick())
        row.pack_start(self.create_back, False, False, 0)
        row.pack_start(self.button("Create profile", "cp-accent", self.on_create), False, False, 0)
        page.pack_start(row, False, False, 0)
        return page

    def show_create(self, first_run=False):
        self.create_back.set_visible(not first_run)
        self.create_back.set_no_show_all(first_run)
        self.name_entry.set_text("")
        self.new_pin_entry.set_text("")
        self.new_gamebox.set_active(False)
        self.create_error.hide()
        files = avatar_files()
        self.select_avatar(files[0] if files else None)
        self.stack.set_visible_child_name("create")
        self.create_page.show_all()
        if first_run:
            self.create_back.hide()
        self.name_entry.grab_focus()

    def select_avatar(self, path):
        self.avatar_choice = path
        for candidate, button in self.avatar_buttons:
            ctx = button.get_style_context()
            (ctx.add_class if candidate == path else ctx.remove_class)("cp-avatar-on")

    def on_create(self, _w):
        try:
            profile = profiles.create_profile(self.name_entry.get_text(), avatar=self.avatar_choice,
                                              pin=self.new_pin_entry.get_text().strip() or None,
                                              start_in="gamebox" if self.new_gamebox.get_active() else "desktop")
        except profiles.ProfileError as exc:
            self.create_error.set_text(str(exc))
            self.create_error.show()
            return
        self.pick_after_create(profile)

    def pick_after_create(self, profile):
        self.finish(profile)

    # ---- keys ----------------------------------------------------------------------------
    def on_key(self, _w, event):
        page = self.stack.get_visible_child_name()
        if event.keyval == Gdk.KEY_Escape and page in ("pin", "create") and profiles.list_profiles():
            self.show_pick()
            return True
        if page == "pick":
            if event.keyval in (Gdk.KEY_F2, Gdk.KEY_Help):
                self.mode_check.set_active(not self.mode_check.get_active())
                return True
            if event.keyval == Gdk.KEY_Delete:
                self.confirm_delete()
                return True
        return False

    def on_delete(self, _widget, _event):
        # A window-close request - Alt+X, chiefly - reaches here as a "delete-event" before GTK
        # would destroy the window and quit. Refuse it on the same terms Escape already refuses
        # to back out: on first run (or every profile just deleted), the create page has nothing
        # behind it to fall back to, so closing here would strand the session with no way to
        # ever sign in. Returning True is what tells GTK not to proceed with the close.
        page = self.stack.get_visible_child_name()
        return page in ("pin", "create") and not profiles.list_profiles()

    def confirm_delete(self):
        tile = self.get_focus()
        profile = self.tile_profiles.get(tile)
        if profile is None:
            return
        if getattr(self, "delete_armed", None) == profile["slug"]:
            profiles.delete_profile(profile["slug"])
            self.delete_armed = None
            if profiles.list_profiles():
                self.show_pick()
            else:
                self.show_create(first_run=True)
        else:
            self.delete_armed = profile["slug"]
            self.pick_error.set_text(f"Press Delete / X again to remove \"{profile['name']}\" and all its settings")
            self.pick_error.show()
            GLib.timeout_add_seconds(4, self.disarm)

    def disarm(self):
        self.delete_armed = None
        self.pick_error.hide()
        return False


def print_environment():
    """`export` lines for the session script."""
    env = dict(profiles.session_environment())
    profile = profiles.active_profile()
    env["COSMOS_START_IN"] = (profile or {}).get("start_in", "desktop")
    for key, value in env.items():
        print(f"export {key}={shlex.quote(value)}")


def main():
    if "--env" in sys.argv[1:]:
        print_environment()
        return 0
    provider = Gtk.CssProvider()
    provider.load_from_data(cosmos_display.scale_css(CSS).encode())
    Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_USER)
    win = Picker()
    win.connect("destroy", Gtk.main_quit)
    win.show_all()
    if not profiles.list_profiles():
        win.create_back.hide()      # first run: nothing to go back to
    Gtk.main()
    return 0 if win.chosen else 1


if __name__ == "__main__":
    raise SystemExit(main())
