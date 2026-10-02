#!/usr/bin/env python3
"""CosmOS Game Packs - squeeze a Windows game into one compressed SquashFS pack that runs under any Proton version.

  Create   pick the game's folder: the exe, the Steam id, ProtonDB's rating, the launch options and Proton version that worked for others, and
           whether a fix exists are all found automatically (cosmos_protondb.py); review and press Create.
  Packs    the packs you have: Play, Info, Delete.
  Proton   the Proton builds installed; import any version from a folder or an archive, or download GE-Proton / Proton-CachyOS releases.

  cosmos-winpack            open the window
The same things without a window: cosmos-winpack-cli.
"""
import os
import sys
import threading
import time

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402
import cosmos_display  # noqa: E402
import cosmos_settings_link  # noqa: E402

import cosmos_proton as cproton
import cosmos_protondb as pdb
import cosmos_winpack as wp

BG, FG, ACCENT, BORDER = "#0a0e16", "#e8ecf4", "#ff0090", "#2a3a56"
CSS = f"""
window.cosmos-winpack {{ background-color: {BG}; }}
.wp-title {{ color: {FG}; font-weight: bold; font-size: 22px; }}
.wp-head {{ color: {FG}; font-weight: bold; font-size: 15px; }}
.wp-text {{ color: {FG}; }}
.wp-note {{ color: {FG}; opacity: 0.7; }}
.wp-warn {{ color: {ACCENT}; font-weight: bold; }}
.wp-card {{ background-color: alpha({FG}, 0.04); border: 1px solid {BORDER}; border-radius: 8px; padding: 12px; }}
.wp-button {{ background-color: {BG}; background-image: none; color: {FG}; border: 1px solid {BORDER}; box-shadow: none; font-weight: bold; padding: 6px 14px; }}
.wp-button:focus, .wp-button:hover {{ border-color: {ACCENT}; }}
.wp-accent {{ background-color: {ACCENT}; background-image: none; color: {BG}; border: none; box-shadow: none; font-weight: bold; padding: 8px 18px; }}
entry, combobox button {{ background-color: {BG}; background-image: none; color: {FG}; border: 1px solid {BORDER}; }}
notebook, notebook > stack, viewport, scrolledwindow, scrolledwindow > viewport > box {{ background-color: {BG}; }}
notebook > header {{ background-color: {BG}; }}
notebook > header > tabs > tab {{ color: {FG}; padding: 6px 14px; }}
notebook > header > tabs > tab:checked {{ border-bottom: 2px solid {ACCENT}; }}
treeview {{ background-color: {BG}; color: {FG}; }}
treeview header button {{ background-image: none; background-color: {BG}; color: {FG}; border: 1px solid {BORDER}; }}
checkbutton label, progressbar text {{ color: {FG}; }}
"""
TIER_LABEL = {"platinum": "Platinum - runs perfectly", "gold": "Gold - runs perfectly after tweaks", "silver": "Silver - runs with minor issues",
              "bronze": "Bronze - runs but often crashes or has issues", "borked": "Borked - doesn't run", "pending": "Pending - too few reports"}


def label(text, css="wp-text", wrap=True, selectable=False):
    lab = Gtk.Label(label=text, xalign=0)
    lab.set_line_wrap(wrap)
    lab.set_selectable(selectable)
    lab.get_style_context().add_class(css)
    return lab


def button(text, cb, accent=False):
    b = Gtk.Button(label=text)
    b.get_style_context().add_class("wp-accent" if accent else "wp-button")
    b.connect("clicked", lambda *_: cb())
    return b


def card(*children):
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
    box.get_style_context().add_class("wp-card")
    for c in children:
        box.pack_start(c, False, False, 0)
    return box


def background(work, done):
    """Run work() off the GTK thread; done(result, error) is called back on it."""
    def run():
        try:
            result, error = work(), None
        except (wp.PackError, cproton.ProtonError, pdb.ProtonDBError, OSError) as exc:
            result, error = None, str(exc)
        GLib.idle_add(lambda: (done(result, error), False)[1])
    threading.Thread(target=run, daemon=True).start()


class Window(Gtk.Window):
    def __init__(self, folder=None):
        super().__init__(title="Game Packs")
        self.get_style_context().add_class("cosmos-winpack")
        cosmos_display.size_window(self, 980, 720)
        self.prepared = None
        self.suggestion_checks = []
        self.builds = []
        self.releases = []
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin=16)
        self.add(outer)
        outer.pack_start(label("Game Packs", "wp-title"), False, False, 0)
        self.notebook = Gtk.Notebook()
        outer.pack_start(self.notebook, True, True, 0)
        self.notebook.append_page(self.build_create(), Gtk.Label(label="Create"))
        self.notebook.append_page(self.build_packs(), Gtk.Label(label="Packs"))
        self.notebook.append_page(self.build_proton(), Gtk.Label(label="Proton"))
        self.connect("destroy", Gtk.main_quit)
        self.refresh_builds()
        self.refresh_packs()
        if folder:
            self.load_folder(folder)

    # ---------------------------------------------------------------- Create
    def build_create(self):
        page = Gtk.ScrolledWindow()
        page.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, margin=10)
        page.add(box)
        row = Gtk.Box(spacing=8)
        self.folder_entry = Gtk.Entry(hexpand=True, placeholder_text="The game's folder")
        self.folder_entry.connect("activate", lambda *_: self.load_folder(self.folder_entry.get_text()))
        row.pack_start(self.folder_entry, True, True, 0)
        row.pack_start(button("Choose folder...", self.choose_folder), False, False, 0)
        row.pack_start(button("Look up", lambda: self.load_folder(self.folder_entry.get_text())), False, False, 0)
        box.pack_start(row, False, False, 0)
        self.status = label("Choose the folder of an installed game. Everything below is filled in for you.", "wp-note")
        box.pack_start(self.status, False, False, 0)

        grid = Gtk.Grid(column_spacing=10, row_spacing=8)
        self.name_entry = Gtk.Entry(hexpand=True)
        self.exe_combo = Gtk.ComboBoxText.new_with_entry()
        self.exe_combo.set_hexpand(True)
        self.appid_entry = Gtk.Entry(hexpand=True, placeholder_text="Steam id (a number) - or leave empty")
        self.appid_note = label("", "wp-note")
        self.proton_combo = Gtk.ComboBoxText()
        self.comp_combo = Gtk.ComboBoxText()
        for c in ("zstd", "xz", "gzip", "lz4", "lzo"):
            self.comp_combo.append_text(c)
        self.comp_combo.set_active(0)
        for r, (text, widget) in enumerate((("Name", self.name_entry), ("Game exe", self.exe_combo), ("Steam id", self.appid_entry),
                                            ("", self.appid_note), ("Proton", self.proton_combo), ("Compression", self.comp_combo))):
            grid.attach(label(text, "wp-note"), 0, r, 1, 1)
            grid.attach(widget, 1, r, 1, 1)
        box.pack_start(grid, False, False, 0)

        self.db_label = label("", "wp-text", selectable=True)
        self.db_card = card(label("ProtonDB", "wp-head"), self.db_label)
        box.pack_start(self.db_card, False, False, 0)
        self.sugg_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self.sugg_card = card(label("Settings other players used", "wp-head"),
                              label("Ticked ones are switched on because several Gold/Platinum reports agree. They are only environment settings, "
                                    "known wrappers and command-line switches - nothing from a report is ever run.", "wp-note"), self.sugg_box)
        box.pack_start(self.sugg_card, False, False, 0)
        self.progress = Gtk.ProgressBar(show_text=True)
        box.pack_start(self.progress, False, False, 0)
        self.create_button = button("Create pack", self.on_create, accent=True)
        box.pack_start(self.create_button, False, False, 0)
        self.result = label("", "wp-text", selectable=True)
        box.pack_start(self.result, False, False, 0)
        return page

    def choose_folder(self):
        dialog = Gtk.FileChooserDialog(title="The game's folder", parent=self, action=Gtk.FileChooserAction.SELECT_FOLDER)
        dialog.add_buttons("Cancel", Gtk.ResponseType.CANCEL, "Choose", Gtk.ResponseType.OK)
        if dialog.run() == Gtk.ResponseType.OK:
            self.load_folder(dialog.get_filename())
        dialog.destroy()

    def load_folder(self, folder):
        folder = os.path.expanduser((folder or "").strip())
        if not os.path.isdir(folder):
            self.status.set_text("That isn't a folder.")
            return
        self.folder_entry.set_text(folder)
        appid = self.appid_entry.get_text().strip()
        self.status.set_text("Looking the game up (Steam, ProtonDB, the fix database) ...")
        background(lambda: wp.prepare(folder, self.name_entry.get_text().strip() or None, int(appid) if appid.isdigit() else None, self.builds),
                   self.on_prepared)

    def on_prepared(self, prepared, error):
        if error:
            self.status.set_text(error)
            return
        self.prepared = prepared
        self.name_entry.set_text(prepared["name"])
        self.exe_combo.remove_all()
        for exe in prepared["executables"]:
            self.exe_combo.append_text(exe)
        self.exe_combo.set_active(0 if prepared["executables"] else -1)
        self.appid_entry.set_text(str(prepared["appid"] or ""))
        if prepared["appid"]:
            self.appid_note.set_text(f"Found from {prepared['appid_source']}.")
        elif prepared["candidates"]:
            self.appid_note.set_text("Not sure which: " + ", ".join(f"{c['name']} = {c['appid']}" for c in prepared["candidates"][:4]) +
                                     " - type the number and press Look up.")
        else:
            self.appid_note.set_text("No Steam game found - a pack still works, without ProtonDB's help.")
        self.proton_combo.remove_all()
        self.proton_combo.append_text("Auto" + (f" - {prepared['proton']} (ProtonDB's favourite)" if prepared["proton"] else " - newest GE-Proton"))
        for b in self.builds:
            self.proton_combo.append_text(b.name)
        self.proton_combo.set_active(0)
        self.show_protondb(prepared)
        self.status.set_text("Review the settings, then press Create pack.")
        self.result.set_text("")

    def show_protondb(self, prepared):
        info = prepared.get("info")
        for child in self.sugg_box.get_children():
            self.sugg_box.remove(child)
        self.suggestion_checks = []
        if not info:
            self.db_label.set_text("Nothing to look up without a Steam id.")
            self.sugg_card.hide()
            return
        lines = []
        s = info.get("summary")
        lines.append(("ProtonDB: " + TIER_LABEL.get(s["tier"], s["tier"]) + f"  ({s['total']} reports, score {s['score']})") if s else "ProtonDB has no rating for this game.")
        fix = info.get("fix")
        lines.append(("Automatic fix available: " + (fix["doc"] or "a protonfixes script") + "\nGE-Proton applies it by itself because the pack sets the Steam id.")
                     if fix else "No special fix is needed or known.")
        a = info.get("analysis") or {}
        if a.get("reports"):
            when = time.strftime("%B %Y", time.localtime(a["newest"])) if a.get("newest") else "?"
            lines.append(f"{a['reports']} community reports found (newest {when}; a snapshot, not live).")
        for v in (a.get("versions") or [])[:3]:
            lines.append(f"Proton {v['version']}: {v['good']} good / {v['bad']} bad" + (f" - you have {v['installed']}" if v["installed"] else ""))
        for p in (a.get("problems") or [])[:2]:
            lines.append("Reported problem: " + p)
        lines += info.get("warnings", [])
        self.db_label.set_text("\n".join(lines))
        suggestions = a.get("suggestions") or []
        for sug in suggestions:
            check = Gtk.CheckButton(label=f"{sug['text']}   ({sug['good']} good of {sug['total']} reports)")
            check.set_active(sug["auto"])
            self.sugg_box.pack_start(check, False, False, 0)
            self.suggestion_checks.append((sug, check))
        self.sugg_card.show_all() if suggestions else self.sugg_card.hide()
        self.sugg_box.show_all()

    def on_create(self):
        if self.prepared is None:
            self.result.set_text("Choose the game's folder first.")
            return
        appid = self.appid_entry.get_text().strip()
        prepared = dict(self.prepared)
        prepared["name"] = self.name_entry.get_text().strip()
        prepared["appid"] = int(appid) if appid.isdigit() else None
        skip = {s["text"] for s, c in self.suggestion_checks if not c.get_active()}
        on = [s for s, c in self.suggestion_checks if c.get_active()]
        prepared["env"] = {s["key"]: s["value"] for s in on if s["kind"] == "env"}
        prepared["wrappers"] = [s["key"] for s in on if s["kind"] == "wrapper"]
        prepared["args"] = [s["key"] for s in on if s["kind"] == "arg"]
        choice = self.proton_combo.get_active_text() or "Auto"
        proton = "auto" if choice.startswith("Auto") else choice
        exe = (self.exe_combo.get_active_text() or "").strip()
        comp = self.comp_combo.get_active_text() or "zstd"
        folder = self.folder_entry.get_text()
        try:
            settings = wp.settings_for(prepared, exe, proton, skip=skip)
        except wp.PackError as exc:
            self.result.set_text(str(exc))
            return
        self.create_button.set_sensitive(False)
        self.progress.set_fraction(0)
        self.result.set_text("Packing ...")

        def work():
            return wp.create(folder, settings, comp=comp, replace=True,
                             progress=lambda p: GLib.idle_add(lambda: (self.progress.set_fraction(p / 100), self.progress.set_text(f"{p}%"), False)[2]))

        def done(result, error):
            self.create_button.set_sensitive(True)
            if error:
                self.result.set_text("Not made: " + error)
                return
            ratio = result["bytes_out"] / max(result["bytes_in"], 1)
            self.result.set_text(f"Made {os.path.basename(result['image'])}: {result['bytes_in'] / 1e6:.0f} MB -> {result['bytes_out'] / 1e6:.0f} MB "
                                 f"({ratio:.0%}). Find it under Packs.")
            self.refresh_packs()
        background(work, done)

    # ---------------------------------------------------------------- Packs
    def build_packs(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin=10)
        self.pack_store = Gtk.ListStore(str, str, str, str, str, str)      # id, name, size, ProtonDB tier, Proton, Steam id
        view = Gtk.TreeView(model=self.pack_store)
        for i, name in enumerate(("Game", "Size", "ProtonDB", "Proton", "Steam id")):
            col = Gtk.TreeViewColumn(name, Gtk.CellRendererText(), text=i + 1)
            col.set_resizable(True)
            view.append_column(col)
        self.pack_view = view
        scroll = Gtk.ScrolledWindow()
        scroll.add(view)
        box.pack_start(scroll, True, True, 0)
        row = Gtk.Box(spacing=8)
        for text, cb, accent in (("Play", self.on_play, True), ("Delete", self.on_delete, False), ("Refresh", self.refresh_packs, False)):
            row.pack_start(button(text, cb, accent), False, False, 0)
        box.pack_start(row, False, False, 0)
        self.pack_status = label("", "wp-note", selectable=True)
        box.pack_start(self.pack_status, False, False, 0)
        return box

    def refresh_packs(self):
        self.packs = wp.list_packs()
        self.pack_store.clear()
        for p in self.packs:
            snap = p.get("protondb") or {}
            self.pack_store.append([p["id"], p["name"], f"{p['size'] / 1e6:.0f} MB", (snap.get("tier") or "-").capitalize(), p["proton"], str(p.get("steam_appid") or "-")])

    def selected_pack(self):
        model, it = self.pack_view.get_selection().get_selected()
        return next((p for p in self.packs if it and p["id"] == model[it][0]), None)

    def on_play(self):
        pack = self.selected_pack()
        if pack is None:
            self.pack_status.set_text("Select a pack first.")
            return
        self.pack_status.set_text(f"Starting {pack['name']} ...")
        lines = []
        background(lambda: wp.launch(pack, self.builds, log=lambda t: (lines.append(t), GLib.idle_add(lambda: (self.pack_status.set_text("\n".join(lines)), False)[1]))),
                   lambda code, err: self.pack_status.set_text(err or f"{pack['name']} closed (exit {code})."))

    def on_delete(self):
        pack = self.selected_pack()
        if pack is None:
            return
        dialog = Gtk.MessageDialog(transient_for=self, modal=True, message_type=Gtk.MessageType.QUESTION, buttons=Gtk.ButtonsType.YES_NO,
                                   text=f"Delete {pack['name']}?", secondary_text="The pack file is removed. Its saves and Wine prefix are kept unless you delete them too.")
        answer = dialog.run()
        dialog.destroy()
        if answer == Gtk.ResponseType.YES:
            wp.delete_pack(pack["id"])
            self.refresh_packs()

    # ---------------------------------------------------------------- Proton
    def build_proton(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin=10)
        self.build_store = Gtk.ListStore(str, str, str)
        view = Gtk.TreeView(model=self.build_store)
        for i, name in enumerate(("Version", "From", "Folder")):
            col = Gtk.TreeViewColumn(name, Gtk.CellRendererText(), text=i)
            col.set_resizable(True)
            view.append_column(col)
        self.build_view = view
        scroll = Gtk.ScrolledWindow(min_content_height=180)
        scroll.add(view)
        box.pack_start(label("Installed Proton versions", "wp-head"), False, False, 0)
        box.pack_start(scroll, True, True, 0)
        row = Gtk.Box(spacing=8)
        for text, cb in (("Import folder...", lambda: self.import_dialog(True)), ("Import archive...", lambda: self.import_dialog(False)),
                         ("Remove selected", self.on_remove_build)):
            row.pack_start(button(text, cb), False, False, 0)
        box.pack_start(row, False, False, 0)
        box.pack_start(label("Download", "wp-head"), False, False, 6)
        drow = Gtk.Box(spacing=8)
        self.source_combo = Gtk.ComboBoxText()
        for s in cproton.SOURCES:
            self.source_combo.append_text(s)
        self.source_combo.set_active(0)
        drow.pack_start(self.source_combo, False, False, 0)
        drow.pack_start(button("List releases", self.on_list_releases), False, False, 0)
        self.release_combo = Gtk.ComboBoxText(hexpand=True)
        drow.pack_start(self.release_combo, True, True, 0)
        drow.pack_start(button("Download and import", self.on_download, True), False, False, 0)
        box.pack_start(drow, False, False, 0)
        self.proton_status = label("", "wp-note", selectable=True)
        box.pack_start(self.proton_status, False, False, 0)
        return box

    def refresh_builds(self):
        self.builds = cproton.discover()
        self.build_store.clear()
        for b in self.builds:
            self.build_store.append([b.name, {"cosmos": "CosmOS", "steam": "Steam", "tool": "Steam tools"}[b.source], b.path])

    def import_dialog(self, folder):
        action = Gtk.FileChooserAction.SELECT_FOLDER if folder else Gtk.FileChooserAction.OPEN
        dialog = Gtk.FileChooserDialog(title="Import a Proton build", parent=self, action=action)
        dialog.add_buttons("Cancel", Gtk.ResponseType.CANCEL, "Import", Gtk.ResponseType.OK)
        path = dialog.get_filename() if dialog.run() == Gtk.ResponseType.OK else None
        dialog.destroy()
        if path:
            self.proton_status.set_text("Importing ...")
            background(lambda: cproton.import_folder(path, replace=True) if folder else cproton.import_archive(path, replace=True),
                       lambda b, err: (self.proton_status.set_text(err or f"Imported {b.name}."), self.refresh_builds()))

    def on_remove_build(self):
        model, it = self.build_view.get_selection().get_selected()
        if it is None:
            return
        if model[it][1] != "CosmOS":
            self.proton_status.set_text("Only builds you imported can be removed here (Steam's are managed by Steam).")
            return
        try:
            cproton.remove(model[it][0])
        except cproton.ProtonError as exc:
            self.proton_status.set_text(str(exc))
        self.refresh_builds()

    def on_list_releases(self):
        source = self.source_combo.get_active_text()
        self.proton_status.set_text("Asking GitHub ...")

        def done(releases, err):
            self.releases = releases or []
            self.release_combo.remove_all()
            for r in self.releases:
                self.release_combo.append_text(f"{r['name']}  ({r['size'] / 1e6:.0f} MB)")
            self.release_combo.set_active(0 if self.releases else -1)
            self.proton_status.set_text(err or f"{len(self.releases)} releases.")
        background(lambda: cproton.list_releases(source), done)

    def on_download(self):
        i = self.release_combo.get_active()
        if i < 0:
            self.proton_status.set_text("List the releases first, then pick one.")
            return
        release = self.releases[i]
        background(lambda: cproton.download_release(release, progress=lambda t: GLib.idle_add(lambda: (self.proton_status.set_text(t), False)[1])),
                   lambda b, err: (self.proton_status.set_text(err or f"Imported {b.name}."), self.refresh_builds()))


def main(argv):
    cosmos_settings_link.redirect("winpack", argv)
    provider = Gtk.CssProvider()
    provider.load_from_data(cosmos_display.scale_css(CSS).encode())
    Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    Window(argv[0] if argv else None).show_all()
    Gtk.main()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
