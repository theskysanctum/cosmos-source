"""The window behind cosmos-files (the scoped file manager) and cosmos-portal-filechooser (the same
view as the file dialog for other apps). See cosmos_places.py for what it may show and how that is
enforced.

Drives that are not mounted yet show up in the list; clicking one mounts it (udisks). The eject
button unmounts. Delete goes to the trash where the drive has one. Keys: Enter open, Backspace up,
F2 rename, Delete, Ctrl+C / X / V, Ctrl+Shift+N new folder, Ctrl+H hidden files.

`chooser` turns it into a file dialog: {"kind": "open"|"dir"|"save", "title", "multiple", "accept_label",
"current_name", "current_folder", "filters": [(name, [(0|1, pattern)])], "current_filter", "on_done": fn(paths|None)}.
"""
import fnmatch
import json
import os
import subprocess
import sys
import tempfile
import threading

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gio, GLib, Gtk  # noqa: E402
import cosmos_display  # noqa: E402

import cosmos_fileops as fo
import cosmos_places as cp

BG = "#0a0e16"
FG = "#e8ecf4"
ACCENT = "#ff0090"
BORDER = "#2a3a56"
RESCAN_MS = 3000

CSS = f"""
window.cosmos-files {{ background-color: {BG}; }}
.cf-title {{ color: {FG}; font-weight: bold; font-size: 20px; }}
.cf-text {{ color: {FG}; }}
.cf-note {{ color: {FG}; opacity: 0.7; }}
.cf-warn {{ color: {ACCENT}; font-weight: bold; }}
list.cf-places, list.cf-places row {{ background-color: {BG}; color: {FG}; }}
list.cf-places row {{ border: 1px solid {BORDER}; border-radius: 8px; padding: 8px 10px; margin-bottom: 6px; }}
list.cf-places row:selected {{ background-color: alpha({ACCENT}, 0.25); border-color: {ACCENT}; }}
.cf-button {{ background-color: {BG}; background-image: none; color: {FG}; border: 1px solid {BORDER};
             box-shadow: none; font-weight: bold; padding: 6px 12px; }}
.cf-button:focus, .cf-button:hover, .cf-button:checked {{ border-color: {ACCENT}; }}
treeview.cf-list {{ background-color: {BG}; color: {FG}; }}
treeview.cf-list:selected {{ background-color: alpha({ACCENT}, 0.35); color: {FG}; }}
treeview.cf-list header button {{ background-color: {BG}; background-image: none; color: {FG}; border: 0; border-bottom: 1px solid {BORDER}; box-shadow: none; }}
scrolledwindow.cf-scroll {{ border: 1px solid {BORDER}; border-radius: 8px; }}
"""


def run_udisks(args):
    """(ok, message) from udisksctl; a polkit rule lets this session mount and unmount drives."""
    try:
        done = subprocess.run(["udisksctl", *args], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"udisksctl isn't available ({exc})."
    text = (done.stdout or done.stderr or "").strip()
    return done.returncode == 0, text


class Window(Gtk.Window):
    def __init__(self, start=None, demo_paths=None, chooser=None):
        super().__init__(title=(chooser or {}).get("title") or "Files")
        self.get_style_context().add_class("cosmos-files")
        cosmos_display.size_window(self, 1000, 640)
        self.demo_paths = demo_paths
        self.chooser = chooser
        self.matcher = None
        self.finished = False
        self.current = None                  # a real directory inside the scope, or None = place list
        self.place = None
        self.clipboard = (None, [])          # ("copy" | "cut", [paths])
        self.show_hidden = False
        self.places, self.scope, self.sig = [], cp.Scope([]), None

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin=14)
        self.add(root)
        top = Gtk.Box(spacing=8)
        root.pack_start(top, False, False, 0)
        self.up_btn = self.button("Back", self.go_up)
        top.pack_start(self.up_btn, False, False, 0)
        self.title = Gtk.Label(label="Files", xalign=0)
        self.title.get_style_context().add_class("cf-title")
        self.title.set_ellipsize(3)
        top.pack_start(self.title, True, True, 6)
        self.tool_buttons = {}
        for text, cb in (("Open", self.open_selected), ("New folder", self.new_folder), ("Copy", lambda *_: self.set_clip("copy")),
                         ("Cut", lambda *_: self.set_clip("cut")), ("Paste", self.paste), ("Rename", self.rename), ("Delete", self.delete)):
            self.tool_buttons[text] = self.button(text, cb)
            top.pack_start(self.tool_buttons[text], False, False, 0)
        self.hidden_btn = Gtk.ToggleButton(label="Hidden")
        self.hidden_btn.get_style_context().add_class("cf-button")
        self.hidden_btn.connect("toggled", self.on_hidden)
        top.pack_start(self.hidden_btn, False, False, 0)

        body = Gtk.Box(spacing=12)
        root.pack_start(body, True, True, 0)
        self.listbox = Gtk.ListBox()
        self.listbox.get_style_context().add_class("cf-places")
        self.listbox.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.listbox.connect("row-activated", self.on_place)
        side = Gtk.ScrolledWindow()
        side.set_size_request(250, -1)
        side.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        side.add(self.listbox)
        body.pack_start(side, False, False, 0)

        right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        body.pack_start(right, True, True, 0)
        self.status = Gtk.Label(label="", xalign=0)
        self.status.get_style_context().add_class("cf-warn")
        self.status.set_line_wrap(True)
        right.pack_start(self.status, False, False, 0)
        self.store = Gtk.ListStore(Gio.Icon, str, str, str, str, bool)      # icon, name, size, modified, path, is_dir
        self.view = Gtk.TreeView(model=self.store)
        self.view.get_style_context().add_class("cf-list")
        self.view.set_headers_visible(True)
        self.view.set_enable_search(True)
        self.view.get_selection().set_mode(Gtk.SelectionMode.MULTIPLE if not chooser or chooser.get("multiple") else Gtk.SelectionMode.SINGLE)
        self.view.connect("row-activated", lambda _v, _p, _c: self.open_selected())
        icon_col = Gtk.TreeViewColumn("")
        cell = Gtk.CellRendererPixbuf()
        cell.set_property("stock-size", Gtk.IconSize.LARGE_TOOLBAR)
        icon_col.pack_start(cell, False)
        icon_col.add_attribute(cell, "gicon", 0)
        self.view.append_column(icon_col)
        for i, (title, expand) in enumerate((("Name", True), ("Size", False), ("Modified", False)), start=1):
            col = Gtk.TreeViewColumn(title, Gtk.CellRendererText(), text=i)
            col.set_expand(expand)
            col.set_sort_column_id(i)
            self.view.append_column(col)
        scroll = Gtk.ScrolledWindow()
        scroll.get_style_context().add_class("cf-scroll")
        scroll.add(self.view)
        right.pack_start(scroll, True, True, 0)

        if chooser:
            self.build_chooser_bar(root)
        self.connect("key-press-event", self.on_key)
        self.connect("destroy", self.on_destroy)
        self.refresh_places(force=True)
        self.open_start(chooser.get("current_folder") if chooser and not start else start)
        GLib.timeout_add(RESCAN_MS, self.on_tick)

    # -- chooser mode (the file dialog other apps get through the portal)
    def build_chooser_bar(self, root):
        chooser = self.chooser
        for name in ("Open", "Copy", "Cut", "Paste", "Rename", "Delete"):
            self.tool_buttons[name].set_no_show_all(True)
            self.tool_buttons[name].hide()
        bar = Gtk.Box(spacing=8)
        root.pack_start(bar, False, False, 0)
        self.name_entry = None
        if chooser["kind"] == "save":
            self.name_entry = Gtk.Entry(text=chooser.get("current_name") or "", hexpand=True)
            self.name_entry.set_placeholder_text("File name")
            self.name_entry.connect("activate", lambda *_: self.accept())
            bar.pack_start(self.name_entry, True, True, 0)
        else:
            bar.pack_start(Gtk.Label(label=""), True, True, 0)
        filters = chooser.get("filters") or []
        self.filter_combo = None
        if filters and chooser["kind"] != "dir":
            self.filter_combo = Gtk.ComboBoxText()
            for name, _rules in filters:
                self.filter_combo.append_text(name)
            self.filter_combo.set_active(min(max(chooser.get("current_filter") or 0, 0), len(filters) - 1))
            self.filter_combo.connect("changed", self.on_filter)
            bar.pack_start(self.filter_combo, False, False, 0)
            self.matcher = make_matcher(filters[self.filter_combo.get_active()][1])
        bar.pack_end(self.button(chooser.get("accept_label") or {"open": "Open", "save": "Save", "dir": "Select"}[chooser["kind"]],
                                 lambda *_: self.accept()), False, False, 0)
        bar.pack_end(self.button("Cancel", lambda *_: self.finish(None)), False, False, 0)
        bar.show_all()

    def on_filter(self, combo):
        self.matcher = make_matcher(self.chooser["filters"][combo.get_active()][1])
        if self.current:
            self.show_dir(self.current)

    def finish(self, paths):
        if self.finished:
            return
        self.finished = True
        done = self.chooser.get("on_done")
        if done:
            done(paths)
        self.destroy()

    def accept(self):
        kind = self.chooser["kind"]
        chosen = self.selected()
        if kind == "save":
            name = (self.name_entry.get_text() or "").strip()
            if not name or os.sep in name or name in (".", ".."):
                self.say("Type a file name (no slashes).")
                return
            if not self.current:
                self.say("Open a folder first.")
                return
            target = os.path.join(self.current, name)
            if os.path.lexists(target):
                ask = Gtk.MessageDialog(transient_for=self, modal=True, message_type=Gtk.MessageType.QUESTION,
                                        buttons=Gtk.ButtonsType.NONE, text=f"Replace \"{name}\"?")
                ask.add_button("Cancel", Gtk.ResponseType.CANCEL)
                ask.add_button("Replace", Gtk.ResponseType.OK)
                answer = ask.run()
                ask.destroy()
                if answer != Gtk.ResponseType.OK:
                    return
            self.finish([self.scope.check(self.current) + os.sep + name])
        elif kind == "dir":
            paths = [p for p, _n, d in chosen if d] or ([self.current] if self.current else [])
            if paths:
                self.finish([self.scope.check(p) for p in paths])
        else:
            files = [p for p, _n, d in chosen if not d]
            if files:
                self.finish([self.scope.check(p) for p in files])
            else:
                self.say("Select a file.")

    def on_destroy(self, *_):
        if self.chooser:
            self.finish(None)
        else:
            Gtk.main_quit()

    # -- helpers
    def button(self, text, cb):
        b = Gtk.Button(label=text)
        b.get_style_context().add_class("cf-button")
        b.connect("clicked", cb)
        return b

    def say(self, text):
        self.status.set_text(text or "")

    def profile_info(self):
        if self.demo_paths:
            return "Skye", self.demo_paths["profile"]
        import cosmos_profiles
        profile = cosmos_profiles.active_profile() or {}
        return profile.get("name") or "My files", cosmos_profiles.profile_path("files")

    # -- places
    def refresh_places(self, force=False):
        name, files_dir = self.profile_info()
        disks = cp.parse_disks(cp.read_lsblk())
        places = cp.all_places(name, files_dir, disks)
        sig = [(p.key, p.title, p.path) for p in places]
        if sig == self.sig and not force:
            return
        self.sig, self.places = sig, places
        self.scope = cp.Scope(places)
        for child in self.listbox.get_children():
            self.listbox.remove(child)
        for place in places:
            row = Gtk.ListBoxRow()
            row.place = place
            box = Gtk.Box(spacing=8)
            icon = {"profile": "user-home", "usb": "drive-removable-media-usb", "disc": "media-optical", "drive": "drive-harddisk"}[place.kind]
            box.pack_start(Gtk.Image.new_from_icon_name(icon, Gtk.IconSize.LARGE_TOOLBAR), False, False, 0)
            text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            title = Gtk.Label(label=place.title, xalign=0)
            title.get_style_context().add_class("cf-text")
            title.set_ellipsize(3)
            sub = Gtk.Label(label=place.subtitle + ("" if place.mounted or place.kind == "profile" else "  -  click to open"), xalign=0)
            sub.get_style_context().add_class("cf-note")
            text.pack_start(title, False, False, 0)
            text.pack_start(sub, False, False, 0)
            box.pack_start(text, True, True, 0)
            if place.kind != "profile" and place.mounted:
                eject = Gtk.Button(label="Eject")
                eject.get_style_context().add_class("cf-button")
                eject.connect("clicked", lambda _b, place=place: self.unmount(place))
                box.pack_end(eject, False, False, 0)
            row.add(box)
            self.listbox.add(row)
        self.listbox.show_all()
        if self.current and not self.scope.locate(self.current):
            self.current, self.place = None, None
            self.say("That drive was removed.")
            self.show_dir(self.places[0].path)

    def on_tick(self):
        if not self.demo_paths:
            self.refresh_places()
        return True

    def on_place(self, _lb, row):
        place = row.place
        if place.mounted:
            self.show_dir(place.path)
        else:
            self.mount(place)

    def mount(self, place):
        self.say(f"Opening {place.title}...")

        def work():
            ok, msg = run_udisks(["mount", "-b", place.device])
            GLib.idle_add(self.after_mount, place, ok, msg)
        threading.Thread(target=work, daemon=True).start()

    def after_mount(self, place, ok, msg):
        if not ok and "already mounted" not in msg.lower():
            self.say(f"Couldn't open {place.title}: {msg}")
            return
        self.say("")
        self.refresh_places(force=True)
        opened = next((p for p in self.places if p.key == place.key and p.mounted), None)
        if opened:
            self.show_dir(opened.path)

    def unmount(self, place):
        def work():
            subprocess.run(["sync"], timeout=120, check=False)
            ok, msg = run_udisks(["unmount", "-b", place.device])
            GLib.idle_add(self.after_unmount, place, ok, msg)
        self.say(f"Ejecting {place.title}...")
        threading.Thread(target=work, daemon=True).start()

    def after_unmount(self, place, ok, msg):
        self.say(f"{place.title} can be removed now." if ok else f"Couldn't eject {place.title}: {msg}")
        self.refresh_places(force=True)

    # -- navigation
    def open_start(self, start):
        target = None
        if start:
            path = Gio.File.new_for_commandline_arg(start).get_path() or start
            target = path
        if target and self.scope.locate(target):
            real = self.scope.check(target)
            self.show_dir(real if os.path.isdir(real) else os.path.dirname(real))
        else:
            if target:
                self.say("That location isn't available in Files - it only shows your files, USB drives and other drives.")
            self.show_dir(self.places[0].path)

    def show_dir(self, path):
        try:
            rows = fo.list_dir(self.scope, path, self.show_hidden)
            if self.matcher:
                rows = [r for r in rows if r[2] or self.matcher(r[0])]
        except cp.OutOfScope:
            self.say("That location isn't available in Files.")
            return
        real = self.scope.check(path)
        self.current = real
        self.place = self.scope.locate(real)[0]
        self.title.set_text(self.scope.relative_title(real))
        self.store.clear()
        for name, full, is_dir, size, mtime in rows:
            info = Gio.File.new_for_path(full).query_info("standard::icon", Gio.FileQueryInfoFlags.NONE, None)
            self.store.append([info.get_icon(), name, "" if is_dir else fo.human_size(size), fo.human_time(mtime), full, is_dir])
        self.up_btn.set_sensitive(real != os.path.realpath(self.place.path))
        self.listbox.unselect_all()
        self.view.grab_focus()

    def go_up(self, *_):
        if self.current and self.place and self.current != os.path.realpath(self.place.path):
            self.show_dir(os.path.dirname(self.current))

    def selected(self):
        model, paths = self.view.get_selection().get_selected_rows()
        return [(model[p][4], model[p][1], model[p][5]) for p in paths]

    def open_selected(self, *_):
        sel = self.selected()
        if len(sel) != 1:
            return
        path, name, is_dir = sel[0]
        if is_dir:
            self.show_dir(path)
        elif self.chooser:
            if self.chooser["kind"] == "save":
                self.name_entry.set_text(name)
            elif self.chooser["kind"] == "open":
                self.accept()
        elif self.scope.locate(path):
            try:
                Gio.AppInfo.launch_default_for_uri(Gio.File.new_for_path(path).get_uri(), None)
                self.say("")
            except GLib.Error as exc:
                self.say(f"Nothing can open that file ({exc.message}).")

    def on_hidden(self, btn):
        self.show_hidden = btn.get_active()
        if self.current:
            self.show_dir(self.current)

    # -- file operations
    def guard(self, fn, *args):
        try:
            return fn(*args)
        except cp.OutOfScope:
            self.say("That isn't inside your files or a drive.")
        except (OSError, ValueError) as exc:
            self.say(str(exc) or "That didn't work.")
        return None

    def set_clip(self, op):
        sel = self.selected()
        if sel:
            self.clipboard = (op, [p for p, _n, _d in sel])
            self.say(f"{len(sel)} item(s) ready to {'copy' if op == 'copy' else 'move'} - open the destination and Paste.")

    def paste(self, *_):
        op, paths = self.clipboard
        if not op or not self.current:
            return
        for path in paths:
            self.guard(fo.copy_into if op == "copy" else fo.move_into, self.scope, path, self.current)
        if op == "cut":
            self.clipboard = (None, [])
        self.show_dir(self.current)

    def new_folder(self, *_):
        if self.current and self.guard(fo.new_folder, self.scope, self.current):
            self.show_dir(self.current)

    def rename(self, *_):
        sel = self.selected()
        if len(sel) != 1:
            return
        path, name, _d = sel[0]
        dialog = Gtk.Dialog(title="Rename", transient_for=self, modal=True)
        entry = Gtk.Entry(text=name, activates_default=True)
        dialog.get_content_area().pack_start(entry, True, True, 12)
        dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
        dialog.add_button("Rename", Gtk.ResponseType.OK)
        dialog.set_default_response(Gtk.ResponseType.OK)
        dialog.show_all()
        if dialog.run() == Gtk.ResponseType.OK:
            self.guard(fo.rename, self.scope, path, entry.get_text().strip())
            self.show_dir(self.current)
        dialog.destroy()

    def delete(self, *_):
        sel = self.selected()
        for path, name, _d in sel:
            if not self.scope.locate(path):
                continue
            try:
                Gio.File.new_for_path(path).trash(None)
            except GLib.Error:
                ask = Gtk.MessageDialog(transient_for=self, modal=True, message_type=Gtk.MessageType.QUESTION,
                                        buttons=Gtk.ButtonsType.NONE, text=f"Delete \"{name}\" permanently?")
                ask.format_secondary_text("This drive has no trash, so it can't be brought back.")
                ask.add_button("Cancel", Gtk.ResponseType.CANCEL)
                ask.add_button("Delete", Gtk.ResponseType.OK)
                if ask.run() == Gtk.ResponseType.OK:
                    self.guard(fo.delete, self.scope, path)
                ask.destroy()
        if sel:
            self.show_dir(self.current)

    def on_key(self, _w, event):
        ctrl = event.state & Gdk.ModifierType.CONTROL_MASK
        shift = event.state & Gdk.ModifierType.SHIFT_MASK
        key = Gdk.keyval_name(event.keyval)
        if isinstance(self.get_focus(), Gtk.Entry):
            return False
        if self.chooser and key in ("F2", "Delete") or (self.chooser and ctrl and key in ("c", "C", "x", "X", "v", "V")):
            return True
        if self.chooser and key == "Escape":
            self.finish(None)
            return True
        if key == "BackSpace":
            self.go_up()
        elif key == "F2":
            self.rename()
        elif key == "Delete":
            self.delete()
        elif ctrl and key in ("c", "C"):
            self.set_clip("copy")
        elif ctrl and key in ("x", "X"):
            self.set_clip("cut")
        elif ctrl and key in ("v", "V"):
            self.paste()
        elif ctrl and shift and key in ("n", "N"):
            self.new_folder()
        elif ctrl and key in ("h", "H"):
            self.hidden_btn.set_active(not self.hidden_btn.get_active())
        else:
            return False
        return True


def install_css():
    provider = Gtk.CssProvider()
    provider.load_from_data(cosmos_display.scale_css(CSS).encode())
    Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)


def make_matcher(rules):
    """A predicate(name) for portal filter rules [(0 glob | 1 mime type, pattern)]; no rules = everything."""
    if not rules:
        return None

    def match(name):
        for kind, pattern in rules:
            if kind == 0 and fnmatch.fnmatchcase(name.lower(), pattern.lower()):
                return True
            if kind == 1:
                guess = Gio.content_type_guess(name, None)[0]
                if Gio.content_type_is_a(guess, pattern):
                    return True
        return False
    return match


def make_demo():
    """Pretend drives: a profile folder, a USB stick, an unmounted internal drive."""
    root = tempfile.mkdtemp(prefix="cosmos-files-demo-")
    profile = os.path.join(root, "profile-files")
    usb = os.path.join(root, "usb")
    for d in ("Documents", "Pictures/Holiday", "Music"):
        os.makedirs(os.path.join(profile, d))
    for d in ("Movies", "Backups"):
        os.makedirs(os.path.join(usb, d))
    for path, size in ((os.path.join(profile, "notes.txt"), 300), (os.path.join(usb, "readme.pdf"), 90000),
                       (os.path.join(usb, "photo.jpg"), 2500000)):
        with open(path, "wb") as fh:
            fh.write(b"x" * size)
    os.symlink("/", os.path.join(usb, "sneaky-link-to-root"))            # must not appear
    lsblk = {"blockdevices": [
        {"name": "nvme0n1", "path": "/dev/nvme0n1", "type": "disk", "size": "1T", "children": [
            {"name": "nvme0n1p3", "path": "/dev/nvme0n1p3", "type": "part", "fstype": "btrfs", "size": "900G", "mountpoints": ["/home", "/"]}]},
        {"name": "sdb", "path": "/dev/sdb", "type": "disk", "rm": True, "hotplug": True, "tran": "usb", "size": "64G", "children": [
            {"name": "sdb1", "path": "/dev/sdb1", "type": "part", "fstype": "exfat", "label": "SANDISK", "size": "64G", "mountpoints": [usb]}]},
        {"name": "sda", "path": "/dev/sda", "type": "disk", "tran": "sata", "size": "2T", "vendor": "Seagate", "children": [
            {"name": "sda1", "path": "/dev/sda1", "type": "part", "fstype": "ntfs", "label": "Games", "size": "2T", "mountpoints": []}]}]}
    path = os.path.join(root, "lsblk.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(lsblk, fh)
    os.environ["COSMOS_LSBLK_JSON"] = path
    return {"profile": profile}
