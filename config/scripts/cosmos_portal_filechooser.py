"""xdg-desktop-portal backend for the file dialog (org.freedesktop.impl.portal.FileChooser), so
every app that asks the portal to open/save a file gets CosmOS's scoped view (your profile's files,
USB drives, other internal drives - never /) instead of a dialog that can browse anywhere.

Apps use the portal when they run sandboxed (Flatpak) or when GTK_USE_PORTAL=1, which the session
sets; GTK3/GTK4 apps and Chromium-based browsers then call it. Qt apps with the qt5ct/qt6ct platform
theme keep their own dialog.

The dialog is the same window as cosmos-files (cosmos_files_ui.Window in chooser mode); what is
chosen can only ever be inside the scope, so an app can't be handed a path outside it. The service
exits after a minute with no dialog open; D-Bus starts it again on the next request.

COSMOS_PORTAL_TEST_PICK=<path> makes each dialog pick that file (or save under that name) by itself
after a moment - only for the tests.
"""
import os
import sys

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gio, GLib, Gtk  # noqa: E402

BUS_NAME = "org.freedesktop.impl.portal.desktop.cosmos"
OBJECT_PATH = "/org/freedesktop/portal/desktop"
IDLE_EXIT_S = 60

FILECHOOSER_XML = """
<node>
  <interface name="org.freedesktop.impl.portal.FileChooser">
    <method name="OpenFile">
      <arg type="o" name="handle" direction="in"/><arg type="s" name="app_id" direction="in"/>
      <arg type="s" name="parent_window" direction="in"/><arg type="s" name="title" direction="in"/>
      <arg type="a{sv}" name="options" direction="in"/>
      <arg type="u" name="response" direction="out"/><arg type="a{sv}" name="results" direction="out"/>
    </method>
    <method name="SaveFile">
      <arg type="o" name="handle" direction="in"/><arg type="s" name="app_id" direction="in"/>
      <arg type="s" name="parent_window" direction="in"/><arg type="s" name="title" direction="in"/>
      <arg type="a{sv}" name="options" direction="in"/>
      <arg type="u" name="response" direction="out"/><arg type="a{sv}" name="results" direction="out"/>
    </method>
    <method name="SaveFiles">
      <arg type="o" name="handle" direction="in"/><arg type="s" name="app_id" direction="in"/>
      <arg type="s" name="parent_window" direction="in"/><arg type="s" name="title" direction="in"/>
      <arg type="a{sv}" name="options" direction="in"/>
      <arg type="u" name="response" direction="out"/><arg type="a{sv}" name="results" direction="out"/>
    </method>
    <property name="version" type="u" access="read"/>
  </interface>
</node>"""
REQUEST_XML = """
<node><interface name="org.freedesktop.impl.portal.Request"><method name="Close"/></interface></node>"""


# --------------------------------------------------------------------------- pure helpers

def bytes_to_path(value):
    """The portal sends paths as NUL-terminated byte arrays."""
    if isinstance(value, (list, tuple)):
        value = bytes(value)
    if isinstance(value, (bytes, bytearray)):
        value = bytes(value).rstrip(b"\0").decode("utf-8", "replace")
    return value or None


def chooser_from_call(method, title, options):
    """The chooser description cosmos_files_ui.Window takes, from one portal call's options."""
    filters = [(name, [(int(kind), pattern) for kind, pattern in rules]) for name, rules in (options.get("filters") or [])]
    current = options.get("current_filter")
    current_filter = None
    if current and filters:
        for i, (name, rules) in enumerate(filters):
            if name == current[0]:
                current_filter = i
    kind = "dir" if (method == "SaveFiles" or options.get("directory")) else "save" if method == "SaveFile" else "open"
    chooser = {"kind": kind, "title": title or None, "multiple": bool(options.get("multiple")),
               "accept_label": options.get("accept_label") or None, "filters": filters, "current_filter": current_filter,
               "current_folder": bytes_to_path(options.get("current_folder"))}
    if method == "SaveFile":
        name = options.get("current_name") or ""
        current_file = bytes_to_path(options.get("current_file"))
        if current_file:
            chooser["current_folder"] = chooser["current_folder"] or os.path.dirname(current_file)
            name = name or os.path.basename(current_file)
        chooser["current_name"] = name
    if method == "SaveFiles":
        chooser["files"] = [bytes_to_path(f) for f in options.get("files") or []]
    return chooser


def uris_for(method, chooser, paths):
    """The `uris` result: the picked paths as file:// URIs; for SaveFiles the chosen folder + each name."""
    if method == "SaveFiles":
        folder = paths[0]
        paths = [os.path.join(folder, os.path.basename(name)) for name in chooser.get("files") or []]
    return [Gio.File.new_for_path(p).get_uri() for p in paths]


# --------------------------------------------------------------------------- the service

class Service:
    def __init__(self):
        import cosmos_files_ui as ui
        self.ui = ui
        ui.install_css()
        self.connection = None
        self.windows = {}          # request handle -> window
        self.request_ids = {}
        self.test_pick = os.environ.get("COSMOS_PORTAL_TEST_PICK")

    def acquired(self, connection, _name):
        self.connection = connection
        node = Gio.DBusNodeInfo.new_for_xml(FILECHOOSER_XML)
        connection.register_object(OBJECT_PATH, node.interfaces[0], self.on_call, self.on_property, None)

    def on_property(self, _c, _s, _p, _i, prop):
        return GLib.Variant("u", 3) if prop == "version" else None

    def on_call(self, _c, _sender, _path, _iface, method, params, invocation):
        if method not in ("OpenFile", "SaveFile", "SaveFiles"):
            invocation.return_dbus_error("org.freedesktop.DBus.Error.UnknownMethod", method)
            return
        handle, _app_id, _parent, title, options = params.unpack()
        chooser = chooser_from_call(method, title, options)

        def done(paths):
            self.forget(handle)
            if not paths:
                invocation.return_value(GLib.Variant("(ua{sv})", (1, {})))
            else:
                results = {"uris": GLib.Variant("as", uris_for(method, chooser, paths))}
                invocation.return_value(GLib.Variant("(ua{sv})", (0, results)))
        chooser["on_done"] = done
        win = self.ui.Window(chooser=chooser)
        self.windows[handle] = win
        try:
            node = Gio.DBusNodeInfo.new_for_xml(REQUEST_XML)
            self.request_ids[handle] = self.connection.register_object(
                handle, node.interfaces[0], lambda *a: self.close_request(handle, a[-1]), None, None)
        except GLib.Error:
            pass
        win.show_all()
        win.present()
        if self.test_pick:
            GLib.timeout_add(400, self.auto_pick, win, chooser["kind"])

    def close_request(self, handle, invocation):
        win = self.windows.get(handle)
        if win:
            win.finish(None)
        invocation.return_value(None)

    def forget(self, handle):
        self.windows.pop(handle, None)
        rid = self.request_ids.pop(handle, None)
        if rid and self.connection:
            self.connection.unregister_object(rid)

    def auto_pick(self, win, kind):
        target = self.test_pick
        if kind == "save":
            win.show_dir(os.path.dirname(target))
            win.name_entry.set_text(os.path.basename(target))
        else:
            win.show_dir(os.path.dirname(target))
            for row in win.store:
                if row[4] == os.path.realpath(target):
                    win.view.get_selection().select_iter(row.iter)
        win.accept()
        return False

    def idle_check(self):
        if not self.windows:
            Gtk.main_quit()
        return True


def main():
    service = Service()
    Gio.bus_own_name(Gio.BusType.SESSION, BUS_NAME, Gio.BusNameOwnerFlags.NONE, service.acquired, None,
                     lambda *_: Gtk.main_quit())
    GLib.timeout_add_seconds(IDLE_EXIT_S, service.idle_check)
    Gtk.main()
    return 0
