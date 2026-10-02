#!/usr/bin/env python3
"""Real, runtime resolution-adaptive scaling for CosmOS's whole
10-foot UI. Every fixed pixel value in this app (theme.css's own
font-size/padding/border-radius rules, and every Python-side
set_size_request()/spacing()/border-width() call in app.py) was tuned by
feel against ONE assumed reference resolution (see REFERENCE_WIDTH/
REFERENCE_HEIGHT below) - confirmed live this doesn't adapt on its own
at all: at 1280x720 (vs. the reference 1920x1080) tiles and hint text
visibly overlapped, since nothing anywhere scaled down to fit a smaller
real screen.

get_scale_factor() must only be called after a real X display is up -
Gdk needs a live connection to query the real monitor size - the same
constraint theme.py/app.py's own deliberately-deferred gi.repository
imports already follow (see run.py's own comment on why), so importing
this module at the top of app.py/theme.py is safe without disturbing
that ordering; only actually CALLING get_scale_factor()/px()/scale_css()
this early would be a problem, and none of them run at import time."""
import re

import debuglog

log = debuglog.setup("scaling")

# The resolution every fixed pixel value in theme.css/app.py was
# actually tuned against - 1920x1080, the real "10-foot" TV resolution
# theme.css's own base-font-size comment describes testing against.
# Never hardcoded anywhere else - every real scaling call derives its
# factor from this one pair.
REFERENCE_WIDTH = 1920
REFERENCE_HEIGHT = 1080

_scale_factor = None
_h_factor = None
_v_factor = None


def _get_geometry():
	import gi

	gi.require_version("Gdk", "3.0")
	from gi.repository import Gdk

	display = Gdk.Display.get_default()
	monitor = display.get_primary_monitor()
	if monitor is None:  # not every real/virtual display reports one (confirmed live: Xephyr doesn't)
		monitor = display.get_monitor(0)
	geometry = monitor.get_geometry()
	return geometry.width, geometry.height


def get_scale_factor():
	"""The real, live scale factor for whatever monitor Gdk's default
	display is actually running on - computed once, cached for the rest
	of this process's life (a resolution change mid-session isn't a real
	scenario this app needs to handle - real hardware's own resolution
	is fixed for the life of a boot).

	min() of the width and height ratios, not just width - a display
	with a different aspect ratio than 1920x1080 (portrait, ultrawide,
	a 1280x720 test window, ...) still has every tile genuinely fit both
	axes rather than overflowing whichever one grew proportionally more,
	at the cost of some unused margin on the other axis - the same
	"reserve extra room rather than let something clip" preference this
	app's own zoom-on-highlight tiles already established (see
	app.py's animate_tile_zoom()).

	This is the SAFE factor - use it (via px()) for anything that must
	never overflow either axis: tile/icon/font sizes. For spacing/margin
	values, where filling unused room is more valuable than never
	overflowing (spacing has no content of its own to clip), use
	px_h()/px_v() instead - see their own docstrings for why a portrait
	screen otherwise left a real, reported dead gap at the bottom of the
	page."""
	global _scale_factor
	if _scale_factor is not None:
		return _scale_factor
	width, height = _get_geometry()
	factor = min(width / REFERENCE_WIDTH, height / REFERENCE_HEIGHT)
	log.info(f"real display is {width}x{height} (reference {REFERENCE_WIDTH}x{REFERENCE_HEIGHT}) -> scale factor {factor:.3f}")
	_scale_factor = factor
	return _scale_factor


def is_portrait():
	"""True if the real display is genuinely taller than it is wide - a
	real target this project cares about (an arcade cabinet marquee, a
	vertical monitor), not just a stress-test shape. Callers use this to
	pick a genuinely different arrangement for a screen's own content
	(see App.build_dashboard_big_tiles_row()'s own portrait branch),
	not just a smaller version of the same landscape layout - two big
	tiles side by side on a narrow-but-tall screen leaves most of the
	real height empty no matter how much the spacing between them
	grows, confirmed live (a real reported dead gap at 720x1280)."""
	width, height = _get_geometry()
	return height > width


def get_horizontal_factor():
	"""The raw, UNCAPPED width ratio (real_width / REFERENCE_WIDTH) - not
	clamped against the height ratio the way get_scale_factor() is. Only
	meant for px_h() below (horizontal spacing/margins), never for a
	real element size (which must stay on the safe min()-based factor to
	guarantee it never overflows)."""
	global _h_factor
	if _h_factor is not None:
		return _h_factor
	width, _height = _get_geometry()
	_h_factor = width / REFERENCE_WIDTH
	return _h_factor


def get_vertical_factor():
	"""The raw, UNCAPPED height ratio - see get_horizontal_factor()'s own
	docstring, same idea for the vertical axis. Only meant for px_v()
	(vertical spacing/margins)."""
	global _v_factor
	if _v_factor is not None:
		return _v_factor
	_width, height = _get_geometry()
	_v_factor = height / REFERENCE_HEIGHT
	return _v_factor


def px(value):
	"""Scales one raw pixel value (a set_size_request()/spacing()/
	border_width() argument, or a module-level *_SIZE/*_WIDTH/*_HEIGHT
	constant in app.py) by the real display's own scale factor - the
	single call every fixed-pixel dimension in this app routes through,
	so there's one real source of truth for "what does 42px actually
	mean on THIS screen" rather than each call site reimplementing the
	same math. Always rounds to a whole int - every real caller is a
	GTK integer-pixel API. For a fractional per-tick animation amount
	(e.g. a scroll speed), use scale() instead - round()ing a small
	sub-pixel step here can floor it to exactly 0 at a smaller scale
	factor, silently freezing whatever it was supposed to animate."""
	return round(value * get_scale_factor())


def scale(value):
	"""Like px(), but returns the raw float rather than rounding to an
	int - for a fractional per-tick amount (e.g.
	PLATFORM_DESC_SCROLL_STEP) added directly to a Gtk.Adjustment's own
	float value, where px()'s rounding could floor a small step to 0 and
	silently freeze the animation entirely at a smaller scale factor."""
	return value * get_scale_factor()


def px_h(value):
	"""Scales a HORIZONTAL spacing/margin value (set_column_spacing(),
	set_margin_start()/set_margin_end(), the spacing= of a HORIZONTAL
	Gtk.Box) by the real display's own raw width ratio - not the safe
	min()-based factor px() uses. Real report, confirmed live: a
	720x1280 portrait test window left a large dead gap of literally
	unused space at the bottom of the page, because every element
	correctly used the safe (width-constrained, in that case) factor to
	never overflow, but the GAPS between them stayed just as small as
	on a 1080p screen instead of growing to use the real extra room -
	spacing has no content of its own to clip, so there's no safety
	reason to hold it back the way a tile/icon/font size needs to be."""
	return round(value * get_horizontal_factor())


def px_v(value):
	"""Scales a VERTICAL spacing/margin value (set_row_spacing(),
	set_margin_top()/set_margin_bottom(), set_border_width(), the
	spacing= of a VERTICAL Gtk.Box) by the real display's own raw height
	ratio - see px_h()'s own docstring, the same reasoning for the other
	axis. This is the one that actually fixes the reported portrait dead
	gap, since that case was height_ratio > the safe scale factor."""
	return round(value * get_vertical_factor())


_PX_RE = re.compile(r"(-?\d+(?:\.\d+)?)px")


def scale_css(css_text):
	"""Rewrites every literal "<number>px" token in a CSS string by the
	real display's own scale factor - GTK3's CSS engine has no relative
	units (em/rem/vw/vh) or calc() support at all (confirmed elsewhere in
	this project - see theme.css's own note on `transform` not existing
	either), so rewriting the raw px literals before the stylesheet is
	ever parsed is the real, working way to make an existing px-authored
	stylesheet resolution-adaptive, short of hand-converting every rule
	to some other unit system. Only whole/decimal px values are touched -
	border-radius: 0, alpha()'s own 0-1 arguments, and anything not
	literally followed by "px" (color hex codes, keyframe percentages,
	font-weight numbers, ...) are left untouched by construction, since
	the regex only matches the "<number>px" pattern itself. Always
	rounds to a whole px in the output - GTK's CSS engine accepting
	fractional px isn't confirmed either way, and there's no real reason
	to risk it."""
	factor = get_scale_factor()
	return _PX_RE.sub(lambda m: f"{round(float(m.group(1)) * factor)}px", css_text)
