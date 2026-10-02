#!/usr/bin/env python3
"""Wi-Fi backend - a thin Python wrapper around wifi.sh, which owns the
real nmcli logic. Per the project's architecture (bash scripts own
System/Audio/Display-Mode operations, Python owns state/process
lifecycle), this module's job is just to invoke the script and parse
its machine-readable stdout, not to shell out to nmcli itself."""
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import debuglog  # noqa: E402

log = debuglog.setup("wifi")

SCRIPT = str(Path(__file__).parent / "wifi.sh")


def is_enabled():
	result = subprocess.run([SCRIPT, "is-enabled"], capture_output=True, text=True)
	enabled = result.stdout.strip() == "yes"
	log.debug(f"radio enabled: {enabled}")
	return enabled


def set_enabled(enabled):
	log.info(f"setting wifi radio {'on' if enabled else 'off'}")
	subprocess.run([SCRIPT, "set-enabled", "on" if enabled else "off"], check=True)


def current_ssid():
	result = subprocess.run([SCRIPT, "current-ssid"], capture_output=True, text=True)
	ssid = result.stdout.strip()
	if ssid:
		log.debug(f"current ssid: {ssid}")
		return ssid
	log.debug("not connected to any network")
	return None


def scan():
	"""Returns networks as {"ssid", "signal", "secured"} dicts, strongest
	signal first (wifi.sh already dedupes/sorts)."""
	result = subprocess.run([SCRIPT, "scan"], capture_output=True, text=True, timeout=15)
	networks = []
	for line in result.stdout.splitlines():
		ssid, _, rest = line.partition(",")
		signal_str, _, secured_str = rest.partition(",")
		if not ssid:
			continue
		signal = int(signal_str) if signal_str.isdigit() else 0
		networks.append({"ssid": ssid, "signal": signal, "secured": secured_str == "secured"})
	log.info(f"scan found {len(networks)} network(s): {[n['ssid'] for n in networks]}")
	return networks


def connect(ssid, password=None):
	"""Returns (ok, message)."""
	args = [SCRIPT, "connect", ssid]
	if password:
		args.append(password)
	log.info(f"connecting to {ssid!r} (password {'provided' if password else 'not provided'})")
	result = subprocess.run(args, capture_output=True, text=True, timeout=30)
	message = result.stdout.strip()
	ok = result.returncode == 0
	log.info(f"connect to {ssid!r}: {'ok' if ok else 'failed'} - {message}")
	return ok, message


def disconnect():
	ssid = current_ssid()
	if ssid:
		log.info(f"disconnecting from {ssid!r}")
	subprocess.run([SCRIPT, "disconnect"], capture_output=True, timeout=15)


def is_online():
	"""A real internet-reachability check (nmcli's own connectivity
	probe, not just "is some interface up") - app.py's own Shop gate
	uses this to refuse opening the whole Shop section (GameGrubber)
	without real internet, since nothing in there works offline
	anyway. False on any failure (nmcli missing, timeout,
	unparseable output) - "can't confirm we're online" is treated the
	same as "not online", never the other way around."""
	try:
		result = subprocess.run([SCRIPT, "is-online"], capture_output=True, text=True, timeout=10)
	except subprocess.TimeoutExpired:
		log.warning("is-online check timed out")
		return False
	return result.stdout.strip() == "yes"
