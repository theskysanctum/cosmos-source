#!/usr/bin/env python3
"""Entry point for the persistent GTK launcher. Deliberately thin and
rarely edited: everything that actually benefits from hot-reload
(screens, backend logic) lives in app.py and the modules it imports,
watched by hotreload.HotReloader and reloaded in place while this
process's X session, consolewm, and OSK-watcher thread stay up for its
whole run - editing UI/logic code should never mean losing your place,
flickering the display, or dropping the gamepad."""
import os
import subprocess
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import graphical  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import debuglog  # noqa: E402
import hotreload  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "osk"))
import osk_watcher  # noqa: E402

log = debuglog.setup("launcher.run")

# input/run.py's own real process entry point - confirmed live this was
# never actually spawned anywhere in the whole repo (not here, not
# startx.sh, no systemd unit, no AppImage AppRun hook), meaning
# inputd.py's entire menu-navigation translation - and the virtual
# gamepad bridge built alongside it - never ran on a real deployed
# system at all until now. A real subprocess (its own hot-reload loop
# targets a different module set than this process's own, and it opens
# real UInput devices independent of anything GTK/X-related here), not
# a thread the way osk_watcher is - same "started once, stays up for
# the launcher's whole life" lifecycle either way.
INPUT_RUN_PATH = Path(__file__).resolve().parent.parent / "input" / "run.py"

# Same "real subprocess, started once, up for the launcher's whole life"
# shape as INPUT_RUN_PATH - see peripherals/device_limits.py's own
# docstring for what this one actually enforces (external audio-output/
# microphone/camera connection caps, alongside the controller cap
# input/run.py already carries).
PERIPHERALS_RUN_PATH = Path(__file__).resolve().parent.parent / "peripherals" / "run.py"

# Not :0 - on real console-os hardware nothing else would be using it, but
# this app is routinely tested on a normal desktop machine that already
# has an lightdm/desktop session legitimately owning :0, and colliding
# with that makes Xorg refuse to bind the display outright. graphical.
# run_graphical()/filters.run_with_filter() no longer start a session of
# their own at all - games/the browser run as ordinary clients inside
# this one display, for the launcher's whole lifetime.
LAUNCHER_DISPLAY = ":8"
RELOAD_CHECK_MS = 300

# Set only by launcher/startx.sh, the real boot entry point - hot-reload's
# whole point is watching live .py files during development, and this
# stays unset for every dev entry point (run_on_vt.sh, Deploy GUI's own
# test button), so this changes nothing about today's always-on dev
# behavior.
PACKAGED = os.environ.get("COSMOS_PACKAGED") == "1"

# Bound inside main(), after start_x() below has a real server running on
# `display` - see main()'s own comment for why the ordering matters. Named
# here (rather than left to spring into existence via `global`) just so
# it's obvious from a read of the top of this file that they're not simply
# missing.
Gtk = None
GLib = None
GdkPixbuf = None
theme = None
app_module = None


class LauncherRunner:
	"""Owns the one long-lived App window and rebuilds it (destroy the old
	one, construct a fresh app_module.App()) whenever hotreload.py says
	anything it or its dependencies pulled in changed on disk. Reassigned
	functions/constants inside those modules take effect immediately on
	their own - this rebuild is specifically for changes to the App class
	itself (new/changed screens), which reload can't retroactively patch
	onto the already-running instance.

	graphical.py is one of the reloaded modules, and importlib.reload()
	re-executes its module-level `LAUNCHER_TTY = 1` statement
	unconditionally - silently reverting whatever main()'s own
	graphical.configure_ttys() call had set, e.g. back to the wrong VT for
	a launcher started on a non-default one. rebuild() re-asserts the real
	configuration every time it runs (on every reload, not just once at
	startup) so this can't silently drift again."""

	def __init__(self, launcher_tty):
		self.launcher_tty = launcher_tty
		self.window = None
		# Shown immediately (see _build_splash()) so something real is on
		# screen the instant X is up, rather than a black screen for
		# however long the first real App() build takes - destroyed the
		# moment the first rebuild() below finishes, and NEVER shown again
		# on a later hot-reload-triggered rebuild (self.window is only
		# None for this very first call) - editing code during dev must
		# never re-flash a splash over the already-running app.
		self.splash = self._build_splash()
		# Set by App.activate_profile() to ask for the same rebuild a
		# hot-reloaded file change already triggers - a profile switch
		# needs every profile-scoped screen (quick launch row, widget row,
		# RA fetch, Shop destination picker) rebuilt from scratch against
		# the newly-active profile, which this reuses rather than
		# hand-refreshing each one individually. Checked in tick(),
		# alongside the existing hot-reload check.
		self.restart_requested = threading.Event()
		# modules= deliberately omitted - hotreload.HotReloader's own
		# default (project_modules()) auto-discovers literally everything
		# this process has imported from the project's own source tree,
		# not a hand-maintained list (which is exactly what silently went
		# stale here before - several real, actively-used modules had
		# drifted out of sync with what app.py actually imports).
		self.reloader = None if PACKAGED else hotreload.HotReloader(on_reload=self.rebuild, logger=log)
		self.rebuild()

	def _build_splash(self):
		"""A real first-boot splash - the CosmOS logo front and center
		over the exact same background every other screen already uses
		(launcher/backgrounds/galaxy.jpg, with the same dark overlay
		gradient - see launcher/themes/console_collision.css's own
		window{} rule, mirrored here rather than imported since this
		window exists entirely outside the App/theme.py machinery, before
		either is built), with a "CosmOS" wordmark underneath in Rajdhani
		Bold - the project's own established gaming-font Google Font,
		already bundled in launcher/fonts/ and installed by
		install_fonts.sh (main() runs that before this is ever built, so
		the font is already registered by the time this CSS references
		it), not a new font dependency. Undecorated and fullscreened so
		it reads as a real boot screen, not a stray window. Nothing
		showed here at all before this - a black screen the whole time X
		was coming up and the real App() was being built."""
		splash = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
		splash.set_decorated(False)
		splash.fullscreen()

		background_path = Path(__file__).resolve().parent / "backgrounds" / "galaxy.jpg"
		css = (
			"window { background-color: #06080e; "
			f'background-image: linear-gradient(rgba(6,8,14,0.82), rgba(6,8,14,0.9)), url("{background_path}"); '
			"background-size: cover; background-position: center; background-repeat: no-repeat; } "
			'.cosmos-splash-title { font-family: "Rajdhani"; font-weight: bold; font-size: 54px; '
			"color: #ff0090; letter-spacing: 6px; }"
		)
		provider = Gtk.CssProvider()
		provider.load_from_data(css.encode())
		splash.get_style_context().add_provider(provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

		box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
		box.set_halign(Gtk.Align.CENTER)
		box.set_valign(Gtk.Align.CENTER)

		if app_module.LOGO_PATH.exists():
			logo_pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(str(app_module.LOGO_PATH), -1, 320, True)
			logo_image = Gtk.Image.new_from_pixbuf(logo_pixbuf)
			logo_image.set_halign(Gtk.Align.CENTER)
			box.pack_start(logo_image, False, False, 0)

		title = Gtk.Label(label="CosmOS")
		title.get_style_context().add_class("cosmos-splash-title")
		# GTK doesn't cascade a provider added to a parent's style context
		# down to a child's own separate context (same reasoning as
		# app.py's own apply_backdrop()) - the .cosmos-splash-title rule
		# above only actually takes effect once this exact widget's own
		# context has the provider too, confirmed live (the label
		# rendered as a plain default-styled GtkLabel without this).
		title.get_style_context().add_provider(provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
		title.set_halign(Gtk.Align.CENTER)
		box.pack_start(title, False, False, 0)

		splash.add(box)
		splash.show_all()
		# show_all() only QUEUES the map/draw - nothing actually flushes it
		# to the X server until the GTK main loop runs, which doesn't
		# happen until main() calls Gtk.main() further down, well after
		# this whole splash-then-rebuild sequence has already finished
		# (confirmed live: the splash never painted at all without this -
		# it built, "showed", and was destroyed again by rebuild() before
		# a single frame ever reached the display). Draining the event
		# queue here forces it to actually paint right now.
		while Gtk.events_pending():
			Gtk.main_iteration()
		return splash

	def rebuild(self):
		log.info("(re)building launcher window")
		graphical.configure_ttys(self.launcher_tty)
		first_build = self.window is None
		if self.window is not None:
			log.debug("destroying previous launcher window")
			self.window.destroy()
		theme.load_theme()
		self.window = app_module.App(restart_requested=self.restart_requested, force_profile_picker=first_build)
		self.window.show_all()
		log.debug("launcher window shown")
		if self.splash is not None:
			self.splash.destroy()
			self.splash = None

	def tick(self):
		if self.reloader is not None:
			self.reloader.check()
		if self.restart_requested.is_set():
			self.restart_requested.clear()
			self.rebuild()
		return True


def main(display=LAUNCHER_DISPLAY, x_binary="Xorg", use_vt_args=True, start_wm=True, vt=graphical.LAUNCHER_TTY, screen_size=None):
	install_fonts = str(Path(__file__).resolve().parent / "install_fonts.sh")
	log.debug(f"ensuring bundled fonts are installed via {install_fonts}")
	subprocess.run([install_fonts], check=True)

	log.info(f"starting X on {display} vt{vt} (binary={x_binary}, use_vt_args={use_vt_args}, start_wm={start_wm})")
	x_proc, wm_proc = graphical.start_x(x_binary, display, vt, use_vt_args, start_wm, screen_size=screen_size)
	os.environ["DISPLAY"] = display
	env = {**os.environ, "DISPLAY": display}
	log.debug("X and consolewm up")

	# Gtk/Gdk - and theme.py/app.py, which pull in Gtk/Gdk themselves at
	# their own module scope, along with everything app.py in turn imports
	# - must not be imported until AFTER the X server above is genuinely
	# up. PyGObject connects to the display as a side effect of importing
	# Gtk itself, a ONE-SHOT attempt made at that exact moment: if nothing
	# is listening yet on `display` (true the very first time this ever
	# runs, unavoidably, since start_x() itself can only run after Python
	# has already finished loading whatever this file imports at the top)
	# the connection fails and never retries, even once a real server
	# comes up moments later on that same display - the launcher window
	# then either fails outright or (worse, confirmed directly) silently
	# connects to whatever display happened to be ambient in this
	# process's environment at import time instead, e.g. a real desktop
	# session's :0 if this was launched from an interactive terminal
	# there. Deferring these specific imports to here, now that a server
	# genuinely exists on `display`, is the fix - `global` so
	# LauncherRunner's methods (defined above, but not called until after
	# this point) can still reach them as plain module-level names.
	global Gtk, GLib, GdkPixbuf, theme, app_module
	import gi

	gi.require_version("Gtk", "3.0")
	gi.require_version("GdkPixbuf", "2.0")
	from gi.repository import GLib, Gtk, GdkPixbuf

	import theme
	import app as app_module  # app.py's own import strips LD_LIBRARY_PATH once it's done - see its own comment

	# Games get the OSK for free via run_graphical()'s own osk_watcher
	# thread; the launcher needs the same thing for its own text-entry
	# screens (Wi-Fi password, IGDB credentials, file manager rename/
	# new-folder) - same BTN_SELECT toggle, same mechanism, started here.
	osk_stop_event = threading.Event()
	osk_thread = threading.Thread(target=osk_watcher.watch, args=(env, osk_stop_event), daemon=True)
	osk_thread.start()
	log.debug("OSK-watcher thread started")

	# See INPUT_RUN_PATH's own comment - a real, separate process, not a
	# thread, so it gets its own Popen rather than joining osk_thread's
	# threading.Event-based stop pattern. No DISPLAY/X dependency at all
	# (pure evdev/UInput), so it doesn't need `env` - started as soon as
	# possible regardless, same "up for the launcher's whole life" spirit.
	input_proc = subprocess.Popen([sys.executable, str(INPUT_RUN_PATH)])
	log.debug(f"input daemon started (pid {input_proc.pid})")

	peripherals_proc = subprocess.Popen([sys.executable, str(PERIPHERALS_RUN_PATH)])
	log.debug(f"peripherals daemon started (pid {peripherals_proc.pid})")

	try:
		runner = LauncherRunner(vt)
		GLib.timeout_add(RELOAD_CHECK_MS, runner.tick)
		log.info("entering Gtk.main()")
		Gtk.main()
	finally:
		log.info("Gtk.main() returned, tearing down")
		osk_stop_event.set()
		osk_thread.join(timeout=5)
		input_proc.terminate()
		try:
			input_proc.wait(timeout=5)
		except subprocess.TimeoutExpired:
			log.warning("input daemon didn't exit after terminate(), killing it")
			input_proc.kill()
		peripherals_proc.terminate()
		try:
			peripherals_proc.wait(timeout=5)
		except subprocess.TimeoutExpired:
			log.warning("peripherals daemon didn't exit after terminate(), killing it")
			peripherals_proc.kill()
		graphical.stop_x(x_proc, wm_proc)


if __name__ == "__main__":
	# Optional positional arg: which VT to run the launcher on (default
	# graphical.LAUNCHER_TTY, i.e. vt1). A plain sys.argv check rather
	# than argparse, matching this project's other entry points, none of
	# which use argparse either.
	vt = int(sys.argv[1]) if len(sys.argv) > 1 else graphical.LAUNCHER_TTY
	main(vt=vt)
