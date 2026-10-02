#!/usr/bin/env python3
"""CosmOS Settings: everything that is a setting in one place. A sidebar of sections (Controllers with a submenu for lights, rumble, motion, triggers and the
button remapper; Display; Wallpaper; Updates; Remote access; Android; Game packs; Game downloads; Admin activity; About) and the page for the chosen one.
The pages are in cosmos_settings_pages.py; most are the existing CosmOS apps, shown inside this window.

  cosmos-settings                    open Settings
  cosmos-settings --page PAGE[/SUB]  open on a page (controllers, controllers/rumble, display, ...)
  cosmos-settings --list             the page ids
  cosmos-settings --check            build every page without showing anything (exit 1 if one fails)"""
import os
import sys

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gio, GLib, Gtk  # noqa: E402
import cosmos_display  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import cosmos_settings_pages as pages  # noqa: E402

BG = "#0a0e16"
PANEL = "#0d1320"
FG = "#e8ecf4"
ACCENT = "#ff0090"
BORDER = "#2a3a56"

CSS = f"""
window.cosmos-settings {{ background-color: {BG}; }}
window.cosmos-settings, window.cosmos-settings label {{ color: {FG}; }}
.st-nav {{ background-color: {PANEL}; }}
.st-nav row {{ padding: 2px 6px; }}
.st-nav row.st-group {{ opacity: 0.6; }}
.st-group-label {{ font-weight: bold; font-size: 12px; padding: 14px 10px 4px 10px; }}
.st-page-label {{ font-size: 16px; font-weight: bold; padding: 9px 10px; }}
.st-sub-label {{ font-size: 14px; padding: 6px 10px 6px 28px; opacity: 0.85; }}
.st-nav row:selected {{ background-color: alpha({ACCENT}, 0.22); border-left: 3px solid {ACCENT}; }}
.st-nav row:hover {{ background-color: alpha({FG}, 0.06); }}
.st-head {{ font-weight: bold; font-size: 22px; }}
.st-text {{ font-size: 14px; }}
.st-note {{ opacity: 0.78; font-size: 14px; }}
.st-button {{ background-color: {BG}; background-image: none; color: {FG}; border: 1px solid {BORDER}; box-shadow: none; font-weight: bold; padding: 8px 18px; }}
.st-button:focus, .st-button:hover {{ border-color: {ACCENT}; }}
.st-accent {{ background-color: {ACCENT}; background-image: none; color: {BG}; border: none; box-shadow: none; font-weight: bold; padding: 8px 18px; }}
.st-accent:focus {{ box-shadow: 0 0 0 3px {FG}; }}
textview.st-log, textview.st-log text {{ background-color: {BG}; color: {FG}; font-family: monospace; }}
"""


class Settings(Gtk.Window):
    def __init__(self, start=None):
        super().__init__(title="Settings")
        self.get_style_context().add_class("cosmos-settings")
        cosmos_display.size_window(self, 1200, 780)
        self.pages = {}            # page id -> Page (built the first time it is opened)
        self.builders = {}
        self.rows = {}             # (page id, sub or None) -> ListBoxRow

        root = Gtk.Box()
        self.add(root)
        nav_scroll = Gtk.ScrolledWindow()
        nav_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        nav_scroll.set_size_request(270, -1)
        self.nav = Gtk.ListBox()
        self.nav.get_style_context().add_class("st-nav")
        self.nav.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.nav.connect("row-selected", self.on_row)
        nav_scroll.add(self.nav)
        root.pack_start(nav_scroll, False, False, 0)
        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.NONE)
        root.pack_start(self.stack, True, True, 0)

        for item in pages.NAV:
            if isinstance(item, str):
                self.add_row(item, "st-group-label", None, None, selectable=False)
                continue
            page_id, title, builder, subs = item
            self.builders[page_id] = builder
            self.add_row(title, "st-page-label", page_id, None)
            for sub_id, sub_title in subs:
                self.add_row(sub_title, "st-sub-label", page_id, sub_id)
        self.show_page(*(start or ("controllers", None)))

    def add_row(self, text, css, page_id, sub, selectable=True):
        row = Gtk.ListBoxRow()
        row.set_selectable(selectable)
        row.set_activatable(selectable)
        label = Gtk.Label(label=text, xalign=0)
        label.get_style_context().add_class(css)
        row.add(label)
        row._page, row._sub = page_id, sub
        if not selectable:
            row.get_style_context().add_class("st-group")
        self.nav.add(row)
        self.rows[(page_id, sub)] = row

    def page(self, page_id):
        if page_id not in self.pages:
            try:
                built = self.builders[page_id]()
            except Exception as exc:                                        # noqa: BLE001 - one broken page must not take Settings down with it
                box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin=24)
                box.pack_start(pages.note("This page could not be opened.", "st-head"), False, False, 0)
                box.pack_start(pages.note(f"{type(exc).__name__}: {exc}"), False, False, 0)
                built = pages.Page(box)
            self.pages[page_id] = built
            self.stack.add_named(built.widget, page_id)
            built.widget.show_all()
        return self.pages[page_id]

    def show_page(self, page_id, sub=None):
        if page_id not in self.builders:
            page_id, sub = "controllers", None
        page = self.page(page_id)
        self.stack.set_visible_child_name(page_id)
        page.on_show()
        if sub:
            page.show_sub(sub)
        row = self.rows.get((page_id, sub)) or self.rows.get((page_id, None))
        if row is not None and self.nav.get_selected_row() is not row:
            self.nav.select_row(row)

    def on_row(self, _box, row):
        if row is not None and row._page:
            self.show_page(row._page, row._sub)


def parse_start(argv):
    if "--page" in argv and argv.index("--page") + 1 < len(argv):
        page, _, sub = argv[argv.index("--page") + 1].partition("/")
        return page, sub or None
    return None


def check():
    """Builds every page once; prints any that fail."""
    failed = 0
    for item in pages.NAV:
        if isinstance(item, str):
            continue
        page_id, _title, builder, _subs = item
        try:
            builder()
            print(f"ok    {page_id}")
        except Exception as exc:                                            # noqa: BLE001
            failed = 1
            print(f"FAIL  {page_id}: {type(exc).__name__}: {exc}")
    return failed


class SettingsApp(Gtk.Application):
    """One Settings window per session: `cosmos-settings --page X` from anywhere (the tray, another command, the launcher) brings the running window forward
    on that page instead of opening a second one."""

    def __init__(self):
        super().__init__(application_id="org.cosmos.Settings", flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        self.window = None

    def do_startup(self):
        Gtk.Application.do_startup(self)
        provider = Gtk.CssProvider()
        provider.load_from_data(cosmos_display.scale_css(CSS).encode())
        Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

    def do_command_line(self, command_line):
        start = parse_start(command_line.get_arguments()[1:])
        if self.window is None:
            self.window = Settings(start)
            self.window.set_application(self)
            self.window.show_all()
        elif start:
            self.window.show_page(*start)
        self.window.present()
        return 0


def main(argv):
    if "--list" in argv:
        print("\n".join(i[0] for i in pages.NAV if not isinstance(i, str)))
        return 0
    if "--check" in argv:
        provider = Gtk.CssProvider()
        provider.load_from_data(cosmos_display.scale_css(CSS).encode())
        Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        return check()
    return SettingsApp().run([sys.argv[0]] + argv)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
