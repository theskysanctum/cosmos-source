#!/usr/bin/env python3
"""Loads CosmOS's GTK theme - the shared structural stylesheet
(theme.css) plus one theme's own color palette (launcher/themes/*.css,
see that directory's own README-style comment in each file). Call
load_theme() once at the start of any GTK entry point - the launcher,
the pause overlay, the OSK - so every window in the system looks like
one consistent console rather than a mix of default-GTK-themed popups.

Two separate Gtk.CssProvider instances, not one concatenated file: the
palette is loaded first so theme.css's own @name references already
resolve by the time its rules apply - GTK's CSS engine resolves
@define-color lookups against every provider currently on the screen,
not just within one file, so this works the same as if it were all one
stylesheet. theme.css never has a literal color in it (see its own
docstring) - swapping the active theme is just loading a different
palette file second time around, nothing in theme.css changes."""
import colorsys
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import debuglog  # noqa: E402

import scaling  # noqa: E402

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gtk  # noqa: E402

log = debuglog.setup("theme")

THEME_CSS_PATH = Path(__file__).parent / "theme.css"
THEMES_DIR = Path(__file__).parent / "themes"

# The original/default theme (see themes/console_collision.css's own
# docstring) - not yet a per-profile setting (that's the next real step
# planned for this system), just today's one hardcoded choice.
DEFAULT_THEME = "console_collision"

# Set by load_theme() to whichever theme it actually resolved and
# loaded - apply_adaptive_accent() below re-hues THESE named colors
# specifically, so it needs to know which theme's own real
# saturation/value to preserve without every caller having to pass it
# explicitly (see its own docstring).
_current_theme = DEFAULT_THEME

# The only tokens content-adaptive theming (see apply_adaptive_accent())
# ever touches - every other named color (backgrounds, text, borders)
# stays exactly as the base theme defines it, on purpose: this is the
# real TV-readability safety net, not just a style choice (see
# [[project_cosmos_console_identity]]) - contrast against real body
# text/backgrounds is guaranteed no matter what hue a game's box art
# happens to extract, because those tokens are never touched at all.
ADAPTIVE_ACCENT_NAMES = ["accent_1", "accent_2", "accent_3", "accent_4", "accent_2_bright"]

_HEX_COLOR_RE_TEMPLATE = r"@define-color\s+{name}\s+#([0-9a-fA-F]{{6}});"

# The live adaptive-accent CssProvider, if one is currently applied -
# tracked so apply_adaptive_accent() can swap it out (remove the old
# one, add the new one) rather than stacking a fresh provider on the
# screen every time the focused game changes.
_adaptive_provider = None


def list_themes():
	"""[theme_id, ...] for every launcher/themes/<id>.css file that
	exists, sorted - what a future theme-picker UI would offer."""
	if not THEMES_DIR.is_dir():
		return []
	return sorted(p.stem for p in THEMES_DIR.glob("*.css"))


def load_theme(theme_name=None):
	"""theme_name omitted (every real call site today) means "whatever
	the active profile has chosen" - resolved fresh on every call, same
	as every profiles.py subpath helper, so a profile switch's own
	restart-and-rebuild (see app.py's activate_profile()) picks up its
	theme automatically with no extra plumbing at any of the five
	call sites (the launcher itself, the pause overlay, the OSK, Epic,
	GOG). Falls back to DEFAULT_THEME if the active profile has never
	chosen one yet (a fresh profile, or none active at all - the
	create-a-profile screen still needs a theme loaded), or if
	theme_name (explicit or stored) turns out invalid - a bad theme name
	should never be able to take down the whole launcher.

	Imported lazily, not at module scope: profiles.py doesn't import
	theme.py, so there's no real cycle, but keeping every gi.repository-
	free import out of this module's own top-level load lets any future
	non-GTK caller (a settings CLI, a test) import theme.py's constants
	without dragging in Gtk/Gdk just to read them."""
	global _current_theme, _adaptive_provider
	if theme_name is None:
		import profiles

		theme_name = profiles.get_theme() or DEFAULT_THEME
	theme_path = THEMES_DIR / f"{theme_name}.css"
	if not theme_path.is_file():
		log.warning(f"theme {theme_name!r} not found at {theme_path}, falling back to {DEFAULT_THEME!r}")
		theme_name = DEFAULT_THEME
		theme_path = THEMES_DIR / f"{theme_name}.css"

	log.debug(f"loading theme palette {theme_name!r} from {theme_path}")
	palette_provider = Gtk.CssProvider()
	palette_provider.load_from_path(str(theme_path))
	Gtk.StyleContext.add_provider_for_screen(
		Gdk.Screen.get_default(), palette_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
	)

	log.debug(f"loading structural theme from {THEME_CSS_PATH}")
	structure_provider = Gtk.CssProvider()
	# scaling.scale_css(), not load_from_path() directly - theme.css is
	# entirely px-authored (see its own base-font-size comment), tuned
	# by feel against ONE assumed reference resolution, so every literal
	# px value in it gets rewritten to the real display's own scale
	# factor before GTK ever parses it (see scaling.py's own docstring
	# for why this is the real, working technique rather than relying on
	# GTK CSS relative units/calc(), neither of which this engine has).
	scaled_css = scaling.scale_css(THEME_CSS_PATH.read_text())
	structure_provider.load_from_data(scaled_css.encode())
	Gtk.StyleContext.add_provider_for_screen(
		Gdk.Screen.get_default(), structure_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
	)
	log.debug(f"theme {theme_name!r} applied to default screen")
	_current_theme = theme_name
	# A fresh (re)build always starts from the base theme's own fixed
	# accents - any adaptive override from before a hot-reload/profile
	# switch was tied to the old screen's providers, which destroying
	# the window already dropped along with it (see run.py's rebuild()).
	_adaptive_provider = None


def _theme_accent_hsv(theme_name):
	"""{token_name: (h, s, v)} for theme_name's own real accent colors -
	read directly from its palette file (not hardcoded) so
	apply_adaptive_accent() keeps working correctly if a theme's own
	colors ever change, and works automatically for any future theme
	too, with zero changes needed here."""
	theme_file = THEMES_DIR / f"{theme_name}.css"
	if not theme_file.is_file():
		return {}
	text = theme_file.read_text()
	result = {}
	for name in ADAPTIVE_ACCENT_NAMES:
		match = re.search(_HEX_COLOR_RE_TEMPLATE.format(name=re.escape(name)), text)
		if not match:
			continue
		hex_value = match.group(1)
		r, g, b = (int(hex_value[i : i + 2], 16) / 255 for i in (0, 2, 4))
		result[name] = colorsys.rgb_to_hsv(r, g, b)
	return result


def apply_adaptive_accent(hue_degrees, theme_name=None):
	"""Content-adaptive theming (see [[project_cosmos_console_identity]]):
	re-hues the active theme's own accent tokens (ADAPTIVE_ACCENT_NAMES)
	toward hue_degrees (0-360, typically boxart.get_cached_hue()'s own
	output for whatever game is currently focused in the Games grid),
	while keeping each token's real saturation/value exactly as the
	base theme already defines it - only the hue rotates. This is the
	actual TV-readability safety net: background, text, and border
	tokens are never part of ADAPTIVE_ACCENT_NAMES, so contrast never
	changes no matter what hue a game's box art happens to extract, and
	every accent keeps its base theme's own established vibrancy rather
	than an unclamped, possibly-washed-out color straight from the art.

	Applied as a THIRD CssProvider, above the palette and structural
	ones added by load_theme(), so it overrides just these few named
	colors without reloading or touching either file - cheap enough to
	call on every focus change (a small generated in-memory stylesheet
	via load_from_data(), no disk I/O)."""
	global _adaptive_provider
	base_hsv = _theme_accent_hsv(theme_name or _current_theme)
	if not base_hsv:
		return
	lines = []
	for name, (_h, s, v) in base_hsv.items():
		r, g, b = colorsys.hsv_to_rgb(hue_degrees / 360, s, v)
		lines.append(f"@define-color {name} #{int(r * 255):02x}{int(g * 255):02x}{int(b * 255):02x};")
	provider = Gtk.CssProvider()
	provider.load_from_data("\n".join(lines).encode())
	screen = Gdk.Screen.get_default()
	if _adaptive_provider is not None:
		Gtk.StyleContext.remove_provider_for_screen(screen, _adaptive_provider)
	Gtk.StyleContext.add_provider_for_screen(screen, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)
	_adaptive_provider = provider


def clear_adaptive_accent():
	"""Reverts to the active theme's own fixed accent colors - called
	when focus moves to a game with no extractable hue
	(boxart.get_cached_hue() returned None, or no art at all yet), or
	when leaving the Games grid entirely (see app.py's go_back())."""
	global _adaptive_provider
	if _adaptive_provider is not None:
		Gtk.StyleContext.remove_provider_for_screen(Gdk.Screen.get_default(), _adaptive_provider)
		_adaptive_provider = None
