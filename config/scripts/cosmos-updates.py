#!/usr/bin/env python3
"""CosmOS Updates - one place for Flatpak, AppImage (AM) and system (DNF / bootc) updates.

  cosmos-updates            open the window
  cosmos-updates --tray     run in the background: a tray icon that checks every few hours,
                            says (icon + one notification per new set) when something can be
                            updated, and opens the window on click. Started by the session.

The window lists what each source has, with an Update button per source and "Update everything";
updating runs the command in a cosmos-task window (its output live, and a reply box for any
question it asks) and the list is refreshed when that ends. Checking never changes anything.
What the sources are and aren't able to do is in cosmos_updates.py (notably: the system side
is an image - its packages update by upgrading or rebuilding the image, not by `dnf upgrade`).
"""
import os
import subprocess
import sys
import threading

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gio, GLib, Gtk  # noqa: E402
import cosmos_display  # noqa: E402
import cosmos_settings_link  # noqa: E402

import cosmos_updates as cu

BG = "#0a0e16"
FG = "#e8ecf4"
ACCENT = "#ff0090"
BORDER = "#2a3a56"
CHECK_EVERY_S = 6 * 3600
FIRST_CHECK_S = 45

CSS = f"""
window.cosmos-updates {{ background-color: {BG}; }}
.cu-title {{ color: {FG}; font-weight: bold; font-size: 22px; }}
.cu-head {{ color: {FG}; font-weight: bold; font-size: 16px; }}
.cu-note {{ color: {FG}; opacity: 0.75; }}
.cu-count {{ color: {ACCENT}; font-weight: bold; }}
.cu-card {{ background-color: alpha({FG}, 0.04); border: 1px solid {BORDER}; border-radius: 8px; padding: 12px; }}
treeview.cu-list {{ background-color: {BG}; color: {FG}; }}
.cu-button {{ background-color: {BG}; background-image: none; color: {FG}; border: 1px solid {BORDER};
             box-shadow: none; font-weight: bold; padding: 8px 18px; }}
.cu-button:focus, .cu-button:hover {{ border-color: {ACCENT}; }}
.cu-accent {{ background-color: {ACCENT}; background-image: none; color: {BG}; border: none;
             box-shadow: none; font-weight: bold; padding: 8px 18px; }}
.cu-accent:focus {{ box-shadow: 0 0 0 3px {FG}; }}
"""

DEMO = [
    cu.Result("flatpak", "Flatpak apps", True, [cu.Update("GIMP", new="3.0.4", source="user"),
              cu.Update("Krita", new="5.2.9", source="system")], "2 update(s) available",
              "flatpak update --user -y --noninteractive"),
    cu.Result("appimage", "AppImages (AM)", True, [], "3 AppImage(s) installed - AM checks each one and updates it in place.", "am -u"),
    cu.Result("system", "System (DNF / bootc)", True, [cu.Update("bash", new="5.2.37-1.fc44", source="updates"),
              cu.Update("kernel", new="7.2.8-200.fc44", source="updates")],
              "2 package(s) are newer in the repositories than in this image. The image was built on this machine, so rebuild it to include them."),
]


def task_binary():
    return GLib.find_program_in_path("cosmos-task") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "cosmos-task.py")


class UpdatesWindow(Gtk.Window):
    def __init__(self, app):
        super().__init__(title="Updates", application=app)
        self.app = app
        cosmos_display.size_window(self, 760, 640)
        self.get_style_context().add_class("cosmos-updates")
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        root.set_border_width(18)
        self.add(root)
        heading = Gtk.Label(label="Updates", xalign=0)
        heading.get_style_context().add_class("cu-title")
        root.pack_start(heading, False, False, 0)
        self.status = Gtk.Label(label="", xalign=0)
        self.status.get_style_context().add_class("cu-note")
        root.pack_start(self.status, False, False, 0)

        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.cards = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        scroller.add(self.cards)
        root.pack_start(scroller, True, True, 0)

        row = Gtk.Box(spacing=8)
        self.check_button = Gtk.Button(label="Check now")
        self.check_button.get_style_context().add_class("cu-button")
        self.check_button.connect("clicked", lambda _b: app.check())
        self.all_button = Gtk.Button(label="Update everything")
        self.all_button.get_style_context().add_class("cu-accent")
        self.all_button.connect("clicked", lambda _b: app.update_all())
        row.pack_start(self.check_button, False, False, 0)
        row.pack_start(self.all_button, False, False, 0)
        root.pack_start(row, False, False, 0)
        self.connect("delete-event", lambda w, _e: w.hide() or True)   # closing hides; the tray keeps running
        self.connect("key-press-event", lambda w, e: (w.hide(), True)[1] if e.keyval == Gdk.KEY_Escape else False)

    def show_results(self, results, checking=False):
        for child in self.cards.get_children():
            self.cards.remove(child)
        for result in results:
            self.cards.pack_start(self.build_card(result), False, False, 0)
        total, text = cu.summary(results)
        self.status.set_text("Checking..." if checking else text)
        self.check_button.set_sensitive(not checking)
        self.all_button.set_sensitive(not checking and any(r.apply_command for r in results))
        self.cards.show_all()

    def build_card(self, result):
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        card.get_style_context().add_class("cu-card")
        head = Gtk.Box(spacing=10)
        title = Gtk.Label(label=result.title, xalign=0)
        title.get_style_context().add_class("cu-head")
        head.pack_start(title, True, True, 0)
        if result.count:
            count = Gtk.Label(label=f"{result.count} available")
            count.get_style_context().add_class("cu-count")
            head.pack_start(count, False, False, 0)
        if result.available and result.apply_command:
            button = Gtk.Button(label="Update")
            button.get_style_context().add_class("cu-button")
            button.connect("clicked", lambda _b, r=result: self.app.run_update(r.title, r.apply_command))
            head.pack_start(button, False, False, 0)
        card.pack_start(head, False, False, 0)
        note = Gtk.Label(label=result.error or result.note, xalign=0)
        note.set_line_wrap(True)
        note.get_style_context().add_class("cu-note")
        card.pack_start(note, False, False, 0)
        if result.updates:
            store = Gtk.ListStore(str, str, str)
            for update in result.updates[:200]:
                store.append([update.name, update.new, update.source])
            tree = Gtk.TreeView(model=store)
            tree.get_style_context().add_class("cu-list")
            tree.set_headers_visible(False)
            for index in range(3):
                tree.append_column(Gtk.TreeViewColumn("", Gtk.CellRendererText(), text=index))
            holder = Gtk.ScrolledWindow()
            holder.set_min_content_height(min(160, 26 * len(result.updates) + 6))
            holder.set_max_content_height(160)
            holder.add(tree)
            card.pack_start(holder, False, False, 0)
        return card


class Updates(Gtk.Application):
    def __init__(self):
        super().__init__(application_id="org.cosmos.Updates", flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        self.window = None
        self.results = []
        self.tray = None
        self.notified = ""
        self.demo = False
        self.checking = False

    def do_startup(self):
        Gtk.Application.do_startup(self)
        provider = Gtk.CssProvider()
        provider.load_from_data(cosmos_display.scale_css(CSS).encode())
        Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_USER)

    def do_command_line(self, command_line):
        args = command_line.get_arguments()[1:]
        self.demo = "--demo" in args
        if "--tray" in args and self.tray is None:
            self.start_tray()
            self.hold()
            GLib.timeout_add_seconds(FIRST_CHECK_S, lambda: (self.check(), False)[1])
            GLib.timeout_add_seconds(CHECK_EVERY_S, lambda: (self.check(), True)[1])
        elif "--standalone" in args or self.demo:
            self.open_window()
            self.check()
        else:
            cosmos_settings_link.redirect("updates", args)          # the window is a page of Settings
            self.open_window()                                        # (no Settings here: fall back to this window)
            self.check()
        return 0

    # ---- window --------------------------------------------------------------
    def open_window(self):
        if self.tray is not None and not self.demo and cosmos_settings_link.open_page("updates"):
            return                                                    # the tray's "Open Updates" opens the Settings page
        if self.window is None:
            self.window = UpdatesWindow(self)
            self.window.show_results(self.results, self.checking)
        self.window.show_all()
        self.window.present()

    def check(self):
        if self.checking:
            return
        self.checking = True
        if self.window:
            self.window.show_results(self.results, checking=True)
        threading.Thread(target=self._check_thread, daemon=True).start()

    def _check_thread(self):
        results = DEMO if self.demo else cu.check_all()
        GLib.idle_add(self._checked, results)

    def _checked(self, results):
        self.checking = False
        self.results = results
        if self.window:
            self.window.show_results(results)
        total, text = cu.summary(results)
        if self.tray:
            self.tray.set_icon("software-update-available" if total else "emblem-default")
            self.tray.set_title(f"Updates: {text}")
        signature = cu.signature(results)
        if total and signature != self.notified:
            self.notified = signature
            try:
                subprocess.Popen(["notify-send", "-i", "software-update-available", "Updates available", text],
                                 stderr=subprocess.DEVNULL)
            except OSError:
                pass
        return False

    # ---- running updates ---------------------------------------------------------
    def run_update(self, title, command):
        process = subprocess.Popen([task_binary(), "--title", f"Update: {title}", "--shell", command])
        threading.Thread(target=lambda: (process.wait(), GLib.idle_add(self.check)), daemon=True).start()

    def update_all(self):
        command = cu.apply_all_command(self.results)
        if command:
            self.run_update("everything", command)

    # ---- tray ----------------------------------------------------------------------
    def start_tray(self):
        import cosmos_sni_item as sni
        self.tray = sni.StatusNotifierItem(
            "cosmos-updates", "Updates", "software-update-available",
            menu=[("Open Updates", self.open_window), ("Check now", self.check), ("Update everything", self.update_all),
                  None, ("Quit updater", self.quit)],
            on_activate=self.open_window,
        )
        self.tray.start()


def main():
    return Updates().run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
