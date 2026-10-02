#!/usr/bin/env python3
"""Entry point for the peripheral device-limit daemon (see
device_limits.py's own docstring for what it actually enforces and
why). Deliberately thin, same shape as input/run.py: a plain polling
loop, no hot-reload machinery of its own since there's no interactive
state here to preserve across an edit the way controller input has -
device_limits.py can just be re-run fresh."""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import device_limits  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import debuglog  # noqa: E402

log = debuglog.setup("peripherals.run")

POLL_INTERVAL_S = 3


def main():
	limiter = device_limits.DeviceLimiter()
	log.info("entering peripheral device-limit loop")
	while True:
		try:
			limiter.enforce()
		except Exception:
			log.exception("device_limits.enforce() failed, continuing")
		time.sleep(POLL_INTERVAL_S)


if __name__ == "__main__":
	main()
