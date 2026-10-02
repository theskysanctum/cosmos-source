#!/usr/bin/env python3
"""Real host-level runtime dependencies CosmOS shells out to (confirmed
by grepping every subprocess.run/Popen call across the whole codebase for
a bare external command name - not guessed), plus every real Python
dependency and the devel headers needed to install it. CosmOS is no
longer packaged as an AppImage at all, per direct request - nothing here
is "bundled" any more, PACKAGES/PIP_PACKAGES/SHARED_LIBS/BUILD_DEVEL_PACKAGES
below is just the complete, real dependency set for any machine actually
running CosmOS, whether that's a bare dev-machine checkout (install()/
install_build_deps()/install_python_deps() below) or the bootc image
(atomic/Containerfile runs the equivalent dnf/pip install steps itself,
baked into the image rather than driven from here).

AppMan (emulators/appman.py) itself is still real, generic
infrastructure, but CosmOS no longer has any standalone emulator
left to install through it - xemu (Xbox) and Supermodel (Sega Model 3)
were both removed outright, per direct request, and RetroArch (bundled
straight into the repo, never installed via AppMan) covers everything
else.
Nothing here is a system-level Flatpak any more either - Brave (the
project's one remaining Flatpak dependency) was replaced outright by
webshell/ (a first-party Electron app, fetched at deploy time via
webshell/electron_runtime.py, not installed as a Flatpak), and Steam/
Wine had already moved off Flatpak before that (Steam to a native RPM
Fusion package - packaging/steam_native.py, needed for real Big Picture
theming; Wine built from source straight into the repo, wine/bin/) - so
packaging/flatpak_apps.py itself is gone too, nothing left in the
project using that mechanism at all. The media center used to be a
Flatpak too (Kodi, then Stremio - see git history/the plan file) -
replaced by CosmOS's own native Video/Music/Photos apps
(media/vlc_player.py, media/addons/), which need a real host-level
`libvlc`-equivalent package instead (see PACKAGES below), not a Flatpak
at all.

dnf and apt only - this project has only ever actually run on Fedora
(dnf), and every dnf package name below was confirmed for real against
this exact machine (`rpm -qf $(which <binary>)`), not assumed from the
binary's own name - node/npm/git in particular are NOT what you'd guess.
The apt names were confirmed against Ubuntu's real package database
(packages.ubuntu.com), not this project's own tested machine - real, but
a notch less certain than the dnf side, which is the one this project
actually runs on."""
import ctypes.util
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import debuglog  # noqa: E402

log = debuglog.setup("host_deps")

# Real runtime dependency: bare command name CosmOS's own code shells
# out to -> the real package name that provides it, per package manager.
PACKAGES = {
	"Xorg": {"dnf": "xorg-x11-server-Xorg", "apt": "xserver-xorg-core"},
	"xdotool": {"dnf": "xdotool", "apt": "xdotool"},
	"xset": {"dnf": "xset", "apt": "x11-xserver-utils"},
	"xprop": {"dnf": "xprop", "apt": "x11-utils"},
	"picom": {"dnf": "picom", "apt": "picom"},
	"xrandr": {"dnf": "xrandr", "apt": "x11-xserver-utils"},
	"nmcli": {"dnf": "NetworkManager", "apt": "network-manager"},
	"bluetoothctl": {"dnf": "bluez", "apt": "bluez"},
	"git": {"dnf": "git-core", "apt": "git"},
	# The GitHub CLI - packaging/deploy_gui/deploy_gui.py's own "Clone
	# Repos..." button shells out to `gh repo list`/`gh repo clone`
	# (handles private-repo auth via the account's own existing gh login
	# automatically, unlike a bare `git clone` URL). Fedora's own repos
	# carry it directly; Ubuntu needs GitHub's own apt repo added first
	# (this project only actually runs on Fedora - see this file's own
	# docstring on the apt side being a notch less certain).
	"gh": {"dnf": "gh", "apt": "gh"},
	"node": {"dnf": "nodejs22-bin", "apt": "nodejs"},
	"npm": {"dnf": "nodejs22-npm-bin", "apt": "npm"},
	"paplay": {"dnf": "pulseaudio-utils", "apt": "pulseaudio-utils"},
}

# Real runtime dependency that's a shared library, not a command on
# PATH - python-vlc (see packaging/requirements.txt, installed the
# normal way via install_python_deps() below) only *binds* to a real
# system libvlc via ctypes; it doesn't bundle one itself. Keyed by the bare
# name ctypes.util.find_library() expects (no "lib"/".so*"), checked
# separately from PACKAGES below since `shutil.which()` doesn't apply
# to a library at all. dnf name confirmed for real against this exact
# machine (`rpm -qf $(ldconfig -p | grep libvlc.so.5 | ...)` -> vlc-libs);
# the apt name is the standard Debian/Ubuntu package for the same
# library, a notch less certain than the dnf side per this module's own
# docstring.
SHARED_LIBS = {
	"vlc": {"dnf": "vlc-libs", "apt": "libvlc5"},
}

# Bare command name -> the real PyPI package that provides it, installed
# to the host's own ~/.local/bin - these run as external CLI tools
# CosmOS shells out to (epic/legendary_backend.py, gog/gogdl_backend.py),
# not imported as Python libraries the way telethon/qrcode/etc. (see
# REQUIREMENTS_FILE/install_python_deps() below) are, so they need their
# own separate `pip install --user` rather than living in
# packaging/requirements.txt. Same across every package manager - pip
# doesn't care what installed it.
PIP_PACKAGES = {
	"legendary": "legendary-gl",
	"gogdl": "gogdl",
	# library/preview_video.py's own YouTube-preview resolver shells out
	# to this exactly the same way - never downloads, just resolves a
	# search term to a direct, playable stream URL.
	"yt-dlp": "yt-dlp",
}

# Needed once on any machine that's going to run install_python_deps()
# below (pip-building PyGObject/pycairo from source against this
# machine's own GTK/GObject-Introspection headers) and to compile
# wm/consolewm (packaging/stage_build.sh) - every machine actually
# running CosmOS directly needs these at least once, not just
# whatever machine used to run the now-retired build_appimage.sh.
BUILD_DEVEL_PACKAGES = {
	"dnf": [
		"gcc",
		"gtk3-devel",
		"gobject-introspection-devel",
		"librsvg2-devel",
		"cairo-devel",
		"cairo-gobject-devel",
		"python3-devel",
		"python3-pip",
		"pkgconf-pkg-config",
		"libX11-devel",
	],
	"apt": [
		"build-essential",
		"libgtk-3-dev",
		"libgirepository1.0-dev",
		"librsvg2-dev",
		"libcairo2-dev",
		"python3-dev",
		"python3-pip",
		"pkg-config",
		"libx11-dev",
	],
}

# The real pip dependency list every runnable CosmOS checkout needs
# (PyGObject/pycairo included - built from source against this machine's
# own GTK/GObject-Introspection headers, the BUILD_DEVEL_PACKAGES above)
# - see install_python_deps() below.
REQUIREMENTS_FILE = Path(__file__).resolve().parent / "requirements.txt"

QUERY_INSTALLED = {
	"dnf": lambda pkg: subprocess.run(["rpm", "-q", pkg], capture_output=True).returncode == 0,
	"apt": lambda pkg: subprocess.run(["dpkg", "-s", pkg], capture_output=True).returncode == 0,
}

# Root comes from the kronos admin service on CosmOS (no sudo there), sudo on any other machine.
_ADMIN = ["kronos", "exec", "--"] if shutil.which("kronos") else ["sudo"]
INSTALL_CMD = {
	"dnf": [*_ADMIN, "dnf", "install", "-y"],
	"apt": [*_ADMIN, "apt-get", "install", "-y"],
}


def detect_package_manager():
	"""dnf or apt, whichever is actually on PATH - dnf checked first since
	that's this project's own real, tested machine. None on anything else
	(an unsupported distro) - check()/install() below stay honest about
	that rather than guessing a third package manager's names."""
	if shutil.which("dnf"):
		return "dnf"
	if shutil.which("apt-get"):
		return "apt"
	return None


PACKAGE_MANAGER = detect_package_manager()


def check():
	"""Returns (missing_bin, missing_pip, missing_libs). Never raises.
	Detecting what's missing doesn't depend on which package manager (if
	any) is present - only actually installing anything (install()) does."""
	missing_bin = [b for b in PACKAGES if shutil.which(b) is None]
	missing_pip = [b for b in PIP_PACKAGES if shutil.which(b) is None]
	missing_libs = [lib for lib in SHARED_LIBS if ctypes.util.find_library(lib) is None]
	log.info(
		f"check: package_manager={PACKAGE_MANAGER} missing={missing_bin} missing_pip={missing_pip} "
		f"missing_libs={missing_libs}"
	)
	return missing_bin, missing_pip, missing_libs


def check_build_deps():
	if PACKAGE_MANAGER is None:
		log.warning("check_build_deps: no supported package manager (dnf/apt) found")
		return []
	query = QUERY_INSTALLED[PACKAGE_MANAGER]
	missing = [pkg for pkg in BUILD_DEVEL_PACKAGES[PACKAGE_MANAGER] if not query(pkg)]
	log.info(f"check_build_deps: package_manager={PACKAGE_MANAGER} missing={missing}")
	return missing


def _run_streamed(cmd, log_fn):
	proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
	for line in proc.stdout:
		log.debug(line.rstrip())
		if log_fn:
			log_fn(line.rstrip())
	proc.wait()
	return proc.returncode == 0


def install(binaries, log_fn=None):
	"""Installs whichever of `binaries` (bare command/library names, keys
	of PACKAGES/PIP_PACKAGES/SHARED_LIBS) are given - real subprocess
	calls, streamed line by line to log_fn if given. Returns True if
	every requested install succeeded. If no supported package manager
	was found, this can't do anything useful - logs what to install by
	hand instead of guessing a command that would just fail."""
	pip_pkgs = sorted({PIP_PACKAGES[b] for b in binaries if b in PIP_PACKAGES})
	native_names = [b for b in binaries if b in PACKAGES or b in SHARED_LIBS]

	if PACKAGE_MANAGER is None and native_names:
		if log_fn:
			log_fn(
				"No supported package manager (dnf or apt) found - install these "
				f"manually: {', '.join(sorted(native_names))}"
			)
		ok = False
	else:
		ok = True
		native_pkgs = sorted(
			{PACKAGES[b][PACKAGE_MANAGER] for b in native_names if b in PACKAGES}
			| {SHARED_LIBS[b][PACKAGE_MANAGER] for b in native_names if b in SHARED_LIBS}
		)
		if native_pkgs:
			cmd = [*INSTALL_CMD[PACKAGE_MANAGER], *native_pkgs]
			if log_fn:
				log_fn(f"== {' '.join(cmd)} ==")
			ok = _run_streamed(cmd, log_fn) and ok

	if pip_pkgs:
		if log_fn:
			log_fn(f"== pip install --user {' '.join(pip_pkgs)} ==")
		ok = _run_streamed([sys.executable, "-m", "pip", "install", "--user", *pip_pkgs], log_fn) and ok

	return ok


def install_build_deps(log_fn=None):
	if PACKAGE_MANAGER is None:
		if log_fn:
			log_fn("No supported package manager (dnf or apt) found - install build-time dependencies manually.")
		return False
	missing = check_build_deps()
	if not missing:
		if log_fn:
			log_fn("All build-time dependencies already present.")
		return True
	cmd = [*INSTALL_CMD[PACKAGE_MANAGER], *missing]
	if log_fn:
		log_fn(f"== {' '.join(cmd)} ==")
	return _run_streamed(cmd, log_fn)


def install_python_deps(log_fn=None):
	"""Installs every real pip dependency CosmOS needs (packaging/
	requirements.txt - PyGObject/pycairo included) straight onto this
	machine's own Python - the one new piece of real work needed to make
	"just run python3 launcher/run.py on a bare checkout" work with zero
	AppImage ever built. Always runs unconditionally (pip itself is
	idempotent and fast to no-op when already satisfied) rather than
	trying to detect what's missing first - there's no cheap, reliable
	"is PyGObject importable" check the way PACKAGES' shutil.which() is
	for a bare command. install_build_deps() should run first (or at
	least once, ever, on this machine) so PyGObject/pycairo have real
	GTK/GObject-Introspection headers to build against."""
	if not REQUIREMENTS_FILE.exists():
		log.error(f"install_python_deps: {REQUIREMENTS_FILE} not found")
		return False
	cmd = [sys.executable, "-m", "pip", "install", "--user", "-r", str(REQUIREMENTS_FILE)]
	if log_fn:
		log_fn(f"== {' '.join(cmd)} ==")
	return _run_streamed(cmd, log_fn)
