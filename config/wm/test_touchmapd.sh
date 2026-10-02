#!/bin/bash
# End to end: compositor + a window that calls itself Waydroid + cosmos_touchmap's daemon. Keys typed while that window has focus become
# touches in it; keys typed in another window are left alone. Run in the cosmos-wl-dev container like test_touch_keys.sh.
mkdir -p /tmp/cw /tmp/wlr/out && cp /src/config/wm/cosmos-wm.c /tmp/cw/ && cd /tmp/cw
wayland-scanner server-header "$(pkg-config --variable=pkgdatadir wayland-protocols)/stable/xdg-shell/xdg-shell.xml" xdg-shell-protocol.h
wayland-scanner server-header /usr/share/wlr-protocols/unstable/wlr-layer-shell-unstable-v1.xml wlr-layer-shell-unstable-v1-protocol.h
gcc -O2 -Wall -Wno-unused-parameter -DWLR_USE_UNSTABLE -I. -o /tmp/wlr/out/cosmos-wm cosmos-wm.c $(pkg-config --cflags --libs wlroots-0.20 wayland-server xkbcommon pangocairo pixman-1 libdrm) 2>&1 | grep -E "error|warning"
export XDG_RUNTIME_DIR=/tmp/rt HOME=/tmp/home XDG_CONFIG_HOME=/tmp/home/.config; mkdir -p $XDG_RUNTIME_DIR $HOME; chmod 700 $XDG_RUNTIME_DIR
export WLR_BACKENDS=headless WLR_RENDERER=pixman WLR_HEADLESS_OUTPUTS=1 COSMOS_WM_TEST=1 COSMOS_SESSION_KIND=desktop
/tmp/wlr/out/cosmos-wm > /tmp/wm.log 2>&1 & WM=$!
sleep 1; export WAYLAND_DISPLAY=wayland-0 GDK_BACKEND=wayland NO_AT_BRIDGE=1
cat > /tmp/client.py <<'PY'
import sys, gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk, GLib
GLib.set_prgname(sys.argv[1])
w = Gtk.Window(title=sys.argv[1]); w.set_default_size(400, 200)
area = Gtk.DrawingArea(); w.add(area)
area.add_events(Gdk.EventMask.TOUCH_MASK)
def touch(wd, ev):
    a = area.get_allocation()
    print(sys.argv[1], "touch", ev.type.value_nick, ev.sequence is not None, "(%.2f,%.2f)" % (ev.x / a.width, ev.y / a.height), flush=True)
area.connect("touch-event", touch)
w.connect("key-press-event", lambda w, e: print(sys.argv[1], "got key", e.hardware_keycode - 8, flush=True))
w.show_all(); Gtk.main()
PY
python3 - <<'PY'
import sys; sys.path.insert(0, "/src/config/scripts")
import cosmos_touchmap as tm
tm.save_profile({"name": "Test", "aspect": 2.0, "elements": [
    {"type": "tap", "inputs": ["KEY_SPACE", "BTN_SOUTH"], "x": 0.8, "y": 0.7},
    {"type": "stick", "keys": {"up": "KEY_W", "down": "KEY_S", "left": "KEY_A", "right": "KEY_D"}, "x": 0.2, "y": 0.6, "radius": 0.1}]})
tm.save_settings({"enabled": True, "profile": "Test"})
PY
python3 /src/config/scripts/cosmos_touchmap.py > /tmp/daemon.log 2>&1 & D=$!
python3 /tmp/client.py Other > /tmp/other.log 2>&1 &
sleep 2
key(){ python3 -c "
import socket
s=socket.socket(socket.AF_UNIX);s.connect('$XDG_RUNTIME_DIR/cosmos-wm.sock');s.send(b'key $1 $2\n');s.makefile().read()"; }
echo "--- typing in the non-Android window (must reach it, and not be mapped)"; key 57 1; key 57 0; sleep .5
python3 /tmp/client.py Waydroid > /tmp/droid.log 2>&1 &
sleep 2.5
echo "--- typing in the Android window: space taps, W held pushes the joystick up"
key 57 1; sleep .2; key 57 0; key 17 1; sleep .4; key 17 0; sleep .5
echo "--- claim file while active:"; ls $XDG_RUNTIME_DIR | grep claimed || echo "(none)"
echo "--- back to the other window (Alt+Tab): keys are normal again"
python3 -c "
import socket
s=socket.socket(socket.AF_UNIX);s.connect('$XDG_RUNTIME_DIR/cosmos-wm.sock');s.send(b'key 56 1\n');s.makefile().read()"
key 15 1; key 15 0
python3 -c "
import socket
s=socket.socket(socket.AF_UNIX);s.connect('$XDG_RUNTIME_DIR/cosmos-wm.sock');s.send(b'key 56 0\n');s.makefile().read()"
sleep 1; key 57 1; key 57 0; sleep .5
echo "--- other.log"; cat /tmp/other.log; echo "--- droid.log"; cat /tmp/droid.log; echo "--- daemon.log"; cat /tmp/daemon.log
echo "--- claim after:"; ls $XDG_RUNTIME_DIR | grep claimed || echo "(none)"
kill $D $WM 2>/dev/null
