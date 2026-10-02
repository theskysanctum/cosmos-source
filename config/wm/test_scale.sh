#!/bin/bash
# Headless test of cosmos-wm's output scaling, run in a clean Fedora container with the built binary at /usr/bin/cosmos-wm:
#   packaging/rpm/test-wm-scale.sh
# (a headless output is 1280x720 with no physical size, so the automatic scale is 1; the rest is overrides.)
export XDG_RUNTIME_DIR=/run/user-test; mkdir -p $XDG_RUNTIME_DIR; chmod 700 $XDG_RUNTIME_DIR
export WLR_BACKENDS=headless WLR_RENDERER=pixman WLR_HEADLESS_OUTPUTS=1 HOME=/root
ipc(){ python3 -c "
import socket
s=socket.socket(socket.AF_UNIX);s.connect('$XDG_RUNTIME_DIR/cosmos-wm.sock');s.send(b'$1\n');print(s.makefile().read(),end='')"; }
fail=0
check(){ if [ "$2" = "$3" ]; then echo "ok   - $1"; else echo "FAIL - $1 (got: $2, want: $3)"; fail=1; fi; }
start(){ cosmos-wm >"$XDG_RUNTIME_DIR/wm.log" 2>&1 & WM=$!; sleep 1.5; }
stop(){ kill $WM 2>/dev/null; wait $WM 2>/dev/null; rm -f "$XDG_RUNTIME_DIR"/cosmos-wm.sock "$XDG_RUNTIME_DIR"/wayland-*; }

start
check "automatic: a 720p output without a physical size is 1x" "$(ipc scale | awk '{print $3, $4}')" "1.00 1280x720"
check "the output command reports the logical size" "$(ipc output)" "1280x720"
check "scale 2 halves the logical size" "$(ipc 'scale 2' | awk '{print $3, $4}')" "2.00 640x360"
check "output follows" "$(ipc output)" "640x360"
check "scale 1.5" "$(ipc 'scale 1.5' | awk '{print $3, $4}')" "1.50 853x480"
if command -v wayland-info >/dev/null; then
  info=$(WAYLAND_DISPLAY=wayland-0 wayland-info 2>&1)
  check "clients are told the whole-number scale (wl_output: 1.5 rounds up to 2)" "$(echo "$info" | tr -d " \t" | grep -o -m1 "scale:[0-9]*")" "scale:2"
  check "clients are told the logical size (xdg-output)" "$(echo "$info" | tr -d " \t" | grep -o -m1 "logical_width:[0-9]*,logical_height:[0-9]*")" "logical_width:853,logical_height:480"
  check "fractional scale is offered to clients that support it" "$(echo "$info" | grep -c "wp_fractional_scale_manager_v1")" "1"
fi
check "a mode the output does not list is refused" "$(ipc 'mode 1920x1080' | cut -c1-6)" "error:"
check "junk instead of a mode is refused" "$(ipc 'mode big' | cut -c1-6)" "error:"
check "a bad value is refused and changes nothing" "$(ipc 'scale banana' | head -1 | cut -c1-6)" "error:"
check "still 1.5 after the refusal" "$(ipc scale | awk '{print $3}')" "1.50"
check "auto goes back" "$(ipc 'scale auto' | awk '{print $3, $4}')" "1.00 1280x720"
stop

export COSMOS_OUTPUT_SCALE=2; start; unset COSMOS_OUTPUT_SCALE
check "COSMOS_OUTPUT_SCALE applies at start" "$(ipc output)" "640x360"
stop

mkdir -p /root/.config/cosmos; echo "scale = 1.25" > /root/.config/cosmos/display.conf
start
check "display.conf applies at start" "$(ipc output)" "1024x576"
stop
printf 'scale = 1.25\nscale HEADLESS-1 = 2\n' > /root/.config/cosmos/display.conf
start
check "a per-output line beats the general one" "$(ipc output)" "640x360"
stop
exit $fail
