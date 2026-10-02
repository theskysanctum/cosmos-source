#!/usr/bin/env python3
"""Backend for which widgets appear on the main menu's dashboard, and in
what order (see build_widget_row() in app.py) - mirrors quick_launch.py's
own INI-backed add/remove/reorder pattern exactly (the active CosmOS
profile's own dashboard_widgets.ini - see profiles.py, one section per
active widget, in file order via ConfigParser, same as quick_launch.ini
and config.ini's own menu entries).

A fresh profile's dashboard starts empty, same as quick_launch starts with
no sites - RetroAchievements and Gaming News are just the first two entries
in WIDGET_TYPES, opted into (or back out of) the same way a quick-launch
site is, via the dashboard's own trailing "+ Add Widget" tile.

Used to carry a per-section "home" field and a per-widget-type "tags"
list (CosmOS had three home screens at the time - Games/Entertainment/
Dashboard - and a widget type could be eligible for some subset of
them) - both retired along with the rest of that split (there's only
one home screen, Dashboard, left - see app.py's HOME_SCREEN_NAMES
comment), so every widget type is simply always eligible now and a
widget's section name (its widget_id) is the only identity that
matters. An old dashboard_widgets.ini written before this still has a
"home" key on every section - harmlessly ignored, never read."""
import configparser
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import debuglog  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
import profiles  # noqa: E402

log = debuglog.setup("dashboard_widgets")

WIDGET_TYPES = {
	"retroachievements": {"label": "RetroAchievements"},
	"gamenews": {"label": "Gaming News"},
	"cosmochat": {"label": "Cosmochat"},
	"clock": {"label": "Clock"},
	"profile_summary": {"label": "Profile"},
}


def label_of(widget_id):
	return WIDGET_TYPES[widget_id]["label"]


def _load():
	parser = configparser.ConfigParser(interpolation=None)
	path = profiles.dashboard_widgets_ini_path()
	if path is not None:  # None before any profile has ever been created
		parser.read(path)
	return parser


def _save(parser):
	path = profiles.dashboard_widgets_ini_path()
	if path is None:
		return  # no active profile yet - nothing to persist to
	with open(path, "w") as f:
		parser.write(f)


def list_active():
	"""[widget_id] in display order. A section left over from an older
	version whose widget type no longer exists is silently skipped
	rather than raised - same defensive treatment config.ini entries
	already get elsewhere in this app."""
	parser = _load()
	active = [s for s in parser.sections() if s in WIDGET_TYPES]
	log.debug(f"list_active: {active}")
	return active


def list_available():
	"""Widget types not currently active - what the "+ Add Widget" page
	offers, in WIDGET_TYPES order."""
	active = set(list_active())
	return [wid for wid in WIDGET_TYPES if wid not in active]


def add_widget(widget_id):
	if widget_id not in WIDGET_TYPES:
		return
	parser = _load()
	if not parser.has_section(widget_id):
		parser.add_section(widget_id)
		_save(parser)
		log.info(f"added dashboard widget {widget_id!r}")


def remove_widget(widget_id):
	parser = _load()
	if parser.has_section(widget_id):
		parser.remove_section(widget_id)
		_save(parser)
		log.info(f"removed dashboard widget {widget_id!r}")


def move_widget(widget_id, direction):
	"""direction is -1 (move left/earlier) or +1 (move right/later). A
	no-op (not an error) if the widget is already at that end."""
	parser = _load()
	all_sections = parser.sections()
	if widget_id not in all_sections:
		return
	index = all_sections.index(widget_id)
	target = index + direction
	if not (0 <= target < len(all_sections)):
		return
	all_sections[index], all_sections[target] = all_sections[target], all_sections[index]

	reordered = configparser.ConfigParser(interpolation=None)
	for section in all_sections:
		reordered.add_section(section)
		for key, value in parser.items(section):
			reordered.set(section, key, value)
	_save(reordered)
	log.debug(f"moved widget {widget_id!r} {'left' if direction < 0 else 'right'}")
