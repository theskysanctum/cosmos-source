#!/usr/bin/env python3
"""Watches for the OSK toggle button during a graphical session and
spawns/kills osk.py in response. osk.py can also close itself (its own
close button), which this notices via poll() and treats the same as
having toggled it off."""
import select
import subprocess
import sys
from pathlib import Path

from evdev import ecodes

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "input"))
from gamepad import find_gamepads  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import debuglog  # noqa: E402

log = debuglog.setup("osk_watcher")

OSK_SCRIPT = str(Path(__file__).parent / "osk.py")
TOGGLE_BUTTON = ecodes.BTN_SELECT


def watch(env, stop_event):
	devices = find_gamepads()
	if not devices:
		log.warning("no gamepad found, OSK toggle will not be available for this session")
		return
	log.debug(f"watching {len(devices)} gamepad(s) for OSK toggle on {env.get('DISPLAY')}")

	device_by_fd = {d.fd: d for d in devices}
	osk_proc = None

	while not stop_event.is_set():
		ready, _, _ = select.select(device_by_fd, [], [], 0.3)
		for fd in ready:
			for event in device_by_fd[fd].read():
				if event.type == ecodes.EV_KEY and event.code == TOGGLE_BUTTON and event.value == 1:
					if osk_proc and osk_proc.poll() is None:
						log.info("toggle pressed, closing OSK")
						osk_proc.terminate()
						osk_proc.wait(timeout=5)
						osk_proc = None
					else:
						log.info("toggle pressed, opening OSK")
						osk_proc = subprocess.Popen(["python3", OSK_SCRIPT], env=env)

		if osk_proc and osk_proc.poll() is not None:
			log.debug(f"OSK process exited on its own (code {osk_proc.returncode})")
			osk_proc = None

	log.debug("stop_event set, watcher shutting down")
	if osk_proc and osk_proc.poll() is None:
		osk_proc.terminate()
		osk_proc.wait(timeout=5)
