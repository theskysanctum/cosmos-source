#!/usr/bin/env python3
"""CosmOS's own wallpaper picker - direct requirement ("I want users to be able to
set their own wallpaper in the desktop"), matching cosmos-bar's/cosmos-vm-manager's
own dark theme (same BG/FG/ACCENT/BORDER CSS as everywhere else in this project)
instead of a generic GTK file-chooser-only tool.

The wallpaper is drawn by cosmos-launcher, which is the desktop's permanent home
screen (a background layer surface behind every window). This app only records the
choice - one path in the active profile's folder (cosmos_profiles.profile_path) - and the launcher notices the file
change and repaints; the same file is what makes it survive a reboot/relogin. (It
used to drive feh, which sets an X11 root-window background; there is no root window
in a Wayland session.)
"""
import os

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk  # noqa: E402
import cosmos_display  # noqa: E402
import cosmos_settings_link  # noqa: E402

import cosmos_profiles

BG = "#0a0e16"
FG = "#e8ecf4"
ACCENT = "#ff0090"
BORDER = "#2a3a56"
DEFAULT_BACKGROUND = "/usr/share/backgrounds/cosmos-galaxy.jpg"
PREVIEW_SIZE = cosmos_display.px(480)

CSS = f"""
window.cosmos-wallpaper {{
    background-color: {BG};
}}
.cosmos-wallpaper-label {{
    color: {FG};
}}
.cosmos-wallpaper-status {{
    color: {FG};
}}
.cosmos-wallpaper-preview {{
    background-color: #000000;
    border: 1px solid {BORDER};
    border-radius: 6px;
}}
.cosmos-wallpaper-button {{
    background-color: {BG};
    background-image: none;
    color: {FG};
    border: 1px solid {BORDER};
    box-shadow: none;
}}
.cosmos-wallpaper-button:hover {{
    background-color: {ACCENT};
    background-image: none;
    color: {BG};
    border-color: {ACCENT};
}}
.cosmos-wallpaper-button-accent {{
    background-color: {ACCENT};
    background-image: none;
    color: {BG};
    border: 1px solid {ACCENT};
    box-shadow: none;
}}
.cosmos-wallpaper-button-accent:hover {{
    background-color: {FG};
    background-image: none;
    border-color: {FG};
}}
"""


WALLPAPER_FILE = cosmos_profiles.profile_path("wallpaper")


def current_wallpaper_path():
    """The path recorded by the last Apply, or the built-in default."""
    try:
        with open(WALLPAPER_FILE, encoding="utf-8") as f:
            path = f.read().strip()
    except OSError:
        return DEFAULT_BACKGROUND
    return path if path and os.path.isfile(path) else DEFAULT_BACKGROUND


class WallpaperPicker(Gtk.Window):
    def __init__(self):
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        self.set_title("Set Wallpaper")
        cosmos_display.size_window(self, 560, 440)
        self.set_position(Gtk.WindowPosition.CENTER)
        self.get_style_context().add_class("cosmos-wallpaper")

        self.selected_path = current_wallpaper_path()

        root_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        root_box.set_border_width(20)
        self.add(root_box)

        title = Gtk.Label(label="Set Wallpaper")
        title.get_style_context().add_class("cosmos-wallpaper-label")
        title.set_xalign(0)
        root_box.pack_start(title, False, False, 0)

        self.preview = Gtk.Image()
        self.preview.get_style_context().add_class("cosmos-wallpaper-preview")
        self.preview.set_size_request(PREVIEW_SIZE, PREVIEW_SIZE * 9 // 16)
        root_box.pack_start(self.preview, True, True, 0)

        button_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        root_box.pack_start(button_row, False, False, 0)

        browse_button = Gtk.Button(label="Choose Image...")
        browse_button.get_style_context().add_class("cosmos-wallpaper-button")
        browse_button.connect("clicked", self.on_browse_clicked)
        button_row.pack_start(browse_button, False, False, 0)

        apply_button = Gtk.Button(label="Apply")
        apply_button.get_style_context().add_class("cosmos-wallpaper-button-accent")
        apply_button.connect("clicked", self.on_apply_clicked)
        button_row.pack_end(apply_button, False, False, 0)

        self.status_label = Gtk.Label(label="")
        self.status_label.get_style_context().add_class("cosmos-wallpaper-status")
        self.status_label.set_xalign(0)
        root_box.pack_start(self.status_label, False, False, 0)

        self.connect("destroy", Gtk.main_quit)
        self.load_preview(self.selected_path)

    def load_preview(self, path):
        try:
            cosmos_display.set_image_from_file(self.preview, path, PREVIEW_SIZE, PREVIEW_SIZE * 9 // 16)
        except GLib.Error:
            pass

    def on_browse_clicked(self, _widget):
        dialog = Gtk.FileChooserDialog(
            title="Choose an image",
            parent=self,
            action=Gtk.FileChooserAction.OPEN,
        )
        dialog.add_buttons(
            Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL, Gtk.STOCK_OPEN, Gtk.ResponseType.OK
        )
        image_filter = Gtk.FileFilter()
        image_filter.set_name("Images")
        image_filter.add_mime_type("image/png")
        image_filter.add_mime_type("image/jpeg")
        image_filter.add_mime_type("image/bmp")
        image_filter.add_mime_type("image/webp")
        dialog.add_filter(image_filter)
        dialog.set_filename(self.selected_path)

        if dialog.run() == Gtk.ResponseType.OK:
            self.selected_path = dialog.get_filename()
            self.status_label.set_text("")
            self.load_preview(self.selected_path)
        dialog.destroy()

    def on_apply_clicked(self, _widget):
        try:
            os.makedirs(os.path.dirname(WALLPAPER_FILE), exist_ok=True)
            with open(WALLPAPER_FILE, "w", encoding="utf-8") as f:
                f.write(self.selected_path + "\n")
            self.status_label.set_text("Wallpaper applied.")
        except OSError as exc:
            self.status_label.set_text(f"Failed to apply wallpaper: {exc}")


def main():
    cosmos_settings_link.redirect("wallpaper")
    provider = Gtk.CssProvider()
    provider.load_from_data(cosmos_display.scale_css(CSS).encode())
    Gtk.StyleContext.add_provider_for_screen(
        Gdk.Screen.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_USER
    )
    win = WallpaperPicker()
    win.show_all()
    Gtk.main()


if __name__ == "__main__":
    main()
