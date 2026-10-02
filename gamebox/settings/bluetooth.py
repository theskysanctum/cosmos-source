#!/usr/bin/env python3
"""Bluetooth backend - a thin Python wrapper around bluetooth.sh, which
owns the real bluetoothctl-scripting logic. Per the project's
architecture (bash scripts own System/Audio/Display-Mode operations,
Python owns state/process lifecycle), this module's job is just to
invoke the script and parse its machine-readable stdout, not to script
bluetoothctl itself."""
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import debuglog  # noqa: E402

log = debuglog.setup("bluetooth")

SCRIPT = str(Path(__file__).parent / "bluetooth.sh")


def _parse_devices(text):
	devices = []
	for line in text.splitlines():
		mac, _, name = line.partition(",")
		if mac:
			devices.append({"mac": mac, "name": name})
	return devices


def is_powered():
	result = subprocess.run([SCRIPT, "is-powered"], capture_output=True, text=True, timeout=10)
	powered = result.stdout.strip() == "yes"
	log.debug(f"powered: {powered}")
	return powered


def set_powered(on):
	log.info(f"setting bluetooth {'on' if on else 'off'}")
	subprocess.run([SCRIPT, "set-powered", "on" if on else "off"], timeout=10)


def paired_devices():
	result = subprocess.run([SCRIPT, "paired-devices"], capture_output=True, text=True, timeout=10)
	devices = _parse_devices(result.stdout)
	log.debug(f"{len(devices)} paired device(s)")
	return devices


def scan(duration=5):
	"""Scans for `duration` seconds and returns devices seen, {"mac", "name"}."""
	log.info(f"scanning for bluetooth devices ({duration}s)")
	result = subprocess.run([SCRIPT, "scan", str(duration)], capture_output=True, text=True, timeout=duration + 15)
	devices = _parse_devices(result.stdout)
	log.info(f"scan found {len(devices)} device(s): {devices}")
	return devices


def pair_and_connect(mac):
	"""Returns (ok, message)."""
	log.info(f"pairing/connecting to {mac}")
	result = subprocess.run([SCRIPT, "pair-and-connect", mac], capture_output=True, text=True, timeout=25)
	ok = result.returncode == 0
	log.info(f"pair/connect {mac}: {'ok' if ok else 'failed'}")
	return ok, result.stdout.strip()


def remove(mac):
	log.info(f"removing paired device {mac}")
	subprocess.run([SCRIPT, "remove", mac], timeout=10)
