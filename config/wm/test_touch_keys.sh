#!/bin/bash
# Headless check of the compositor's mapper interface: a subscriber gets the keys typed in its app's window (and the window gets none of
# them), and can put touch points into that window. Run inside the cosmos-wl-dev container (see test_launcher_launch.sh for the flags).
# Expected: "touch down 3 (0.5,0.25)" style lines from the client, and "keys: [30 1, 30 0]" from the subscriber.
mkdir -p /tmp/cw /tmp/wlr/out && cp /src/config/wm/cosmos-wm.c /tmp/cw/ && cd /tmp/cw
wayland-scanner server-header "$(pkg-config --variable=pkgdatadir wayland-protocols)/stable/xdg-shell/xdg-shell.xml" xdg-shell-protocol.h
wayland-scanner server-header /usr/share/wlr-protocols/unstable/wlr-layer-shell-unstable-v1.xml wlr-layer-shell-unstable-v1-protocol.h
gcc -O2 -Wall -Wno-unused-parameter -DWLR_USE_UNSTABLE -I. -o /tmp/wlr/out/cosmos-wm cosmos-wm.c $(pkg-config --cflags --libs wlroots-0.20 wayland-server xkbcommon pangocairo pixman-1 libdrm) 2>&1 | grep -E "error|warning"
export XDG_RUNTIME_DIR=/tmp/rt; mkdir -p $XDG_RUNTIME_DIR; chmod 700 $XDG_RUNTIME_DIR
export WLR_BACKENDS=headless WLR_RENDERER=pixman WLR_HEADLESS_OUTPUTS=1 COSMOS_WM_TEST=1 COSMOS_SESSION_KIND=desktop
/tmp/wlr/out/cosmos-wm > /tmp/wm.log 2>&1 & WM=$!
sleep 1; export WAYLAND_DISPLAY=wayland-0 GDK_BACKEND=wayland NO_AT_BRIDGE=1
cat > /tmp/client.py <<'PY'
import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk, GLib
w = Gtk.Window(title="t"); w.set_default_size(400, 200)
w.connect("destroy", Gtk.main_quit)
area = Gtk.DrawingArea(); w.add(area)
area.add_events(Gdk.EventMask.TOUCH_MASK | Gdk.EventMask.KEY_PRESS_MASK)
w.add_events(Gdk.EventMask.KEY_PRESS_MASK)
def touch(wd, ev):
    a = area.get_allocation()
    print("touch", ev.type.value_nick, "(%.2f,%.2f)" % (ev.x / a.width, ev.y / a.height), flush=True)
area.connect("touch-event", touch)
w.connect("key-press-event", lambda w, e: print("client got a key", e.hardware_keycode, flush=True))
w.show_all()
Gtk.main()
PY
python3 /tmp/client.py > /tmp/client.log 2>&1 &
sleep 2
cat > /tmp/sub.py <<'PY'
import os, socket, time
p = os.environ["XDG_RUNTIME_DIR"] + "/cosmos-wm.sock"
def one(cmd):
    s = socket.socket(socket.AF_UNIX); s.connect(p); s.send(cmd.encode() + b"\n"); return s.makefile().read()
one("key 30 1"); one("key 30 0"); time.sleep(.3); print("(baseline key sent)")
focused = one("focused"); print("focused:", repr(focused))
app = focused.split("\t")[0]
sub = socket.socket(socket.AF_UNIX); sub.connect(p); f = sub.makefile("rw")
sub2 = f
def ask(c):
    f.write(c + "\n"); f.flush(); return f.readline().strip()
print("touch before subscribing:", one("touch down 0 .5 .5").strip())
print("subscribe:", ask("subscribe keys " + app))
print("down 3:", ask("touch down 3 0.5 0.25")); time.sleep(.1)
print("move 3:", ask("touch move 3 0.75 0.5")); time.sleep(.1)
print("up 3:", ask("touch up 3")); print("bad id:", ask("touch down 11 0 0"))
print("down 5 then dying:", ask("touch down 5 0.1 0.1"))
one("key 30 1"); one("key 30 0"); time.sleep(.3)
sub.settimeout(1); got = []
try:
    while len(got) < 2:
        got += [l for l in sub.recv(100).decode().splitlines()]
except Exception: pass
print("keys:", got)
f.close(); sub.close(); time.sleep(.5)
print("focused after:", repr(one("focused")))
one("key 31 1"); time.sleep(.2); one("key 31 0"); time.sleep(.5)
one("key 32 1"); time.sleep(.2); one("key 32 0")
PY
python3 /tmp/sub.py; sleep 1
echo "--- client log"; cat /tmp/client.log
kill $WM 2>/dev/null; kill %2 2>/dev/null
