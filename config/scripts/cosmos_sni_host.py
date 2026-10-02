"""System tray for cosmos-bar on Wayland: a StatusNotifierWatcher + host.

X11's XEmbed tray (stalonetray) does not exist here. Applications that want a
tray icon (nm-applet, blueman, the GameBox tray, ...) speak
the StatusNotifierItem D-Bus protocol instead: they register with a
`org.kde.StatusNotifierWatcher` and expose an icon, a title and a menu
(`com.canonical.dbusmenu`). This module is the watcher (owned by the bar
process) and the host (the row of icons the bar shows), including a small
dbusmenu client so right-click menus work.

The watcher claims its bus name only if nobody else owns it; items that register
before the bar is up are picked up because they re-register when the watcher
name appears (that is what the protocol requires of them).
"""
import logging

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Gtk  # noqa: E402
import cosmos_display  # noqa: E402

log = logging.getLogger("cosmos-tray")

WATCHER_NAME = "org.kde.StatusNotifierWatcher"
WATCHER_PATH = "/StatusNotifierWatcher"
ITEM_IFACE = "org.kde.StatusNotifierItem"
MENU_IFACE = "com.canonical.dbusmenu"

WATCHER_XML = """
<node>
  <interface name="org.kde.StatusNotifierWatcher">
    <method name="RegisterStatusNotifierItem"><arg type="s" direction="in" name="service"/></method>
    <method name="RegisterStatusNotifierHost"><arg type="s" direction="in" name="service"/></method>
    <property name="RegisteredStatusNotifierItems" type="as" access="read"/>
    <property name="IsStatusNotifierHostRegistered" type="b" access="read"/>
    <property name="ProtocolVersion" type="i" access="read"/>
    <signal name="StatusNotifierItemRegistered"><arg type="s"/></signal>
    <signal name="StatusNotifierItemUnregistered"><arg type="s"/></signal>
    <signal name="StatusNotifierHostRegistered"/>
    <signal name="StatusNotifierHostUnregistered"/>
  </interface>
</node>
"""


def split_service(arg, sender):
    """Items register as "busname", "busname/path" or just "/path" (sender is
    then the bus name). Returns (busname, objectpath)."""
    if arg.startswith("/"):
        return sender, arg
    if "/" in arg:
        name, rest = arg.split("/", 1)
        return name, "/" + rest
    return arg, "/StatusNotifierItem"


def pixbuf_from_pixmaps(pixmaps, size):
    """IconPixmap is a(iiay): width, height, ARGB32 bytes in network order."""
    best = None
    for width, height, data in pixmaps:
        if width <= 0 or height <= 0 or len(data) < width * height * 4:
            continue
        if best is None or abs(width - size) < abs(best[0] - size):
            best = (width, height, bytes(data))
    if best is None:
        return None
    width, height, data = best
    rgba = bytearray(len(data))
    rgba[0::4], rgba[1::4], rgba[2::4], rgba[3::4] = data[1::4], data[2::4], data[3::4], data[0::4]
    pixbuf = GdkPixbuf.Pixbuf.new_from_bytes(GLib.Bytes.new(bytes(rgba)), GdkPixbuf.Colorspace.RGB, True, 8,
                                            width, height, width * 4)
    if width != size or height != size:
        pixbuf = pixbuf.scale_simple(size, size, GdkPixbuf.InterpType.BILINEAR)
    return pixbuf


class TrayItem(Gtk.Button):
    """One tray icon: reads the item's properties, follows its change signals,
    and turns clicks into Activate / SecondaryAction / its menu."""

    def __init__(self, conn, service, bus_name, path, icon_size):
        super().__init__()
        self.conn, self.service, self.bus_name, self.path = conn, service, bus_name, path
        self.icon_size = icon_size
        self.props = {}
        self.set_relief(Gtk.ReliefStyle.NONE)
        self.image = Gtk.Image()
        self.add(self.image)
        self.connect("button-press-event", self.on_press)
        self.sub_ids = [
            conn.signal_subscribe(bus_name, ITEM_IFACE, None, path, None, Gio.DBusSignalFlags.NONE,
                                  lambda *_a: self.refresh())
        ]
        self.refresh()

    def destroy_item(self):
        for sub in self.sub_ids:
            self.conn.signal_unsubscribe(sub)
        self.destroy()

    def call(self, iface, method, params, reply_type, on_done, path=None):
        def done(conn, res):
            try:
                on_done(conn.call_finish(res))
            except GLib.Error as exc:
                log.debug("%s %s.%s: %s", self.bus_name, iface, method, exc.message)
        self.conn.call(self.bus_name, path or self.path, iface, method, params,
                       GLib.VariantType(reply_type) if reply_type else None,
                       Gio.DBusCallFlags.NONE, 2000, None, done)

    def refresh(self):
        self.call("org.freedesktop.DBus.Properties", "GetAll", GLib.Variant("(s)", (ITEM_IFACE,)), "(a{sv})",
                  self.on_props)

    def on_props(self, reply):
        self.props = {k: v for k, v in reply.unpack()[0].items()}
        theme_path = self.props.get("IconThemePath")
        if theme_path:
            Gtk.IconTheme.get_default().append_search_path(theme_path)
        self.update_icon()
        self.set_tooltip_text(self.tooltip_text())
        self.set_visible(self.props.get("Status") != "Passive")

    def tooltip_text(self):
        tip = self.props.get("ToolTip")            # (icon name, pixmaps, title, text)
        if tip and (tip[2] or tip[3]):
            return tip[2] + (f": {tip[3]}" if tip[2] and tip[3] else tip[3])
        return self.props.get("Title") or self.props.get("Id") or ""

    def update_icon(self):
        attention = self.props.get("Status") == "NeedsAttention"
        name = (self.props.get("AttentionIconName") if attention else None) or self.props.get("IconName")
        pixmaps = (self.props.get("AttentionIconPixmap") if attention else None) or self.props.get("IconPixmap")
        theme = Gtk.IconTheme.get_default()
        if name and name.startswith("/"):
            try:
                cosmos_display.set_image_from_file(self.image, name, self.icon_size, self.icon_size)
                return
            except GLib.Error:
                pass
        if name and theme.has_icon(name):
            self.image.set_from_icon_name(name, Gtk.IconSize.MENU)
            self.image.set_pixel_size(self.icon_size)
            return
        pixbuf = pixbuf_from_pixmaps(pixmaps, self.icon_size) if pixmaps else None
        if pixbuf is not None:
            self.image.set_from_pixbuf(pixbuf)
        else:
            self.image.set_from_icon_name("image-missing", Gtk.IconSize.MENU)
            self.image.set_pixel_size(self.icon_size)

    def on_press(self, _widget, event):
        if event.button == 1 and not self.props.get("ItemIsMenu"):
            self.call(ITEM_IFACE, "Activate", GLib.Variant("(ii)", (int(event.x_root), int(event.y_root))), None,
                      lambda _r: None)
        elif event.button == 2:
            self.call(ITEM_IFACE, "SecondaryAction", GLib.Variant("(ii)", (int(event.x_root), int(event.y_root))),
                      None, lambda _r: None)
        else:
            self.open_menu(Gtk.get_current_event())
        return True

    def open_menu(self, event):
        menu_path = self.props.get("Menu")
        if not menu_path or menu_path == "/":
            self.call(ITEM_IFACE, "ContextMenu", GLib.Variant("(ii)", (0, 0)), None, lambda _r: None)
            return
        def got_layout(reply):
            _revision, layout = reply.unpack()
            menu = self.build_menu(menu_path, layout[2])
            if menu.get_children():
                menu.show_all()
                menu.popup_at_widget(self, Gdk.Gravity.SOUTH_EAST, Gdk.Gravity.NORTH_EAST,
                                    event if isinstance(event, Gdk.Event) else None)
        def about(_reply):
            self.call(MENU_IFACE, "GetLayout", GLib.Variant("(iias)", (0, -1, [])), "(u(ia{sv}av))", got_layout,
                      path=menu_path)
        self.call(MENU_IFACE, "AboutToShow", GLib.Variant("(i)", (0,)), "(b)", about, path=menu_path)

    def build_menu(self, menu_path, children):
        menu = Gtk.Menu()
        for child in children:
            item_id, props, sub = child
            if props.get("visible", True) is False:
                continue
            if props.get("type") == "separator":
                menu.append(Gtk.SeparatorMenuItem())
                continue
            label = props.get("label", "")
            toggle = props.get("toggle-type")
            if toggle in ("checkmark", "radio"):
                entry = Gtk.CheckMenuItem(label=label, use_underline=True)
                entry.set_active(props.get("toggle-state", 0) == 1)
                entry.set_draw_as_radio(toggle == "radio")
            else:
                entry = Gtk.MenuItem(label=label, use_underline=True)
            entry.set_sensitive(props.get("enabled", True))
            if sub or props.get("children-display") == "submenu":
                entry.set_submenu(self.build_menu(menu_path, sub))
            else:
                entry.connect("activate", self.on_menu_activate, menu_path, item_id)
            menu.append(entry)
        return menu

    def on_menu_activate(self, _entry, menu_path, item_id):
        self.call(MENU_IFACE, "Event", GLib.Variant("(isvu)", (item_id, "clicked", GLib.Variant("i", 0),
                                                              int(GLib.get_real_time() / 1000000))), None,
                  lambda _r: None, path=menu_path)


class TrayHost(Gtk.Box):
    """The StatusNotifierWatcher (D-Bus service) and the row of icons."""

    def __init__(self, icon_size=22):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        self.icon_size = icon_size
        self.items = {}       # "busname/path" -> TrayItem
        self.watches = {}
        self.conn = None
        self.registration = None
        self.node = Gio.DBusNodeInfo.new_for_xml(WATCHER_XML)
        self.owner_id = Gio.bus_own_name(Gio.BusType.SESSION, WATCHER_NAME, Gio.BusNameOwnerFlags.NONE,
                                         self.on_bus_acquired, None, self.on_name_lost)

    # -- D-Bus watcher service --------------------------------------
    def on_bus_acquired(self, conn, _name):
        self.conn = conn
        self.registration = conn.register_object(WATCHER_PATH, self.node.interfaces[0], self.on_method_call,
                                                 self.on_get_property, None)
        conn.emit_signal(None, WATCHER_PATH, WATCHER_NAME, "StatusNotifierHostRegistered", None)

    def on_name_lost(self, _conn, _name):
        log.warning("another StatusNotifierWatcher owns the bus; the tray is disabled")

    def on_get_property(self, _conn, _sender, _path, _iface, prop):
        if prop == "RegisteredStatusNotifierItems":
            return GLib.Variant("as", list(self.items))
        if prop == "IsStatusNotifierHostRegistered":
            return GLib.Variant("b", True)
        if prop == "ProtocolVersion":
            return GLib.Variant("i", 0)
        return None

    def on_method_call(self, conn, sender, _path, _iface, method, params, invocation):
        if method == "RegisterStatusNotifierItem":
            self.add_item(sender, params.unpack()[0])
        invocation.return_value(None)

    # -- items ---------------------------------------------------------
    def add_item(self, sender, arg):
        bus_name, path = split_service(arg, sender)
        service = f"{bus_name}{path}"
        if service in self.items:
            return
        item = TrayItem(self.conn, service, bus_name, path, self.icon_size)
        self.items[service] = item
        self.pack_start(item, False, False, 0)
        item.show_all()
        self.watches[service] = Gio.bus_watch_name_on_connection(
            self.conn, bus_name, Gio.BusNameWatcherFlags.NONE, None, lambda *_a, s=service: self.remove_item(s))
        self.conn.emit_signal(None, WATCHER_PATH, WATCHER_NAME, "StatusNotifierItemRegistered",
                              GLib.Variant("(s)", (service,)))

    def remove_item(self, service):
        item = self.items.pop(service, None)
        if item is None:
            return
        Gio.bus_unwatch_name(self.watches.pop(service))
        item.destroy_item()
        self.conn.emit_signal(None, WATCHER_PATH, WATCHER_NAME, "StatusNotifierItemUnregistered",
                              GLib.Variant("(s)", (service,)))
