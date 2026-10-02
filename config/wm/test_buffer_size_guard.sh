#!/bin/bash
# Headless test: a real burst of the gridscreen's own protocol traffic must not kill its
# connection. This is the actual field bug behind "closing Settings kills the gridscreen" - the
# gridscreen (or any client) stayed on screen, pixel-identical, but its Wayland connection got
# forcibly closed by the compositor mid-burst, which is fatal for any Wayland client: "[wayland]
# error in client communication" / "Data too big for buffer (4080 + 20 > 4096)" - the 4096-byte
# default libwayland gives every client's connection buffer, unless a compositor explicitly
# raises it with wl_display_set_default_max_buffer_size(). Confirmed live before this fix: this
# exact burst reproduced the exact field error, byte for byte; after it, the same burst survives.
# Run in a clean Fedora container with the compositor build.py made:   packaging/rpm/test-buffer-size-guard.sh
export XDG_RUNTIME_DIR=/run/user-test; mkdir -p $XDG_RUNTIME_DIR; chmod 700 $XDG_RUNTIME_DIR
export WLR_BACKENDS=headless WLR_RENDERER=pixman WLR_HEADLESS_OUTPUTS=1 HOME=/root NO_AT_BRIDGE=1 GDK_BACKEND=wayland COSMOS_WM_TEST=1 COSMOS_SESSION_KIND=desktop
ipc(){ python3 -c "
import socket
s=socket.socket(socket.AF_UNIX);s.connect('$XDG_RUNTIME_DIR/cosmos-wm.sock');s.send(b'$1\n');print(s.makefile().read(),end='')"; }
fail=0
check(){ if [ "$2" = "$3" ]; then echo "ok   - $1"; else echo "FAIL - $1 (got: $2, want: $3)"; fail=1; fi; }

cosmos-wm >"$XDG_RUNTIME_DIR/wm.log" 2>&1 & WM=$!; sleep 2; export WAYLAND_DISPLAY=wayland-0

cosmos-launcher >"$XDG_RUNTIME_DIR/launcher.log" 2>&1 & APP=$!
sleep 2
check "the gridscreen is running" "$(kill -0 $APP 2>/dev/null && echo alive || echo dead)" "alive"

# 25,000 configure events back to back, unflushed - empirically bisected (reliable across
# repeated runs both ways) as enough to overflow the OLD 4096-byte connection buffer, since the
# ring buffer plus the kernel socket's own send buffer together give more slack than 4096 bytes
# alone suggests, while staying below where an artificially huge burst starts hitting unrelated
# kernel socket-buffer limits regardless of this fix (a real burst 40,000+ messages in a row
# started intermittently failing a different way even with the fix in place - not the bug under
# test here, just an artifact of a single-threaded C loop never yielding to the event loop at
# all, which no real client traffic pattern does).
ipc "stress-configure 25000" >/dev/null
sleep 1

check "the gridscreen survives a real burst of its own protocol traffic" "$(kill -0 $APP 2>/dev/null && echo alive || echo dead)" "alive"
check "no wayland buffer-overflow error was logged" "$(grep -c 'too big for buffer' "$XDG_RUNTIME_DIR/wm.log" 2>/dev/null)" "0"

kill $APP 2>/dev/null; wait $APP 2>/dev/null
kill $WM 2>/dev/null; wait $WM 2>/dev/null
exit $fail
