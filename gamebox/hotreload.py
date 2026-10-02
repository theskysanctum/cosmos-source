#!/usr/bin/env python3
"""Shared hot-reload watcher for the project's long-running processes
(the launcher and the controller daemon) - watches a set of already-
imported modules' source files by mtime and importlib.reload()s any
that changed, so code edits take effect without restarting the whole
process. That matters here specifically because restarting either of
those means tearing down and rebuilding an X session/consolewm/gamepad
device handles, which is slow and, for the launcher, visibly disruptive.

Short-lived scripts (osk.py, pause_overlay.py, filters/draw_filter.py)
don't need this at all - they're re-spawned fresh from disk
every single time they're used, so they already pick up any code change
on their very next launch with zero extra machinery.

Reload mechanics, and their real limits: reassigning a module-level
function or constant (importlib.reload does this in place, in the same
module object already in sys.modules) takes effect immediately for any
caller that looks the name up by attribute (`module.func(...)`) or as a
bare name inside a function defined in that same module - both resolve
dynamically against the module's __dict__ on every call, not once at
definition time. Re-executing a `class Foo:` statement, however, rebinds
the module's `Foo` name to a brand-new class object; objects already
built from the *old* Foo keep the old class's methods forever - reload
does not retroactively patch existing instances. Callers holding
long-lived instances of a reloadable module's classes (the launcher's
own App window; inputd.py's StickState/RepeatTracker) need to rebuild
those instances after a reload if they want new class behavior to take
effect, which is what each one's on_reload callback is for.

The watched set is auto-discovered (project_modules() below), not a
hand-maintained list - a fixed list is exactly what let this silently go
stale as new modules got added over time (confirmed directly: several
real, actively-used modules were missing from launcher/run.py's own
list before this). Since we no longer package as an AppImage at all
(nothing bundled/frozen - every real deployment is just this live git
checkout run directly), there's no reason any project module should
ever need a process restart to pick up an edit."""
import importlib
import sys
import time
from pathlib import Path

MIN_CHECK_INTERVAL_S = 1.0

REPO_ROOT = Path(__file__).resolve().parent


def project_modules():
	"""Every currently-imported module that's actually part of this
	project's own source tree (anything under REPO_ROOT) - the standard
	library and third-party packages (site-packages, PyGObject's own
	gi.repository bindings, ...) are never included, since reloading
	those is neither wanted nor safe. Call this fresh each time you want
	the current set (see HotReloader.check() below) rather than caching
	it once - new modules get imported well after startup too (a screen
	opened for the first time, a lazily-imported backend), and those
	need to become watchable the moment they exist, not just whatever
	happened to already be loaded when the reloader was constructed."""
	for module in list(sys.modules.values()):
		file = getattr(module, "__file__", None)
		if not file:
			continue
		try:
			Path(file).resolve().relative_to(REPO_ROOT)
		except ValueError:
			continue
		yield module


def _watched_module_paths(modules):
	"""modules: an iterable of already-imported module objects. Returns
	{path: module}, skipping any without a real file on disk (built-ins/
	extension modules have nothing to watch)."""
	paths = {}
	for module in modules:
		file = getattr(module, "__file__", None)
		if file:
			paths[Path(file)] = module
	return paths


class HotReloader:
	def __init__(self, on_reload, logger, modules=project_modules):
		"""modules: a zero-arg callable returning the current iterable of
		module objects to watch - defaults to project_modules() (i.e.
		"literally everything this process has imported from its own
		source tree"), but a narrower callable is still fine for a
		process that genuinely only ever wants a fixed subset watched."""
		self.modules_provider = modules
		self.watched = {}
		self.mtimes = {}
		self.on_reload = on_reload
		self.log = logger
		self._last_check = 0.0
		self._sync_watched()
		self.log.debug(f"hot-reload watching {len(self.watched)} file(s) initially: {sorted(str(p) for p in self.watched)}")

	def _sync_watched(self):
		"""Adds any newly-seen watchable module with its CURRENT mtime as
		a baseline (never flagged as "changed" the moment it's first
		noticed) - existing entries are left untouched, so this is cheap
		to call on every check()."""
		for path, module in _watched_module_paths(self.modules_provider()).items():
			if path in self.watched:
				continue
			self.watched[path] = module
			try:
				self.mtimes[path] = path.stat().st_mtime
			except OSError:
				self.mtimes[path] = None

	def check(self):
		"""Call as often as you like (e.g. every input-loop tick) - actual
		stat() calls are internally throttled to MIN_CHECK_INTERVAL_S, so
		callers don't need to worry about polling too aggressively.
		Reloads every changed module, then calls on_reload() once if
		anything did. Returns True if a reload happened."""
		now = time.monotonic()
		if now - self._last_check < MIN_CHECK_INTERVAL_S:
			return False
		self._last_check = now
		self._sync_watched()

		changed = []
		for path, module in self.watched.items():
			try:
				mtime = path.stat().st_mtime
			except OSError as e:
				self.log.warning(f"hot-reload: could not stat {path}: {e}")
				continue
			if mtime != self.mtimes[path]:
				self.mtimes[path] = mtime
				changed.append((path, module))

		if not changed:
			return False

		for path, module in changed:
			self.log.info(f"hot-reload: {path} changed, reloading {module.__name__}")
			try:
				importlib.reload(module)
			except Exception:
				self.log.exception(f"hot-reload: failed to reload {module.__name__}, keeping the old version")

		self.log.info(f"hot-reload: {len(changed)} module(s) reloaded, rebuilding")
		self.on_reload()
		return True
