#!/bin/bash
# Headless test: nothing behind the raised gridscreen should be reachable while it's up - neither a shortcut acting on it (Alt+X used to
# close whatever window was hidden behind the grid, with nothing on screen to show it had happened) nor the keyboard itself (closing the
# window that had focus used to silently hand the keyboard to some OTHER window still hidden behind the grid - the grid stayed on screen,
# looking alive, but stopped responding to a single keystroke, which is what "closing Settings kills the gridscreen" actually was).
# Run in a clean Fedora container with the compositor build.py made:   packaging/rpm/test-gridscreen-guard.sh
export XDG_RUNTIME_DIR=/run/user-test; mkdir -p $XDG_RUNTIME_DIR; chmod 700 $XDG_RUNTIME_DIR
export WLR_BACKENDS=headless WLR_RENDERER=pixman WLR_HEADLESS_OUTPUTS=1 HOME=/root NO_AT_BRIDGE=1 GDK_BACKEND=wayland COSMOS_WM_TEST=1 COSMOS_SESSION_KIND=desktop
KEYLOG=$(mktemp -d)
ipc(){ python3 -c "
import socket
s=socket.socket(socket.AF_UNIX);s.connect('$XDG_RUNTIME_DIR/cosmos-wm.sock');s.send(b'$1\n');print(s.makefile().read(),end='')"; }
tap(){ ipc "key $1 1" >/dev/null; ipc "key $1 0" >/dev/null; }
alt_x(){ ipc "key 56 1" >/dev/null; tap 45; ipc "key 56 0" >/dev/null; }   # keycode 56 = Left Alt, 45 = X
fail=0
check(){ if [ "$2" = "$3" ]; then echo "ok   - $1"; else echo "FAIL - $1 (got: $2, want: $3)"; fail=1; fi; }

cosmos-wm >"$XDG_RUNTIME_DIR/wm.log" 2>&1 & WM=$!; sleep 2; export WAYLAND_DISPLAY=wayland-0

# A stand-in gridscreen: a real layer-shell surface, keyboard-interactive, on the background layer - the one thing home_raise() actually
# looks for (home_layer()). Without one raising the grid is a silent no-op, which is what let this test pass by accident the first time.
# Logs every key it receives to $KEYLOG/grid.log, so later checks can tell whether the keyboard actually reached it.
KEYLOG="$KEYLOG" python3 - <<'PYEOF' >/dev/null 2>&1 &
import os
import gi
gi.require_version("Gtk", "3.0"); gi.require_version("GtkLayerShell", "0.1")
from gi.repository import Gtk, GtkLayerShell
win = Gtk.Window()
GtkLayerShell.init_for_window(win)
GtkLayerShell.set_layer(win, GtkLayerShell.Layer.BACKGROUND)
GtkLayerShell.set_keyboard_mode(win, GtkLayerShell.KeyboardMode.ON_DEMAND)
for e in (GtkLayerShell.Edge.LEFT, GtkLayerShell.Edge.RIGHT, GtkLayerShell.Edge.TOP, GtkLayerShell.Edge.BOTTOM):
    GtkLayerShell.set_anchor(win, e, True)
win.add(Gtk.Label(label="GRIDSCREEN"))

def on_key(_w, event):
    with open(os.path.join(os.environ["KEYLOG"], "grid.log"), "a") as f:
        f.write(f"{event.keyval}\n")
    return False
win.connect("key-press-event", on_key)

win.show_all()
Gtk.main()
PYEOF
GRID=$!
sleep 1.5

cosmos-updates --standalone >/dev/null 2>&1 & APP=$!
sleep 1.5
check "the window is open" "$(ipc windows | wc -l)" "1"

tap 125   # Windows key: raise the gridscreen over the window
sleep 0.5
alt_x
sleep 0.5
check "Alt+X while the grid is raised does not close the window behind it" "$(ipc windows | wc -l)" "1"

tap 125   # lower the grid again
sleep 0.5
check "the window is still there after lowering the grid" "$(ipc windows | wc -l)" "1"

alt_x     # now the window is actually what's on screen: Alt+X should work normally
sleep 0.5
check "Alt+X closes the window once it is actually on screen (not just behind the grid)" "$(ipc windows | wc -l)" "0"

# Two more plain windows, each logging every key it receives - to check WHERE THE KEYBOARD GOES, not just what is on screen.
mkwin(){
python3 - "$1" "$2" <<'PYEOF' >/dev/null 2>&1 &
import sys
import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk
win = Gtk.Window(title=sys.argv[1])
logfile = sys.argv[2]
def on_key(_w, event):
    with open(logfile, "a") as f:
        f.write(f"{event.keyval}\n")
    return False
win.connect("key-press-event", on_key)
win.add(Gtk.Label(label=sys.argv[1]))
win.show_all()
Gtk.main()
PYEOF
}
mkwin WINA "$KEYLOG/winA.log"; WINA=$!
sleep 1
mkwin WINB "$KEYLOG/winB.log"; WINB=$!
sleep 1
check "two windows are open" "$(ipc windows | wc -l)" "2"

tap 125   # raise the grid over both
sleep 0.5
kill $WINB 2>/dev/null; wait $WINB 2>/dev/null   # close the front one, same as closing Settings while the grid is up
sleep 1
check "the other window is still there, just hidden behind the grid" "$(ipc windows | wc -l)" "1"

tap 30    # keycode 30 = 'a' - should go to the grid, never to WINA hiding behind it
sleep 0.5
check "the keyboard stayed with the grid, not the window behind it" "$([ -s "$KEYLOG/grid.log" ] && echo yes || echo no)/$([ -s "$KEYLOG/winA.log" ] && echo yes || echo no)" "yes/no"

kill $WINA $APP $GRID 2>/dev/null; wait $WINA 2>/dev/null
kill $WM 2>/dev/null; wait $WM 2>/dev/null
rm -rf "$KEYLOG"
exit $fail
