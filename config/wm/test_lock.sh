#!/bin/bash
# Headless lock-screen test for cosmos-wm (run inside the cosmos-wl-dev container; see test_cosmos_wm.sh).
# Uses the COSMOS_WM_TEST=1 "key" control command instead of wtype, whose per-invocation keymaps confuse GTK.
export XDG_RUNTIME_DIR=/tmp/rt; rm -rf $XDG_RUNTIME_DIR; mkdir -p $XDG_RUNTIME_DIR; chmod 700 $XDG_RUNTIME_DIR
export WLR_BACKENDS=headless WLR_RENDERER=pixman WLR_HEADLESS_OUTPUTS=1
printf '#!/bin/sh\nread p\n[ "$p" = ok ]\n' > /usr/local/bin/cosmos-authcheck; chmod +x /usr/local/bin/cosmos-authcheck
export PATH=/cfg/scripts:$PATH; ln -sf /cfg/scripts/cosmos-lockscreen.py /usr/local/bin/cosmos-lockscreen
export COSMOS_WM_TEST=1 COSMOS_LOCK_CMD="python3 /cfg/scripts/cosmos-lockscreen.py --locked"; export WAYLAND_DISPLAY=wayland-0 GDK_BACKEND=wayland NO_AT_BRIDGE=1 HOME=/tmp/home COSMOS_INPUT_DIR=/cfg/input PYTHONPATH=/cfg/scripts; mkdir -p $HOME
COSMOS_WM_TEST=1 /tmp/wlr/out/cosmos-wm > /tmp/wlr/t/wm.log 2>&1 &
sleep 1; sleep .5
key(){ python3 -c "
import sys; sys.path.insert(0,'/cfg/scripts'); import cosmos_wm_ipc as i
for c in '$1'.split():
    i.request(f'key {c} 1'); i.request(f'key {c} 0')"; }
ipc(){ python3 -c "
import sys; sys.path.insert(0,'/cfg/scripts'); import cosmos_wm_ipc as i; print(i.request('$1'),end='')"; }
python3 /cfg/scripts/cosmos-bar.py 2>/dev/null & sleep 2.5
foot -T native sleep 100 2>/dev/null & sleep 1.5
grim /tmp/wlr/t/k0.png
echo "--- lock"; python3 /cfg/scripts/cosmos-lockscreen.py; echo rc=$?; sleep 3; grim /tmp/wlr/t/k1.png
echo "--- while locked: activate"; ipc "activate 1"
echo "--- wrong password"; key "48 30 32 28"; sleep 1.5; grim /tmp/wlr/t/k2.png
echo "--- kill lock UI, expect respawn"; pkill -f "cosmos-lockscreen.py --locked"; sleep 3; pgrep -f "cosmos-lockscreen.py --locked" >/dev/null && echo respawned || echo NOT-respawned
grim /tmp/wlr/t/k3.png
echo "--- right password"; key "24 37 28"; sleep 2; grim /tmp/wlr/t/k4.png
pgrep -f "cosmos-lockscreen.py --locked" >/dev/null && echo still-running || echo exited
ipc windows
kill %1 2>/dev/null; wait 2>/dev/null
