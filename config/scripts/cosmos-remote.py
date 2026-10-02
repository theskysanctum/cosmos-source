#!/usr/bin/env python3
"""CosmOS Remote Access - turn SSH on or off, see how to connect, and manage which computers may log in (public keys).
CosmOS has no terminal, so this is how SSH is set up: paste a public key (or pick its .pub file) and it can log in.
Access is by key only - no passwords - for the desktop account. See cosmos_remote.py and config/ssh/50-cosmos.conf.

  cosmos-remote            open the window
  cosmos-remote --demo     the window with pretend state (for looking at it)
"""
import os
import subprocess
import sys
import tempfile
import threading

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402
import cosmos_display  # noqa: E402
import cosmos_settings_link  # noqa: E402

import cosmos_firewall as fw
import cosmos_remote as cr
import cosmos_steam_door as sd
import cosmos_webtop_access as wa

BG = "#0a0e16"
FG = "#e8ecf4"
ACCENT = "#ff0090"
BORDER = "#2a3a56"
CSS = f"""
window.cosmos-remote {{ background-color: {BG}; }}
.cr-title {{ color: {FG}; font-weight: bold; font-size: 22px; }}
.cr-head {{ color: {FG}; font-weight: bold; font-size: 16px; }}
.cr-text {{ color: {FG}; }}
.cr-note {{ color: {FG}; opacity: 0.7; }}
.cr-mono {{ color: {FG}; font-family: monospace; }}
.cr-warn {{ color: {ACCENT}; font-weight: bold; }}
.cr-card {{ background-color: alpha({FG}, 0.04); border: 1px solid {BORDER}; border-radius: 8px; padding: 12px; }}
.cr-button {{ background-color: {BG}; background-image: none; color: {FG}; border: 1px solid {BORDER}; box-shadow: none;
             font-weight: bold; padding: 6px 14px; }}
.cr-button:focus, .cr-button:hover {{ border-color: {ACCENT}; }}
.cr-accent {{ background-color: {ACCENT}; background-image: none; color: {BG}; border: none; box-shadow: none; font-weight: bold; padding: 6px 14px; }}
textview.cr-entry, textview.cr-entry text {{ background-color: {BG}; color: {FG}; }}
textview.cr-entry {{ border: 1px solid {BORDER}; }}
"""


def label(text, css="cr-text", selectable=False, wrap=True):
    lab = Gtk.Label(label=text, xalign=0)
    lab.get_style_context().add_class(css)
    lab.set_line_wrap(wrap)
    lab.set_selectable(selectable)
    return lab


def button(text, cb, accent=False):
    b = Gtk.Button(label=text)
    b.get_style_context().add_class("cr-accent" if accent else "cr-button")
    b.connect("clicked", cb)
    return b


class Window(Gtk.Window):
    def __init__(self, demo=False):
        super().__init__(title="Remote Access")
        self.get_style_context().add_class("cosmos-remote")
        cosmos_display.size_window(self, 820, 700)
        self.demo = demo
        self.auth = os.path.join(tempfile.mkdtemp(prefix="cosmos-remote-demo-"), ".ssh", "authorized_keys") if demo else \
            os.path.expanduser("~/.ssh/authorized_keys")
        if demo:
            for line in ("ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIKf3gLK8pV1M4xqk5x0vTq8oYh3fJ0WlU0uX2a9mZbQr alice@laptop",
                         "ecdsa-sha2-nistp256 AAAAE2VjZHNhLXNoYTItbmlzdHAyNTYAAAAIbmlzdHAyNTYAAABBBGk2mB0zx9t3Z8p0Vq7Yc1mR4sJw0aQe5Ul7n2XcH6dVk3Fq1sPpLwYd9tOeRz0bC8yNfMhQjKuIvTgD3xA= bob@phone"):
                try:
                    cr.add_key(self.auth, line)
                except cr.KeyError_:
                    pass
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin=18)
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.add(root)
        self.add(scroll)
        root.pack_start(label("Remote Access", "cr-title"), False, False, 0)

        # -- on/off
        card = self.card(root)
        self.state_label = label("", "cr-head")
        card.pack_start(self.state_label, False, False, 0)
        card.pack_start(label("SSH lets you sign in to this computer from another one. It uses keys only - there are no passwords to guess - "
                              "and only for this account. Nobody can sign in until you add a key below."), False, False, 0)
        self.toggle_btn = button("Turn SSH on", self.on_toggle, accent=True)
        card.pack_start(self.toggle_btn, False, False, 0)

        # -- how to connect
        card = self.card(root)
        card.pack_start(label("Connect from another computer", "cr-head"), False, False, 0)
        hints = cr.connect_hints()
        card.pack_start(label("Sign in:", "cr-note"), False, False, 0)
        card.pack_start(label(hints["ssh"], "cr-mono", selectable=True), False, False, 0)
        card.pack_start(label("Reach the Webtop (development desktop) in your own browser at http://localhost:3000 with:", "cr-note"), False, False, 0)
        card.pack_start(label(hints["webtop_tunnel"], "cr-mono", selectable=True), False, False, 0)
        card.pack_start(label("The first time you connect, your computer shows a fingerprint. It should be one of these:", "cr-note"), False, False, 0)
        fps = cr.host_fingerprints() if not demo else [("ssh-ed25519", "SHA256:Zk3nVJ0q0bH1Wm4d9t8sYx2E7uKpL5aQcRfN6oT1gXs")]
        for kind, fp in fps or [("", "(this computer has not made its SSH host keys yet - turn SSH on)")]:
            card.pack_start(label(f"{kind}  {fp}".strip(), "cr-mono", selectable=True), False, False, 0)

        # -- what the firewall lets in
        self.fw_label = label("", "cr-note")
        card = self.card(root)
        card.pack_start(label("Firewall", "cr-head"), False, False, 0)
        card.pack_start(self.fw_label, False, False, 0)
        card.pack_start(label("Everything coming in from the network is rejected except the doors above. Turning SSH or the Webtop's network "
                              "access on or off opens or closes its door for you.", "cr-note"), False, False, 0)

        # -- Steam's doors: each follows Steam (open only while it runs, once switched on)
        self.steam = {}
        for door, title, blurb in (
                ("steam-remote-play", "Steam Remote Play", "Lets other devices stream a game from Steam on this computer."),
                ("steam-lan-transfer", "Steam local network game transfers", "Lets other computers on your network copy an installed game from this one.")):
            card = self.card(root)
            card.pack_start(label(title, "cr-head"), False, False, 0)
            state = label("", "cr-text")
            card.pack_start(state, False, False, 0)
            card.pack_start(label(blurb + " Turn it on once: the door then opens by itself when Steam starts and closes when Steam quits (and whenever "
                                  "you turn it off here). Nothing is open while Steam isn't running.", "cr-note"), False, False, 0)
            btn = button("Turn on", lambda _b, d=door: self.on_steam(d), accent=True)
            card.pack_start(btn, False, False, 0)
            self.steam[door] = {"state": state, "button": btn, "armed": False}

        # -- Webtop on the network
        card = self.card(root)
        card.pack_start(label("Webtop from other computers", "cr-head"), False, False, 0)
        self.webtop_state = label("", "cr-text")
        card.pack_start(self.webtop_state, False, False, 0)
        card.pack_start(label("Normally the Webtop can only be opened on this computer. Set a password and turn this on to open it from other computers on your "
                              "network (over HTTPS - your browser will warn about the certificate the first time). It is off until you do, and it can never be "
                              "on without a password.", "cr-note"), False, False, 0)
        self.pw1 = Gtk.Entry(visibility=False, placeholder_text="Password (at least %d characters)" % wa.MIN_LENGTH)
        self.pw2 = Gtk.Entry(visibility=False, placeholder_text="Type it again")
        card.pack_start(self.pw1, False, False, 0)
        card.pack_start(self.pw2, False, False, 0)
        row = Gtk.Box(spacing=8)
        self.pw_button = button("Set password and turn on", self.on_webtop_on, accent=True)
        self.off_button = button("Turn off network access", self.on_webtop_off)
        row.pack_start(self.pw_button, False, False, 0)
        row.pack_start(self.off_button, False, False, 0)
        card.pack_start(row, False, False, 0)
        self.webtop_url = label("", "cr-mono", selectable=True)
        card.pack_start(self.webtop_url, False, False, 0)

        # -- keys
        card = self.card(root)
        card.pack_start(label("Computers allowed to sign in (public keys)", "cr-head"), False, False, 0)
        self.keys_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        card.pack_start(self.keys_box, False, False, 0)
        card.pack_start(label("Add a key: paste the public key (one line, from the other computer's .pub file - never the private key) or pick the file.",
                              "cr-note"), False, False, 6)
        self.entry = Gtk.TextView()
        self.entry.get_style_context().add_class("cr-entry")
        self.entry.set_wrap_mode(Gtk.WrapMode.CHAR)
        self.entry.set_size_request(-1, 70)
        card.pack_start(self.entry, False, False, 0)
        row = Gtk.Box(spacing=8)
        row.pack_start(button("Add key", self.on_add, accent=True), False, False, 0)
        row.pack_start(button("Choose a .pub file...", self.on_choose), False, False, 0)
        card.pack_start(row, False, False, 0)
        self.message = label("", "cr-warn")
        root.pack_start(self.message, False, False, 0)

        self.home = os.path.join(os.path.dirname(os.path.dirname(self.auth)), "webtop-home") if demo else None
        self.refresh()
        if not demo:
            GLib.timeout_add(4000, lambda: (self.refresh_state(), True)[1])

    def card(self, root):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.get_style_context().add_class("cr-card")
        root.pack_start(box, False, False, 0)
        return box

    def say(self, text):
        self.message.set_text(text or "")

    # -- state
    def refresh(self):
        self.refresh_state()
        self.refresh_webtop()
        self.refresh_firewall()
        for child in self.keys_box.get_children():
            self.keys_box.remove(child)
        keys = cr.load_keys(self.auth)
        if not keys:
            self.keys_box.pack_start(label("No keys yet - nobody can sign in over SSH.", "cr-note"), False, False, 0)
        for k in keys:
            row = Gtk.Box(spacing=10)
            text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            text.pack_start(label(k["comment"] or "(no name)", "cr-text"), False, False, 0)
            text.pack_start(label(f"{k['type']}  {k['fingerprint']}", "cr-mono", selectable=True), False, False, 0)
            row.pack_start(text, True, True, 0)
            row.pack_end(button("Remove", lambda _b, fp=k["fingerprint"]: self.on_remove(fp)), False, False, 0)
            self.keys_box.pack_start(row, False, False, 0)
        self.keys_box.show_all()

    def arm_path(self, door):
        return os.path.join(os.path.dirname(os.path.dirname(self.auth)), "armed-" + door) if self.demo else sd.arm_file(door)

    def refresh_steam(self, st=None):
        if st is None:
            st = {"running": True, "doors": {}} if self.demo else fw.status()
        for door, w in self.steam.items():
            armed = sd.is_armed(self.arm_path(door))
            opened = bool(st.get("doors", {}).get(door))
            w["armed"] = armed
            if not st.get("running"):
                text = "The firewall isn't running, so there is nothing to open."
            elif not armed:
                text = "Off."
            elif opened:
                text = "On - Steam is running, so the door is open to your network."
            else:
                text = "On - the door opens when Steam starts." if not sd.steam_running() else "On - opening the door..."
            w["state"].set_text(text)
            w["button"].set_label("Turn off" if armed else "Turn on")
            w["button"].set_sensitive(bool(st.get("running")))

    def on_steam(self, door):
        turning_off = self.steam[door]["armed"]
        (sd.disarm if turning_off else sd.arm)(self.arm_path(door))
        prefix = "(demo) " if self.demo else ""
        self.say(prefix + ("Off. The door closes in a few seconds." if turning_off else "On. The door opens while Steam is running."))
        self.refresh_firewall()
        GLib.timeout_add(7000, lambda: (self.refresh_firewall(), False)[1])        # the watcher acts within a few seconds

    def refresh_firewall(self):
        if self.demo:
            self.fw_label.set_text(fw.describe({"running": True, "zone_ok": True, "open": ["ssh", "cosmos-webtop"],
                                                "doors": {"ssh": True, "webtop": True}, "unexpected": []}))
            self.refresh_steam()
        else:
            st = fw.status()
            self.fw_label.set_text(fw.describe(st))
            self.refresh_steam(st)

    def refresh_webtop(self):
        st = wa.state(self.home)
        addrs = cr.addresses() or ["this-computer"]
        if st["network"]:
            self.webtop_state.set_text("Network access is ON - a password is required.")
            self.webtop_url.set_text(wa.network_url(addrs[0]))
            self.pw_button.set_label("Change password")
        else:
            self.webtop_state.set_text("Network access is OFF - the Webtop opens on this computer only.")
            self.webtop_url.set_text("")
            self.pw_button.set_label("Set password and turn on")
        self.off_button.set_sensitive(st["network"] or st["password_set"])

    def refresh_state(self):
        if self.demo:
            active, enabled = "active", True
        else:
            active, enabled = cr.sshd_state()
        on = active == "active"
        self.state_label.set_text("SSH is ON" if on else "SSH is OFF" if active == "inactive" else "SSH: status unknown")
        self.toggle_btn.set_label("Turn SSH off" if on else "Turn SSH on")
        self.toggle_btn.get_style_context().remove_class("cr-accent" if on else "cr-button")
        self.toggle_btn.get_style_context().add_class("cr-button" if on else "cr-accent")
        self.toggle_on = on

    # -- actions
    def on_toggle(self, *_):
        if self.demo:
            self.say("(demo) nothing changed.")
            return
        turn_on = not self.toggle_on
        self.say("Working...")

        def work():
            ok, msg = cr.set_sshd(turn_on)
            if ok:                                               # the door follows the service
                ok, msg = fw.set_door("ssh", turn_on)
            GLib.idle_add(lambda: (self.say(msg), self.refresh_state(), False)[2])
        threading.Thread(target=work, daemon=True).start()

    def add_text(self, text):
        try:
            _fp, added = cr.add_key(self.auth, text)
        except cr.KeyError_ as exc:
            self.say(str(exc))
            return False
        self.say("" if added else "That key was already allowed.")
        self.refresh()
        return True

    def on_add(self, *_):
        buf = self.entry.get_buffer()
        text = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)
        if self.add_text(text):
            buf.set_text("")

    def on_choose(self, *_):
        dialog = Gtk.FileChooserNative(title="Choose a public key (.pub)", transient_for=self, action=Gtk.FileChooserAction.OPEN)
        flt = Gtk.FileFilter()
        flt.set_name("Public keys (*.pub)")
        flt.add_pattern("*.pub")
        dialog.add_filter(flt)
        if dialog.run() == Gtk.ResponseType.ACCEPT:
            try:
                with open(dialog.get_filename(), encoding="utf-8", errors="replace") as fh:
                    self.add_text(fh.read(cr.MAX_LINE + 1))
            except OSError as exc:
                self.say(f"Couldn't read that file: {exc}")
        dialog.destroy()

    def apply_async(self, done_message):
        def work():
            msg = "" if self.demo else wa.apply_systemd()
            if not msg and not self.demo:
                ok, msg = fw.set_door("webtop", self.opening)
            GLib.idle_add(lambda: (self.say(msg or done_message), self.refresh_webtop(), self.refresh_firewall(), False)[3])
        threading.Thread(target=work, daemon=True).start()

    def on_webtop_on(self, *_):
        a, b = self.pw1.get_text(), self.pw2.get_text()
        if a != b:
            self.say("The two passwords don't match.")
            return
        try:
            wa.activate(a, home=self.home, base_text=wa.read_base())
        except wa.AccessError as exc:
            self.say(str(exc))
            return
        self.pw1.set_text("")
        self.pw2.set_text("")
        self.opening = True
        self.say("Working...")
        self.refresh_webtop()
        self.apply_async("Done. The Webtop restarted with the password and is reachable on the network.")

    def on_webtop_off(self, *_):
        wa.deactivate(self.home)
        self.opening = False
        self.say("Working...")
        self.refresh_webtop()
        self.apply_async("Done. The Webtop is back to this computer only.")

    def on_remove(self, fp):
        cr.remove_key(self.auth, fp)
        self.say("")
        self.refresh()


def main(argv):
    cosmos_settings_link.redirect("remote", argv)
    provider = Gtk.CssProvider()
    provider.load_from_data(cosmos_display.scale_css(CSS).encode())
    Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    win = Window(demo="--demo" in argv)
    win.connect("destroy", Gtk.main_quit)
    win.show_all()
    Gtk.main()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
