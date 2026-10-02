#!/usr/bin/env python3
"""The pages of the CosmOS Settings app (cosmos-settings.py). Each page is a function that returns a Page: the widget to show, and a `show_sub(name)` for
pages that have a submenu (Controllers). Most pages are the existing CosmOS apps put inside Settings: their window's content is taken out of the (never
shown) window and placed in the page, so an app has one implementation whether it is opened on its own or from Settings. A page whose package is not
installed says so instead of being missing or breaking the app.

Adding a page = one function here and one line in NAV."""
import importlib.machinery
import importlib.util
import os
import platform
import subprocess
import sys
import threading

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

import cosmos_display  # noqa: E402
import cosmos_display_settings as ds  # noqa: E402

HERE = os.path.dirname(os.path.realpath(__file__))
_modules = {}
_css_installed = set()


class Page:
    def __init__(self, widget, show_sub=None, on_show=None):
        self.widget = widget
        self.show_sub = show_sub or (lambda name: None)
        self.on_show = on_show or (lambda: None)


def note(text, css="st-note", wrap=True):
    label = Gtk.Label(label=text, xalign=0)
    label.set_line_wrap(wrap)
    label.set_selectable(False)
    label.get_style_context().add_class(css)
    return label


def missing(what, package):
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin=24)
    box.pack_start(note(what, "st-head"), False, False, 0)
    box.pack_start(note(f"This part of Settings comes from the {package} package, which is not installed on this system.  ( sudo dnf install {package} )"), False, False, 0)
    return Page(box)


def load_module(filename):
    """The CosmOS app script FILENAME (from the folder Settings itself is in) as a module, or None when it is not installed."""
    if filename in _modules:
        return _modules[filename]
    path = os.path.join(HERE, filename)
    module = None
    if os.path.isfile(path):
        name = "cosmos_app_" + os.path.splitext(filename)[0].replace("-", "_")
        loader = importlib.machinery.SourceFileLoader(name, path)
        spec = importlib.util.spec_from_loader(name, loader)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        loader.exec_module(module)
    _modules[filename] = module
    return module


def install_css(module):
    """The app's own stylesheet (its CSS constant, which its main() would load), once."""
    if module.__name__ in _css_installed or not hasattr(module, "CSS"):
        return
    _css_installed.add(module.__name__)
    provider = Gtk.CssProvider()
    data = module.CSS if isinstance(module.CSS, bytes) else str(module.CSS).encode()
    provider.load_from_data(cosmos_display.scale_css(data))
    Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)


def embed(filename, class_name, package, title, *args, **kwargs):
    """Page holding an existing app's window content. Returns (Page, window instance) - the instance stays alive (hidden), because the widgets' handlers
    belong to it."""
    module = load_module(filename)
    if module is None:
        return missing(title, package), None
    install_css(module)
    window = getattr(module, class_name)(*args, **kwargs)
    child = window.get_child()
    window.remove(child)
    holder = Gtk.ScrolledWindow()
    holder.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
    holder.set_shadow_type(Gtk.ShadowType.NONE)
    holder.add(child)
    return Page(holder), window


def walk(widget):
    yield widget
    if isinstance(widget, Gtk.Container):
        for child in widget.get_children():
            yield from walk(child)


# ---------------------------------------------------------------- pages

CONTROLLER_SUBS = (("light", "Lights"), ("players", "Player lights"), ("rumble", "Rumble"), ("motion", "Motion"), ("triggers", "Triggers"), ("haptics", "Haptics"),
                   ("remap", "Button remapper"))


def controllers():
    page, window = embed("cosmos-controllers.py", "Window", "cosmos-controllers", "Controllers")
    if window is None:
        return page
    module = load_module("cosmos-controllers.py")
    for widget in walk(page.widget):                                     # the app's own title and tab strip: the sidebar replaces them
        if isinstance(widget, Gtk.StackSwitcher):
            widget.set_no_show_all(True)
            widget.hide()
        elif isinstance(widget, Gtk.Label) and widget.get_text() == "Controller Customizer":
            widget.set_no_show_all(True)
            widget.set_visible(False)
    try:
        module.start_daemon()
    except Exception:                                                     # noqa: BLE001 - the daemon is a convenience; the page works without it
        pass

    def show_sub(name):
        if name == "remap":
            window.show_remapper()
            return
        if window.pad is not None:
            window.right.set_visible_child_name("detail")
            window.tabs.set_visible_child_name(name)
    page.show_sub = show_sub
    return page


def display():
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin=24)
    box.pack_start(note("Display", "st-head"), False, False, 0)
    info = note("")
    box.pack_start(info, False, False, 0)
    grid = Gtk.Grid(column_spacing=16, row_spacing=12)
    box.pack_start(grid, False, False, 0)
    resolution = Gtk.ComboBoxText()
    scale = Gtk.ComboBoxText()
    scale.append("auto", "Automatic (chosen for this screen)")
    for value in ds.SCALES:
        scale.append(value, ds.scale_label(value))
    for row, (text, widget) in enumerate((("Resolution", resolution), ("Size of everything", scale))):
        grid.attach(note(text, "st-text", wrap=False), 0, row, 1, 1)
        widget.set_hexpand(True)
        grid.attach(widget, 1, row, 1, 1)
    animations_check = Gtk.CheckButton(label="Play animations (windows and the gridscreen fade in and out)")
    box.pack_start(animations_check, False, False, 0)
    apply_button = Gtk.Button(label="Apply")
    apply_button.get_style_context().add_class("st-accent")
    apply_button.set_halign(Gtk.Align.START)
    box.pack_start(apply_button, False, False, 0)
    status = note("")
    box.pack_start(status, False, False, 0)
    box.pack_start(note("The choice is kept for the next start (~/.config/cosmos/display.conf). 'Size of everything' makes text, buttons and pictures bigger "
                        "or smaller on the same screen; on a 4K TV it is chosen for you."), False, False, 0)
    confirm = Gtk.Box(spacing=12)
    confirm_label = note("", "st-text", wrap=False)
    keep = Gtk.Button(label="Keep this")
    revert = Gtk.Button(label="Go back")
    for b in (keep, revert):
        b.get_style_context().add_class("st-button")
    confirm.pack_start(confirm_label, False, False, 0)
    confirm.pack_start(keep, False, False, 0)
    confirm.pack_start(revert, False, False, 0)
    confirm.set_no_show_all(True)
    box.pack_start(confirm, False, False, 0)

    state = {"modes": [], "before": None, "timer": None, "left": 0, "syncing_animations": False}

    def ipc(command):
        try:
            import cosmos_wm_ipc
        except ImportError:
            return None
        return cosmos_wm_ipc.request(command, timeout=3.0)

    def refresh(*_):
        modes = ds.parse_modes(ipc("modes"))
        current = ds.parse_scale_reply(ipc("scale"))
        state["modes"] = modes
        resolution.remove_all()
        if current is None:
            info.set_text("The desktop compositor isn't answering, so the display can't be changed from here.")
            apply_button.set_sensitive(False)
            animations_check.set_sensitive(False)
            return
        apply_button.set_sensitive(True)
        name, pixels, factor, logical = current
        resolution.set_sensitive(bool(modes))
        extra = "" if modes else "  This screen doesn't offer a choice of resolutions (it is a virtual or fixed-size display); only the size of everything can be changed."
        info.set_text(f"{name}: {pixels[0]} x {pixels[1]} pixels, drawn at {int(factor * 100)}% - room for {logical[0]} x {logical[1]}.{extra}")
        now = ds.current_size(modes) or pixels
        for width, height, _mode in ds.sizes(modes):
            resolution.append(f"{width}x{height}", f"{width} x {height}")
        resolution.set_active_id(f"{now[0]}x{now[1]}")
        saved = ds.read_conf()["scale"]
        scale.set_active_id(saved if saved in ds.SCALES else "auto")
        animations_check.set_sensitive(True)
        state["syncing_animations"] = True                    # reflecting the compositor's live state must not itself trigger a write
        animations_check.set_active((ipc("animations") or "").strip() != "off")
        state["syncing_animations"] = False

    def end_confirm():
        if state["timer"]:
            GLib.source_remove(state["timer"])
            state["timer"] = None
        confirm.hide()

    def tick():
        state["left"] -= 1
        if state["left"] <= 0:
            go_back()
            return False
        confirm_label.set_text(f"Keep these display settings?  Going back in {state['left']} s")
        return True

    def on_apply(*_):
        target = resolution.get_active_id()
        wanted_scale = scale.get_active_id()
        before = ds.parse_scale_reply(ipc("scale"))
        state["before"] = {"size": ds.current_size(state["modes"]), "scale": before[2] if before else 1.0, "conf": ds.read_conf()}
        if target and target != "x".join(map(str, state["before"]["size"] or ())):
            reply = ipc(f"mode {target}") or ""
            if reply.startswith("error"):
                status.set_text("That resolution can't be used on this screen.")
                return
        reply = ipc(f"scale {wanted_scale}") or ""
        if reply.startswith("error"):
            status.set_text("That size can't be used.")
            return
        status.set_text("")
        state["left"] = 15
        confirm_label.set_text("Keep these display settings?  Going back in 15 s")
        confirm.show_all()
        state["timer"] = GLib.timeout_add_seconds(1, tick)
        state["chosen"] = (target, wanted_scale)

    def on_keep(*_):
        end_confirm()
        target, wanted_scale = state["chosen"]
        ds.write_conf(mode=target, scale=None if wanted_scale == "auto" else wanted_scale)
        status.set_text("Saved.")
        refresh()

    def go_back(*_):
        end_confirm()
        before = state["before"]
        if before:
            if before["size"]:
                ipc("mode %dx%d" % before["size"])
            ipc(f"scale {before['scale']}")
        status.set_text("Went back to the previous display settings.")
        refresh()

    def on_animations_toggled(*_):
        if state["syncing_animations"]:
            return                                             # refresh() setting the checkbox to match reality, not a real click
        on = animations_check.get_active()
        ipc("animations on" if on else "animations off")        # takes effect at once
        ds.write_conf(animations="on" if on else "off")         # and is kept for the next start - no separate Apply/Save step, unlike resolution
        status.set_text("")                                     # /scale, since turning this off can never leave the screen unusable

    animations_check.connect("toggled", on_animations_toggled)
    apply_button.connect("clicked", on_apply)
    keep.connect("clicked", on_keep)
    revert.connect("clicked", go_back)
    refresh()
    scroller = Gtk.ScrolledWindow()
    scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
    scroller.add(box)
    return Page(scroller, on_show=refresh)


def wallpaper():
    page, window = embed("cosmos-wallpaper.py", "WallpaperPicker", "cosmos-shell", "Wallpaper")
    return page


def updates():
    """The Updates page: the Updates application object is made here without being run (the tray daemon is `cosmos-updates --tray`, a separate process)."""
    module = load_module("cosmos-updates.py")
    if module is None:
        return missing("Updates", "cosmos-updates")
    install_css(module)
    app = module.Updates()
    app.set_application_id("org.cosmos.Settings.Updates")      # not the tray daemon's own id
    try:
        app.register(None)                                       # a window can only be added to a started application
    except Exception:                                            # noqa: BLE001
        pass
    window = module.UpdatesWindow(app)
    app.window = window
    child = window.get_child()
    window.remove(child)
    window.show_results([], False)

    def on_show():
        if not app.checking and not app.results:
            app.check()
    page = Page(child, on_show=on_show)
    page._keep = (app, window)
    return page


def remote():
    page, window = embed("cosmos-remote.py", "Window", "cosmos-remote", "Remote access")
    return page


def android():
    """Android, with the touch button mapper as its submenu page (the "Button mapper..." button opens it here instead of in a window of its own)."""
    page, window = embed("cosmos-android.py", "Window", "cosmos-android", "Android")
    if window is None:
        return page
    stack = Gtk.Stack()
    stack.set_transition_type(Gtk.StackTransitionType.NONE)
    stack.add_named(page.widget, "android")
    editor_page, editor = embed("cosmos-touchmap-editor.py", "Editor", "cosmos-android", "Button mapper")
    stack.add_named(editor_page.widget, "mapper")
    window.open_mapper = lambda: stack.set_visible_child_name("mapper")
    stack.show_all()
    result = Page(stack, show_sub=lambda name: stack.set_visible_child_name("mapper" if name == "mapper" else "android"),
                  on_show=lambda: stack.set_visible_child_name("android"))
    result._keep = (window, editor)
    return result


def vm():
    page, window = embed("cosmos-vm-manager.py", "VMManagerWindow", "cosmos-vm-manager", "Virtual machines")
    return page


def winpack():
    page, window = embed("cosmos-winpack.py", "Window", "cosmos-winpack", "Game packs")
    return page


def admin():
    page, window = embed("cosmos-admin.py", "Window", "cosmos-kronos", "Admin activity")
    return page


def downloads():
    """Game downloads (the GameGrubber module): whether it is on, and turning it on or off."""
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin=24)
    box.pack_start(note("Game downloads", "st-head"), False, False, 0)
    state = note("")
    box.pack_start(state, False, False, 0)
    box.pack_start(note("Game downloads adds the Games and BIOS lists to the CosmOS Shop, which fetch games through your own Real-Debrid account. It is an "
                        "optional extra: nothing is installed until you turn it on, and turning it off removes it again."), False, False, 0)
    button = Gtk.Button()
    button.get_style_context().add_class("st-accent")
    button.set_halign(Gtk.Align.START)
    box.pack_start(button, False, False, 0)
    log = Gtk.TextView(editable=False, cursor_visible=False, wrap_mode=Gtk.WrapMode.WORD_CHAR)
    log.get_style_context().add_class("st-log")
    scroller = Gtk.ScrolledWindow()
    scroller.set_min_content_height(160)
    scroller.add(log)
    box.pack_start(scroller, True, True, 0)

    try:
        import cosmos_modules as modules
    except ImportError:
        return missing("Game downloads", "cosmos-common")
    busy = {"on": False}

    def refresh(*_):
        installed = modules.is_installed("gamegrubber")
        state.set_text("Game downloads is ON." if installed else "Game downloads is OFF.")
        button.set_label("Turn off" if installed else "Turn on")
        button.set_sensitive(not busy["on"])

    def add_line(text):
        buffer = log.get_buffer()
        buffer.insert(buffer.get_end_iter(), text + "\n")
        log.scroll_to_iter(buffer.get_end_iter(), 0, False, 0, 0)
        return False

    def worker(turn_on):
        ok = modules.install("gamegrubber", log=lambda l: GLib.idle_add(add_line, l)) if turn_on else modules.uninstall("gamegrubber", log=lambda l: GLib.idle_add(add_line, l))
        GLib.idle_add(finished, ok, turn_on)

    def finished(ok, turn_on):
        busy["on"] = False
        add_line(("Done." if ok else "That didn't work - see the lines above. (Turning it on needs `gh` signed in to an account that can read the modules repository.)"))
        refresh()
        return False

    def on_click(*_):
        turn_on = not modules.is_installed("gamegrubber")
        busy["on"] = True
        button.set_sensitive(False)
        add_line("Turning it on ..." if turn_on else "Turning it off ...")
        threading.Thread(target=worker, args=(turn_on,), daemon=True).start()

    button.connect("clicked", on_click)
    refresh()
    return Page(box, on_show=refresh)


def about():
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, margin=24)
    box.pack_start(note("About this system", "st-head"), False, False, 0)

    def rpm(name):
        try:
            out = subprocess.run(["rpm", "-q", "--qf", "%{VERSION}-%{RELEASE}", name], capture_output=True, text=True, timeout=10)
            return out.stdout.strip() if out.returncode == 0 else "not installed"
        except (OSError, subprocess.SubprocessError):
            return "unknown"

    def os_release():
        try:
            with open("/etc/os-release", encoding="utf-8") as fh:
                for line in fh:
                    if line.startswith("PRETTY_NAME="):
                        return line.split("=", 1)[1].strip().strip('"')
        except OSError:
            pass
        return platform.system()

    image = "unknown"
    try:
        import cosmos_updates as cu
        kind, ref = cu.bootc_source()
        image = f"{ref} ({'a registry image, so it can be updated' if kind == 'registry' else 'built on this machine' if kind == 'local' else 'not an image system'})" if kind else "not an image system"
    except Exception:                                                     # noqa: BLE001
        pass
    rows = (("System", os_release()), ("CosmOS packages", rpm("cosmos-common")), ("Image", image), ("Kernel", platform.release()),
            ("Screen", "%d x %d" % cosmos_display.logical_size()))
    grid = Gtk.Grid(column_spacing=24, row_spacing=8)
    for i, (name, value) in enumerate(rows):
        grid.attach(note(name, "st-text", wrap=False), 0, i, 1, 1)
        v = note(value, "st-note")
        v.set_selectable(True)
        grid.attach(v, 1, i, 1, 1)
    box.pack_start(grid, False, False, 0)
    return Page(box)


# (group heading | (page id, title, builder, submenu)) in the order the sidebar shows them
NAV = (
    "Devices",
    ("controllers", "Controllers", controllers, CONTROLLER_SUBS),
    "System",
    ("display", "Display", display, ()),
    ("wallpaper", "Wallpaper", wallpaper, ()),
    ("updates", "Updates", updates, ()),
    ("vm", "Virtual machines", vm, ()),
    ("about", "About", about, ()),
    "Network",
    ("remote", "Remote access", remote, ()),
    "Games",
    ("android", "Android", android, (("mapper", "Button mapper"),)),
    ("winpack", "Game packs", winpack, ()),
    ("downloads", "Game downloads", downloads, ()),
    "Security",
    ("admin", "Admin activity", admin, ()),
)
