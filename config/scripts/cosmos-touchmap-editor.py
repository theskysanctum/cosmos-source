#!/usr/bin/env python3
"""Button mapper for Android games: place touch points on a picture of the screen and say which controller button or key presses each one.

The canvas is the Android screen (any size; positions are fractions of it). Add a Tap (button/key -> one point), a Joystick (a stick or
WASD -> a virtual joystick) or a Look area (a stick -> drag to look around), click "Listen" and press the button or key you want. Profiles are
saved as you go; "Use this profile in Android" turns the mapper on (cosmos-touchmapd, started by the Android app). Engine: cosmos_touchmap.py.
"""
import copy
import math
import subprocess
import sys

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402
import cosmos_display  # noqa: E402
import cosmos_settings_link  # noqa: E402

import cosmos_touchmap as tm

BG, FG, ACCENT, BORDER = "#0a0e16", "#e8ecf4", "#ff0090", "#2a3a56"
CSS = f"""
window.cosmos-tmap {{ background-color: {BG}; }}
.tm-title {{ color: {FG}; font-weight: bold; font-size: 20px; }}
.tm-head {{ color: {FG}; font-weight: bold; }}
.tm-text {{ color: {FG}; }}
.tm-note {{ color: {FG}; opacity: 0.7; }}
.tm-button {{ background-color: {BG}; background-image: none; color: {FG}; border: 1px solid {BORDER}; box-shadow: none; padding: 4px 10px; }}
.tm-button:focus, .tm-button:hover {{ border-color: {ACCENT}; }}
.tm-accent {{ background-color: {ACCENT}; background-image: none; color: {BG}; border: none; box-shadow: none; font-weight: bold; padding: 6px 14px; }}
entry, combobox button, spinbutton {{ background-color: {BG}; background-image: none; color: {FG}; border: 1px solid {BORDER}; }}
checkbutton label, treeview {{ color: {FG}; }}
treeview {{ background-color: {BG}; }}
"""
KIND_NAMES = {"tap": "Tap", "stick": "Joystick", "look": "Look"}
COLORS = {"tap": (1.0, 0.0, 0.56), "stick": (0.3, 0.8, 1.0), "look": (1.0, 0.8, 0.2)}
DIR_LABELS = (("up", "Up"), ("left", "Left"), ("down", "Down"), ("right", "Right"))


def label(text, css="tm-text"):
    lab = Gtk.Label(label=text, xalign=0)
    lab.set_line_wrap(True)
    lab.get_style_context().add_class(css)
    return lab


def button(text, cb, accent=False):
    b = Gtk.Button(label=text)
    b.get_style_context().add_class("tm-accent" if accent else "tm-button")
    b.connect("clicked", lambda *_: cb())
    return b


def friendly(name):
    return name.replace("KEY_", "Key ").replace("BTN_", "").replace("_", " ").title() if name else ""


class Listener:
    """Waits for one press of a controller button or a key. The controllers are claimed meanwhile so cosmos-input doesn't type them."""

    def __init__(self, window, done):
        self.done = done
        self.window = window
        self.devices = []
        self.claimed = tm._claim()
        try:
            import evdev
            from evdev import ecodes
            self.ecodes = ecodes
            for path in evdev.list_devices():
                try:
                    dev = evdev.InputDevice(path)
                except OSError:
                    continue
                keys = dev.capabilities().get(ecodes.EV_KEY, [])
                if ecodes.BTN_SOUTH in keys or ecodes.BTN_GAMEPAD in keys:
                    self.devices.append(dev)
        except ImportError:
            self.ecodes = None
        self.key_handler = window.connect("key-press-event", self.on_key)
        self.timer = GLib.timeout_add(40, self.poll)

    def finish(self, name):
        if self.timer is None:
            return
        GLib.source_remove(self.timer)
        self.timer = None
        self.window.disconnect(self.key_handler)
        for dev in self.devices:
            dev.close()
        tm._unclaim(self.claimed)
        self.done(name)

    def on_key(self, _w, event):
        if event.keyval == Gdk.KEY_Escape:
            self.finish(None)
        else:
            name = tm.code_name(event.hardware_keycode - 8)
            self.finish(name if tm.INPUT_RE.match(name or "") else None)
        return True

    def poll(self):
        for dev in self.devices:
            try:
                for ev in iter(dev.read_one, None):
                    name = self.pad_name(dev, ev)
                    if name:
                        self.finish(name)
                        return False
            except OSError:
                continue
        return self.timer is not None

    def pad_name(self, dev, ev):
        e = self.ecodes
        if ev.type == e.EV_KEY and ev.value == 1:
            name = tm.code_name(ev.code)
            return name if name != "BTN_MODE" else None
        if ev.type == e.EV_ABS:
            name = tm.code_name(ev.code, "abs")
            info = dev.absinfo(ev.code)
            span = (info.max - info.min) or 1
            if name in tm.HAT and ev.value != 0:
                return tm.HAT[name][0 if ev.value < 0 else 1]
            if name in tm.TRIGGERS and (ev.value - info.min) / span > 0.5:
                return tm.TRIGGERS[name]
        return None


class Editor(Gtk.Window):
    def __init__(self):
        super().__init__(title="Android button mapper")
        self.get_style_context().add_class("cosmos-tmap")
        cosmos_display.size_window(self, 1100, 700)
        self.profile = tm.clean_profile({"name": "Untitled"})
        self.selected = None
        self.dragging = False
        self.listener = None
        self.building = False

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin=14)
        self.add(outer)
        top = Gtk.Box(spacing=8)
        top.pack_start(label("Button mapper", "tm-title"), False, False, 0)
        self.profiles = Gtk.ComboBoxText()
        self.profiles.connect("changed", self.on_profile_chosen)
        top.pack_start(self.profiles, False, False, 8)
        top.pack_start(button("New", self.on_new), False, False, 0)
        top.pack_start(button("Delete", self.on_delete), False, False, 0)
        self.use = Gtk.CheckButton(label="Use this profile in Android")
        self.use.connect("toggled", self.on_use)
        top.pack_end(self.use, False, False, 0)
        outer.pack_start(top, False, False, 0)

        body = Gtk.Box(spacing=12)
        outer.pack_start(body, True, True, 0)
        self.canvas = Gtk.DrawingArea()
        self.canvas.set_size_request(640, 360)
        self.canvas.add_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.BUTTON_RELEASE_MASK | Gdk.EventMask.POINTER_MOTION_MASK)
        self.canvas.connect("draw", self.on_draw)
        self.canvas.connect("button-press-event", self.on_press)
        self.canvas.connect("motion-notify-event", self.on_motion)
        self.canvas.connect("button-release-event", self.on_release)
        body.pack_start(self.canvas, True, True, 0)

        side = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        side.set_size_request(340, -1)
        body.pack_start(side, False, False, 0)
        add = Gtk.Box(spacing=6)
        for kind in ("tap", "stick", "look"):
            add.pack_start(button("+ " + KIND_NAMES[kind], lambda k=kind: self.add_element(k)), False, False, 0)
        side.pack_start(add, False, False, 0)
        self.store = Gtk.ListStore(str)
        self.tree = Gtk.TreeView(model=self.store, headers_visible=False)
        self.tree.append_column(Gtk.TreeViewColumn("", Gtk.CellRendererText(), text=0))
        self.tree.get_selection().connect("changed", self.on_row)
        scroll = Gtk.ScrolledWindow()
        scroll.set_min_content_height(140)
        scroll.add(self.tree)
        side.pack_start(scroll, True, True, 0)
        self.form = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        side.pack_start(self.form, False, False, 0)
        side.pack_start(button("Remove this point", self.on_remove), False, False, 0)
        self.note = label("Click the picture to move the selected point. While a game runs, hold the Guide button to leave (it goes Home).", "tm-note")
        outer.pack_start(self.note, False, False, 0)
        self.connect("destroy", Gtk.main_quit)
        self.reload_profiles(tm.load_settings()["profile"])
        self.use.set_active(tm.load_settings()["enabled"])

    # ---- profiles
    def reload_profiles(self, choose=None):
        self.building = True
        self.profiles.remove_all()
        for name in tm.list_profiles():
            self.profiles.append(name, name)
        self.building = False
        names = tm.list_profiles()
        if choose in names:
            self.profiles.set_active_id(choose)
        elif names:
            self.profiles.set_active(0)
        else:
            self.profile = tm.clean_profile({"name": "Untitled"})
            self.refresh()

    def on_profile_chosen(self, _combo):
        name = self.profiles.get_active_id()
        if self.building or not name:
            return
        try:
            self.profile = tm.load_profile(name)
        except (OSError, ValueError):
            return
        self.selected = 0 if self.profile["elements"] else None
        settings = tm.load_settings()
        tm.save_settings({"enabled": settings["enabled"], "profile": name})
        self.refresh()

    def on_new(self):
        dialog = Gtk.Dialog(title="New profile", transient_for=self, modal=True)
        dialog.add_buttons("Cancel", Gtk.ResponseType.CANCEL, "Create", Gtk.ResponseType.OK)
        entry = Gtk.Entry(placeholder_text="A name for the game, e.g. Shooter", activates_default=True)
        dialog.get_content_area().pack_start(entry, True, True, 12)
        dialog.set_default_response(Gtk.ResponseType.OK)
        dialog.show_all()
        if dialog.run() == Gtk.ResponseType.OK:
            name = entry.get_text().strip()
            try:
                tm.profile_path(name)
            except ValueError as exc:
                self.note.set_text(str(exc))
            else:
                tm.save_profile({"name": name, "elements": []})
                self.reload_profiles(name)
        dialog.destroy()

    def on_delete(self):
        name = self.profiles.get_active_id()
        if name:
            tm.delete_profile(name)
            self.selected = None
            self.reload_profiles()

    def on_use(self, _check):
        settings = tm.load_settings()
        tm.save_settings({"enabled": self.use.get_active(), "profile": settings["profile"] or self.profiles.get_active_id() or ""})
        if self.use.get_active():
            try:
                subprocess.Popen(["cosmos-touchmapd"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except OSError:
                self.note.set_text("The mapper service (cosmos-touchmapd) isn't installed.")

    def save(self):
        if self.profiles.get_active_id():
            tm.save_profile(self.profile)

    # ---- elements
    def add_element(self, kind):
        if not self.profiles.get_active_id():
            self.note.set_text("Create a profile first (New).")
            return
        element = {"type": kind, "x": 0.5, "y": 0.5}
        if kind == "tap":
            element["inputs"] = []
        else:
            element.update({"stick": "left" if kind == "stick" else "right", "keys": {}, "radius": 0.15})
            if kind == "look":
                element["sensitivity"] = 1.0
        # a tap needs an input to be valid; keep it out of the saved profile until it has one
        self.profile["elements"].append(element)
        self.selected = len(self.profile["elements"]) - 1
        self.refresh(save=False)

    def on_remove(self):
        if self.selected is not None:
            del self.profile["elements"][self.selected]
            self.selected = min(self.selected, len(self.profile["elements"]) - 1) if self.profile["elements"] else None
            self.refresh()

    def describe(self, element):
        if element["type"] == "tap":
            return "Tap: " + (", ".join(friendly(i) for i in element["inputs"]) or "(press Listen)")
        parts = ([element["stick"] + " stick"] if element.get("stick") else []) + ([" ".join(friendly(k) for k in element["keys"].values())] if element.get("keys") else [])
        return KIND_NAMES[element["type"]] + ": " + (" + ".join(parts) or "(choose a source)")

    def refresh(self, save=True):
        if save:
            self.save()
        self.building = True
        self.store.clear()
        for element in self.profile["elements"]:
            self.store.append([self.describe(element)])
        if self.selected is not None:
            self.tree.get_selection().select_path(Gtk.TreePath(self.selected))
        self.building = False
        self.build_form()
        self.canvas.queue_draw()

    def on_row(self, selection):
        if self.building:
            return
        model, it = selection.get_selected()
        self.selected = model.get_path(it)[0] if it else None
        self.build_form()
        self.canvas.queue_draw()

    def input_row(self, title, current, on_set, append=False):
        row = Gtk.Box(spacing=6)
        row.pack_start(label(title, "tm-note"), False, False, 0)
        shown = label(current or "-", "tm-text")
        row.pack_start(shown, True, True, 0)

        def listen():
            if self.listener:
                return
            shown.set_text("press it now ...")

            def got(name):
                self.listener = None
                if name:
                    on_set(name)
                else:
                    shown.set_text(current or "-")
            self.listener = Listener(self, got)
        row.pack_end(button("Listen", listen), False, False, 0)
        if current and not append:
            row.pack_end(button("Clear", lambda: on_set(None)), False, False, 0)
        return row

    def build_form(self):
        for child in self.form.get_children():
            self.form.remove(child)
        if self.selected is None or self.selected >= len(self.profile["elements"]):
            self.form.show_all()
            return
        element = self.profile["elements"][self.selected]

        def changed(key, value):
            element[key] = value
            self.refresh()
        if element["type"] == "tap":
            for i, name in enumerate(element["inputs"]):
                def clear(new, i=i):
                    if new is None:
                        element["inputs"].pop(i)
                    else:
                        element["inputs"][i] = new
                    self.refresh()
                self.form.pack_start(self.input_row("Input", friendly(name), clear), False, False, 0)
            if len(element["inputs"]) < 6:
                self.form.pack_start(self.input_row("Add input", "", lambda n: (element["inputs"].append(n), self.refresh()), True), False, False, 0)
        else:
            src = Gtk.ComboBoxText()
            for key, text in (("", "No stick (keys only)"), ("left", "Left stick"), ("right", "Right stick")):
                src.append(key, text)
            src.set_active_id(element.get("stick") or "")
            src.connect("changed", lambda c: None if self.building else changed("stick", c.get_active_id() or None))
            self.form.pack_start(label("Controller stick", "tm-note"), False, False, 0)
            self.form.pack_start(src, False, False, 0)
            for direction, title in DIR_LABELS:
                def setkey(new, d=direction):
                    if new is None:
                        element["keys"].pop(d, None)
                    else:
                        element["keys"][d] = new
                    self.refresh()
                self.form.pack_start(self.input_row(title, friendly(element["keys"].get(direction, "")), setkey), False, False, 0)
            self.form.pack_start(self.spin("Size", element["radius"], 0.02, 0.5, 0.01, lambda v: changed("radius", v)), False, False, 0)
            if element["type"] == "look":
                self.form.pack_start(self.spin("Sensitivity", element["sensitivity"], 0.1, 5, 0.1, lambda v: changed("sensitivity", v)), False, False, 0)
        self.form.show_all()

    def spin(self, title, value, low, high, step, on_change):
        row = Gtk.Box(spacing=6)
        row.pack_start(label(title, "tm-note"), False, False, 0)
        spin = Gtk.SpinButton.new_with_range(low, high, step)
        spin.set_value(value)
        spin.set_digits(2)
        spin.connect("value-changed", lambda s: None if self.building else on_change(round(s.get_value(), 3)))
        row.pack_end(spin, False, False, 0)
        return row

    # ---- canvas
    def geometry(self):
        alloc = self.canvas.get_allocation()
        aspect = self.profile["aspect"]
        width = min(alloc.width, alloc.height * aspect)
        return width, width / aspect

    def on_draw(self, _w, cr):
        width, height = self.geometry()
        cr.set_source_rgb(0.05, 0.07, 0.11)
        cr.rectangle(0, 0, width, height)
        cr.fill()
        cr.set_source_rgb(0.16, 0.23, 0.34)
        cr.set_line_width(2)
        cr.rectangle(0, 0, width, height)
        cr.stroke()
        for index, element in enumerate(self.profile["elements"]):
            x, y = element["x"] * width, element["y"] * height
            red, green, blue = COLORS[element["type"]]
            selected = index == self.selected
            radius = 14 if element["type"] == "tap" else element["radius"] * height
            cr.set_source_rgba(red, green, blue, 0.35 if selected else 0.18)
            cr.new_path()
            cr.arc(x, y, radius, 0, 2 * math.pi)
            cr.fill_preserve()
            cr.set_source_rgb(red, green, blue)
            cr.set_line_width(3 if selected else 1.5)
            cr.stroke()
            cr.set_source_rgb(0.9, 0.93, 0.96)
            cr.select_font_face("sans-serif")
            cr.set_font_size(12)
            text = self.describe(element).split(": ", 1)[-1][:22]
            extents = cr.text_extents(text)
            cr.move_to(x - extents.width / 2, y + 4)
            cr.show_text(text)

    def on_press(self, _w, event):
        width, height = self.geometry()
        if event.x > width or event.y > height:
            return
        near = None
        for index, element in enumerate(self.profile["elements"]):
            reach = 14 if element["type"] == "tap" else element["radius"] * height
            d = math.hypot(event.x - element["x"] * width, event.y - element["y"] * height)
            if d <= max(reach, 14) and (near is None or d < near[0]):
                near = (d, index)
        if near:
            self.selected = near[1]
            self.refresh(save=False)
        if self.selected is not None:
            self.dragging = True
            self.move_selected(event.x, event.y, width, height)

    def on_release(self, *_):
        if self.dragging:
            self.dragging = False
            self.save()

    def on_motion(self, _w, event):
        if self.dragging:
            width, height = self.geometry()
            self.move_selected(event.x, event.y, width, height, save=False)

    def move_selected(self, px, py, width, height, save=True):
        element = self.profile["elements"][self.selected]
        element["x"] = min(1.0, max(0.0, round(px / width, 4)))
        element["y"] = min(1.0, max(0.0, round(py / height, 4)))
        self.canvas.queue_draw()
        if save:
            self.save()


def main():
    cosmos_settings_link.redirect("android/mapper")
    style = Gtk.CssProvider()
    style.load_from_data(cosmos_display.scale_css(CSS).encode())
    Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), style, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    Editor().show_all()
    Gtk.main()


if __name__ == "__main__":
    main()
