#!/usr/bin/env python3
"""Where the media library lives - plain folders under /cosmos (see
paths.py), shared across every profile the same way the ROM library
itself already is (games and media are both shared content libraries,
not per-user state - see profiles.py's own module docstring for what
*is* per-profile: saves, quick launch, dashboard widgets,
RetroAchievements login). These constants exist because Shop's own
download-destination picker (see app.py's open_shop_destination_picker()
and friends) needs to know where a downloaded movie/show/track/photo
should land, the same folders each media type's own built-in Local
Files add-on scans (see media/addons/)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import paths  # noqa: E402

MOVIES_DIR = paths.DATA_ROOT / "movies"
TV_DIR = paths.DATA_ROOT / "tv"
MUSIC_DIR = paths.DATA_ROOT / "music"
PHOTOS_DIR = paths.DATA_ROOT / "photos"


def movies_dir():
	return MOVIES_DIR


def tv_dir():
	return TV_DIR


def music_dir():
	return MUSIC_DIR


def photos_dir():
	return PHOTOS_DIR
