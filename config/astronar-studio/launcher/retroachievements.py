#!/usr/bin/env python3
"""Fetches a RetroAchievements profile summary (points, rank, recent
achievements) for the main menu's dashboard, via RA's public Web API
(https://api-docs.retroachievements.org/). Credentials (username + API
key, from retroachievements.org/settings) live in the active CosmOS
profile's own ra_credentials.ini (see profiles.py - a different, unrelated
use of the word "profile" from RA's own account-summary sense above) -
this module only ever logs whether they're set, never the key itself.

Also handles the *other*, unrelated RA credential: the Connect API login
(login_connect_api() below) that Dolphin, PCSX2, and RetroArch - every
emulator in this project with real rcheevos achievement-unlocking built
in (see emulators/appman.py's docstring for RetroArch's own from-source
build, which has cheevos genuinely compiled in, not stripped) - actually
need to track achievements in-game. That's a genuinely different RA API
surface from the Web API key above: it needs a real username+password
login (RA's own Connect API has no key-based alternative), which this
module only ever uses once, transiently, to exchange for a persistent
login token - the password itself is never written to disk anywhere,
only the resulting token (see save_credentials()'s own connect_token
field, and emulators/retroachievements_sync.py/retroarch_config.py for
where that one token actually reaches each emulator's own config -
never this module)."""
import configparser
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import debuglog  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
import profiles  # noqa: E402

log = debuglog.setup("retroachievements")

API_BASE = "https://retroachievements.org/API"
CONNECT_API_URL = "https://retroachievements.org/dorequest.php"
CONNECT_USER_AGENT = "CosmOS/1.0 (Linux) RetroAchievements-Connect/1.0"
REQUEST_TIMEOUT_S = 8
RECENT_ACHIEVEMENTS_LIMIT = 5


def load_credentials():
    credentials_path = profiles.ra_credentials_path()
    if credentials_path is None or not credentials_path.exists():
        log.debug(f"{credentials_path} does not exist, returning empty credentials")
        return {"username": "", "api_key": "", "connect_token": ""}
    parser = configparser.ConfigParser()
    parser.read(credentials_path)
    section = parser["RetroAchievements"] if parser.has_section("RetroAchievements") else {}
    username = section.get("username", "")
    api_key = section.get("api_key", "")
    connect_token = section.get("connect_token", "")
    log.debug(
        f"loaded credentials (username={'set' if username else 'empty'}, "
        f"api_key={'set' if api_key else 'empty'}, connect_token={'set' if connect_token else 'empty'})"
    )
    return {"username": username, "api_key": api_key, "connect_token": connect_token}


def save_credentials(username=None, api_key=None, connect_token=None):
    """Merges whichever fields are given (not None) into the active
    profile's ra_credentials.ini, leaving any field left as None
    untouched - e.g. saving a new Web API key shouldn't blank out an
    already-stored Connect API login token, and logging in via
    login_connect_api() shouldn't require re-typing the Web API key.
    Deliberately never logging an actual secret value, only whether one
    ended up set - this file, like the credentials themselves, is a
    secret (see .gitignore)."""
    credentials_path = profiles.ra_credentials_path()
    if credentials_path is None:
        return  # no active profile yet - nothing to persist to
    current = load_credentials()
    merged = {
        "username": username if username is not None else current["username"],
        "api_key": api_key if api_key is not None else current["api_key"],
        "connect_token": connect_token if connect_token is not None else current["connect_token"],
    }
    parser = configparser.ConfigParser()
    parser["RetroAchievements"] = merged
    with open(credentials_path, "w") as f:
        parser.write(f)
    log.info(
        f"saved RetroAchievements credentials (username={'set' if merged['username'] else 'empty'}, "
        f"api_key={'set' if merged['api_key'] else 'empty'}, connect_token={'set' if merged['connect_token'] else 'empty'})"
    )


def login_connect_api(username, password):
    """One-time login against RA's Connect API (the same endpoint
    RetroArch/Dolphin/PCSX2's own built-in login screens use) to
    exchange a username+password for a persistent login token - the
    password is used only for this single request and is never returned
    by this function or written anywhere. Called from a background
    thread (see app.py's run_async); never raises.

    Returns {"success": True, "token": ...} or
    {"success": False, "error": <message>}."""
    if not username or not password:
        return {"success": False, "error": "username and password are required"}
    try:
        response = requests.get(
            CONNECT_API_URL,
            params={"r": "login2", "u": username, "p": password},
            headers={"User-Agent": CONNECT_USER_AGENT},
            timeout=REQUEST_TIMEOUT_S,
        )
        response.raise_for_status()
        data = response.json()
    except Exception:
        log.exception("RetroAchievements Connect API login request failed")
        return {"success": False, "error": "network error"}
    if not data.get("Success"):
        log.info(f"RetroAchievements Connect API login rejected for username={'set' if username else 'empty'}")
        return {"success": False, "error": data.get("Error") or "invalid username or password"}
    log.info("RetroAchievements Connect API login succeeded")
    return {"success": True, "token": data["Token"]}


def _flatten_recent_achievements(raw):
    """API_GetUserSummary.php's RecentAchievements comes back as a dict
    nested {game_id: {achievement_id: {...}}}, not a flat list - this
    handles that shape defensively (and a flat list, in case that ever
    changes) rather than assuming one exact structure."""
    if isinstance(raw, list):
        return raw
    if not isinstance(raw, dict):
        return []
    flat = []
    for value in raw.values():
        if not isinstance(value, dict):
            continue
        if value and all(isinstance(v, dict) for v in value.values()):
            flat.extend(value.values())
        else:
            flat.append(value)
    return flat


def fetch_summary():
    """Returns a dict with username/rank/total_points/recent_achievements,
    or None if credentials aren't configured yet or the request fails -
    callers should render a "not connected"/"unavailable" state for None
    rather than crash, since this runs unattended on every menu visit.
    Called from a background thread (see app.py's run_async); never
    raises."""
    creds = load_credentials()
    if not creds["username"] or not creds["api_key"]:
        log.debug("no RetroAchievements credentials configured, skipping fetch")
        return None

    params = {"z": creds["username"], "y": creds["api_key"], "u": creds["username"]}
    try:
        response = requests.get(f"{API_BASE}/API_GetUserSummary.php", params=params, timeout=REQUEST_TIMEOUT_S)
        response.raise_for_status()
        data = response.json()
    except Exception:
        log.exception("failed to fetch RetroAchievements summary")
        return None

    log.debug(f"RetroAchievements response top-level keys: {sorted(data.keys()) if isinstance(data, dict) else type(data)}")

    recent = _flatten_recent_achievements(data.get("RecentAchievements", {}))
    recent_achievements = [
        {
            "title": a.get("Title", "?"),
            "game": a.get("GameTitle", "?"),
        }
        for a in recent[:RECENT_ACHIEVEMENTS_LIMIT]
    ]

    # Rank comes back as a JSON null (not a missing key) for an unranked
    # account (e.g. brand new, 0 points) - .get()'s default only covers
    # a missing key, so this normalizes the None case explicitly instead
    # of letting "Rank #None" reach the UI.
    rank = data.get("Rank")
    summary = {
        "username": creds["username"],
        "rank": rank if rank is not None else "unranked",
        "total_points": data.get("TotalPoints", "?"),
        "recent_achievements": recent_achievements,
    }
    log.debug(
        f"RetroAchievements summary fetched: rank={summary['rank']} "
        f"points={summary['total_points']} recent={len(recent_achievements)}"
    )
    return summary
