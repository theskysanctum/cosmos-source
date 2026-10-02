#!/usr/bin/env python3
"""Display resolution backend - a thin Python wrapper around display.sh,
which owns the real xrandr logic. Per the project's architecture (bash
scripts own System/Audio/Display-Mode operations, Python owns state/
process lifecycle), this module's job is just to invoke the script and
parse its machine-readable stdout, not to shell out to xrandr itself."""
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import debuglog  # noqa: E402

log = debuglog.setup("display")

SCRIPT = str(Path(__file__).parent / "display.sh")


def primary_output():
	result = subprocess.run([SCRIPT, "primary-output"], capture_output=True, text=True)
	output = result.stdout.strip() or None
	log.debug(f"primary output: {output}")
	return output


def list_modes(output=None):
	"""Returns modes as {"mode", "current"} dicts, in xrandr's own order
	(largest/preferred first)."""
	args = [SCRIPT, "list-modes"] + ([output] if output else [])
	result = subprocess.run(args, capture_output=True, text=True)
	modes = []
	for line in result.stdout.splitlines():
		mode, _, current = line.partition(",")
		if mode:
			modes.append({"mode": mode, "current": current == "current"})
	log.debug(f"{len(modes)} mode(s) for {output or '(primary)'}")
	return modes


def set_mode(mode, output=None):
	args = [SCRIPT, "set-mode", mode] + ([output] if output else [])
	log.info(f"setting {output or '(primary)'} to {mode}")
	subprocess.run(args, check=True)
