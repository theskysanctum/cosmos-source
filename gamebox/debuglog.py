#!/usr/bin/env python3
"""Shared verbose-debugging setup. Every entry point/module calls
setup(name) once (at import time is fine - it's idempotent) to get a
logger that writes timestamped, leveled messages to both stderr and a
per-name log file under /tmp/console-os-logs/, so behavior can be
inspected after the fact - this runs on a TV, not a terminal you're
watching live - as well as during interactive testing.

Verbosity is controlled by the CONSOLE_OS_DEBUG env var: unset or "1"
(anything but "0") = DEBUG (verbose), "0" = INFO and above only. Verbose
is the default - this project's standard is "detailed debugging is
always available", not something you opt into after already hitting a
bug."""
import logging
import os
import sys
from pathlib import Path

LOG_DIR = Path("/tmp/console-os-logs")


def setup(name):
	level = logging.INFO if os.environ.get("CONSOLE_OS_DEBUG") == "0" else logging.DEBUG

	logger = logging.getLogger(name)
	logger.setLevel(level)
	logger.propagate = False
	if logger.handlers:
		return logger  # already set up (e.g. re-imported after a hot reload)

	formatter = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s", datefmt="%H:%M:%S")

	stream_handler = logging.StreamHandler(sys.stderr)
	stream_handler.setFormatter(formatter)
	logger.addHandler(stream_handler)

	try:
		LOG_DIR.mkdir(parents=True, exist_ok=True)
		file_handler = logging.FileHandler(LOG_DIR / f"{name}.log")
		file_handler.setFormatter(formatter)
		logger.addHandler(file_handler)
	except OSError:
		# A read-only /tmp or permissions issue shouldn't take the whole
		# process down over logging - stderr alone still works.
		logger.warning(f"could not open log file under {LOG_DIR}, logging to stderr only", exc_info=True)

	return logger
