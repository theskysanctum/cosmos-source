#!/usr/bin/env python3
"""Short UI sound effects (menu move/confirm/back) - fire-and-forget
subprocess.Popen calls to paplay (PulseAudio's own real CLI player), the
same "drive a real, well-tested tool" approach every other backend in
this project already uses (emulators/appman.py's appman, packaging/
flatpak_apps.py's flatpak) rather than a new Python audio library or an
in-process GStreamer pipeline. Never waits on the
child process - a sound effect blocking real keyboard/gamepad
navigation, even briefly, would be worse than no sound at all.

The three sound files themselves are plain synthesized tones (see
generate_sfx.py), not recorded/licensed audio."""
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import debuglog  # noqa: E402

log = debuglog.setup("sfx")

SOUNDS_DIR = Path(__file__).resolve().parent / "sounds"

SOUNDS = {
	"move": SOUNDS_DIR / "move.wav",
	"confirm": SOUNDS_DIR / "confirm.wav",
	"back": SOUNDS_DIR / "back.wav",
}

_paplay_missing_logged = False


def play(name):
	"""Never raises, never blocks - a missing sound file or a missing
	paplay binary is never worth crashing or stalling navigation over,
	the same "log and move on" contract appman.py's own subprocess calls
	already follow (just without their return-value bool, since there's
	nothing meaningful to report back for a fire-and-forget sound)."""
	global _paplay_missing_logged
	path = SOUNDS.get(name)
	if path is None or not path.exists():
		log.debug(f"play: no sound file for {name!r}")
		return
	try:
		subprocess.Popen(["paplay", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
	except FileNotFoundError:
		if not _paplay_missing_logged:
			log.warning("paplay not found - is pulseaudio-utils installed? (only logging this once)")
			_paplay_missing_logged = True
