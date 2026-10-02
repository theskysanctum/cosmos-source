#!/usr/bin/env python3
"""Real, curated per-platform art (per direct instruction - "look in
bttu and use as much art as you can to cover the fifty systems in
systems.json", then "add all the art [types] to the ones you can" plus
"for the game engines and projects like mame just browse the web and a
hq logo"). Two sources, kept deliberately separate by what each is
actually good for:

launcher/platform_icons/<platform_id>.png - one representative image
per platform, resized so the LONGER edge is 200px (or 160px tall for
the hardware photos specifically - see below), alpha preserved. Two
real sub-sources feed this one directory:
  - Real hardware cutout photos (console/computer/handheld/arcade
    cabinet), sourced from the local bttu/ theme pack (a RetroBat/
    EmulationStation-style frontend theme, not itself committed - see
    .gitignore) at bttu/_systemmedia/hardware/ - every one hand-checked
    against systems.json's own id, not a blind filename match (a few
    needed a plain alias, e.g. "ps1"->"psx", "lynx"->"atarilynx"; one
    candidate, "love2d", was rejected outright - bttu's own "love.png"
    is a generic unbranded controller photo with no real connection to
    LÖVE at all).
  - Real official project logos, web-sourced (Wikipedia/Wikimedia
    Commons almost exclusively - SVG originals rasterized via Inkscape
    where only a vector source existed) for platforms that are
    fundamentally SOFTWARE rather than one piece of distinct physical
    hardware - the game-making engines (RPG Maker's 5 versions share
    its one franchise logo; GameMaker, Ren'Py, Adventure Game Studio,
    TIC-80, LÖVE each have their own) plus MAME and Visual Pinball,
    whose bttu hardware images (a generic arcade cabinet, a real
    pinball machine) got replaced by their own actual project logos -
    a truer, more specific identifier than art that could just as
    easily represent a different platform. MS-DOS falls into this same
    "software, not hardware" bucket (a real DOS logo now exists here,
    replacing what bttu could only offer as a plain flat logo, not a
    hardware photo, for the same reason - DOS itself was never one
    piece of hardware).

launcher/platform_controllers/<platform_id>.png - one real controller/
input-device vector per platform, resized so the longer edge is 300px,
sourced from bttu's own _common/controllers/ set (same hand-checked
standard as the icons above) - covers every platform bttu's icon set
covers except "love2d" (bttu's own controller image for it is the same
generic unbranded one its hardware slot already failed on).

Coverage: 49 of systems.json's 50 platforms have an icon. The one real
gap is wolfrpg (Wolf RPG Editor) - a niche Japanese freeware tool with
no Wikipedia/Wikimedia Commons presence and no confidently-official logo
found through any reliable source; left with no icon at all rather than
a guessed match, same "missing just means missing" convention this
project already uses for a profile with no picture. Two more platforms
that ARE real distinct arcade-emulation projects (fbneo, teknoparrot)
kept their existing bttu hardware-photo icons rather than a web logo -
no reliably-official HQ logo for either was found through Wikipedia,
Wikimedia Commons, or their own GitHub repos, and their existing bttu
art (a real branded arcade cabinet render, a real arcade joystick) was
already a confident, accurate match."""
from pathlib import Path

ICONS_DIR = Path(__file__).resolve().parent / "platform_icons"
CONTROLLERS_DIR = Path(__file__).resolve().parent / "platform_controllers"


def icon_path(platform_id):
	path = ICONS_DIR / f"{platform_id}.png"
	return path if path.is_file() else None


def controller_path(platform_id):
	path = CONTROLLERS_DIR / f"{platform_id}.png"
	return path if path.is_file() else None
