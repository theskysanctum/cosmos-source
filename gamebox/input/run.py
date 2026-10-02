#!/usr/bin/env python3
"""Entry point for the controller input daemon. Deliberately thin: all
the actual button/stick logic lives in inputd.py, hot-reloaded in place
by hotreload.HotReloader every loop tick (internally throttled - see
hotreload.py) while this process's gamepad file descriptors and UInput
device stay open the whole time. Editing input-mapping code should
never mean physically unplugging/replugging a controller."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import inputd  # noqa: E402
from gamepad import find_gamepads  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import debuglog  # noqa: E402
import hotreload  # noqa: E402

log = debuglog.setup("input.run")


def main():
	# Not a blocking "wait for the first gamepad" loop any more -
	# inputd.run() itself now re-scans for real gamepads on a timer (see
	# its own docstring), so it's just as happy starting from zero as
	# from however many are already plugged in; a controller connected
	# either before or after this process starts gets picked up the same
	# way, on the same mechanism, rather than needing two.
	gamepads = find_gamepads()
	log.info(f"found {len(gamepads)} gamepad(s) at startup")

	ui = inputd.make_virtual_keyboard()
	virtual_pads = inputd.make_virtual_gamepads()
	# modules= deliberately omitted - see hotreload.py's own project_modules()
	# docstring: auto-discovers everything this process has imported from
	# the project's own source tree, not a hand-maintained list.
	reloader = hotreload.HotReloader(on_reload=lambda: None, logger=log)

	try:
		log.info("entering input loop")
		inputd.run(gamepads, ui, virtual_pads, on_tick=reloader.check)
	finally:
		log.info("input loop exited, closing UInput devices")
		ui.close()
		for pad in virtual_pads:
			pad.close()


if __name__ == "__main__":
	main()
