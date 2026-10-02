#!/usr/bin/env python3
"""CosmOS's own made-up controller button badges - not modeled on any
real platform's actual layout, letters, or branding (matching theme.css's
own "not tied to any single manufacturer" ethos, the same reasoning
behind picking generic icons over Xbox/PlayStation glyph packs), just a
small, original set of badges for the buttons this project actually maps
to something (see input/inputd.py's ACTION_BUTTON_MAP):

    N (Nova)     - BTN_SOUTH        - confirm/select
    V (Void)     - BTN_EAST         - back/cancel
    F (Flux)     - BTN_WEST         - delete/remove
    TL / TR      - BTN_TL/BTN_TR    - shoulders/bumpers (reorder, etc.)
    TL2 / TR2    - BTN_TL2/BTN_TR2  - triggers (rotate the live preview -
                                       see avatar3d_viewer.AvatarCustomizePage)
    Cosmos       - BTN_MODE         - opens the Controller Help overlay

TL/TR (not the old single-letter L/R) get their own trigger-shaped badge
(theme.css's .button-badge-trigger: flatter, wider, sharp outer corners)
rather than the round face-button badge shape, since a shoulder
bumper/trigger reads as a genuinely different kind of control, not just
another face button that happens to say something else.

The Cosmos button's own badge is the real line-art mark drawn on it in
branding/cosmos_controller.png (cropped out as branding/
cosmos_button.png) - a real controller's own Guide/Home button always
shows the platform's own logo rather than a letter, so this is the one
badge that's an image, not text.

BTN_NORTH has no mapped action yet, so it has no badge here either -
add one (a "Pulse"/P badge would fit the naming pattern) when something
actually uses it, rather than inventing a badge for a button that does
nothing."""
from pathlib import Path

from gi.repository import GdkPixbuf, Gtk

import scaling

CONFIRM = "N"
BACK = "V"
DELETE = "F"
SHOULDER_LEFT = "TL"
SHOULDER_RIGHT = "TR"
TRIGGER_LEFT = "TL2"
TRIGGER_RIGHT = "TR2"
COSMOS = "COSMOS"

_TRIGGER_SHAPED = (SHOULDER_LEFT, SHOULDER_RIGHT, TRIGGER_LEFT, TRIGGER_RIGHT)
_ALL_BADGES = (CONFIRM, BACK, DELETE, SHOULDER_LEFT, SHOULDER_RIGHT, TRIGGER_LEFT, TRIGGER_RIGHT, COSMOS)

LOGO_BADGE_PATH = Path(__file__).resolve().parent.parent / "branding" / "cosmos_button.png"
LOGO_BADGE_HEIGHT = scaling.px(26)  # matches the ~26-30px cap-height the letter badges already read at


def make_logo_badge():
	image = Gtk.Image()
	pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(str(LOGO_BADGE_PATH), -1, LOGO_BADGE_HEIGHT, True)
	image.set_from_pixbuf(pixbuf)
	return image


def make_badge(letter):
	if letter == COSMOS:
		return make_logo_badge()
	label = Gtk.Label(label=letter)
	css_class = "button-badge-trigger" if letter in _TRIGGER_SHAPED else "button-badge"
	label.get_style_context().add_class(css_class)
	return label


def make_hint_row(*parts):
	"""Builds a horizontal Gtk.Box hint line out of a mix of plain text
	strings and button badges (pass a badge letter constant, e.g.
	controller_glyphs.CONFIRM, to insert one) - the same "Arrows: move
	[N] select" shape every hint row in the app wants, without each
	caller hand-assembling the Box/Label mix itself."""
	row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=scaling.px_h(6))
	for part in parts:
		if part in _ALL_BADGES:
			row.pack_start(make_badge(part), False, False, 0)
		else:
			label = Gtk.Label(label=part)
			label.get_style_context().add_class("dim-label")
			row.pack_start(label, False, False, 0)
	return row
