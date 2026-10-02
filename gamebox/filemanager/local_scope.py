#!/usr/bin/env python3
"""Curated content categories for File Manager's own "Local" source -
per direct instruction ("it should only show media_files... roms, etc.
nothing system facing should ever be visible"), replacing the old
unrestricted Path.home()-based browsing (see paths.py's own docstring,
which used to document that as deliberate - reversed here per direct
instruction) with a fixed, synthetic top-level category list that never
exposes a real filesystem path (dotfiles, /home, system config dirs)
outside of CosmOS's own real content.

Two categories today: "Media" (the same real movies/tv/music/photos
folders media/media_backend.py already owns - not new literals) and
"ROMs" (/cosmos/roms, library/systems.json's own real shared roms_dir
parent). Nothing else - no screenshots directory exists anywhere in this
project yet, save files live deep inside per-emulator config trees too
messy to expose as a clean category, and everything else under
paths.DATA_ROOT (profiles, credentials, caches) is account/app internals
that were never meant to be user-browsable.

Real containment checks, not a naive string-prefix comparison - a
symlink planted inside a category, or a crafted ".." sequence, can never
resolve outside that category's own real root. filemanager/files.py
itself is untouched (library/disc_content.py's and usb/usb_content.py's
own real callers pass already-known-good destination paths, never a
user-browsed one, and must keep working unchanged) - this module is
the one and only gate the File Manager UI itself goes through before
ever calling into files.py with a path a user actually navigated to."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import debuglog  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "media"))
import media_backend  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
import files  # noqa: E402

log = debuglog.setup("filemanager.local_scope")

ROMS_ROOT = Path("/cosmos/roms")

# name -> real root dir(s) this category's own top-level listing merges
# together (Media has four real sibling folders; ROMs has just the one).
CATEGORIES = {
	"Media": [media_backend.MOVIES_DIR, media_backend.TV_DIR, media_backend.MUSIC_DIR, media_backend.PHOTOS_DIR],
	"ROMs": [ROMS_ROOT],
}


def list_root():
	"""The synthetic top-level listing - one pseudo-directory per real
	category, in CATEGORIES' own order. Shaped like files.list_dir()'s
	own (dirs, files) return so callers don't need a separate code path
	for "at the category picker" vs "inside a real directory"."""
	dirs = [{"name": name, "path": f"local:{name}"} for name in CATEGORIES]
	return dirs, []


def _matching_category(path):
	"""The real category root `path` actually resolves inside, or None
	if it's outside every category entirely (should never happen for a
	path this module itself produced, but never trust a caller-supplied
	string on its own - see resolve_safe())."""
	try:
		resolved = path.resolve()
	except OSError:
		return None
	for roots in CATEGORIES.values():
		for root in roots:
			try:
				if resolved.is_relative_to(root.resolve()):
					return root
			except OSError:
				continue
	return None


def is_within_scope(path):
	return _matching_category(Path(path)) is not None


def resolve_safe(scoped_path):
	"""Real, containment-checked resolution of whatever the UI is about
	to list/act on - `scoped_path` is either the root sentinel ("local:"
	with no category yet - see list_root()), "local:<Category>" (a real
	category's own root, the "up" floor within it), or a real absolute
	path already confirmed to live inside one of CATEGORIES. Returns
	None for the bare root sentinel (meaning "show list_root() instead
	of a real directory"), or the real, resolved Path otherwise - never
	a path outside scope, even if the caller's own string tried to walk
	there with a symlink or a crafted "..". Never raises."""
	if scoped_path is None or scoped_path == "local:":
		return None
	if isinstance(scoped_path, str) and scoped_path.startswith("local:"):
		name = scoped_path[len("local:") :]
		roots = CATEGORIES.get(name)
		if not roots:
			log.warning(f"unknown Local category {name!r}, falling back to the category list")
			return None
		return roots[0]  # the category's own real root - Media's own "up" floor, not a merged synthetic dir
	real_path = Path(scoped_path)
	if _matching_category(real_path) is None:
		log.warning(f"refusing to browse {scoped_path!r} - outside every Local category")
		return None
	return real_path


def list_dir(scoped_path):
	"""(dirs, files) for whatever `scoped_path` actually refers to - the
	synthetic category list, a category's own merged top-level dirs (for
	Media, all four real folders' contents interleaved as if they were
	one directory), or a real subdirectory's own contents once already
	inside one category. Delegates to files.list_dir() only once
	resolve_safe() has confirmed the real path is genuinely in scope."""
	resolved = resolve_safe(scoped_path)
	if resolved is None and (scoped_path is None or scoped_path == "local:"):
		return list_root()
	if resolved is None:
		return [], []
	# A category's own root IS the real content dir (ROMs) or one of
	# several real content dirs that all merge into one listing at this
	# level (Media) - detect "we're sitting exactly on a category root"
	# by identity, not by string comparison, since resolve_safe() above
	# already did the real resolution.
	for name, roots in CATEGORIES.items():
		if any(resolved == root for root in roots) and len(roots) > 1:
			dirs, files_out = [], []
			for root in roots:
				if not root.is_dir():
					continue
				d, f = files.list_dir(root)
				dirs.extend(d)
				files_out.extend(f)
			dirs.sort(key=lambda d: d["name"].lower())
			files_out.sort(key=lambda f: f["name"].lower())
			return dirs, files_out
	if not resolved.is_dir():
		return [], []
	return files.list_dir(resolved)


def is_merge_sentinel(scoped_path):
	"""True only for a category sentinel ("local:Media") whose own
	listing merges more than one real root together - there's no single
	real directory such a view corresponds to, so write operations
	(New Folder) that need one real destination directory must refuse
	it, distinct from a single-root category ("local:ROMs") where the
	sentinel and its one real root are interchangeable."""
	if not (isinstance(scoped_path, str) and scoped_path.startswith("local:")):
		return False
	roots = CATEGORIES.get(scoped_path[len("local:") :])
	return bool(roots) and len(roots) > 1


def can_write(scoped_path):
	"""Whether scoped_path names exactly one real, in-scope directory a
	new folder can be created in - false at the synthetic root category
	list and false at an ambiguous multi-root merge view (see
	is_merge_sentinel())."""
	return scoped_path is not None and not is_merge_sentinel(scoped_path) and resolve_safe(scoped_path) is not None


def up(scoped_path):
	"""The scoped_path one level up from scoped_path, or None if
	scoped_path is already at the top of Local (the synthetic root
	category list) - the one and only floor Local's own "up" navigation
	ever stops at, per direct instruction ("nothing system facing
	should ever be visible"). A category sentinel always goes straight
	to the root list; a real path either lands on its own merge-view
	sentinel (if its parent is exactly one of a multi-root category's
	real roots - e.g. stepping up out of a movie's own folder lands back
	on the merged Media view, not on /userdata/movies as its own bare
	directory) or its own real parent, or the root list if that parent
	falls outside every category entirely."""
	if scoped_path is None:
		return None
	if isinstance(scoped_path, str) and scoped_path.startswith("local:"):
		return None
	parent = Path(scoped_path).parent
	try:
		resolved_parent = parent.resolve()
	except OSError:
		return None
	for name, roots in CATEGORIES.items():
		if len(roots) > 1 and any(resolved_parent == root.resolve() for root in roots if root.exists()):
			return f"local:{name}"
	if is_within_scope(parent):
		return str(parent)
	return None
