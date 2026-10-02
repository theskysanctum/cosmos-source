#!/bin/bash
# Runs CosmOS Settings in a headless cosmos-wm: every page must build (--check), and each page, opened directly, must fit the screen at two sizes.
# Screenshots of each page go to /shots when that folder exists. Run in a clean Fedora container:  packaging/rpm/test-settings.sh
export XDG_RUNTIME_DIR=/run/user-test; mkdir -p $XDG_RUNTIME_DIR; chmod 700 $XDG_RUNTIME_DIR
export WLR_BACKENDS=headless WLR_RENDERER=pixman WLR_HEADLESS_OUTPUTS=1 HOME=/root NO_AT_BRIDGE=1 GDK_BACKEND=wayland COSMOS_WM_TEST=1 COSMOS_SESSION_KIND=desktop
PAGES=${*:-"controllers controllers/rumble controllers/remap display wallpaper updates vm remote android android/mapper winpack downloads admin about"}
ipc(){ python3 -c "
import socket
s=socket.socket(socket.AF_UNIX);s.connect('$XDG_RUNTIME_DIR/cosmos-wm.sock');s.send(b'$1\n');print(s.makefile().read(),end='')"; }
check(){ if [ "$2" = "$3" ]; then echo "   ok   - $1"; else echo "   FAIL - $1 (got: $2, want: $3)"; bad=1; fi; }
bad=0
for scale in 1 2; do
    export COSMOS_OUTPUT_SCALE=$scale
    cosmos-wm >"$XDG_RUNTIME_DIR/wm.log" 2>&1 & WM=$!; sleep 2; export WAYLAND_DISPLAY=wayland-0
    echo "== logical screen $(ipc output | tr -d '\n') (scale $scale)"
    if [ "$scale" = 1 ]; then echo "-- every page builds:"; cosmos-settings --check 2>&1 | grep -v "^$" | tail -n 14 | sed 's/^/   /'; cosmos-settings --check >/dev/null 2>&1 || bad=1; fi
    for page in $PAGES; do
        cosmos-settings --page $page >"$XDG_RUNTIME_DIR/s.log" 2>&1 & PID=$!
        sleep 7
        line=$(ipc geometry | tail -n 1)
        if [ -z "$line" ]; then echo "   $page: no window ($(tail -n 2 "$XDG_RUNTIME_DIR/s.log" | tr '\n' ' ' | cut -c1-140))"; bad=1
        else state=$(echo "$line" | cut -f3); echo "   $page: $(echo "$line" | cut -f2) $state"; [ "$state" = fits ] || bad=1; fi
        [ -d /shots ] && command -v grim >/dev/null && grim "/shots/settings-$scale-$(echo $page | tr / -).png"
        kill $PID 2>/dev/null; wait $PID 2>/dev/null
    done
    kill $WM 2>/dev/null; wait $WM 2>/dev/null; rm -f $XDG_RUNTIME_DIR/wayland-* $XDG_RUNTIME_DIR/cosmos-wm.sock
done

echo "-- animations: the Display page's switch applies live and persists --"
export COSMOS_OUTPUT_SCALE=1
rm -f "$HOME/.config/cosmos/display.conf"
cosmos-wm >"$XDG_RUNTIME_DIR/wm.log" 2>&1 & WM=$!; sleep 2; export WAYLAND_DISPLAY=wayland-0
before=$(ipc animations)
python3 -c "
import sys
sys.path.insert(0, '/usr/lib/cosmos')
import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk
import cosmos_settings_pages as pages

page = pages.display()

def find_check(widget):
    if isinstance(widget, Gtk.CheckButton):
        return widget
    if isinstance(widget, Gtk.Container):
        for child in widget.get_children():
            found = find_check(child)
            if found:
                return found
    return None

check = find_check(page.widget)
assert check is not None, 'no Gtk.CheckButton found on the Display page'
check.set_active(not check.get_active())   # fires the real 'toggled' handler, same as a real click would
"
after=$(ipc animations)
conf=$(cat "$HOME/.config/cosmos/display.conf" 2>/dev/null | grep -c "^animations = ")
check "toggling the switch actually changed the compositor's live state" "$([ "$before" != "$after" ] && echo yes || echo no)" "yes"
check "the new state is written to display.conf" "$conf" "1"
check "display.conf agrees with the compositor's new live state" "$(grep '^animations = ' "$HOME/.config/cosmos/display.conf" | cut -d= -f2 | tr -d ' ')" "$after"
kill $WM 2>/dev/null; wait $WM 2>/dev/null
exit $bad
