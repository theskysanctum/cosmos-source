#!/usr/bin/env python3
"""CosmOS's own application launcher - direct requirement
("let's replace rofi with a full screen alphabetic grid"), replacing
`rofi -show drun` (cosmos-bar.py's own bottom-bar "Apps" button used to
launch this directly) with a real GTK3 app matching the rest of this
project's own dark theme instead of rofi's separate (non-GTK) theming
engine.

Three real sections, one shared full-screen window, switched via the
same button-row-of-tabs treatment CosmOS Shop already uses for its own
three sources (see config/scripts/cosmos-shop.py's own docstring) -
direct requirement ("combine apps, web apps, and development apps into the
bottom left apps icon, and just separate the grids by tabs at the top",
"this needs to be easy enough for my wife to use"). Three separate
full-screen grids toggled from three different bar buttons was real,
measurable complexity for no real benefit once CosmOS Shop had already
proven the tabbed pattern works - one launch point, one Escape, one
consistent set of keyboard shortcuts, matching how one person here
("the only thing my wife should have to worry about is the games,
emulators, etc") never needed the Development/Web-Apps split to be visible
at the bar level at all, just reachable.

  - Apps: every installed app (Gio.AppInfo.get_all() + app.should_show(),
    the same real GLib API every proper application menu uses - already
    handles NoDisplay/Hidden/OnlyShowIn/TryExec correctly) except
    whatever DEVELOPMENT_DESKTOP_IDS excludes (see that constant's own
    comment).
  - Web: Brave's own installed-as-app PWA shortcuts (the same
    brave-*.desktop convention the Containerfile's own default-web-app
    block scans for/generates), plus a "+ Add Web App" tile and a
    right-click dialog to rename it, change its icon (a PNG of exactly
    128 x 128 only) or remove it (confirmed via a real Yes/No dialog, never
    immediate - a real, live accidental deletion this avoids, see
    confirm_remove_dialog()'s own docstring); see cosmos_webapp_edit.py.
  - Development: everything developer/programming/design/networking/
    administrative related, organized into labeled sections (direct
    request: "organize everything under headers in Development so everything
    is easier to find") - see DEVELOPMENT_SECTIONS.

Every tile everywhere shares one more right-click option regardless of
section - Favorite/Unfavorite, the shared cosmos_favorites.py storage
cosmos-bar.py's own left-bar Favorite Apps list reads back.

Three real, confirmed-live bugs fixed here that all three of this
file's former separate sources (cosmos-launcher.py, cosmos-webapps.py,
cosmos-grid.py) shared independently, found while hardening
CosmOS Shop against the same bug classes and fixed here for good while
merging rather than carried forward three times:
  - label.set_ellipsize(True) silently coerces to
    Pango.EllipsizeMode.START (True == 1, the same integer value) -
    every overflowing name truncated from the front instead of the end.
  - GtkFlowBox's own real default max-children-per-line is 7, never
    overridden anywhere in any of the three former files - wastes most
    of any screen wider than ~1150px as dead space to the right of a
    narrow 7-wide column (see FLOWBOX_MAX_COLUMNS's own comment).
  - Activating a tile re-scanned every section for "whichever flowbox
    still has anything selected" instead of using the exact
    (flowbox, child) the activating signal already hands over - since
    each section is its own separate Gtk.FlowBox with its own
    independent SelectionMode.SINGLE, nothing ever cleared a section's
    selection when a different section's tile got clicked, so two
    sections could each show a highlighted tile at once and activating
    the wrong one could fire the FIRST stale-selected entry instead of
    the one actually clicked (see on_selection_changed()'s own
    docstring - this file's own Development tab has several sections at
    once, the same shape CosmOS Shop's own categories already had this
    bug in)."""
import glob
import logging
import os
import re
from urllib.parse import urlparse

import gi
import requests

log = logging.getLogger("cosmos-launcher")

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GtkLayerShell", "0.1")
from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Gtk, GtkLayerShell, Pango  # noqa: E402
import cosmos_display  # noqa: E402

import cosmos_favorites
import cosmos_webapp_edit
import cosmos_pad_native
import cosmos_profiles
import cosmos_session_state
import cosmos_wm_ipc

BG = "#0a0e16"
FG = "#e8ecf4"
ACCENT = "#ff0090"
BORDER = "#2a3a56"
ICON_SIZE = cosmos_display.px(48)

# Written by cosmos-wallpaper; the desktop background behind the launcher's tiles.
WALLPAPER_FILE = cosmos_profiles.profile_path("wallpaper")
DEFAULT_WALLPAPER = "/usr/share/backgrounds/cosmos-galaxy.jpg"

# GtkFlowBox's own real default max-children-per-line is 7 (confirmed
# live) - see cosmos-shop.py's own FLOWBOX_MAX_COLUMNS comment for the
# full reasoning (same fix, same constant value, applied here for the
# same reason). A generous constant well past anything a real
# monitor/tile-width combination could ever actually fill - GTK still
# only lays out as many columns as actually fit.
FLOWBOX_MAX_COLUMNS = 32

# Real per-section grouping for the Development tab (direct request:
# "organize everything under headers in Development so everything is easier
# to find"). Exact ids confirmed live via the actual built image's own
# /usr/share/applications/ (`sudo podman run --rm
# localhost/cosmos:latest ls /usr/share/applications/`, and for
# the sound-and-video/robotics-suite dnf groups specifically, `rpm -ql`
# against every real group member to find which ones actually own a
# .desktop file at all - most of both groups are CLI-only with nothing
# to move), not guessed - several don't match their package name the
# way you'd expect (`dbeaver-ce.desktop`, not `dbeaver.desktop`;
# `sublime_text.desktop`, underscore not hyphen; Godot/Tiled/Qt
# Creator/Krita/Inkscape/Darktable/MuseScore/Hydrogen/Qtractor/
# QjackCtl/OBS Studio all ship reverse-DNS `org.*`/`com.*` ids).
#
# The single source of truth for "what counts as Development" now that both
# the Apps tab's own exclusion set and the Development tab's own sections
# live in the same file - DEVELOPMENT_DESKTOP_IDS below is derived from this
# directly instead of hand-maintaining a second, separately-drifting
# flat copy the way the former cosmos-launcher.py/cosmos-grid.py
# split needed to.
DEVELOPMENT_SECTIONS = [
    ("CosmOS Tools", [
        "astronar-studio.desktop",
        "theme-studio.desktop",
        "gamebox-addon-studio.desktop",
        "gamebox-fetch-assets.desktop",
    ]),
    ("IDEs & Programming", [
        "cosmos-webtop.desktop",          # a full development desktop in a container (always available)
        # The Developer Tools Expansion Pack (Flatpaks, installed from the Shop) - listed only once installed.
        "com.google.AndroidStudio.desktop",
        "com.jetbrains.IntelliJ-IDEA-Community.desktop",
        "org.eclipse.Java.desktop",
        "io.dbeaver.DBeaverCommunity.desktop",
        "com.visualstudio.code.desktop",
        "com.sublimehq.SublimeText.desktop",
        "git-gui.desktop",
        "org.gnome.gitg.desktop",
        "io.neovim.nvim.desktop",
    ]),
    ("Game Development", [
        "org.godotengine.Godot.desktop",
        "org.love2d.love2d.desktop",
        "org.mapeditor.Tiled.desktop",
    ]),
    ("Design & Art", [
        "org.gimp.GIMP.desktop",
        "org.kde.krita.desktop",
        "org.inkscape.Inkscape.desktop",
        "org.darktable.Darktable.desktop",
        "org.blender.Blender.desktop",
    ]),
    ("Audio Production", [
        "org.audacityteam.Audacity.desktop",
        "io.lmms.LMMS.desktop",
        "org.musescore.MuseScore.desktop",
        "org.hydrogenmusic.Hydrogen.desktop",
        "org.rncbc.qtractor.desktop",
        "studio.kx.carla.desktop",
        "calf.desktop",
    ]),
    ("Video Production", [
        "org.kde.kdenlive.desktop",
        "fr.handbrake.ghb.desktop",
        "com.obsproject.Studio.desktop",
    ]),
    ("Robotics", [
        "rcssmonitor.desktop",
        "rcssserver.desktop",
        "rcssserver3d.desktop",
    ]),
    ("System & Admin", [
        "cosmos-vm-manager.desktop",
        "blueman-manager.desktop",
        "org.pulseaudio.pavucontrol.desktop",
        "nvidia-settings.desktop",
        "qt5ct.desktop",
        "qt6ct.desktop",
    ]),
    ("Windows Compatibility (Wine)", [
        "wine-notepad.desktop",
        "wine-regedit.desktop",
        "wine-winecfg.desktop",
        "wine-winefile.desktop",
        "wine-winhelp.desktop",
        "wine-oleview.desktop",
        "wine-uninstaller.desktop",
        "wine-wordpad.desktop",
        "wine-wineboot.desktop",
    ]),
]

# Flattened from DEVELOPMENT_SECTIONS above (see that constant's own
# comment) - direct requirement ("the only thing my wife should have to
# worry about is the games, emulators, etc... no gimp, robotics,
# art... all other suites need to be under studio"): everything
# developer/programming/design/networking/administrative related lives
# in the Development tab instead, so the Apps tab excludes those same ids
# rather than showing every one of them a second time right alongside
# the games. cosmos-wallpaper.desktop is deliberately NOT here - direct
# request ("changing the wallpaper belongs in apps not studio").
DEVELOPMENT_DESKTOP_IDS = {desktop_id for _title, ids in DEVELOPMENT_SECTIONS for desktop_id in ids}


def shows_in_grid(app):
    """An installed app the grid should list: visible, and not one that needs a terminal -
    CosmOS has no terminal emulator, so a Terminal=true entry (htop, vim, ...) could not
    start anyway and is left out rather than offered and broken."""
    if not app.should_show():
        return False
    return not (isinstance(app, Gio.DesktopAppInfo) and app.get_boolean("Terminal"))


# Only the user's own local applications dir - NOT /usr/share/
# applications too (confirmed live this was a real bug shared with the
# now-removed config/rofi/brave-apps.sh's own original app_dirs list:
# Brave's RPM installs its own main-browser launcher at
# /usr/share/applications/brave-browser.desktop, "Name=Brave Web
# Browser", which also matches the brave-*.desktop glob and would show
# up disguised as an installed web app. Real "Install as app" PWA
# shortcuts are a per-user action and can only ever land under the
# user's own ~/.local/share/applications - a per-user browser action has
# no permission to write into /usr/share at all - so dropping that
# directory from the scan fixes this cleanly rather than trying to
# filter the main browser entry out by name).
BRAVE_APP_DIRS = [os.path.expanduser("~/.local/share/applications")]

CSS = f"""
window.cosmos-launcher {{
    background-color: {BG};
}}
.cosmos-launcher-query {{
    background-color: {BG};
    color: {FG};
    font-weight: bold;
    font-size: 20px;
    border: 2px solid {BORDER};
    border-radius: 6px;
    padding: 10px 20px;
}}
.cosmos-launcher-hint {{
    color: {FG};
    opacity: 0.6;
}}
.cosmos-launcher-section-header {{
    color: {ACCENT};
    font-weight: bold;
    font-size: 14px;
}}
flowboxchild {{
    padding: 12px;
    border-radius: 6px;
}}
flowboxchild:selected {{
    background-color: {ACCENT};
}}
.cosmos-launcher-tile-label {{
    color: {FG};
    font-weight: bold;
}}
flowboxchild:selected .cosmos-launcher-tile-label {{
    color: {BG};
}}
.cosmos-launcher-tile-pinned {{
    border: 2px solid {ACCENT};
}}
.cosmos-launcher-menu {{
    background-color: {BG};
    background-image: none;
    border: 1px solid {BORDER};
}}
.cosmos-launcher-menu menuitem {{
    background-color: {BG};
    background-image: none;
    color: {FG};
}}
.cosmos-launcher-menu menuitem:hover {{
    background-color: {ACCENT};
    background-image: none;
    color: {BG};
}}
.cosmos-launcher-entry {{
    background-color: {BG};
    background-image: none;
    color: {FG};
    caret-color: {FG};
    border: 1px solid {BORDER};
    box-shadow: none;
    font-weight: bold;
    padding: 10px 12px;
}}
.cosmos-launcher-entry:focus {{
    border-color: {ACCENT};
    box-shadow: none;
}}
.cosmos-launcher-button {{
    background-color: {BG};
    background-image: none;
    color: {FG};
    border: 1px solid {BORDER};
    box-shadow: none;
    font-weight: bold;
    padding: 8px 14px;
}}
.cosmos-launcher-error {{
    color: {ACCENT};
    font-weight: bold;
}}
.cosmos-launcher-button-accent {{
    background-color: {ACCENT};
    background-image: none;
    color: {BG};
    border: 1px solid {ACCENT};
    box-shadow: none;
    font-weight: bold;
    padding: 8px 14px;
}}
.cosmos-launcher-section-button {{
    background-color: {BG};
    background-image: none;
    color: {FG};
    border: 1px solid {BORDER};
    box-shadow: none;
    font-weight: bold;
    padding: 10px 20px;
}}
.cosmos-launcher-section-button-active {{
    background-color: {ACCENT};
    background-image: none;
    color: {BG};
    border: 1px solid {ACCENT};
    box-shadow: none;
    font-weight: bold;
    padding: 10px 20px;
}}
"""


# ======================================================================
# Web Apps - shared helpers (discovery, add, real favicon fetch)
# ======================================================================

def discover_web_apps():
    """Same brave-*.desktop convention cosmos-bar.py's own (now-removed)
    discover_brave_apps() used - Gio.DesktopAppInfo, not a hand-rolled
    Name=/Exec= parse, and deliberately ignores each file's own
    NoDisplay=true (would otherwise be should_show() == False) - that
    flag hides these specifically from the Apps tab's own
    Gio.AppInfo.get_all() scan, not from here, which exists specifically
    to show them."""
    apps = []
    for app_dir in BRAVE_APP_DIRS:
        if not os.path.isdir(app_dir):
            continue
        for path in sorted(glob.glob(os.path.join(app_dir, "brave-*.desktop"))):
            # Same real TypeError-instead-of-None behavior as
            # Gio.DesktopAppInfo.new() elsewhere in this file (see
            # populate_development()'s own comment) - a match from the glob
            # above only proves the file exists, not that its contents
            # are well-formed, so this can still fail on a
            # truncated/corrupt .desktop file.
            try:
                app = Gio.DesktopAppInfo.new_from_filename(path)
            except TypeError:
                app = None
            if app is not None:
                apps.append((app, path))
    return apps


def slugify(name):
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "app"


def fetch_favicon_icon_name(url, slug):
    """Real favicon, not the generic Brave lion for every tile - direct
    requirement ("replace the Brave Icon by the Favicons... so they are
    distinguishable from each other"). Google's public favicon-
    resolution service (the same one the Containerfile's own build-time
    equivalent uses for the 9 default web apps) resolves whatever a
    site's own actual favicon is. Cached into
    ~/.local/share/icons/hicolor/128x128/apps/, the real per-user
    hicolor icon theme location - returning a bare icon NAME (resolved
    via the standard icon theme search path against THIS user's own
    $HOME) rather than baking an absolute path into the .desktop file.
    Falls back to "brave-browser" on any failure (offline, DNS, timeout,
    empty response) rather than leaving a .desktop file pointing at an
    icon that doesn't actually exist."""
    icon_dir = os.path.expanduser("~/.local/share/icons/hicolor/128x128/apps")
    icon_path = os.path.join(icon_dir, f"brave-{slug}.png")
    domain = urlparse(url).netloc or url
    try:
        response = requests.get(
            "https://www.google.com/s2/favicons",
            params={"domain": domain, "sz": "128"},
            timeout=5,
        )
        response.raise_for_status()
        if not response.content:
            raise ValueError("empty favicon response")
        os.makedirs(icon_dir, exist_ok=True)
        with open(icon_path, "wb") as f:
            f.write(response.content)
        return f"brave-{slug}"
    except (requests.RequestException, OSError, ValueError):
        return "brave-browser"


def add_web_app(name, url):
    """Same .desktop shape the Containerfile's own build-time default
    web apps use - both write into the exact same directory, so either
    one sees whatever the other just added the next time this tab is
    opened."""
    apps_dir = BRAVE_APP_DIRS[0]
    os.makedirs(apps_dir, exist_ok=True)
    slug = slugify(name)
    path = os.path.join(apps_dir, f"brave-{slug}.desktop")
    icon_name = fetch_favicon_icon_name(url, slug)
    content = (
        "[Desktop Entry]\n"
        "Type=Application\n"
        f"Name={name}\n"
        f"Exec=brave-browser --app={url} --profile-directory=Default\n"
        f"Icon={icon_name}\n"
        "Categories=Network;\n"
        "Terminal=false\n"
        "NoDisplay=true\n"
    )
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


# ======================================================================
# GTK UI
# ======================================================================

TABS = ["apps", "webapps", "development"]
TAB_LABELS = {"apps": "Apps", "webapps": "Web", "development": "Dev"}


class CosmosLauncher(Gtk.Window):
    def __init__(self):
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        self.set_title("cosmos-launcher")
        self.set_decorated(False)
        self.get_style_context().add_class("cosmos-launcher")
        # The home screen: a permanent BACKGROUND layer surface filling the
        # space between the bars, behind every window. It never closes - launching
        # something just puts that window on top of it, and "Apps" (or closing
        # the last window) brings it back into view. cosmos-wm gives it keyboard
        # focus whenever no window has it.
        GtkLayerShell.init_for_window(self)
        GtkLayerShell.set_namespace(self, "cosmos-launcher")
        GtkLayerShell.set_layer(self, GtkLayerShell.Layer.BACKGROUND)
        for edge in (GtkLayerShell.Edge.TOP, GtkLayerShell.Edge.BOTTOM, GtkLayerShell.Edge.LEFT, GtkLayerShell.Edge.RIGHT):
            GtkLayerShell.set_anchor(self, edge, True)
        GtkLayerShell.set_exclusive_zone(self, 0)   # inside the bars, not under them
        GtkLayerShell.set_keyboard_mode(self, GtkLayerShell.KeyboardMode.ON_DEMAND)
        self.connect("notify::is-active", self.on_active_changed)
        self.connect("destroy", lambda _w: (cosmos_session_state.release_pad(), Gtk.main_quit()))

        self.query = ""
        self.active_tab = "apps"
        # tab -> [(header_label_or_None, flowbox), ...] - a single
        # (None, flowbox) pair for Apps/Web Apps (one flat, unheadered
        # grid each - see build_flowbox()'s own comment), several
        # (title, flowbox) pairs for Development's own real sections.
        self.sections = {key: [] for key in TABS}

        root_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        root_box.set_border_width(32)
        self.add(root_box)

        tab_button_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        tab_button_row.set_halign(Gtk.Align.CENTER)
        root_box.pack_start(tab_button_row, False, False, 0)

        self.tab_buttons = {}
        for key in TABS:
            button = Gtk.Button(label=TAB_LABELS[key])
            button.get_style_context().add_class("cosmos-launcher-section-button")
            button.connect("clicked", self.on_tab_button_clicked, key)
            tab_button_row.pack_start(button, False, False, 0)
            self.tab_buttons[key] = button

        self.query_label = Gtk.Label(label="Type to search...")
        self.query_label.get_style_context().add_class("cosmos-launcher-query")
        self.query_label.set_halign(Gtk.Align.CENTER)
        root_box.pack_start(self.query_label, False, False, 0)

        self.stack = Gtk.Stack()
        root_box.pack_start(self.stack, True, True, 0)

        for key in TABS:
            self.stack.add_named(self.build_tab_page(key), key)

        self.hint_label = Gtk.Label()
        self.hint_label.get_style_context().add_class("cosmos-launcher-hint")
        self.hint_label.set_halign(Gtk.Align.CENTER)
        root_box.pack_start(self.hint_label, False, False, 0)

        self.connect("key-press-event", self.on_key_press)

        # Computed before populate_apps() specifically so it can exclude
        # by name, not just by id - see that method's own comment on why
        # id alone isn't enough.
        self.development_names = self.compute_development_names()
        self.populate_apps()
        self.populate_webapps()
        self.populate_development()
        self.set_active_tab("apps")

        # Controller-native navigation. cosmos-input (the system-wide
        # pad-to-keyboard layer) stands down while this window holds the claim,
        # so nothing is handled twice.
        self.osk = OskController()
        self.pads = cosmos_pad_native.NativePads(
            LauncherPadEmitter(self), self.pad_enabled, on_select=self.osk.toggle, on_r2=self.back_to_last_window
        )
        self.pads.start()

        # Wallpaper: whatever cosmos-wallpaper last recorded, under a dark veil so
        # the tiles stay readable. Re-read whenever the file changes.
        self.wallpaper_provider = Gtk.CssProvider()
        self.get_style_context().add_provider(self.wallpaper_provider, Gtk.STYLE_PROVIDER_PRIORITY_USER + 1)
        self.wallpaper_mtime = None
        GLib.timeout_add_seconds(2, self.refresh_wallpaper)
        self.refresh_wallpaper()
        # Hidden for as long as GameBox runs (same request file as the bar).
        cosmos_session_state.watch_hide_request(self.on_hide_request)

    # -- home-screen lifecycle / controller ---------------------------

    def refresh_wallpaper(self):
        try:
            mtime = os.stat(WALLPAPER_FILE).st_mtime
        except OSError:
            mtime = None
        if mtime == self.wallpaper_mtime and self.wallpaper_mtime is not None:
            return True
        self.wallpaper_mtime = mtime
        path = DEFAULT_WALLPAPER
        try:
            with open(WALLPAPER_FILE, encoding="utf-8") as fh:
                chosen = fh.read().strip()
            if chosen and os.path.isfile(chosen):
                path = chosen
        except OSError:
            pass
        if not os.path.isfile(path):
            self.wallpaper_provider.load_from_data(b"")
            return True
        css = (f'window.cosmos-launcher {{ background-image: linear-gradient(rgba(10, 14, 22, 0.62), '
               f'rgba(10, 14, 22, 0.62)), url("{path}"); background-size: cover; background-position: center; }}')
        self.wallpaper_provider.load_from_data(css.encode())
        return True

    def on_hide_request(self, hidden):
        if hidden:
            self.hide()
        else:
            self.show_all()

    def on_active_changed(self, *_args):
        if self.props.is_active:
            cosmos_session_state.claim_pad()
        else:
            cosmos_session_state.release_pad()

    def pad_enabled(self):
        return self.props.is_active and self.get_visible() and not self.osk.open

    def back_to_last_window(self):
        """R2: return to the most recently used window (the home screen's stand-in
        for the Alt+Tab chord cosmos-input offers when a window has focus)."""
        windows = cosmos_wm_ipc.windows()
        if windows:
            cosmos_wm_ipc.activate(windows[0][0])

    def reset_view(self):
        """Back to a clean home screen: no search text, Apps tab."""
        self.query = ""
        self.set_active_tab("apps")

    def clear_search(self):
        """Empty the search box without changing the tab or moving the selection when nothing was typed."""
        if self.query:
            self.update_query("")
        return False

    def selected_child(self):
        for _header, flowbox in self.sections.get(self.active_tab, []):
            selected = flowbox.get_selected_children()
            if selected:
                return selected[0]
        return None

    def move_selection(self, direction):
        """2-D navigation across every visible tile on the tab, all sections
        included: the nearest tile in the pressed direction, weighting sideways
        drift double so up/down stays in its column."""
        current = self.selected_child()
        if current is None:
            self.select_first_visible()
            return

        def center(child):
            pos = child.translate_coordinates(self.stack, 0, 0)
            alloc = child.get_allocation()
            if pos is None:
                return None
            return (pos[0] + alloc.width / 2, pos[1] + alloc.height / 2)

        here = center(current)
        if here is None:
            return
        best, best_score = None, None
        for _header, flowbox in self.sections.get(self.active_tab, []):
            if not flowbox.get_visible():
                continue
            for child in flowbox.get_children():
                if child is current or not child.get_child_visible():
                    continue
                there = center(child)
                if there is None:
                    continue
                dx, dy = there[0] - here[0], there[1] - here[1]
                along, across = {
                    "up": (-dy, dx), "down": (dy, dx), "left": (-dx, dy), "right": (dx, dy),
                }[direction]
                if along < 4:
                    continue
                score = along + 2 * abs(across)
                if best_score is None or score < best_score:
                    best, best_score = child, score
        if best is not None:
            flowbox = best.get_parent()
            flowbox.select_child(best)
            best.grab_focus()

    def switch_tab(self, step):
        keys = list(TABS)
        self.set_active_tab(keys[(keys.index(self.active_tab) + step) % len(keys)])

    def toggle_selected_favorite(self):
        child = self.selected_child()
        if child is None:
            return
        flowbox = child.get_parent()
        _name, app, _extra, tile = flowbox._local_entries[child.get_index()]
        if app is not None:
            self.toggle_favorite_tile(app, tile.get_child())

    def toggle_favorite_tile(self, app_info, box):
        now_pinned = cosmos_favorites.toggle_favorite(app_info.get_id())
        style = box.get_style_context()
        if now_pinned:
            style.add_class("cosmos-launcher-tile-pinned")
        else:
            style.remove_class("cosmos-launcher-tile-pinned")

    def on_pad_key(self, keycode):
        """What a controller button does here (cosmos-input's own mapping:
        A=Enter B=Esc X=Delete Y=Tab L1/R1=[ ])."""
        ecodes = self.pads.ecodes
        directions = {ecodes.KEY_UP: "up", ecodes.KEY_DOWN: "down",
                      ecodes.KEY_LEFT: "left", ecodes.KEY_RIGHT: "right"}
        if keycode in directions:
            self.move_selection(directions[keycode])
        elif keycode == ecodes.KEY_ENTER:
            self.launch_selected()
        elif keycode == ecodes.KEY_ESC:
            self.reset_view()
        elif keycode == ecodes.KEY_DELETE:
            self.toggle_selected_favorite()
        elif keycode == ecodes.KEY_LEFTBRACE:
            self.switch_tab(-1)
        elif keycode == ecodes.KEY_RIGHTBRACE:
            self.switch_tab(1)
        elif keycode == ecodes.KEY_TAB:
            self.osk.toggle()   # Y: type to search

    # -- page/section construction ---------------------------------

    def build_tab_page(self, key):
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        page.pack_start(scroller, True, True, 0)
        sections_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=20)
        scroller.add(sections_box)
        page._sections_box = sections_box
        return page

    def build_flowbox(self, tab):
        """One shared flowbox constructor for every section on every
        tab - real, confirmed-live max-children-per-line fix applied
        here for all three (see FLOWBOX_MAX_COLUMNS's own comment),
        plus the same cross-section selected-children-changed sync
        CosmOS Shop's own categories needed (see
        on_selection_changed()'s own docstring)."""
        flowbox = Gtk.FlowBox()
        flowbox.set_valign(Gtk.Align.START)
        flowbox.set_halign(Gtk.Align.START)
        flowbox.set_selection_mode(Gtk.SelectionMode.SINGLE)
        flowbox.set_homogeneous(True)
        flowbox.set_row_spacing(4)
        flowbox.set_column_spacing(4)
        flowbox.set_max_children_per_line(FLOWBOX_MAX_COLUMNS)
        flowbox._local_entries = []
        flowbox.set_filter_func(self.make_filter_func(flowbox))
        flowbox.connect("child-activated", self.on_child_activated, tab)
        flowbox.connect("selected-children-changed", self.on_selection_changed, tab)
        return flowbox

    def add_section(self, tab, title, flowbox):
        page = self.stack.get_child_by_name(tab)
        sections_box = page._sections_box
        header = None
        if title is not None:
            header = Gtk.Label(label=title)
            header.get_style_context().add_class("cosmos-launcher-section-header")
            header.set_halign(Gtk.Align.START)
            sections_box.pack_start(header, False, False, 0)
        sections_box.pack_start(flowbox, False, False, 0)
        self.sections[tab].append((header, flowbox))

    # -- Apps tab ---------------------------------------------------

    def compute_development_names(self):
        """Real display names for every DEVELOPMENT_SECTIONS id that
        resolves on this system - direct requirement ("anything in
        Development should NEVER be under Apps"). DEVELOPMENT_DESKTOP_IDS alone
        (exact .desktop id match) isn't enough to guarantee that: real,
        confirmed-live gap found checking this - Flathub's own real app
        ids for several DEVELOPMENT_SECTIONS entries are completely
        different strings from their native DNF package's id (Blender's
        native package ships `blender.desktop`; Flathub's real Blender
        is `org.blender.Blender`, confirmed live against Flathub's own
        appstream catalog - same real mismatch for GIMP,
        `org.gimp.GIMP` vs `gimp.desktop`). Installing one of these
        specific apps via CosmOS Shop's own Flatpak section instead of
        (or alongside) its DNF package would register a second
        Gio.AppInfo under that different id, which id-only exclusion
        would miss entirely and show in Apps despite the exact same
        real application already being designated Development-only. Matching
        by display name too closes that gap regardless of which package
        manager - DNF, Flatpak, or an AppImage via CosmOS Shop's AM
        section - actually provided it."""
        names = set()
        for _title, desktop_ids in DEVELOPMENT_SECTIONS:
            for desktop_id in desktop_ids:
                try:
                    app = Gio.DesktopAppInfo.new(desktop_id)
                except TypeError:
                    app = None
                if app is not None:
                    names.add(app.get_display_name().lower())
        return names

    def populate_apps(self):
        flowbox = self.build_flowbox("apps")
        apps = [
            app
            for app in Gio.AppInfo.get_all()
            if shows_in_grid(app)
            and app.get_id() not in DEVELOPMENT_DESKTOP_IDS
            and app.get_display_name().lower() not in self.development_names
        ]
        apps.sort(key=lambda app: app.get_display_name().lower())
        for app in apps:
            tile = self.build_tile("apps", app)
            # Appended before add(), not after - flowbox.add() invokes
            # the filter function immediately for the child it just
            # added (via get_index()), so _local_entries needs this
            # child's own entry to already exist before add() runs.
            flowbox._local_entries.append((app.get_display_name().lower(), app, None, tile))
            flowbox.add(tile)
        self.add_section("apps", None, flowbox)

    # -- Web Apps tab -------------------------------------------------

    def populate_webapps(self):
        flowbox = self.build_flowbox("webapps")
        add_tile = self.build_add_tile()
        flowbox._local_entries.append(("", None, None, add_tile))
        flowbox.add(add_tile)

        apps = discover_web_apps()
        apps.sort(key=lambda pair: pair[0].get_display_name().lower())
        for app, path in apps:
            tile = self.build_tile("webapps", app)
            flowbox._local_entries.append((app.get_display_name().lower(), app, path, tile))
            flowbox.add(tile)
        self.add_section("webapps", None, flowbox)

    def build_add_tile(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.set_size_request(120, 100)
        image = Gtk.Image.new_from_icon_name("list-add", Gtk.IconSize.DIALOG)
        image.set_pixel_size(ICON_SIZE)
        image.set_halign(Gtk.Align.CENTER)
        box.pack_start(image, False, False, 0)
        label = Gtk.Label(label="Add Web App")
        label.get_style_context().add_class("cosmos-launcher-tile-label")
        label.set_line_wrap(True)
        label.set_justify(Gtk.Justification.CENTER)
        box.pack_start(label, False, False, 0)
        event_box = Gtk.EventBox()
        event_box.add(box)
        return event_box

    # -- Development tab ---------------------------------------------------

    def populate_development(self):
        for title, desktop_ids in DEVELOPMENT_SECTIONS:
            flowbox = self.build_flowbox("development")
            for desktop_id in desktop_ids:
                # Real, confirmed-live crash this avoids: this PyGObject
                # version raises TypeError("constructor returned NULL")
                # for a desktop_id with no matching .desktop file on
                # this system, rather than returning None the way older
                # PyGObject versions (and this exact former
                # cosmos-grid.py code, unexercised until run for
                # real) assumed - any single DEVELOPMENT_SECTIONS id not
                # actually installed (a failed/skipped package, a
                # renamed .desktop file, a partial image) used to take
                # the entire launcher down on startup with no error
                # dialog, just a bare traceback and the window vanishing.
                try:
                    app = Gio.DesktopAppInfo.new(desktop_id)
                except TypeError:
                    app = None
                if app is None:
                    continue
                tile = self.build_tile("development", app)
                flowbox._local_entries.append((app.get_display_name().lower(), app, None, tile))
                flowbox.add(tile)
            if not flowbox._local_entries:
                continue
            self.add_section("development", title, flowbox)

    # -- shared tile/menu construction ---------------------------------

    def build_tile(self, tab, app_info):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.set_size_request(120, 100)
        if cosmos_favorites.is_favorite(app_info.get_id()):
            box.get_style_context().add_class("cosmos-launcher-tile-pinned")

        gicon = app_info.get_icon()
        fallback_icon_name = "applications-internet" if tab == "webapps" else "application-x-executable"
        if gicon is not None:
            image = Gtk.Image.new_from_gicon(gicon, Gtk.IconSize.DIALOG)
        else:
            image = Gtk.Image.new_from_icon_name(fallback_icon_name, Gtk.IconSize.DIALOG)
        image.set_pixel_size(ICON_SIZE)
        image.set_halign(Gtk.Align.CENTER)
        box.pack_start(image, False, False, 0)

        label = Gtk.Label(label=app_info.get_display_name())
        label.get_style_context().add_class("cosmos-launcher-tile-label")
        label.set_line_wrap(True)
        label.set_justify(Gtk.Justification.CENTER)
        label.set_lines(2)
        # Confirmed live: a plain bool here silently coerces to
        # Pango.EllipsizeMode.START (True == 1, the same integer value) -
        # every overflowing name truncated from the front instead of
        # the end.
        label.set_ellipsize(Pango.EllipsizeMode.END)
        label.set_max_width_chars(14)
        box.pack_start(label, False, False, 0)

        # Wrapped in a Gtk.EventBox, not a bare Gtk.Box - a plain
        # Gtk.Box has no GdkWindow of its own to reliably receive
        # pointer button events. Right-click opens a real labeled
        # context menu (Favorite/Unfavorite, plus Remove on the Web
        # Apps tab where allowed) - direct request ("make a box show up
        # with favorite and remove as options"), not a bare gesture
        # with no visible indication it exists.
        event_box = Gtk.EventBox()
        event_box.add(box)
        event_box.connect("button-press-event", self.on_tile_button_press, tab, app_info, box)
        return event_box

    def on_tile_button_press(self, widget, event, tab, app_info, box):
        if event.button != 3:
            return False
        if tab == "webapps":
            child = widget.get_parent()
            flowbox = child.get_parent()
            path = flowbox._local_entries[child.get_index()][2]
            if path is not None:
                # a web app: one dialog to rename it, change its icon, favorite it or remove it (cosmos_webapp_edit.py has the rules)
                self.show_web_app_editor(app_info, path, child, flowbox, box)
                return True
        menu = Gtk.Menu()
        menu.get_style_context().add_class("cosmos-launcher-menu")

        pinned = cosmos_favorites.is_favorite(app_info.get_id())
        favorite_item = Gtk.MenuItem(label="Unfavorite" if pinned else "Favorite")

        def on_toggle_favorite(_item):
            self.toggle_favorite_tile(app_info, box)

        favorite_item.connect("activate", on_toggle_favorite)
        menu.append(favorite_item)

        menu.show_all()
        menu.popup_at_pointer(event)
        return True

    def show_web_app_editor(self, app_info, path, child, flowbox, box):
        """Right-click on a web app: rename it, change its icon (a PNG of exactly 128 x 128 only - see cosmos_webapp_edit.py), favorite it, or
        remove it (built-in defaults can't be removed). Renames apply on Save/Enter; a new icon applies as soon as it is chosen and passes the
        size check; Remove asks again first."""
        we = cosmos_webapp_edit
        slug = we.slug_of(path)
        image, label = box.get_children()[0], box.get_children()[1]

        window = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
        window.set_title("cosmos-launcher-edit-web-app")
        window.set_decorated(False)
        window.get_style_context().add_class("cosmos-launcher")
        make_centered_overlay(window)
        window.set_size_request(int(screen_width() * 0.3), -1)
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        content.set_border_width(20)
        window.add(content)

        title = Gtk.Label(label="Edit web app")
        title.get_style_context().add_class("cosmos-launcher-tile-label")
        content.pack_start(title, False, False, 0)

        name_entry = Gtk.Entry()
        name_entry.set_text(label.get_text())                       # the tile's own text: Gio caches the old name after a rename this session
        name_entry.set_placeholder_text("Name")
        name_entry.get_style_context().add_class("cosmos-launcher-entry")
        content.pack_start(name_entry, False, False, 0)

        icon_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        preview = Gtk.Image()
        preview.set_pixel_size(ICON_SIZE)
        icon_row.pack_start(preview, False, False, 0)
        change_button = Gtk.Button(label="Change icon...")
        change_button.get_style_context().add_class("cosmos-launcher-button")
        icon_row.pack_start(change_button, False, False, 0)
        hint = Gtk.Label(label=f"PNG, exactly {we.ICON_PX} x {we.ICON_PX}", xalign=0)
        hint.get_style_context().add_class("cosmos-launcher-tile-label")
        icon_row.pack_start(hint, True, True, 0)
        content.pack_start(icon_row, False, False, 0)

        status = Gtk.Label(label="", xalign=0)
        status.set_line_wrap(True)
        status.get_style_context().add_class("cosmos-launcher-error")
        content.pack_start(status, False, False, 0)

        def show_current_icon():
            icon_file = we.icon_path_for(slug)
            try:
                cosmos_display.set_image_from_file(preview, icon_file, ICON_SIZE, ICON_SIZE)
                cosmos_display.set_image_from_file(image, icon_file, ICON_SIZE, ICON_SIZE)                      # the tile behind the dialog, so it changes at once too
            except GLib.Error:
                gicon = app_info.get_icon()
                if gicon is not None:
                    preview.set_from_gicon(gicon, Gtk.IconSize.DIALOG)
        show_current_icon()

        def choose_icon(_b):
            # this dialog is an exclusive-keyboard overlay, which would sit on top of the file chooser: step aside while it is open
            window.hide()
            chooser = Gtk.FileChooserNative.new("Choose an icon", None, Gtk.FileChooserAction.OPEN, "Choose", "Cancel")
            png = Gtk.FileFilter()
            png.set_name("PNG images")
            png.add_mime_type("image/png")
            chooser.add_filter(png)
            pictures = os.path.expanduser("~/Pictures")
            if os.path.isdir(pictures):
                chooser.set_current_folder(pictures)
            picked = chooser.get_filename() if chooser.run() == Gtk.ResponseType.ACCEPT else None
            chooser.destroy()
            window.show_all()
            window.present()
            if not picked:
                return
            try:
                we.set_icon(path, picked)
            except we.EditError as exc:
                status.set_text(str(exc))                          # refused: the current icon stays
                return
            except OSError as exc:
                status.set_text(f"Couldn't save the icon ({exc.strerror or exc}).")
                return
            status.set_text("")
            Gtk.IconTheme.get_default().rescan_if_needed()
            show_current_icon()

        change_button.connect("clicked", choose_icon)

        button_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        content.pack_start(button_row, False, False, 0)

        pinned = cosmos_favorites.is_favorite(app_info.get_id())
        favorite_button = Gtk.Button(label="Unfavorite" if pinned else "Favorite")
        favorite_button.get_style_context().add_class("cosmos-launcher-button")

        def on_favorite(_b):
            self.toggle_favorite_tile(app_info, box)
            favorite_button.set_label("Unfavorite" if cosmos_favorites.is_favorite(app_info.get_id()) else "Favorite")
        favorite_button.connect("clicked", on_favorite)
        button_row.pack_start(favorite_button, False, False, 0)

        if not we.is_default(path):
            remove_button = Gtk.Button(label="Remove")
            remove_button.get_style_context().add_class("cosmos-launcher-button")

            def do_remove():
                if not os.path.exists(path):
                    return
                try:
                    we.remove(path)
                except (we.EditError, OSError) as exc:
                    status.set_text(str(exc))
                    return
                if cosmos_favorites.is_favorite(app_info.get_id()):
                    cosmos_favorites.toggle_favorite(app_info.get_id())      # no dangling favorite for an app that is gone
                # child.get_index() again, not an index from before the confirm dialog: another tile may have gone meanwhile
                fresh_idx = child.get_index()
                flowbox.remove(child)
                del flowbox._local_entries[fresh_idx]
                window.destroy()

            def on_remove(_b):
                confirm_remove_dialog(label.get_text(), do_remove)
            remove_button.connect("clicked", on_remove)
            button_row.pack_start(remove_button, False, False, 0)

        spacer = Gtk.Box()
        button_row.pack_start(spacer, True, True, 0)
        cancel_button = Gtk.Button(label="Close")
        cancel_button.get_style_context().add_class("cosmos-launcher-button")
        cancel_button.connect("clicked", lambda _b: window.destroy())
        button_row.pack_start(cancel_button, False, False, 0)
        save_button = Gtk.Button(label="Save name")
        save_button.get_style_context().add_class("cosmos-launcher-button-accent")

        def on_save(_b):
            wanted = name_entry.get_text()
            try:
                new_name = we.rename(path, wanted)
            except (we.EditError, OSError) as exc:
                status.set_text(str(exc) if isinstance(exc, we.EditError) else f"Couldn't save ({exc.strerror or exc}).")
                return
            label.set_text(new_name)
            fresh_idx = child.get_index()
            _old, _app, _path, tile = flowbox._local_entries[fresh_idx]
            flowbox._local_entries[fresh_idx] = (new_name.lower(), app_info, path, tile)      # search matches the new name
            window.destroy()
        save_button.connect("clicked", on_save)
        name_entry.connect("activate", on_save)
        button_row.pack_start(save_button, False, False, 0)

        window.connect("key-press-event", lambda _w, event: window.destroy() if event.keyval == Gdk.KEY_Escape else None)
        window.show_all()
        window.present()
        name_entry.grab_focus()
        return window

    # -- tab switching --------------------------------------------------

    def on_tab_button_clicked(self, _button, key):
        self.set_active_tab(key)

    def set_active_tab(self, key):
        self.active_tab = key
        self.stack.set_visible_child_name(key)
        for tab_key, button in self.tab_buttons.items():
            ctx = button.get_style_context()
            if tab_key == key:
                ctx.remove_class("cosmos-launcher-section-button")
                ctx.add_class("cosmos-launcher-section-button-active")
            else:
                ctx.remove_class("cosmos-launcher-section-button-active")
                ctx.add_class("cosmos-launcher-section-button")
        if key == "webapps":
            self.hint_label.set_text(
                "Type to search · Arrow keys/Tab to browse · Enter to launch · "
                "Right-click to rename, change the icon or remove · Escape to reset"
            )
        else:
            self.hint_label.set_text(
                "Type to search · Arrow keys/Tab to browse · Enter to launch · "
                "Right-click for Favorite · Escape to reset"
            )
        self.update_query(self.query)

    # -- search / selection ---------------------------------------------

    def make_filter_func(self, flowbox):
        def filter_func(child):
            idx = child.get_index()
            name, app, _extra, _tile = flowbox._local_entries[idx]
            if app is None:
                return True  # "+ Add Web App" always shows, unaffected by the filter
            return self.query.lower() in name

        return filter_func

    def update_query(self, new_query):
        self.query = new_query
        self.query_label.set_text(self.query if self.query else "Type to search...")
        for header, flowbox in self.sections.get(self.active_tab, []):
            flowbox.invalidate_filter()
            # Real, confirmed-live bug this fixes (found and fixed the
            # same way in cosmos-shop.py's own update_query()):
            # invalidate_filter() only hides the individual tiles that
            # stop matching - a section's own header label had no
            # visibility logic tied to that at all, so searching left
            # every non-matching section's header showing with nothing
            # underneath it.
            has_match = any(child.get_child_visible() for child in flowbox.get_children())
            if header is not None:
                header.set_visible(has_match)
            flowbox.set_visible(has_match)
        self.select_first_visible()

    def select_first_visible(self):
        """One shared implementation for all three tabs. Apps/Development
        entries never have app is None, so this reduces to "select the
        first match in top-to-bottom order" for them (or the very first
        entry at all when the query is empty, since "" is a substring of
        every name) - the same behavior both of those tabs' own former
        separate scripts already had. Web Apps' own "+ Add Web App" tile
        is the one entry anywhere with app is None - deliberately held
        back as a fallback rather than ever winning a real match, but
        still preferred over every real entry while the query is empty
        (direct carry-over from cosmos-webapps.py's own original
        behavior: land on the Add tile first when nothing's typed yet,
        so Enter with an empty query opens the add dialog rather than
        launching whatever happened to be first alphabetically)."""
        for _header, flowbox in self.sections.get(self.active_tab, []):
            flowbox.unselect_all()

        def select(flowbox, tile):
            child = tile.get_parent()
            flowbox.select_child(child)
            child.grab_focus()

        fallback = None
        for _header, flowbox in self.sections.get(self.active_tab, []):
            for name, app, _extra, tile in flowbox._local_entries:
                if app is None:
                    if fallback is None:
                        fallback = (flowbox, tile)
                    continue
                if self.query and self.query.lower() not in name:
                    continue
                if self.query or fallback is None:
                    select(flowbox, tile)
                    return
        if fallback is not None:
            select(*fallback)

    def on_selection_changed(self, changed_flowbox, tab):
        """Enforces one selected tile per tab, not one per section -
        every section is its own separate Gtk.FlowBox, each
        independently SelectionMode.SINGLE, so GTK itself only ever
        guarantees single-selection within each of them individually.
        Only acts when the changed box just GAINED a selection -
        unselecting a sibling here fires this same signal on it too
        (going from selected to empty), so returning early whenever the
        box that just changed has nothing selected is what stops that
        from recursing forever, not a depth guard. Real, confirmed-live
        bug this fixes (same class as cosmos-shop.py's own categories -
        see that file's own on_selection_changed() docstring): the
        Development tab has several sections on screen at once, and nothing
        used to clear one section's selection when a different
        section's tile got clicked, so two tiles could show highlighted
        at once and Enter/activation could fire the wrong (stale-
        selected) one instead of whichever was actually just clicked."""
        if not changed_flowbox.get_selected_children():
            return
        for _header, flowbox in self.sections.get(tab, []):
            if flowbox is not changed_flowbox and flowbox.get_selected_children():
                flowbox.unselect_all()

    # -- activation -----------------------------------------------------

    def activate_entry(self, app, extra):
        if app is None:
            show_add_web_app_dialog()
            return
        app.launch([], None)
        # The launched window covers this one. Clear what was typed in the search, but stay on the tab it was
        # launched from: this used to reset to the Apps tab, so a web app launched from Web made the Web
        # icons vanish from the grid for whenever it was shown again.
        GLib.idle_add(self.clear_search)

    def on_child_activated(self, flowbox, child, _tab):
        """Uses the exact (flowbox, child) the signal itself already
        gives us, not a re-scan through every section for "whichever
        one still has something selected" - see on_selection_changed()'s
        own docstring for the real bug this avoids reintroducing."""
        idx = child.get_index()
        _name, app, extra, _tile = flowbox._local_entries[idx]
        self.activate_entry(app, extra)

    def launch_selected(self):
        """The Enter-key path - unlike a tile click/activation, a bare
        keypress on the window has no specific (flowbox, child) to hand
        over, so this still has to look for whichever section currently
        has something selected. Reliable now (see
        on_selection_changed()) since at most one section anywhere on
        the active tab can ever have a selection at once."""
        for _header, flowbox in self.sections.get(self.active_tab, []):
            selected = flowbox.get_selected_children()
            if not selected:
                continue
            idx = selected[0].get_index()
            _name, app, extra, _tile = flowbox._local_entries[idx]
            self.activate_entry(app, extra)
            return

    def on_key_press(self, _widget, event):
        keyval = event.keyval
        if keyval == Gdk.KEY_Escape:
            self.reset_view()
            return True
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            self.launch_selected()
            return True
        if keyval == Gdk.KEY_BackSpace:
            self.update_query(self.query[:-1])
            return True
        # Arrow keys, Tab, etc. - let whichever FlowBox currently has
        # keyboard focus handle these normally (its own built-in
        # keyboard navigation moves within that section; Tab hops to
        # the next section/tab button via GTK's ordinary focus chain).
        if keyval in (
            Gdk.KEY_Up, Gdk.KEY_Down, Gdk.KEY_Left, Gdk.KEY_Right, Gdk.KEY_Tab, Gdk.KEY_ISO_Left_Tab,
        ):
            return False
        unicode_char = Gdk.keyval_to_unicode(keyval)
        if unicode_char and chr(unicode_char).isprintable():
            self.update_query(self.query + chr(unicode_char))
            return True
        return False


def screen_width():
    monitor = Gdk.Display.get_default().get_monitor(0)
    return monitor.get_geometry().width if monitor else 1280


def make_centered_overlay(window):
    """Turns a plain window into a centered popup: an unanchored OVERLAY layer
    surface (the compositor centers it) that takes exclusive keyboard focus. An
    ordinary Wayland toplevel can neither center itself nor float over the
    maximized single-window layout."""
    GtkLayerShell.init_for_window(window)
    GtkLayerShell.set_namespace(window, "cosmos-launcher-dialog")
    GtkLayerShell.set_layer(window, GtkLayerShell.Layer.OVERLAY)
    GtkLayerShell.set_keyboard_mode(window, GtkLayerShell.KeyboardMode.EXCLUSIVE)


class LauncherPadEmitter:
    """Where cosmos-input's Translator sends its "keys" here: straight into the
    launcher's own handlers instead of a virtual keyboard. rel() (right-stick
    cursor movement, driven unconditionally by cosmos_pad_native.NativePads -
    see its own docstring on why that bypasses pointer_mode entirely) is the
    one real exception: a report that nothing in the gridscreen was reachable
    with a controller and no cursor confirmed this needed to actually move a
    real pointer, not stay the no-op it used to be - a plain UInput mouse, the
    same device shape cosmos_input.Emitter already uses system-wide, so the
    compositor renders and moves the cursor exactly the way it would for any
    other relative-motion input device."""

    def __init__(self, launcher):
        self.launcher = launcher
        self.ui = None
        try:
            import evdev
            from evdev import ecodes
            self.ui = evdev.UInput({ecodes.EV_REL: [ecodes.REL_X, ecodes.REL_Y, ecodes.REL_WHEEL]},
                                    name="cosmos-launcher-pad-mouse")
        except Exception:
            log.warning("could not create a virtual mouse for controller cursor movement", exc_info=True)

    def key(self, code, value):
        if value == 1:
            self.launcher.on_pad_key(code)

    def rel(self, dx=0, dy=0, wheel=0):
        if self.ui is None:
            return
        from evdev import ecodes
        if dx:
            self.ui.write(ecodes.EV_REL, ecodes.REL_X, dx)
        if dy:
            self.ui.write(ecodes.EV_REL, ecodes.REL_Y, dy)
        if wheel:
            self.ui.write(ecodes.EV_REL, ecodes.REL_WHEEL, wheel)
        self.ui.syn()


class OskController:
    """Opens/closes the on-screen keyboard (same script cosmos-input starts on
    Select) - here for typing a search from the couch."""

    def __init__(self):
        self.proc = None
        self.script = None
        for directory in (os.environ.get("COSMOS_INPUT_DIR"), "/opt/cosmos-input", "/usr/lib/cosmos",
                          os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "input")):
            if directory and os.path.exists(os.path.join(directory, "cosmos_osk.py")):
                self.script = os.path.join(directory, "cosmos_osk.py")
                break

    @property
    def open(self):
        if self.proc is not None and self.proc.poll() is not None:
            self.proc = None
        return self.proc is not None

    def toggle(self):
        if self.script is None:
            return
        if self.open:
            self.proc.terminate()
            self.proc = None
        else:
            import subprocess
            import sys
            self.proc = subprocess.Popen([sys.executable, self.script])


def confirm_remove_dialog(name, on_confirm):
    """Small centered Yes/No dialog - direct requirement ("delete needs
    a yes or no dialogue so it's not automatic"), a real, live
    accidental deletion this fixes (Disney+ was removed outright with a
    single right-click before this existed). Not a grabbing/modal
    window - just a floating window kept on top - on_confirm() only
    ever runs from the Yes button's own click handler, never as a side
    effect of opening this at all."""
    window = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
    window.set_title("cosmos-launcher-confirm-remove")
    window.set_decorated(False)
    window.get_style_context().add_class("cosmos-launcher")
    make_centered_overlay(window)
    window.set_size_request(int(screen_width() * 0.25), -1)

    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
    box.set_border_width(20)
    window.add(box)

    label = Gtk.Label(label=f'Remove "{name}"?')
    label.get_style_context().add_class("cosmos-launcher-tile-label")
    box.pack_start(label, False, False, 0)

    button_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    button_row.set_halign(Gtk.Align.END)
    box.pack_start(button_row, False, False, 0)

    no_button = Gtk.Button(label="No")
    no_button.get_style_context().add_class("cosmos-launcher-button")
    no_button.connect("clicked", lambda _b: window.destroy())
    button_row.pack_start(no_button, False, False, 0)

    yes_button = Gtk.Button(label="Yes")
    yes_button.get_style_context().add_class("cosmos-launcher-button-accent")

    def on_yes(_b):
        window.destroy()
        on_confirm()

    yes_button.connect("clicked", on_yes)
    button_row.pack_start(yes_button, False, False, 0)

    window.connect(
        "key-press-event",
        lambda _w, event: window.destroy() if event.keyval == Gdk.KEY_Escape else None,
    )

    window.show_all()
    window.present()
    no_button.grab_focus()
    return window


def show_add_web_app_dialog():
    window = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
    window.set_title("cosmos-launcher-add-web-app")
    window.set_decorated(False)
    window.get_style_context().add_class("cosmos-launcher")
    make_centered_overlay(window)
    window.set_size_request(int(screen_width() * 0.3), -1)

    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    box.set_border_width(20)
    window.add(box)

    name_entry = Gtk.Entry()
    name_entry.set_placeholder_text("Name (e.g. Discord)")
    name_entry.get_style_context().add_class("cosmos-launcher-entry")
    box.pack_start(name_entry, False, False, 0)

    url_entry = Gtk.Entry()
    url_entry.set_placeholder_text("URL (e.g. discord.com/app)")
    url_entry.get_style_context().add_class("cosmos-launcher-entry")
    box.pack_start(url_entry, False, False, 0)

    button_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    button_row.set_halign(Gtk.Align.END)
    box.pack_start(button_row, False, False, 0)

    cancel_button = Gtk.Button(label="Cancel")
    cancel_button.get_style_context().add_class("cosmos-launcher-button")
    cancel_button.connect("clicked", lambda _b: window.destroy())
    button_row.pack_start(cancel_button, False, False, 0)

    add_button = Gtk.Button(label="Add")
    add_button.get_style_context().add_class("cosmos-launcher-button-accent")

    def on_add(_b):
        name = name_entry.get_text().strip()
        url = url_entry.get_text().strip()
        if not name or not url:
            return
        if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", url):
            url = "https://" + url
        add_web_app(name, url)
        window.destroy()

    add_button.connect("clicked", on_add)
    url_entry.connect("activate", on_add)
    button_row.pack_start(add_button, False, False, 0)

    window.connect(
        "key-press-event",
        lambda _w, event: window.destroy() if event.keyval == Gdk.KEY_Escape else None,
    )

    window.show_all()
    window.present()
    name_entry.grab_focus()
    return window


def main():
    provider = Gtk.CssProvider()
    provider.load_from_data(cosmos_display.scale_css(CSS).encode())
    Gtk.StyleContext.add_provider_for_screen(
        Gdk.Screen.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_USER
    )
    win = CosmosLauncher()
    win.show_all()
    Gtk.main()


if __name__ == "__main__":
    main()
