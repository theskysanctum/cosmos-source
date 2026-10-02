#!/bin/bash
# Headless check that launching a web app from the Web tab leaves the launcher on the Web tab (it used to jump to Apps, so the web
# app icons vanished from the grid). Run inside the cosmos-wl-dev container with --privileged (GTK's SVG loader needs bwrap), the repo
# mounted at /src and a folder at /tmp/out for screenshots:
#   podman run --rm --privileged -v .:/src:ro,Z -v ./out:/tmp/out:Z localhost/cosmos-wl-dev bash /src/config/wm/test_launcher_launch.sh
# Expected last lines:  on tab: webapps / launching web app: gmail / after launch, tab is: webapps
mkdir -p /tmp/cw /tmp/wlr/out /tmp/out && cp /src/config/wm/cosmos-wm.c /tmp/cw/ && cd /tmp/cw
wayland-scanner server-header "$(pkg-config --variable=pkgdatadir wayland-protocols)/stable/xdg-shell/xdg-shell.xml" xdg-shell-protocol.h
wayland-scanner server-header /usr/share/wlr-protocols/unstable/wlr-layer-shell-unstable-v1.xml wlr-layer-shell-unstable-v1-protocol.h
gcc -O2 -Wall -Wno-unused-parameter -DWLR_USE_UNSTABLE -I. -o /tmp/wlr/out/cosmos-wm cosmos-wm.c $(pkg-config --cflags --libs wlroots-0.20 wayland-server xkbcommon pangocairo pixman-1 libdrm) 2>&1 | grep error
export XDG_RUNTIME_DIR=/tmp/rt; mkdir -p $XDG_RUNTIME_DIR; chmod 700 $XDG_RUNTIME_DIR
export WLR_BACKENDS=headless WLR_RENDERER=pixman WLR_HEADLESS_OUTPUTS=1 COSMOS_WM_TEST=1 COSMOS_SESSION_KIND=desktop
export HOME=/tmp/home; mkdir -p $HOME/.local/share/applications $HOME/.config/cosmos $HOME/.local/share/icons/hicolor/128x128/apps
cp /src/config/webapp-icons/gmail.png $HOME/.local/share/icons/hicolor/128x128/apps/brave-gmail.png
cat > $HOME/.local/share/applications/brave-gmail.desktop <<'D'
[Desktop Entry]
Type=Application
Name=Gmail
Exec=foot -T gmail sleep 100
Icon=brave-gmail
Categories=Network;
NoDisplay=true
D
/tmp/wlr/out/cosmos-wm > /tmp/wm.log 2>&1 &
sleep 1; export WAYLAND_DISPLAY=wayland-0 GDK_BACKEND=wayland NO_AT_BRIDGE=1 XDG_DATA_DIRS=$HOME/.local/share:/usr/share
ipc(){ python3 -c "
import socket
s=socket.socket(socket.AF_UNIX);s.connect('$XDG_RUNTIME_DIR/cosmos-wm.sock');s.send(b'$1\n');print(s.makefile().read(),end='')"; }
cat > /tmp/drv.py <<'PY'
import importlib.machinery, importlib.util, sys
sys.path.insert(0, "/src/config/scripts")
loader = importlib.machinery.SourceFileLoader("cl", "/src/config/scripts/cosmos-launcher.py")
spec = importlib.util.spec_from_loader("cl", loader); cl = importlib.util.module_from_spec(spec); loader.exec_module(cl)
from gi.repository import Gtk, Gdk, GLib
p = Gtk.CssProvider(); p.load_from_data(cl.CSS.encode())
Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), p, Gtk.STYLE_PROVIDER_PRIORITY_USER)
w = cl.CosmosLauncher(); w.show_all()
def go_web(): w.set_active_tab("webapps"); print("on tab:", w.active_tab, flush=True); return False
def launch():
    for _h, fb in w.sections.get("webapps", []):
        for name, app, extra, tile in fb._local_entries:
            if app is not None:
                print("launching web app:", name, flush=True); w.activate_entry(app, extra); return False
    print("no web app tile found", flush=True); return False
def report(): print("after launch, tab is:", w.active_tab, flush=True); return False
GLib.timeout_add(1500, go_web); GLib.timeout_add(2500, launch); GLib.timeout_add(4500, report)
Gtk.main()
PY
python3 /tmp/drv.py > /tmp/drv.log 2>&1 &
sleep 4; grim /tmp/out/w1-web-tab.png
sleep 3; grim /tmp/out/w2-after-launch.png; ipc home >/dev/null; sleep 1.5; grim /tmp/out/w3-grid-raised.png
grep -E "on tab|launching|after launch|no web" /tmp/drv.log
