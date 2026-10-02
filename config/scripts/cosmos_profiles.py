"""CosmOS profiles - who is using the machine, without a Unix login.

There is one Unix account (`user`, blank password, logged in automatically). A profile
is not an account: it is a name, a picture, an optional PIN and a folder of that
person's own settings - launcher favorites, wallpaper, and whatever GameBox keeps -
under ~/.local/share/cosmos/profiles/<slug>/:

    profile.json     {"name", "avatar", "pin": {"salt", "hash", "iterations"} | null,
                      "start_in": "desktop" | "gamebox", "created"}
    avatar.png       the picture (also copied to ~/.face for the bar)
    favorites.txt    launcher/bar favorites
    wallpaper        path of the wallpaper chosen in cosmos-wallpaper
    ...              anything else a program wants to keep per profile

The profile picker (cosmos-profiles) chooses one at the start of every session and
records it as the active profile; everything that keeps per-person state asks
profile_path("favorites.txt") etc. and gets the active profile's file (or the old
shared location when no profile is active, so nothing breaks before the first one is made).
The PIN only gates the picker and the lock screen; it is a courtesy between people
sharing a machine, not protection from someone with the disk.
"""
import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import time

DATA_DIR = os.path.expanduser("~/.local/share/cosmos/profiles")
CONFIG_DIR = os.path.expanduser("~/.config/cosmos")
ACTIVE_FILE = os.path.join(CONFIG_DIR, "active-profile")
FACE_PATH = os.path.expanduser("~/.face")
PIN_ITERATIONS = 200_000
PIN_MIN, PIN_MAX = 4, 12
MODES = ("desktop", "gamebox")


class ProfileError(Exception):
    pass


def _slugify(name):
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "profile"


def profile_dir(slug):
    return os.path.join(DATA_DIR, slug)


def _json_path(slug):
    return os.path.join(profile_dir(slug), "profile.json")


def load(slug):
    try:
        with open(_json_path(slug), encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    data["slug"] = slug
    return data


def save(profile):
    slug = profile["slug"]
    os.makedirs(profile_dir(slug), exist_ok=True)
    data = {k: v for k, v in profile.items() if k != "slug"}
    with open(_json_path(slug), "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)


def list_profiles():
    """All profiles, oldest first."""
    profiles = []
    try:
        names = os.listdir(DATA_DIR)
    except OSError:
        return profiles
    for slug in names:
        profile = load(slug)
        if profile:
            profiles.append(profile)
    return sorted(profiles, key=lambda p: (p.get("created", 0), p["slug"]))


def create_profile(name, avatar=None, pin=None, start_in="desktop"):
    """Makes a profile; returns it. `avatar` is a picture file to copy in."""
    name = (name or "").strip()
    if not name:
        raise ProfileError("A profile needs a name.")
    if start_in not in MODES:
        raise ProfileError(f"start_in must be one of {MODES}")
    base = _slugify(name)
    slug, n = base, 2
    while os.path.exists(profile_dir(slug)):
        slug, n = f"{base}-{n}", n + 1
    profile = {"slug": slug, "name": name, "avatar": None, "pin": None, "start_in": start_in,
               "created": time.time()}
    os.makedirs(profile_dir(slug))
    if avatar and os.path.isfile(avatar):
        target = os.path.join(profile_dir(slug), "avatar.png")
        shutil.copyfile(avatar, target)
        profile["avatar"] = target
    if pin:
        profile["pin"] = _hash_pin(pin)
    save(profile)
    return profile


def set_avatar_file(slug, source):
    """Makes the picture file `source` the profile's picture (copied in as avatar.png). When the profile is the active one it also becomes ~/.face at once, which is
    what the bar and lock screen show. This is how GameBox hands over the portrait of an Astronar the moment one is made."""
    profile = load(slug)
    if profile is None:
        raise ProfileError("No such profile.")
    target = os.path.join(profile_dir(slug), "avatar.png")
    if os.path.abspath(source) != os.path.abspath(target):
        shutil.copyfile(source, target)
    profile["avatar"] = target
    save(profile)
    if active_slug() == slug:
        try:
            shutil.copyfile(target, FACE_PATH)
        except OSError:
            pass
    return profile


def delete_profile(slug):
    if load(slug) is None:
        return
    shutil.rmtree(profile_dir(slug), ignore_errors=True)
    if active_slug() == slug:
        try:
            os.remove(ACTIVE_FILE)
        except OSError:
            pass


# ---- PIN ------------------------------------------------------------------
def _hash_pin(pin, salt=None, iterations=PIN_ITERATIONS):
    if not (isinstance(pin, str) and pin.isdigit() and PIN_MIN <= len(pin) <= PIN_MAX):
        raise ProfileError(f"A PIN is {PIN_MIN}-{PIN_MAX} digits.")
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", pin.encode(), bytes.fromhex(salt), iterations)
    return {"salt": salt, "hash": digest.hex(), "iterations": iterations}


def has_pin(profile):
    return bool(profile and profile.get("pin"))


def verify_pin(profile, pin):
    """True if the profile has no PIN (nothing to check) or `pin` matches it."""
    stored = profile.get("pin") if profile else None
    if not stored:
        return True
    try:
        candidate = hashlib.pbkdf2_hmac("sha256", str(pin).encode(), bytes.fromhex(stored["salt"]),
                                        int(stored["iterations"]))
    except (ValueError, KeyError, TypeError):
        return False
    return hmac.compare_digest(candidate.hex(), stored["hash"])


def set_pin(slug, pin):
    """Sets (or, with pin=None/"", clears) a profile's PIN."""
    profile = load(slug)
    if profile is None:
        raise ProfileError("No such profile.")
    profile["pin"] = _hash_pin(pin) if pin else None
    save(profile)


# ---- the active profile -----------------------------------------------------
def active_slug():
    try:
        with open(ACTIVE_FILE, encoding="utf-8") as fh:
            slug = fh.read().strip()
    except OSError:
        return None
    return slug if slug and load(slug) else None


def active_profile():
    slug = active_slug()
    return load(slug) if slug else None


def set_active(slug):
    """Makes `slug` the active profile: recorded, and its picture copied to ~/.face."""
    profile = load(slug)
    if profile is None:
        raise ProfileError("No such profile.")
    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(ACTIVE_FILE, "w", encoding="utf-8") as fh:
        fh.write(slug + "\n")
    if profile.get("avatar") and os.path.isfile(profile["avatar"]):
        try:
            shutil.copyfile(profile["avatar"], FACE_PATH)
        except OSError:
            pass
    return profile


def profile_path(filename):
    """Where the active profile keeps `filename` - or the pre-profiles shared
    location (~/.config/cosmos/<filename>) when no profile is active."""
    slug = active_slug()
    if slug:
        return os.path.join(profile_dir(slug), filename)
    return os.path.join(CONFIG_DIR, filename)


def session_environment():
    """Variables handed to anything started for the active profile (GameBox uses them to
    keep its own per-profile data)."""
    slug = active_slug()
    if not slug:
        return {}
    return {"COSMOS_PROFILE": slug, "COSMOS_PROFILE_DIR": profile_dir(slug)}


# ---- carrying the pre-profiles state over -------------------------------------------
def migrate_legacy(display_name):
    """First run on an install that predates profiles: if there is already a shared
    favorites list / wallpaper / picture, make a profile from them so nothing is lost.
    Returns the new profile, or None if there was nothing to carry over (or profiles exist)."""
    if list_profiles():
        return None
    legacy = {name: os.path.join(CONFIG_DIR, name) for name in ("favorites.txt", "wallpaper")}
    has_state = any(os.path.isfile(p) for p in legacy.values()) or os.path.isfile(FACE_PATH)
    if not has_state:
        return None
    profile = create_profile(display_name or "Default", avatar=FACE_PATH if os.path.isfile(FACE_PATH) else None)
    for name, path in legacy.items():
        if os.path.isfile(path):
            shutil.copyfile(path, os.path.join(profile_dir(profile["slug"]), name))
    return profile
