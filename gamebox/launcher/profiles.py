#!/usr/bin/env python3
"""Multi-user profiles - each household member gets their own
/cosmos/profiles/<name>/ (see paths.py's own docstring for why
/cosmos, not something derived from the real user's home) - never part
of the git tree and never needs a .gitignore entry of its own.

Deliberately scoped to *personal state*, not content: save data,
quick-launch bookmarks, dashboard widgets, and RetroAchievements login are
each individual's own, but the game and media libraries themselves
(roms/, movies/, tv/, music/, photos/ - see media_backend.py) are shared
across every profile the same way one physical console's ROM/disc
collection would be, not duplicated or split per person. Video/Music/
Photos (CosmOS's own native apps - see media/addons/) likewise stay
one shared library, not one per profile. Brave used to be the one
exception (its own per-profile browser_profile_dir()) but that turned
out to actively break Widevine: each profile got a totally empty
Chromium user-data-dir, so every one had to independently win the race
against this project's process-tree-kill exit (see brave_launcher.py)
to finish downloading the CDM before getting killed. Brave is a single
shared profile now too (brave_launcher.py's own BRAVE_HOME), for the
same reason.

Every subpath helper below (quick_launch_ini_path(), etc.) resolves the
*active* profile fresh on every call rather than caching it once - so
nothing here can go stale across a profile switch without this module
needing to know anything about when a switch happened. See app.py's
activate_profile()/run.py's restart-on-switch plumbing for how the rest of
the app picks that change up."""
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import debuglog  # noqa: E402
import paths  # noqa: E402

log = debuglog.setup("profiles")


def _load_cosmos_profiles():
	"""CosmOS's own profile module (cosmos_profiles.py: the profiles the profile picker, lock screen and gridscreen use), or None when this GameBox is not
	running on CosmOS. There is one Unix account and one list of people, so on CosmOS GameBox does not keep a list of its own: see the shared mode below."""
	here = Path(__file__).resolve()
	for folder in ("/usr/lib/cosmos", str(here.parents[2] / "config" / "scripts")):
		if folder not in sys.path and Path(folder, "cosmos_profiles.py").is_file():
			sys.path.append(folder)
	try:
		import cosmos_profiles
		return cosmos_profiles
	except ImportError:
		return None


_cp = _load_cosmos_profiles()


def shared():
	"""True when the profiles are CosmOS's (the normal case on CosmOS)."""
	return _cp is not None

PROFILES_ROOT = paths.DATA_ROOT / "profiles"
ACTIVE_PROFILE_PATH = Path(__file__).parent / "active_profile.txt"

PROFILE_SUBDIRS = ("saves",)

_migrated = False


_cache = {"at": 0.0, "profiles": []}


def _profiles():
	"""CosmOS's profile list, re-read from disk at most every second and a half (every path helper below asks for it)."""
	import time
	if time.monotonic() - _cache["at"] > 1.5:
		_cache["profiles"] = _cp.list_profiles()
		_cache["at"] = time.monotonic()
	return _cache["profiles"]


def _forget():
	_cache["at"] = 0.0


def _find(name):
	"""The CosmOS profile called `name` (its display name, or failing that its folder name), or None."""
	if _cp is None or not name:
		return None
	profiles = _profiles()
	for profile in profiles:
		if profile.get("name") == name:
			return profile
	for profile in profiles:
		if profile.get("slug") == name:
			return profile
	return None


def _migrate_legacy_once():
	"""GameBox's own profiles from before they were shared (folders under /userdata/profiles/) become CosmOS profiles, keeping everything in them; done once
	(a marker is left), and never over an existing CosmOS profile of the same name."""
	global _migrated
	if _migrated:
		return
	_migrated = True
	marker = PROFILES_ROOT / ".migrated-to-cosmos"
	if not PROFILES_ROOT.is_dir() or marker.exists():
		return
	for legacy in sorted(p for p in PROFILES_ROOT.iterdir() if p.is_dir()):
		if _find(legacy.name) is not None:
			continue
		try:
			created = _cp.create_profile(legacy.name)
			shutil.copytree(legacy, _cp.profile_dir(created["slug"]), dirs_exist_ok=True)
			log.info(f"moved GameBox profile {legacy.name!r} into the CosmOS profiles")
		except (OSError, _cp.ProfileError) as exc:
			log.warning(f"could not carry GameBox profile {legacy.name!r} over: {exc}")
	_forget()
	try:
		marker.touch()
	except OSError:
		pass


def has_pin(name):
	"""True when the CosmOS profile has a PIN (GameBox asks for it before switching to that profile)."""
	found = _find(name)
	return bool(found and _cp.has_pin(found))


def verify_pin(name, pin):
	found = _find(name)
	return bool(found and _cp.verify_pin(found, pin))


def list_profiles():
	if _cp is not None:
		_migrate_legacy_once()
		return sorted(p["name"] for p in _profiles())
	if not PROFILES_ROOT.exists():
		return []
	names = sorted(p.name for p in PROFILES_ROOT.iterdir() if p.is_dir())
	log.debug(f"list_profiles: {names}")
	return names


def get_active_profile():
	"""The name of the active profile, or None if none has ever been
	created (a fresh install) - callers (see app.py's App.__init__) treat
	that as "force the create-a-profile screen first, nothing else has
	anywhere real to read from yet".

	Falls back to the first profile (alphabetically) if the stored name
	is missing or no longer exists (e.g. that profile was deleted) rather
	than raising - same defensive treatment config.ini entries already get
	elsewhere in this app."""
	if _cp is not None:
		active = _cp.active_profile()
		return active["name"] if active else None
	profiles = list_profiles()
	if not profiles:
		return None
	stored = None
	if ACTIVE_PROFILE_PATH.exists():
		stored = ACTIVE_PROFILE_PATH.read_text().strip()
	if stored and stored in profiles:
		return stored
	log.debug(f"stored active profile {stored!r} not found, falling back to {profiles[0]!r}")
	return profiles[0]


def set_active_profile(name):
	if _cp is not None:
		found = _find(name)
		if found is None:
			raise ValueError(f"no such profile: {name!r}")
		_cp.set_active(found["slug"])
		_forget()
		log.info(f"active profile set to {name!r} (CosmOS profile {found['slug']})")
		return
	ACTIVE_PROFILE_PATH.write_text(name)
	log.info(f"active profile set to {name!r}")


def create_profile(name):
	if _cp is not None:
		found = _find(name) or _cp.create_profile(name)
		_forget()
		for subdir in PROFILE_SUBDIRS:
			(Path(_cp.profile_dir(found["slug"])) / subdir).mkdir(parents=True, exist_ok=True)
		log.info(f"created profile {name!r} as CosmOS profile {found['slug']}")
		return
	profile_dir(name).mkdir(parents=True, exist_ok=True)
	for subdir in PROFILE_SUBDIRS:
		(profile_dir(name) / subdir).mkdir(parents=True, exist_ok=True)
	log.info(f"created profile {name!r} at {profile_dir(name)}")


def delete_profile(name):
	if _cp is not None:
		found = _find(name)
		if found is not None:
			_cp.delete_profile(found["slug"])
			_forget()
		log.info(f"deleted profile {name!r}")
		return
	shutil.rmtree(profile_dir(name), ignore_errors=True)
	log.info(f"deleted profile {name!r}")


def profile_dir(name=None):
	"""None if name isn't given and there's no active profile either (a
	fresh install, before the very first profile is created) - every
	subpath helper below, and every module that calls one, has to treat
	that as "nothing to read/write yet" rather than crash: App.__init__()
	unconditionally builds every page (including ones that read
	quick-launch/widget/RA data) before it's had a chance to act on
	get_active_profile() being None and force the create-a-profile screen,
	so this has to stay safe to call that early."""
	active = name or get_active_profile()
	if active is None:
		return None
	if _cp is not None:
		found = _find(active)
		if found is None:
			return None
		folder = Path(_cp.profile_dir(found["slug"]))
		for subdir in PROFILE_SUBDIRS:
			try:
				(folder / subdir).mkdir(parents=True, exist_ok=True)
			except OSError:
				pass
		return folder
	return PROFILES_ROOT / active


def saves_dir():
	pd = profile_dir()
	return pd / "saves" if pd else None


def picture_traits_path(name=None):
	"""Unlike every other helper here, this one takes an explicit name -
	the Switch Profile list (see app.py's refresh_profile_picker_list())
	needs every profile's own picture, not just the active one, at the
	same time. Omit name for the active profile's own, matching
	profile_dir()'s own default.

	A profile's "picture" is a real avatar3d_rig.build_rig()-shaped
	trait dict (skin/hair/eye colors, clothing, animal/mythical
	features, body sliders, ...), not a saved image file - the real
	picture (avatar3d.render_portrait() for a small icon,
	avatar3d_viewer's live rig for the customization screen itself) gets
	rendered fresh on demand at whatever exact size/form a caller needs,
	same "never stale, no scaling artifacts" reasoning the older
	avatar_gen.py-based picture_seed.txt this replaced already
	established - just a real explicit trait set now instead of one
	seed-random integer (see avatar3d.py's own docstring on why this
	generator isn't seed-random at all)."""
	pd = profile_dir(name)
	return pd / "picture_traits.json" if pd else None


def get_picture_traits(name=None):
	"""None if this profile has no picture yet - a fresh profile, or one
	that predates this feature (still has an old picture_seed.txt, now
	orphaned - never migrated, since a missing/None picture was already
	a real, handled state every caller treats as "show the default
	look", not an error). Callers fall back to
	avatar3d_viewer.CUSTOM_DEFAULTS the same way get_theme()'s own None
	case already falls back to a hardcoded default."""
	path = picture_traits_path(name)
	if path is None or not path.exists():
		return None
	try:
		traits = json.loads(path.read_text())
	except (ValueError, OSError):
		log.warning(f"{path} does not contain real picture traits JSON", exc_info=True)
		return None
	if "animal_ears" in traits:
		# One-time migration for traits saved before avatar3d.py's own
		# animal_ears -> ears rename (build_character() no longer accepts
		# the old key at all - confirmed live, this crashed on a real
		# saved profile rather than just looking stale) - fixed in place
		# and rewritten so this branch never has to run again for this
		# profile, not re-migrated on every single load.
		traits["ears"] = traits.pop("animal_ears")
		path.write_text(json.dumps(traits))
		log.info(f"migrated {path} from animal_ears to ears")
	removed_body_keys = [
		k for k in traits
		if k.startswith("groin_") or k in ("chest_size", "butt_size", "butt_curvature", "butt_angle")
	]
	if removed_body_keys:
		# One-time migration for traits saved while the short-lived
		# groin_*/groin_lower_*/chest_size/butt_size/butt_curvature/
		# butt_angle sliders existed (build_character()/build_rig() no
		# longer accept any of them at all - removed outright, not worth
		# keeping around) - dropped in place and rewritten so this branch
		# never has to run again for this profile.
		for key in removed_body_keys:
			del traits[key]
		path.write_text(json.dumps(traits))
		log.info(f"migrated {path}, dropping removed body-shape traits {removed_body_keys}")
	return traits


def set_picture_traits(traits, name=None):
	picture_traits_path(name).write_text(json.dumps(traits))
	log.info(f"set picture traits for {name or get_active_profile()!r}")
	if _cp is not None:
		_publish_portrait(traits, name or get_active_profile())


def _publish_portrait(traits, name):
	"""On CosmOS the Astronar is also the profile's picture everywhere else (the profile picker, the bar, the lock screen): its portrait is rendered the moment
	the Astronar is made or changed and handed to the CosmOS profile. Never raises - a failed render leaves the previous picture."""
	found = _find(name)
	if found is None:
		return
	try:
		import avatar3d                                   # imported here: avatar3d needs a GPU context, and imports this module
		image = avatar3d.render_portrait(traits, 256)
		target = Path(_cp.profile_dir(found["slug"])) / "avatar.png"
		image.save(target, "PNG")
		_cp.set_avatar_file(found["slug"], str(target))
		_forget()
		log.info(f"the portrait of {name!r}'s Astronar is now the CosmOS profile picture")
	except Exception as exc:                              # noqa: BLE001
		log.warning(f"could not turn {name!r}'s Astronar into the profile picture: {exc}")


def outfits_path(name=None):
	"""Same shape as picture_traits_path() - up to 5 saved outfits (per
	direct instruction: "multiple outfits are fine, 5 max, but not
	based on times of day or anything" - a flat list of slots, no
	category scheme). An "outfit" is only ever the Clothing/Accessories
	subset of a real trait dict (see avatar3d_viewer.OUTFIT_TRAIT_KEYS)
	- body shape, face, and creature features stay the one character
	identity, never swapped per outfit."""
	pd = profile_dir(name)
	return pd / "outfits.json" if pd else None


def get_outfits(name=None):
	"""[{"name", "traits"}, ...], up to 5 entries, in slot order - []
	if this profile has never saved one, same "missing means empty"
	contract get_picture_traits() already follows for a fresh profile."""
	path = outfits_path(name)
	if path is None or not path.exists():
		return []
	try:
		outfits = json.loads(path.read_text())
	except (ValueError, OSError):
		log.warning(f"{path} does not contain real outfits JSON", exc_info=True)
		return []
	return outfits


MAX_OUTFITS = 5


def save_outfit(slot_index, traits, name=None):
	"""Writes/overwrites slot slot_index (0-4) with the given Clothing/
	Accessories-only trait dict, extending the saved list as needed -
	never beyond MAX_OUTFITS, a real cap refused (logged, not silently
	truncated/misbehaving) rather than enforced by the caller."""
	if not (0 <= slot_index < MAX_OUTFITS):
		log.warning(f"save_outfit: slot_index {slot_index} out of range (0-{MAX_OUTFITS - 1})")
		return
	path = outfits_path(name)
	if path is None:
		return  # no active profile yet - nothing to persist to
	outfits = get_outfits(name)
	while len(outfits) <= slot_index:
		outfits.append(None)
	outfits[slot_index] = {"name": f"Outfit {slot_index + 1}", "traits": traits}
	path.write_text(json.dumps(outfits))
	log.info(f"saved outfit slot {slot_index} for {name or get_active_profile()!r}")


def delete_outfit(slot_index, name=None):
	"""Clears slot slot_index back to empty (None - the same "empty
	slot" convention get_outfits()'s own docstring already establishes
	for a slot padded-but-never-saved) rather than removing it from the
	list outright, so slot NUMBERS never shift - deleting slot 1 must
	never silently turn what was slot 3 into slot 2. A real, separate
	action from save_outfit() overwriting a slot with the current
	traits - this is the only way to actually clear one."""
	if not (0 <= slot_index < MAX_OUTFITS):
		log.warning(f"delete_outfit: slot_index {slot_index} out of range (0-{MAX_OUTFITS - 1})")
		return
	path = outfits_path(name)
	if path is None:
		return
	outfits = get_outfits(name)
	if slot_index < len(outfits):
		outfits[slot_index] = None
		path.write_text(json.dumps(outfits))
		log.info(f"deleted outfit slot {slot_index} for {name or get_active_profile()!r}")


def quick_launch_ini_path():
	pd = profile_dir()
	return pd / "quick_launch.ini" if pd else None


def dashboard_widgets_ini_path():
	pd = profile_dir()
	return pd / "dashboard_widgets.ini" if pd else None


def ra_credentials_path():
	pd = profile_dir()
	return pd / "ra_credentials.ini" if pd else None


def usb_assignment_path():
	pd = profile_dir()
	return pd / "usb_save_assignment.json" if pd else None


def dropbox_saves_path():
	"""This profile's own Dropbox save-sync config (see
	launcher/save_sync.py) - a real per-player OAuth connection (their
	own refresh token) plus which platforms they've chosen to sync and
	whether auto-sync is on, same "personal per-profile state" shape as
	ra_credentials_path()/usb_assignment_path()."""
	pd = profile_dir()
	return pd / "dropbox_saves.json" if pd else None


def cosmos_account_path():
	"""This profile's own CosmOS online-services account tokens (see
	online/cosmos_auth.py) - encrypted at rest via credentials_crypto.py,
	same "personal per-profile state" shape as ra_credentials_path()/
	dropbox_saves_path()."""
	pd = profile_dir()
	return pd / "cosmos_account.enc" if pd else None


def cosmochat_session_path():
	"""Telethon's own session file (login state - which Telegram account,
	if any, this profile is signed into) - personal per-profile state, same
	as ra_credentials_path(), not the shared Telegram API app credentials
	(see notifications/telegram_credentials.py, a global app-level
	registration, not a personal login)."""
	pd = profile_dir()
	return pd / "telegram" if pd else None


def cosmochat_messages_path():
	pd = profile_dir()
	return pd / "cosmochat_messages.json" if pd else None


def theme_path(name=None):
	"""Same shape as picture_traits_path() - takes an explicit name since a
	future theme-picker preview could in principle want to peek at another
	profile's choice, though today only the active profile's own is ever
	read (see theme.py's load_theme())."""
	pd = profile_dir(name)
	return pd / "theme.txt" if pd else None


def get_theme(name=None):
	"""The stored theme id for this profile, or None if it has never
	chosen one (a fresh profile, or one that predates this feature) -
	callers treat that the same as get_picture_traits()'s None case: fall
	back to a hardcoded default rather than treating it as an error."""
	path = theme_path(name)
	if path is None or not path.exists():
		return None
	theme_id = path.read_text().strip()
	return theme_id or None


def set_theme(theme_id, name=None):
	theme_path(name).write_text(theme_id)
	log.info(f"set theme for {name or get_active_profile()!r} to {theme_id!r}")


