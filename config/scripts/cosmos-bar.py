#!/usr/bin/env python3
"""CosmOS's own bars - replaces xfce4-panel entirely.

Why: xfce4-panel decides which screen edge it's actually sitting on by
querying _NET_WORKAREA (confirmed live via its own startup warning,
"Failed to get _NET_WORKAREA; using full screen dimensions") - i3 never
answers that, so every "bottom" position value tried (multiple built-in
position codes, explicit pixel y-coordinates, even a real simulated
mouse drag) always fell back to the same top placement. i3 DOES fully
support real dock windows and _NET_WM_STRUT_PARTIAL directly (the same
mechanism polybar/tint2/lemonbar rely on to sit under i3) - a window
that sets its own type hint to DOCK and writes its own strut never
needs to ask the WM anything about where "top" or "bottom" is, so this
sidesteps the bug completely instead of working around it.

Three plain GTK3 dock windows, one process, one Gtk.main loop:
  - bottom bar: the profile picture in the centre (click it to switch profile) and the
    date at the right. There is no Apps button: the gridscreen (cosmos-launcher: Apps /
    Web / Dev tabs) is the permanent layer behind every window, and Alt+Tab
    (hold R2 on a controller) always lists it as "Home" - the way back to it.
  - top bar: the centered clock and, at the right end, the system tray (a
    StatusNotifierItem host - see cosmos_sni_host.py).
  - left bar: from top to bottom, the "Shop" button (toggles
    cosmos-shop, a full-screen grid treatment for browsing/installing
    DNF/AppImage/Flatpak packages - see that file's own docstring), the
    Brave browser launcher itself, the keyboard-shortcuts hint icon,
    then the Favorite Apps list (build_favorite_apps_list()) - direct
    requirement ("put the brave browser launcher at the top of the left
    bar under web apps... also move the help icon under brave"), from
    back when Web Apps still had its own separate left-bar button (see
    cosmos-launcher.py's own docstring for why it's a tab there now
    instead). This is also, deliberately, everything left on the bars
    themselves once the old pinned dev-tools row moved into cosmos-
    launcher's own Development tab - the one shared account's two people
    have very different needs (a whole IDE/dev-tool row vs. "nothing to
    worry about but games/emulators"), and the left bar ends up being
    exactly the small, stable set useful to both.

GDK exposes DOCK as a window type hint directly (maps to
_NET_WM_WINDOW_TYPE_DOCK on realize, no manual Xlib needed) but GTK3's
Python bindings never exposed partial-strut support, so the actual
_NET_WM_STRUT_PARTIAL/_NET_WM_STRUT properties are written by hand via
python-xlib against the window's own XID once realized.
"""
import os
import subprocess
import time

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GtkLayerShell", "0.1")
from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Gtk, GtkLayerShell  # noqa: E402
import cosmos_display  # noqa: E402

import cosmos_favorites
import cosmos_profiles
import cosmos_session_state
import cosmos_sni_host
import cosmos_wm_ipc

BAR_HEIGHT = cosmos_display.px(30)
TRAY_RESERVED_WIDTH = 170
ICON_SIZE = cosmos_display.px(22)
AVATAR_SIZE = cosmos_display.px(24)
# Smaller than ICON_SIZE on purpose - the left bar itself is only
# BAR_HEIGHT (30px) wide, so a plain ICON_SIZE icon plus the button's
# own default padding left it nearly touching both edges (direct
# feedback: "shrink the swap icon so it's not touching the bars
# edge").
SWAP_ICON_SIZE = cosmos_display.px(16)
LOGO_PATH = "/opt/astronar-studio/launcher/icons/cosmos_logo.png"

BG = "#0a0e16"
FG = "#e8ecf4"
ACCENT = "#ff0090"

# The one launcher icon still pinned directly on a bar - direct
# requirement ("put the brave browser launcher at the top of the left
# bar under web apps"). Every other former pinned-icon-row entry now
# lives in cosmos-launcher.py's own DEVELOPMENT_DESKTOP_IDS/DEVELOPMENT_SECTIONS
# instead (its own Development tab).
BRAVE_DESKTOP_ID = "brave-browser.desktop"

# Shared between build_bottom_bar()'s Apps button and build_shop_button()
# - direct requirement ("apps and web apps should never be opened at
# the same time. One opening should always close the other"), now
# covering the two full-screen grids this bar can open (cosmos-launcher
# itself absorbed the former separate Web Apps/Development toggles as tabs -
# direct requirement: "combine apps, web apps, and development apps into the
# bottom left apps icon" - so there's only ever one launcher-family
# window to track here any more, plus CosmOS Shop). A module-level
# dict, not one local to either button, specifically because they're
# two separate functions/closures with no other shared state between
# them at all - this is the one thing that actually needs to be visible
# to both.
FULLSCREEN_GRID_STATE = {
    "cosmos-shop": None,
}


def toggle_fullscreen_grid(command, button):
    """Real mutual exclusion, not just each button's own independent
    toggle - opening either grid always closes the other first, then
    toggles the one actually clicked (open if it wasn't running, closed
    if it was already the one open). A loop over FULLSCREEN_GRID_STATE's
    other keys, not a single hardcoded "other" - real, live bug this
    fixes (confirmed back when this tracked three separate grids,
    cosmos-launcher/cosmos-webapps/cosmos-grid, before Web
    Apps/Development folded into cosmos-launcher's own tabs: the old binary
    `"cosmos-webapps" if command == "cosmos-launcher" else
    "cosmos-launcher"` never considered a third entry at all, so opening
    one grid while a different second one was open left both running at
    once). Still written as a loop rather than simplified back down to
    a single hardcoded "other" now that there are only two entries again
    - nothing about FULLSCREEN_GRID_STATE's own shape guarantees it
    stays at exactly two forever, and the loop costs nothing extra to
    keep correct either way.

    button.get_style_context().set_state_flags(NORMAL, True) - real,
    live bug this fixes (confirmed via a burst of screenshots taken
    right as a grid closed, not guessed: the button stayed visibly
    "stuck" in its own :hover/:active highlight color indefinitely
    afterward, looking like some kind of ghost/duplicate window at a
    glance). The fullscreen grid this spawns immediately steals both
    keyboard focus and the pointer, so this button never gets the
    ordinary GDK leave-notify event GTK's own CSS :hover/:active state
    tracking normally relies on to clear itself once the pointer moves
    away - clearing it explicitly here is the only way it ever resets
    at all, since nothing else ever tells GTK the pointer left."""
    for other, other_proc in FULLSCREEN_GRID_STATE.items():
        if other == command:
            continue
        if other_proc is not None and other_proc.poll() is None:
            other_proc.terminate()
            FULLSCREEN_GRID_STATE[other] = None

    proc = FULLSCREEN_GRID_STATE[command]
    if proc is not None and proc.poll() is None:
        proc.terminate()
        FULLSCREEN_GRID_STATE[command] = None
    else:
        FULLSCREEN_GRID_STATE[command] = subprocess.Popen([command])
    button.get_style_context().set_state_flags(Gtk.StateFlags.NORMAL, True)


CSS = f"""
window.cosmos-bar {{
    background-color: {BG};
}}
.cosmos-bar button {{
    background: transparent;
    border: none;
    box-shadow: none;
    padding: 2px 8px;
}}
.cosmos-bar button:hover {{
    background-color: alpha({ACCENT}, 0.2);
}}
/* The left bar is only BAR_HEIGHT wide: the horizontal bars' 8px side padding
would make its buttons wider than that and the panel would claim more. */
.cosmos-bar-left button {{
    padding: 2px 3px;
    min-width: 0;
}}
.cosmos-bar label {{
    color: {FG};
}}
.cosmos-avatar {{
    background: transparent;
}}
/* Tray icons' right-click menus (drawn by the bar from the app's dbusmenu). */
menu, .menu {{
    background-color: {BG};
    color: {FG};
    border: 1px solid alpha({FG}, 0.2);
}}
menuitem {{
    color: {FG};
}}
menuitem:hover {{
    background-color: {ACCENT};
    color: {BG};
}}
""".encode()

# ---------------------------------------------------------------------
# Hide while GameBox is open (direct request: "cosmos-bar is hidden until
# gamebox closes"). launch-gamebox writes its own PID to this file for as long
# as GameBox is running; the bar polls it. Keyed on a live PID rather than the
# file merely existing, so a launcher that gets killed without cleaning up can
# never leave the desktop with no bar - a dead PID counts as "not requested".
# ---------------------------------------------------------------------

def set_bars_hidden(bars, hidden):
    """Unmaps every bar (which releases its reserved screen edge - a layer
    surface's exclusive zone only counts while it is mapped) or puts them all
    back. Shown in the order they were built: cosmos-wm arranges layer surfaces
    in creation order, and the left bar is sized against whatever the top and
    bottom bars already left over."""
    for window in bars:
        if hidden:
            window.hide()
        else:
            window.show_all()


def install_hide_watcher(bars):
    cosmos_session_state.watch_hide_request(lambda hidden: set_bars_hidden(bars, hidden))


def make_bar_window(name, edges, thickness):
    """A layer-shell panel: pinned to `edges`, `thickness` px thick across
    the axis perpendicular to them, reserving that strip of the screen for
    every window (the exclusive zone that replaced _NET_WM_STRUT_PARTIAL)."""
    window = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
    window.set_title(name)
    window.set_decorated(False)
    window.set_resizable(False)
    window.get_style_context().add_class("cosmos-bar")
    GtkLayerShell.init_for_window(window)
    GtkLayerShell.set_namespace(window, name)
    GtkLayerShell.set_layer(window, GtkLayerShell.Layer.TOP)
    for edge in (GtkLayerShell.Edge.TOP, GtkLayerShell.Edge.BOTTOM, GtkLayerShell.Edge.LEFT, GtkLayerShell.Edge.RIGHT):
        GtkLayerShell.set_anchor(window, edge, edge in edges)
    # The long axis is 1, not -1: the compositor stretches the panel to the
    # full edge (anchored on both ends), but GTK never draws - and so the
    # surface never maps - a window whose requested size is 0 along an axis,
    # which is what an all-expanding, contentless bar (the top one) asks for.
    if GtkLayerShell.Edge.LEFT in edges and GtkLayerShell.Edge.RIGHT not in edges:
        window.set_size_request(thickness, 1)
    else:
        window.set_size_request(1, thickness)
    GtkLayerShell.auto_exclusive_zone_enable(window)
    return window


def icon_for(app_info, size):
    gicon = app_info.get_icon()
    if gicon is None:
        return Gtk.Image.new_from_icon_name("application-x-executable", Gtk.IconSize.MENU)
    image = Gtk.Image.new_from_gicon(gicon, Gtk.IconSize.MENU)
    image.set_pixel_size(size)
    return image


def launcher_button(desktop_id):
    # Real, confirmed-live crash this avoids: this PyGObject version
    # raises TypeError("constructor returned NULL") for a desktop_id
    # with no matching .desktop file, rather than returning None the
    # way this function's own very next line already assumed - a
    # missing/renamed BRAVE_DESKTOP_ID used to take this whole bar
    # process down at startup instead of just leaving this one button
    # off the left bar.
    try:
        app_info = Gio.DesktopAppInfo.new(desktop_id)
    except TypeError:
        app_info = None
    if app_info is None:
        return None
    button = Gtk.Button()
    button.set_relief(Gtk.ReliefStyle.NONE)
    button.set_image(icon_for(app_info, ICON_SIZE))
    button.set_tooltip_text(app_info.get_display_name())

    def on_click(_btn):
        app_info.launch([], None)

    button.connect("clicked", on_click)
    return button


def build_shop_button():
    """cosmos-shop's own toggle - same full-screen grid treatment as
    cosmos-launcher, for browsing/installing DNF/AppImage/Flatpak
    packages (see that file's own docstring). Icon-only, no label -
    this sits on the narrow left bar (BAR_HEIGHT, 30px wide), which a
    text label never fit the way it did horizontally, same reasoning
    launcher_button() and the Favorite Apps list buttons are icon-only
    for.

    Same toggle_fullscreen_grid() as every other grid button - opening
    this always closes whichever other grid is open first (see
    FULLSCREEN_GRID_STATE's own comment)."""
    button = Gtk.Button()
    button.set_relief(Gtk.ReliefStyle.NONE)
    try:
        pixbuf = Gtk.IconTheme.get_default().load_icon("system-software-install", ICON_SIZE, 0)
        icon = Gtk.Image.new_from_pixbuf(pixbuf)
    except GLib.Error:
        icon = Gtk.Image.new_from_icon_name("system-software-install", Gtk.IconSize.MENU)
    button.set_image(icon)
    button.set_tooltip_text("Shop")
    button.connect("clicked", lambda b: toggle_fullscreen_grid("cosmos-shop", b))
    return button


def load_avatar_pixbuf(size):
    """The logged-in account's own ~/.face picture - resolves per-user
    automatically since each account runs its own copy of this whole
    bar under its own X session/HOME, same reason LOGO_PATH itself
    doesn't need a username baked in anywhere. Falls back to the same
    GameBox logo the Applications button already falls back to, for an
    account with no picture of its own yet (e.g. before
    the profile picker has run)."""
    face_path = os.path.expanduser("~/.face")
    scale = cosmos_display.monitor_scale()      # loaded at the monitor's scale so it is sharp on a scaled output (painted back at 1/scale)
    try:
        return GdkPixbuf.Pixbuf.new_from_file_at_scale(face_path, size * scale, size * scale, False)
    except GLib.Error:
        pass
    try:
        return GdkPixbuf.Pixbuf.new_from_file_at_scale(LOGO_PATH, size * scale, size * scale, True)
    except GLib.Error:
        return None


def build_avatar_widget(size=AVATAR_SIZE):
    """The logged-in account's own picture, clipped into a diamond (a
    45-degree-rotated square) - GTK3 has no built-in image-masking
    widget, so this is a plain Gtk.DrawingArea painting the pixbuf
    itself through a real Cairo clip path rather than faking it with
    CSS. Wrapped in a Gtk.EventBox (not a Gtk.Button) specifically so
    clicking it doesn't paint the `.cosmos-bar button:hover` CSS rule's
    full rectangular hover background over what's meant to read as a
    diamond - transparency comes from the "cosmos-avatar" CSS class
    below, NOT set_visible_window(False): confirmed live (via a debug
    print in the click handler that never fired) that a windowless
    EventBox never receives button-press-event at all once nested
    inside a Gtk.Overlay - it needs a real GdkWindow of its own to
    reliably receive input in that context. Clicking it offers to switch
    profile (see on_press below)."""
    picture = {"pixbuf": load_avatar_pixbuf(size)}
    pixbuf_scale = cosmos_display.monitor_scale()

    area = Gtk.DrawingArea()
    area.set_size_request(size, size)

    def on_draw(_widget, cr):
        pixbuf = picture["pixbuf"]
        if pixbuf is None:
            return False
        half = size / 2
        cr.move_to(half, 0)
        cr.line_to(size, half)
        cr.line_to(half, size)
        cr.line_to(0, half)
        cr.close_path()
        cr.clip()
        cosmos_display.paint_pixbuf(cr, pixbuf, pixbuf_scale)
        return False

    area.connect("draw", on_draw)

    # The picture is replaced while the bar is running when the profile's picture changes (GameBox hands over the portrait of a new Astronar the moment it is
    # made, and the picker's ~/.face changes with it), so the file is watched.
    def reload_picture(*_):
        picture["pixbuf"] = load_avatar_pixbuf(size)
        area.queue_draw()

    monitor = Gio.File.new_for_path(os.path.expanduser("~/.face")).monitor_file(Gio.FileMonitorFlags.NONE, None)
    monitor.connect("changed", lambda _m, _f, _o, event: reload_picture() if event in (
        Gio.FileMonitorEvent.CHANGES_DONE_HINT, Gio.FileMonitorEvent.CREATED, Gio.FileMonitorEvent.RENAMED) else None)
    area._face_monitor = monitor            # kept alive with the widget

    event_box = Gtk.EventBox()
    event_box.get_style_context().add_class("cosmos-avatar")
    event_box.add(area)

    # Clicking the picture is how you change profile: the session ends (closing your apps)
    # and the profile picker comes back - offered as a one-item menu so a stray click
    # cannot end the session.
    event_box.set_tooltip_text("Your profile")
    profile = cosmos_profiles.active_profile()
    if profile:
        event_box.set_tooltip_text(f"{profile['name']} - click to switch profile")

    def on_press(_widget, event):
        menu = Gtk.Menu()
        item = Gtk.MenuItem(label="Switch profile (closes your apps)")
        item.connect("activate", lambda _i: cosmos_wm_ipc.request("quit"))
        menu.append(item)
        menu.show_all()
        menu.popup_at_widget(event_box, Gdk.Gravity.NORTH, Gdk.Gravity.SOUTH, event)
        return True

    event_box.connect("button-press-event", on_press)
    return event_box


def build_favorite_apps_list():
    """Replaces the old window-swap dropdown on the left bar - direct
    requirement ("a Favorite Apps on the left bar in place of the
    switch button, and instead [use] Alt+Tab for swapping"): switching
    between open windows moves entirely to a real Alt+Tab in
    cosmos-wm.c (see that file's own comment on the cycling switcher),
    freeing this bar up for something that was never really about
    window-switching in the first place - a quick-launch strip for
    whatever apps someone's pinned as favorites, from cosmos-launcher.py's
    own right-click -> Favorite context menu item, available on any of
    its three tabs (see cosmos_favorites.py's own docstring for the
    shared storage format).

    A real Gio.FileMonitor on cosmos_favorites.FAVORITES_PATH, not a
    poll loop - pinning/unpinning happens in a completely different
    process (cosmos-launcher, whenever it's open), so this bar has no
    other way to find out the list changed. Works fine even before that
    file exists yet (a fresh
    account with nothing pinned) - GFileMonitor still fires once it's
    finally created. The monitor object itself is stashed on the
    returned box (box._favorites_monitor) purely to keep it alive -
    GFileMonitor stops watching the moment nothing still holds a
    reference to it, and a local variable inside this function would be
    garbage collected as soon as build_favorite_apps_list() returns."""
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)

    def refresh(*_args):
        for child in box.get_children():
            box.remove(child)
        for desktop_id in cosmos_favorites.load_favorites():
            # Same real TypeError-instead-of-None crash as
            # launcher_button() above, much more likely to actually
            # happen here specifically: favorites.txt can list a
            # desktop_id for an app that's since been uninstalled, or a
            # web app that's since been removed (cosmos-launcher.py's
            # own Remove menu item) while still favorited - this used
            # to crash the ENTIRE bar (Apps/Shop toggles, Brave
            # launcher, clock, tray, everything) every time this refresh
            # ran afterward, not just skip the one stale favorite.
            try:
                app_info = Gio.DesktopAppInfo.new(desktop_id)
            except TypeError:
                app_info = None
            if app_info is None:
                continue
            button = Gtk.Button()
            button.set_relief(Gtk.ReliefStyle.NONE)
            button.set_tooltip_text(app_info.get_display_name())
            gicon = app_info.get_icon()
            if gicon is not None:
                image = Gtk.Image.new_from_gicon(gicon, Gtk.IconSize.SMALL_TOOLBAR)
                image.set_pixel_size(SWAP_ICON_SIZE)
            else:
                image = Gtk.Image.new_from_icon_name("application-x-executable", Gtk.IconSize.SMALL_TOOLBAR)
                image.set_pixel_size(SWAP_ICON_SIZE)
            button.add(image)
            button.connect("clicked", lambda _b, a=app_info: a.launch([], None))
            box.pack_start(button, False, False, 0)
        box.show_all()

    refresh()
    favorites_file = Gio.File.new_for_path(cosmos_favorites.FAVORITES_PATH)
    monitor = favorites_file.monitor_file(Gio.FileMonitorFlags.NONE, None)
    monitor.connect("changed", refresh)
    box._favorites_monitor = monitor
    return box


def build_left_bar():
    """The 3rd bar (direct request: "a 3rd bar on the left that
    connects the top and bottom") - spans exactly the gap between the
    top and bottom bars (y=BAR_HEIGHT to y=screen_height-BAR_HEIGHT),
    same BAR_HEIGHT thickness the other two bars already use, so all
    three read as one continuous frame rather than three
    differently-sized strips.

    Favorite Apps (build_favorite_apps_list()), not the old window-swap
    icon any more - direct requirement ("a Favorite Apps on the left
    bar in place of the switch button, and instead [use] Alt+Tab for
    swapping" - see that function's own docstring, and cosmos-wm.c's
    own comment on the real Alt+Tab cycling switcher that now owns
    window-switching instead).

    Web Apps, Brave, and the shortcuts hint now stack above the
    Favorite Apps list too - direct requirement ("put the brave browser
    launcher at the top of the left bar under web apps... also move the
    help icon under brave"), moved off the top bar once its own pinned
    icon row became the Studio grid instead (see BRAVE_DESKTOP_ID's own
    comment). Shop sits between Web Apps and Brave - same toggle
    treatment, no dedicated screen real estate of its own to fight for.
    """
    # Pinned to the left, top AND bottom edges: cosmos-wm has already carved
    # the top and bottom bars out of the usable area by the time this one is
    # arranged (they are built first), so it spans exactly the gap between them.
    window = make_bar_window(
        "cosmos-bar-left",
        {GtkLayerShell.Edge.LEFT, GtkLayerShell.Edge.TOP, GtkLayerShell.Edge.BOTTOM},
        BAR_HEIGHT,
    )
    window.get_style_context().add_class("cosmos-bar-left")

    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    box.get_style_context().add_class("cosmos-bar")
    window.add(box)

    top_group = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
    top_group.set_halign(Gtk.Align.CENTER)
    top_group.set_margin_top(8)

    shop_button = build_shop_button()
    top_group.pack_start(shop_button, False, False, 0)

    brave_button = launcher_button(BRAVE_DESKTOP_ID)
    if brave_button is not None:
        top_group.pack_start(brave_button, False, False, 0)

    shortcuts_hint = build_shortcuts_hint()
    top_group.pack_start(shortcuts_hint, False, False, 0)

    box.pack_start(top_group, False, False, 0)

    favorites_box = build_favorite_apps_list()
    favorites_box.set_halign(Gtk.Align.CENTER)
    favorites_box.set_valign(Gtk.Align.START)
    favorites_box.set_margin_top(12)
    box.pack_start(favorites_box, False, False, 0)

    window.show_all()
    return window


def build_bottom_bar():
    window = make_bar_window(
        "cosmos-bar-bottom",
        {GtkLayerShell.Edge.BOTTOM, GtkLayerShell.Edge.LEFT, GtkLayerShell.Edge.RIGHT},
        BAR_HEIGHT,
    )

    box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
    box.get_style_context().add_class("cosmos-bar")

    # No Apps button any more: the gridscreen (cosmos-launcher) is always the layer behind
    # every window, and the Windows key (Guide on a controller) is the way back to it: it raises the grid
    # over the windows, and again lowers it (cosmos-wm).

    # The date lives here now (direct request: "the date in the bottom
    # right bar") - a plain expanding spacer pushes it to the bar's
    # true right edge, same technique the top bar's own right-aligned
    # launcher group already uses. Refreshed once a minute, not every
    # second like the top bar's clock - a date has no reason to poll
    # any faster than that.
    date_spacer = Gtk.Box()
    box.pack_start(date_spacer, True, True, 0)

    date_label = Gtk.Label(label="")
    date_label.set_margin_end(10)
    box.pack_start(date_label, False, False, 0)

    def update_date():
        date_label.set_text(time.strftime("%a %b %d"))
        return True

    update_date()
    GLib.timeout_add_seconds(60, update_date)

    # Gtk.Overlay, not just another pack_start slot in the same box -
    # the app menu button on the left has no matching content on the
    # right to balance it, so a plain box layout can't put anything at
    # the true horizontal center; an overlay child positioned via
    # halign/valign CENTER is centered against the window's own real
    # allocated width regardless of what's on either side.
    overlay = Gtk.Overlay()
    overlay.add(box)
    avatar = build_avatar_widget()
    avatar.set_halign(Gtk.Align.CENTER)
    avatar.set_valign(Gtk.Align.CENTER)
    overlay.add_overlay(avatar)

    window.add(overlay)
    window.show_all()
    return window


def build_shortcuts_hint():
    """Direct request: "a hint icon that shows shortcuts when hovering
    in-between web applications and the clock" - cosmos-wm.c grabs its
    global keybindings itself (Alt+X, close the focused window - see
    that file's own comment on why Alt+X specifically, chosen after an
    earlier on-screen close button was tried and rejected; and Alt+Tab/
    Alt+Shift+Tab, the real window-cycling switcher that replaced
    cosmos-bar's own on-bar swap button, see that file's own comment on
    alt_tab_init()), and there's nowhere else in this desktop anyone
    would discover either from otherwise - no title bar, no menu entry,
    nothing else even hints a keybinding exists at all. A plain
    Gtk.Image has no window of its own to receive pointer events for
    GTK's own tooltip mechanism to trigger on - wrapped in a
    Gtk.EventBox for exactly that, the same reason every other
    icon-only widget in this file that needs its own event handling
    (build_window_swap_button(), for instance) is a real interactive
    widget rather than a bare Image."""
    icon_box = Gtk.EventBox()
    try:
        pixbuf = Gtk.IconTheme.get_default().load_icon("dialog-information", ICON_SIZE, 0)
        icon = Gtk.Image.new_from_pixbuf(pixbuf)
    except GLib.Error:
        icon = Gtk.Image.new_from_icon_name("dialog-information", Gtk.IconSize.MENU)
    icon_box.add(icon)
    icon_box.set_tooltip_text(
        "Keyboard shortcuts:\n"
        "• Windows key — show the grid; press again to go back to your window\n"
        "• Alt+Tab / Alt+Shift+Tab — switch between open windows\n"
        "• Alt+X — close the focused window"
    )
    return icon_box


def build_top_bar():
    window = make_bar_window(
        "cosmos-bar-top",
        {GtkLayerShell.Edge.TOP, GtkLayerShell.Edge.LEFT, GtkLayerShell.Edge.RIGHT},
        BAR_HEIGHT,
    )

    box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)
    box.get_style_context().add_class("cosmos-bar")

    # Web Apps, Studio, and the shortcuts hint all moved elsewhere (Web
    # Apps/Development became tabs on cosmos-launcher's own bottom-left Apps
    # icon instead of separate bar toggles - direct requirement:
    # "combine apps, web apps, and development apps into the bottom left
    # apps icon" - and the shortcuts hint lives on the left bar) - this
    # bar's own horizontal flow is now just a plain spacer, with only
    # the centered clock (below) actually on it.
    spacer = Gtk.Box()
    box.pack_start(spacer, True, True, 0)

    # The system tray (StatusNotifierItem host) sits at the right end, where
    # stalonetray's reserved gap used to be.
    tray = cosmos_sni_host.TrayHost(ICON_SIZE)
    box.pack_end(tray, False, False, 6)
    window._cosmos_tray = tray

    # The clock moved out of this right-aligned flow entirely - direct
    # request ("put the clock in the top center bar instead") - it's
    # now a centered Gtk.Overlay child below, the same true-centering
    # technique build_bottom_bar() already uses for its own avatar,
    # rather than anything living in this box's own left-to-right flow.

    # Gtk.Overlay with get-child-position rather than halign=CENTER: the
    # clock is placed against the overlay's real width and clamped so it can
    # never run into the bar's edges on a very narrow screen.
    overlay = Gtk.Overlay()
    overlay.add(box)

    clock_label = Gtk.Label(label="")

    CLAMP_MARGIN = 12

    def position_clock(_overlay, widget, allocation):
        if widget is not clock_label:
            return False
        _min_size, natural_size = widget.get_preferred_size()
        allocation.width = natural_size.width
        allocation.height = natural_size.height
        bar_width = _overlay.get_allocated_width()
        x = (bar_width - natural_size.width) // 2

        right_bound = bar_width - CLAMP_MARGIN - natural_size.width
        x = min(x, right_bound)
        x = max(x, CLAMP_MARGIN)

        allocation.x = x
        allocation.y = (BAR_HEIGHT - natural_size.height) // 2
        return True

    overlay.connect("get-child-position", position_clock)
    overlay.add_overlay(clock_label)

    def update_clock():
        clock_label.set_text(time.strftime("%H:%M"))
        return True

    update_clock()
    GLib.timeout_add_seconds(1, update_clock)

    window.add(overlay)
    window.show_all()
    return window


def main():
    screen = Gdk.Screen.get_default()

    provider = Gtk.CssProvider()
    provider.load_from_data(cosmos_display.scale_css(CSS))
    # PRIORITY_USER, not the more usual PRIORITY_APPLICATION - GTK
    # auto-loads ~/.config/gtk-3.0/gtk.css (config/gtk-3.0/gtk.css in
    # this repo) at PRIORITY_USER, which is higher than
    # PRIORITY_APPLICATION regardless of selector specificity (GTK's
    # cascade always resolves priority tier before specificity) - that
    # generic `window { background-color: #0d1220; }` rule was winning
    # over this file's own more specific `window.cosmos-bar` rule,
    # confirmed live via pixel-sampling a real boot (#0d1220 instead of
    # the intended #0a0e16). Matching PRIORITY_USER here ties the two
    # providers, and ties resolve in the order they were added - since
    # gtk-3.0/gtk.css is loaded by GTK itself before this script ever
    # runs, this provider's rule (added second) wins the tie.
    Gtk.StyleContext.add_provider_for_screen(
        screen, provider, Gtk.STYLE_PROVIDER_PRIORITY_USER
    )

    # Creation order matters (see set_bars_hidden): the left bar goes last.
    bars = [
        build_bottom_bar(),
        build_top_bar(),
        build_left_bar(),
    ]
    install_hide_watcher(bars)

    Gtk.main()


if __name__ == "__main__":
    main()
