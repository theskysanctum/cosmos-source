#!/usr/bin/env python3
"""CosmOS's avatar generator plugin format - two real, separate roots,
scanned together (see list_installed()):

- BUILTIN_ROOT (avatar_plugins_builtin/) - CosmOS's own first-party
  items/motions, one feature per file, each following the exact same
  manifest+module format as an installed plugin. Version-controlled,
  shipped as part of the OS, never touched by uninstall() - splitting
  the avatar generator's own built-in content into one file per
  feature (a direct request: "separate all the different items and
  animations... using the extension system we made") without making
  any of it accidentally removable the way it would be if it just
  lived in PLUGINS_ROOT alongside real installed plugins.
- PLUGINS_ROOT (avatar_plugins/) - real third-party plugins installed
  from github.com/theskysanctum/CosmOS-avatar-plugins (see
  avatar_plugin_repo.py for the GitHub client) - this is what
  install()/uninstall() actually operate on.

Two plugin types (a third, "showcase" - a standalone
ctx["run(ctx)"]-driven video-export format - existed earlier but was
removed outright as unnecessary; nothing in this module depends on it,
and none of the plugin content this project actually ships used it for
anything real):

- "motion" features: <root>/motion/<id>/motion.json + motion.py, the
  latter providing exactly one entry point, `pose(rig, t) -> None` -
  the exact same interface every avatar3d_anim.py motion function
  already has (rig is a live avatar3d_rig.Rig, t a normalized loop
  phase in [0, 1)). See installed_motions() for how these get merged in
  alongside avatar3d_anim.MOTIONS, without that module itself needing
  to know any of this exists at all.

- "bodypart" features: <root>/bodypart/<id>/bodypart.json +
  bodypart.py - the real extension point for adding whole new meshes/
  body parts/accessories to the Astronar generator itself. Deliberately
  built ahead of any real content ("not necessarily make anything for
  it but able to expand it" - a direct request, ahead of an eventual
  Astronar Store section shipping actual third-party bodypart plugins
  to install) - there is no real plugin of this type in the repo yet;
  this is the seam itself, not a reflection of content that already
  exists.

  bodypart.json can declare a "fields" list - real customizer sliders/
  choices (the exact same dict shape avatar3d_viewer.CUSTOM_FIELDS
  entries already use: category/key/label/kind/range-or-options, plus
  a "default" this project's own built-in fields don't need since
  CUSTOM_DEFAULTS lists those separately) - so a plugin can expose real
  UI controls without shipping code just to describe them. See
  installed_bodypart_fields() for how these get namespaced and merged
  into avatar3d_viewer's own CUSTOM_FIELDS/CUSTOM_DEFAULTS/CATEGORIES.

  bodypart.py provides up to two entry points, both optional (a plugin
  can implement either or both, depending on whether its own look makes
  sense on the static portrait, the live/posable rig, or both):

	build_portrait(vertex, normal, color, tris, idx, box, outlined_box,
	                traits, metrics) -> idx
		Called from avatar3d.build_character() for every static
		portrait render, writing directly into the SAME shared low-
		level mesh buffer every built-in _build_*() function in
		avatar3d.py already writes through - full access, not a
		restricted API, matching the trust level "motion" plugins
		already get (see avatar3d._apply_bodypart_plugins_static()
		for exactly what `traits`/`metrics` contain).

	build_animated(rig, traits, metrics) -> None
		Called from avatar3d_rig.build_rig() for the live/posable rig -
		`rig` is the real, already-built avatar3d_rig.Rig, so a plugin
		attaches its own new geometry to whichever existing joint
		NodePath makes sense (rig.root/rig.waist/rig.neck/rig.shoulder_l/...
		- see avatar3d_rig.Rig's own field list for every real joint)
		the same way this project's own internal rig-attachment code
		(e.g. the mouth_open overlay) already does: build a small
		buffer, _finish() it, reparentTo() a joint.

Both plugin types are loaded via importlib.util's spec_from_file_location/
module_from_spec/exec_module, never cached - same hot-reload standard
media/addon_backend.py's own load_addon() already follows, and the same
reason: an installed plugin edited on disk (or freshly re-installed)
takes effect on its very next use, not just after a restart."""
import importlib.util
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import avatar3d_anim  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import debuglog  # noqa: E402

log = debuglog.setup("avatar_plugin_backend")

# Two separate roots, same manifest+module format, scanned together by
# list_installed() below - the real reason CosmOS's own built-in avatar
# items/motions are being split into one file per feature at all (a
# direct request: "separate all the different items and animations,
# one per py file... using the extension system we made"), rather than
# just moving them into PLUGINS_ROOT itself, is that PLUGINS_ROOT is
# exactly what a future Astronar Store's own install/uninstall flow
# operates on - a built-in feature (the "walk" motion, a backpack)
# living there would be genuinely uninstallable the same as anything a
# user installs later. BUILTIN_ROOT is version-controlled, shipped as
# part of the OS, and uninstall() below refuses to touch anything in
# it - the split buys real per-feature file separation using the exact
# same loader/contract, without making any of it removable.
BUILTIN_ROOT = Path(__file__).resolve().parent / "avatar_plugins_builtin"
PLUGINS_ROOT = Path(__file__).resolve().parent / "avatar_plugins"

MANIFEST_FILENAMES = {"motion": "motion.json", "bodypart": "bodypart.json"}
MODULE_FILENAMES = {"motion": "motion.py", "bodypart": "bodypart.py"}


def _root_for_source(source):
	return BUILTIN_ROOT if source == "builtin" else PLUGINS_ROOT


def list_installed(plugin_type=None):
	"""[{"id", "type", "name", "description", "source"}, ...] for every
	plugin under BOTH BUILTIN_ROOT and PLUGINS_ROOT's own <type>/
	subdirectories, sorted by name. "source" is "builtin" or "installed"
	- every other function in this module that iterates this list
	(installed_motions(), installed_bodypart_fields(), the builder
	functions below) picks up built-in features for free just by
	calling this, with no separate merging logic of their own needed.
	Skips anything whose own manifest is missing or unparsable rather
	than raising - one broken plugin/feature shouldn't take the whole
	list down."""
	types = [plugin_type] if plugin_type else list(MANIFEST_FILENAMES)
	plugins = []
	for source, root in (("builtin", BUILTIN_ROOT), ("installed", PLUGINS_ROOT)):
		for t in types:
			type_dir = root / t
			if not type_dir.is_dir():
				continue
			for plugin_dir in sorted(type_dir.iterdir()):
				if not plugin_dir.is_dir():
					continue
				manifest_path = plugin_dir / MANIFEST_FILENAMES[t]
				if not manifest_path.is_file():
					continue
				try:
					manifest = json.loads(manifest_path.read_text())
					plugins.append({
						"id": plugin_dir.name, "type": t, "source": source,
						"name": manifest.get("name", plugin_dir.name),
						"description": manifest.get("description", ""),
					})
				except (OSError, ValueError):
					log.exception(f"failed to read {manifest_path}")
	plugins.sort(key=lambda p: p["name"])
	log.debug(f"list_installed({plugin_type}): {[(p['id'], p['source']) for p in plugins]}")
	return plugins


def uninstall(plugin_type, plugin_id):
	"""Only ever removes from PLUGINS_ROOT (the real, user-installed
	set) - a built-in feature under BUILTIN_ROOT is never touched here,
	even if a caller passes its id, since it isn't a real installed
	plugin at all (see this module's own docstring on why the two roots
	are kept separate)."""
	if (BUILTIN_ROOT / plugin_type / plugin_id).is_dir() and not (PLUGINS_ROOT / plugin_type / plugin_id).is_dir():
		log.warning(f"refusing to uninstall {plugin_id!r} ({plugin_type}) - it's a built-in feature, not an installed plugin")
		return
	plugin_dir = PLUGINS_ROOT / plugin_type / plugin_id
	if plugin_dir.is_dir():
		shutil.rmtree(plugin_dir)
	log.info(f"uninstalled avatar plugin {plugin_id!r} ({plugin_type})")


def _load_module(plugin_type, plugin_id, source="installed"):
	module_path = _root_for_source(source) / plugin_type / plugin_id / MODULE_FILENAMES[plugin_type]
	if not module_path.is_file():
		raise FileNotFoundError(f"no {MODULE_FILENAMES[plugin_type]} at {module_path}")
	spec = importlib.util.spec_from_file_location(f"cosmos_avatar_plugin_{plugin_type}_{plugin_id}", module_path)
	module = importlib.util.module_from_spec(spec)
	spec.loader.exec_module(module)
	return module


def installed_motions():
	"""avatar3d_anim.MOTIONS, plus every built-in AND installed "motion"
	plugin's own pose(rig, t) merged in under its own plugin id (a
	plugin can only ever ADD a name, never shadow one that's already
	claimed - list_installed()'s own builtin-before-installed root order
	means a built-in motion, e.g. "walk" under avatar_plugins_builtin/
	motion/walk/, always gets merged in before any third-party installed
	plugin could try to use that same name, so a driver calling
	installed_motions()["walk"] always gets the real built-in one)."""
	motions = dict(avatar3d_anim.MOTIONS)
	for entry in list_installed("motion"):
		try:
			module = _load_module("motion", entry["id"], entry["source"])
		except Exception:
			log.exception(f"failed to load motion {entry['id']!r} ({entry['source']})")
			continue
		pose_fn = getattr(module, "pose", None)
		if pose_fn is None:
			log.warning(f"motion {entry['id']!r} ({entry['source']}) has no pose(rig, t) entry point")
			continue
		if entry["id"] not in motions:
			motions[entry["id"]] = pose_fn
	return motions


def _bodypart_field_key(plugin_id, raw_key):
	return f"plugin_{plugin_id}_{raw_key}"


def installed_bodypart_fields():
	"""Every built-in AND installed "bodypart" feature's own customizer
	field declarations, read straight from each one's own bodypart.json
	manifest ("fields": [...], the same dict shape avatar3d_viewer.
	CUSTOM_FIELDS entries already use, plus a "default").

	An INSTALLED (third-party) entry's own `key` is rewritten to
	`plugin_<id>_<raw key>` (see _bodypart_field_key()) - guarantees no
	collision with a core trait key or another plugin's own, without
	requiring third-party authors to coordinate key names with each
	other or with this project. A BUILT-IN entry's key is left exactly
	as declared - built-in features are first-party, coordinated in
	this same repo, and some genuinely need to share a real core trait
	key (e.g. every accessory-type built-in toggles membership in the
	one shared "accessories" list the same hand-written CUSTOM_FIELDS
	rows always have - see avatar_plugins_builtin/bodypart/backpack/
	bodypart.json for a real example), which namespacing would break.

	Manifest-declared, not Python code - a feature can add real
	customizer sliders/choices without shipping executable code just to
	describe its own UI, and this can run anywhere (including a context
	that never touches Panda3D at all) without importing avatar3d/
	avatar3d_rig. Skips anything whose manifest is missing/unparsable or
	whose own field entries are malformed, same "one broken plugin
	shouldn't take the whole list down" resilience as list_installed()."""
	fields = []
	for entry in list_installed("bodypart"):
		manifest_path = _root_for_source(entry["source"]) / "bodypart" / entry["id"] / MANIFEST_FILENAMES["bodypart"]
		try:
			manifest = json.loads(manifest_path.read_text())
		except (OSError, ValueError):
			log.exception(f"failed to read {manifest_path}")
			continue
		for raw_field in manifest.get("fields", []):
			try:
				field = dict(raw_field)
				if entry["source"] == "installed":
					field["key"] = _bodypart_field_key(entry["id"], raw_field["key"])
				fields.append(field)
			except (KeyError, TypeError):
				log.warning(f"bodypart feature {entry['id']!r} ({entry['source']}) has a malformed field entry: {raw_field!r}")
	return fields


def installed_bodypart_defaults():
	"""{field key: default value} for every installed_bodypart_fields()
	entry that actually declares its own "default" - avatar3d_viewer.
	refresh_custom_fields() merges this straight into CUSTOM_DEFAULTS,
	the same source every "no traits saved yet" call site already falls
	back to.

	A field with NO "default" key is skipped entirely here, not treated
	as "defaults to None" - a real, confirmed bug in the first version:
	several built-in accessory-type fields (see avatar_plugins_builtin/
	bodypart/backpack/bodypart.json) deliberately point at the shared
	"accessories" key WITHOUT their own default (that key's one real
	default, an empty list, is already owned by avatar3d_viewer's own
	_BASE_CUSTOM_DEFAULTS) - defaulting a missing "default" to None
	would silently clobber CUSTOM_DEFAULTS["accessories"] from [] to
	None the moment any such field existed at all."""
	return {f["key"]: f["default"] for f in installed_bodypart_fields() if "default" in f}


def installed_bodypart_builders():
	"""[(plugin_id, build_portrait_fn), ...] for every built-in AND
	installed "bodypart" feature that actually provides a
	build_portrait() entry point (some may implement only
	build_animated(), or vice versa - see this module's own docstring)
	- used by avatar3d._apply_bodypart_plugins_static()."""
	builders = []
	for entry in list_installed("bodypart"):
		try:
			module = _load_module("bodypart", entry["id"], entry["source"])
		except Exception:
			log.exception(f"failed to load bodypart feature {entry['id']!r} ({entry['source']})")
			continue
		build_fn = getattr(module, "build_portrait", None)
		if build_fn is not None:
			builders.append((entry["id"], build_fn))
	return builders


def installed_bodypart_rig_builders():
	"""[(plugin_id, build_animated_fn), ...] for every built-in AND
	installed "bodypart" feature that provides a build_animated() entry
	point - the rig-side counterpart to installed_bodypart_builders(),
	used by avatar3d_rig._apply_bodypart_plugins_rig()."""
	builders = []
	for entry in list_installed("bodypart"):
		try:
			module = _load_module("bodypart", entry["id"], entry["source"])
		except Exception:
			log.exception(f"failed to load bodypart feature {entry['id']!r} ({entry['source']})")
			continue
		build_fn = getattr(module, "build_animated", None)
		if build_fn is not None:
			builders.append((entry["id"], build_fn))
	return builders


