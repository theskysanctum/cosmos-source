#!/usr/bin/env python3
"""
Runs a program as an ordinary client inside the launcher's own
already-running X session, for that program's whole lifetime
(run_graphical()) - one level up from how app.py already does
subprocess.run/wait/show for plain entries. Filter-wrapped games
(filters.run_with_filter()) work the same way.

This project used to also support a DRM/KMS-direct "console" launch mode
(run_console()) that switched away to a dedicated runtime VT and back,
for entries that couldn't run inside X at all - removed once nothing
(no config.ini entry, no systems.json platform) used it anymore: every
real entry runs graphical, inside this one X session, and the setuid-root
helper that mode needed (wm/vtswitch) went with it.
"""
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pause"))
import pause_menu  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "osk"))
import osk_watcher  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import debuglog  # noqa: E402

log = debuglog.setup("graphical")

CONSOLEWM = str(Path(__file__).resolve().parent.parent / "wm" / "consolewm")
SET_BLANK_CURSOR = str(Path(__file__).resolve().parent / "set_blank_cursor.py")
SET_WINDOW_BLANK_CURSOR = str(Path(__file__).resolve().parent / "set_window_blank_cursor.py")

LAUNCHER_TTY = 1


def reset_cursor(env):
	"""Re-asserts the blank root cursor (see set_blank_cursor.py) -
	called once at X startup, but also every time a separate program's
	session ends (run_graphical(), and filters.py for games), not just
	then: a program closing in some way other than its normal quit flow
	(confirmed directly with Brave closed via Alt+F4) can still leave
	X11's own un-themed default cursor - a plain bitmap "X" glyph -
	showing back on the launcher, and there's no reliable way to know in
	advance which closing path a given program will take the pointer
	through. Cheap and idempotent, so just re-running it unconditionally
	on every return-to-launcher is simpler than trying to detect which
	cases actually need it."""
	subprocess.run(["python3", SET_BLANK_CURSOR], env=env, capture_output=True, text=True)


def configure_ttys(launcher_tty):
	"""Called once at startup (by launcher/run.py) to point this at
	whichever VT the launcher was actually started on - LAUNCHER_TTY=1 is
	the real production default, not a hardcoded truth."""
	global LAUNCHER_TTY
	log.info(f"configuring launcher_tty={launcher_tty}")
	LAUNCHER_TTY = launcher_tty


def _display_paths(display):
	num = display.lstrip(":")
	return Path(f"/tmp/.X{num}-lock"), Path(f"/tmp/.X11-unix/X{num}")


def _display_is_live(socket_path):
	"""A lock/socket file existing on disk doesn't mean a server is still
	behind it - the only way to actually know is to try connecting. This
	matters: display :0 in particular can legitimately belong to a real,
	already-running desktop session (e.g. lightdm) rather than to this
	app, on any machine that isn't a dedicated console-os install, and
	deleting a *live* server's files out from under it (confirmed the
	hard way - see the session that added this comment) is a much worse
	failure than leaving a genuinely stale one in place."""
	if not socket_path.exists():
		return False
	try:
		with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
			sock.settimeout(0.5)
			sock.connect(str(socket_path))
		return True
	except OSError:
		return False


def _clear_stale_display(display):
	"""Xorg removes its own lock file (/tmp/.X{N}-lock) and socket
	(/tmp/.X11-unix/X{N}) on a clean exit, but this app tears its X
	session down and restarts a fresh one on every single game/filter
	launch - if any one of those got killed ungracefully (a crash, a
	SIGKILL) the files can survive. A stale lock makes the *next* Xorg
	refuse to start at all ("Server is already active for display"); a
	stale socket is worse, since it makes _wait_for_display() below
	falsely report the new X server up immediately (the file already
	exists, nothing is listening behind it), so consolewm then fails to
	connect and retries into a "cannot open display" error - repeatably,
	on every subsequent launch, since nothing ever clears the stale file
	on its own.

	Only removes files confirmed dead via _display_is_live() - never on
	existence alone. If `display` turns out to be genuinely live (another
	real X server, not a leftover), this deliberately leaves it in place
	and lets the Xorg-fails-to-bind error below surface instead - the
	fix for that is picking a different DISPLAY, not deleting someone
	else's files."""
	lock_path, socket_path = _display_paths(display)
	if _display_is_live(socket_path):
		log.debug(f"{socket_path} is a live X server, leaving it alone")
		return
	for path in (lock_path, socket_path):
		if path.exists():
			log.warning(f"removing stale X display file from a previous session: {path}")
			path.unlink()


def _wait_for_display(display, x_proc, timeout=10):
	log.debug(f"waiting for X socket on {display} (timeout={timeout}s)")
	_, socket_path = _display_paths(display)
	deadline = time.time() + timeout
	while time.time() < deadline:
		if x_proc.poll() is not None:
			log.error(f"Xorg exited immediately (code {x_proc.returncode}) before {display} came up")
			raise RuntimeError(
				f"Xorg exited immediately with code {x_proc.returncode} - check Xorg's own log "
				f"(e.g. /var/log/Xorg.{display.lstrip(':')}.log) for the real reason"
			)
		if socket_path.exists():
			log.debug(f"X socket {socket_path} appeared")
			return
		time.sleep(0.1)
	log.error(f"X server on {display} did not come up within {timeout}s")
	raise TimeoutError(f"X server on {display} did not come up within {timeout}s")


def _wait_for_wm(env, timeout=10):
	log.debug(f"waiting for a window manager to claim {env.get('DISPLAY')} (timeout={timeout}s)")
	deadline = time.time() + timeout
	while time.time() < deadline:
		result = subprocess.run(["xprop", "-root", "_NET_SUPPORTING_WM_CHECK"], capture_output=True, text=True, env=env)
		if result.returncode == 0 and "not found" not in result.stdout:
			log.debug("window manager present")
			return
		time.sleep(0.1)
	log.error(f"window manager did not come up within {timeout}s on {env.get('DISPLAY')}")
	raise TimeoutError(f"window manager did not come up within {timeout}s")


def _disable_screen_blanking(env):
	"""A console/kiosk display must never sleep or blank mid-game or
	mid-movie - X11's own screensaver timeout and DPMS power-down are
	still active by default even though nothing in this project ever
	wants them, so every X session this app starts gets them turned off
	here, once, rather than relying on each individual game/app to
	remember to. Best-effort: some virtual/test X servers (Xvfb, Xephyr)
	don't implement DPMS at all, so a failure here is logged and ignored
	rather than treated as fatal - it's a real console display's
	requirement, not a hard dependency of getting an X session up.

	`dpms force on` runs first, before disabling DPMS - `-dpms` only
	stops *future* blanking, it does nothing for a monitor DPMS has
	already powered down (confirmed directly: a real monitor stayed dark
	even after this function's other three calls all succeeded, until
	`force on` was added). `dpms force on` also only works while DPMS is
	still enabled, so it has to run *before* `-dpms` disables it, not
	after - the other order silently no-ops."""
	subprocess.run(["xset", "dpms", "force", "on"], env=env, capture_output=True, text=True)
	for args in (["s", "off"], ["s", "noblank"], ["-dpms"]):
		result = subprocess.run(["xset", *args], env=env, capture_output=True, text=True)
		if result.returncode != 0:
			log.debug(f"xset {' '.join(args)} failed (expected on some virtual X servers): {result.stderr.strip()}")


def start_x(x_binary="Xorg", display=":9", vt=LAUNCHER_TTY, use_vt_args=True, start_wm=True, screen_size=None):
	"""Start an X session (with consolewm managing it, by default) and wait
	for both to come up.

	x_binary/use_vt_args are overridable so tests can substitute a headless
	virtual server (e.g. Xvfb, which takes no vt argument and needs no
	root) for the real Xorg-on-a-real-vt used in production. run.py's own
	launcher session passes its own real display/vt explicitly rather
	than relying on this default; run_graphical()/filters.run_with_filter()
	don't call this at all anymore, since games/the browser run inside
	whichever X session is already up rather than starting one of their
	own.

	screen_size ("WIDTHxHEIGHT") only makes sense for a virtual test server
	like Xephyr (Xorg on real hardware picks its own real resolution) -
	Xephyr's own default screen is small enough (640x480 seen live) to
	clip real page content below the fold during UI dev testing, so a
	real 720p test session always passes "1280x720" here rather than
	leaving it to that default.

	Returns (x_proc, wm_proc) - wm_proc is None if start_wm=False.
	"""
	_clear_stale_display(display)
	x_args = [x_binary, display, f"vt{vt}", "-nolisten", "tcp"] if use_vt_args else [x_binary, display]
	if screen_size:
		x_args += ["-screen", screen_size]
	log.info(f"starting X: {x_args}")
	x_proc = subprocess.Popen(x_args)
	_wait_for_display(display, x_proc)
	env = {**os.environ, "DISPLAY": display}
	_disable_screen_blanking(env)
	reset_cursor(env)

	wm_proc = None
	if start_wm:
		log.info(f"starting consolewm on {display}")
		wm_proc = subprocess.Popen([CONSOLEWM], env=env)
		_wait_for_wm(env)

	log.info(f"X session on {display} ready (x_pid={x_proc.pid}, wm_pid={wm_proc.pid if wm_proc else None})")
	return x_proc, wm_proc


def stop_x(x_proc, wm_proc=None):
	log.info(f"stopping X session (x_pid={x_proc.pid}, wm_pid={wm_proc.pid if wm_proc else None})")
	if wm_proc:
		wm_proc.terminate()
		wm_proc.wait(timeout=5)
	x_proc.terminate()
	x_proc.wait(timeout=5)
	log.debug("X session stopped")


def find_window_for_pid(pid, env, timeout=10, settle=0.5):
	"""Waits for a *visible* (mapped) window, not just an existing one - a
	freshly-created window's own app can still be setting its initial
	geometry, and positioning it too early gets silently overwritten by
	that. --onlyvisible plus a short settle delay avoids that race.
	env must include the target DISPLAY - it is not inherited automatically."""
	log.debug(f"waiting for a visible window for pid {pid} on {env.get('DISPLAY')} (timeout={timeout}s)")
	deadline = time.time() + timeout
	while time.time() < deadline:
		result = subprocess.run(
			["xdotool", "search", "--onlyvisible", "--pid", str(pid)], capture_output=True, text=True, env=env
		)
		window_ids = result.stdout.split()
		if window_ids:
			log.debug(f"found window {window_ids[-1]} for pid {pid}, settling {settle}s")
			time.sleep(settle)
			return window_ids[-1]
		time.sleep(0.2)
	log.warning(f"no visible window found for pid {pid} within {timeout}s")
	return None


def find_window_for_class(window_class, env, timeout=10, settle=0.5):
	"""Same shape as find_window_for_pid(), matching by WM_CLASS instead
	of PID - some programs (Brave/Chromium chief among them) fork or
	daemonize in a way that leaves the pid subprocess.Popen sees
	completely disconnected from whatever process actually ends up
	owning the real window, making PID-based lookup silently never find
	anything for them - confirmed directly: find_window_for_pid timed
	out its full 10s even with a real, visibly-mapped Brave window
	already on screen the whole time."""
	log.debug(f"waiting for a visible window with class {window_class!r} on {env.get('DISPLAY')} (timeout={timeout}s)")
	deadline = time.time() + timeout
	while time.time() < deadline:
		result = subprocess.run(
			["xdotool", "search", "--onlyvisible", "--class", window_class], capture_output=True, text=True, env=env
		)
		window_ids = result.stdout.split()
		if window_ids:
			log.debug(f"found window {window_ids[-1]} for class {window_class!r}, settling {settle}s")
			time.sleep(settle)
			return window_ids[-1]
		time.sleep(0.2)
	log.warning(f"no visible window found for class {window_class!r} within {timeout}s")
	return None


def position_window(window_id, rect, env):
	x, y, width, height = rect
	log.debug(f"positioning window {window_id} at {rect}")
	subprocess.run(["xdotool", "windowsize", window_id, str(width), str(height)], check=True, env=env)
	subprocess.run(["xdotool", "windowmove", window_id, str(x), str(y)], check=True, env=env)

def focus_window(window_id, env):
	log.debug(f"focusing window {window_id}")
	subprocess.run(["xdotool", "windowfocus", window_id], env=env)


def get_display_geometry(env):
	"""(width, height) of the real display - used by callers that need to
	force a launched window to the actual full screen size themselves
	(see run_graphical()'s window_geometry param) rather than trusting
	the program's own --start-maximized/fullscreen handling."""
	result = subprocess.run(["xdotool", "getdisplaygeometry"], capture_output=True, text=True, env=env, check=True)
	width, height = result.stdout.split()
	return int(width), int(height)


def run_graphical(cmd, window_geometry=None, window_class=None, blank_cursor_on_map=False, env_overrides=None, cwd=None):
	"""Runs cmd as an ordinary client inside the launcher's own already-
	running X session (same DISPLAY, same consolewm instance) rather than
	spinning up a second Xorg on another VT and chvt'ing over to it -
	there is only one VT/X session for the whole app's lifetime now.
	Callers hide the launcher's own window first (see app.py) so cmd's
	window is the only thing visible while it runs; nothing here needs to
	manage that, consolewm already handles whatever client maps itself.

	window_geometry, if given, is an (x, y, width, height) tuple this
	forces the launched window to right after it maps - for a program
	whose own --start-maximized/window-placement handling turned out
	unreliable (see brave_launcher.py's own comment on exactly that:
	--force-device-scale-factor interacting badly with Chromium's own
	maximize sizing, confirmed directly by screenshotting the result),
	sidestepping whatever internal sizing decision it makes rather than
	trying to convince it to make the right one itself.

	window_class, if given alongside window_geometry, looks the window
	up by WM_CLASS (find_window_for_class()) instead of by pid - Brave
	needs this specifically: subprocess.Popen's own pid for it turned
	out completely disconnected from whatever process really ends up
	owning the browser's window (confirmed directly: find_window_for_pid
	timed out its full 10s with a real, visibly-mapped Brave window
	already on screen), some fork/daemonize step in Brave's own startup
	the pid-based lookup has no way to follow.

	blank_cursor_on_map, if given alongside window_geometry, blanks
	that specific window's own X11 cursor (see set_window_blank_cursor.py)
	the moment it's found - only Brave needs this: a real report of a
	brief flash of the plain X11 arrow right as its window first
	appears, before dot_cursor_extension/'s own CSS has anything loaded
	yet to apply "cursor: none" to.

	env_overrides, if given, is a dict merged into the child's environment
	- e.g. library.py's launch_game() redirects HOME for any emulator
	that isn't already self-contained/portable, so its native save/config
	writes land under /cosmos instead of the real user's home.

	cwd, if given, is the child's working directory - only mkxp-z (see
	systems.json's rpgmaker_xp/vx/vxace entries and mkxp_config.py) needs
	this: it reads its own config from a bare relative "mkxp.json" path,
	resolved against whatever the process's CWD happens to be at start,
	with no CLI flag to point it elsewhere. Every other platform leaves
	this at the default (None - inherits the launcher's own CWD, the
	existing behavior)."""
	env = dict(os.environ)
	if env_overrides:
		env.update(env_overrides)
	log.info(
		f"run_graphical: {cmd} window_geometry={window_geometry} window_class={window_class} "
		f"blank_cursor_on_map={blank_cursor_on_map} env_overrides={env_overrides} cwd={cwd}"
	)
	game_proc = subprocess.Popen(cmd, env=env, cwd=cwd)
	log.debug(f"launched {cmd} as pid {game_proc.pid}")

	if window_geometry:
		window_id = find_window_for_class(window_class, env) if window_class else find_window_for_pid(game_proc.pid, env)
		if window_id:
			if blank_cursor_on_map:
				subprocess.run(
					["python3", SET_WINDOW_BLANK_CURSOR, window_id], env=env, capture_output=True, text=True
				)
			position_window(window_id, window_geometry, env)
		else:
			log.warning(f"never found a window ({window_class=}), leaving window_geometry {window_geometry} unapplied")

	osk_stop_event = threading.Event()
	osk_thread = threading.Thread(target=osk_watcher.watch, args=(env, osk_stop_event), daemon=True)
	osk_thread.start()
	log.debug("OSK-watcher thread started for this session")

	pause_menu.supervise(game_proc, env)
	log.info(f"{cmd} exited with code {game_proc.returncode}")

	osk_stop_event.set()
	osk_thread.join(timeout=5)
	reset_cursor(env)
