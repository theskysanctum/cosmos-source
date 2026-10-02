"""A StatusNotifierItem for CosmOS's own tray programs (gamebox-tray).

The Wayland replacement for Gtk.StatusIcon (XEmbed, X11 only). One item exposes an
icon, a tooltip and a menu; the menu is served over `com.canonical.dbusmenu`,
which is what cosmos-bar's tray host (cosmos_sni_host.py) - and every other
StatusNotifier host - draws, since a tray program cannot pop a GTK menu up on its
own without a window to anchor it to.

    item = StatusNotifierItem("gamebox", "GameBox", "applications-games",
                              menu=[("Open GameBox", open_it), None, ("Quit", quit_it)],
                              on_activate=open_it)
    item.start()          # then run the GLib/Gtk main loop

Menu entries: (label, callback), (label, callback, {"checked": bool}) for a
checkbox, or None for a separator. set_menu()/set_icon()/set_title() may be
called at any time.
"""
import os

from gi.repository import Gio, GLib

WATCHER_NAME = "org.kde.StatusNotifierWatcher"
WATCHER_PATH = "/StatusNotifierWatcher"

ITEM_XML = """
<node>
  <interface name="org.kde.StatusNotifierItem">
    <method name="Activate"><arg type="i" direction="in"/><arg type="i" direction="in"/></method>
    <method name="SecondaryAction"><arg type="i" direction="in"/><arg type="i" direction="in"/></method>
    <method name="ContextMenu"><arg type="i" direction="in"/><arg type="i" direction="in"/></method>
    <method name="Scroll"><arg type="i" direction="in"/><arg type="s" direction="in"/></method>
    <property name="Category" type="s" access="read"/>
    <property name="Id" type="s" access="read"/>
    <property name="Title" type="s" access="read"/>
    <property name="Status" type="s" access="read"/>
    <property name="IconName" type="s" access="read"/>
    <property name="Menu" type="o" access="read"/>
    <property name="ItemIsMenu" type="b" access="read"/>
    <property name="ToolTip" type="(sa(iiay)ss)" access="read"/>
    <signal name="NewIcon"/>
    <signal name="NewTitle"/>
    <signal name="NewStatus"><arg type="s"/></signal>
    <signal name="NewToolTip"/>
  </interface>
</node>
"""

MENU_XML = """
<node>
  <interface name="com.canonical.dbusmenu">
    <method name="GetLayout">
      <arg type="i" direction="in"/><arg type="i" direction="in"/><arg type="as" direction="in"/>
      <arg type="u" direction="out"/><arg type="(ia{sv}av)" direction="out"/>
    </method>
    <method name="GetGroupProperties">
      <arg type="ai" direction="in"/><arg type="as" direction="in"/><arg type="a(ia{sv})" direction="out"/>
    </method>
    <method name="GetProperty">
      <arg type="i" direction="in"/><arg type="s" direction="in"/><arg type="v" direction="out"/>
    </method>
    <method name="Event">
      <arg type="i" direction="in"/><arg type="s" direction="in"/><arg type="v" direction="in"/><arg type="u" direction="in"/>
    </method>
    <method name="AboutToShow"><arg type="i" direction="in"/><arg type="b" direction="out"/></method>
    <property name="Version" type="u" access="read"/>
    <property name="TextDirection" type="s" access="read"/>
    <property name="Status" type="s" access="read"/>
    <signal name="LayoutUpdated"><arg type="u"/><arg type="i"/></signal>
  </interface>
</node>
"""


class StatusNotifierItem:
    def __init__(self, item_id, title, icon_name, menu=None, on_activate=None):
        self.item_id, self.title, self.icon_name = item_id, title, icon_name
        self.status = "Active"
        self.on_activate = on_activate
        self.menu_entries = []
        self.revision = 1
        self.conn = None
        self.set_menu(menu or [])

    # -- public --------------------------------------------------------
    def start(self):
        self.bus_name = f"org.kde.StatusNotifierItem-{os.getpid()}-1"
        Gio.bus_own_name(Gio.BusType.SESSION, self.bus_name, Gio.BusNameOwnerFlags.NONE,
                         self.on_bus_acquired, None, None)

    def set_menu(self, entries):
        self.menu_entries = list(entries)
        self.revision += 1
        if self.conn is not None:
            self.conn.emit_signal(None, "/MenuBar", "com.canonical.dbusmenu", "LayoutUpdated",
                                  GLib.Variant("(ui)", (self.revision, 0)))

    def set_icon(self, name):
        self.icon_name = name
        self.emit("NewIcon")

    def set_title(self, title):
        self.title = title
        self.emit("NewTitle")
        self.emit("NewToolTip")

    def emit(self, signal, params=None):
        if self.conn is not None:
            self.conn.emit_signal(None, "/StatusNotifierItem", "org.kde.StatusNotifierItem", signal, params)

    # -- D-Bus ---------------------------------------------------------
    def on_bus_acquired(self, conn, _name):
        self.conn = conn
        conn.register_object("/StatusNotifierItem", Gio.DBusNodeInfo.new_for_xml(ITEM_XML).interfaces[0],
                             self.on_item_call, self.on_item_prop, None)
        conn.register_object("/MenuBar", Gio.DBusNodeInfo.new_for_xml(MENU_XML).interfaces[0],
                             self.on_menu_call, self.on_menu_prop, None)
        # Register now, and again whenever a watcher (re)appears - the bar may start after us.
        Gio.bus_watch_name_on_connection(conn, WATCHER_NAME, Gio.BusNameWatcherFlags.NONE,
                                         self.on_watcher_appeared, None)

    def on_watcher_appeared(self, conn, _name, _owner):
        conn.call(WATCHER_NAME, WATCHER_PATH, WATCHER_NAME, "RegisterStatusNotifierItem",
                  GLib.Variant("(s)", (self.bus_name,)), None, Gio.DBusCallFlags.NONE, 2000, None, None, None)

    def on_item_prop(self, _conn, _sender, _path, _iface, prop):
        values = {
            "Category": GLib.Variant("s", "ApplicationStatus"),
            "Id": GLib.Variant("s", self.item_id),
            "Title": GLib.Variant("s", self.title),
            "Status": GLib.Variant("s", self.status),
            "IconName": GLib.Variant("s", self.icon_name),
            "Menu": GLib.Variant("o", "/MenuBar"),
            "ItemIsMenu": GLib.Variant("b", self.on_activate is None),
            "ToolTip": GLib.Variant("(sa(iiay)ss)", ("", [], self.title, "")),
        }
        return values.get(prop)

    def on_item_call(self, _conn, _sender, _path, _iface, method, _params, invocation):
        if method == "Activate" and self.on_activate:
            GLib.idle_add(self.once, self.on_activate)
        invocation.return_value(None)

    def on_menu_prop(self, _conn, _sender, _path, _iface, prop):
        return {"Version": GLib.Variant("u", 3), "TextDirection": GLib.Variant("s", "ltr"),
                "Status": GLib.Variant("s", "normal")}.get(prop)

    @staticmethod
    def once(callback):
        """idle_add target that runs callback a single time whatever it returns."""
        callback()
        return GLib.SOURCE_REMOVE

    def entry_props(self, index):
        entry = self.menu_entries[index]
        if entry is None:
            return {"type": GLib.Variant("s", "separator")}
        props = {"label": GLib.Variant("s", entry[0])}
        if len(entry) > 2 and "checked" in entry[2]:
            props["toggle-type"] = GLib.Variant("s", "checkmark")
            props["toggle-state"] = GLib.Variant("i", 1 if entry[2]["checked"] else 0)
        return props

    def on_menu_call(self, _conn, _sender, _path, _iface, method, params, invocation):
        if method == "GetLayout":
            children = [GLib.Variant("(ia{sv}av)", (i + 1, self.entry_props(i), []))
                        for i in range(len(self.menu_entries))]
            layout = (0, {"children-display": GLib.Variant("s", "submenu")}, children)
            invocation.return_value(GLib.Variant("(u(ia{sv}av))", (self.revision, layout)))
        elif method == "GetGroupProperties":
            ids = params.unpack()[0] or [i + 1 for i in range(len(self.menu_entries))]
            invocation.return_value(GLib.Variant("(a(ia{sv}))", ([(i, self.entry_props(i - 1)) for i in ids
                                                                    if 0 < i <= len(self.menu_entries)],)))
        elif method == "Event":
            item_id, event_type = params.unpack()[:2]
            if event_type == "clicked" and 0 < item_id <= len(self.menu_entries):
                entry = self.menu_entries[item_id - 1]
                if entry is not None and entry[1]:
                    GLib.idle_add(self.once, entry[1])
            invocation.return_value(None)
        elif method == "AboutToShow":
            invocation.return_value(GLib.Variant("(b)", (False,)))
        else:
            invocation.return_value(None)
