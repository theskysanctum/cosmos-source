#!/usr/bin/env python3
"""Persistent GTK home-screen app: replaces the old curses launcher.py +
library.py + usb_menu.py UI with one process, navigated through a
Gtk.Stack of pages (menu / categories / manufacturers / platforms /
games / usb). Runs under its own X session on the launcher tty, started
once for the whole process lifetime, so its screens share one toolkit
with the filter/OSK/pause overlays that were already GTK3.

Launching an actual game, the browser, or a console-mode entry is
unchanged - it still goes through graphical.py's existing switch-to-
runtime-tty-and-back dance. Only the picker UI itself moved off curses;
list navigation (Up/Down/Enter) comes from Gtk.ListBox's own keyboard
handling, which already works with inputd.py's synthetic keypresses
the same way curses did.

This module is deliberately just the App class and its screens, not a
process entry point - run.py owns starting X, the OSK-watcher thread,
and Gtk.main(), and hot-reloads this module (and everything it imports)
in place while all of that stays up. See hotreload.py for how, and its
real limits: reassigned functions/constants take effect on the next
call automatically, but run.py has to explicitly destroy and rebuild
the App instance to pick up changes to the App class itself.
"""
import configparser
import datetime
import json
import math
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk, Pango  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
import graphical  # noqa: E402
import gamenews  # noqa: E402
import retroachievements  # noqa: E402
import quick_launch  # noqa: E402
import dashboard_widgets  # noqa: E402
import profiles  # noqa: E402
import save_sync  # noqa: E402
import theme  # noqa: E402
import controller_glyphs  # noqa: E402
import avatar3d_portrait_anim  # noqa: E402
import avatar3d_viewer  # noqa: E402
import sfx  # noqa: E402
import platform_history  # noqa: E402
import scaling  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "library"))
import library  # noqa: E402
import disc_content  # noqa: E402
import boxart  # noqa: E402
import skyscraper_scraper  # noqa: E402
import preview_video  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "usb"))
import usb_saves  # noqa: E402
import usb_content  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "notifications"))
import telegram_client  # noqa: E402
import telegram_credentials  # noqa: E402
import telegram_listener  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "settings"))
import wifi  # noqa: E402
import bluetooth  # noqa: E402
import display as display_settings  # noqa: E402
import igdb  # noqa: E402
import realdebrid  # noqa: E402
import screenscraper  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "packaging"))
import dropbox_token  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "filemanager"))
import files as filemanager  # noqa: E402
import local_scope as fm_local_scope  # noqa: E402
import remote as fm_remote  # noqa: E402
import remote_connections as fm_remote_connections  # noqa: E402


sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "media"))
import media_backend  # noqa: E402
import addon_backend  # noqa: E402
import addon_repo  # noqa: E402
import stremio_addon  # noqa: E402
import vlc_player  # noqa: E402
import photo_viewer  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "epic"))
import freegames_backend  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "input"))
import gamepad  # noqa: E402
import stick_mode  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "streaming"))
import sunshine_manager  # noqa: E402
import moonlight_manager  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import debuglog  # noqa: E402

log = debuglog.setup("app")

CONFIG_PATH = Path(__file__).parent / "config.ini"
USB_REFRESH_MS = 1000
COSMOCHAT_REFRESH_SECONDS = 5
RESOLUTION_REVERT_SECONDS = 15
# webshell/kiosk_launcher.py replaced browser/kiosk_launcher.py (which
# itself replaced Brave) - single-URL, no-chrome kiosk launches for the
# quick-launch row and Gaming News article links, see that file's own
# docstring.
KIOSK_LAUNCHER_PATH = str(Path(__file__).resolve().parent.parent / "webshell" / "kiosk_launcher.py")
# The staged Electron runtime kiosk_launcher.py itself needs (see
# webshell/electron_runtime.py) - checked directly here (see
# launch_subprocess()) rather than through a Flatpak-shaped
# is_installed() abstraction now that Brave/flatpak_apps.py are gone;
# Electron ships fetched-at-deploy-time, not installed as a system
# package, so a plain path existence check is the real thing to ask.
ELECTRON_BIN = Path(__file__).resolve().parent.parent / "webshell" / "electron" / "electron"

# dashboard_widgets widget id -> the full-content stack page its tile opens
# (see build_widget_row()/open_widget()).
WIDGET_PAGES = {
	"retroachievements": "retroachievements_full",
	"gamenews": "gaming_news",
	"cosmochat": "cosmochat_full",
	"clock": "clock_full",
	"profile_summary": "profile_summary_full",
}


def load_entries():
	"""Each INI section is one menu entry, in file order (ConfigParser
	preserves both) - the section name is the "name" field, everything
	else is whatever keys that section has. "command" is the one key
	that isn't a plain string downstream (launch_subprocess needs argv
	as a list for subprocess.run/Popen), so it's the one field split
	back out of its comma-joined INI representation here.

	Every entry lives on CosmOS's one home screen (Dashboard - see
	HOME_SCREEN_NAMES) - a leftover "home" field from an older config.ini
	(back when Games/Entertainment/Dashboard were three separate homes)
	is simply ignored; entry["home"] is always "dashboard" now.

	"utility" (a real boolean here, not the raw INI string - parsed once
	so every caller compares a plain True/False rather than re-parsing
	"true"/"false" itself) pulls an entry out of the header's own top-
	right row entirely, into the shared bottom-right utility cluster
	instead (see _build_home_page()) - for entries that aren't really
	about the dashboard's own top-row content (Settings, File Manager, ...).
	"""
	parser = configparser.ConfigParser()
	parser.read(CONFIG_PATH)
	entries = []
	for section in parser.sections():
		entry = {"name": section, **dict(parser.items(section))}
		entry["home"] = "dashboard"
		entry["utility"] = entry.get("utility", "false").lower() == "true"
		if "command" in entry:
			entry["command"] = [part.strip() for part in entry["command"].split(",")]
		entries.append(entry)
	log.debug(f"loaded {len(entries)} menu entries from {CONFIG_PATH}")
	return entries



def make_heading(text):
	"""A title label with a bold four-color "header stripe" banner
	underneath it (see theme.css's .header-stripe) - the same collision
	motif as make_row()'s bar, turned into an underline.

	Returns the container (what callers pack_start), with the actual
	Gtk.Label reachable as .label_widget for callers that need to change
	the heading text later (the container itself has no set_text)."""
	container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=scaling.px_v(4))

	label = Gtk.Label(label=text, xalign=0)
	label.get_style_context().add_class("title")
	container.pack_start(label, False, False, 0)
	container.label_widget = label

	stripe = Gtk.Box()
	stripe.get_style_context().add_class("header-stripe")
	container.pack_start(stripe, False, False, 0)

	return container


def make_row(item, text, icon_path=None):
	"""A ListBoxRow with a small white accent bar (see theme.css's
	.collision-bar) beside its label, plus a "> " selection caret on the
	text itself, both toggled by install_carets() - without baking that
	bookkeeping into every page builder. icon_path, if given and real,
	packs a small logo ahead of the label - same "just skip it if
	missing, never a placeholder" rule the rest of this app's own icon/
	thumbnail loading already follows."""
	row = Gtk.ListBoxRow()
	row.item = item
	row.plain_text = text

	box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=scaling.px_h(8))
	collision_bar = Gtk.Box()
	collision_bar.get_style_context().add_class("collision-bar")
	box.pack_start(collision_bar, False, False, 0)

	if icon_path is not None:
		pixbuf = GdkPixbuf.Pixbuf.new_from_file(str(icon_path))
		image = Gtk.Image.new_from_pixbuf(pixbuf)
		image.set_valign(Gtk.Align.CENTER)
		box.pack_start(image, False, False, 0)

	label = Gtk.Label(label="  " + text, xalign=0)
	box.pack_start(label, True, True, 0)
	row.label_widget = label

	row.add(box)
	return row


TILE_ZOOM_STEPS = 10
TILE_ZOOM_INTERVAL_MS = 16  # ~60fps


def animate_tile_zoom(child, target_size):
	"""The real "zoom up on highlight" effect used across this app's own
	tile grids (profile picker, avatar picker, header icon row, quick-
	launch row, games cover art, ...). GTK3's CSS engine has no transform
	property at all - confirmed live, not guessed: a real GtkCssProvider
	parse error ("'transform' is not a valid property name") the first
	time this was tried on the profile picker, which crashed the whole
	app outright. This is a genuine stepped resize driven from Python
	instead, the same manual-animation-loop shape
	App._platform_desc_scroll_tick() already established for the
	Platforms page's own auto-scroll.

	Any FlowBoxChild works with this as long as it carries two things,
	set up once at tile-build time: child.current_size (the tile's own
	currently-rendered size) and child.render_at_size(size) (a per-tile
	closure that redraws whatever image/icon that specific tile holds at
	a given pixel size - different for every tile type, e.g. a rendered
	avatar3d portrait vs. a static SVG vs. cover art loaded from disk,
	which is why this stays generic over a callback rather than
	hardcoding any one of them). A no-op if the tile's already at its
	target size."""
	start_size = child.current_size
	if start_size == target_size:
		return
	steps = TILE_ZOOM_STEPS
	delta = (target_size - start_size) / steps
	state = {"step": 0}

	def tick():
		state["step"] += 1
		size = round(start_size + delta * state["step"])
		child.current_size = size
		child.render_at_size(size)
		return state["step"] < steps

	GLib.timeout_add(TILE_ZOOM_INTERVAL_MS, tick)


def wire_tile_zoom(flowbox, base_size, target_size):
	"""Hooks a FlowBox's own "selected-children-changed" signal (fires on
	real BROWSE-mode selection moves - arrow-key navigation - not just
	activation) to drive animate_tile_zoom() for every child that opts in
	via a render_at_size callback - shared wiring for every zoomable tile
	grid in this app, called once from whichever build_*_page() method
	constructs that FlowBox."""
	def on_changed(fb):
		selected = set(fb.get_selected_children())
		for child in fb.get_children():
			if not hasattr(child, "render_at_size"):
				continue
			target = target_size if child in selected else base_size
			animate_tile_zoom(child, target)

	flowbox.connect("selected-children-changed", on_changed)


def _avatar_traits_or_default(name):
	"""profiles.get_picture_traits(name), falling back to
	avatar3d_viewer.CUSTOM_DEFAULTS - the same "None means nothing chosen
	yet, use the hardcoded default look" handling get_theme()'s own None
	case already gets, not an error every picture-displaying call site
	would otherwise have to guard against separately."""
	return profiles.get_picture_traits(name) or avatar3d_viewer.CUSTOM_DEFAULTS


def install_carets(listbox):
	def on_row_selected(_lb, selected_row):
		for row in listbox.get_children():
			row.label_widget.set_text(("> " if row is selected_row else "  ") + row.plain_text)

	listbox.connect("row-selected", on_row_selected)


# The Platforms page's right-hand info pane (see build_platforms_page()/
# _platform_desc_scroll_tick()) auto-scrolls its history blurb slowly
# downward, pauses at the bottom, jumps back to the top, pauses again,
# and repeats - a plain marquee-style loop, not a back-and-forth bounce.
PLATFORM_DESC_SCROLL_STEP = scaling.scale(0.6)  # scale(), not px() - a sub-pixel step, rounding could floor it to 0
PLATFORM_DESC_SCROLL_INTERVAL_MS = 40
PLATFORM_DESC_PAUSE_TICKS = 45  # ~1.8s at PLATFORM_DESC_SCROLL_INTERVAL_MS

NEWS_THUMB_WIDTH = scaling.px(96)
NEWS_THUMB_HEIGHT = scaling.px(54)

# Real box art (IGDB's own "cover_big" size is 264x374 - see
# library/boxart.py) scaled down slightly so a real TV-viewing-distance
# grid can show several per row without cropping into illegibility -
# still portrait cover-art proportions, not a square icon crop.
GAME_TILE_ART_WIDTH = scaling.px(220)
GAME_TILE_ART_HEIGHT = scaling.px(312)
# "Zoom up on highlight" target (see animate_tile_zoom()) - a smaller
# ratio than the single-row screens (profile/avatar picker, header
# icons): this is a real WRAPPING multi-row grid, not a fixed single
# row, so every tile (not just the selected one) has to reserve this
# extra headroom up front - a bigger ratio here would visibly thin out
# the whole grid with wasted padding, not just the selected tile.
GAME_TILE_ART_WIDTH_SELECTED = scaling.px(240)
GAME_TILE_ART_HEIGHT_SELECTED = round(GAME_TILE_ART_WIDTH_SELECTED * GAME_TILE_ART_HEIGHT / GAME_TILE_ART_WIDTH)


def make_game_tile(game, platform_id):
	"""One tile in the Games list's real box-art grid (see
	build_games_page()) - a portrait cover-art image over a title label,
	console-style (PS3/PS4/Xbox 360 all browse games by cover art, not a
	filename list - see [[project_cosmos_console_identity]]), not the
	old flat text-only ListBox this replaces. The image starts on a
	placeholder icon - boxart.fetch() is a real network call the first
	time a given game's art hasn't been cached yet, so it can't be ready
	synchronously while building the tile; App._on_game_boxart_ready()
	fills tile.art_image in once it lands (or leaves the placeholder in
	place if IGDB has nothing for this title - never a broken/blank
	image). child.boxart_key (the rom's own stem - stable across a
	multi-disc .m3u's own folder name too) is what ties an async fetch
	result back to the right tile, same idea as make_news_row()'s own
	row.thumb_image."""
	child = Gtk.FlowBoxChild()
	child.item = game
	child.platform_id = platform_id
	child.boxart_key = Path(game["rom"]).stem

	tile = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=scaling.px_v(8))
	tile.get_style_context().add_class("menu-icon-tile")

	art = Gtk.Image()
	art.set_size_request(GAME_TILE_ART_WIDTH_SELECTED, GAME_TILE_ART_HEIGHT_SELECTED)
	art.set_halign(Gtk.Align.CENTER)
	art.set_valign(Gtk.Align.CENTER)
	tile.pack_start(art, False, False, 0)
	child.art_image = art

	def render_at_size(width, child=child):
		"""width, not a generic "size" - box art is portrait, not square,
		so height is always derived to keep the real aspect ratio rather
		than distorting it. Re-checks boxart.get_cached() fresh every call
		(not just at tile-build time) so this same closure is also what
		App._on_game_boxart_ready() calls once a real async fetch lands -
		one shared code path for "draw whatever's currently the best art
		this tile has, at this size" rather than two."""
		height = round(width * GAME_TILE_ART_HEIGHT / GAME_TILE_ART_WIDTH)
		cached_path = boxart.get_cached(child.platform_id, child.boxart_key)
		if cached_path:
			try:
				pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(cached_path, width, height, True)
				child.art_image.set_from_pixbuf(pixbuf)
			except GLib.Error:
				log.warning(f"could not decode cached box art at {cached_path}", exc_info=True)
		else:
			placeholder_path = ICONS_DIR / "games.svg"
			if placeholder_path.exists():
				pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(str(placeholder_path), width // 2, width // 2, True)
				child.art_image.set_from_pixbuf(pixbuf)

	child.render_at_size = render_at_size
	child.current_size = GAME_TILE_ART_WIDTH
	render_at_size(GAME_TILE_ART_WIDTH)

	label_text = f"{game['description']} [USB]" if game["source"] == "usb" else game["description"]
	label = Gtk.Label(label=label_text)
	label.set_justify(Gtk.Justification.CENTER)
	label.set_line_wrap(True)
	label.set_max_width_chars(20)
	label.get_style_context().add_class("menu-icon-label")
	tile.pack_start(label, False, False, 0)

	child.add(tile)
	return child


def make_news_row(item):
	"""Same shape as make_row() (collision bar + caret-toggled label via
	install_carets()), plus a thumbnail image ahead of the text. The
	image starts blank - gamenews.fetch_thumbnail() is a network call,
	so it can't be ready synchronously while building the row; App.
	on_news_done() kicks off that fetch per-row and fills row.thumb_image
	in once it lands (see App._on_news_thumb_ready)."""
	row = Gtk.ListBoxRow()
	row.item = item
	row.plain_text = f"[{item['source']}] {item['title']}"

	box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=scaling.px_h(8))
	collision_bar = Gtk.Box()
	collision_bar.get_style_context().add_class("collision-bar")
	box.pack_start(collision_bar, False, False, 0)

	thumb = Gtk.Image()
	thumb.set_size_request(NEWS_THUMB_WIDTH, NEWS_THUMB_HEIGHT)
	box.pack_start(thumb, False, False, 0)
	row.thumb_image = thumb

	label = Gtk.Label(label="  " + row.plain_text, xalign=0)
	label.set_line_wrap(True)
	box.pack_start(label, True, True, 0)
	row.label_widget = label

	row.add(box)
	return row



COSMOCHAT_ICON_SIZE = scaling.px(32)


def _format_notification_time(epoch):
	return time.strftime("%I:%M %p", time.localtime(epoch)).lstrip("0")


def make_cosmochat_row(item):
	"""Same shape as make_news_row() (collision bar + caret-toggled label
	via install_carets(), fixed leading icon) - one bundled cosmochat.svg
	for every row rather than a per-sender fetched avatar, keeping this
	page's look consistent with the rest of the app rather than pulling
	in Telegram's own avatar images."""
	row = Gtk.ListBoxRow()
	row.item = item

	timestamp = _format_notification_time(item["timestamp"])
	row.plain_text = f"{item['sender']}: {item['text']}  ({timestamp})"

	box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=scaling.px_h(8))
	collision_bar = Gtk.Box()
	collision_bar.get_style_context().add_class("collision-bar")
	box.pack_start(collision_bar, False, False, 0)

	icon_path = ICONS_DIR / "cosmochat.svg"
	if icon_path.exists():
		pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(str(icon_path), COSMOCHAT_ICON_SIZE, COSMOCHAT_ICON_SIZE, True)
		image = Gtk.Image.new_from_pixbuf(pixbuf)
		box.pack_start(image, False, False, 0)

	label = Gtk.Label(label="  " + row.plain_text, xalign=0)
	label.set_line_wrap(True)
	box.pack_start(label, True, True, 0)
	row.label_widget = label

	row.add(box)
	return row


ICONS_DIR = Path(__file__).parent / "icons"
ICON_MAP = {
	"Games": "games.svg",
	"Game Disc": "disc.svg",
	"Web Browser": "web.svg",
	"Storage": "usb.svg",
	"Settings": "settings.svg",
	"File Manager": "folder.svg",
	"Kodi": "media.svg",
	"Switch Profile": "switch_profile.svg",
	"Power": "power.svg",
}
# Screens that are entirely useless without real internet - checked
# centrally by switch_to() itself (see its own docstring) rather than at
# each individual call site, so every path that ever reaches one of
# these (a Dashboard/Games/Entertainment icon, a Settings row, anywhere
# else switch_to() gets called) is covered automatically. Per direct
# instruction: Play Together first,
# then extended to the whole Settings > API section and every other
# screen whose entire purpose requires a live connection - RetroAchievements
# Login/its own achievements list, Gaming News, and CosmoChat. Video/
# Music/Photos add-on browsing and Epic/GOG (a separate external process,
# not a named stack screen at all) are NOT covered here - see the
# session notes on why those need different, more invasive treatment.
ONLINE_ONLY_SCREENS = {
	"play_together": "Play Together needs an internet connection to host or join a session - check your Wi-Fi and try again.",
	"api_settings": "API settings need an internet connection to be useful - check your Wi-Fi and try again.",
	"ra_login": "RetroAchievements login needs an internet connection - check your Wi-Fi and try again.",
	"retroachievements_full": "RetroAchievements needs an internet connection - check your Wi-Fi and try again.",
	"gaming_news": "Gaming News needs an internet connection - check your Wi-Fi and try again.",
	"cosmochat_full": "CosmoChat needs an internet connection - check your Wi-Fi and try again.",
}

ICON_PIXEL_SIZE = scaling.px(42)  # 10-foot-interface sizing - see theme.css's own base-font-size comment
ICON_PIXEL_SIZE_SELECTED = scaling.px(50)  # "zoom up on highlight" target - see animate_tile_zoom()
MEDIA_TYPE_LABELS = {"video": "Video", "music": "Music", "photos": "Photos"}

# CosmOS's one and only home screen, per direct instruction ("no more
# separate Entertainment and Games home screens, dashboard only").
# Dashboard used to be the true root/main screen with Games and
# Entertainment as separate destinations drilled into from its own big-
# tiles row - both of those were themselves just flat icon grids (Games:
# Games/Game Disc/Play Together; Entertainment: Video/Music/Photos), so
# they were retired entirely and every one of their icons folded
# directly into Dashboard's own single header icon row instead (see
# _build_home_page()) - there's nothing left to drill into any more.
# Kept as single-entry dicts (rather than flattened to plain constants)
# so every existing HOME_SCREEN_NAMES["dashboard"]/SCREEN_HOMES.get(...)
# call site elsewhere in this file keeps working unchanged.
HOME_SCREEN_NAMES = {"dashboard": "menu_dashboard"}
HOME_TITLES = {"dashboard": "Dashboard"}
SCREEN_HOMES = {screen: home for home, screen in HOME_SCREEN_NAMES.items()}
LOGO_PATH = ICONS_DIR / "cosmos_logo.png"
LOGO_PIXEL_HEIGHT = scaling.px(64)  # matches label.title's ~40px font + line spacing at a glance
ASTRONAR_BADGE_SIZE = scaling.px(64)  # same scale as the logo it sits next to in the header row

# A real Python object, not a string - a profile name is always a real
# user-typed string, so a sentinel like this can never collide with one
# (unlike, say, "__new__"). See App.open_avatar_picker()/
# confirm_avatar_traits() - the one case where there's no real
# profile yet to save a picture directly into.
NEW_PROFILE = object()

# branding/ is a plain git-tracked directory, not something the build
# needs to special-case - packaging/stage_build.sh stages every tracked
# file (`git ls-files`) already, the same as launcher/ itself.
BRANDING_DIR = Path(__file__).resolve().parent.parent / "branding"
CONTROLLER_OVERLAY_FILES = {
	"xbox": BRANDING_DIR / "cosmos_controller_xbox.png",
	"playstation": BRANDING_DIR / "cosmos_controller_playstation.png",
	"switch": BRANDING_DIR / "cosmos_controller_switch.png",
	None: BRANDING_DIR / "cosmos_controller.png",
}
CONTROLLER_HELP_WIDTH = scaling.px(1100)  # the full-screen version - large enough to actually read, not just a corner reminder


def make_menu_icon(entry):
	"""One entry in the dashboard's header icon bar (see build_dashboard_home_page) -
	a compact box using the same collision-gradient CSS border-image as
	the rest of the theme (see theme.css's .menu-icon-tile), just sized to
	sit in the header row instead of filling the page. Falls back to a
	text-only tile for any entry without a mapped icon file, so a future
	config.ini addition never silently disappears for lack of art.

	The icon image's own size_request is always reserved at the LARGER
	"zoomed" size (ICON_PIXEL_SIZE_SELECTED) - see animate_tile_zoom() -
	so the tile's own footprint in the row never changes on selection,
	only the icon drawn inside it does."""
	child = Gtk.FlowBoxChild()
	child.item = entry

	tile = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=scaling.px_v(2))
	tile.get_style_context().add_class("menu-icon-tile")
	tile.set_size_request(scaling.px(92), scaling.px(81))

	icon_file = ICON_MAP.get(entry["name"])
	icon_path = ICONS_DIR / icon_file if icon_file else None
	image = Gtk.Image()
	image.set_size_request(ICON_PIXEL_SIZE_SELECTED, ICON_PIXEL_SIZE_SELECTED)
	image.set_halign(Gtk.Align.CENTER)
	image.set_valign(Gtk.Align.CENTER)
	tile.pack_start(image, False, False, 0)

	def render_at_size(size, image=image, icon_path=icon_path):
		if icon_path and icon_path.exists():
			pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(str(icon_path), size, size, True)
			image.set_from_pixbuf(pixbuf)

	child.render_at_size = render_at_size
	child.current_size = ICON_PIXEL_SIZE
	render_at_size(ICON_PIXEL_SIZE)

	label = Gtk.Label(label=entry["name"])
	label.set_justify(Gtk.Justification.CENTER)
	label.set_line_wrap(True)
	label.set_max_width_chars(10)
	label.get_style_context().add_class("menu-icon-label")
	tile.pack_start(label, False, False, 0)

	child.add(tile)
	return child


def make_quick_launch_tile(entry):
	"""One tile in the quick-launch row (see build_quick_launch_row()) -
	entry is a dict from quick_launch.list_entries() for a real site, or
	None for the trailing "add a site" tile. Reuses the exact same
	.menu-icon-tile/.menu-icon-label styling as the header icon bar, just
	fed a fetched-and-cached favicon (a real external file, unlike the
	header icons' trusted bundled SVGs, so pixbuf loading is wrapped in
	try/except the way Epic/GOG's own cover-art loading already is
	rather than trusting it can't fail). Same "always reserve the zoomed
	size" convention as make_menu_icon() above."""
	child = Gtk.FlowBoxChild()
	child.entry = entry

	tile = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=scaling.px_v(2))
	tile.get_style_context().add_class("menu-icon-tile")
	tile.set_size_request(scaling.px(92), scaling.px(81))

	icon_path = Path(entry["icon_path"]) if entry and entry.get("icon_path") else (ICONS_DIR / "add.svg")
	image = Gtk.Image()
	image.set_size_request(ICON_PIXEL_SIZE_SELECTED, ICON_PIXEL_SIZE_SELECTED)
	image.set_halign(Gtk.Align.CENTER)
	image.set_valign(Gtk.Align.CENTER)
	tile.pack_start(image, False, False, 0)

	def render_at_size(size, image=image, icon_path=icon_path):
		if not icon_path.exists():
			return
		try:
			pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(str(icon_path), size, size, True)
			image.set_from_pixbuf(pixbuf)
		except GLib.Error:
			log.warning(f"could not decode quick-launch icon at {icon_path}", exc_info=True)

	child.render_at_size = render_at_size
	child.current_size = ICON_PIXEL_SIZE
	render_at_size(ICON_PIXEL_SIZE)

	label = Gtk.Label(label=entry["name"] if entry else "Add")
	label.set_justify(Gtk.Justification.CENTER)
	label.set_line_wrap(True)
	label.set_max_width_chars(10)
	label.get_style_context().add_class("menu-icon-label")
	tile.pack_start(label, False, False, 0)

	child.add(tile)
	return child


def make_widget_tile(widget_id, summary_text):
	"""One tile in the dashboard's widget row (see build_widget_row()) -
	widget_id is a dashboard_widgets.WIDGET_TYPES key for a real widget, or
	None for the trailing "add a widget" tile. A real info card (see
	theme.css's box.dashboard-widget-tile - filled panel, rounded corners,
	real padding), not the small square icon-button look of a quick-launch
	tile - deliberately just a title + one-line summary though, not the
	old full-height card that used to show a scrolling list inline (see
	dashboard_widgets.py's module docstring): activating a real widget's
	tile below (open_widget()) is what opens its actual content, on its
	own full page, rather than trying to cram a navigable list into a
	tile-sized card."""
	child = Gtk.FlowBoxChild()
	child.widget_id = widget_id

	tile = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=scaling.px_v(10))
	tile.get_style_context().add_class("dashboard-widget-tile")
	# Sized (and padded/spaced - see theme.css and build_widget_row()'s
	# own set_row_spacing/set_column_spacing) for a couch/TV viewing
	# distance, matching the rest of this file's ~1.4x "10-foot interface"
	# scale-up (see theme.css's own base font-size comment) - a real
	# report that these read as small/cramped, not a guess.
	tile.set_size_request(scaling.px(340), scaling.px(190))

	if widget_id is None:
		icon_path = ICONS_DIR / "add.svg"
		if icon_path.exists():
			try:
				pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(str(icon_path), ICON_PIXEL_SIZE * 2, ICON_PIXEL_SIZE * 2, True)
				image = Gtk.Image.new_from_pixbuf(pixbuf)
				image.set_halign(Gtk.Align.CENTER)
				tile.pack_start(image, True, True, 0)
			except GLib.Error:
				log.warning(f"could not decode add-widget icon at {icon_path}", exc_info=True)
		label = Gtk.Label(label="Add Widget")
		label.get_style_context().add_class("dashboard-widget-title")
		tile.pack_start(label, False, False, 0)
		child.add(tile)
		return child

	title = Gtk.Label(label=dashboard_widgets.label_of(widget_id), xalign=0)
	title.get_style_context().add_class("dashboard-widget-title")
	title.set_line_wrap(True)
	title.set_max_width_chars(16)
	title.set_ellipsize(Pango.EllipsizeMode.END)
	tile.pack_start(title, False, False, 0)

	# max_width_chars, not just line_wrap+ellipsize - without it a Label
	# still requests its natural (unwrapped) full-text width, and since
	# this tile sits in a homogeneous FlowBox, that one long headline
	# stretched every tile in the row out to match it - confirmed as the
	# actual cause of a real report ("the cards look like stretched
	# rectangles, not actually cards"), not just a padding/sizing tweak.
	summary = Gtk.Label(label=summary_text, xalign=0)
	summary.get_style_context().add_class("dashboard-widget-summary")
	summary.set_line_wrap(True)
	summary.set_max_width_chars(22)
	summary.set_ellipsize(Pango.EllipsizeMode.END)
	summary.set_lines(3)
	tile.pack_start(summary, True, True, 0)
	child.summary_label = summary

	child.add(tile)
	return child


def _scan_bluetooth_with_paired():
	"""Runs on the background thread started by run_async - bundles the
	scan with the paired-devices lookup so the UI can mark already-paired
	entries in one round trip."""
	found = bluetooth.scan()
	paired = {d["mac"] for d in bluetooth.paired_devices()}
	return found, paired


def make_list_page(title, items, get_label, on_back, empty_text="(nothing here)", extra_hint=None, get_icon=None):
	"""A titled, scrollable Gtk.ListBox page - the shape shared by the
	menu, categories, manufacturers, platforms, and shop screens. Arrow-
	key/Enter navigation comes for free from Gtk.ListBox's own focus
	handling; callers just connect "row-activated". get_icon, if given,
	is called per item for an optional row icon path - None (the
	default) matches every existing caller's plain text-only rows
	exactly."""
	box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=scaling.px_v(6))
	box.set_border_width(scaling.px_v(22))
	box.pack_start(make_heading(title), False, False, 0)

	scroller = Gtk.ScrolledWindow()
	scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
	listbox = Gtk.ListBox()
	listbox.set_selection_mode(Gtk.SelectionMode.BROWSE)
	install_carets(listbox)
	for item in items:
		listbox.add(make_row(item, get_label(item), get_icon(item) if get_icon else None))
	scroller.add(listbox)
	box.pack_start(scroller, True, True, 0)

	hint_text = "Up/Down: move   Enter: select"
	if not items:
		hint_text = empty_text
	elif extra_hint:
		hint_text += f"   {extra_hint}"
	if on_back:
		hint_text += "   Backspace: back"
	hint = Gtk.Label(label=hint_text)
	hint.get_style_context().add_class("dim-label")
	box.pack_start(hint, False, False, 0)

	box.listbox = listbox
	if items:
		listbox.select_row(listbox.get_row_at_index(0))
	box.show_all()
	return box


class App(Gtk.Window):
	def __init__(self, restart_requested=None, force_profile_picker=False):
		"""restart_requested, if given, is a threading.Event run.py's
		LauncherRunner polls alongside its own hot-reload check (see its
		own tick()) - activate_profile() sets it to ask for the same
		destroy-and-reconstruct-App() rebuild hot-reload already does,
		rather than hand-refreshing every profile-scoped widget (quick
		launch row, widget row, RA fetch, Shop destination picker)
		individually. None (e.g. any standalone test harness building an
		App directly) just means a profile switch can't request a
		rebuild - activate_profile() itself still works, the window just
		won't show the new profile's data until it's rebuilt some other
		way.

		force_profile_picker - per direct instruction ("I want the login
		screen to always show after the splash no matter what"): True
		only for run.py's own very first App() build in a process's
		whole lifetime (LauncherRunner.rebuild()'s own `self.window is
		None` check, the same signal that decides whether the boot
		splash shows at all - see its own docstring), never on a later
		hot-reload- or profile-switch-triggered rebuild, so editing code
		during dev still never knocks you back to profile selection."""
		super().__init__(title="Home Screen")
		self.restart_requested = restart_requested
		self.fullscreen()
		self.connect("key-press-event", self.on_key)

		self.stack = Gtk.Stack()
		# Real GTK-native page transitions, not a snap cut - switch_to()/
		# go_back() pick the actual direction (SLIDE_LEFT going deeper,
		# SLIDE_RIGHT coming back, CROSSFADE for anything that isn't
		# forward/back nav, e.g. a same-page content refresh) by setting
		# transition-type right before changing the visible child; this
		# just sets how long that transition takes, shared by every one.
		self.stack.set_transition_duration(220)
		# The confirm dialog (see build_confirm_page()) is a real floating
		# modal - a dimmed scrim + centered card layered over whatever
		# page is showing - not another Stack page, so the whole Stack
		# lives inside an Overlay rather than being added to the window
		# directly.
		self.root_overlay = Gtk.Overlay()
		self.root_overlay.add(self.stack)
		self.add(self.root_overlay)

		self.history = []
		self.cat_idx = self.man_idx = self.plat_idx = 0
		self.categories = library.load_categories()

		self.pending_wifi_ssid = None
		self.pending_display_mode = None
		self.previous_display_mode = None
		self.resolution_revert_timeout = None

		# Double Commander-style dual panes - index 0/1 = left/right, each
		# browsing its own source independently (see
		# build_filemanager_page()). No clipboard/paste concept: Copy/Move
		# (see build_fm_actions_page()) target the *other* pane's current
		# directory directly.
		#
		# Each pane is a dict, never a bare path - per direct instruction
		# ("nothing system facing should ever be visible"), a pane's
		# location is always scoped to one of three curated sources, never
		# an arbitrary filesystem path:
		#   {"kind": "local", "path": ...}   - filemanager/local_scope.py:
		#       None = the synthetic Media/ROMs category list (the one and
		#       only floor "up" ever stops at), "local:<Category>" = a
		#       category's own root/merge view, else a real path already
		#       confirmed in-scope.
		#   {"kind": "usb", "path": ...}      - usb_content.py's own
		#       already-mounted content drive; None = that drive's own
		#       root (or "nothing mounted" if none is).
		#   {"kind": "remote", "protocol": ..., "connection": {...}, "path": ...}
		#       - a saved FTP/SFTP/WebDAV connection (filemanager/
		#       remote_connections.py); path is a real remote path string,
		#       "/" is that connection's own floor.
		# Both panes start on Local - never $HOME (see paths.py's own
		# docstring, which used to document Path.home()-based browsing as
		# deliberate; reversed per direct instruction) - reachable from
		# there via the "[Change Source]" row.
		self.fm_panes = [{"kind": "local", "path": None}, {"kind": "local", "path": None}]
		self.fm_active_pane = 0
		self.fm_action_item = None
		self.fm_action_pane = None
		self.fm_input_purpose = None
		self.fm_input_pane = None
		# Which pane a source-picker/connection-list/connection-edit
		# screen is currently acting on behalf of, and which protocol's
		# saved connections filemanager_connections is showing - see
		# build_fm_source_page()/build_fm_connections_page()/
		# build_fm_connection_edit_page().
		self.fm_source_pane = None
		self.fm_connections_protocol = None
		self.fm_connection_editing_id = None
		self.confirm_on_yes = None
		self.ceremony_pulse_timeout_id = None
		# See build_avatar_picker_page() - which profile (or NEW_PROFILE,
		# staging one that doesn't exist yet) the live customize screen
		# is editing, and whichever trait set got confirmed for a
		# not-yet-real profile, picked up by activate_profile()'s own
		# create branch once submit_profile_add() actually creates it.
		self.avatar_picker_target = None
		self.avatar_picker_staged_traits = None

		# widget_id -> its tile's last-fetched one-line summary text, so
		# re-populating the widget row (add/remove/reorder) doesn't flash a
		# widget whose data was already fetched this session back to
		# "Loading..." - see _populate_widget_row()/_update_widget_tile_summary().
		self.widget_summaries = {}

		# One navigation "frame" stack per native media app (Video/Music/
		# Photos) - see build_video_categories_page()/_refresh_media_page()
		# below. Persists across screen visits, same as fm_dirs never
		# resetting to home - re-opening Video lands back where you left
		# off, not back at the top every time.
		self.media_nav = {}
		self.media_status_labels = {}

		# Keyed by home id (see HOME_SCREEN_NAMES) - a single-entry dict
		# now that there's only one home screen (Dashboard) left, kept as
		# a dict rather than flattened to plain attributes so this and
		# every method that reads it (self.menu_icon_flows[home], etc.)
		# didn't need to change shape when Games/Entertainment were
		# retired as separate homes.
		self.menu_icon_flows = {}
		self.utility_icon_flows = {}
		self.quick_launch_flows = {}
		self.widget_flows = {}
		# One Astronar badge for the Dashboard header (see
		# _build_astronar_badge()) - a list (built once, so always length
		# 1) rather than a single widget purely so refresh_astronar_
		# badges() doesn't need a separate code path from when this used
		# to mean "one badge per home screen" (back when there were
		# three); harmless to leave as a list now that there's only ever
		# one.
		self.astronar_badge_images = []
		# The badge's own FlowBox region (see _build_astronar_badge()) -
		# set for real once _build_home_page() runs; None here only so
		# _all_menu_flows()/_menu_row_regions() have something to
		# reference before that happens.
		self.astronar_badge_flow = None
		# unbind() callbacks from avatar3d_portrait_anim.bind() for
		# whichever picture is currently animating each Astronar badge/
		# the Dashboard Profile widget - refresh_astronar_badges()/
		# refresh_profile_summary_widget() call the old one (if any)
		# before binding a fresh picture, so a profile switch or an
		# avatar edit never leaves a stale animation still ticking.
		self._astronar_badge_anim_unbinds = []
		self._profile_summary_anim_unbind = None
		# Which "Add Quick Launch"/"Add Widget" flow is in progress - set
		# right before switch_to("quick_launch_add")/switch_to("widget_add"),
		# read back once that page submits, since those two pages are
		# shared single stack entries rather than duplicated. Always
		# "dashboard" now (there's only one home screen left), kept as a
		# named value rather than a bare literal so _populate_quick_launch_row()/
		# _populate_widget_row()'s own "home" parameter (still used to key
		# self.quick_launch_flows/self.widget_flows, harmless as a single-
		# entry dict) has an explicit source instead of a magic string
		# sprinkled at every call site.
		self.quick_launch_add_home = None
		self.widget_add_home = None

		self.build_dashboard_home_page()
		self.refresh_astronar_badges()
		self.build_categories_page()
		self.build_disc_page()
		self.build_video_categories_page()
		self.build_music_categories_page()
		self.build_photos_categories_page()
		self.build_stremio_addon_add_page()
		self.build_media_player_page()
		self.build_photo_viewer_page()
		self.build_usb_page()
		self.build_settings_page()
		self.build_theme_page()
		self.build_api_settings_page()
		self.build_power_page()
		self.build_os_install_page()
		self.build_wifi_page()
		self.build_wifi_password_page()
		self.build_bluetooth_page()
		self.build_display_page()
		self.build_igdb_page()
		self.build_realdebrid_page()
		self.build_screenscraper_page()
		self.build_game_detail_page()
		self.build_save_sync_page()
		self.build_save_sync_connect_page()
		self.build_save_sync_platforms_page()
		self.build_ra_login_page()
		self.build_play_together_page()
		self.build_play_together_host_page()
		self.build_play_together_join_page()
		self.build_play_together_stream_page()
		self.build_filemanager_page()
		self.build_filemanager_input_page()
		self.build_fm_connection_edit_page()
		self.build_quick_launch_add_page()
		self.build_widget_add_page()
		self.build_retroachievements_page()
		self.build_gaming_news_page()
		self.build_clock_widget_page()
		self.build_profile_summary_widget_page()
		self.build_cosmochat_page()
		self.build_cosmochat_credentials_page()
		self.build_cosmochat_qr_page()
		self.build_cosmochat_password_page()
		self.build_profile_picker_page()
		self.build_profile_add_page()
		self.build_profile_pin_page()
		self.build_avatar_picker_page()
		self.build_confirm_page()
		self.build_outfit_action_page()
		self.build_offline_toast()
		self.build_update_ceremony_overlay()
		self.build_controller_help_overlay()
		# every ROM platform (anything with its own roms_dir), for Game Disc's platform lookup and Storage's "Import to..." picker
		self.shop_platforms = library.list_rom_platforms(self.categories)

		GLib.timeout_add(PLATFORM_DESC_SCROLL_INTERVAL_MS, self._platform_desc_scroll_tick)

		self.on_enter_handlers = {
			"menu_dashboard": self.refresh_dashboard,
			"widget_add": self.refresh_widget_add_list,
			"profile_picker": self.refresh_profile_picker_list,
			"wifi": self.start_wifi_scan,
			"bluetooth": self.start_bluetooth_scan,
			"display": self.refresh_display_list,
			"igdb": self.refresh_igdb_page,
			"realdebrid": self.refresh_realdebrid_page,
			"screenscraper": self.refresh_screenscraper_page,
			"game_detail": self.refresh_game_detail_page,
			"save_sync": self.refresh_save_sync_page,
			"save_sync_connect": self.refresh_save_sync_connect_page,
			"save_sync_platforms": self.refresh_save_sync_platforms_page,
			"api_settings": self.refresh_api_settings_page,
			"ra_login": self.refresh_ra_login_page,
			"play_together_host": self.refresh_play_together_host_page,
			"play_together_join": self.refresh_play_together_join_page,
			"filemanager": self.refresh_fm_panes,
			"filemanager_connection_edit": self.refresh_fm_connection_edit_page,
			"cosmochat_full": self.refresh_cosmochat_state,
			"cosmochat_qr": self.start_cosmochat_login,
			"os_install": self.refresh_os_install_list,
			"theme": self.refresh_theme_page,
			"disc": self.refresh_disc_list,
			"video_categories": lambda: self._refresh_media_page("video"),
			"music_categories": lambda: self._refresh_media_page("music"),
			"photos_categories": lambda: self._refresh_media_page("photos"),
		}

		# Every page above is built (and safe to build - see profiles.py's
		# own None-safe profile_dir()) even with zero profiles ever
		# created; this is the one place that actually decides where to
		# land. A fresh install starts on "profile_add" instead of
		# Dashboard - nothing has anywhere real to read from yet - and
		# stays there (Backspace/Escape are a no-op on an empty
		# self.history) until a first profile is created, which rebuilds
		# the whole window (see activate_profile()) and lands cleanly on
		# Dashboard this same way. Otherwise, a real boot (force_profile_
		# picker, see this method's own docstring) always lands on the
		# profile picker instead of resuming straight into whatever
		# profile was last active - a real "login screen" every time, per
		# direct instruction. Dashboard, not Games, is the real landing
		# screen the rest of the time (once a profile's actually picked,
		# or on a hot-reload/profile-switch rebuild) - it's the main
		# screen per direct instruction (see HOME_SCREEN_NAMES's own
		# comment).
		if profiles.get_active_profile() is None:
			self.switch_to("profile_add", push_history=False)
		elif force_profile_picker and not profiles.shared():
			# On CosmOS the profile was already chosen (with its PIN, if it has one) by the CosmOS profile picker when the session started, and
			# GameBox uses that same profile: it does not ask who you are a second time.
			self.switch_to("profile_picker", push_history=False)
		else:
			self.switch_to("menu_dashboard", push_history=False)

	# -- background work --------------------------------------------------

	def run_async(self, work, on_done):
		"""Runs `work` (a no-arg callable) on a thread so slow backend calls
		(Wi-Fi/Bluetooth scans, connect attempts) don't freeze the whole UI,
		then delivers its result back to `on_done` on the GTK thread via
		GLib.idle_add - GTK widgets may only be touched from the main loop."""

		def target():
			result = work()
			GLib.idle_add(on_done, result)

		threading.Thread(target=target, daemon=True).start()

	def run_streamed(self, cmd, on_line, on_done, env=None):
		"""Like run_async(), but for a long-running subprocess whose real-
		time output matters (`bootc upgrade`/`bootc rollback`'s own
		download/apply progress, in particular) - `on_line` is called once
		per line of stdout+stderr as it happens, `on_done` once at the
		very end with the real exit code. Both always run on the GTK
		thread via GLib.idle_add, same reasoning as run_async().

		`env` is None by default (subprocess.Popen's own "inherit this
		process's environment" default)."""

		def target():
			proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1, env=env)
			for line in proc.stdout:
				GLib.idle_add(on_line, line.rstrip())
			proc.wait()
			GLib.idle_add(on_done, proc.returncode)

		threading.Thread(target=target, daemon=True).start()

	# -- stack navigation ------------------------------------------------

	def switch_to(self, name, push_history=True):
		"""The one real choke point every forward navigation in this app
		goes through (go_back() below deliberately bypasses it - see its
		own docstring - so backing OUT of an online-only screen is never
		gated, only getting INTO one is). name in ONLINE_ONLY_SCREENS
		means a real connectivity check (settings/wifi.py's own nmcli-
		backed probe) runs first via require_online(); everything else is
		zero-cost, unchanged from before this existed."""
		if name in ONLINE_ONLY_SCREENS:
			self.require_online(lambda: self._switch_to_now(name, push_history), ONLINE_ONLY_SCREENS[name])
			return
		self._switch_to_now(name, push_history)

	def _switch_to_now(self, name, push_history=True):
		current = self.stack.get_visible_child_name()
		log.debug(f"switch_to: {current} -> {name} (push_history={push_history})")
		if push_history and current:
			self.history.append(current)
		# push_history means "going deeper" (the common case - opening a
		# menu entry, a search result, a destination picker, ...), so it
		# slides in from the right like a new mobile-app screen would;
		# go_back() below mirrors it sliding the opposite way. Anything
		# switching pages without push_history isn't real forward/back
		# nav (e.g. a same-page content refresh, or landing on the very
		# first screen at startup) - crossfade reads as "changed" without
		# implying a direction that isn't actually there.
		self.stack.set_transition_type(
			Gtk.StackTransitionType.SLIDE_LEFT if push_history else Gtk.StackTransitionType.CROSSFADE
		)
		self.stack.set_visible_child_name(name)
		child = self.stack.get_visible_child()
		if hasattr(child, "listbox"):
			GLib.idle_add(child.listbox.grab_focus)
		elif hasattr(child, "focus_widget"):
			GLib.idle_add(child.focus_widget.grab_focus)
		handler = self.on_enter_handlers.get(name)
		if handler:
			handler()

	def go_back(self):
		if self.history:
			# Backspace/Escape (see on_key) call this directly, bypassing
			# PlayerPage's own on-screen Back button (which stops playback
			# itself before calling on_back) - confirmed live: without this,
			# backing out of the player mid-playback left libvlc decoding
			# and rendering into an off-screen widget indefinitely, audio
			# and all, until something else happened to call stop().
			if self.stack.get_visible_child_name() == "media_player":
				self.player_page.stop()
			elif self.stack.get_visible_child_name() == "avatar_picker":
				# Same reasoning as the media_player case just above -
				# without this, backing out of the live customize screen
				# (Backspace/Escape, bypassing confirm_avatar_traits()'s
				# own explicit stop()) left its render-frame GLib timer
				# running indefinitely against a now-invisible page.
				self.avatar_customize_page.stop()
			elif self.stack.get_visible_child_name() == "games":
				theme.clear_adaptive_accent()  # leaving the grid - back to the profile's own fixed theme
			elif self.stack.get_visible_child_name() == "disc" and self.disc_folder_stack:
				# Browsing a disc's own subfolders doesn't push real history (replace_page keeps the same "disc" name) - back comes up one
				# breadcrumb at a time here instead, only falling through to real history once the disc's root is reached again.
				self.replace_page("disc", self._build_disc_list_page(self.disc_folder_stack.pop()))
				return
			target = self.history.pop()
			log.debug(f"go_back -> {target}")
			self.stack.set_transition_type(Gtk.StackTransitionType.SLIDE_RIGHT)
			self.stack.set_visible_child_name(target)

	def replace_page(self, name, page):
		"""Removing the currently-visible child makes GtkStack silently
		fall back to some other child (confirmed directly: whichever was
		added first - "menu_dashboard" here) rather than staying "pending" for a
		same-named replacement - re-adding a widget under that name
		afterward does NOT restore it as visible on its own. Every
		on_enter_handlers entry that replaces the very page switch_to()
		just made visible (disc, video/music/photos categories, ...)
		would otherwise silently bounce back to the menu - re-asserting
		the name here after re-adding is what actually keeps it in place."""
		was_visible = self.stack.get_visible_child_name() == name
		old = self.stack.get_child_by_name(name)
		if old:
			self.stack.remove(old)
		self.stack.add_named(page, name)
		if was_visible:
			self.stack.set_visible_child_name(name)

	# -- generic Yes/No confirm dialog ---------------------------------

	def build_confirm_page(self):
		"""One shared destructive-action confirm dialog, reused by every
		feature that needs one (quick-launch removal, File Manager delete)
		rather than each building its own - call confirm(title, message,
		on_yes) to open it. A real floating modal (see __init__'s
		root_overlay), not a Stack page: a full-window dimming scrim with
		a small centered card on top, so whatever page was showing stays
		visible (dimmed) behind it - Gtk.Box.set_center_widget() does the
		centering, the scrim itself just fills the overlay via its
		default halign/valign. Yes/No are a 2-child FlowBox (the same
		selectable-tile mechanism as every other grid in this app, so
		Left/Right arrow-key movement between them is free), with No
		always on the right and always the default selection - the safer
		choice should be what a stray Enter press lands on. Built once and
		toggled with show()/hide() (set_no_show_all so the app's one
		startup show_all() doesn't force it visible), matching how
		epic_app.py's own progress indicators stay hidden until needed."""
		self.confirm_scrim = Gtk.Box()
		self.confirm_scrim.get_style_context().add_class("confirm-scrim")

		card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=scaling.px_v(10))
		card.get_style_context().add_class("confirm-card")
		card.set_halign(Gtk.Align.CENTER)
		card.set_valign(Gtk.Align.CENTER)

		self.confirm_heading = make_heading("")
		card.pack_start(self.confirm_heading, False, False, 0)

		self.confirm_message_label = Gtk.Label(xalign=0, wrap=True)
		self.confirm_message_label.set_max_width_chars(48)
		card.pack_start(self.confirm_message_label, False, False, 0)

		self.confirm_flow = Gtk.FlowBox()
		# The white "selected" breathing-border look elsewhere (header
		# icons, quick launch, widgets) only renders under a flowbox's own
		# .row-focused class (see on_menu_row_focus_child's docstring on
		# why that's tracked explicitly rather than trusting real focus) -
		# this dialog has nothing else to ever be "row-focused" instead of,
		# so it can just carry the class permanently rather than needing
		# its own focus-tracking wiring.
		self.confirm_flow.get_style_context().add_class("row-focused")
		self.confirm_flow.set_selection_mode(Gtk.SelectionMode.BROWSE)
		self.confirm_flow.set_homogeneous(True)
		self.confirm_flow.set_min_children_per_line(2)
		self.confirm_flow.set_max_children_per_line(2)
		self.confirm_flow.set_activate_on_single_click(False)
		self.confirm_flow.set_halign(Gtk.Align.CENTER)
		self.confirm_flow.connect(
			"child-activated", lambda _fb, child: self.on_confirm_choice(child.is_yes)
		)

		yes_child = self._make_confirm_tile("Yes")
		yes_child.is_yes = True
		no_child = self._make_confirm_tile("No")
		no_child.is_yes = False
		self.confirm_flow.add(yes_child)
		self.confirm_flow.add(no_child)  # No is always second - i.e. on the right
		card.pack_start(self.confirm_flow, False, False, 0)

		hint = controller_glyphs.make_hint_row(
			"Left/Right: choose   ", controller_glyphs.CONFIRM, ": confirm   Escape: cancel"
		)
		card.pack_start(hint, False, False, 0)

		self.confirm_scrim.set_center_widget(card)
		self.root_overlay.add_overlay(self.confirm_scrim)
		# show_all() BEFORE set_no_show_all(True) - confirmed the hard way
		# that the order matters: no_show_all blocks a show_all() cascade
		# starting from this exact widget too, not just one arriving from
		# an ancestor, so setting it first left every descendant
		# permanently unshown (and unfocusable) no matter how many times
		# confirm_scrim itself was later shown.
		self.confirm_scrim.show_all()
		self.confirm_scrim.set_no_show_all(True)
		self.confirm_scrim.hide()

	def _make_confirm_tile(self, text):
		child = Gtk.FlowBoxChild()
		tile = Gtk.Box()
		tile.get_style_context().add_class("menu-icon-tile")
		label = Gtk.Label(label=text)
		label.get_style_context().add_class("menu-icon-label")
		tile.pack_start(label, True, True, 0)
		child.add(tile)
		return child

	def confirm(self, title, message, on_yes):
		"""Opens the shared confirm dialog as a floating modal over
		whatever's currently showing. on_yes is called with no arguments
		if the user picks Yes; picking No (or Escape/Backspace - see
		on_key's confirm_scrim check) does nothing and just closes it.
		Gtk.grab_add() makes this genuinely modal for keyboard/gamepad
		input - the page underneath keeps its own focus state, but key
		events go to the dialog until it's closed."""
		self.confirm_heading.label_widget.set_text(title)
		self.confirm_message_label.set_text(message)
		self.confirm_on_yes = on_yes
		self.confirm_scrim.show()
		no_child = self.confirm_flow.get_child_at_index(1)
		self.confirm_flow.select_child(no_child)  # default to No
		# Focus the specific child widget, not the FlowBox container -
		# confirmed the hard way that FlowBox.grab_focus() doesn't
		# reliably delegate down to whichever child is selected; grabbing
		# the child directly does. idle_add (not a direct call) because
		# grabbing focus right after show() doesn't reliably take before
		# the widget is fully mapped - the same reason switch_to()'s own
		# focus grab already needed it.
		GLib.idle_add(no_child.grab_focus)
		self.confirm_scrim.grab_add()

	def hide_confirm(self):
		self.confirm_scrim.grab_remove()
		self.confirm_scrim.hide()

	def on_confirm_choice(self, is_yes):
		self.hide_confirm()
		if is_yes:
			self.confirm_on_yes()

	# -- outfit slot action popup (Customize Astronar) -------------------

	def build_outfit_action_page(self):
		"""A small floating modal - same "dimming scrim + centered card"
		shape as build_confirm_page()'s own Yes/No dialog (same
		confirm-scrim/confirm-card CSS classes, same real-GTK-selection-
		based FlowBox rather than avatar3d_viewer.py's own Cairo-drawn-
		ring convention, since this lives in app.py's own modal-dialog
		family, not that screen's widget tree) - but with two custom-
		labeled actions instead of Yes/No, since confirm()/
		build_confirm_page() are hardcoded to that literal wording and
		already relied on elsewhere (quick-launch removal, File Manager
		delete) that must not be disturbed. Opened by Confirm on a
		filled saved-outfit slot (see on_key's own avatar_picker
		dispatch) - per direct instruction, selecting a slot no longer
		loads it immediately, it offers a choice first."""
		self.outfit_action_scrim = Gtk.Box()
		self.outfit_action_scrim.get_style_context().add_class("confirm-scrim")

		card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=scaling.px_v(10))
		card.get_style_context().add_class("confirm-card")
		card.set_halign(Gtk.Align.CENTER)
		card.set_valign(Gtk.Align.CENTER)

		self.outfit_action_heading = make_heading("")
		card.pack_start(self.outfit_action_heading, False, False, 0)

		self.outfit_action_flow = Gtk.FlowBox()
		self.outfit_action_flow.get_style_context().add_class("row-focused")
		self.outfit_action_flow.set_selection_mode(Gtk.SelectionMode.BROWSE)
		self.outfit_action_flow.set_homogeneous(True)
		self.outfit_action_flow.set_min_children_per_line(2)
		self.outfit_action_flow.set_max_children_per_line(2)
		self.outfit_action_flow.set_activate_on_single_click(False)
		self.outfit_action_flow.set_halign(Gtk.Align.CENTER)
		self.outfit_action_flow.connect(
			"child-activated", lambda _fb, child: self.on_outfit_action_choice(child.action)
		)

		change_child = self._make_confirm_tile("Change into it")
		change_child.action = "change"
		delete_child = self._make_confirm_tile("Delete")
		delete_child.action = "delete"
		self.outfit_action_flow.add(change_child)
		self.outfit_action_flow.add(delete_child)  # Delete always on the right, never the default
		card.pack_start(self.outfit_action_flow, False, False, 0)

		hint = controller_glyphs.make_hint_row(
			"Left/Right: choose   ", controller_glyphs.CONFIRM, ": confirm   Escape: cancel"
		)
		card.pack_start(hint, False, False, 0)

		self.outfit_action_scrim.set_center_widget(card)
		self.root_overlay.add_overlay(self.outfit_action_scrim)
		self.outfit_action_scrim.show_all()  # same show-then-no_show_all ordering build_confirm_page() already found necessary
		self.outfit_action_scrim.set_no_show_all(True)
		self.outfit_action_scrim.hide()
		self.outfit_action_index = None
		self.outfit_action_outfit = None

	def open_outfit_action_popup(self, index, outfit):
		"""index/outfit - which saved-outfit slot this popup is acting
		on (see on_key's own avatar_picker dispatch, the only call
		site). "Change into it" is always the safe default (index 0,
		selected on open) - Delete needs an explicit Right press first,
		the same "the safer choice is what a stray Confirm lands on"
		reasoning build_confirm_page()'s own No-by-default already
		established."""
		self.outfit_action_index = index
		self.outfit_action_outfit = outfit
		self.outfit_action_heading.label_widget.set_text(outfit["name"])
		self.outfit_action_scrim.show()
		change_child = self.outfit_action_flow.get_child_at_index(0)
		self.outfit_action_flow.select_child(change_child)
		GLib.idle_add(change_child.grab_focus)
		self.outfit_action_scrim.grab_add()

	def hide_outfit_action_popup(self):
		self.outfit_action_scrim.grab_remove()
		self.outfit_action_scrim.hide()
		self.outfit_action_index = None
		self.outfit_action_outfit = None

	def on_outfit_action_choice(self, action):
		index = self.outfit_action_index
		name = self.outfit_action_outfit["name"]
		self.hide_outfit_action_popup()
		if action == "change":
			self.avatar_customize_page.outfit_confirm()
		elif action == "delete":
			self.confirm(
				"Delete Outfit", f"Delete {name!r}? This can't be undone.",
				on_yes=lambda: self.avatar_customize_page.outfit_delete(index),
			)

	# -- offline toast: a real connectivity check blocked something -----

	def build_offline_toast(self):
		"""A lightweight, non-interactive "No Internet Connection" toast -
		unlike build_confirm_page()'s Yes/No dialog, there's no choice to
		make here, so it never grabs input the way that modal does: it
		just tells the user why nothing happened and closes itself after
		~3 seconds (real console UX for a blocked action - PS5/Xbox both
		toast-and-fade rather than making you dismiss a dialog for
		something you can't do anything about anyway). Shared by every
		online-only entry point (see open_online_only_screen() and
		require_online())."""
		self.offline_toast_scrim = Gtk.Box()
		self.offline_toast_scrim.get_style_context().add_class("confirm-scrim")

		card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=scaling.px_v(10))
		card.get_style_context().add_class("confirm-card")
		card.set_halign(Gtk.Align.CENTER)
		card.set_valign(Gtk.Align.CENTER)

		card.pack_start(make_heading("No Internet Connection"), False, False, 0)

		self.offline_toast_message_label = Gtk.Label(xalign=0, wrap=True)
		self.offline_toast_message_label.set_max_width_chars(48)
		card.pack_start(self.offline_toast_message_label, False, False, 0)

		self.offline_toast_scrim.set_center_widget(card)
		self.root_overlay.add_overlay(self.offline_toast_scrim)
		self.offline_toast_scrim.show_all()
		self.offline_toast_scrim.set_no_show_all(True)
		self.offline_toast_scrim.hide()
		self._offline_toast_timeout_id = None

	def show_offline_toast(self, message="This needs an internet connection - check your Wi-Fi and try again."):
		"""Shows the shared offline toast for ~3 seconds, then hides
		itself automatically - safe to call again while already showing
		(resets the timer rather than stacking multiple auto-hides)."""
		self.offline_toast_message_label.set_text(message)
		self.offline_toast_scrim.show()
		if self._offline_toast_timeout_id is not None:
			GLib.source_remove(self._offline_toast_timeout_id)
		self._offline_toast_timeout_id = GLib.timeout_add(3000, self._hide_offline_toast)

	def _hide_offline_toast(self):
		self.offline_toast_scrim.hide()
		self._offline_toast_timeout_id = None
		return False

	def require_online(self, on_online, message=None):
		"""The general-purpose gate every online-only entry point below
		funnels through: runs a real connectivity check (settings/
		wifi.py's own nmcli-backed probe) off the GTK thread, then either
		calls on_online() (no args) or shows the shared offline toast -
		never both. Use this directly for an action (a button/row
		handler); open_online_only_screen() is the thin wrapper for the
		common "just switch_to(screen) if online" case."""
		self.run_async(wifi.is_online, lambda online: self._on_require_online_checked(on_online, message, online))

	def _on_require_online_checked(self, on_online, message, online):
		if online:
			on_online()
		elif message:
			self.show_offline_toast(message)
		else:
			self.show_offline_toast()

	# -- update ceremony: full-screen takeover during a real bootc upgrade/
	# rollback (see _run_bootc_upgrade()/_run_bootc_rollback()) ------------

	def build_update_ceremony_overlay(self):
		"""A real floating modal like build_confirm_page()'s own dialog,
		just far more opaque and with no Yes/No choice at all - once a real
		`bootc upgrade`/`bootc rollback` is actually running, there is
		nothing safe to let the user navigate to underneath it (see
		on_key's own ceremony_scrim check, which swallows every key while
		this is up). An indeterminate Gtk.ProgressBar (pulsed on a timer,
		not a real percentage - bootc's own upgrade output is a stream of
		textual layer-pull messages, not a numeric progress value) plus the
		streamed log underneath it stand in for the "don't power off"
		console-style ceremony this is meant to read as."""
		self.ceremony_scrim = Gtk.Box()
		self.ceremony_scrim.get_style_context().add_class("ceremony-scrim")

		card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=scaling.px_v(14))
		card.get_style_context().add_class("ceremony-card")
		card.set_halign(Gtk.Align.CENTER)
		card.set_valign(Gtk.Align.CENTER)

		self.ceremony_heading = make_heading("")
		card.pack_start(self.ceremony_heading, False, False, 0)

		warning = Gtk.Label(label="Do not power off the system.")
		warning.get_style_context().add_class("ceremony-warning")
		card.pack_start(warning, False, False, 0)

		self.ceremony_progress = Gtk.ProgressBar()
		card.pack_start(self.ceremony_progress, False, False, 0)

		log_scroller = Gtk.ScrolledWindow()
		log_scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
		log_scroller.set_can_focus(False)
		log_scroller.set_size_request(-1, scaling.px(240))
		self.ceremony_log_buffer = Gtk.TextBuffer()
		self.ceremony_log_view = Gtk.TextView(buffer=self.ceremony_log_buffer)
		self.ceremony_log_view.set_editable(False)
		self.ceremony_log_view.set_monospace(True)
		self.ceremony_log_view.set_can_focus(False)
		log_scroller.add(self.ceremony_log_view)
		card.pack_start(log_scroller, False, False, 0)

		self.ceremony_scrim.set_center_widget(card)
		self.root_overlay.add_overlay(self.ceremony_scrim)
		# Same show_all()-before-set_no_show_all() ordering build_confirm_
		# page() already documents needing - no_show_all blocks a show_all()
		# cascade starting from this exact widget too, not just one
		# arriving from an ancestor.
		self.ceremony_scrim.show_all()
		self.ceremony_scrim.set_no_show_all(True)
		self.ceremony_scrim.hide()

	def _show_ceremony(self, title):
		self.ceremony_heading.label_widget.set_text(title)
		self.ceremony_log_buffer.set_text("")
		self.ceremony_progress.set_fraction(0.0)
		self.ceremony_scrim.show()
		self.ceremony_scrim.grab_add()
		if self.ceremony_pulse_timeout_id is None:
			self.ceremony_pulse_timeout_id = GLib.timeout_add(120, self._pulse_ceremony_progress)

	def _hide_ceremony(self):
		if self.ceremony_pulse_timeout_id is not None:
			GLib.source_remove(self.ceremony_pulse_timeout_id)
			self.ceremony_pulse_timeout_id = None
		self.ceremony_scrim.grab_remove()
		self.ceremony_scrim.hide()

	def _pulse_ceremony_progress(self):
		self.ceremony_progress.pulse()
		return True  # keep the GLib.timeout_add call repeating

	def _ceremony_log_line(self, text):
		end_iter = self.ceremony_log_buffer.get_end_iter()
		self.ceremony_log_buffer.insert(end_iter, text + "\n")
		self.ceremony_log_view.scroll_to_iter(self.ceremony_log_buffer.get_end_iter(), 0, False, 0, 0)

	def build_controller_help_overlay(self):
		"""The Cosmos button's own full-screen reference card (see
		input/inputd.py's BTN_MODE mapping and on_key()'s own KEY_Help
		handling) - same real floating-modal shape as build_confirm_page()/
		build_update_ceremony_overlay(), just with nothing to choose or
		wait on: a scrim, a heading (branded with the real Cosmos-button
		mark itself, branding/cosmos_button.png - cropped straight out of
		the real controller art, not the separate, more polished header
		logo), the current controller image at real reading size (not the
		small corner reminder both home pages already show), and a close
		hint."""
		self.controller_help_scrim = Gtk.Box()
		self.controller_help_scrim.get_style_context().add_class("controller-help-scrim")

		card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=scaling.px_v(18))
		card.get_style_context().add_class("controller-help-card")
		card.set_halign(Gtk.Align.CENTER)
		card.set_valign(Gtk.Align.CENTER)

		heading = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=scaling.px_h(12))
		heading.set_halign(Gtk.Align.CENTER)
		logo_pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(str(controller_glyphs.LOGO_BADGE_PATH), -1, scaling.px(48), True)
		heading.pack_start(Gtk.Image.new_from_pixbuf(logo_pixbuf), False, False, 0)
		heading_label = make_heading("Controller Help")
		heading.pack_start(heading_label, False, False, 0)
		card.pack_start(heading, False, False, 0)

		self.controller_help_image = Gtk.Image()
		card.pack_start(self.controller_help_image, False, False, 0)

		hint = Gtk.Label(label="Escape / Backspace / Cosmos button: close")
		hint.get_style_context().add_class("dim-label")
		hint.set_halign(Gtk.Align.CENTER)
		card.pack_start(hint, False, False, 0)

		self.controller_help_scrim.set_center_widget(card)
		self.root_overlay.add_overlay(self.controller_help_scrim)
		self.controller_help_scrim.show_all()
		self.controller_help_scrim.set_no_show_all(True)
		self.controller_help_scrim.hide()

	def show_controller_help(self):
		# Detected fresh on every open, not read from some continuously-
		# polled cached value - there's no longer a background poll to
		# keep one current (see the removed corner overlay), and this is
		# cheap enough (a plain evdev device scan) to just do right here.
		path = CONTROLLER_OVERLAY_FILES[gamepad.detect_brand()]
		try:
			pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(str(path), CONTROLLER_HELP_WIDTH, -1, True)
		except GLib.Error:
			log.warning(f"could not decode controller help art at {path}", exc_info=True)
			return
		self.controller_help_image.set_from_pixbuf(pixbuf)
		self.controller_help_scrim.show()
		self.controller_help_scrim.grab_add()

	def hide_controller_help(self):
		self.controller_help_scrim.grab_remove()
		self.controller_help_scrim.hide()

	def on_key(self, _widget, event):
		"""Backspace/q double as global "back"/"home" navigation - except
		while a Gtk.Entry has focus (the Wi-Fi password and IGDB credential
		forms), where those keys must type normally. Escape always means
		"back", since nothing here ever needs to type a literal Escape.

		The confirm dialog (see confirm()) is a floating modal, not a
		Stack page - self.stack's own visible child never changes while
		it's open - so it's checked first, ahead of anything keyed off
		current_screen: Escape/Backspace close it (same as picking No),
		Left/Right/Enter are handled explicitly here too (see below) -
		a real report ("can't use a keyboard in the yes/no box") traced to
		trusting the dialog's own FlowBox to have real GTK keyboard focus
		and handle those natively; it often doesn't (grab_add() makes this
		modal for *event delivery*, but doesn't itself guarantee the
		FlowBox - rather than whatever had focus on the page underneath -
		is what actually owns keyboard focus), so an unhandled Right arrow
		was silently reaching the *page underneath* instead, moving its
		selection and, from there, somehow leaving the dialog closed.
		Reading/setting the dialog's own selection state directly (not
		relying on which widget has real focus) sidesteps that ambiguity
		entirely - every other key while the dialog is open is swallowed
		outright, never left to fall through to the page underneath.

		The resolution-confirm screen is a similar special case, just as
		an actual Stack page: it has no listbox or entry to receive keys
		itself, and Enter/Escape there mean "keep"/"revert" rather than
		plain navigation."""
		# UI sound effects (see sfx.py) - hooked at the raw keypress here,
		# not each screen's own row-activated/child-activated handler:
		# there's one of those per page (dozens of them), while every
		# screen's navigation and every dialog's own Left/Right/Enter/
		# Escape handling (both below and native GTK ListBox movement)
		# all still go through this one function first. Gated on typing
		# (a Gtk.Entry focused) the same way BackSpace-as-back already is
		# below - arrow keys moving a text cursor and Enter submitting a
		# form are normal text editing, not menu navigation, and
		# shouldn't sound like it.
		typing_for_sfx = isinstance(self.get_focus(), Gtk.Entry)
		if event.keyval in (Gdk.KEY_Up, Gdk.KEY_Down, Gdk.KEY_Left, Gdk.KEY_Right) and not typing_for_sfx:
			sfx.play("move")
		elif event.keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
			sfx.play("confirm")
		elif event.keyval == Gdk.KEY_Escape or (event.keyval == Gdk.KEY_BackSpace and not typing_for_sfx):
			sfx.play("back")
		if self.confirm_scrim.get_visible():
			if event.keyval in (Gdk.KEY_Escape, Gdk.KEY_BackSpace):
				self.hide_confirm()
				return True
			if event.keyval in (Gdk.KEY_Left, Gdk.KEY_Right):
				# Yes is index 0 (left), No is index 1 (right) - see
				# build_confirm_page()'s own "No is always second" comment.
				target_index = 0 if event.keyval == Gdk.KEY_Left else 1
				self.confirm_flow.select_child(self.confirm_flow.get_child_at_index(target_index))
				return True
			if event.keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
				selected = self.confirm_flow.get_selected_children()
				self.on_confirm_choice(selected[0].is_yes if selected else False)
				return True
			return True

		if self.outfit_action_scrim.get_visible():
			# Same shape as confirm_scrim's own block just above - "Change
			# into it" is index 0 (left, the default), "Delete" is index 1
			# (right) - see build_outfit_action_page()'s own comment.
			if event.keyval in (Gdk.KEY_Escape, Gdk.KEY_BackSpace):
				self.hide_outfit_action_popup()
				return True
			if event.keyval in (Gdk.KEY_Left, Gdk.KEY_Right):
				target_index = 0 if event.keyval == Gdk.KEY_Left else 1
				self.outfit_action_flow.select_child(self.outfit_action_flow.get_child_at_index(target_index))
				return True
			if event.keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
				selected = self.outfit_action_flow.get_selected_children()
				self.on_outfit_action_choice(selected[0].action if selected else "change")
				return True
			return True

		if self.ceremony_scrim.get_visible():
			# No Yes/No choice here at all (unlike confirm_scrim just
			# above) - once a real bootc upgrade/rollback is actually
			# running there is nothing safe to cancel or navigate away to,
			# so every key is simply swallowed until _hide_ceremony() is
			# called from the run's own on_done callback.
			return True

		if self.controller_help_scrim.get_visible():
			# A read-only reference card, not a dialog with a choice to
			# make - Escape/Backspace/the Cosmos button itself (pressing
			# it again) all just close it; every other key is swallowed
			# the same way the two scrims above already do, since there's
			# nothing else safe to do while it's covering the screen.
			if event.keyval in (Gdk.KEY_Escape, Gdk.KEY_BackSpace, Gdk.KEY_Help):
				self.hide_controller_help()
			return True

		current_screen = self.stack.get_visible_child_name()

		if current_screen == "resolution_confirm":
			if event.keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
				self.keep_display_mode()
				return True
			if event.keyval in (Gdk.KEY_Escape, Gdk.KEY_BackSpace):
				self.revert_display_mode()
				return True
			return False

		if current_screen == "avatar_picker":
			# Explicit, matching every other screen's own "handle
			# navigation keys ourselves" convention rather than relying on
			# any widget's native keynav - Up/Down move which trait is
			# selected, Left/Right change its value, L1/R1 jump a whole
			# CATEGORY at once (see avatar3d_viewer.AvatarCustomizePage's
			# own field-cursor/category model - 87 fields is too many to
			# reach anything by Up/Down alone), TL2/TR2 (comma/period -
			# see input/inputd.py's own ACTION_BUTTON_MAP) rotate the live
			# preview itself.
			page = self.avatar_customize_page
			# Left/Right/Confirm/Delete all branch on page.outfit_focused
			# (whether the saved-outfit row or the field/options grid
			# currently has focus - see AvatarCustomizePage.field_next()'s
			# own crossover). Up/Down/L1/R1/TL2/TR2/Backspace stay wired to
			# the exact same page methods either way - the crossover and
			# any outfit-focus-clearing both live inside those methods
			# themselves, not here.
			if page.color_picker_active:
				# The custom color picker takes over every key here while
				# it's open (category jump/rotate/etc. would be genuinely
				# confusing mid-pick) - Up/Down picks Hue/Saturation/
				# Value, Left/Right adjusts it (already applies live -
				# see picker_adjust()), Delete goes back to the basic-hue
				# grid (toggle_color_picker() again), Confirm/Escape/
				# Backspace all just close the whole dropdown (the value
				# is already live-applied, nothing left to "confirm").
				if event.keyval == Gdk.KEY_Up:
					page.picker_slider_prev()
				elif event.keyval == Gdk.KEY_Down:
					page.picker_slider_next()
				elif event.keyval == Gdk.KEY_Left:
					page.picker_adjust(-1)
				elif event.keyval == Gdk.KEY_Right:
					page.picker_adjust(1)
				elif event.keyval == Gdk.KEY_Delete:
					page.toggle_color_picker()
				elif event.keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter, Gdk.KEY_Escape, Gdk.KEY_BackSpace):
					page.close_dropdown()
				return True
			if event.keyval in (Gdk.KEY_Escape, Gdk.KEY_BackSpace) and page.field_expanded:
				# Closes the dropdown first, the same "the more local
				# thing closes before anything more global does" pattern
				# confirm_scrim's own Escape/Backspace-closes handling
				# already follows - not a value pick, just backing out.
				page.close_dropdown()
				return True
			if event.keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
				if page.outfit_focused:
					# Selecting a slot no longer loads it directly - it
					# offers a Change into it/Delete choice first (see
					# open_outfit_action_popup()), same as everywhere else
					# in this app a destructive action needs a real
					# confirm step. A no-op on an empty slot, unchanged.
					outfit = page.selected_outfit()
					if outfit is not None:
						self.open_outfit_action_popup(page.selected_outfit_index(), outfit)
				elif page.field_expanded:
					# A dropdown is open - Confirm closes it (picks the
					# currently-previewed value, which already applied
					# live) rather than immediately finishing the whole
					# screen, which is what Confirm means everywhere else
					# here (see the plain else branch just below).
					page.close_dropdown()
				else:
					page.confirm()
				return True
			if event.keyval == Gdk.KEY_Up:
				page.field_prev()
				return True
			if event.keyval == Gdk.KEY_Down:
				page.field_next()
				return True
			if event.keyval == Gdk.KEY_Left:
				page.outfit_prev() if page.outfit_focused else page.value_prev()
				return True
			if event.keyval == Gdk.KEY_Right:
				page.outfit_next() if page.outfit_focused else page.value_next()
				return True
			if event.keyval == Gdk.KEY_Delete and page.outfit_focused:
				page.outfit_save()
				return True
			if event.keyval == Gdk.KEY_Delete and page.field_expanded and page.field_is_color():
				# Opens the custom color picker (see toggle_color_picker())
				# - only offered for color/color_opt fields, the same way
				# outfit_save() just above only fires when outfit_focused.
				page.toggle_color_picker()
				return True
			if event.keyval == Gdk.KEY_bracketleft:
				page.category_prev()
				return True
			if event.keyval == Gdk.KEY_bracketright:
				page.category_next()
				return True
			if event.keyval == Gdk.KEY_comma:
				page.rotate_left()
				return True
			if event.keyval == Gdk.KEY_period:
				page.rotate_right()
				return True
			# WASD orbit / +- zoom / Home reset - ported straight over from
			# avatar3d_previewer.py's own standalone dev tool (same keys,
			# same behavior) per direct instruction ("add the same kb
			# controls from the previewer to this"). Keyboard-only, same as
			# that tool itself - there's no controller equivalent glyph for
			# free orbit/zoom in controller_glyphs, so unlike every other
			# control on this screen these deliberately aren't in the hint
			# row either. A/D duplicate comma/period's own heading rotate
			# rather than replacing it.
			if event.keyval in (Gdk.KEY_a, Gdk.KEY_A):
				page.rotate_left()
				return True
			if event.keyval in (Gdk.KEY_d, Gdk.KEY_D):
				page.rotate_right()
				return True
			if event.keyval in (Gdk.KEY_w, Gdk.KEY_W):
				page.orbit_up()
				return True
			if event.keyval in (Gdk.KEY_s, Gdk.KEY_S):
				page.orbit_down()
				return True
			if event.keyval in (Gdk.KEY_plus, Gdk.KEY_equal, Gdk.KEY_KP_Add):
				page.zoom_in()
				return True
			if event.keyval in (Gdk.KEY_minus, Gdk.KEY_KP_Subtract):
				page.zoom_out()
				return True
			if event.keyval == Gdk.KEY_Home:
				page.reset_camera()
				return True
			return False

		typing = typing_for_sfx

		# L1/R1 (inputd.py maps these to bracketleft/bracketright - see its
		# own comment on ACTION_BUTTON_MAP) reorder the currently selected
		# tile in whichever of the two reorderable rows (quick launch,
		# widgets) actually has focus (_focused_reorderable_flow()), only
		# on the home screen - a no-op everywhere else, including when the
		# header icon row is focused instead - nothing to reorder there.
		# Games and Entertainment used to be separate homes you could flip
		# between (switch_home(), L1+R1 on the header row) - both retired
		# entirely per direct instruction ("no more separate Entertainment
		# and Games home screens, dashboard only"); there is only ever one
		# home screen left now (see HOME_SCREEN_NAMES's own comment).
		home = SCREEN_HOMES.get(current_screen)

		# The Cosmos button (see input/inputd.py's own BTN_MODE mapping)
		# opens the full-screen Controller Help overlay - only from a home
		# screen ("the menus"), matching every real platform's own Guide/
		# Home button convention of opening a system overlay from the
		# dashboard, not from inside whatever you're actually doing.
		if home and not typing and event.keyval == Gdk.KEY_Help:
			self.show_controller_help()
			return True

		if home and not typing and event.keyval in (Gdk.KEY_bracketleft, Gdk.KEY_bracketright):
			direction = -1 if event.keyval == Gdk.KEY_bracketleft else 1
			flow = self._focused_reorderable_flow(home)
			if flow is self.quick_launch_flows[home]:
				self.reorder_selected_quick_launch(home, direction)
			elif flow is self.widget_flows[home]:
				self.reorder_selected_widget(home, direction)
			return True

		# Down/Up move between a home screen's three stacked regions
		# (header icons -> quick launch -> widgets). See
		# _move_between_menu_rows()'s own comment for why this is handled
		# explicitly here rather than through GTK's "keynav-failed" signal
		# (used to be, wasn't reliable). All three regions are single-row
		# FlowBoxes, so Down/Up can never mean "move within this row" -
		# safe to intercept unconditionally on a home screen.
		if home and not typing and event.keyval in (Gdk.KEY_Down, Gdk.KEY_Up):
			self._move_between_menu_rows(home, 1 if event.keyval == Gdk.KEY_Down else -1)
			return True

		# Left/Right within whichever of a home screen's rows currently
		# has focus - handled explicitly for the same reason Down/Up
		# already are (see _move_within_flow()'s own comment): a real
		# report that Left/Right did nothing at all on first entering the
		# menu, until some other key was pressed first, traced to
		# GtkFlowBox's own native arrow-key handling not reliably
		# engaging - the same class of bug the confirm dialog already
		# hit and fixed the same way, just for this screen's own
		# FlowBoxes instead of that one.
		#
		# The Astronar badge isn't one of _menu_row_regions()'s own Up/
		# Down stops (see that method's own comment) - it sits in the
		# SAME visual row as the header icons, so per direct correction
		# ("up? left makes more sense") it's reached by pressing Left
		# from the header icon row's own leftmost icon instead, crossing
		# over the moment _move_within_flow() there reports it's already
		# clamped at that edge (and Right from the badge crosses back).
		if home and not typing and event.keyval in (Gdk.KEY_Left, Gdk.KEY_Right):
			direction = -1 if event.keyval == Gdk.KEY_Left else 1
			flow = next(
				(r for r in (self.astronar_badge_flow, *self._menu_row_regions(home)) if r.get_style_context().has_class("row-focused")),
				None,
			)
			if flow is self.menu_icon_flows[home] and direction == -1 and not self._move_within_flow(flow, direction):
				self._focus_selected_or_first(self.astronar_badge_flow)
			elif flow is self.astronar_badge_flow and direction == 1:
				self._focus_selected_or_first(self.menu_icon_flows[home])
			elif flow is not None:
				self._move_within_flow(flow, direction)
			return True

		# X (Xbox) / Square (PlayStation) - inputd.py maps both to
		# KEY_Delete - opens the shared confirm dialog for the selected
		# tile in whichever reorderable row has focus. Only meaningful on
		# a home screen with a real entry (not an Add tile) selected;
		# a no-op otherwise.
		if home and not typing and event.keyval == Gdk.KEY_Delete:
			flow = self._focused_reorderable_flow(home)
			if flow is self.quick_launch_flows[home]:
				self.delete_selected_quick_launch(home)
			elif flow is self.widget_flows[home]:
				self.delete_selected_widget(home)
			return True

		# Left/Right switch which of the two Double Commander-style panes
		# is active, only on the file manager screen - a single-column
		# ListBox has no native use for Left/Right itself, so this is free
		# real estate rather than a conflict. See switch_fm_pane().
		if current_screen == "filemanager" and not typing and event.keyval in (Gdk.KEY_Left, Gdk.KEY_Right):
			self.switch_fm_pane(0 if event.keyval == Gdk.KEY_Left else 1)
			return True

		# X (Xbox) / Square (PlayStation) - inputd.py maps both to
		# KEY_Delete, same as the quick-launch/widget rows' own delete
		# key - opens the currently-selected item's actions menu (Copy/
		# Move/Rename/Delete, plus Open for a folder). See
		# open_fm_item_actions()'s own comment on why this exists at all.
		if current_screen == "filemanager" and not typing and event.keyval == Gdk.KEY_Delete:
			self.open_fm_item_actions(self.fm_active_pane)
			return True

		# Same X (Xbox) / Square (PlayStation) delete key, on File
		# Manager's own saved-connections list (see build_fm_connections_
		# page()) - activating a connection row there means "connect to
		# it", so removing a saved connection needs its own dedicated key
		# too, the same split as open_fm_item_actions() above.
		if current_screen == "filemanager_connections" and not typing and event.keyval == Gdk.KEY_Delete:
			self.delete_selected_fm_connection()
			return True


		# X (Xbox) / Square (PlayStation) - inputd.py maps both to
		# KEY_Delete, same delete key as everywhere else - uninstalls the
		# selected row's own add-on, gated behind the usual confirm()
		# dialog. Only meaningful on Video/Music/Photos' shared
		# "*_categories" screen, and only when the selected row is
		# actually an installed add-on (see delete_selected_addon()'s own
		# check) - a no-op otherwise, same as the home screen's own
		# delete key on a non-removable tile.
		media_type = current_screen[: -len("_categories")] if current_screen.endswith("_categories") else None
		if media_type in MEDIA_TYPE_LABELS and not typing and event.keyval == Gdk.KEY_Delete:
			self.delete_selected_addon(media_type)
			return True

		# Video/Music player: real console-native direct manipulation
		# (d-pad seeks/adjusts volume immediately) rather than tabbing
		# focus between the small on-screen buttons first - the same
		# convention every real console video app (Netflix, YouTube,
		# Kodi) already uses, and the only reliable option regardless
		# given this codebase's own repeated, confirmed finding that
		# GTK's native per-widget keynav doesn't reliably engage here
		# (see the confirm dialog's and the main menu's own comments on
		# exactly that). The on-screen buttons stay mouse/touch-
		# clickable; this is what a real gamepad/keyboard session
		# actually drives. Backspace/Escape's own existing global
		# handling already exits back out (and stops playback - see
		# go_back()'s own comment on why that had to be added).
		if current_screen == "media_player" and not typing:
			if event.keyval == Gdk.KEY_Left:
				self.player_page.seek_relative(-10000)
				return True
			if event.keyval == Gdk.KEY_Right:
				self.player_page.seek_relative(10000)
				return True
			if event.keyval == Gdk.KEY_Up:
				self.player_page.adjust_volume(10)
				return True
			if event.keyval == Gdk.KEY_Down:
				self.player_page.adjust_volume(-10)
				return True
			if event.keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
				self.player_page.toggle_play_pause()
				return True

		# Escape isn't gated on typing (unlike BackSpace here) - it needs to
		# still work with a real Gtk.Entry focused (Wi-Fi password, IGDB
		# credentials, quick-launch URL, file manager rename/new-folder),
		# the same reasoning that replaced 'q' with Escape as the quit key
		# in epic_app.py/gog_app.py: 'q' had to stay gated
		# since it's a real letter someone might be typing, but Escape
		# never collides with actual text entry.
		#
		# A no-op only on Dashboard now - the one and only home screen
		# (see HOME_SCREEN_NAMES's own comment), reached from nowhere
		# else, so there's genuinely nothing to go back to there; a stray
		# Escape quitting the launcher used to be a real risk this
		# guarded against, and a menu entry with action=quit (see
		# config.ini's own comment on it) remains the deliberate, opt-in
		# way to exit. Games/Video/Music/Photos etc. are all real drill-
		# ins from Dashboard's own header icon row now (switch_to(push_
		# history=True)), so Escape/BackSpace there pops the normal
		# history stack straight back to Dashboard, same mechanism as
		# everywhere else in this app.
		if event.keyval == Gdk.KEY_Escape:
			if current_screen != HOME_SCREEN_NAMES["dashboard"]:
				self.go_back()
			return True
		if not typing and event.keyval == Gdk.KEY_BackSpace:
			if current_screen != HOME_SCREEN_NAMES["dashboard"]:
				self.go_back()
			return True
		return False

	def quit(self):
		log.info("quit requested")
		Gtk.main_quit()

	# -- main menu ---------------------------------------------------------

	def build_dashboard_home_page(self):
		self._build_home_page("dashboard")

	def _build_home_page(self, home):
		"""CosmOS's one and only home screen (Dashboard, "menu_dashboard" -
		see HOME_SCREEN_NAMES) - a "living" dashboard, not a grid of
		tiles: every config.ini entry (that isn't utility=true) becomes a
		compact icon in the header's top-right (see make_menu_icon()),
		navigated left/right the same free way the old FlowBox grid was
		(FlowBox's own arrow-key handling), Down/Up hops between it, the
		quick-launch row, and the widget row - the widget row
		(build_widget_row()) is a set of small, addable/removable tiles
		(see dashboard_widgets.py), not a couple of massive fixed panels
		the way this used to be. This layout is deliberately only used
		for this one screen - every other screen keeps make_heading()'s
		plain title+stripe banner.

		Used to be three of these (Games/Entertainment/Dashboard, each a
		separate destination) - Games and Entertainment were both just
		flat icon grids of their own (Games: Games/Game Disc/Play
		Together; Entertainment: Video/Music/Photos), so per direct
		instruction they were retired entirely and every one of their
		icons folded directly into this one screen's own header row
		instead, rather than surviving as a tap-once-deeper destination.
		`home` is always "dashboard" now - kept as a parameter (rather
		than removed) purely so every other per-home dict this method
		feeds (self.menu_icon_flows[home], self.quick_launch_flows[home],
		...) keeps working unchanged as a single-entry dict."""
		box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=scaling.px_v(6))
		box.set_border_width(scaling.px_v(22))

		header_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=scaling.px_h(12))

		if LOGO_PATH.exists():
			try:
				logo_pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(str(LOGO_PATH), -1, LOGO_PIXEL_HEIGHT, True)
				logo_image = Gtk.Image.new_from_pixbuf(logo_pixbuf)
				logo_image.set_valign(Gtk.Align.CENTER)
				header_row.pack_start(logo_image, False, False, 0)
			except GLib.Error:
				log.warning(f"could not decode logo at {LOGO_PATH}", exc_info=True)

		header_row.pack_start(self._build_astronar_badge(), False, False, 0)

		title = Gtk.Label(label=HOME_TITLES[home], xalign=0)
		title.get_style_context().add_class("title")
		title.set_valign(Gtk.Align.CENTER)
		header_row.pack_start(title, True, True, 0)

		# Only entries that are actually about *this* home's own theme -
		# a generic entry (Settings, File Manager, ...) goes in the
		# shared utility cluster instead (see below), not either home's
		# own top row. Both rows end up the same real entry count this
		# way (3 each, as of this split) - line_count below is derived
		# from that count on both calls, not a fixed guess, so both
		# homes' rows end up with identical tile/grid sizing as a direct
		# consequence, not a coincidence that would drift if either
		# list's length ever changed independently.
		entries = [e for e in load_entries() if e["home"] == home and not e["utility"]]
		icon_flow = Gtk.FlowBox()
		self.menu_icon_flows[home] = icon_flow
		icon_flow.set_selection_mode(Gtk.SelectionMode.BROWSE)
		icon_flow.set_homogeneous(True)
		# Real gaps between the icons (a real report that 6px read as
		# cramped, same fix as the widget row's own tighter-than-it-looked
		# spacing), plus a right margin so the row doesn't run right up to
		# the screen edge - box's own 22px border_width already insets the
		# whole page, but the icon row wanted noticeably more than that on
		# its own.
		icon_flow.set_row_spacing(scaling.px_v(16))
		icon_flow.set_column_spacing(scaling.px_h(16))
		icon_flow.set_margin_end(scaling.px_h(16))
		# All entries in a single row (min == max == count) - this is a
		# header icon bar, not a wrapping grid.
		line_count = max(len(entries), 1)
		icon_flow.set_min_children_per_line(line_count)
		icon_flow.set_max_children_per_line(line_count)
		icon_flow.set_activate_on_single_click(False)
		icon_flow.set_valign(Gtk.Align.CENTER)
		icon_flow.set_halign(Gtk.Align.END)

		for entry in entries:
			icon_flow.add(make_menu_icon(entry))
		if entries:
			icon_flow.select_child(icon_flow.get_child_at_index(0))
		icon_flow.connect("child-activated", lambda _fb, child: self.activate_menu_entry(child.item))
		icon_flow.connect("set-focus-child", self.on_menu_row_focus_child)
		wire_tile_zoom(icon_flow, ICON_PIXEL_SIZE, ICON_PIXEL_SIZE_SELECTED)

		header_row.pack_end(icon_flow, False, False, 0)
		box.pack_start(header_row, False, False, 0)

		stripe = Gtk.Box()
		stripe.get_style_context().add_class("header-stripe")
		# box's own 6px spacing (shared by every child in this column) read
		# as too tight right around the stripe specifically - margins here
		# give it real breathing room from the header icons above and the
		# quick-launch row below without changing that shared spacing
		# everywhere else, same approach as the widget row's own
		# set_margin_top() below.
		stripe.set_margin_top(scaling.px_v(10))
		stripe.set_margin_bottom(scaling.px_v(14))
		box.pack_start(stripe, False, False, 0)

		box.pack_start(self.build_quick_launch_row(home), False, False, 0)
		box.pack_start(self.build_widget_row(home), False, False, 0)

		hint = controller_glyphs.make_hint_row(
			"Arrows: move   ",
			controller_glyphs.CONFIRM,
			": select   ",
			controller_glyphs.SHOULDER_LEFT,
			controller_glyphs.SHOULDER_RIGHT,
			": reorder / switch home   ",
			controller_glyphs.DELETE,
			": remove tile   ",
			controller_glyphs.COSMOS,
			": controller help",
		)
		# make_hint_row()'s box defaults to Gtk.Align.FILL, which stretches
		# it to the page's full width and leaves its own left-anchored
		# children looking flush against the left edge - centering the row
		# itself (not its individual labels) is what actually centers the
		# whole hint as a single group.
		hint.set_halign(Gtk.Align.CENTER)
		hint.set_hexpand(True)
		# valign=CENTER too - a real, confirmed-live bug: FILL is the
		# default on both axes, and when hint used to share one horizontal
		# row with the (much taller) utility cluster, that row's own
		# cross-axis allocation stretched every non-opted-out child to its
		# full height - including every button-badge label inside hint,
		# whose border-radius: 999px then drew a full vertical capsule
		# instead of the small round/trigger badge shape theme.css
		# actually defines. hint has its own row now (see bottom_row
		# below), but this still doesn't hurt to keep.
		hint.set_valign(Gtk.Align.CENTER)

		# Generic, not-really-either-home entries (Settings, File
		# Manager, ...) - the SAME real list on both homes (filtered by
		# "utility" alone, not "home"), each home just builds its own
		# separate-but-identical FlowBox instance from it, the same way
		# quick_launch/widgets are already separate per-home instances
		# of a conceptually shared idea. Tucked into the bottom-right
		# corner - deliberately de-emphasized by position, not by a
		# smaller tile size (still make_menu_icon()'s own real tiles),
		# so it still reads as "the same app", just not this home's own
		# main business.
		utility_entries = [e for e in load_entries() if e["utility"]]
		utility_flow = Gtk.FlowBox()
		self.utility_icon_flows[home] = utility_flow
		utility_flow.set_selection_mode(Gtk.SelectionMode.BROWSE)
		utility_flow.set_homogeneous(True)
		utility_flow.set_row_spacing(scaling.px_v(16))
		utility_flow.set_column_spacing(scaling.px_h(16))
		utility_line_count = max(len(utility_entries), 1)
		utility_flow.set_min_children_per_line(utility_line_count)
		utility_flow.set_max_children_per_line(utility_line_count)
		utility_flow.set_activate_on_single_click(False)
		utility_flow.set_valign(Gtk.Align.CENTER)
		utility_flow.set_halign(Gtk.Align.END)
		for entry in utility_entries:
			child = make_menu_icon(entry)
			# .utility-icon-tile (theme.css) drops the gradient border the
			# same tiles get everywhere else - direct feedback that a
			# bordered box here still read as competing with the two real
			# home-specific rows above it, when the whole point of this
			# cluster is to read as a quiet, secondary shared toolbar.
			# Focus/selection is still legible without it - the .row-
			# focused selected-label glow (theme.css) is scoped to the
			# label, not the tile's own border, so it's untouched by this.
			child.get_child().get_style_context().add_class("utility-icon-tile")
			utility_flow.add(child)
		if utility_entries:
			utility_flow.select_child(utility_flow.get_child_at_index(0))
		wire_tile_zoom(utility_flow, ICON_PIXEL_SIZE, ICON_PIXEL_SIZE_SELECTED)
		utility_flow.connect("child-activated", lambda _fb, child: self.activate_menu_entry(child.item))
		utility_flow.connect("set-focus-child", self.on_menu_row_focus_child)

		# hint and utility_flow used to share ONE horizontal row (hint
		# centered in whatever space was left after utility_flow's own
		# natural width) - a real, confirmed-live bug once the base font
		# size grew (see theme.css's own base font-size comment): hint's
		# own natural width grew right along with it, past what was left
		# next to utility_flow, so the two visibly overlapped/clipped
		# instead of both rendering in full. Stacked vertically instead -
		# each gets the page's full width to itself, so there's no shared
		# horizontal space left to run out of.
		bottom_row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
		bottom_row.set_margin_top(scaling.px_v(10))
		bottom_row.pack_start(hint, False, False, 0)
		utility_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
		utility_row.set_margin_top(scaling.px_v(10))
		utility_row.pack_end(utility_flow, False, False, 0)
		bottom_row.pack_start(utility_row, False, False, 0)
		# pack_end, not pack_start - with the widget row now a compact set
		# of small cards instead of the old vexpand-filled dashboard, there
		# was nothing left claiming the rest of the page's height, so a
		# pack_start bottom row sat right under the widget row instead of
		# anchored to the bottom like every other screen's footer hint.
		# pack_end docks it to the bottom of the page's allocated height
		# regardless of how little the rest of the page uses, without
		# needing an explicit filler widget to eat the leftover space.
		box.pack_end(bottom_row, False, False, 0)

		# The specific child, not the FlowBox container - confirmed the
		# hard way (here and in confirm()) that FlowBox.grab_focus()
		# doesn't reliably delegate down to whichever child is selected.
		# Grabbing the container directly used to mean icon_flow's own
		# "set-focus-child" (see on_menu_row_focus_child) never fired on
		# first entering this page at all, leaving the quick-launch row's
		# own last-focused child - whatever GTK's default fallback focus
		# landed on - stuck showing the .row-focused highlight instead.
		box.focus_widget = icon_flow.get_child_at_index(0) if entries else icon_flow
		box.show_all()
		self.stack.add_named(box, HOME_SCREEN_NAMES[home])

	def build_quick_launch_row(self, home):
		"""A single-row FlowBox of user-added website shortcuts (see
		quick_launch.py), plus a trailing "Add" tile - sits between the
		header stripe and the RetroAchievements/news dashboard, one per
		home screen. Rebuilt from scratch on every call rather than
		diffed in place (same approach as the RA/news cards) since add/
		remove/reorder are all infrequent, deliberate actions, not
		something needing a fast incremental update path."""
		flow = Gtk.FlowBox()
		self.quick_launch_flows[home] = flow
		flow.set_selection_mode(Gtk.SelectionMode.BROWSE)
		flow.set_homogeneous(True)
		flow.set_row_spacing(scaling.px_v(6))
		flow.set_column_spacing(scaling.px_h(6))
		flow.set_activate_on_single_click(False)
		flow.set_valign(Gtk.Align.CENTER)
		# set_vexpand(False) - a real, confirmed-live bug found testing a
		# genuine portrait target (an arcade cabinet's own tall, narrow
		# aspect - see scaling.is_portrait()): GtkFlowBox silently claims
		# vertical expansion on its own regardless of this row's own
		# pack_start(..., False, False, 0) into the page's outer box, even
		# though that's normally exactly what expand=False in a pack_start
		# call is supposed to prevent. Confirmed by adding a temporary
		# bright debug background to every flowbox (theme.css's own base
		# `flowbox {}` rule) and measuring the real rendered bands: three
		# separate large gaps, one before EACH of this row, the big-tiles
		# row, and the widget row - not the one single, intentional gap the
		# page's own pack_end()'d footer already accounts for (see
		# box.pack_end(bottom_row, ...) below). Explicit set_vexpand(False)
		# on all three is what actually stops each one from silently eating
		# a share of the page's own leftover height - valign=CENTER alone
		# was never the problem, it only matters once there's real extra
		# space to center within.
		flow.set_vexpand(False)
		flow.set_halign(Gtk.Align.START)
		flow.connect("child-activated", lambda _fb, child, h=home: self.activate_quick_launch(h, child))
		flow.connect("set-focus-child", self.on_menu_row_focus_child)
		wire_tile_zoom(flow, ICON_PIXEL_SIZE, ICON_PIXEL_SIZE_SELECTED)
		self._populate_quick_launch_row(home)
		return flow

	def on_menu_row_focus_child(self, container, child):
		"""The home screen stacks several independently-focusable regions
		(the header icon bar, the quick-launch row, the widget row, the
		shared bottom-right utility cluster - see build_widget_row()/
		_build_home_page()); every one of these FlowBoxes independently
		always keeps one child "selected" (Gtk.SelectionMode.BROWSE) even
		when none of them is the one actually receiving keyboard input -
		theme.css's .row-focused class scopes the real highlight to
		whichever FlowBox actually has focus, tracked here via each
		region's own "set-focus-child" signal, rather than CSS
		:focus/:focus-within, both tried first and confirmed (by
		screenshotting an isolated repro, not just reasoning about it) to
		not reliably reflect real focus state for this exact widget
		combination: :focus-within never toggled at all, and :focus left
		a stale glow on whichever child was the window's original
		default-focused widget even after focus visibly moved elsewhere.

		Also doubles as the lookup _focused_reorderable_flow() uses to
		figure out which of quick_launch_flows[home]/widget_flows[home]
		the L1/R1/Delete keys in on_key() should act on - the
		.row-focused class this sets IS that answer, not a separate piece
		of state."""
		if child is None:
			return
		for flow in self._all_menu_flows():
			if flow is container:
				flow.get_style_context().add_class("row-focused")
			else:
				flow.get_style_context().remove_class("row-focused")

	def _all_menu_flows(self):
		return (
			[self.astronar_badge_flow]
			+ list(self.menu_icon_flows.values())
			+ list(self.quick_launch_flows.values())
			+ list(self.widget_flows.values())
			+ list(self.utility_icon_flows.values())
		)

	def _focused_reorderable_flow(self, home):
		"""Which of home's two reorderable/removable rows (quick launch,
		widgets) currently owns keyboard focus - see on_key()'s L1/R1/
		Delete handling. None if neither does (header icons focused, or
		some other screen entirely - though callers only ask this while
		on a home screen)."""
		for flow in (self.quick_launch_flows[home], self.widget_flows[home]):
			if flow.get_style_context().has_class("row-focused"):
				return flow
		return None

	# Up/Down between the home screen's stacked regions (header icons ->
	# quick launch -> widgets -> the shared bottom-right utility cluster)
	# isn't something GTK gives for free. This used to be wired through
	# each FlowBox's own "keynav-failed" signal (emitted when arrow-key
	# movement can't go any further within a widget) - dropped after a
	# real report ("no navigation") traced to it not reliably firing:
	# focus sometimes moved between regions anyway (via some other GTK-
	# internal fallback), just not landing on the child that was actually
	# selected, since that fallback path never ran the "which child"
	# logic below at all. Every region is a single-row FlowBox, so
	# Down/Up can never mean "move within this row" - on_key() below now
	# owns the whole transition explicitly instead of trusting
	# keynav-failed to hand off to it. Left/Right stays free within a
	# single row, handled natively.
	def _menu_row_regions(self, home):
		# The Astronar badge is deliberately NOT one of these Up/Down
		# stops - see on_key()'s own Left/Right handling for why (it's
		# reached from the header icon row's own leftmost icon instead,
		# per direct correction "up? left makes more sense").
		return [
			self.menu_icon_flows[home],
			self.quick_launch_flows[home],
			self.widget_flows[home],
			self.utility_icon_flows[home],
		]

	def _move_between_menu_rows(self, home, direction):
		regions = self._menu_row_regions(home)
		current_index = next((i for i, r in enumerate(regions) if r.get_style_context().has_class("row-focused")), 0)
		target_index = current_index + direction
		if not (0 <= target_index < len(regions)):
			return False
		self._focus_selected_or_first(regions[target_index])
		return True

	def _focus_selected_or_first(self, container):
		"""FlowBox and ListBox expose their "currently selected" child
		through different methods (get_selected_children() returns a
		list, get_selected_row() returns one row-or-None) - this hides
		that so _move_between_menu_rows() doesn't need to know which kind
		of container it's handing focus to."""
		if isinstance(container, Gtk.ListBox):
			child = container.get_selected_row() or container.get_row_at_index(0)
		else:
			selected = container.get_selected_children()
			child = selected[0] if selected else container.get_child_at_index(0)
		if child:
			child.grab_focus()

	def _move_within_flow(self, flow, direction):
		"""Left/Right movement within one of a home screen's FlowBox rows -
		handled explicitly here rather than left to GtkFlowBox's own
		native arrow-key binding, the same reasoning (and the same fix)
		already applied to the confirm dialog (see on_key()'s own comment
		on it): trusting a FlowBox to handle its own Left/Right natively
		doesn't reliably work in this app - confirmed directly, a real
		report that Left/Right did nothing at all the moment the menu was
		first entered, until some other key (Down/Up, which are ALSO
		handled explicitly rather than natively) was pressed first, after
		which Left/Right started working - i.e. GTK's own native handling
		only sometimes engages, not something to depend on. Clamps at
		either edge rather than wrapping, matching GtkFlowBox's own
		default (non-wrapping) arrow-key behavior. Returns whether it
		actually moved - False (clamped, already at that edge) is what
		on_key()'s own Left/Right handling uses to cross over into the
		Astronar badge from the header icon row's own leftmost icon."""
		n = len(flow.get_children())
		if n == 0:
			return False
		selected = flow.get_selected_children()
		current_index = 0
		if selected:
			for i in range(n):
				if flow.get_child_at_index(i) is selected[0]:
					current_index = i
					break
		target_index = current_index + direction
		if not (0 <= target_index < n):
			return False
		child = flow.get_child_at_index(target_index)
		flow.select_child(child)
		child.grab_focus()
		return True

	def _populate_quick_launch_row(self, home, select_name=None):
		flow = self.quick_launch_flows[home]
		for child in flow.get_children():
			flow.remove(child)

		entries = quick_launch.list_entries()
		line_count = len(entries) + 1  # +1 for the trailing Add tile
		flow.set_min_children_per_line(line_count)
		flow.set_max_children_per_line(line_count)

		select_index = 0
		for i, entry in enumerate(entries):
			flow.add(make_quick_launch_tile(entry))
			if select_name and entry["name"] == select_name:
				select_index = i
		flow.add(make_quick_launch_tile(None))  # the Add tile, always last
		flow.show_all()
		selected_child = flow.get_child_at_index(select_index)
		flow.select_child(selected_child)
		# select_child() only sets *visual* BROWSE selection - it does not
		# update the FlowBox's own internal "focus child" bookkeeping, and
		# a real report (Delete on the quick-launch row deleting the
		# wrong/no entry) traced back to exactly that gap: entering this
		# row from the header icons above landed real keyboard focus on
		# whichever child GTK remembered from construction (the Add tile,
		# added last) rather than the one actually selected here, bypassing
		# _move_between_menu_rows()/_focus_selected_or_first() entirely
		# rather than going through it. idle_add because grabbing focus
		# during construction (before this window is realized) doesn't
		# reliably take - same reason switch_to()'s own focus grab needs
		# it.
		GLib.idle_add(selected_child.grab_focus)

	def activate_quick_launch(self, home, child):
		"""Both the "Add" tile and every real entry are real web
		buttons - both gated on a real connectivity check (per direct
		instruction covering Games' and Entertainment's own Quick Launch
		rows specifically), not just switch_to("quick_launch_add") the
		way this used to work: adding one needs network to fetch its
		title/favicon (see quick_launch.add_entry()), same as opening an
		existing one needs it to actually load the page."""
		entry = child.entry
		if entry is None:

			def open_add():
				self.quick_launch_add_status.set_text("")
				self.quick_launch_add_entry.set_text("")
				self.quick_launch_add_home = home
				self.switch_to("quick_launch_add")

			self.require_online(open_add, "Adding a Quick Launch link needs an internet connection - check your Wi-Fi and try again.")
			return

		def open_url():
			log.info(f"quick-launch: opening {entry['name']} ({entry['url']})")
			self.launch_subprocess({"mode": "graphical", "command": ["python3", KIOSK_LAUNCHER_PATH, entry["url"]]})

		self.require_online(open_url, f"{entry['name']} needs an internet connection - check your Wi-Fi and try again.")

	def reorder_selected_quick_launch(self, home, direction):
		selected = self.quick_launch_flows[home].get_selected_children()
		if not selected or selected[0].entry is None:
			return  # nothing selected, or the Add tile can't be reordered
		name = selected[0].entry["name"]
		quick_launch.move_entry(name, direction)
		self._populate_quick_launch_row(home, select_name=name)

	def delete_selected_quick_launch(self, home):
		selected = self.quick_launch_flows[home].get_selected_children()
		if not selected or selected[0].entry is None:
			return  # nothing selected, or the Add tile can't be deleted
		entry = selected[0].entry
		self.confirm(
			"Remove this?",
			f"Remove {entry['name']} from quick launch? You can always add it again.",
			lambda: self._do_quick_launch_delete(home, entry["name"]),
		)

	def _do_quick_launch_delete(self, home, name):
		quick_launch.remove_entry(name)
		self._populate_quick_launch_row(home)

	def build_widget_row(self, home):
		"""A single-row FlowBox of the dashboard widgets the user's actually
		added (see dashboard_widgets.py), plus a trailing "Add Widget"
		tile - exactly build_quick_launch_row()'s own shape (same add/
		reorder/remove pattern, wired through the same on_key() handlers
		via _focused_reorderable_flow()), one per home screen. Starts
		empty on a fresh install; RetroAchievements and Gaming News are
		just the first two entries in dashboard_widgets.WIDGET_TYPES,
		opted into like any other widget.

		Each tile (make_widget_tile()) is a small title + one-line summary,
		not the old full-height card that used to show a scrolling list
		inline - that's what made the dashboard "massive" before. Real
		content lives on each widget's own full page instead
		(WIDGET_PAGES), reached by activating its tile (open_widget())."""
		flow = Gtk.FlowBox()
		self.widget_flows[home] = flow
		flow.set_selection_mode(Gtk.SelectionMode.BROWSE)
		flow.set_homogeneous(True)
		# Wider gutters than the small icon-tile rows above (6px) - these
		# are real cards, not compact icon buttons, and read as a cramped
		# strip rather than separate cards without real air between them.
		flow.set_row_spacing(scaling.px_v(24))
		flow.set_column_spacing(scaling.px_h(24))
		# _build_home_page()'s own box spacing (6px, shared by every child
		# in that column) reads fine between the small header/quick-launch
		# rows above, but was too tight once this row became full-size
		# cards - a top margin here (rather than bumping that shared
		# spacing, which would also crowd the small rows above it) is
		# scoped to just this one gap.
		flow.set_margin_top(scaling.px_v(18))
		flow.set_activate_on_single_click(False)
		flow.set_valign(Gtk.Align.CENTER)
		flow.set_vexpand(False)  # see build_quick_launch_row()'s own comment on this
		flow.set_halign(Gtk.Align.START)
		flow.connect("child-activated", lambda _fb, child, h=home: self.open_widget(h, child))
		flow.connect("set-focus-child", self.on_menu_row_focus_child)
		self._populate_widget_row(home)
		return flow

	def _populate_widget_row(self, home, select_id=None):
		flow = self.widget_flows[home]
		for child in flow.get_children():
			flow.remove(child)

		active = dashboard_widgets.list_active()
		line_count = len(active) + 1  # +1 for the trailing Add tile
		flow.set_min_children_per_line(line_count)
		flow.set_max_children_per_line(line_count)

		select_index = 0
		for i, widget_id in enumerate(active):
			summary = self.widget_summaries.get(widget_id, "Loading...")
			flow.add(make_widget_tile(widget_id, summary))
			if select_id and widget_id == select_id:
				select_index = i
		flow.add(make_widget_tile(None, None))  # the Add tile, always last
		flow.show_all()
		selected_child = flow.get_child_at_index(select_index)
		flow.select_child(selected_child)
		# select_child() alone isn't enough - see _populate_quick_launch_row()'s
		# own comment on exactly this gap (a real report traced to the
		# sibling quick-launch row, same construction shape here).
		GLib.idle_add(selected_child.grab_focus)

	def open_widget(self, home, child):
		if child.widget_id is None:
			self.widget_add_home = home
			self.switch_to("widget_add")
			return
		self.switch_to(WIDGET_PAGES[child.widget_id])

	def reorder_selected_widget(self, home, direction):
		selected = self.widget_flows[home].get_selected_children()
		if not selected or selected[0].widget_id is None:
			return  # nothing selected, or the Add tile can't be reordered
		widget_id = selected[0].widget_id
		dashboard_widgets.move_widget(widget_id, direction)
		self._populate_widget_row(home, select_id=widget_id)

	def delete_selected_widget(self, home):
		selected = self.widget_flows[home].get_selected_children()
		if not selected or selected[0].widget_id is None:
			return  # nothing selected, or the Add tile can't be deleted
		widget_id = selected[0].widget_id
		self.confirm(
			"Remove this?",
			f"Remove {dashboard_widgets.label_of(widget_id)} from the dashboard? You can always add it again.",
			lambda: self._do_widget_delete(home, widget_id),
		)

	def _do_widget_delete(self, home, widget_id):
		dashboard_widgets.remove_widget(widget_id)
		self._populate_widget_row(home)

	def build_widget_add_page(self):
		"""Not make_list_page() - its items list (and the "Up/Down: move"
		vs. empty_text hint it bakes from that) is fixed at construction
		time, but which widgets are available here changes as the user
		adds/removes them, so this needs its own dynamically-rebuildable
		hint label the way refresh_fm_list()'s status line is its own
		widget too, not part of a reused static page shape."""
		box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=scaling.px_v(6))
		box.set_border_width(scaling.px_v(22))
		box.pack_start(make_heading("Add Widget"), False, False, 0)

		scroller = Gtk.ScrolledWindow()
		scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
		scroller.set_can_focus(False)
		self.widget_add_listbox = Gtk.ListBox()
		self.widget_add_listbox.set_selection_mode(Gtk.SelectionMode.BROWSE)
		install_carets(self.widget_add_listbox)
		self.widget_add_listbox.connect("row-activated", lambda _lb, row: self.submit_widget_add(row.item))
		scroller.add(self.widget_add_listbox)
		box.pack_start(scroller, True, True, 0)

		self.widget_add_hint = Gtk.Label(xalign=0)
		self.widget_add_hint.get_style_context().add_class("dim-label")
		box.pack_start(self.widget_add_hint, False, False, 0)

		box.listbox = self.widget_add_listbox
		self.stack.add_named(box, "widget_add")

	def refresh_widget_add_list(self):
		for row in list(self.widget_add_listbox.get_children()):
			self.widget_add_listbox.remove(row)
		available = dashboard_widgets.list_available()
		for widget_id in available:
			self.widget_add_listbox.add(make_row(widget_id, dashboard_widgets.label_of(widget_id)))
		self.widget_add_listbox.show_all()
		if available:
			self.widget_add_listbox.select_row(self.widget_add_listbox.get_row_at_index(0))
			self.widget_add_hint.set_text("Up/Down: move   Enter: add   Backspace: back")
		else:
			self.widget_add_hint.set_text("All widgets already added   Backspace: back")

	def submit_widget_add(self, widget_id):
		home = self.widget_add_home
		dashboard_widgets.add_widget(widget_id)
		self.go_back()
		self._populate_widget_row(home, select_id=widget_id)
		# go_back() (unlike switch_to()) doesn't fire on_enter_handlers, so
		# the newly-added widget's tile would otherwise sit on "Loading..."
		# until the next full menu visit - fetch its data directly instead,
		# same as on_quick_launch_add_done() has to manually re-populate
		# the quick-launch row rather than relying on a handler.
		if widget_id == "retroachievements":
			self.run_async(retroachievements.fetch_summary, self.on_ra_summary_done)
		elif widget_id == "gamenews":
			self.run_async(gamenews.fetch_headlines, self.on_news_done)
		elif widget_id == "cosmochat":
			self.refresh_cosmochat_state()
		elif widget_id == "clock":
			self.refresh_clock_widget()
		elif widget_id == "profile_summary":
			self.refresh_profile_summary_widget()

	def _update_widget_tile_summary(self, widget_id, text):
		self.widget_summaries[widget_id] = text
		for flow in self.widget_flows.values():
			for child in flow.get_children():
				if getattr(child, "widget_id", None) == widget_id:
					if hasattr(child, "summary_label"):
						child.summary_label.set_text(text)
					return

	def build_retroachievements_page(self):
		"""The RetroAchievements widget's full content - a profile-summary
		header and recent-achievements list, plain non-interactive labels
		(same as before this became a widget), just on its own page now
		instead of squeezed into a tile-sized dashboard card."""
		box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=scaling.px_v(6))
		box.set_border_width(scaling.px_v(22))
		box.pack_start(make_heading("RetroAchievements"), False, False, 0)

		scroller = Gtk.ScrolledWindow()
		scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
		self.ra_body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=scaling.px_v(4))
		scroller.add(self.ra_body)
		box.pack_start(scroller, True, True, 0)

		hint = Gtk.Label(label="Backspace: back")
		hint.get_style_context().add_class("dim-label")
		box.pack_start(hint, False, False, 0)

		self._set_dashboard_placeholder(self.ra_body, "Loading...")
		box.show_all()
		self.stack.add_named(box, "retroachievements_full")

	def build_gaming_news_page(self):
		"""The Gaming News widget's full content - a real Gtk.ListBox
		(make_news_row()/install_carets(), same shape as every other
		navigable list in this app), on its own page instead of squeezed
		into a tile-sized dashboard card."""
		box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=scaling.px_v(6))
		box.set_border_width(scaling.px_v(22))
		box.pack_start(make_heading("Gaming News"), False, False, 0)

		scroller = Gtk.ScrolledWindow()
		scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
		scroller.set_can_focus(False)
		self.news_listbox = Gtk.ListBox()
		self.news_listbox.set_selection_mode(Gtk.SelectionMode.BROWSE)
		install_carets(self.news_listbox)
		self.news_listbox.connect("row-activated", lambda _lb, row: self.open_news_article(row.item))
		scroller.add(self.news_listbox)
		box.pack_start(scroller, True, True, 0)

		hint = Gtk.Label(label="Up/Down: move   Enter: open   Backspace: back")
		hint.get_style_context().add_class("dim-label")
		box.pack_start(hint, False, False, 0)

		box.listbox = self.news_listbox
		self._set_news_placeholder("Loading...")
		box.show_all()
		self.stack.add_named(box, "gaming_news")

	def build_clock_widget_page(self):
		"""The Clock widget's full content - built specifically for
		Dashboard (see dashboard_widgets.py's own docstring on "dash"-only
		widgets), not a relocated Games/Entertainment widget. Local/instant
		like profile_summary below - refreshed on every visit to Dashboard
		(see refresh_dashboard()), not a live per-second ticker, same
		"cheap enough to just rebuild on enter" reasoning already used for
		lazy pages like usb_actions/disc."""
		box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=scaling.px_v(6))
		box.set_border_width(scaling.px_v(22))
		box.pack_start(make_heading("Clock"), False, False, 0)

		self.clock_time_label = Gtk.Label(xalign=0)
		self.clock_time_label.get_style_context().add_class("title")
		box.pack_start(self.clock_time_label, False, False, 0)

		self.clock_date_label = Gtk.Label(xalign=0)
		self.clock_date_label.get_style_context().add_class("dim-label")
		box.pack_start(self.clock_date_label, False, False, 0)

		hint = Gtk.Label(label="Backspace: back")
		hint.get_style_context().add_class("dim-label")
		box.pack_start(hint, True, False, 0)

		box.show_all()
		self.stack.add_named(box, "clock_full")

	def refresh_clock_widget(self):
		"""Shared by refresh_dashboard() (on-enter) and submit_widget_add()
		(right after adding it) - see _update_widget_tile_summary()'s own
		"don't leave a freshly-added tile on 'Loading...'" reasoning."""
		now = datetime.datetime.now()
		time_text = now.strftime("%I:%M %p").lstrip("0")
		date_text = now.strftime("%A, %B %-d")
		self.clock_time_label.set_text(time_text)
		self.clock_date_label.set_text(date_text)
		self._update_widget_tile_summary("clock", f"{time_text} · {date_text}")

	def build_profile_summary_widget_page(self):
		"""The Profile widget's full content - built specifically for
		Dashboard, same reasoning as build_clock_widget_page() above.
		Reuses avatar3d_portrait_anim.bind() exactly as
		refresh_astronar_badges() already does for the active profile's
		own animated picture."""
		box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=scaling.px_v(6))
		box.set_border_width(scaling.px_v(22))
		box.pack_start(make_heading("Profile"), False, False, 0)

		self.profile_summary_image = Gtk.Image()
		self.profile_summary_image.set_halign(Gtk.Align.START)
		box.pack_start(self.profile_summary_image, False, False, 0)

		self.profile_summary_name_label = Gtk.Label(xalign=0)
		self.profile_summary_name_label.get_style_context().add_class("title")
		box.pack_start(self.profile_summary_name_label, False, False, 0)

		hint = Gtk.Label(label="Backspace: back")
		hint.get_style_context().add_class("dim-label")
		box.pack_start(hint, True, False, 0)

		box.show_all()
		self.stack.add_named(box, "profile_summary_full")

	PROFILE_SUMMARY_PICTURE_SIZE = scaling.px(96)

	def refresh_profile_summary_widget(self):
		if self._profile_summary_anim_unbind is not None:
			self._profile_summary_anim_unbind()
			self._profile_summary_anim_unbind = None
		active = profiles.get_active_profile()
		if active is None:
			self.profile_summary_image.clear()
			self._update_widget_tile_summary("profile_summary", "No profile")
			return
		traits = _avatar_traits_or_default(active)
		_render_at_size, self._profile_summary_anim_unbind = avatar3d_portrait_anim.bind(
			self.profile_summary_image, traits, self.PROFILE_SUMMARY_PICTURE_SIZE
		)
		self.profile_summary_name_label.set_text(active)
		self._update_widget_tile_summary("profile_summary", active)

	def _build_astronar_badge(self):
		"""Just the active profile's own Astronar picture - built directly
		into _build_home_page()'s own header row, always present. A
		real, activatable region (a single-child FlowBox, not a plain
		Gtk.Button - see this method's earlier version/comment history
		for why a Button doesn't fit this app's own Down/Up/Left/Right
		region-based navigation model), wired so selecting it is how you
		change the active profile's own Astronar (open_avatar_picker()),
		not through the profile picker at all.

		Per direct correction, this is deliberately just the picture -
		no name label, no background tile/card the way every other icon
		in this app gets - only a focus ring (.astronar-badge-image in
		theme.css) that appears around the picture itself once it's
		actually focused, so at rest it reads as nothing more than a
		portrait sitting in the header.

		Appends to astronar_badge_images (see its own init comment)
		rather than assuming a single instance - a holdover from when
		this was called once per home screen (there was more than one);
		refresh_astronar_badges() updates every instance together
		regardless of how many there are."""
		flow = Gtk.FlowBox()
		self.astronar_badge_flow = flow
		flow.get_style_context().add_class("astronar-badge-flow")
		flow.set_selection_mode(Gtk.SelectionMode.BROWSE)
		flow.set_activate_on_single_click(False)
		flow.set_valign(Gtk.Align.CENTER)
		flow.set_halign(Gtk.Align.START)
		flow.set_min_children_per_line(1)
		flow.set_max_children_per_line(1)
		flow.connect("child-activated", lambda _fb, _child: self.open_avatar_picker(profiles.get_active_profile()))
		flow.connect("set-focus-child", self.on_menu_row_focus_child)
		# No wire_tile_zoom() here, unlike every other tile grid in this
		# app - this tile's own portrait always renders at a fixed
		# ASTRONAR_BADGE_SIZE via avatar3d_portrait_anim.bind() (see
		# refresh_astronar_badges()), not a size that changes on focus,
		# so there's nothing for a zoom effect to actually do. It still
		# gets the .row-focused focus-ring treatment below from
		# on_menu_row_focus_child() alone.

		child = Gtk.FlowBoxChild()
		# A thin Box wrapper, not the Gtk.Image directly - the focus ring
		# below is painted via Cairo on this Box (see
		# _draw_astronar_badge_ring()), not CSS: a real, confirmed-live
		# GTK3 quirk defeated every CSS attempt at this (outline-style/
		# border/background resets) - GtkFlowBoxChild's own native
		# keyboard-focus rendering kept painting a plain gray frame the
		# moment this tile actually had real focus, regardless of what
		# theme.css said to do instead. Painting our own ring on top
		# (connect_after "draw", so this runs after GTK's own default
		# rendering) sidesteps that fight entirely rather than continuing
		# to chase it in CSS.
		cell = Gtk.Box()
		cell.connect_after("draw", self._draw_astronar_badge_ring)
		image = Gtk.Image()
		self.astronar_badge_images.append(image)
		cell.pack_start(image, False, False, 0)
		child.add(cell)
		flow.add(child)
		flow.select_child(child)
		return flow

	def _draw_astronar_badge_ring(self, widget, cr):
		"""Paints the badge's own focus ring directly, rather than
		trusting CSS to draw it (see _build_astronar_badge()'s own
		comment on why). Reads self.astronar_badge_flow's ".row-focused"
		class fresh on every draw rather than tracking a separate bit of
		state - that class is already the single source of truth
		on_menu_row_focus_child() maintains for every other region in
		this app, no reason to duplicate it here."""
		if not self.astronar_badge_flow.get_style_context().has_class("row-focused"):
			return False
		width = widget.get_allocated_width()
		height = widget.get_allocated_height()
		line_width = scaling.px(4)
		radius = scaling.px(14)
		x, y = line_width / 2, line_width / 2
		w, h = width - line_width, height - line_width
		cr.save()
		cr.set_line_width(line_width)
		cr.set_source_rgba(1.0, 0.0, 0.565, 0.9)  # theme.css's own @accent_2 (#ff0090)
		cr.new_sub_path()
		cr.arc(x + w - radius, y + radius, radius, -math.pi / 2, 0)
		cr.arc(x + w - radius, y + h - radius, radius, 0, math.pi / 2)
		cr.arc(x + radius, y + h - radius, radius, math.pi / 2, math.pi)
		cr.arc(x + radius, y + radius, radius, math.pi, 3 * math.pi / 2)
		cr.close_path()
		cr.stroke()
		cr.restore()
		return False

	def refresh_astronar_badges(self):
		"""Called once at startup (after the dashboard page exists) and
		again any time the *active* profile's own Astronar picture
		changes (see confirm_avatar_traits()) - a profile switch itself
		restarts the whole app (see activate_profile()'s own docstring),
		which rebuilds these from scratch anyway, so this never needs to
		handle that case."""
		for unbind in self._astronar_badge_anim_unbinds:
			unbind()
		self._astronar_badge_anim_unbinds = []
		active = profiles.get_active_profile()
		if active is None:
			for image in self.astronar_badge_images:
				image.clear()
			return
		traits = _avatar_traits_or_default(active)
		for image in self.astronar_badge_images:
			_render_at_size, unbind = avatar3d_portrait_anim.bind(image, traits, ASTRONAR_BADGE_SIZE)
			self._astronar_badge_anim_unbinds.append(unbind)

	def build_cosmochat_page(self):
		"""The Cosmochat widget's full content - real Telegram messages
		relayed by the background listener (notifications/
		telegram_listener.py, a systemd --user service) into the active
		profile's own cosmochat_messages.json, shown as a real Gtk.ListBox
		(make_cosmochat_row()/install_carets(), same shape as every other
		navigable list in this app). Purely informational once logged in -
		no click-through action on a real message row - but the *empty*
		state doubles as the setup entry point: activating it walks
		through entering API credentials and then the QR login, each on
		their own page (see refresh_cosmochat_state()/
		_on_cosmochat_row_activated())."""
		box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=scaling.px_v(6))
		box.set_border_width(scaling.px_v(22))
		box.pack_start(make_heading("Cosmochat"), False, False, 0)

		scroller = Gtk.ScrolledWindow()
		scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
		scroller.set_can_focus(False)
		self.cosmochat_listbox = Gtk.ListBox()
		self.cosmochat_listbox.set_selection_mode(Gtk.SelectionMode.BROWSE)
		install_carets(self.cosmochat_listbox)
		self.cosmochat_listbox.connect("row-activated", lambda _lb, row: self._on_cosmochat_row_activated(row))
		scroller.add(self.cosmochat_listbox)
		box.pack_start(scroller, True, True, 0)

		self.cosmochat_hint = Gtk.Label(label="Up/Down: move   Backspace: back")
		self.cosmochat_hint.get_style_context().add_class("dim-label")
		box.pack_start(self.cosmochat_hint, False, False, 0)

		box.listbox = self.cosmochat_listbox
		box.show_all()
		self.stack.add_named(box, "cosmochat_full")

		# Not scoped to page-enter/leave the way most fetches are - a fixed
		# tick registered once, matching build_usb_page()'s own
		# USB_REFRESH_MS precedent, so a message that arrives while you're
		# already looking at the dashboard or this page shows up within a
		# few seconds rather than only on next navigation.
		GLib.timeout_add_seconds(COSMOCHAT_REFRESH_SECONDS, self._cosmochat_tick)

	def _cosmochat_tick(self):
		if self.stack.get_visible_child_name() in ("menu_dashboard", "cosmochat_full"):
			self.refresh_cosmochat_state()
		return True

	def refresh_cosmochat_state(self):
		"""What both on_enter_handlers["cosmochat_full"] and the periodic
		tick above call - re-checks setup/login state fresh every time
		(unlike a plain data fetch, this can change between visits: the
		user might have just entered API credentials or completed a QR
		login on a different page) and either prompts the next setup step
		or fetches real messages."""
		if not telegram_credentials.load()["api_id"]:
			self._set_cosmochat_prompt("Not set up yet", "setup_credentials")
		elif not telegram_client.is_logged_in():
			self._set_cosmochat_prompt("Not logged in yet", "login")
		else:
			self.run_async(telegram_listener.list_recent, self.on_cosmochat_done)

	def _set_cosmochat_prompt(self, text, action):
		for row in self.cosmochat_listbox.get_children():
			self.cosmochat_listbox.remove(row)
		row = make_row({"action": action}, text)
		if action is None:
			row.set_activatable(False)
		self.cosmochat_listbox.add(row)
		self.cosmochat_listbox.show_all()
		self.cosmochat_listbox.select_row(row)
		# select_row() alone isn't enough - see _populate_widget_row()'s own
		# comment on exactly this gap (GTK3 ListBox's keyboard "cursor row"
		# for Enter/::activate-cursor-row doesn't always follow a
		# programmatic select_row() the way real keyboard navigation does).
		GLib.idle_add(row.grab_focus)
		self.cosmochat_hint.set_text("Enter: continue   Backspace: back" if action else "Up/Down: move   Backspace: back")
		self._update_widget_tile_summary("cosmochat", text)

	def _on_cosmochat_row_activated(self, row):
		action = row.item.get("action") if isinstance(row.item, dict) else None
		if action == "setup_credentials":
			self.switch_to("cosmochat_credentials")
		elif action == "login":
			self.switch_to("cosmochat_qr")

	def on_cosmochat_done(self, messages):
		if not messages:
			self._set_cosmochat_prompt("No messages yet", None)
			return
		for row in self.cosmochat_listbox.get_children():
			self.cosmochat_listbox.remove(row)
		for item in messages:
			self.cosmochat_listbox.add(make_cosmochat_row(item))
		self.cosmochat_listbox.show_all()
		first_row = self.cosmochat_listbox.get_row_at_index(0)
		self.cosmochat_listbox.select_row(first_row)
		GLib.idle_add(first_row.grab_focus)
		self.cosmochat_hint.set_text("Up/Down: move   Backspace: back")
		latest = messages[0]
		self._update_widget_tile_summary("cosmochat", f"{latest['sender']}: {latest['text']}")

	# -- Cosmochat: Telegram API credentials ---------------------------------

	def build_cosmochat_credentials_page(self):
		box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=scaling.px_v(10))
		box.set_border_width(scaling.px_v(22))
		box.pack_start(make_heading("Cosmochat Setup"), False, False, 0)

		info = Gtk.Label(label="Get these once at my.telegram.org -> API development tools.", xalign=0)
		info.get_style_context().add_class("dim-label")
		info.set_line_wrap(True)
		box.pack_start(info, False, False, 0)

		box.pack_start(Gtk.Label(label="API ID", xalign=0), False, False, 0)
		self.cosmochat_api_id_entry = Gtk.Entry()
		box.pack_start(self.cosmochat_api_id_entry, False, False, 0)

		box.pack_start(Gtk.Label(label="API Hash", xalign=0), False, False, 0)
		self.cosmochat_api_hash_entry = Gtk.Entry()
		self.cosmochat_api_hash_entry.set_visibility(False)
		box.pack_start(self.cosmochat_api_hash_entry, False, False, 0)

		self.cosmochat_credentials_status = Gtk.Label(xalign=0)
		self.cosmochat_credentials_status.get_style_context().add_class("dim-label")
		box.pack_start(self.cosmochat_credentials_status, False, False, 0)

		hint = Gtk.Label(label="Tab: next field   Enter: save   Backspace: back")
		hint.get_style_context().add_class("dim-label")
		box.pack_start(hint, False, False, 0)

		self.cosmochat_api_id_entry.connect("activate", lambda _e: self.cosmochat_api_hash_entry.grab_focus())
		self.cosmochat_api_hash_entry.connect("activate", lambda _e: self.save_cosmochat_credentials())

		box.focus_widget = self.cosmochat_api_id_entry
		box.show_all()
		self.stack.add_named(box, "cosmochat_credentials")

		credentials = telegram_credentials.load()
		self.cosmochat_api_id_entry.set_text(credentials["api_id"])
		self.cosmochat_api_hash_entry.set_text(credentials["api_hash"])

	def save_cosmochat_credentials(self):
		log.info("save_cosmochat_credentials")
		telegram_credentials.save(self.cosmochat_api_id_entry.get_text(), self.cosmochat_api_hash_entry.get_text())
		self.cosmochat_credentials_status.set_text("Saved. Go back and choose Cosmochat to log in.")

	# -- Cosmochat: QR login --------------------------------------------------

	def build_cosmochat_qr_page(self):
		box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=scaling.px_v(10))
		box.set_border_width(scaling.px_v(22))
		box.pack_start(make_heading("Log In to Cosmochat"), False, False, 0)

		self.cosmochat_qr_image = Gtk.Image()
		box.pack_start(self.cosmochat_qr_image, True, True, 0)

		self.cosmochat_qr_status = Gtk.Label(label="Generating QR code...", xalign=0)
		self.cosmochat_qr_status.get_style_context().add_class("dim-label")
		box.pack_start(self.cosmochat_qr_status, False, False, 0)

		hint = Gtk.Label(
			label="On your phone: Telegram -> Settings -> Devices -> Link Desktop Device, then scan.   Backspace: cancel"
		)
		hint.get_style_context().add_class("dim-label")
		hint.set_line_wrap(True)
		box.pack_start(hint, False, False, 0)

		box.show_all()
		self.stack.add_named(box, "cosmochat_qr")

	def start_cosmochat_login(self):
		self.cosmochat_qr_status.set_text("Generating QR code...")
		self.run_async(
			lambda: telegram_client.start_qr_login(
				self.on_cosmochat_qr_ready,
				self.on_cosmochat_password_needed,
				self.on_cosmochat_login_success,
				self.on_cosmochat_login_error,
			),
			lambda _result: None,
		)

	def on_cosmochat_qr_ready(self, png_path):
		try:
			pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(png_path, scaling.px(320), scaling.px(320), True)
			self.cosmochat_qr_image.set_from_pixbuf(pixbuf)
		except GLib.Error:
			log.warning(f"could not decode QR image at {png_path}", exc_info=True)
		self.cosmochat_qr_status.set_text("Scan with Telegram on your phone.")
		return False

	def on_cosmochat_password_needed(self):
		self.switch_to("cosmochat_password")
		return False

	def on_cosmochat_login_success(self):
		log.info("Cosmochat: Telegram login succeeded")
		self.restart_cosmochat_listener()
		self.go_back()
		self._populate_widget_row()
		return False

	def on_cosmochat_login_error(self, message):
		self.cosmochat_qr_status.set_text(f"Failed: {message}")
		return False

	def restart_cosmochat_listener(self):
		subprocess.run(["systemctl", "--user", "restart", "cosmochat.service"], check=False)

	# -- Cosmochat: 2FA password ----------------------------------------------

	def build_cosmochat_password_page(self):
		box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=scaling.px_v(10))
		box.set_border_width(scaling.px_v(22))
		box.pack_start(make_heading("Two-Step Verification"), False, False, 0)

		self.cosmochat_password_entry = Gtk.Entry()
		self.cosmochat_password_entry.set_visibility(False)
		self.cosmochat_password_entry.connect("activate", lambda _e: self.submit_cosmochat_password())
		box.pack_start(self.cosmochat_password_entry, False, False, 0)

		self.cosmochat_password_status = Gtk.Label(xalign=0)
		self.cosmochat_password_status.get_style_context().add_class("dim-label")
		box.pack_start(self.cosmochat_password_status, False, False, 0)

		hint = Gtk.Label(label="Enter: submit   Backspace: back")
		hint.get_style_context().add_class("dim-label")
		box.pack_start(hint, False, False, 0)

		box.focus_widget = self.cosmochat_password_entry
		box.show_all()
		self.stack.add_named(box, "cosmochat_password")

	def submit_cosmochat_password(self):
		log.info("submit_cosmochat_password")
		telegram_client.submit_password(self.cosmochat_password_entry.get_text())
		self.cosmochat_password_status.set_text("Submitting...")
		self.go_back()  # back to cosmochat_qr, which shows the final status

	def _set_dashboard_placeholder(self, container, text):
		for child in container.get_children():
			container.remove(child)
		label = Gtk.Label(label=text, xalign=0)
		label.get_style_context().add_class("dim-label")
		container.pack_start(label, False, False, 0)
		container.show_all()

	def _set_news_placeholder(self, text):
		"""Gtk.ListBox has no pack_start (it's not a Gtk.Box), so this can't
		share _set_dashboard_placeholder's body - a plain make_row(None,
		text) with selection/activation turned off reuses the same
		label_widget/plain_text shape install_carets() already expects
		from every row in the list, placeholder included (its own
		row-selected handler walks every row, not just the one becoming
		selected)."""
		for row in self.news_listbox.get_children():
			self.news_listbox.remove(row)
		row = make_row(None, text)
		row.set_selectable(False)
		row.set_activatable(False)
		self.news_listbox.add(row)
		self.news_listbox.show_all()

	def refresh_dashboard(self):
		log.debug("refreshing dashboard widgets")
		active = dashboard_widgets.list_active()
		if "retroachievements" in active:
			self.run_async(retroachievements.fetch_summary, self.on_ra_summary_done)
		if "gamenews" in active:
			self.run_async(gamenews.fetch_headlines, self.on_news_done)
		if "cosmochat" in active:
			self.refresh_cosmochat_state()
		if "clock" in active:
			self.refresh_clock_widget()
		if "profile_summary" in active:
			self.refresh_profile_summary_widget()
		# A no-op call on every plain home-screen visit unless the user has
		# ever completed the one-time interactive login (see epic_app.py's
		# 'f' key/start_freegames_login()) and it's actually been long
		# enough since the last check - freegames_backend.claim_if_due()
		# itself guards both, so this can just be fired here
		# unconditionally (on either home) rather than needing its own
		# scheduling logic in the launcher.
		self.run_async(freegames_backend.claim_if_due, self.on_freegames_claimed)

	def on_freegames_claimed(self, titles):
		if titles:
			log.info(f"Epic free games auto-claimed: {', '.join(titles)} - they'll show up in the Epic client's library now")

	def on_ra_summary_done(self, summary):
		if not summary:
			self._set_dashboard_placeholder(
				self.ra_body, "Not connected - see Settings > RetroAchievements Login"
			)
			self._update_widget_tile_summary("retroachievements", "Not connected")
			return
		for child in self.ra_body.get_children():
			self.ra_body.remove(child)

		rank_text = f"Rank #{summary['rank']}" if isinstance(summary["rank"], int) else str(summary["rank"]).capitalize()
		header = Gtk.Label(
			label=f"{summary['username']}  ·  {rank_text}  ·  {summary['total_points']} pts",
			xalign=0,
		)
		header.get_style_context().add_class("dashboard-line")
		header.set_line_wrap(True)
		self.ra_body.pack_start(header, False, False, 0)
		self._update_widget_tile_summary("retroachievements", f"{rank_text} · {summary['total_points']} pts")

		if not summary["recent_achievements"]:
			empty = Gtk.Label(label="No recent achievements", xalign=0)
			empty.get_style_context().add_class("dim-label")
			self.ra_body.pack_start(empty, False, False, 0)
		for achievement in summary["recent_achievements"]:
			line = Gtk.Label(label=f"{achievement['title']} — {achievement['game']}", xalign=0)
			line.get_style_context().add_class("dashboard-line")
			line.set_ellipsize(Pango.EllipsizeMode.END)
			self.ra_body.pack_start(line, False, False, 0)
		self.ra_body.show_all()

	def on_news_done(self, headlines):
		if not headlines:
			self._set_news_placeholder("No headlines available right now")
			self._update_widget_tile_summary("gamenews", "No headlines available")
			return
		for row in self.news_listbox.get_children():
			self.news_listbox.remove(row)

		for item in headlines:
			row = make_news_row(item)
			self.news_listbox.add(row)
			self.run_async(
				lambda item=item: gamenews.fetch_thumbnail(item), lambda path, row=row: self._on_news_thumb_ready(row, path)
			)
		self.news_listbox.show_all()
		self.news_listbox.select_row(self.news_listbox.get_row_at_index(0))
		self._update_widget_tile_summary("gamenews", headlines[0]["title"])

	def _on_news_thumb_ready(self, row, path):
		if not path or row.get_parent() is None:
			return  # no thumbnail for this headline, or the list's been rebuilt since this fetch started
		try:
			pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(path, NEWS_THUMB_WIDTH, NEWS_THUMB_HEIGHT, True)
		except GLib.Error:
			log.warning(f"could not decode news thumbnail at {path}", exc_info=True)
			return
		row.thumb_image.set_from_pixbuf(pixbuf)

	def open_news_article(self, item):
		if not item.get("link"):
			return
		log.info(f"opening news article: {item['link']}")
		self.launch_subprocess({"mode": "graphical", "command": ["python3", KIOSK_LAUNCHER_PATH, item["link"]]})

	def build_quick_launch_add_page(self):
		"""Same shape as build_filemanager_input_page()'s single-Entry form,
		except adding a site is a network call (fetch the page for its
		title/favicon - see quick_launch.add_entry()), so this follows the
		Wi-Fi password screen's async run_async pattern instead of
		filemanager's synchronous one."""
		box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=scaling.px_v(10))
		box.set_border_width(scaling.px_v(22))
		box.pack_start(make_heading("Add Quick Launch"), False, False, 0)

		self.quick_launch_add_entry = Gtk.Entry()
		self.quick_launch_add_entry.connect("activate", lambda _e: self.submit_quick_launch_add())
		box.pack_start(self.quick_launch_add_entry, False, False, 0)

		self.quick_launch_add_status = Gtk.Label(xalign=0)
		self.quick_launch_add_status.get_style_context().add_class("dim-label")
		box.pack_start(self.quick_launch_add_status, False, False, 0)

		hint = Gtk.Label(label="Enter: add   Escape: cancel")
		hint.get_style_context().add_class("dim-label")
		box.pack_start(hint, False, False, 0)

		box.focus_widget = self.quick_launch_add_entry
		box.show_all()
		self.stack.add_named(box, "quick_launch_add")

	def submit_quick_launch_add(self):
		url = self.quick_launch_add_entry.get_text().strip()
		if not url:
			return
		log.info(f"submit_quick_launch_add: {url}")
		self.quick_launch_add_status.set_text("Adding...")
		self.quick_launch_add_entry.set_sensitive(False)
		self.run_async(lambda: quick_launch.add_entry(url), self.on_quick_launch_add_done)

	def on_quick_launch_add_done(self, entry):
		self.quick_launch_add_entry.set_sensitive(True)
		if entry is None:
			self.quick_launch_add_status.set_text("Couldn't reach that site - check the address and try again.")
			return
		self.go_back()
		self._populate_quick_launch_row(self.quick_launch_add_home, select_name=entry["name"])

	# -- profiles -----------------------------------------------------------

	PROFILE_PICKER_CELL_SIZE = scaling.px(220)

	def build_profile_picker_page(self):
		"""A real console-style user-select screen (PS4/Xbox 360 - big
		portrait tiles side by side, the active one called out, a "+ Add
		Profile" tile at the end) - replaced the old plain Gtk.ListBox of
		names-with-a-tiny-thumbnail, which read as a settings menu rather
		than the "who's playing" moment every actual console makes of this.
		Rebuilt from scratch on every entry (see refresh_profile_picker_list())
		rather than built once, same reasoning build_widget_add_page() and
		the avatar picker's own grid already use: which profiles exist
		changes as the user creates/removes them."""
		box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=scaling.px_v(14))
		box.set_border_width(scaling.px_v(22))
		box.pack_start(make_heading("Switch Profile"), False, False, 0)

		self.profile_picker_flow = Gtk.FlowBox()
		self.profile_picker_flow.set_halign(Gtk.Align.CENTER)
		self.profile_picker_flow.set_valign(Gtk.Align.CENTER)
		self.profile_picker_flow.set_vexpand(True)
		self.profile_picker_flow.set_selection_mode(Gtk.SelectionMode.BROWSE)
		self.profile_picker_flow.set_homogeneous(True)
		self.profile_picker_flow.set_row_spacing(scaling.px_v(20))
		self.profile_picker_flow.set_column_spacing(scaling.px_h(20))
		self.profile_picker_flow.set_activate_on_single_click(False)
		self.profile_picker_flow.connect(
			"child-activated", lambda _fb, child: self.activate_profile_row(child.item)
		)
		wire_tile_zoom(self.profile_picker_flow, self.PROFILE_PICKER_CELL_SIZE, self.PROFILE_PICKER_CELL_SIZE_SELECTED)
		box.pack_start(self.profile_picker_flow, True, True, 0)

		hint = controller_glyphs.make_hint_row(
			"Arrows: move   ", controller_glyphs.CONFIRM, ": switch to profile   Backspace: back"
		)
		hint.set_halign(Gtk.Align.CENTER)
		box.pack_start(hint, False, False, 0)

		box.focus_widget = self.profile_picker_flow
		box.show_all()
		self.stack.add_named(box, "profile_picker")

	PROFILE_PICKER_CELL_SIZE_SELECTED = scaling.px(250)  # "zoom up on highlight" target size

	def _make_profile_picker_tile(self, name):
		"""One tile of the user-select lineup - name=None is the "+ Add
		Profile" tile (see activate_profile_row()), matching the same
		None-means-the-add-action convention the old ListBox version used -
		given the same card treatment as every real profile tile (a plain
		"+" icon standing in for a picture, reusing the exact icon/label
		shape make_widget_tile() already established for the Dashboard's
		own "Add Widget" tile) rather than floating as bare text.

		The avatar image's own size_request is always reserved at the
		LARGER, "zoomed" size (PROFILE_PICKER_CELL_SIZE_SELECTED), never
		just the resting size - the card itself never resizes when a tile
		is selected, only what's drawn inside the already-reserved space
		does (see animate_tile_zoom()), so nothing here ever forces a
		FlowBox reflow/reflow-jitter across the row."""
		child = Gtk.FlowBoxChild()
		child.item = name
		traits = _avatar_traits_or_default(name) if name else None
		is_add = name is None
		cell = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=scaling.px_v(10))
		cell.get_style_context().add_class("profile-picker-cell")

		image = Gtk.Image()
		image.set_size_request(self.PROFILE_PICKER_CELL_SIZE_SELECTED, self.PROFILE_PICKER_CELL_SIZE_SELECTED)
		image.set_halign(Gtk.Align.CENTER)
		image.set_valign(Gtk.Align.CENTER)
		cell.pack_start(image, False, False, 0)

		# A real profile's tile binds ONCE to the shared animation
		# heartbeat (see avatar3d_portrait_anim.bind()) the first time
		# render_at_size() runs, then just calls the returned
		# render_at_size(size) itself on every later call (including
		# every intermediate step of the zoom animation) - the same
		# render_at_size(size) contract animate_tile_zoom() already
		# expects, just backed by a bound animation instead of a fresh
		# static avatar3d.render_portrait() render each time. unbind is
		# stashed on child.anim_unbind rather than tied to a "destroy"
		# signal - confirmed live that FlowBox.remove() (what
		# refresh_profile_picker_list() actually calls to tear old tiles
		# down) only detaches a child, it does NOT fire "destroy", so a
		# signal-based unbind never ran and left every old tile's picture
		# still ticking on the shared heartbeat after a rebuild.
		anim_state = {}

		def render_at_size(size, image=image, traits=traits, is_add=is_add, anim_state=anim_state):
			if is_add:
				icon_path = ICONS_DIR / "add.svg"
				if not icon_path.exists():
					return
				try:
					pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(str(icon_path), size // 2, size // 2, True)
					image.set_from_pixbuf(pixbuf)
				except GLib.Error:
					log.warning(f"could not decode add-profile icon at {icon_path}", exc_info=True)
			elif "render_at_size" in anim_state:
				anim_state["render_at_size"](size)
			else:
				anim_render_at_size, unbind = avatar3d_portrait_anim.bind(image, traits, size)
				anim_state["render_at_size"] = anim_render_at_size
				child.anim_unbind = unbind

		child.render_at_size = render_at_size
		child.current_size = self.PROFILE_PICKER_CELL_SIZE
		render_at_size(self.PROFILE_PICKER_CELL_SIZE)

		active = profiles.get_active_profile()
		label = Gtk.Label(label=name if name else "+ Add Profile")
		label.get_style_context().add_class("profile-picker-name")
		cell.pack_start(label, False, False, 0)
		if name and name == active:
			current_label = Gtk.Label(label="Current")
			current_label.get_style_context().add_class("profile-picker-current-badge")
			cell.pack_start(current_label, False, False, 0)

		child.add(cell)
		return child

	def refresh_profile_picker_list(self):
		for child in list(self.profile_picker_flow.get_children()):
			# A plain FlowBox.remove() only detaches a child - it does NOT
			# fire "destroy" (confirmed live: the "destroy"-signal unbind
			# this used to rely on left avatar3d_portrait_anim's shared
			# heartbeat still ticking every old tile's image after a
			# rebuild, growing without bound) - a real profile tile's own
			# anim_unbind (see _make_profile_picker_tile()) has to be
			# called explicitly here instead.
			if hasattr(child, "anim_unbind"):
				child.anim_unbind()
			self.profile_picker_flow.remove(child)
		# All tiles in a single row (min == max == count), same as every
		# other single-row FlowBox in this app (header icon bar, quick-
		# launch row, ...) - NOT a large fixed placeholder count: confirmed
		# live that GTK reserves natural width for min-children-per-line
		# columns regardless of how many children actually exist, so a
		# generic "big enough" number (64, tried first) blew the window
		# out to over 17000px wide instead of just never wrapping.
		line_count = max(len(profiles.list_profiles()) + 1, 1)  # +1 for the Add tile
		self.profile_picker_flow.set_min_children_per_line(line_count)
		self.profile_picker_flow.set_max_children_per_line(line_count)
		active = profiles.get_active_profile()
		selected_child = None
		for name in profiles.list_profiles():
			child = self._make_profile_picker_tile(name)
			self.profile_picker_flow.add(child)
			if name == active:
				selected_child = child
		self.profile_picker_flow.add(self._make_profile_picker_tile(None))  # the Add tile, always last
		self.profile_picker_flow.show_all()
		if selected_child is None:
			selected_child = self.profile_picker_flow.get_child_at_index(0)
		if selected_child is not None:
			self.profile_picker_flow.select_child(selected_child)
			GLib.idle_add(selected_child.grab_focus)

	def activate_profile_row(self, name):
		"""Picking a profile switches to it directly - no confirmation
		submenu in between, per direct instruction ("it should honestly
		just take me to the dashboard directly"). Changing an existing
		profile's own Astronar isn't reached from here at all any more -
		per direct instruction, that's done by selecting the Astronar
		badge on Dashboard itself (see _build_astronar_badge())."""
		if name is None:
			self.open_avatar_picker(NEW_PROFILE)
			return
		if name != profiles.get_active_profile() and profiles.has_pin(name):
			self.open_profile_pin(name)                      # a CosmOS profile with a PIN: the same PIN the profile picker asks for
			return
		self.activate_profile(name)

	def build_profile_pin_page(self):
		"""The PIN of a CosmOS profile, asked before switching to it here (the same PIN the CosmOS profile picker and lock screen use)."""
		box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=scaling.px_v(10))
		box.set_border_width(scaling.px_v(22))
		self.profile_pin_heading = make_heading("PIN")
		box.pack_start(self.profile_pin_heading, False, False, 0)
		self.profile_pin_entry = Gtk.Entry()
		self.profile_pin_entry.set_visibility(False)
		self.profile_pin_entry.set_input_purpose(Gtk.InputPurpose.PIN)
		self.profile_pin_entry.connect("activate", lambda _e: self.submit_profile_pin())
		box.pack_start(self.profile_pin_entry, False, False, 0)
		self.profile_pin_status = Gtk.Label(xalign=0)
		self.profile_pin_status.get_style_context().add_class("dim-label")
		box.pack_start(self.profile_pin_status, False, False, 0)
		hint = Gtk.Label(label="Enter: switch   Escape: cancel")
		hint.get_style_context().add_class("dim-label")
		box.pack_start(hint, False, False, 0)
		box.focus_widget = self.profile_pin_entry
		box.show_all()
		self.profile_pin_target = None
		self.stack.add_named(box, "profile_pin")

	def open_profile_pin(self, name):
		self.profile_pin_target = name
		self.profile_pin_heading.label_widget.set_text(f"PIN for {name}")
		self.profile_pin_entry.set_text("")
		self.profile_pin_status.set_text("")
		self.switch_to("profile_pin")

	def submit_profile_pin(self):
		name = self.profile_pin_target
		if name is None:
			return
		if profiles.verify_pin(name, self.profile_pin_entry.get_text().strip()):
			self.profile_pin_entry.set_text("")
			self.profile_pin_target = None
			self.activate_profile(name)
		else:
			self.profile_pin_entry.set_text("")
			self.profile_pin_status.set_text("That isn't the PIN.")

	def build_profile_add_page(self):
		"""Same shape as build_quick_launch_add_page()'s single-Entry form.
		Reachable two ways: from the picker's own "+ Add Profile" row (a
		real Backspace-to-cancel screen, same as quick-launch add), or
		forced as the very first screen on a fresh install with zero
		profiles (see App.__init__) - Backspace/Escape there is a harmless
		no-op (go_back() on an empty self.history), which is exactly the
		"you have to create one" behavior wanted in that case, not
		something that needed its own special-casing here."""
		box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=scaling.px_v(10))
		box.set_border_width(scaling.px_v(22))
		box.pack_start(make_heading("Add Profile"), False, False, 0)

		self.profile_add_entry = Gtk.Entry()
		self.profile_add_entry.connect("activate", lambda _e: self.submit_profile_add())
		box.pack_start(self.profile_add_entry, False, False, 0)

		self.profile_add_status = Gtk.Label(xalign=0)
		self.profile_add_status.get_style_context().add_class("dim-label")
		box.pack_start(self.profile_add_status, False, False, 0)

		hint = Gtk.Label(label="Enter: create   Escape: cancel")
		hint.get_style_context().add_class("dim-label")
		box.pack_start(hint, False, False, 0)

		box.focus_widget = self.profile_add_entry
		box.show_all()
		self.stack.add_named(box, "profile_add")

	def submit_profile_add(self):
		name = self.profile_add_entry.get_text().strip()
		if not name:
			return
		if name in profiles.list_profiles():
			self.profile_add_status.set_text("A profile with that name already exists.")
			return
		log.info(f"submit_profile_add: {name!r}")
		self.profile_add_entry.set_text("")
		self.activate_profile(name, create=True)

	def build_avatar_picker_page(self):
		"""Shared by both real entry points - staging a brand new profile's
		first picture (target=NEW_PROFILE, see activate_profile_row()) and
		changing an existing one's (target=a real profile name, reached
		via the Delete key on the profile picker - see on_key()'s own
		profile_picker handling) - rather than two near-identical screens.

		A single live, animated avatar3d_viewer.AvatarCustomizePage,
		reused across every trip (same "construct once, reset on entry"
		convention media/vlc_player.py's own PlayerPage already
		established) - replaced the old 3x3 grid of random space-creature
		seeds entirely: this generator's own explicit-choice design (see
		avatar3d.py's own docstring) means there's a real trait to change
		rather than reroll-until-you-like-it."""
		self.avatar_customize_page = avatar3d_viewer.AvatarCustomizePage(on_confirm=self.confirm_avatar_traits)
		self.avatar_customize_page.show_all()
		self.stack.add_named(self.avatar_customize_page, "avatar_picker")

	def open_avatar_picker(self, target):
		self.avatar_picker_target = target
		existing = None if target is NEW_PROFILE else profiles.get_picture_traits(target)
		profile_name = None if target is NEW_PROFILE else target
		self.avatar_customize_page.start(existing or avatar3d_viewer.CUSTOM_DEFAULTS, profile_name)
		self.switch_to("avatar_picker")

	def confirm_avatar_traits(self, traits):
		self.avatar_customize_page.stop()
		if self.avatar_picker_target is NEW_PROFILE:
			# Nothing real to save yet - there's no profile directory to
			# put stored traits into until submit_profile_add() actually
			# creates one, which picks this same staged trait set up
			# itself (see activate_profile()'s own create branch).
			self.avatar_picker_staged_traits = traits
			self.switch_to("profile_add")
			return
		profiles.set_picture_traits(traits, self.avatar_picker_target)
		log.info(f"updated picture for profile {self.avatar_picker_target!r}")
		if self.avatar_picker_target == profiles.get_active_profile():
			self.refresh_astronar_badges()
		self.go_back()  # avatar_picker -> profile_picker (reached directly via the Delete key now, no profile_actions in between)
		self.refresh_profile_picker_list()

	def activate_profile(self, name, create=False):
		"""Switches (or creates-then-switches) the active profile, re-points
		every emulator's save symlink at it (usb_saves.sync_all_saves() -
		has to happen before the rebuild, not after, so nothing can launch
		a game against the previous profile's save symlink in the gap),
		then requests the same destroy-and-reconstruct-App() rebuild
		hot-reload already uses (see App.__init__'s own restart_requested
		docstring) rather than hand-refreshing every profile-scoped widget
		individually."""
		if create:
			profiles.create_profile(name)
			# Picks up whatever's already staged from a real trip through
			# the avatar picker (the normal path - see
			# activate_profile_row()); a profile created some other way
			# (namely the very first one ever, forced straight to
			# profile_add on a fresh install with no picker step at all -
			# see App.__init__) still gets a real picture rather than
			# none, avatar3d_viewer.CUSTOM_DEFAULTS used right here instead.
			traits = self.avatar_picker_staged_traits if self.avatar_picker_staged_traits is not None else avatar3d_viewer.CUSTOM_DEFAULTS
			profiles.set_picture_traits(traits, name)
			self.avatar_picker_staged_traits = None
		profiles.set_active_profile(name)
		usb_saves.sync_all_saves()
		self.restart_cosmochat_listener()
		log.info(f"activated profile {name!r}")
		if self.restart_requested is not None:
			self.restart_requested.set()

	def activate_menu_entry(self, entry):
		log.info(f"menu entry activated: {entry.get('name')}")
		if entry.get("action") == "quit":
			self.quit()
			return
		screen = entry.get("screen")
		if screen:
			self.switch_to(screen)  # gated automatically for anything in ONLINE_ONLY_SCREENS
			return
		self.launch_subprocess(entry)

	def _hide_own_cursor(self):
		"""Explicitly re-blanks this window's own cursor after self.show() -
		graphical.reset_cursor() (called inside run_graphical()/
		run_with_filter(), before this window is remapped - see
		launch_subprocess()/launch_game_row()) turned out not enough on
		its own: it only ever targets the ROOT window, and something in
		GTK's own show()/map handling can reassert a real cursor on this
		window specifically once it becomes visible again, overriding
		whatever the root window says to inherit - confirmed directly, a
		real report that resetting the root cursor alone didn't actually
		fix this. Setting it straight on this window's own GdkWindow,
		after show() rather than only on root before it, is what actually
		sticks."""
		window = self.get_window()
		if window:
			display = Gdk.Display.get_default()
			cursor = Gdk.Cursor.new_for_display(display, Gdk.CursorType.BLANK_CURSOR)
			window.set_cursor(cursor)
			log.debug("re-blanked launcher window's own cursor")
		else:
			log.warning("_hide_own_cursor: self.get_window() returned None, nothing to set a cursor on")

	def launch_subprocess(self, entry):
		mode = entry.get("mode", "plain")
		# Quick-launch tiles/news links go through kiosk_launcher.py (see
		# activate_quick_launch()/open_news_article()) with no upstream
		# place to hide them the way a static menu tile could be hidden
		# at config.ini/systems.json load time - checked here instead,
		# right before anything would actually try to run.
		if KIOSK_LAUNCHER_PATH in entry.get("command", []) and not ELECTRON_BIN.exists():
			log.warning("launch_subprocess: skipping - Electron runtime isn't fetched (see webshell/electron_runtime.py)")
			return
		if "command" in entry:
			# Only tokens that actually reference {repo} (config.ini/
			# systems.json entries shipping their own launcher script, e.g.
			# epic_app.py) go through library.expand() -
			# every other token (URLs, bare flags like "-gamepadui", the
			# "python3"/"steam" interpreter name itself) is left exactly as
			# written, since expand()'s own Path()/expanduser() round-trip
			# would otherwise corrupt a URL's "//" (a real, confirmed
			# PurePosixPath behavior, not a hypothetical).
			entry["command"] = [library.expand(token) if "{repo}" in token else token for token in entry["command"]]
		log.info(f"launch_subprocess: {entry.get('command')} (mode={mode})")
		self.hide()
		# self.hide() only queues an X11 unmap request - it isn't actually
		# sent to the X server until something flushes the connection,
		# which normally happens as a side effect of Gtk.main() iterating.
		# The blocking calls below (run_graphical waits for the whole
		# subprocess to exit) never let that iteration happen, so
		# without an explicit flush here the request just sits buffered
		# for the launched app's entire runtime - the launcher stays
		# fully mapped and visible the whole time despite hide() having
		# been called. Confirmed directly: querying window map state
		# mid-launch showed the launcher's window still IsViewable.
		# Harmless for every entry that covers the whole screen anyway
		# (games, Epic/GOG, the browser); it only became visible once an
		# entry (Media) used a normal, non-fullscreen window that the
		# still-mapped launcher showed through around.
		Gdk.flush()
		try:
			if mode == "graphical":
				graphical.run_graphical(entry["command"])
			else:
				subprocess.run(entry["command"])
		finally:
			self.show()
			self._hide_own_cursor()

	# -- games: categories -> manufacturers -> platforms -> dual pane ------

	def build_categories_page(self):
		page = make_list_page("Category", self.categories, lambda c: c["name"], on_back=True)
		page.listbox.connect("row-activated", lambda _lb, row: self.select_category(row.item))
		self.stack.add_named(page, "categories")

	def select_category(self, category):
		self.cat_idx = self.categories.index(category)
		self.build_manufacturers_page()
		self.switch_to("manufacturers")

	def build_manufacturers_page(self):
		"""Drops any manufacturer whose "requires_command" (see systems.
		json's own Steam entry - a native RPM, not a Flatpak, see
		packaging/steam_native.py) names a bare command that isn't on
		PATH - a defensive fallback: Deploy GUI's build-time install
		should mean this never actually triggers, but a tile that can't
		launch shouldn't be shown regardless. self.categories itself is
		left untouched - only this page's own display list is filtered."""
		manufacturers = [
			m
			for m in self.categories[self.cat_idx]["manufacturers"]
			if not m.get("requires_command") or shutil.which(m["requires_command"])
		]
		page = make_list_page(self.categories[self.cat_idx]["name"], manufacturers, lambda m: m["name"], on_back=True)
		page.listbox.connect("row-activated", lambda _lb, row: self.select_manufacturer(row.item))
		self.replace_page("manufacturers", page)

	def select_manufacturer(self, manufacturer):
		"""Most manufacturers are a ROM-scanning group (Nintendo, Sega, ...)
		and drill into platforms/games; a "launch" entry instead (Steam,
		eventually Epic - see systems.json's "PC" category) is a single
		external client with no ROM folder to browse, so it hands off
		straight to launch_subprocess() the same way a plain config.ini menu
		entry would, skipping the platforms/games screens entirely."""
		launch = manufacturer.get("launch")
		if launch:
			self.launch_subprocess(launch)
			return
		manufacturers = self.categories[self.cat_idx]["manufacturers"]
		self.man_idx = manufacturers.index(manufacturer)
		self.build_platforms_page()
		self.switch_to("platforms")

	def build_platforms_page(self):
		"""Two panes, not a plain list - per direct instruction: the left
		side is a plain name ListBox, the right is a real info panel
		(metadata plus an auto-scrolling paraphrased history blurb - see
		launcher/platform_history.py) that updates live as the selection
		moves, the same "selection drives a side panel" shape
		[[project_cosmos_console_identity]] already established for
		content-adaptive theming on the Games grid. Rebuilt from scratch
		on every visit (replace_page(), not cached), matching every other
		lazy-built page in this app."""
		platforms = self.categories[self.cat_idx]["manufacturers"][self.man_idx]["platforms"]
		group_name = self.categories[self.cat_idx]["manufacturers"][self.man_idx]["name"]

		outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=scaling.px_v(6))
		outer.set_border_width(scaling.px_v(22))
		outer.pack_start(make_heading(group_name), False, False, 0)

		panes = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=scaling.px_h(24))
		outer.pack_start(panes, True, True, 0)

		left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
		left.set_size_request(scaling.px(560), -1)
		scroller = Gtk.ScrolledWindow()
		scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
		listbox = Gtk.ListBox()
		listbox.set_selection_mode(Gtk.SelectionMode.BROWSE)
		install_carets(listbox)
		for platform in platforms:
			listbox.add(make_row(platform, platform["name"]))
		scroller.add(listbox)
		left.pack_start(scroller, True, True, 0)
		panes.pack_start(left, False, False, 0)

		panes.pack_start(self._build_platform_info_pane(), True, True, 0)

		hint = Gtk.Label(label="Up/Down: move   Enter: select   Backspace: back")
		hint.get_style_context().add_class("dim-label")
		outer.pack_start(hint, False, False, 0)

		listbox.connect("row-activated", lambda _lb, row: self.select_platform(row.item))
		listbox.connect("row-selected", lambda _lb, row: self._update_platform_info_pane(row.item if row else None))

		outer.listbox = listbox
		outer.show_all()
		self.replace_page("platforms", outer)
		if platforms:
			listbox.select_row(listbox.get_row_at_index(0))
		else:
			self._update_platform_info_pane(None)

	def _build_platform_info_pane(self):
		"""Builds the right pane's widgets once per page (fresh on every
		build_platforms_page() call, like everything else on this page) -
		_update_platform_info_pane() fills them in per selection rather
		than rebuilding the pane itself on every arrow-key move."""
		pane = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=scaling.px_v(14))
		pane.get_style_context().add_class("platform-info-pane")

		self.platform_info_meta_label = Gtk.Label(xalign=0, wrap=True)
		self.platform_info_meta_label.get_style_context().add_class("platform-info-meta")
		pane.pack_start(self.platform_info_meta_label, False, False, 0)

		self.platform_desc_scroller = Gtk.ScrolledWindow()
		self.platform_desc_scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
		self.platform_desc_scroller.set_can_focus(False)
		self.platform_desc_label = Gtk.Label(xalign=0, yalign=0, wrap=True)
		self.platform_desc_label.get_style_context().add_class("platform-info-description")
		self.platform_desc_scroller.add(self.platform_desc_label)
		pane.pack_start(self.platform_desc_scroller, True, True, 0)

		self.platform_desc_scroll_state = {"phase": "pause", "ticks": PLATFORM_DESC_PAUSE_TICKS}
		return pane

	def _update_platform_info_pane(self, platform):
		"""Called on every Platforms-page selection change (see
		build_platforms_page()'s "row-selected" hookup) - fills the info
		pane in for `platform`, or clears it entirely when the list is
		empty (platform=None). Any piece with no data for this platform
		(see platform_history.py's own "missing just means missing"
		convention) is left blank rather than showing something wrong."""
		if platform is None:
			self.platform_info_meta_label.set_text("")
			self.platform_desc_label.set_text("")
			return

		manufacturer = self.categories[self.cat_idx]["manufacturers"][self.man_idx]["name"]
		meta_lines = [f"Manufacturer: {manufacturer}"]
		year = platform_history.YEAR.get(platform["id"])
		if year:
			meta_lines.append(f"Released: {year}")
		if platform.get("description"):
			meta_lines.append(platform["description"])
		self.platform_info_meta_label.set_text("\n".join(meta_lines))

		self.platform_desc_label.set_text(platform_history.HISTORY.get(platform["id"], ""))
		self.platform_desc_scroller.get_vadjustment().set_value(0)
		self.platform_desc_scroll_state = {"phase": "pause", "ticks": PLATFORM_DESC_PAUSE_TICKS}

	def _platform_desc_scroll_tick(self):
		"""One always-on GLib.timeout_add (started once in __init__, never
		removed) rather than a per-page-visit start/stop pair - cheap
		enough to just no-op via this getattr() guard on every screen that
		isn't the Platforms page, same reasoning USB_REFRESH_MS's own tick
		already uses. See PLATFORM_DESC_SCROLL_STEP's own comment for the
		scroll-pause-jump-pause loop this drives."""
		scroller = getattr(self, "platform_desc_scroller", None)
		if scroller is None:
			return True
		state = self.platform_desc_scroll_state
		adjustment = scroller.get_vadjustment()
		max_value = max(0.0, adjustment.get_upper() - adjustment.get_page_size())
		if state["phase"] == "pause":
			state["ticks"] -= 1
			if state["ticks"] <= 0:
				state["phase"] = "reset" if adjustment.get_value() >= max_value else "scrolling"
			return True
		if state["phase"] == "reset":
			adjustment.set_value(0)
			state["phase"] = "pause"
			state["ticks"] = PLATFORM_DESC_PAUSE_TICKS
			return True
		new_value = min(adjustment.get_value() + PLATFORM_DESC_SCROLL_STEP, max_value)
		adjustment.set_value(new_value)
		if new_value >= max_value:
			state["phase"] = "pause"
			state["ticks"] = PLATFORM_DESC_PAUSE_TICKS
		return True

	def select_platform(self, platform):
		platforms = self.categories[self.cat_idx]["manufacturers"][self.man_idx]["platforms"]
		self.plat_idx = platforms.index(platform)
		self.build_games_page()
		self.switch_to("games")

	def build_games_page(self):
		"""A real box-art grid (see make_game_tile()) - console-style
		cover-art browsing (PS3/PS4/Xbox 360 all work this way - see
		[[project_cosmos_console_identity]]), not the flat filename
		ListBox this replaced. Rebuilt from scratch on every visit
		(replace_page(), not cached) since which platform is showing
		changes every time this page is reached - same reasoning as
		every other lazy lister in this app."""
		platform = self.categories[self.cat_idx]["manufacturers"][self.man_idx]["platforms"][self.plat_idx]
		extra_roms_dir = usb_content.usb_roms_dir(platform["id"])
		games = library.list_games(platform, extra_roms_dir=extra_roms_dir)

		box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=scaling.px_v(6))
		box.set_border_width(scaling.px_v(22))
		box.pack_start(make_heading(platform["name"]), False, False, 0)

		if platform.get("description"):
			subtitle = Gtk.Label(label=platform["description"], xalign=0, wrap=True)
			subtitle.get_style_context().add_class("dim-label")
			box.pack_start(subtitle, False, False, 0)

		scroller = Gtk.ScrolledWindow()
		scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
		scroller.set_can_focus(False)
		flow = Gtk.FlowBox()
		flow.set_selection_mode(Gtk.SelectionMode.BROWSE)
		flow.set_homogeneous(True)
		flow.set_row_spacing(scaling.px_v(20))
		flow.set_column_spacing(scaling.px_h(20))
		flow.set_activate_on_single_click(False)
		flow.set_valign(Gtk.Align.START)
		flow.set_halign(Gtk.Align.FILL)
		# .row-focused (theme.css) is what actually turns on a selected
		# tile's glow/breathing-animation state (see .menu-icon-tile's own
		# CSS) - normally toggled dynamically because a page can have
		# several competing focusable regions (see on_menu_row_focus_child()),
		# but this page has exactly one (the grid itself), so it's set
		# once here rather than needing that machinery at all.
		flow.get_style_context().add_class("row-focused")
		# No fixed min/max children per line, unlike the small fixed-count
		# rows elsewhere (header icons, big tiles) - a real wrapping grid,
		# as many tiles per row as the actual window width allows, same
		# as every real console's own game-grid browsing. Selection is
		# shown via .menu-icon-tile's own selected-glow CSS (theme.css),
		# the same convention every other FlowBox grid in this app
		# already uses - install_carets() is a ListBox-only affordance,
		# not applicable here.
		for game in games:
			flow.add(make_game_tile(game, platform["id"]))
		wire_tile_zoom(flow, GAME_TILE_ART_WIDTH, GAME_TILE_ART_WIDTH_SELECTED)
		scroller.add(flow)
		box.pack_start(scroller, True, True, 0)

		if not games:
			empty_label = Gtk.Label(label=f"No games found in {platform['roms_dir']}", xalign=0.5, yalign=0.5)
			empty_label.get_style_context().add_class("dim-label")
			empty_label.set_vexpand(True)
			box.pack_start(empty_label, True, True, 0)

		hint_text = "Up/Down/Left/Right: move   Enter: launch   Backspace: back" if games else "Backspace: back"
		hint = Gtk.Label(label=hint_text)
		hint.get_style_context().add_class("dim-label")
		box.pack_start(hint, False, False, 0)

		flow.connect("child-activated", lambda _fb, child: self.open_game_detail(platform, child.item))
		# Content-adaptive theming (see [[project_cosmos_console_identity]]):
		# re-hues the UI's own accent tokens toward whatever's dominant in
		# the currently-focused tile's box art, live, as focus moves around
		# the grid - BROWSE selection mode fires this on every arrow-key
		# move, not just Enter/activation (that's child-activated, above).
		flow.connect("selected-children-changed", self._on_games_selection_changed)
		box.focus_widget = flow.get_child_at_index(0) if games else flow
		theme.clear_adaptive_accent()  # always start clean; re-applied below if the first tile has art
		if games:
			flow.select_child(flow.get_child_at_index(0))
		box.show_all()

		self.replace_page("games", box)
		self._queue_boxart_fetches(flow, platform["id"], games)
		if games:
			self._apply_game_accent(flow, flow.get_child_at_index(0))

	def _on_games_selection_changed(self, flow):
		children = flow.get_selected_children()
		if not children:
			theme.clear_adaptive_accent()
			return
		self._apply_game_accent(flow, children[0])

	def _apply_game_accent(self, flow, child):
		hue = boxart.get_cached_hue(child.platform_id, child.boxart_key)
		if hue is not None:
			theme.apply_adaptive_accent(hue)
			return
		if boxart.get_cached(child.platform_id, child.boxart_key) is None:
			theme.clear_adaptive_accent()  # no art fetched yet (or none found) - stay on the base theme
			return
		# Art is on disk but its hue was never computed (predates this
		# feature) - decode it once in the background, then apply it only
		# if focus hasn't already moved on to a different tile by the time
		# it's ready.
		self.run_async(
			lambda: boxart.ensure_hue_cached(child.platform_id, child.boxart_key),
			lambda _result: self._on_game_hue_ready(flow, child),
		)

	def _on_game_hue_ready(self, flow, child):
		if child not in flow.get_selected_children():
			return
		hue = boxart.get_cached_hue(child.platform_id, child.boxart_key)
		if hue is not None:
			theme.apply_adaptive_accent(hue)

	def _queue_boxart_fetches(self, flow, platform_id, games):
		"""Fetches box art for every game on this page one at a time (not
		all at once in parallel, unlike gamenews' own per-row run_async
		calls) - a real ROM library can be hundreds of games deep, and
		IGDB's API has real rate limits a burst of a hundred simultaneous
		requests would blow straight through. Only games without
		already-cached art are queued at all - boxart.get_cached() (no
		network) already filled in anything make_game_tile() found on
		disk synchronously while building the tile."""
		to_fetch = [
			(child, game)
			for child, game in zip(flow.get_children(), games)
			if boxart.get_cached(platform_id, child.boxart_key) is None
		]
		self._fetch_next_boxart(flow, platform_id, to_fetch)

	def _fetch_next_boxart(self, flow, platform_id, queue):
		if not queue:
			return
		child, game = queue[0]
		rest = queue[1:]
		self.run_async(
			lambda: boxart.fetch(platform_id, child.boxart_key, game["description"]),
			lambda path: self._on_game_boxart_ready(flow, child, path, platform_id, rest),
		)

	def _on_game_boxart_ready(self, flow, child, path, platform_id, rest):
		if path and child.get_parent() is not None:  # tile might be gone if the page was rebuilt mid-fetch
			# Redraws at whatever size this tile is CURRENTLY at (not
			# always GAME_TILE_ART_WIDTH) - if the user is sitting zoomed
			# in on this exact tile when its real art lands, it should
			# render at that zoomed size, not snap back to resting size.
			child.render_at_size(child.current_size)
			# fetch() already cached this tile's hue as part of the download
			# (see boxart.py) - if the user is sitting on this exact tile
			# right now, apply its accent immediately rather than waiting
			# for them to move focus away and back.
			if child in flow.get_selected_children():
				self._apply_game_accent(flow, child)
		self._fetch_next_boxart(flow, platform_id, rest)

	def launch_game_row(self, platform, game):
		log.info(f"launch_game_row: {platform.get('id')} / {game.get('rom')}")
		self.hide()
		try:
			library.launch_game(platform, game["rom"])
		finally:
			self.show()
			self._hide_own_cursor()

	# -- game detail (screenshots/wheel art + a live-resolved YouTube preview) --

	def open_game_detail(self, platform, game):
		"""A tile tap now opens this instead of launching directly (per
		direct instruction) - self.game_detail_platform/_game are read by
		refresh_game_detail_page() (see on_enter_handlers) and by this
		page's own Play/Watch Preview buttons below."""
		self.game_detail_platform = platform
		self.game_detail_game = game
		self.switch_to("game_detail")

	def build_game_detail_page(self):
		"""No Wikipedia text on this page. Box art reuses the same cached library/boxart.py
		(IGDB) result the grid tile already has; screenshot/wheel art comes
		from library/skyscraper_scraper.py (ScreenScraper via the vendored
		Skyscraper CLI), fetched on first visit if not already cached -
		same "show cached synchronously, fetch in background" shape
		build_games_page()'s own _queue_boxart_fetches() already
		establishes. Play launches the emulator (the same
		launch_game_row() every tile used to call directly); Watch Preview
		resolves a real YouTube stream live via library/preview_video.py
		and hands it to the shared media player - never auto-played."""
		box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=scaling.px_v(12))
		box.set_border_width(scaling.px_v(22))
		self.game_detail_heading = make_heading("")
		box.pack_start(self.game_detail_heading, False, False, 0)

		content = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=scaling.px_h(20))

		self.game_detail_art_image = Gtk.Image()
		self.game_detail_art_image.set_size_request(GAME_TILE_ART_WIDTH_SELECTED, GAME_TILE_ART_HEIGHT_SELECTED)
		content.pack_start(self.game_detail_art_image, False, False, 0)

		extra_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=scaling.px_h(12))
		self.game_detail_screenshot_image = Gtk.Image()
		self.game_detail_wheel_image = Gtk.Image()
		extra_box.pack_start(self.game_detail_screenshot_image, False, False, 0)
		extra_box.pack_start(self.game_detail_wheel_image, False, False, 0)
		content.pack_start(extra_box, True, True, 0)

		box.pack_start(content, False, False, 0)

		self.game_detail_play_button = Gtk.Button(label="Play")
		self.game_detail_play_button.connect("clicked", lambda _b: self.play_game_detail())
		box.pack_start(self.game_detail_play_button, False, False, 0)

		self.game_detail_preview_button = Gtk.Button(label="Watch Preview")
		self.game_detail_preview_button.connect("clicked", lambda _b: self.watch_game_detail_preview())
		box.pack_start(self.game_detail_preview_button, False, False, 0)

		self.game_detail_preview_status = Gtk.Label(xalign=0)
		self.game_detail_preview_status.get_style_context().add_class("dim-label")
		box.pack_start(self.game_detail_preview_status, False, False, 0)

		hint = Gtk.Label(label="Backspace: back")
		hint.get_style_context().add_class("dim-label")
		box.pack_start(hint, False, False, 0)

		box.focus_widget = self.game_detail_play_button
		box.show_all()
		self.stack.add_named(box, "game_detail")

	def refresh_game_detail_page(self):
		platform, game = self.game_detail_platform, self.game_detail_game
		self.game_detail_heading.label_widget.set_text(game["description"])
		self.game_detail_preview_status.set_text("")
		boxart_key = Path(game["rom"]).stem

		cached_boxart = boxart.get_cached(platform["id"], boxart_key)
		if cached_boxart:
			try:
				pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(
					cached_boxart, GAME_TILE_ART_WIDTH_SELECTED, GAME_TILE_ART_HEIGHT_SELECTED, True
				)
				self.game_detail_art_image.set_from_pixbuf(pixbuf)
			except GLib.Error:
				log.warning(f"could not decode cached box art at {cached_boxart}", exc_info=True)
		else:
			self.game_detail_art_image.clear()

		self.game_detail_screenshot_image.clear()
		self.game_detail_wheel_image.clear()
		media = skyscraper_scraper.get_cached(platform["id"], boxart_key)
		if media:
			self._render_game_detail_media(media)
		else:
			self.run_async(
				lambda: skyscraper_scraper.fetch(platform["id"], boxart_key, game["rom"]),
				lambda media: self._on_game_detail_media_ready(game, media),
			)
		self.game_detail_play_button.grab_focus()

	def _render_game_detail_media(self, media):
		def load(image_widget, path):
			if not path:
				image_widget.clear()
				return
			try:
				pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(path, GAME_TILE_ART_WIDTH, GAME_TILE_ART_HEIGHT, True)
				image_widget.set_from_pixbuf(pixbuf)
			except GLib.Error:
				log.warning(f"could not decode cached skyscraper media at {path}", exc_info=True)
				image_widget.clear()

		load(self.game_detail_screenshot_image, media.get("screenshot"))
		load(self.game_detail_wheel_image, media.get("wheel"))

	def _on_game_detail_media_ready(self, expected_game, media):
		if self.game_detail_game is not expected_game:
			return  # navigated away to a different game before this landed
		if media:
			self._render_game_detail_media(media)

	def play_game_detail(self):
		self.launch_game_row(self.game_detail_platform, self.game_detail_game)

	def watch_game_detail_preview(self):
		game = self.game_detail_game

		def work():
			return preview_video.resolve_stream_url(game["description"])

		def on_done(result):
			if self.game_detail_game is not game:
				return  # navigated away before this landed
			if not result:
				self.game_detail_preview_status.set_text("No preview found.")
				return
			video_url, audio_url = result
			self.game_detail_preview_status.set_text("")
			self.player_page.play(video_url, title=game["description"], audio_url=audio_url)
			self.switch_to("media_player")

		self.game_detail_preview_status.set_text("Looking for a preview...")
		self.run_async(work, on_done)

	# -- play disc -------------------------------------------------------

	def build_disc_page(self):
		"""Registered eagerly (like build_usb_page) so the "disc" stack
		name exists from the start, but its actual content is rebuilt
		fresh via refresh_disc_list() every time the screen is entered
		(see on_enter_handlers) - the same "live state, rebuild on every
		open" reasoning open_usb_actions_page() already uses, since a
		disc could have been swapped since the last visit."""
		self.stack.add_named(self._build_disc_list_page(), "disc")

	def _build_disc_list_page(self, folder=None):
		"""folder=None browses the disc's own root (mounting it first, and forgetting any breadcrumb from a previous visit); a folder entry re-enters
		this with that subfolder, so browsing nests as deep as the disc's own layout does (see disc_content.browse()). self.disc_folder_stack is the
		breadcrumb Backspace/Escape walks back up (see go_back())."""
		if folder is None:
			disc_content.mount_disc()
			folder = disc_content.MOUNTPOINT
			self.disc_folder_stack = []
		entries = disc_content.browse(folder)
		self.disc_browse_folder = folder

		if not entries:
			at_root = folder == disc_content.MOUNTPOINT
			empty_text = "No disc detected, or nothing playable on it (play.txt is missing, or the rom isn't unambiguous)." if at_root else 				"Nothing playable here."
		else:
			empty_text = ""

		page = make_list_page("Game Disc", entries, lambda e: e.name, on_back=True, empty_text=empty_text)
		page.listbox.connect("row-activated", lambda _lb, row: self.open_disc_entry(row.item))

		self.disc_status_label = Gtk.Label(xalign=0)
		page.pack_start(self.disc_status_label, False, False, 0)
		page.show_all()
		return page

	def refresh_disc_list(self):
		self.replace_page("disc", self._build_disc_list_page())

	def open_disc_entry(self, entry):
		"""A folder entry browses into it (still the "disc" screen, one level deeper - the current folder is pushed onto disc_folder_stack so
		Backspace/Escape comes back up one level at a time, see go_back()); a game entry goes to Play/Install/Cancel."""
		if entry.kind == "folder":
			self.disc_folder_stack.append(self.disc_browse_folder)
			self.replace_page("disc", self._build_disc_list_page(entry.path))
			return
		self.open_disc_actions_page(entry)

	def open_disc_actions_page(self, entry):
		"""Same shape as open_usb_actions_page() - make_list_page already
		provides the heading/list/hint, this just optionally slots a
		boxart preview in right below that heading (index 1, since index
		0 is make_list_page's own heading) when the disc provides one."""
		actions = ["Play", "Install to Library", "Cancel"]
		page = make_list_page(entry.name, actions, lambda a: a, on_back=True)

		if entry.boxart:
			try:
				pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(str(entry.boxart), -1, scaling.px(300), True)
				image = Gtk.Image.new_from_pixbuf(pixbuf)
				page.pack_start(image, False, False, 0)
				page.reorder_child(image, 1)
				image.show()
			except GLib.Error:
				log.warning(f"could not decode disc boxart at {entry.boxart}", exc_info=True)

		page.listbox.connect("row-activated", lambda _lb, row: self.handle_disc_action(entry, row.item))
		self.replace_page("disc_actions", page)
		self.switch_to("disc_actions")

	def _find_platform(self, platform_id):
		return next((p for p in self.shop_platforms if p["id"] == platform_id), None)

	def handle_disc_action(self, entry, action):
		log.info(f"handle_disc_action: {action!r} on {entry.name!r}")
		if action == "Play":
			platform = self._find_platform(entry.system)
			if not platform:
				self.go_back()
				self.disc_status_label.set_text(f"Unknown system {entry.system!r} - can't launch {entry.name}.")
				return
			self.launch_game_row(platform, {"rom": str(entry.rom)})
		elif action == "Install to Library":
			platform = self._find_platform(entry.system)
			if not platform:
				self.go_back()
				self.disc_status_label.set_text(f"Unknown system {entry.system!r} - can't install {entry.name}.")
				return
			self.stack.get_child_by_name("disc_actions").listbox.set_sensitive(False)
			self.run_async(
				lambda: disc_content.install_game(entry, platform["roms_dir"]), lambda result: self._on_disc_install_done(result)
			)
		elif action == "Cancel":
			self.go_back()

	def _on_disc_install_done(self, result):
		_ok, message = result
		self.disc_status_label.set_text(message)
		self.go_back()  # back to "disc"

	# -- native Video/Music/Photos ----------------------------------------

	def build_video_categories_page(self):
		self._build_media_page("video")

	def build_music_categories_page(self):
		self._build_media_page("music")

	def build_photos_categories_page(self):
		self._build_media_page("photos")

	def build_media_player_page(self):
		"""One shared player page for Video and Music (see media/
		vlc_player.py's own docstring on why one PlayerPage covers both) -
		on_back and on_end_of_media both just return to whatever
		video_categories/music_categories frame was showing before Play,
		via the same self.history switch_to() already pushed there."""
		self.player_page = vlc_player.PlayerPage(on_back=self.go_back)
		self.player_page.on_end_of_media = self.go_back
		self.stack.add_named(self.player_page, "media_player")

	def build_photo_viewer_page(self):
		self.photo_viewer_page = photo_viewer.PhotoViewerPage(on_back=self.go_back)
		self.stack.add_named(self.photo_viewer_page, "photo_viewer")

	def _build_media_page(self, media_type):
		"""Resets media_type's own nav stack to its root frame (Local
		Files' own top-level categories, plus a trailing "Add-ons" row -
		see _media_frame_items()) and renders it. Only called once, at
		startup - re-entering the screen later re-renders whatever frame
		was last showing instead (see on_enter_handlers), same as
		fm_dirs never resetting to home on re-entry."""
		self.media_nav[media_type] = [{"kind": "root", "title": MEDIA_TYPE_LABELS[media_type]}]
		self._refresh_media_page(media_type)

	def _media_frame_items(self, media_type, frame):
		"""Returns this frame's own content rows (never the synthetic
		"Back" row - _refresh_media_page prepends that itself, the same
		split build_filemanager_page's own ".." row keeps from each
		pane's real directory listing)."""
		kind = frame["kind"]
		if kind == "root":
			categories = addon_backend.list_categories(media_type, addon_backend.BUILTIN_ADDON_ID)
			rows = [{"row_kind": "category", "id": c["id"], "label": c["name"]} for c in categories]
			rows.append({"row_kind": "open_addons", "label": "Add-ons"})
			return rows
		if kind == "addons_list":
			addons = addon_backend.list_addons(media_type)
			rows = [{"row_kind": "open_addon", "id": a["id"], "label": a["name"]} for a in addons]
			rows.append({"row_kind": "browse_repo", "label": "Get More Add-ons..."})
			return rows
		if kind == "addon_categories":
			categories = addon_backend.list_categories(media_type, frame["addon_id"])
			return [{"row_kind": "category", "id": c["id"], "label": c["name"]} for c in categories]
		if kind == "items":
			items = addon_backend.list_items(media_type, frame["addon_id"], frame.get("category_id"))
			return [
				{
					"row_kind": "folder" if item.get("is_folder") else "leaf",
					"id": item["id"],
					"label": item.get("title", item["id"]),
					"raw": item,
				}
				for item in items
			]
		if kind == "repo_browse":
			available = addon_repo.list_available(media_type)
			return [{"row_kind": "install_addon", "label": entry.get("name", entry["id"]), "entry": entry} for entry in available]

		# Video only from here down - real Stremio add-ons (stremio_addon.py),
		# not addon_backend's own custom format (kept for Music/Photos -
		# see that module's own docstring for why: Stremio's real add-on
		# ecosystem has no music/photos equivalent at all).
		if kind == "stremio_addons_list":
			addons = stremio_addon.list_installed()
			rows = [
				{"row_kind": "open_stremio_addon", "manifest_url": a["manifest_url"], "label": a["manifest"].get("name", a["manifest_url"])}
				for a in addons
			]
			rows.append({"row_kind": "add_stremio_addon", "label": "Add Add-on by URL..."})
			return rows
		if kind == "stremio_catalogs":
			addon = frame["addon"]
			catalogs = stremio_addon.list_catalogs(addon)
			return [
				{
					"row_kind": "stremio_catalog",
					"content_type": c["type"],
					"catalog_id": c["id"],
					"label": f"{c['name']} ({c['type'].capitalize()})",
				}
				for c in catalogs
			]
		if kind == "stremio_catalog_items":
			addon = frame["addon"]
			metas = stremio_addon.fetch_catalog(addon, frame["content_type"], frame["catalog_id"])
			return [
				{"row_kind": "stremio_item", "label": m.get("name", m["id"]), "raw": m}
				for m in metas
			]
		if kind == "stremio_streams":
			# Aggregated across every installed add-on that declares
			# stream support for this content type (see
			# stremio_addon.fetch_all_streams()'s own docstring for why
			# this can't just be the one add-on whose catalog got browsed
			# here - confirmed live: Cinemeta's own real catalog items
			# have zero streams of their own, Torrentio provides them for
			# the same shared IMDB id instead).
			streams = stremio_addon.fetch_all_streams(frame["content_type"], frame["video_id"])
			rows = []
			for s in streams:
				url = stremio_addon.resolve_stream_url(s)
				name = s.get("name") or s.get("title") or s.get("description") or "(unnamed stream)"
				label = f"[{s['_addon_name']}] {name}"
				if url and url.startswith("magnet:"):
					label = f"[Torrent - not yet playable] {label}"
				rows.append({"row_kind": "stremio_stream", "label": label, "url": url})
			return rows
		return []

	def _refresh_media_page(self, media_type):
		nav = self.media_nav[media_type]
		frame = nav[-1]
		content_rows = self._media_frame_items(media_type, frame)
		rows = []
		if len(nav) > 1:
			rows.append({"row_kind": "back", "label": "‹ Back"})
		rows.extend(content_rows)

		empty_text = "Nothing here yet."
		if frame["kind"] == "repo_browse" and not content_rows:
			empty_text = "No add-ons available right now - check your network connection and try again."

		# Only when there's a real installed add-on row to act on - the
		# built-in Local Files add-on never shows up here at all (see
		# addon_backend.list_addons()'s own exclusion of it), so nothing
		# on this screen can ever hint at removing something that isn't
		# safe to remove.
		extra_hint = (
			"Delete: uninstall"
			if any(r.get("row_kind") in ("open_addon", "open_stremio_addon") for r in content_rows)
			else None
		)

		title = frame.get("title", MEDIA_TYPE_LABELS[media_type])
		page = make_list_page(title, rows, lambda r: r["label"], on_back=True, empty_text=empty_text, extra_hint=extra_hint)
		page.listbox.connect("row-activated", lambda _lb, row, mt=media_type: self._on_media_row_activated(mt, row.item))

		status_label = Gtk.Label(xalign=0)
		status_label.get_style_context().add_class("dim-label")
		page.pack_start(status_label, False, False, 0)
		page.show_all()
		self.media_status_labels[media_type] = status_label

		self.replace_page(f"{media_type}_categories", page)

	def _on_media_row_activated(self, media_type, row_data):
		nav = self.media_nav[media_type]
		current_frame = nav[-1]
		current_addon_id = current_frame.get("addon_id", addon_backend.BUILTIN_ADDON_ID)
		kind = row_data["row_kind"]

		if kind == "back":
			nav.pop()
		elif kind == "open_addons":
			# Video gets real Stremio add-ons; Music/Photos keep the
			# existing custom addon_backend/addon_repo system - see
			# stremio_addon.py's own docstring for why.
			if media_type == "video":
				nav.append({"kind": "stremio_addons_list", "title": "Add-ons"})
			else:
				nav.append({"kind": "addons_list", "title": "Add-ons"})
		elif kind == "open_stremio_addon":
			addon = next((a for a in stremio_addon.list_installed() if a["manifest_url"] == row_data["manifest_url"]), None)
			if addon is None:
				log.warning(f"stremio add-on {row_data['manifest_url']!r} vanished before it could be opened")
			else:
				nav.append({"kind": "stremio_catalogs", "addon": addon, "title": row_data["label"]})
		elif kind == "add_stremio_addon":
			self.open_add_stremio_addon_page()
			return  # a real separate page, not another frame on this nav stack
		elif kind == "stremio_catalog":
			nav.append(
				{
					"kind": "stremio_catalog_items",
					"addon": current_frame["addon"],
					"content_type": row_data["content_type"],
					"catalog_id": row_data["catalog_id"],
					"title": row_data["label"],
				}
			)
		elif kind == "stremio_item":
			# No "addon" key here - streams are aggregated across every
			# installed add-on that supports them (see
			# stremio_addon.fetch_all_streams()), not just the one whose
			# catalog led here.
			nav.append(
				{
					"kind": "stremio_streams",
					"content_type": current_frame["content_type"],
					"video_id": row_data["raw"]["id"],
					"title": row_data["label"],
				}
			)
		elif kind == "stremio_stream":
			self._play_stremio_stream(row_data["url"])
			return  # switching to the player page - don't re-render this list
		elif kind == "open_addon":
			addon_id, addon_name = row_data["id"], row_data["label"]
			# An add-on with no real categories (list_categories()/
			# list_albums() undefined or empty) skips straight to its own
			# top-level items instead of showing an empty categories page.
			if addon_backend.list_categories(media_type, addon_id):
				nav.append({"kind": "addon_categories", "addon_id": addon_id, "title": addon_name})
			else:
				nav.append({"kind": "items", "addon_id": addon_id, "category_id": None, "title": addon_name})
		elif kind in ("category", "folder"):
			nav.append({"kind": "items", "addon_id": current_addon_id, "category_id": row_data["id"], "title": row_data["label"]})
		elif kind == "leaf":
			self._play_media_item(media_type, current_addon_id, current_frame, row_data["raw"])
			return  # switching to the player/viewer page - don't re-render this list
		elif kind == "browse_repo":
			nav.append({"kind": "repo_browse", "title": "Get More Add-ons"})
		elif kind == "install_addon":
			self._install_repo_addon(media_type, row_data["entry"])
			return  # _on_addon_installed() re-renders once the download finishes

		self._refresh_media_page(media_type)

	def build_stremio_addon_add_page(self):
		"""A plain text-entry page for a Stremio add-on's own manifest URL
		- the OSK already works with any focused Gtk.Entry, no special
		wiring needed here. Built once; open_add_stremio_addon_page() just
		resets it before switching in."""
		box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=scaling.px_v(6))
		box.set_border_width(scaling.px_v(22))
		box.pack_start(make_heading("Add Stremio Add-on"), False, False, 0)

		hint_text = Gtk.Label(
			label="Paste a Stremio add-on's manifest URL (e.g. https://torrentio.strem.fun/manifest.json)",
			xalign=0,
			wrap=True,
		)
		hint_text.get_style_context().add_class("dim-label")
		box.pack_start(hint_text, False, False, 0)

		self.stremio_addon_url_entry = Gtk.Entry()
		self.stremio_addon_url_entry.connect("activate", self.on_stremio_addon_add_activate)
		box.pack_start(self.stremio_addon_url_entry, False, False, 0)

		self.stremio_addon_add_status = Gtk.Label(xalign=0, wrap=True)
		self.stremio_addon_add_status.get_style_context().add_class("dim-label")
		box.pack_start(self.stremio_addon_add_status, False, False, 0)

		hint = Gtk.Label(label="Type a URL, Enter to install   Backspace: back")
		hint.get_style_context().add_class("dim-label")
		box.pack_start(hint, False, False, 0)

		box.show_all()
		self.stack.add_named(box, "stremio_addon_add")

	def open_add_stremio_addon_page(self):
		self.stremio_addon_url_entry.set_text("")
		self.stremio_addon_add_status.set_text("")
		self.switch_to("stremio_addon_add")
		self.stremio_addon_url_entry.grab_focus()

	def on_stremio_addon_add_activate(self, entry):
		manifest_url = entry.get_text().strip()
		if not manifest_url:
			return
		entry.set_sensitive(False)
		self.stremio_addon_add_status.set_text(f"Installing {manifest_url}...")
		self.run_async(lambda: stremio_addon.install(manifest_url), self._on_stremio_addon_add_done)

	def _on_stremio_addon_add_done(self, result):
		ok, message = result
		self.stremio_addon_url_entry.set_sensitive(True)
		self.stremio_addon_add_status.set_text(message)
		if ok:
			# Back to Video's own "Add-ons" list, freshly refreshed so the
			# newly-installed add-on actually shows up - same "pop the
			# frame that led here" shape _on_addon_installed() already
			# uses for the old repo-browse flow.
			self._refresh_media_page("video")
			self.go_back()

	def _play_stremio_stream(self, url):
		"""url is whatever stremio_addon.resolve_stream_url() resolved a
		chosen Stream Object to - real, direct http(s) for most add-ons,
		but per direct instruction this can also be a real but currently
		unplayable magnet: URI (see stremio_addon.py's own docstring) or
		None (a malformed stream with neither url/infoHash/ytId - shown
		as an error rather than silently doing nothing)."""
		if url is None:
			self.media_status_labels["video"].set_text("This stream has no playable source.")
			return
		log.info(f"_play_stremio_stream: {url}")
		self.player_page.play(url, title="")
		self.switch_to("media_player")

	def _install_repo_addon(self, media_type, entry):
		display_name = entry.get("name", entry["id"])
		log.info(f"installing add-on {entry['id']!r} ({media_type}) from CosmOS-addons")
		self.stack.get_child_by_name(f"{media_type}_categories").listbox.set_sensitive(False)
		self.media_status_labels[media_type].set_text(f"Installing {display_name}...")
		self.run_async(lambda: addon_repo.install(entry), lambda result: self._on_addon_installed(media_type, result))

	def _on_addon_installed(self, media_type, result):
		ok, message = result
		if ok:
			self.media_nav[media_type].pop()  # back out of repo_browse to addons_list, which now includes it
		# _refresh_media_page() rebuilds the whole page - including a
		# brand new status_label - so the message has to be set AFTER
		# that call, on the new label, not the one on the page just torn
		# down by replace_page().
		self._refresh_media_page(media_type)
		self.media_status_labels[media_type].set_text(message)

	def delete_selected_addon(self, media_type):
		"""Only acts when the addons_list frame's own selected row is a
		real installed add-on ("open_addon" for Music/Photos'
		addon_backend-based add-ons, "open_stremio_addon" for Video's real
		Stremio ones) - a stray Delete press on "Get More Add-ons..."/
		"Add Add-on by URL..."/"‹ Back", or while looking at that add-on's
		own categories/items instead of the list of add-ons itself, is a
		no-op rather than something to guess at."""
		listbox = self.stack.get_child_by_name(f"{media_type}_categories").listbox
		row = listbox.get_selected_row()
		if row is None:
			return
		row_kind = row.item.get("row_kind")
		if row_kind == "open_addon":
			addon_id, addon_name = row.item["id"], row.item["label"]
			self.confirm(
				f"Uninstall {addon_name}?",
				"Removes this add-on completely. Local Files and anything already "
				"downloaded elsewhere are never touched.",
				lambda: self._do_delete_addon(media_type, addon_id, addon_name),
			)
		elif row_kind == "open_stremio_addon":
			manifest_url, addon_name = row.item["manifest_url"], row.item["label"]
			self.confirm(
				f"Uninstall {addon_name}?",
				"Removes this add-on from CosmOS. Nothing on the add-on's own "
				"server is affected - you can always add it back by URL.",
				lambda: self._do_delete_stremio_addon(manifest_url, addon_name),
			)

	def _do_delete_addon(self, media_type, addon_id, addon_name):
		log.info(f"uninstalling add-on {addon_id!r} ({media_type})")
		addon_backend.uninstall(media_type, addon_id)
		# Same ordering _on_addon_installed() already needs - refresh
		# first (a brand new status_label comes with it), only then set
		# the message on the label that actually exists afterward.
		self._refresh_media_page(media_type)
		self.media_status_labels[media_type].set_text(f"Uninstalled {addon_name}.")

	def _do_delete_stremio_addon(self, manifest_url, addon_name):
		log.info(f"uninstalling stremio add-on {addon_name!r} ({manifest_url})")
		stremio_addon.uninstall(manifest_url)
		self._refresh_media_page("video")
		self.media_status_labels["video"].set_text(f"Uninstalled {addon_name}.")

	def _current_leaf_items(self, media_type, addon_id, frame):
		items = addon_backend.list_items(media_type, addon_id, frame.get("category_id"))
		return [item for item in items if not item.get("is_folder")]

	def _play_media_item(self, media_type, addon_id, frame, item):
		log.info(f"_play_media_item: {media_type}/{addon_id}: {item.get('title')!r}")
		resolved = addon_backend.resolve(media_type, addon_id, item)
		if media_type == "photos":
			# Gather every other photo in this same frame (not just this
			# one item) so Previous/Next in photo_viewer.py can move
			# through the whole album, not just the single selected photo.
			siblings = [addon_backend.resolve(media_type, addon_id, i) for i in self._current_leaf_items(media_type, addon_id, frame)]
			start_index = next((i for i, s in enumerate(siblings) if s["id"] == item["id"]), 0)
			# switch_to() first - show_album() reads real allocation to
			# size the pixbuf, which a not-yet-visible Gtk.Stack child
			# doesn't have yet (confirmed live: it renders solid black,
			# scaled to a stale/near-zero allocation, until the page has
			# actually been made visible at least once).
			self.switch_to("photo_viewer")
			self.photo_viewer_page.show_album(siblings, start_index)
		else:
			self.player_page.play(resolved["path"], title=resolved.get("title", ""))
			self.switch_to("media_player")

	# -- usb save assignment -------------------------------------------------

	def build_usb_page(self):
		box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=scaling.px_v(6))
		box.set_border_width(scaling.px_v(22))
		box.pack_start(make_heading("Storage"), False, False, 0)

		self.usb_current_label = Gtk.Label(xalign=0)
		box.pack_start(self.usb_current_label, False, False, 0)

		scroller = Gtk.ScrolledWindow()
		scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
		scroller.set_can_focus(False)
		self.usb_listbox = Gtk.ListBox()
		self.usb_listbox.set_selection_mode(Gtk.SelectionMode.BROWSE)
		install_carets(self.usb_listbox)
		scroller.add(self.usb_listbox)
		box.pack_start(scroller, True, True, 0)

		self.usb_status_label = Gtk.Label(xalign=0)
		box.pack_start(self.usb_status_label, False, False, 0)

		hint = Gtk.Label(label="Up/Down: move   Enter: choose an action   Backspace: back")
		hint.get_style_context().add_class("dim-label")
		box.pack_start(hint, False, False, 0)

		self.usb_listbox.connect("row-activated", lambda _lb, row: self.open_usb_actions_page(row.item))
		box.listbox = self.usb_listbox
		box.show_all()
		self.stack.add_named(box, "usb")

		self.refresh_usb_list()
		GLib.timeout_add(USB_REFRESH_MS, self._usb_tick)

	def _usb_tick(self):
		if self.stack.get_visible_child_name() == "usb":
			self.refresh_usb_list()
		return True

	def refresh_usb_list(self):
		assignment = usb_saves.load_assignment()
		content_device = usb_content.active_content_device()
		status_bits = [f"Saves: {assignment['label']}" if assignment else "Saves: (none assigned)"]
		status_bits.append(f"Content source: {content_device}" if content_device else "Content source: (none active)")
		self.usb_current_label.set_text("   ".join(status_bits))

		selected_row = self.usb_listbox.get_selected_row()
		selected_index = selected_row.get_index() if selected_row else 0

		for row in list(self.usb_listbox.get_children()):
			self.usb_listbox.remove(row)

		partitions = usb_saves.list_removable_partitions()
		for partition in partitions:
			label = f"{partition['label']} ({partition['device_node']}, {partition['fs_type']})"
			self.usb_listbox.add(make_row(partition, label))
		self.usb_listbox.show_all()

		if partitions:
			index = min(selected_index, len(partitions) - 1)
			self.usb_listbox.select_row(self.usb_listbox.get_row_at_index(index))
		else:
			self.usb_status_label.set_text("No USB drives detected. Plug one in and reopen this screen.")

	def open_usb_actions_page(self, partition):
		"""Per-drive action list (same shape as the file manager's own
		build_fm_actions_page()/handle_fm_action()) - a row activation used
		to assign-for-saves immediately; now there are four independent
		things a drive can be used for, so it opens a choice instead."""
		is_content_source = partition["device_node"] == usb_content.active_content_device()
		actions = ["Assign for Saves"]
		actions.append("Stop Using as Content Source" if is_content_source else "Use as Content Source")
		if is_content_source:
			actions.append("Import ROMs/Media from this Drive")
		actions.append("Cancel")
		page = make_list_page(partition["label"], actions, lambda a: a, on_back=True)
		page.listbox.connect("row-activated", lambda _lb, row: self.handle_usb_action(partition, row.item))
		self.replace_page("usb_actions", page)
		self.switch_to("usb_actions")

	def handle_usb_action(self, partition, action):
		log.info(f"handle_usb_action: {action!r} on {partition['device_node']} ({partition['label']})")
		if action == "Assign for Saves":
			usb_saves.save_assignment(partition)
			synced = usb_saves.sync_all_saves()
			self.usb_status_label.set_text(
				f"Assigned {partition['label']}; synced saves for: {', '.join(synced) or '(no platforms configured)'}"
			)
			self.go_back()
			self.refresh_usb_list()
		elif action == "Use as Content Source":
			mounted = usb_content.mount_content_drive(partition)
			self.usb_status_label.set_text(
				f"{partition['label']} is now the content source." if mounted else f"Could not mount {partition['label']}."
			)
			self.go_back()
			self.refresh_usb_list()
		elif action == "Stop Using as Content Source":
			usb_content.unmount_content_drive()
			self.usb_status_label.set_text(f"{partition['label']} is no longer the content source.")
			self.go_back()
			self.refresh_usb_list()
		elif action == "Import ROMs/Media from this Drive":
			self.open_usb_import_picker()
		elif action == "Cancel":
			self.go_back()

	def open_usb_import_picker(self):
		"""One list of "where does this go" options, sourced from ROM
		platforms (self.shop_platforms - see library.list_rom_platforms()) plus the
		four media kinds."""
		options = [{"kind": "rom", "id": p["id"], "label": p["name"], "roms_dir": p["roms_dir"]} for p in self.shop_platforms]
		options += [{"kind": "media", "id": k, "label": f"{k.capitalize()} (media)"} for k in usb_content.MEDIA_DIRS]
		page = make_list_page("Import to...", options, lambda o: o["label"], on_back=True)
		page.listbox.connect("row-activated", lambda _lb, row: self._do_usb_import(row.item))
		self.replace_page("usb_import", page)
		self.switch_to("usb_import")

	def _do_usb_import(self, option):
		log.info(f"_do_usb_import: {option}")
		self.usb_status_label.set_text(f"Importing {option['label']}...")
		self.stack.get_child_by_name("usb_import").listbox.set_sensitive(False)
		if option["kind"] == "rom":
			work = lambda: usb_content.import_roms(option["id"], option["roms_dir"])  # noqa: E731
		else:
			work = lambda: usb_content.import_media(option["id"])  # noqa: E731
		self.run_async(work, lambda result: self._on_usb_import_done(option["label"], result))

	def _on_usb_import_done(self, label, result):
		self.stack.get_child_by_name("usb_import").listbox.set_sensitive(True)
		copied, skipped = result
		self.usb_status_label.set_text(f"{label}: imported {copied}, skipped {skipped} (already present).")
		self.go_back()  # back to usb_actions
		self.go_back()  # back to the drive list

	# -- settings menu -----------------------------------------------------

	def build_settings_page(self):
		items = [
			{"name": "Wi-Fi", "screen": "wifi"},
			{"name": "Bluetooth", "screen": "bluetooth"},
			{"name": "Display Resolution", "screen": "display"},
			{"name": "Theme", "screen": "theme"},
			{"name": "API", "screen": "api_settings"},
			{"name": "RetroAchievements Login", "screen": "ra_login"},
			{"name": "Install / Update CosmOS", "screen": "os_install"},
			# Not a CosmOS screen - launches a real external GUI tool
			# (packaging/millennium_installer/, built from source - see its
			# own module docstring) the same way Steam/Epic/GOG's own
			# systems.json entries do via launch_subprocess(). A one-time,
			# interactive setup step (pick the Steam path, click Install) -
			# there's nothing here for CosmOS itself to automate or show
			# progress for, unlike os_install's own real ceremony overlay.
			{
				"name": "Theme Steam (Millennium)",
				"command": ["{repo}/packaging/millennium_installer/millennium-installer"],
				"mode": "graphical",
			},
		]
		page = make_list_page("Settings", items, lambda e: e["name"], on_back=True)
		page.listbox.connect(
			"row-activated",
			lambda _lb, row: self.launch_subprocess(row.item) if "command" in row.item else self.switch_to(row.item["screen"]),
		)
		self.stack.add_named(page, "settings")

	def build_theme_page(self):
		"""Registered eagerly (like build_disc_page) so the "theme" stack
		name exists from the start; its actual content is rebuilt fresh
		via refresh_theme_page() every time the screen is entered (see
		on_enter_handlers), since which row reads "(current)" can change
		between visits."""
		self.stack.add_named(make_list_page("Theme", [], lambda t: t, on_back=True), "theme")

	def refresh_theme_page(self):
		"""Per-profile theme selection (see theme.py's load_theme() and
		profiles.get_theme()/set_theme()) - a plain list of every palette
		file under launcher/themes/."""
		current = profiles.get_theme() or theme.DEFAULT_THEME
		labels = {t: (f"{t.replace('_', ' ').title()} (current)" if t == current else t.replace("_", " ").title()) for t in theme.list_themes()}
		page = make_list_page("Theme", list(labels), lambda t: labels[t], on_back=True)
		page.listbox.connect("row-activated", lambda _lb, row: self.handle_theme_row(row.item))
		self.replace_page("theme", page)

	def handle_theme_row(self, theme_id):
		log.info(f"theme {theme_id!r} selected")
		profiles.set_theme(theme_id)
		self.go_back()
		if self.restart_requested is not None:
			self.restart_requested.set()

	def build_api_settings_page(self):
		"""Registered eagerly (like build_theme_page)
		so the "api_settings" stack name exists from the start;
		refresh_api_settings_page() (see on_enter_handlers) rebuilds the
		actual row list on every visit. A real submenu (Settings > API >
		IGDB / Real-Debrid), not a leaf itself - each row below navigates
		one level deeper to that credential's own existing entry screen
		(igdb/realdebrid), which need no changes themselves: go_back()
		works purely off the shared history stack, not a hardcoded parent
		per page, so nesting an existing leaf one level deeper "just
		works." Every project-wide API credential a user might actually
		configure lives here (there is no embedded GitHub credential any more),
		and RetroAchievements Login
		stays its own top-level Settings row rather than folding in here -
		a different shape (username+one-time password+API key, per-profile
		rather than OS-wide) than the plain "one API key" rows below."""
		self.stack.add_named(make_list_page("API", [], lambda t: t, on_back=True), "api_settings")

	def refresh_api_settings_page(self):
		items = ["IGDB", "Real-Debrid", "Save Sync (Dropbox)", "ScreenScraper (Skyscraper)"]
		targets = {
			"IGDB": "igdb",
			"Real-Debrid": "realdebrid",
			"Save Sync (Dropbox)": "save_sync",
			"ScreenScraper (Skyscraper)": "screenscraper",
		}
		page = make_list_page("API", items, lambda t: t, on_back=True)
		page.listbox.connect("row-activated", lambda _lb, row: self.switch_to(targets[row.item]))
		self.replace_page("api_settings", page)

	# -- power: Logout / Restart / Shutdown ---------------------------------
	#
	# Its own top-level utility icon (config.ini's [Power], not nested under
	# Settings) - a system power action is common enough on any console/
	# appliance UI to earn its own button rather than a click deeper, the
	# same reasoning Video/Music/Photos already got their own top-level
	# entries instead of living under one "Media" menu.

	def build_power_page(self):
		items = ["Exit to Desktop", "Restart", "Shutdown"]
		page = make_list_page("Power", items, lambda e: e, on_back=True)
		page.listbox.connect("row-activated", lambda _lb, row: self.handle_power_action(row.item))

		self.power_status_label = Gtk.Label(xalign=0)
		self.power_status_label.get_style_context().add_class("dim-label")
		page.pack_start(self.power_status_label, False, False, 0)
		page.show_all()

		self.stack.add_named(page, "power")

	def handle_power_action(self, action):
		log.info(f"handle_power_action: {action!r}")
		if action == "Exit to Desktop":
			# quit() just ends this process (Gtk.main_quit()) - the gridscreen and bar were
			# only ever hidden while GameBox ran (see cosmos-session-apps/launch-gamebox), never
			# killed, so they reappear the moment it exits. Framed here as what it actually does
			# (switches back to the desktop, still logged in) - this used to be labeled "Logout"
			# with "returns to a fresh session," which read as ending the whole session and was
			# the one and only way back to the gridscreen, so nobody trusted it enough to use it
			# for that (a real report: "no way to exit back to grid screen").
			self.confirm(
				"Exit to the desktop?",
				"Closes GameBox and shows the gridscreen - you stay logged in.",
				self.quit,
			)
		elif action == "Restart":
			self.confirm(
				"Restart the system?",
				"Reboots the whole machine, not just CosmOS. Anything currently "
				"running (a game, a download) is stopped.",
				self._run_restart,
			)
		elif action == "Shutdown":
			self.confirm(
				"Shut down the system?",
				"Powers off the whole machine. Anything currently running (a game, "
				"a download) is stopped.",
				self._run_shutdown,
			)

	def _run_restart(self):
		"""Same shape as _run_bootc_upgrade()/_run_bootc_rollback() below -
		the ceremony overlay's "don't touch it" framing fits a real reboot
		just as well as a bootc one, and on_done here is realistically only
		ever a failure path (a successful `systemctl reboot` takes this
		whole process down with it before it could return)."""
		self._show_ceremony("Restarting")

		def on_done(returncode):
			self._hide_ceremony()
			self.power_status_label.set_text(f"Restart failed (exit {returncode}).")

		self.run_streamed(["systemctl", "reboot"], self._ceremony_log_line, on_done)

	def _run_shutdown(self):
		self._show_ceremony("Shutting Down")

		def on_done(returncode):
			self._hide_ceremony()
			self.power_status_label.set_text(f"Shutdown failed (exit {returncode}).")

		self.run_streamed(["systemctl", "poweroff"], self._ceremony_log_line, on_done)

	# -- settings: Install / Update CosmOS ---------------------------------
	#
	# bootc (see atomic/Containerfile) natively replaces the custom
	# download-an-ISO/repartition ritual the older Debian A/B pipeline
	# needed its own image/installer.py for - `bootc upgrade`/`bootc
	# rollback` handle both "fetch and apply an update" and "go back to the
	# previous version" as single, well-tested real actions, no disk-picking
	# or live-vs-installed distinction needed at all (bootc/ostree already
	# knows exactly what state this system is in). The Debian pipeline (and
	# this screen's own former dual-mode installer.py branch) has been
	# retired outright, per direct request - bootc is the one real target
	# now.

	def build_os_install_page(self):
		box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=scaling.px_v(6))
		box.set_border_width(scaling.px_v(22))
		box.pack_start(make_heading("Install / Update CosmOS"), False, False, 0)

		self.os_install_status_label = Gtk.Label(xalign=0)
		self.os_install_status_label.set_line_wrap(True)
		box.pack_start(self.os_install_status_label, False, False, 0)

		scroller = Gtk.ScrolledWindow()
		scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
		scroller.set_can_focus(False)
		self.os_install_listbox = Gtk.ListBox()
		self.os_install_listbox.set_selection_mode(Gtk.SelectionMode.BROWSE)
		install_carets(self.os_install_listbox)
		self.os_install_listbox.connect("row-activated", lambda _lb, row: self.activate_os_install_row(row.item))
		scroller.add(self.os_install_listbox)
		box.pack_start(scroller, True, True, 0)

		log_scroller = Gtk.ScrolledWindow()
		log_scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
		log_scroller.set_can_focus(False)
		log_scroller.set_size_request(-1, scaling.px(220))
		self.os_install_log_buffer = Gtk.TextBuffer()
		self.os_install_log_view = Gtk.TextView(buffer=self.os_install_log_buffer)
		self.os_install_log_view.set_editable(False)
		self.os_install_log_view.set_monospace(True)
		self.os_install_log_view.set_can_focus(False)
		log_scroller.add(self.os_install_log_view)
		box.pack_start(log_scroller, False, False, 0)

		hint = Gtk.Label(label="Up/Down: move   Enter: select   Backspace: back")
		hint.get_style_context().add_class("dim-label")
		box.pack_start(hint, False, False, 0)

		box.listbox = self.os_install_listbox
		box.show_all()
		self.stack.add_named(box, "os_install")

	# `bootc` (see atomic/Containerfile) means this is the Fedora Atomic/
	# bootc-based image - checking for the real binary rather than
	# assuming, since a bare dev-machine checkout (no bootc installed at
	# all) should still show a sensible "nothing to do here" message
	# instead of erroring.
	BOOTC_PATH = Path("/usr/bin/bootc")

	def _bootc_available(self):
		return self.BOOTC_PATH.exists()

	def _bootc_status(self):
		"""Parses `bootc status --format json` - real field shapes
		confirmed directly against a real build of this image (`status.
		booted`/`status.staged`/`status.rollback`, each null or a real
		deployment object with its own `image.image.image` ref and
		`version`). Returns None on any failure (not a bootc system after
		all, subprocess/JSON error, ...) - every caller already treats
		that the same as "nothing else to show" rather than crashing.

		Run through a plain `ls /boot` first, same real reason
		_run_bootc_upgrade()'s own BOOTC_BOOT_AUTOMOUNT_WORKAROUND exists
		(bootc-dev/bootc#2402) - this is called every time Settings is
		opened (see refresh_os_install_list()), almost always well past
		the automount's own 2-minute idle window in real use, not just
		right after boot."""
		try:
			result = subprocess.run(
				["bash", "-c", f"{self.BOOTC_BOOT_AUTOMOUNT_WORKAROUND}; bootc status --format json"],
				capture_output=True,
				text=True,
				timeout=15,
				check=True,
			)
			return json.loads(result.stdout)
		except (subprocess.SubprocessError, OSError, json.JSONDecodeError) as e:
			log.warning(f"_bootc_status: {e}")
			return None

	def refresh_os_install_list(self):
		for row in list(self.os_install_listbox.get_children()):
			self.os_install_listbox.remove(row)
		self.os_install_log_buffer.set_text("")

		if self._bootc_available():
			self._refresh_bootc_update_list()
			self.os_install_listbox.show_all()
			first_row = self.os_install_listbox.get_row_at_index(0)
			if first_row:
				self.os_install_listbox.select_row(first_row)
			return

		self.os_install_status_label.set_text("bootc is not present on this system - nothing to do here.")

	def _refresh_bootc_update_list(self):
		"""bootc's own update mechanism - the whole reason this project
		switched to Fedora Atomic/bootc in the first place: `bootc
		upgrade`/`bootc rollback` natively handle "fetch and apply an
		update" and "go back to the previous version" as single, real
		actions, no disk-picking or live-vs-installed distinction needed
		at all. Manual-only, triggered from here - never automatic - and
		behind the same real confirm() dialog every other real disk/OS
		action in this page already uses."""
		status = self._bootc_status()
		booted = (status or {}).get("status", {}).get("booted") or {}
		booted_image = booted.get("image") or {}
		image_ref = (booted_image.get("image") or {}).get("image")
		version = booted_image.get("version")
		if image_ref:
			self.os_install_status_label.set_text(f"Running: {image_ref}" + (f" ({version})" if version else ""))
		else:
			self.os_install_status_label.set_text("Fedora Atomic/bootc - unable to read the current image status.")

		self.os_install_listbox.add(make_row({"kind": "bootc-check"}, "Check for Update"))
		self.os_install_listbox.add(make_row({"kind": "bootc-upgrade"}, "Install Update"))
		rollback = (status or {}).get("status", {}).get("rollback")
		if rollback:
			rollback_ref = ((rollback.get("image") or {}).get("image") or {}).get("image") or "previous deployment"
			self.os_install_listbox.add(
				make_row({"kind": "bootc-rollback"}, f"Roll Back to Previous Version ({rollback_ref})")
			)

	def activate_os_install_row(self, item):
		if item.get("kind") == "bootc-check":
			self._run_bootc_check()
			return
		if item.get("kind") == "bootc-upgrade":
			self.confirm(
				"Install the available update?",
				"Downloads the new OS image and applies it, then reboots automatically once "
				"it's ready. The current version stays available as a fallback if the new "
				"one ever fails to boot cleanly (bootc rollback). Userdata is never touched.",
				self._run_bootc_upgrade,
			)
			return
		if item.get("kind") == "bootc-rollback":
			self.confirm(
				"Roll back to the previous version?",
				"Reboots into the deployment that was active before the last update. "
				"Userdata is never touched.",
				self._run_bootc_rollback,
			)
			return

	def _run_bootc_check(self):
		"""`bootc upgrade --check` only fetches updated metadata (its own
		--help: "This only downloads updated metadata, not the full image
		layers") - quick and non-disruptive, so this reuses the page's own
		small log view rather than the full-screen ceremony reserved for
		an actual upgrade/rollback."""
		self.os_install_log_buffer.set_text("")
		self.os_install_listbox.set_sensitive(False)
		self.os_install_status_label.set_text("Checking for an update...")

		def on_line(text):
			end_iter = self.os_install_log_buffer.get_end_iter()
			self.os_install_log_buffer.insert(end_iter, text + "\n")
			self.os_install_log_view.scroll_to_iter(self.os_install_log_buffer.get_end_iter(), 0, False, 0, 0)

		def on_done(returncode):
			self.os_install_listbox.set_sensitive(True)
			self.os_install_status_label.set_text(
				"Check complete - see the log above." if returncode == 0 else f"Check failed (exit {returncode}) - see the log above."
			)

		self.run_streamed(
			self._bootc_admin_argv("upgrade", "--check"), on_line, on_done
		)

	# A real, open upstream bootc bug (bootc-dev/bootc#2402), hit live
	# testing this exact update ritual: /boot is a systemd-gpt-auto-
	# generator automount with a 120s idle timeout, and bootc's own
	# access to it (re-exec'd under `unshare -m`) does NOT trigger that
	# automount the way a normal process's access does - so any real
	# `bootc upgrade`/`bootc status`/`bootc rollback` run more than ~2
	# minutes after boot fails outright with an opaque "opendir(boot):
	# Operation not permitted", not a real problem with the update
	# itself. Confirmed the documented working workaround directly:
	# touching /boot from a NORMAL (non-unshare'd) shell command
	# immediately beforehand re-triggers the automount, after which the
	# very next bootc invocation succeeds. `ls /boot` (harmless, real,
	# and always available - not touch, which would need write access)
	# covers both call sites below.
	BOOTC_BOOT_AUTOMOUNT_WORKAROUND = "ls /boot >/dev/null 2>&1"

	def _bootc_admin_argv(self, *bootc_args):
		"""bootc as root. On CosmOS that is the kronos admin service (its os.upgrade / os.rollback actions already do the /boot wake-up
		below; this account has no sudo); anywhere else it is sudo, with the wake-up in front."""
		if shutil.which("kronos"):
			return ["kronos", "exec", "--", "bootc", *bootc_args]
		return ["sudo", "bash", "-c", f"{self.BOOTC_BOOT_AUTOMOUNT_WORKAROUND}; bootc {' '.join(bootc_args)}"]

	def _run_bootc_upgrade(self):
		"""`bootc upgrade --apply` fetches the new image, stages it, and
		reboots into it automatically once ready (its own --help:
		"Restart or reboot into the new target image... this always
		reboots") - one real action covers this project's whole "manual,
		from Settings, full-screen takeover with a progress bar" design
		decision for the update ritual, with no separate reboot-prompt
		step needed. on_done below only really fires on a real FAILURE
		(a successful run reboots the machine out from under this process
		instead of ever returning)."""
		self._show_ceremony("Installing Update")

		def on_done(returncode):
			self._hide_ceremony()
			self.os_install_status_label.set_text(f"Update failed (exit {returncode}) - see the log above.")
			self.os_install_log_buffer.set_text(self.ceremony_log_buffer.get_text(*self.ceremony_log_buffer.get_bounds(), True))
			self.refresh_os_install_list()

		self.run_streamed(
			self._bootc_admin_argv("upgrade", "--apply"),
			self._ceremony_log_line,
			on_done,
		)

	def _run_bootc_rollback(self):
		"""`bootc rollback --apply` swaps the boot order back to the
		previous deployment and reboots into it automatically - same
		one-action-covers-everything shape as _run_bootc_upgrade() above,
		same reasoning for why on_done here is really only a failure path."""
		self._show_ceremony("Rolling Back")

		def on_done(returncode):
			self._hide_ceremony()
			self.os_install_status_label.set_text(f"Rollback failed (exit {returncode}) - see the log above.")
			self.os_install_log_buffer.set_text(self.ceremony_log_buffer.get_text(*self.ceremony_log_buffer.get_bounds(), True))
			self.refresh_os_install_list()

		self.run_streamed(
			self._bootc_admin_argv("rollback", "--apply"),
			self._ceremony_log_line,
			on_done,
		)

	# -- settings: Wi-Fi -----------------------------------------------------

	def build_wifi_page(self):
		box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=scaling.px_v(6))
		box.set_border_width(scaling.px_v(22))
		box.pack_start(make_heading("Wi-Fi"), False, False, 0)

		self.wifi_status_label = Gtk.Label(xalign=0)
		box.pack_start(self.wifi_status_label, False, False, 0)

		scroller = Gtk.ScrolledWindow()
		scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
		scroller.set_can_focus(False)
		self.wifi_listbox = Gtk.ListBox()
		self.wifi_listbox.set_selection_mode(Gtk.SelectionMode.BROWSE)
		install_carets(self.wifi_listbox)
		scroller.add(self.wifi_listbox)
		box.pack_start(scroller, True, True, 0)

		self.wifi_hint = Gtk.Label(label="Scanning...")
		self.wifi_hint.get_style_context().add_class("dim-label")
		box.pack_start(self.wifi_hint, False, False, 0)

		self.wifi_listbox.connect("row-activated", lambda _lb, row: self.select_wifi_network(row.item))
		box.listbox = self.wifi_listbox
		box.show_all()
		self.stack.add_named(box, "wifi")

	def start_wifi_scan(self):
		self.wifi_hint.set_text("Scanning...")
		self.run_async(wifi.scan, self.on_wifi_scan_done)

	def on_wifi_scan_done(self, networks):
		current = wifi.current_ssid()
		for row in list(self.wifi_listbox.get_children()):
			self.wifi_listbox.remove(row)
		for net in networks:
			lock = "  (secured)" if net["secured"] else ""
			mark = "  [connected]" if net["ssid"] == current else ""
			self.wifi_listbox.add(make_row(net, f"{net['ssid']}  signal {net['signal']}%{lock}{mark}"))
		self.wifi_listbox.show_all()
		if networks:
			self.wifi_listbox.select_row(self.wifi_listbox.get_row_at_index(0))
		self.wifi_status_label.set_text(f"Connected: {current}" if current else "Not connected")
		self.wifi_hint.set_text("Up/Down: move   Enter: connect   Backspace: back")
		return False

	def select_wifi_network(self, network):
		log.info(f"select_wifi_network: {network['ssid']} (secured={network['secured']})")
		if network["secured"]:
			self.pending_wifi_ssid = network["ssid"]
			self.wifi_password_heading.label_widget.set_text(f"Password for {network['ssid']}")
			self.wifi_password_entry.set_text("")
			self.wifi_password_status.set_text("")
			self.switch_to("wifi_password")
		else:
			self.wifi_hint.set_text(f"Connecting to {network['ssid']}...")
			self.run_async(lambda: wifi.connect(network["ssid"]), self.on_wifi_connect_done)

	def on_wifi_connect_done(self, result):
		ok, message = result
		current = wifi.current_ssid()
		self.wifi_status_label.set_text(f"Connected: {current}" if current else "Not connected")
		self.wifi_hint.set_text(("Connected." if ok else f"Failed: {message}") + "   Backspace: back")
		return False

	def build_wifi_password_page(self):
		box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=scaling.px_v(10))
		box.set_border_width(scaling.px_v(22))
		self.wifi_password_heading = make_heading("Wi-Fi Password")
		box.pack_start(self.wifi_password_heading, False, False, 0)

		self.wifi_password_entry = Gtk.Entry()
		self.wifi_password_entry.set_visibility(False)
		self.wifi_password_entry.connect("activate", lambda _e: self.submit_wifi_password())
		box.pack_start(self.wifi_password_entry, False, False, 0)

		self.wifi_password_status = Gtk.Label(xalign=0)
		self.wifi_password_status.get_style_context().add_class("dim-label")
		box.pack_start(self.wifi_password_status, False, False, 0)

		hint = Gtk.Label(label="Enter: connect   Escape: back")
		hint.get_style_context().add_class("dim-label")
		box.pack_start(hint, False, False, 0)

		box.focus_widget = self.wifi_password_entry
		box.show_all()
		self.stack.add_named(box, "wifi_password")

	def submit_wifi_password(self):
		# Not logging the password itself, only that one was submitted.
		password = self.wifi_password_entry.get_text()
		log.info(f"submit_wifi_password: connecting to {self.pending_wifi_ssid}")
		self.wifi_password_status.set_text("Connecting...")
		self.run_async(lambda: wifi.connect(self.pending_wifi_ssid, password), self.on_wifi_password_connect_done)

	def on_wifi_password_connect_done(self, result):
		ok, message = result
		self.wifi_password_status.set_text("Connected." if ok else f"Failed: {message}")
		if ok:
			self.go_back()
			self.start_wifi_scan()
		return False

	# -- settings: Bluetooth -------------------------------------------------

	def build_bluetooth_page(self):
		box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=scaling.px_v(6))
		box.set_border_width(scaling.px_v(22))
		box.pack_start(make_heading("Bluetooth"), False, False, 0)

		self.bt_status_label = Gtk.Label(xalign=0)
		box.pack_start(self.bt_status_label, False, False, 0)

		scroller = Gtk.ScrolledWindow()
		scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
		scroller.set_can_focus(False)
		self.bt_listbox = Gtk.ListBox()
		self.bt_listbox.set_selection_mode(Gtk.SelectionMode.BROWSE)
		install_carets(self.bt_listbox)
		scroller.add(self.bt_listbox)
		box.pack_start(scroller, True, True, 0)

		self.bt_hint = Gtk.Label(label="Scanning...")
		self.bt_hint.get_style_context().add_class("dim-label")
		box.pack_start(self.bt_hint, False, False, 0)

		self.bt_listbox.connect("row-activated", lambda _lb, row: self.select_bluetooth_device(row.item))
		box.listbox = self.bt_listbox
		box.show_all()
		self.stack.add_named(box, "bluetooth")

	def start_bluetooth_scan(self):
		for row in list(self.bt_listbox.get_children()):
			self.bt_listbox.remove(row)
		if not bluetooth.is_powered():
			self.bt_status_label.set_text("Bluetooth is powered off")
			self.bt_hint.set_text("Backspace: back")
			return
		self.bt_status_label.set_text("Powered on")
		self.bt_hint.set_text("Scanning...")
		self.run_async(_scan_bluetooth_with_paired, self.on_bluetooth_scan_done)

	def on_bluetooth_scan_done(self, result):
		found, paired = result
		for row in list(self.bt_listbox.get_children()):
			self.bt_listbox.remove(row)
		for device in found:
			mark = "  [paired]" if device["mac"] in paired else ""
			self.bt_listbox.add(make_row(device, f"{device['name']}{mark}"))
		self.bt_listbox.show_all()
		if found:
			self.bt_listbox.select_row(self.bt_listbox.get_row_at_index(0))
		self.bt_hint.set_text("Up/Down: move   Enter: pair/connect   Backspace: back")
		return False

	def select_bluetooth_device(self, device):
		log.info(f"select_bluetooth_device: {device['mac']} ({device['name']})")
		self.bt_hint.set_text(f"Pairing with {device['name']}...")
		self.run_async(lambda: bluetooth.pair_and_connect(device["mac"]), self.on_bluetooth_pair_done)

	def on_bluetooth_pair_done(self, result):
		ok, _message = result
		self.bt_hint.set_text(("Paired and connected." if ok else "Failed to pair.") + "   Backspace: back")
		return False

	# -- settings: display resolution ----------------------------------------

	def build_display_page(self):
		box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=scaling.px_v(6))
		box.set_border_width(scaling.px_v(22))
		box.pack_start(make_heading("Display Resolution"), False, False, 0)

		scroller = Gtk.ScrolledWindow()
		scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
		scroller.set_can_focus(False)
		self.display_listbox = Gtk.ListBox()
		self.display_listbox.set_selection_mode(Gtk.SelectionMode.BROWSE)
		install_carets(self.display_listbox)
		scroller.add(self.display_listbox)
		box.pack_start(scroller, True, True, 0)

		hint = Gtk.Label(label="Up/Down: move   Enter: apply   Backspace: back")
		hint.get_style_context().add_class("dim-label")
		box.pack_start(hint, False, False, 0)

		self.display_listbox.connect("row-activated", lambda _lb, row: self.select_display_mode(row.item))
		box.listbox = self.display_listbox
		box.show_all()
		self.stack.add_named(box, "display")

	def refresh_display_list(self):
		for row in list(self.display_listbox.get_children()):
			self.display_listbox.remove(row)
		modes = display_settings.list_modes()
		for mode in modes:
			label = mode["mode"] + ("  [current]" if mode["current"] else "")
			self.display_listbox.add(make_row(mode, label))
		self.display_listbox.show_all()
		if modes:
			current_index = next((i for i, m in enumerate(modes) if m["current"]), 0)
			self.display_listbox.select_row(self.display_listbox.get_row_at_index(current_index))

	def select_display_mode(self, mode):
		log.info(f"select_display_mode: {mode['mode']}")
		self.previous_display_mode = next(
			(m["mode"] for m in display_settings.list_modes() if m["current"]), None
		)
		if mode["mode"] == self.previous_display_mode:
			log.debug("selected mode is already current, no-op")
			return
		display_settings.set_mode(mode["mode"])
		self.pending_display_mode = mode["mode"]
		self.build_resolution_confirm_page()
		self.switch_to("resolution_confirm")

	def build_resolution_confirm_page(self):
		box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=scaling.px_v(10))
		box.set_border_width(scaling.px_v(22))
		box.pack_start(make_heading("Keep this resolution?"), False, False, 0)

		label = Gtk.Label(xalign=0, wrap=True)
		label.set_text(
			f"Switched to {self.pending_display_mode}. Reverting to {self.previous_display_mode} in "
			f"{RESOLUTION_REVERT_SECONDS}s unless you keep it."
		)
		box.pack_start(label, False, False, 0)

		hint = Gtk.Label(label="Enter: keep   Escape/Backspace: revert now")
		hint.get_style_context().add_class("dim-label")
		box.pack_start(hint, False, False, 0)

		box.show_all()
		self.replace_page("resolution_confirm", box)
		self.resolution_revert_timeout = GLib.timeout_add_seconds(RESOLUTION_REVERT_SECONDS, self.revert_display_mode)

	def _cancel_resolution_revert_timeout(self):
		if self.resolution_revert_timeout:
			GLib.source_remove(self.resolution_revert_timeout)
			self.resolution_revert_timeout = None

	def keep_display_mode(self):
		log.info(f"keep_display_mode: keeping {self.pending_display_mode}")
		self._cancel_resolution_revert_timeout()
		self.go_back()
		self.refresh_display_list()

	def revert_display_mode(self):
		log.info(f"revert_display_mode: reverting to {self.previous_display_mode}")
		self._cancel_resolution_revert_timeout()
		if self.previous_display_mode:
			display_settings.set_mode(self.previous_display_mode)
		self.go_back()
		self.refresh_display_list()
		return False

	# -- settings: IGDB API credentials --------------------------------------
	#
	# Replaceable, but never displayed back - per direct instruction, the
	# fields never show whatever's currently saved (unlike Wi-Fi's own
	# password field, which at least has real content to hide behind
	# dots; here the fix is to just never load the stored value into the
	# widget at all, in either field, not only the already-masked secret
	# one). Entering this screen (see on_enter_handlers) always starts
	# from two blank fields ready to type a *replacement* - typing and
	# saving overwrites whatever igdb.py already had stored, but reading
	# it back is never possible from this screen, this session or any
	# other.

	def build_igdb_page(self):
		box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=scaling.px_v(10))
		box.set_border_width(scaling.px_v(22))
		box.pack_start(make_heading("IGDB API Credentials"), False, False, 0)

		box.pack_start(Gtk.Label(label="New Client ID", xalign=0), False, False, 0)
		self.igdb_client_id_entry = Gtk.Entry()
		box.pack_start(self.igdb_client_id_entry, False, False, 0)

		box.pack_start(Gtk.Label(label="New Client Secret", xalign=0), False, False, 0)
		self.igdb_client_secret_entry = Gtk.Entry()
		self.igdb_client_secret_entry.set_visibility(False)
		box.pack_start(self.igdb_client_secret_entry, False, False, 0)

		self.igdb_status_label = Gtk.Label(xalign=0)
		self.igdb_status_label.get_style_context().add_class("dim-label")
		box.pack_start(self.igdb_status_label, False, False, 0)

		hint = Gtk.Label(label="Tab: next field   Enter: save   Escape: back")
		hint.get_style_context().add_class("dim-label")
		box.pack_start(hint, False, False, 0)

		self.igdb_client_id_entry.connect("activate", lambda _e: self.igdb_client_secret_entry.grab_focus())
		self.igdb_client_secret_entry.connect("activate", lambda _e: self.save_igdb_credentials())

		box.focus_widget = self.igdb_client_id_entry
		box.show_all()
		self.stack.add_named(box, "igdb")

	def refresh_igdb_page(self):
		"""Always starts blank (see this section's own comment) - called on
		every entry (see on_enter_handlers), not just at startup, so
		backing out mid-edit without saving never leaves a stale typed
		value sitting on screen for a later visit either."""
		self.igdb_client_id_entry.set_text("")
		self.igdb_client_secret_entry.set_text("")
		self.igdb_status_label.set_text("")
		self.igdb_client_id_entry.grab_focus()

	def save_igdb_credentials(self):
		log.info("save_igdb_credentials")
		igdb.save(self.igdb_client_id_entry.get_text(), self.igdb_client_secret_entry.get_text())
		self.igdb_client_id_entry.set_text("")
		self.igdb_client_secret_entry.set_text("")
		self.igdb_status_label.set_text("Saved.")

	def build_realdebrid_page(self):
		"""Single-field sibling of build_igdb_page - Real-Debrid is just
		one API key (see settings/realdebrid.py's own docstring: unlike
		IGDB, there's no project-owned default to fall back to, since a
		Real-Debrid account is inherently personal/paid), so this skips
		the client-id/client-secret two-field shape entirely."""
		box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=scaling.px_v(10))
		box.set_border_width(scaling.px_v(22))
		box.pack_start(make_heading("Real-Debrid API Key"), False, False, 0)

		box.pack_start(Gtk.Label(label="New API Key", xalign=0), False, False, 0)
		self.realdebrid_key_entry = Gtk.Entry()
		self.realdebrid_key_entry.set_visibility(False)
		box.pack_start(self.realdebrid_key_entry, False, False, 0)

		self.realdebrid_status_label = Gtk.Label(xalign=0)
		self.realdebrid_status_label.get_style_context().add_class("dim-label")
		box.pack_start(self.realdebrid_status_label, False, False, 0)

		hint = Gtk.Label(label="Enter: save   Escape: back")
		hint.get_style_context().add_class("dim-label")
		box.pack_start(hint, False, False, 0)

		self.realdebrid_key_entry.connect("activate", lambda _e: self.save_realdebrid_credentials())

		box.focus_widget = self.realdebrid_key_entry
		box.show_all()
		self.stack.add_named(box, "realdebrid")

	def refresh_realdebrid_page(self):
		"""Always starts blank, same reasoning as refresh_igdb_page's own
		identical comment."""
		self.realdebrid_key_entry.set_text("")
		self.realdebrid_status_label.set_text("")
		self.realdebrid_key_entry.grab_focus()

	def save_realdebrid_credentials(self):
		log.info("save_realdebrid_credentials")
		realdebrid.save(self.realdebrid_key_entry.get_text())
		self.realdebrid_key_entry.set_text("")
		self.realdebrid_status_label.set_text("Saved.")

	def build_screenscraper_page(self):
		"""Two-field sibling of build_igdb_page (username+password, like
		IGDB's client-id+client-secret) plus one extra element neither
		IGDB nor Real-Debrid have: a manual "Scrape All Games Now" button
		(library/skyscraper_scraper.py's own bulk trigger, run via the
		same run_async()-backed status-label pattern app.py's Save Sync
		page uses for its own manual actions) - per direct instruction,
		the Skyscraper scrape is triggered manually from here (the
		Shop's game downloads no longer trigger one)."""
		box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=scaling.px_v(10))
		box.set_border_width(scaling.px_v(22))
		box.pack_start(make_heading("ScreenScraper Account"), False, False, 0)

		box.pack_start(Gtk.Label(label="New Username", xalign=0), False, False, 0)
		self.screenscraper_username_entry = Gtk.Entry()
		box.pack_start(self.screenscraper_username_entry, False, False, 0)

		box.pack_start(Gtk.Label(label="New Password", xalign=0), False, False, 0)
		self.screenscraper_password_entry = Gtk.Entry()
		self.screenscraper_password_entry.set_visibility(False)
		box.pack_start(self.screenscraper_password_entry, False, False, 0)

		self.screenscraper_status_label = Gtk.Label(xalign=0)
		self.screenscraper_status_label.get_style_context().add_class("dim-label")
		box.pack_start(self.screenscraper_status_label, False, False, 0)

		hint = Gtk.Label(label="Tab: next field   Enter: save   Escape: back")
		hint.get_style_context().add_class("dim-label")
		box.pack_start(hint, False, False, 0)

		self.screenscraper_username_entry.connect("activate", lambda _e: self.screenscraper_password_entry.grab_focus())
		self.screenscraper_password_entry.connect("activate", lambda _e: self.save_screenscraper_credentials())

		scrape_button = Gtk.Button(label="Scrape All Games Now")
		scrape_button.connect("clicked", lambda _b: self.do_screenscraper_scrape_all())
		box.pack_start(scrape_button, False, False, scaling.px_v(10))

		self.screenscraper_scrape_status = Gtk.Label(xalign=0)
		self.screenscraper_scrape_status.get_style_context().add_class("dim-label")
		self.screenscraper_scrape_status.set_line_wrap(True)
		box.pack_start(self.screenscraper_scrape_status, False, False, 0)

		box.focus_widget = self.screenscraper_username_entry
		box.show_all()
		self.stack.add_named(box, "screenscraper")

	def refresh_screenscraper_page(self):
		"""Always starts blank, same reasoning as refresh_igdb_page's own
		identical comment - the scrape-all status line is left alone
		though, so a scrape still in flight (or its last result) stays
		visible across a quick back-and-forth into this page."""
		self.screenscraper_username_entry.set_text("")
		self.screenscraper_password_entry.set_text("")
		self.screenscraper_status_label.set_text("")
		self.screenscraper_username_entry.grab_focus()

	def save_screenscraper_credentials(self):
		log.info("save_screenscraper_credentials")
		screenscraper.save(self.screenscraper_username_entry.get_text(), self.screenscraper_password_entry.get_text())
		self.screenscraper_username_entry.set_text("")
		self.screenscraper_password_entry.set_text("")
		self.screenscraper_status_label.set_text("Saved.")

	def do_screenscraper_scrape_all(self):
		def work():
			results = {"ok": 0, "failed": 0, "skipped": 0}
			for category in self.categories:
				for manufacturer in category["manufacturers"]:
					for platform in manufacturer.get("platforms", []):
						if not platform.get("roms_dir"):
							continue
						for game in library.list_games(platform):
							rom_stem = Path(game["rom"]).stem
							if skyscraper_scraper.get_cached(platform["id"], rom_stem):
								results["skipped"] += 1
								continue
							media = skyscraper_scraper.fetch(platform["id"], rom_stem, game["rom"])
							results["ok" if media else "failed"] += 1
			return results

		def on_done(results):
			self.screenscraper_scrape_status.set_text(
				f"Done - {results['ok']} scraped, {results['failed']} not found/failed, "
				f"{results['skipped']} already cached."
			)

		self.screenscraper_scrape_status.set_text("Scraping all games... this can take a long time.")
		self.run_async(work, on_done)

	# -- settings: Save Sync (per-player Dropbox save-file sync) -------------
	#
	# A real, distinct trust model from IGDB/Real-Debrid above: each
	# profile connects THEIR OWN Dropbox account (see
	# packaging/dropbox_token.py's own "Saves app" docstring) rather than
	# entering a single shared credential, so this is three screens deep
	# (save_sync -> save_sync_connect / save_sync_platforms), not one
	# leaf - see launcher/save_sync.py's own docstring for the backend
	# this all drives.

	def build_save_sync_page(self):
		"""Registered eagerly (like build_api_settings_page) so the
		"save_sync" stack name exists from the start; refresh_save_sync_page()
		rebuilds the actual row list on every visit, since it shows
		different rows depending on whether this profile has connected a
		Dropbox account yet."""
		self.stack.add_named(make_list_page("Save Sync (Dropbox)", [], lambda t: t, on_back=True), "save_sync")

	def refresh_save_sync_page(self):
		config = save_sync.load_config()
		if not config.get("refresh_token"):
			items = ["Connect Dropbox"]
			actions = {"Connect Dropbox": lambda: self.switch_to("save_sync_connect")}
		else:
			n = len(config.get("platforms", []))
			auto = "On" if config.get("auto_sync") else "Off"
			upload_label = "Sync Now (Upload)" + getattr(self, "_save_sync_upload_status", "")
			download_label = "Restore from Cloud (Download)" + getattr(self, "_save_sync_download_status", "")
			items = [
				f"Choose Platforms ({n} selected)",
				upload_label,
				download_label,
				f"Auto-Sync: {auto}",
				"Disconnect",
			]
			actions = {
				items[0]: lambda: self.switch_to("save_sync_platforms"),
				upload_label: self.do_save_sync_upload,
				download_label: self.do_save_sync_download,
				items[3]: self.toggle_save_sync_auto,
				items[4]: self.disconnect_save_sync,
			}
		page = make_list_page("Save Sync (Dropbox)", items, lambda t: t, on_back=True)
		page.listbox.connect("row-activated", lambda _lb, row: actions[row.item]())
		self.replace_page("save_sync", page)

	def _save_sync_result_suffix(self, results):
		"""Turns {platform_id: True/False/None} into a short " - X ok, Y
		failed" suffix (nothing appended when everything succeeded and
		at least one platform had real data, matching this project's own
		"only surface it when something's actually worth mentioning"
		reasoning) - None entries (nothing to sync yet for that
		platform) don't count as failures."""
		ok = sum(1 for v in results.values() if v is True)
		failed = sum(1 for v in results.values() if v is False)
		if failed:
			return f" - {ok} ok, {failed} failed"
		if ok:
			return " - done"
		return " - nothing to sync"

	def do_save_sync_upload(self):
		def work():
			return save_sync.upload_saves()

		def on_done(results):
			self._save_sync_upload_status = self._save_sync_result_suffix(results)
			self.refresh_save_sync_page()

		self._save_sync_upload_status = " - syncing..."
		self.refresh_save_sync_page()
		self.run_async(work, on_done)

	def do_save_sync_download(self):
		def work():
			return save_sync.download_saves()

		def on_done(results):
			self._save_sync_download_status = self._save_sync_result_suffix(results)
			self.refresh_save_sync_page()

		self._save_sync_download_status = " - syncing..."
		self.refresh_save_sync_page()
		self.run_async(work, on_done)

	def toggle_save_sync_auto(self):
		config = save_sync.load_config()
		save_sync.set_auto_sync(not config.get("auto_sync"))
		self.refresh_save_sync_page()

	def disconnect_save_sync(self):
		self.confirm(
			"Disconnect Dropbox",
			"Disconnect this profile's Dropbox account? Your platform selection and auto-sync setting stay, ready to reconnect anytime.",
			self._do_disconnect_save_sync,
		)

	def _do_disconnect_save_sync(self):
		save_sync.disconnect()
		self._save_sync_upload_status = ""
		self._save_sync_download_status = ""
		self.refresh_save_sync_page()

	def build_save_sync_connect_page(self):
		box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=scaling.px_v(10))
		box.set_border_width(scaling.px_v(22))
		box.pack_start(make_heading("Connect Dropbox"), False, False, 0)

		self.save_sync_connect_url_label = Gtk.Label(xalign=0)
		self.save_sync_connect_url_label.set_line_wrap(True)
		box.pack_start(self.save_sync_connect_url_label, False, False, 0)

		box.pack_start(Gtk.Label(label="On any device, open that link, approve access, then paste the code it shows you below:", xalign=0), False, False, 0)

		self.save_sync_connect_code_entry = Gtk.Entry()
		box.pack_start(self.save_sync_connect_code_entry, False, False, 0)

		self.save_sync_connect_status_label = Gtk.Label(xalign=0)
		self.save_sync_connect_status_label.get_style_context().add_class("dim-label")
		box.pack_start(self.save_sync_connect_status_label, False, False, 0)

		hint = Gtk.Label(label="Enter: connect   Escape: back")
		hint.get_style_context().add_class("dim-label")
		box.pack_start(hint, False, False, 0)

		self.save_sync_connect_code_entry.connect("activate", lambda _e: self.submit_save_sync_connect())

		box.focus_widget = self.save_sync_connect_code_entry
		box.show_all()
		self.stack.add_named(box, "save_sync_connect")

	def refresh_save_sync_connect_page(self):
		self.save_sync_connect_url_label.set_text(dropbox_token.saves_authorize_url())
		self.save_sync_connect_code_entry.set_text("")
		self.save_sync_connect_status_label.set_text("")
		self.save_sync_connect_code_entry.grab_focus()

	def submit_save_sync_connect(self):
		code = self.save_sync_connect_code_entry.get_text().strip()
		if not code:
			return
		self.save_sync_connect_status_label.set_text("Connecting...")
		self.save_sync_connect_code_entry.set_sensitive(False)

		def work():
			return save_sync.connect(code)

		def on_done(ok):
			self.save_sync_connect_code_entry.set_sensitive(True)
			self.save_sync_connect_code_entry.set_text("")
			if ok:
				self.save_sync_connect_status_label.set_text("Connected.")
				self.go_back()
			else:
				self.save_sync_connect_status_label.set_text("That code didn't work - codes expire quickly, try a fresh link.")

		self.run_async(work, on_done)

	def build_save_sync_platforms_page(self):
		self.stack.add_named(make_list_page("Choose Platforms", [], lambda t: t, on_back=True), "save_sync_platforms")

	def refresh_save_sync_platforms_page(self):
		config = save_sync.load_config()
		selected = set(config.get("platforms", []))
		platforms = save_sync.list_syncable_platforms()
		labels = {pid: (f"[x] {name}" if pid in selected else f"[ ] {name}") for pid, name in platforms}
		page = make_list_page("Choose Platforms", [pid for pid, _ in platforms], lambda pid: labels[pid], on_back=True)
		page.listbox.connect("row-activated", lambda _lb, row: self.toggle_save_sync_platform(row.item))
		self.replace_page("save_sync_platforms", page)

	def toggle_save_sync_platform(self, platform_id):
		config = save_sync.load_config()
		selected = set(config.get("platforms", []))
		if platform_id in selected:
			selected.discard(platform_id)
		else:
			selected.add(platform_id)
		save_sync.set_platforms(sorted(selected))
		self.refresh_save_sync_platforms_page()

	# -- settings: RetroAchievements login -----------------------------------
	#
	# Two genuinely different RA credentials live on this one screen: a
	# Web API key (optional - only used for the dashboard summary widget's
	# read-only stats, see retroachievements.fetch_summary()) and a
	# username+password login (used once, transiently, to obtain a
	# Connect API token - see retroachievements.login_connect_api() - the
	# password itself is never saved anywhere; only the resulting token
	# is, which is what actually lets Dolphin/PCSX2 track achievements
	# in-game, synced from that token on every launch by
	# retroachievements_sync.py). Password field always starts blank like
	# Wi-Fi's own; username/API key fields prefill from whatever's
	# already stored, since neither is secret in the same way a password
	# is and prefilling them makes it obvious at a glance whether this
	# profile has anything configured yet.

	def build_ra_login_page(self):
		box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=scaling.px_v(10))
		box.set_border_width(scaling.px_v(22))
		box.pack_start(make_heading("RetroAchievements Login"), False, False, 0)

		self.ra_login_status = Gtk.Label(xalign=0)
		self.ra_login_status.get_style_context().add_class("dim-label")
		self.ra_login_status.set_line_wrap(True)
		box.pack_start(self.ra_login_status, False, False, 0)

		box.pack_start(Gtk.Label(label="Username", xalign=0), False, False, 0)
		self.ra_login_username_entry = Gtk.Entry()
		box.pack_start(self.ra_login_username_entry, False, False, 0)

		box.pack_start(Gtk.Label(label="Web API Key (optional, for dashboard stats)", xalign=0), False, False, 0)
		self.ra_login_api_key_entry = Gtk.Entry()
		self.ra_login_api_key_entry.set_visibility(False)
		box.pack_start(self.ra_login_api_key_entry, False, False, 0)

		box.pack_start(Gtk.Label(label="Password (only needed to log Dolphin/PCSX2 into RetroAchievements)", xalign=0), False, False, 0)
		self.ra_login_password_entry = Gtk.Entry()
		self.ra_login_password_entry.set_visibility(False)
		box.pack_start(self.ra_login_password_entry, False, False, 0)

		hint = Gtk.Label(label="Tab: next field   Enter: save   Escape: back")
		hint.get_style_context().add_class("dim-label")
		box.pack_start(hint, False, False, 0)

		self.ra_login_username_entry.connect("activate", lambda _e: self.ra_login_api_key_entry.grab_focus())
		self.ra_login_api_key_entry.connect("activate", lambda _e: self.ra_login_password_entry.grab_focus())
		self.ra_login_password_entry.connect("activate", lambda _e: self.submit_ra_login())

		box.focus_widget = self.ra_login_username_entry
		box.show_all()
		self.stack.add_named(box, "ra_login")

	def refresh_ra_login_page(self):
		creds = retroachievements.load_credentials()
		self.ra_login_username_entry.set_text(creds["username"])
		self.ra_login_api_key_entry.set_text(creds["api_key"])
		self.ra_login_password_entry.set_text("")
		if creds["username"] and creds["connect_token"]:
			self.ra_login_status.set_text(f"Dolphin/PCSX2 logged in as {creds['username']}.")
		elif creds["username"]:
			self.ra_login_status.set_text(f"Dashboard connected as {creds['username']} - no Dolphin/PCSX2 login yet (enter your password below).")
		else:
			self.ra_login_status.set_text("Not connected.")
		self.ra_login_username_entry.grab_focus()

	def submit_ra_login(self):
		username = self.ra_login_username_entry.get_text().strip()
		api_key = self.ra_login_api_key_entry.get_text().strip()
		password = self.ra_login_password_entry.get_text()
		if not username:
			self.ra_login_status.set_text("Enter a username first.")
			return
		if not password:
			log.info("submit_ra_login: saving username/API key without a Connect API login")
			retroachievements.save_credentials(username=username, api_key=api_key)
			self.ra_login_status.set_text("Saved.")
			self.run_async(retroachievements.fetch_summary, self.on_ra_summary_done)
			return
		log.info("submit_ra_login: attempting a Connect API login")
		self.ra_login_status.set_text("Logging in...")
		self.run_async(
			lambda: retroachievements.login_connect_api(username, password),
			lambda result: self.on_ra_login_done(result, username, api_key),
		)

	def on_ra_login_done(self, result, username, api_key):
		self.ra_login_password_entry.set_text("")
		if not result.get("success"):
			self.ra_login_status.set_text(f"Login failed: {result.get('error', 'unknown error')}")
			return False
		retroachievements.save_credentials(username=username, api_key=api_key, connect_token=result["token"])
		self.ra_login_status.set_text(f"Dolphin/PCSX2 logged in as {username}.")
		self.run_async(retroachievements.fetch_summary, self.on_ra_summary_done)
		return False

	# -- Play Together (Sunshine host + moonlight-embedded join) -------------
	#
	# One co-op streaming system standing in for netplay across all three
	# emulators - RetroArch's own real netplay is off the table too, per
	# direct request, so hosting/joining works identically no matter what's
	# actually running (RetroArch, Dolphin, PCSX2, or anything else CosmOS
	# has open) rather than needing separate per-emulator wiring. See
	# streaming/sunshine_manager.py and streaming/moonlight_manager.py's
	# own module docstrings for the full "why" (Dolphin's netplay is Qt-
	# GUI-only with no CLI hook, PCSX2 has none upstream at all). Its own
	# top-level dashboard entry (see config.ini), not tucked into
	# Settings, per direct instruction.

	def build_play_together_page(self):
		items = [
			{"name": "Host", "screen": "play_together_host"},
			{"name": "Join", "screen": "play_together_join"},
		]
		page = make_list_page("Play Together", items, lambda e: e["name"], on_back=True)
		page.listbox.connect("row-activated", lambda _lb, row: self.switch_to(row.item["screen"]))
		self.stack.add_named(page, "play_together")

	# -- Play Together: Host --------------------------------------------------

	def build_play_together_host_page(self):
		box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=scaling.px_v(8))
		box.set_border_width(scaling.px_v(22))
		box.pack_start(make_heading("Host"), False, False, 0)

		self.pt_host_status = Gtk.Label(xalign=0)
		self.pt_host_status.get_style_context().add_class("dim-label")
		self.pt_host_status.set_line_wrap(True)
		box.pack_start(self.pt_host_status, False, False, 0)

		self.pt_host_toggle_button = Gtk.Button(label="Start Hosting")
		self.pt_host_toggle_button.connect("clicked", lambda _b: self.toggle_play_together_hosting())
		box.pack_start(self.pt_host_toggle_button, False, False, 0)

		box.pack_start(Gtk.Label(label="Device name (optional)", xalign=0), False, False, 0)
		self.pt_host_name_entry = Gtk.Entry()
		self.pt_host_name_entry.connect("activate", lambda _e: self.pt_host_pin_entry.grab_focus())
		box.pack_start(self.pt_host_name_entry, False, False, 0)

		box.pack_start(Gtk.Label(label="PIN from a joining device", xalign=0), False, False, 0)
		self.pt_host_pin_entry = Gtk.Entry()
		self.pt_host_pin_entry.connect("activate", lambda _e: self.submit_play_together_pin())
		box.pack_start(self.pt_host_pin_entry, False, False, 0)

		self.pt_host_pin_status = Gtk.Label(xalign=0)
		self.pt_host_pin_status.get_style_context().add_class("dim-label")
		box.pack_start(self.pt_host_pin_status, False, False, 0)

		box.pack_start(Gtk.Label(label="Paired devices", xalign=0), False, False, 0)
		scroller = Gtk.ScrolledWindow()
		scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
		self.pt_host_clients_listbox = Gtk.ListBox()
		install_carets(self.pt_host_clients_listbox)
		self.pt_host_clients_listbox.connect("row-activated", lambda _lb, row: self.unpair_play_together_client(row.item))
		scroller.add(self.pt_host_clients_listbox)
		box.pack_start(scroller, True, True, 0)

		hint = Gtk.Label(label="Enter (on a paired device): unpair   Backspace: back")
		hint.get_style_context().add_class("dim-label")
		box.pack_start(hint, False, False, 0)

		box.focus_widget = self.pt_host_toggle_button
		box.show_all()
		self.stack.add_named(box, "play_together_host")

	def refresh_play_together_host_page(self):
		self.pt_host_name_entry.set_text("")
		self.pt_host_pin_entry.set_text("")
		self.pt_host_pin_status.set_text("")
		self._update_play_together_host_status()

	def _update_play_together_host_status(self):
		running = sunshine_manager.is_running()
		self.pt_host_toggle_button.set_label("Stop Hosting" if running else "Start Hosting")
		self.pt_host_status.set_text(
			"Hosting - ask others to Join using this device's address." if running else "Not hosting."
		)
		if running:
			auth = sunshine_manager.ensure_bootstrapped()
			self.run_async(lambda: sunshine_manager.list_clients(auth), self.on_play_together_clients_loaded)
		else:
			self.on_play_together_clients_loaded([])

	def toggle_play_together_hosting(self):
		if sunshine_manager.is_running():
			sunshine_manager.stop()
			self._update_play_together_host_status()
			return

		def work():
			sunshine_manager.ensure_bootstrapped()
			return sunshine_manager.start()

		self.run_async(work, self.on_play_together_hosting_started)

	def on_play_together_hosting_started(self, started):
		self._update_play_together_host_status()
		return False

	def submit_play_together_pin(self):
		pin = self.pt_host_pin_entry.get_text().strip()
		if not pin:
			self.pt_host_pin_status.set_text("Enter the PIN shown on the joining device first.")
			return
		device_name = self.pt_host_name_entry.get_text().strip() or "Guest"
		self.pt_host_pin_status.set_text("Submitting...")
		auth = sunshine_manager.ensure_bootstrapped()
		self.run_async(lambda: sunshine_manager.submit_pin(pin, device_name, auth), self.on_play_together_pin_done)

	def on_play_together_pin_done(self, ok):
		self.pt_host_pin_entry.set_text("")
		self.pt_host_pin_status.set_text("Paired!" if ok else "Failed - check the PIN and try again.")
		if ok:
			self._update_play_together_host_status()
		return False

	def on_play_together_clients_loaded(self, clients):
		for row in self.pt_host_clients_listbox.get_children():
			self.pt_host_clients_listbox.remove(row)
		if not clients:
			row = make_row(None, "No paired devices yet")
			row.set_selectable(False)
			row.set_activatable(False)
			self.pt_host_clients_listbox.add(row)
		else:
			for client in clients:
				self.pt_host_clients_listbox.add(make_row(client, f"{client['name']} (Enter to unpair)"))
		self.pt_host_clients_listbox.show_all()
		return False

	def unpair_play_together_client(self, client):
		if not client:
			return
		auth = sunshine_manager.ensure_bootstrapped()
		self.run_async(
			lambda: sunshine_manager.unpair_client(client["uuid"], auth),
			lambda _ok: self._update_play_together_host_status(),
		)

	# -- Play Together: Join ---------------------------------------------------

	def build_play_together_join_page(self):
		box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=scaling.px_v(10))
		box.set_border_width(scaling.px_v(22))
		box.pack_start(make_heading("Join"), False, False, 0)

		box.pack_start(Gtk.Label(label="Host address", xalign=0), False, False, 0)
		self.pt_join_host_entry = Gtk.Entry()
		self.pt_join_host_entry.connect("activate", lambda _e: self.submit_play_together_join())
		box.pack_start(self.pt_join_host_entry, False, False, 0)

		self.pt_join_status = Gtk.Label(xalign=0)
		self.pt_join_status.get_style_context().add_class("dim-label")
		self.pt_join_status.set_line_wrap(True)
		box.pack_start(self.pt_join_status, False, False, 0)

		hint = Gtk.Label(label="Enter: connect   Backspace: back")
		hint.get_style_context().add_class("dim-label")
		box.pack_start(hint, False, False, 0)

		box.focus_widget = self.pt_join_host_entry
		box.show_all()
		self.stack.add_named(box, "play_together_join")

	def refresh_play_together_join_page(self):
		self.pt_join_host_entry.set_text("")
		self.pt_join_status.set_text("")
		self.pt_join_host_entry.grab_focus()

	def submit_play_together_join(self):
		host = self.pt_join_host_entry.get_text().strip()
		if not host:
			return
		self.play_together_join_host = host
		self.pt_join_status.set_text("Connecting...")
		log.info(f"submit_play_together_join: pairing with {host}")
		self.run_streamed(
			moonlight_manager.pair_command(host), self.on_play_together_join_line, self.on_play_together_join_done
		)

	def on_play_together_join_line(self, line):
		pin = moonlight_manager.parse_pin(line)
		if pin:
			self.pt_join_status.set_text(f"Enter this PIN on the host's Play Together > Host screen: {pin}")
		return False

	def on_play_together_join_done(self, returncode):
		if returncode == 0:
			self.pt_join_status.set_text("Paired!")
			self.switch_to("play_together_stream")
		else:
			self.pt_join_status.set_text("Pairing failed - check the address and try again.")
		return False

	# -- Play Together: Stream --------------------------------------------------

	def build_play_together_stream_page(self):
		items = [{"name": "Play"}]
		page = make_list_page("Ready to Play", items, lambda e: e["name"], on_back=True)
		page.listbox.connect("row-activated", lambda _lb, _row: self.start_play_together_stream())
		self.stack.add_named(page, "play_together_stream")

	def start_play_together_stream(self):
		host = self.play_together_join_host
		cmd = moonlight_manager.stream_command(host)
		log.info(f"start_play_together_stream: {cmd}")
		self.hide()
		stick_mode.set_navigate_mode()
		try:
			graphical.run_graphical(cmd)
		finally:
			stick_mode.set_navigate_mode()
			self.show()
			self._hide_own_cursor()

	# -- file manager ---------------------------------------------------------

	def build_filemanager_page(self):
		"""Two independent panes (Double Commander-style), each browsing
		its own source - Left/Right switches which pane is "active" (see
		on_key()'s own handling and switch_fm_pane()), Up/Down moves
		within whichever pane that is, tracked by each pane's own
		"set-focus-child" signal (see _on_fm_pane_focus() - same pattern
		as on_menu_row_focus_child()'s .row-focused tracking). Copy/Move
		actions (see build_fm_actions_page()) target the *other* pane's
		current location directly - no separate clipboard/paste step
		needed, unlike a single-pane file manager.

		Each pane's own source (Local/USB/FTP/SFTP/WebDAV - see
		App.__init__'s own self.fm_panes docstring) is reached through a
		"[Change Source: ...]" row always sitting at the top of its
		listing (see _refresh_fm_pane()/open_fm_source_picker()), not a
		separate widget - every row in this screen is plain Gtk.ListBox
		navigation, so a source switch needs to be a row too."""
		box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=scaling.px_v(6))
		box.set_border_width(scaling.px_v(22))
		box.pack_start(make_heading("File Manager"), False, False, 0)

		panes_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=scaling.px_h(16))
		panes_row.set_homogeneous(True)

		self.fm_listboxes = []
		self.fm_pane_boxes = []
		self.fm_pane_headings = []
		for pane in range(2):
			pane_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=scaling.px_v(6))
			pane_box.get_style_context().add_class("fm-pane")

			heading = Gtk.Label(xalign=0)
			heading.get_style_context().add_class("fm-pane-heading")
			heading.set_ellipsize(Pango.EllipsizeMode.START)
			pane_box.pack_start(heading, False, False, 0)

			scroller = Gtk.ScrolledWindow()
			scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
			scroller.set_can_focus(False)
			listbox = Gtk.ListBox()
			listbox.set_selection_mode(Gtk.SelectionMode.BROWSE)
			install_carets(listbox)
			listbox.connect("row-activated", lambda _lb, row, pane=pane: self.activate_fm_row(row.item, pane))
			listbox.connect("set-focus-child", lambda _lb, child, pane=pane: self._on_fm_pane_focus(pane, child))
			scroller.add(listbox)
			pane_box.pack_start(scroller, True, True, 0)

			panes_row.pack_start(pane_box, True, True, 0)
			self.fm_listboxes.append(listbox)
			self.fm_pane_boxes.append(pane_box)
			self.fm_pane_headings.append(heading)

		box.pack_start(panes_row, True, True, 0)

		self.fm_status = Gtk.Label(xalign=0)
		self.fm_status.get_style_context().add_class("dim-label")
		box.pack_start(self.fm_status, False, False, 0)

		hint = controller_glyphs.make_hint_row(
			"Left/Right: switch pane   Up/Down: move   ",
			controller_glyphs.CONFIRM,
			": open/select   ",
			controller_glyphs.DELETE,
			": actions   Backspace: back to menu",
		)
		hint.set_halign(Gtk.Align.CENTER)
		box.pack_start(hint, False, False, 0)

		box.show_all()
		self.stack.add_named(box, "filemanager")

	def _on_fm_pane_focus(self, pane, child):
		if child is None:
			return
		self.fm_active_pane = pane
		for i, pane_box in enumerate(self.fm_pane_boxes):
			if i == pane:
				pane_box.get_style_context().add_class("fm-pane-active")
			else:
				pane_box.get_style_context().remove_class("fm-pane-active")

	def switch_fm_pane(self, pane):
		if pane != self.fm_active_pane:
			self._focus_fm_pane(pane)

	def refresh_fm_panes(self):
		for pane in range(2):
			self._refresh_fm_pane(pane)
		self._focus_fm_pane(self.fm_active_pane)

	def _focus_fm_pane(self, pane):
		"""Explicitly grabs the *selected row*, not just the listbox -
		with two listboxes both doing their own select_row() during
		refresh, grabbing the container alone (which relies on GTK
		delegating focus down to a child) turned out to lose a race to
		whichever pane's own select_row() ran last, landing real focus on
		the wrong pane; asking the row directly for focus doesn't leave
		that to chance."""
		def grab():
			row = self.fm_listboxes[pane].get_selected_row()
			if row:
				row.grab_focus()
		GLib.idle_add(grab)

	# -- per-pane source dispatch ---------------------------------------
	#
	# Every one of these branches on self.fm_panes[pane]["kind"]
	# ("local"/"usb"/"remote") so the rest of this screen (_refresh_fm_
	# pane, activate_fm_row, build_fm_actions_page, submit_fm_input, ...)
	# never has to know which of the three a given pane is actually
	# looking at - it just calls these. "local" goes through filemanager/
	# local_scope.py's own curated category gate; "usb" browses whatever
	# usb_content.py already has mounted as the active content source
	# (see that module's own active_content_root()); "remote" goes
	# through filemanager/remote.py's protocol dispatch over a saved
	# filemanager/remote_connections.py connection.

	def _fm_real_dir(self, pane):
		"""The real local directory pane's current location refers to,
		for local/usb panes only (None for remote, or if there simply
		isn't one right now - Local sitting on the root category list or
		an ambiguous multi-root merge view, or USB with nothing
		mounted) - what a Copy/Move/New Folder targeting this pane
		actually writes into."""
		p = self.fm_panes[pane]
		if p["kind"] == "local":
			resolved = fm_local_scope.resolve_safe(p["path"])
			return str(resolved) if resolved else None
		if p["kind"] == "usb":
			root = usb_content.active_content_root()
			if root is None:
				return None
			return p["path"] or str(root)
		return None

	def _fm_pane_label(self, pane):
		p = self.fm_panes[pane]
		if p["kind"] == "local":
			return "Local"
		if p["kind"] == "usb":
			return "USB"
		return p["connection"]["name"]

	def _fm_pane_heading_text(self, pane):
		p = self.fm_panes[pane]
		if p["kind"] == "local":
			if p["path"] is None:
				return "Local"
			if p["path"].startswith("local:"):
				return f"Local / {p['path'][len('local:') :]}"
			return f"Local / {p['path']}"
		if p["kind"] == "usb":
			root = usb_content.active_content_root()
			if root is None:
				return "USB (no drive mounted)"
			return f"USB / {p['path'] or str(root)}"
		return f"{p['protocol'].upper()}: {p['connection']['name']}{p['path'] or '/'}"

	def _fm_list_pane(self, pane):
		"""(dirs, files, message) - message is a real user-facing string
		("nothing mounted", a remote connection/auth failure, ...) to
		show as a single unselectable row instead of dirs/files whenever
		this pane's current location can't actually be listed right now;
		None otherwise. Never raises - every branch here already has its
		own "never crash" contract (filemanager/local_scope.py's own
		docstring, filemanager/remote.py's protocol backends)."""
		p = self.fm_panes[pane]
		if p["kind"] == "local":
			try:
				dirs, files = fm_local_scope.list_dir(p["path"])
				return dirs, files, None
			except OSError as e:
				return [], [], f"Can't read this directory: {e}"
		if p["kind"] == "usb":
			root = usb_content.active_content_root()
			if root is None:
				return [], [], "No USB drive mounted as a content source - go to Storage to mount one."
			target = p["path"] or str(root)
			try:
				dirs, files = filemanager.list_dir(target)
				return dirs, files, None
			except OSError as e:
				return [], [], f"Can't read this directory: {e}"
		dirs, files, error = fm_remote.list_dir(p["connection"], p["path"] or "/")
		return dirs, files, error

	def _fm_can_go_up(self, pane):
		p = self.fm_panes[pane]
		if p["kind"] in ("local", "usb"):
			return p["path"] is not None
		return (p["path"] or "/") != "/"

	def _fm_up_path(self, pane):
		p = self.fm_panes[pane]
		if p["kind"] == "local":
			return fm_local_scope.up(p["path"])
		if p["kind"] == "usb":
			root = usb_content.active_content_root()
			if p["path"] is None or root is None:
				return None
			parent = str(Path(p["path"]).parent)
			if parent == str(root) or not parent.startswith(str(root)):
				return None  # back to the mount's own root, or out of scope entirely -> same floor
			return parent
		path = p["path"] or "/"
		parent = path.rsplit("/", 1)[0] or "/"
		return None if parent == "/" else parent

	def _fm_can_write(self, pane):
		p = self.fm_panes[pane]
		if p["kind"] == "local":
			return fm_local_scope.can_write(p["path"])
		if p["kind"] == "usb":
			return usb_content.active_content_root() is not None
		return True  # every remote backend supports make_dir

	def _fm_dispatch_make_dir(self, pane, name):
		p = self.fm_panes[pane]
		if p["kind"] == "local":
			filemanager.make_dir(str(fm_local_scope.resolve_safe(p["path"])), name)
		elif p["kind"] == "usb":
			filemanager.make_dir(p["path"] or str(usb_content.active_content_root()), name)
		else:
			err = fm_remote.make_dir(p["connection"], p["path"] or "/", name)
			if err:
				raise OSError(err)

	def _fm_dispatch_rename(self, pane, item, new_name):
		p = self.fm_panes[pane]
		if p["kind"] in ("local", "usb"):
			filemanager.rename(item["path"], new_name)
		else:
			err = fm_remote.rename(p["connection"], item["path"], new_name)
			if err:
				raise OSError(err)

	def _fm_dispatch_delete(self, pane, item):
		p = self.fm_panes[pane]
		if p["kind"] in ("local", "usb"):
			filemanager.delete(item["path"])
		else:
			err = fm_remote.delete(p["connection"], item["path"], is_dir=(item["kind"] == "dir"))
			if err:
				raise OSError(err)

	def _refresh_fm_pane(self, pane):
		self.fm_pane_headings[pane].set_text(self._fm_pane_heading_text(pane))
		listbox = self.fm_listboxes[pane]
		for row in list(listbox.get_children()):
			listbox.remove(row)

		dirs, files, message = self._fm_list_pane(pane)

		listbox.add(make_row({"kind": "source"}, f"[Change Source: {self._fm_pane_label(pane)}]"))

		if message:
			listbox.add(make_row({"kind": "message"}, message))
		else:
			if self._fm_can_go_up(pane):
				listbox.add(make_row({"kind": "up"}, ".."))
			if self._fm_can_write(pane):
				listbox.add(make_row({"kind": "new_folder"}, "[New Folder]"))
			for d in dirs:
				listbox.add(make_row({"kind": "dir", **d}, d["name"] + "/"))
			for f in files:
				label = f"{f['name']}  ({filemanager.format_size(f['size'])})"
				listbox.add(make_row({"kind": "file", **f}, label))

		listbox.show_all()
		first_row = listbox.get_row_at_index(0)
		if first_row:
			listbox.select_row(first_row)

	def activate_fm_row(self, item, pane):
		"""A folder navigates straight in, the same as every real file
		manager - it used to route through the same actions menu a file
		does (an extra "Open" confirmation step just to browse), a real
		report that this read as "asking to open [a folder] like a
		file". Copy/Move/Rename/Delete for a folder are still reachable,
		just via the Delete key instead (see open_fm_item_actions()) -
		the same "activating a tile does the obvious default thing,
		removing it needs a dedicated key" split the quick-launch/widget
		rows already use."""
		kind = item["kind"]
		log.debug(f"activate_fm_row: pane={pane} kind={kind} item={item.get('name')}")
		if kind == "source":
			self.open_fm_source_picker(pane)
		elif kind == "message":
			pass  # nothing to act on - just an explanation row (e.g. "no USB drive mounted")
		elif kind == "up":
			self.fm_panes[pane]["path"] = self._fm_up_path(pane)
			self._refresh_fm_pane(pane)
		elif kind == "new_folder":
			self.fm_input_purpose = "new_folder"
			self.fm_input_pane = pane
			self.fm_input_heading.label_widget.set_text("New Folder Name")
			self.fm_input_entry.set_text("")
			self.fm_input_status.set_text("")
			self.switch_to("filemanager_input")
		elif kind == "dir":
			self.fm_panes[pane]["path"] = item["path"]
			self._refresh_fm_pane(pane)
		else:  # "file" - no default action of its own, just the actions menu
			self.fm_action_item = item
			self.fm_action_pane = pane
			self.build_fm_actions_page(is_dir=False)
			self.switch_to("filemanager_actions")

	def open_fm_item_actions(self, pane):
		"""Delete-key entry point into the Copy/Move/Rename/Delete menu -
		the one activate_fm_row() no longer opens for a folder by
		default (see its own comment). A no-op if nothing real is
		selected (the "[Change Source]"/".."/"[New Folder]"/message rows
		have nothing to act on)."""
		row = self.fm_listboxes[pane].get_selected_row()
		if not row or row.item["kind"] not in ("dir", "file"):
			return
		self.fm_action_item = row.item
		self.fm_action_pane = pane
		self.build_fm_actions_page(is_dir=(row.item["kind"] == "dir"))
		self.switch_to("filemanager_actions")

	def build_fm_actions_page(self, is_dir):
		"""Copy/Move are only offered for a folder when both panes are
		real local filesystems (local/USB) - transferring a whole folder
		to or from a remote source would mean this screen silently
		recursing an entire directory tree over FTP/SFTP/WebDAV one file
		at a time, which isn't something to take on implicitly; a single
		file is always fine, every protocol backend handles exactly one
		file per call (see filemanager/remote_ftp.py and friends)."""
		other_pane = 1 - self.fm_action_pane
		other_label = self._fm_pane_label(other_pane)
		source_kind = self.fm_panes[self.fm_action_pane]["kind"]
		other_kind = self.fm_panes[other_pane]["kind"]
		transfer_ok = not is_dir or (source_kind in ("local", "usb") and other_kind in ("local", "usb"))
		actions = ["Open"] if is_dir else []
		if transfer_ok:
			actions += [f"Copy to {other_label}", f"Move to {other_label}"]
		actions += ["Rename", "Delete", "Cancel"]
		page = make_list_page(self.fm_action_item["name"], actions, lambda a: a, on_back=True)
		page.listbox.connect("row-activated", lambda _lb, row: self.handle_fm_action(row.item))
		self.replace_page("filemanager_actions", page)

	def handle_fm_action(self, action):
		item = self.fm_action_item
		pane = self.fm_action_pane
		log.info(f"handle_fm_action: {action!r} on {item.get('name')} (pane={pane})")
		if action == "Open":
			self.fm_panes[pane]["path"] = item["path"]
			self.go_back()
			self._refresh_fm_pane(pane)
		elif action.startswith("Copy to "):
			self._do_fm_transfer(item, 1 - pane, mode="copy")
		elif action.startswith("Move to "):
			self._do_fm_transfer(item, 1 - pane, mode="move")
		elif action == "Rename":
			self.fm_input_purpose = "rename"
			self.fm_input_heading.label_widget.set_text(f"Rename {item['name']}")
			self.fm_input_entry.set_text(item["name"])
			self.fm_input_status.set_text("")
			self.switch_to("filemanager_input")
		elif action == "Delete":
			self.confirm(
				"Delete this?",
				f"This permanently deletes {item['name']}. This cannot be undone.",
				lambda: self._do_fm_delete(item, pane),
			)
		elif action == "Cancel":
			self.go_back()

	def _do_fm_transfer(self, item, dest_pane, mode):
		"""Copies/moves item (from the pane opposite dest_pane) into
		dest_pane's current location - the Double Commander convention:
		the *other* pane is always the implicit destination, no separate
		clipboard/paste step. Both panes get refreshed once done - the
		destination gained something, and a move also emptied the
		source.

		A real network transfer (download/upload) whenever either side
		is a remote pane, not a plain shutil.copy - remote-to-remote
		goes through a short-lived local temp file (download then
		upload) since none of the three protocol backends talk to each
		other directly. Folders are never transferred to/from a remote
		pane at all (see build_fm_actions_page()'s own docstring) -
		item["kind"] == "dir" only ever reaches here when both panes are
		local/USB, where filemanager.copy()/move() already recurse a
		whole directory tree the normal way."""
		source_pane = 1 - dest_pane
		source_kind = self.fm_panes[source_pane]["kind"]
		dest_kind = self.fm_panes[dest_pane]["kind"]
		self.fm_status.set_text("Working...")

		def work():
			try:
				if source_kind in ("local", "usb") and dest_kind in ("local", "usb"):
					dest_dir = self._fm_real_dir(dest_pane)
					if dest_dir is None:
						return "No destination directory available."
					if mode == "move":
						filemanager.move(item["path"], dest_dir)
					else:
						filemanager.copy(item["path"], dest_dir)
				elif source_kind in ("local", "usb") and dest_kind == "remote":
					connection = self.fm_panes[dest_pane]["connection"]
					dest_path = self.fm_panes[dest_pane]["path"] or "/"
					err = fm_remote.upload(connection, item["path"], dest_path)
					if err:
						return err
					if mode == "move":
						filemanager.delete(item["path"])
				elif source_kind == "remote" and dest_kind in ("local", "usb"):
					dest_dir = self._fm_real_dir(dest_pane)
					if dest_dir is None:
						return "No destination directory available."
					connection = self.fm_panes[source_pane]["connection"]
					err = fm_remote.download(connection, item["path"], dest_dir)
					if err:
						return err
					if mode == "move":
						derr = fm_remote.delete(connection, item["path"], is_dir=False)
						if derr:
							return f"Downloaded, but couldn't delete the original: {derr}"
				else:  # remote -> remote
					source_connection = self.fm_panes[source_pane]["connection"]
					dest_connection = self.fm_panes[dest_pane]["connection"]
					dest_path = self.fm_panes[dest_pane]["path"] or "/"
					with tempfile.TemporaryDirectory() as tmp:
						err = fm_remote.download(source_connection, item["path"], tmp)
						if err:
							return err
						tmp_path = str(Path(tmp) / item["path"].rstrip("/").split("/")[-1])
						err = fm_remote.upload(dest_connection, tmp_path, dest_path)
						if err:
							return err
					if mode == "move":
						derr = fm_remote.delete(source_connection, item["path"], is_dir=False)
						if derr:
							return f"Copied, but couldn't delete the original: {derr}"
				return None
			except OSError as e:
				return str(e)

		def done(error):
			if error:
				log.warning(f"{mode} failed: {error}")
				self.fm_status.set_text(f"Failed: {error}")
			else:
				self.fm_status.set_text(f"{'Moved' if mode == 'move' else 'Copied'} {item['name']}")
			self.go_back()  # actions -> browse
			self._refresh_fm_pane(dest_pane)
			if mode == "move":
				self._refresh_fm_pane(source_pane)
			return False

		self.run_async(work, done)

	def _do_fm_delete(self, item, pane):
		try:
			self._fm_dispatch_delete(pane, item)
			self.fm_status.set_text(f"Deleted {item['name']}")
		except OSError as e:
			log.warning(f"delete of {item['path']} failed: {e}")
			self.fm_status.set_text(f"Failed to delete {item['name']}: {e}")
		self.go_back()  # actions -> browse (the item is gone, no point lingering on its actions page)
		self._refresh_fm_pane(pane)

	def build_filemanager_input_page(self):
		box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=scaling.px_v(10))
		box.set_border_width(scaling.px_v(22))
		self.fm_input_heading = make_heading("")
		box.pack_start(self.fm_input_heading, False, False, 0)

		self.fm_input_entry = Gtk.Entry()
		self.fm_input_entry.connect("activate", lambda _e: self.submit_fm_input())
		box.pack_start(self.fm_input_entry, False, False, 0)

		self.fm_input_status = Gtk.Label(xalign=0)
		self.fm_input_status.get_style_context().add_class("dim-label")
		box.pack_start(self.fm_input_status, False, False, 0)

		hint = Gtk.Label(label="Enter: confirm   Escape: cancel")
		hint.get_style_context().add_class("dim-label")
		box.pack_start(hint, False, False, 0)

		box.focus_widget = self.fm_input_entry
		box.show_all()
		self.stack.add_named(box, "filemanager_input")

	def submit_fm_input(self):
		name = self.fm_input_entry.get_text().strip()
		log.info(f"submit_fm_input: purpose={self.fm_input_purpose} name={name!r}")
		if not name or "/" in name:
			log.debug("rejected: empty or contains '/'")
			self.fm_input_status.set_text("Invalid name.")
			return
		try:
			if self.fm_input_purpose == "new_folder":
				self._fm_dispatch_make_dir(self.fm_input_pane, name)
				self.go_back()  # input -> browse
				self._refresh_fm_pane(self.fm_input_pane)
			else:
				self._fm_dispatch_rename(self.fm_action_pane, self.fm_action_item, name)
				self.go_back()  # input -> actions
				self.go_back()  # actions -> browse (renamed item's identity changed)
				self._refresh_fm_pane(self.fm_action_pane)
		except OSError as e:
			log.warning(f"submit_fm_input failed: {e}")
			self.fm_input_status.set_text(f"Failed: {e}")

	# -- File Manager: source picker (Local/USB/FTP/SFTP/WebDAV) ---------

	def open_fm_source_picker(self, pane):
		self.fm_source_pane = pane
		items = ["Local", "USB", "FTP", "SFTP", "WebDAV"]
		page = make_list_page("Change Source", items, lambda a: a, on_back=True)
		page.listbox.connect("row-activated", lambda _lb, row: self.handle_fm_source_choice(row.item))
		self.replace_page("filemanager_source", page)
		self.switch_to("filemanager_source")

	def handle_fm_source_choice(self, choice):
		pane = self.fm_source_pane
		if choice == "Local":
			self.fm_panes[pane] = {"kind": "local", "path": None}
			self.go_back()
			self._refresh_fm_pane(pane)
		elif choice == "USB":
			self.fm_panes[pane] = {"kind": "usb", "path": None}
			self.go_back()
			self._refresh_fm_pane(pane)
		else:
			self.fm_connections_protocol = choice.lower()
			self.open_fm_connections_list()

	def build_fm_connections_page(self):
		"""Rebuilds the saved-connections list for self.fm_connections_
		protocol in place - called both when first opening it and after
		adding/deleting a connection, so the visible list always
		reflects filemanager/remote_connections.py's own current state
		without needing a separate switch_to() (which would push a
		duplicate history entry every time it's just refreshing, not
		actually navigating anywhere new)."""
		protocol = self.fm_connections_protocol
		connections = fm_remote_connections.list_connections(protocol)
		items = [{"kind": "connection", **c} for c in connections] + [{"kind": "add"}]

		def label(entry):
			return "[Add Connection]" if entry["kind"] == "add" else f"{entry['name']} ({entry['host']})"

		page = make_list_page(f"{protocol.upper()} Connections", items, label, on_back=True, extra_hint=f"{controller_glyphs.DELETE}: remove connection")
		page.listbox.connect("row-activated", lambda _lb, row: self.handle_fm_connection_choice(row.item))
		self.replace_page("filemanager_connections", page)

	def open_fm_connections_list(self):
		self.build_fm_connections_page()
		self.switch_to("filemanager_connections")

	def handle_fm_connection_choice(self, item):
		if item["kind"] == "add":
			self.fm_connection_editing_id = None
			self.switch_to("filemanager_connection_edit")
			return
		pane = self.fm_source_pane
		self.fm_panes[pane] = {"kind": "remote", "protocol": item["protocol"], "connection": item, "path": "/"}
		self.go_back()  # connections -> source
		self.go_back()  # source -> filemanager
		self._refresh_fm_pane(pane)

	def delete_selected_fm_connection(self):
		"""Delete-key entry point on the filemanager_connections screen -
		same "dedicated key, not the row's own default action" split as
		open_fm_item_actions(), since activating a connection row here
		means "connect to it", not "manage it"."""
		box = self.stack.get_child_by_name("filemanager_connections")
		if not box:
			return
		row = box.listbox.get_selected_row()
		if not row or row.item["kind"] != "connection":
			return
		self.confirm(
			"Delete this connection?",
			f"This permanently removes the saved connection {row.item['name']!r}.",
			lambda: self._do_delete_fm_connection(row.item["id"]),
		)

	def _do_delete_fm_connection(self, connection_id):
		fm_remote_connections.delete_connection(connection_id)
		self.build_fm_connections_page()

	# -- File Manager: add a saved FTP/SFTP/WebDAV connection ------------
	#
	# Reachable from File Manager's own source picker ("[Add
	# Connection]"), not Settings - a File Manager concern, matching the
	# approved plan's own §6. Always starts blank, same "never show what
	# was already saved back" pattern as build_igdb_page()/build_
	# realdebrid_page(), except for editing an existing entry (there is
	# no edit path yet, only add/delete - see this file's own commit
	# history) - reserved for a future "Edit" action if ever needed.

	def build_fm_connection_edit_page(self):
		box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=scaling.px_v(8))
		box.set_border_width(scaling.px_v(22))
		self.fm_connection_heading = make_heading("Add Connection")
		box.pack_start(self.fm_connection_heading, False, False, 0)

		box.pack_start(Gtk.Label(label="Name", xalign=0), False, False, 0)
		self.fm_connection_name_entry = Gtk.Entry()
		box.pack_start(self.fm_connection_name_entry, False, False, 0)

		box.pack_start(Gtk.Label(label="Host", xalign=0), False, False, 0)
		self.fm_connection_host_entry = Gtk.Entry()
		box.pack_start(self.fm_connection_host_entry, False, False, 0)

		box.pack_start(Gtk.Label(label="Port", xalign=0), False, False, 0)
		self.fm_connection_port_entry = Gtk.Entry()
		box.pack_start(self.fm_connection_port_entry, False, False, 0)

		box.pack_start(Gtk.Label(label="Username", xalign=0), False, False, 0)
		self.fm_connection_username_entry = Gtk.Entry()
		box.pack_start(self.fm_connection_username_entry, False, False, 0)

		box.pack_start(Gtk.Label(label="Password", xalign=0), False, False, 0)
		self.fm_connection_password_entry = Gtk.Entry()
		self.fm_connection_password_entry.set_visibility(False)
		box.pack_start(self.fm_connection_password_entry, False, False, 0)

		self.fm_connection_status = Gtk.Label(xalign=0)
		self.fm_connection_status.get_style_context().add_class("dim-label")
		box.pack_start(self.fm_connection_status, False, False, 0)

		hint = Gtk.Label(label="Tab: next field   Enter (on Password): save   Escape: cancel")
		hint.get_style_context().add_class("dim-label")
		box.pack_start(hint, False, False, 0)

		entries = [
			self.fm_connection_name_entry,
			self.fm_connection_host_entry,
			self.fm_connection_port_entry,
			self.fm_connection_username_entry,
			self.fm_connection_password_entry,
		]
		for entry, nxt in zip(entries, entries[1:]):
			entry.connect("activate", lambda _e, nxt=nxt: nxt.grab_focus())
		self.fm_connection_password_entry.connect("activate", lambda _e: self.save_fm_connection())

		box.focus_widget = self.fm_connection_name_entry
		box.show_all()
		self.stack.add_named(box, "filemanager_connection_edit")

	def refresh_fm_connection_edit_page(self):
		editing = fm_remote_connections.get_connection(self.fm_connection_editing_id) if self.fm_connection_editing_id else None
		self.fm_connection_heading.label_widget.set_text("Edit Connection" if editing else "Add Connection")
		self.fm_connection_name_entry.set_text(editing["name"] if editing else "")
		self.fm_connection_host_entry.set_text(editing["host"] if editing else "")
		default_port = fm_remote_connections.DEFAULT_PORTS.get(self.fm_connections_protocol, "")
		self.fm_connection_port_entry.set_text(str(editing["port"]) if editing else str(default_port))
		self.fm_connection_username_entry.set_text(editing["username"] if editing else "")
		self.fm_connection_password_entry.set_text(editing["password"] if editing else "")
		self.fm_connection_status.set_text("")
		self.fm_connection_name_entry.grab_focus()

	def save_fm_connection(self):
		host = self.fm_connection_host_entry.get_text().strip()
		if not host:
			self.fm_connection_status.set_text("Host is required.")
			return
		port_text = self.fm_connection_port_entry.get_text().strip()
		try:
			port = int(port_text) if port_text else fm_remote_connections.DEFAULT_PORTS.get(self.fm_connections_protocol, 0)
		except ValueError:
			self.fm_connection_status.set_text("Port must be a number.")
			return
		name = self.fm_connection_name_entry.get_text().strip() or host
		username = self.fm_connection_username_entry.get_text().strip()
		password = self.fm_connection_password_entry.get_text()
		log.info(f"save_fm_connection: protocol={self.fm_connections_protocol} name={name!r} host={host!r}")
		fm_remote_connections.save_connection(
			self.fm_connections_protocol, name, host, port, username, password, connection_id=self.fm_connection_editing_id
		)
		self.go_back()  # edit -> connections
		self.build_fm_connections_page()


