#!/usr/bin/env python3
"""CosmOS's own add-on format - originally for Video/Music/Photos
uniformly, now Music/Photos only: Video moved to the real Stremio
add-on protocol instead (see media/stremio_addon.py's own docstring -
"we are just going to use stremio addons", direct instruction). Stremio
has no music/photos equivalent at all, so this format - and its
CosmOS-addons GitHub repo (addon_repo.py) - are still the real thing
for those two, unchanged.

An add-on is a plain directory under media/addons/<type>/<addon_id>/
(<type> is now just "music" or "photos" in practice, though nothing
here hardcodes that - a "video" add-on directory would still work if
one ever existed again):

	addon.json  - {"name": ..., "description": ...}, the same
	              just-enough-to-display shape dats.resolve()'s own
	              fallback dict already uses elsewhere in this project.
	addon.py    - a plain Python module implementing a small, fixed
	              interface (see load_addon()'s own docstring for what
	              each type's module needs to provide).

"local_files" is the one add-on every type ships with by default (see
media/addons/<type>/local_files/) - it's what actually backs each
type's main categories screen (app.py's build_video_categories_page()
and friends), scanning media_backend.py's own real folders. list_addons()
deliberately excludes it: the "Add-ons" entry at the bottom of each
categories screen is for browsing/managing anything installed *beyond*
Local Files, not an alternate path to the same content."""
import importlib.util
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import debuglog  # noqa: E402

log = debuglog.setup("addon_backend")

ADDONS_ROOT = Path(__file__).resolve().parent / "addons"
BUILTIN_ADDON_ID = "local_files"


def list_addons(media_type, include_builtin=False):
	"""[{"id", "name", "description"}, ...] for every add-on installed
	under media/addons/<media_type>/, sorted by name. Skips anything
	whose addon.json is missing or unparsable rather than raising - one
	broken add-on shouldn't take the whole list down. Excludes the
	built-in Local Files add-on unless include_builtin is set (see this
	module's own docstring for why)."""
	type_dir = ADDONS_ROOT / media_type
	if not type_dir.is_dir():
		return []
	addons = []
	for addon_dir in sorted(type_dir.iterdir()):
		if not addon_dir.is_dir():
			continue
		if addon_dir.name == BUILTIN_ADDON_ID and not include_builtin:
			continue
		manifest_path = addon_dir / "addon.json"
		if not manifest_path.is_file():
			continue
		try:
			with open(manifest_path) as f:
				manifest = json.load(f)
			addons.append({"id": addon_dir.name, "name": manifest.get("name", addon_dir.name), "description": manifest.get("description", "")})
		except (OSError, ValueError):
			log.exception(f"failed to read {manifest_path}")
	addons.sort(key=lambda a: a["name"])
	log.debug(f"list_addons({media_type}, include_builtin={include_builtin}): {[a['id'] for a in addons]}")
	return addons


def uninstall(media_type, addon_id):
	"""Deletes media/addons/<media_type>/<addon_id>/ entirely - real,
	irreversible removal, gated by the caller's own confirm() dialog
	(see launcher/app.py's delete_selected_addon()). Refuses to touch
	the built-in Local Files add-on - list_addons() already treats it as
	not a real "installed add-on" at all, and every type's main
	categories screen depends on its directory actually being there."""
	if addon_id == BUILTIN_ADDON_ID:
		raise ValueError("cannot uninstall the built-in Local Files add-on")
	addon_dir = ADDONS_ROOT / media_type / addon_id
	if addon_dir.is_dir():
		shutil.rmtree(addon_dir)
	log.info(f"uninstalled add-on {addon_id!r} ({media_type})")


def load_addon(media_type, addon_id):
	"""Imports media/addons/<media_type>/<addon_id>/addon.py as a fresh
	module and returns it - never cached, matching this project's own
	hot-reload standard (see [[feedback_hotreload_and_verbose_debug_standard]]):
	an add-on edited on disk takes effect on its very next use, not just
	after a restart.

	Video/Music add-on modules provide:
		list_categories() -> [{"id", "name"}, ...] (optional - a module
			with no real categories can omit this; callers treat a
			missing function the same as an empty list)
		list_items(category_id) -> [{"id", "title", "path", ...}, ...] -
			category_id is None when list_categories() is absent/empty
		resolve(item) -> item, possibly with a different "path" - a
			no-op passthrough for local files; a real hook for a future
			non-local add-on to do actual work in (fetch/auth/etc.)

	Photos add-on modules provide:
		list_albums() -> [{"id", "name"}, ...] (optional, same as above)
		list_photos(album_id) -> [{"id", "title", "path"}, ...]

	Raises FileNotFoundError/ImportError on a genuinely missing or
	broken add-on - callers are expected to already know the id is real
	(from list_addons()/the built-in ids), so silently returning None
	here would just move a real error somewhere harder to see."""
	addon_path = ADDONS_ROOT / media_type / addon_id / "addon.py"
	if not addon_path.is_file():
		raise FileNotFoundError(f"no addon.py at {addon_path}")
	spec = importlib.util.spec_from_file_location(f"cosmos_addon_{media_type}_{addon_id}", addon_path)
	module = importlib.util.module_from_spec(spec)
	spec.loader.exec_module(module)
	return module


def list_categories(media_type, addon_id):
	module = load_addon(media_type, addon_id)
	fn = getattr(module, "list_categories", None) or getattr(module, "list_albums", None)
	return fn() if fn else []


def list_items(media_type, addon_id, category_id=None):
	module = load_addon(media_type, addon_id)
	fn = getattr(module, "list_items", None) or getattr(module, "list_photos", None)
	return fn(category_id) if fn else []


def resolve(media_type, addon_id, item):
	"""Passthrough (returns item unchanged) if the add-on doesn't define
	resolve() at all - the common case for Local Files and any other
	purely-local add-on, which already has a real, playable path."""
	module = load_addon(media_type, addon_id)
	fn = getattr(module, "resolve", None)
	return fn(item) if fn else item
