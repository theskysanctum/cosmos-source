"""Where GameBox is, for the CosmOS tools that work on its files (GameBox Add-on Studio).

GameBox is baked into the image; `gamebox-dir` prepares the writable copy it runs from (/cosmos/app/current) and prints its path, so that
is where these tools work. $GAMEBOX_REPO_DIR always wins, for anyone scripting a tool against another copy."""
import os
import subprocess
from pathlib import Path

WRITABLE_COPY = Path("/cosmos/app/current")


def gamebox_dir():
	"""The GameBox folder to work on: $GAMEBOX_REPO_DIR, else whatever `gamebox-dir` answers (it makes the writable copy on first use),
	else the usual writable location."""
	override = os.environ.get("GAMEBOX_REPO_DIR")
	if override:
		return Path(override)
	try:
		done = subprocess.run(["gamebox-dir"], capture_output=True, text=True, timeout=120)
		answer = done.stdout.strip().splitlines()[-1] if done.returncode == 0 and done.stdout.strip() else ""
	except (OSError, subprocess.SubprocessError):
		answer = ""
	return Path(answer) if answer else WRITABLE_COPY


def require_dir(path, label):
	"""Returns path if it's a real directory, else raises with a message a GTK error dialog can show directly - the tools here need the real
	files to do anything useful, so failing loud and early (before building any UI that would just be empty) is the right default."""
	if not path.is_dir():
		raise FileNotFoundError(f"The {label} files weren't found at {path} - this image should carry them at /usr/share/gamebox.")
	return path
