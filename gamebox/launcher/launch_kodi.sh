#!/bin/bash
# Starts Kodi full screen from GameBox's menu (config.ini's [Kodi] entry). In a CosmOS session
# there is a Wayland compositor, and Kodi is happiest as a native Wayland client there (GPU
# accelerated, its own full-screen window) - the launcher's own nested X display is only a
# software-GL Xephyr. Without a Wayland display it falls back to X11.
# The launcher hides itself while this runs and comes back when Kodi exits.
if ! command -v kodi >/dev/null 2>&1; then
	notify-send -u critical -i applications-multimedia "Kodi" "Kodi isn't installed on this system." 2>/dev/null || true
	echo "launch_kodi: kodi is not installed" >&2
	exit 127
fi
if [ -n "${WAYLAND_DISPLAY:-}" ]; then
	windowing=wayland
else
	windowing=x11
fi
exec kodi -fs --windowing="$windowing" "$@"
