#!/bin/bash
# Headless behaviour test for cosmos-wm. Run inside the cosmos-wl-dev container
# (needs foot, grim, wtype, gtk-layer-shell) with the built binary at /tmp/wlr/out/cosmos-wm.
# Prints the MRU window list after each Alt+Tab variant; compare against the
# expectations in the comments of the X11 cosmos-wm it replaced.
export XDG_RUNTIME_DIR=/tmp/rt; mkdir -p $XDG_RUNTIME_DIR; chmod 700 $XDG_RUNTIME_DIR
export WLR_BACKENDS=headless WLR_RENDERER=pixman WLR_HEADLESS_OUTPUTS=1
/tmp/wlr/out/cosmos-wm > /tmp/wlr/t/wm.log 2>&1 &
sleep 1; export WAYLAND_DISPLAY=wayland-0 GDK_BACKEND=wayland NO_AT_BRIDGE=1
ipc(){ python3 -c "
import socket
s=socket.socket(socket.AF_UNIX);s.connect('$XDG_RUNTIME_DIR/cosmos-wm.sock');s.send(b'$1\n');print(s.makefile().read(),end='')"; }
python3 panel.py bg 2>/dev/null & sleep 1.5
foot -T one sleep 100 2>/dev/null & sleep 1
foot -T two sleep 100 2>/dev/null & sleep 1
foot -T three sleep 100 2>/dev/null & sleep 1
echo "--- start (MRU: three two one)"; ipc windows
echo "--- Alt+Tab tap  -> two"; wtype -M alt -k Tab -m alt; sleep .5; ipc windows
echo "--- Alt+Tab Tab  (held) -> from two: third entry = three"; wtype -M alt -k Tab -k Tab -m alt; sleep .5; ipc windows
echo "--- Alt+Shift+Tab -> oldest"; wtype -M alt -M shift -k Tab -m shift -m alt; sleep .5; ipc windows
echo "--- Alt+Tab, Esc cancels"; wtype -M alt -k Tab -k Escape -m alt; sleep .5; ipc windows
echo "--- overlay screenshot"; wtype -P alt -k Tab; sleep .5; grim /tmp/wlr/t/sw.png; wtype -p alt; sleep .3; ipc windows
echo "--- Alt+X closes active"; wtype -M alt -k x -m alt; sleep 1; ipc windows
# --- the gridscreen: the Windows key raises/lowers it (needs COSMOS_WM_TEST=1 COSMOS_SESSION_KIND=desktop; keys typed through the socket)
echo "--- Windows key toggles the gridscreen over/behind the windows (nothing is minimized)"
what(){ grim -t ppm /tmp/wlr/t/x.ppm; python3 -c "
d=open('/tmp/wlr/t/x.ppm','rb').read(); p=d.split(b'\n',3); w,h=map(int,p[1].split()); i=((h//2)*w+w//2)*3
print('centre brightness', sum(p[3][i:i+3])//3)"; }
tap(){ ipc "key $1 1" >/dev/null; ipc "key $1 0" >/dev/null; sleep .5; }
echo "window on top:"; what; echo "Windows tap (grid up: light):"; tap 125; what; ipc windows | wc -l; echo "Windows tap again (window back):"; tap 125; what
kill %1 2>/dev/null; wait 2>/dev/null
