#!/bin/bash
# Runs each CosmOS GTK app in a headless cosmos-wm at several logical screen sizes and reports any window that is bigger than the screen ("clipped"),
# using the compositor's `geometry` command. Run in a clean Fedora container with the cosmos-* packages installed:  packaging/rpm/test-apps-fit.sh
#   test_apps_fit.sh [APP...]          default: every app below
export XDG_RUNTIME_DIR=/run/user-test; mkdir -p $XDG_RUNTIME_DIR; chmod 700 $XDG_RUNTIME_DIR
export WLR_BACKENDS=headless WLR_RENDERER=pixman WLR_HEADLESS_OUTPUTS=1 HOME=/root NO_AT_BRIDGE=1 GDK_BACKEND=wayland COSMOS_WM_TEST=1 COSMOS_SESSION_KIND=desktop
APPS=${*:-"cosmos-controllers cosmos-android cosmos-admin cosmos-remote cosmos-files cosmos-updates cosmos-touchmap-editor cosmos-winpack cosmos-wallpaper cosmos-vm-manager cosmos-shop cosmos-profiles"}
SCALES=${SCALES:-"1 2 3"}       # headless output is 1280x720: logical 1280x720, 640x360 and 427x240
ipc(){ python3 -c "
import socket
s=socket.socket(socket.AF_UNIX);s.connect('$XDG_RUNTIME_DIR/cosmos-wm.sock');s.send(b'$1\n');print(s.makefile().read(),end='')"; }
bad=0
for scale in $SCALES; do
    export COSMOS_OUTPUT_SCALE=$scale
    cosmos-wm >"$XDG_RUNTIME_DIR/wm.log" 2>&1 & WM=$!
    sleep 2; export WAYLAND_DISPLAY=wayland-0
    echo "== logical screen $(ipc output | tr -d '\n') (scale $scale)"
    for app in $APPS; do
        command -v $app >/dev/null || { echo "   $app: not installed"; continue; }
        $app --standalone >"$XDG_RUNTIME_DIR/$app.log" 2>&1 & PID=$!
        sleep 5
        line=$(ipc geometry | tail -n 1)
        if [ -z "$line" ]; then echo "   $app: no window ($(tail -n 1 "$XDG_RUNTIME_DIR/$app.log" | cut -c1-100))"
        else state=$(echo "$line" | cut -f3); echo "   $app: $(echo "$line" | cut -f2) $state"; [ "$state" = fits ] || bad=1; fi
        kill $PID 2>/dev/null; wait $PID 2>/dev/null
    done
    kill $WM 2>/dev/null; wait $WM 2>/dev/null; rm -f $XDG_RUNTIME_DIR/wayland-* $XDG_RUNTIME_DIR/cosmos-wm.sock
done
exit $bad
