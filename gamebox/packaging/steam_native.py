#!/usr/bin/env python3
"""Installs Steam as a real native RPM (RPM Fusion's own "steam"
package) rather than the Flatpak - the one required dependency this
project needed off Flatpak specifically so Millennium (see
packaging/millennium_installer.py) can actually theme it. Flatpak's own
sandboxing is a hard, structural blocker for Millennium - its own
maintainers confirmed live-code-injecting into a sandboxed Steam breaks
the sandbox's whole contract (SSL/cert access in particular) and ruled
it out entirely, not a bug that might get fixed later.

dnf-only (see host_deps.py's own docstring for why this project only
really supports dnf/Fedora) - RPM Fusion itself isn't a Fedora default
repo, so it needs its own explicit enable step first, unlike every
plain PACKAGES entry in host_deps.py."""
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import debuglog  # noqa: E402

log = debuglog.setup("steam_native")


def _fedora_version():
	return subprocess.run(["rpm", "-E", "%fedora"], capture_output=True, text=True, check=True).stdout.strip()


def _rpmfusion_urls():
	version = _fedora_version()
	return [
		f"https://mirrors.rpmfusion.org/free/fedora/rpmfusion-free-release-{version}.noarch.rpm",
		f"https://mirrors.rpmfusion.org/nonfree/fedora/rpmfusion-nonfree-release-{version}.noarch.rpm",
	]


def _admin():
	"""The words that give a command root: the kronos admin service on CosmOS (no sudo there), sudo anywhere else."""
	return ["kronos", "exec", "--"] if shutil.which("kronos") else ["sudo"]


def _run_streamed(cmd, log_fn=None):
	proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
	for line in proc.stdout:
		log.debug(line.rstrip())
		if log_fn:
			log_fn(line.rstrip())
	proc.wait()
	return proc.returncode == 0


def is_installed():
	return subprocess.run(["rpm", "-q", "steam"], capture_output=True).returncode == 0


def ensure_rpmfusion(log_fn=None):
	"""Installing the release RPMs directly by URL (dnf's own real,
	documented way to enable RPM Fusion) is naturally idempotent -
	already-installed release packages are just a no-op, not an error."""
	return _run_streamed([*_admin(), "dnf", "install", "-y", *_rpmfusion_urls()], log_fn)


def install(log_fn=None):
	"""Blocking, never raises; callers run this off the GTK/Tk thread
	like every other real install in this project. Returns True only if
	RPM Fusion was enabled AND steam installed successfully."""
	if is_installed():
		if log_fn:
			log_fn("steam already installed, skipping.")
		return True
	if not ensure_rpmfusion(log_fn):
		if log_fn:
			log_fn("Could not enable RPM Fusion - aborting.")
		return False
	return _run_streamed([*_admin(), "dnf", "install", "-y", "steam"], log_fn)


if __name__ == "__main__":
	install(log_fn=print)
