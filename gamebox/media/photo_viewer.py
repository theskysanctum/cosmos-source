#!/usr/bin/env python3
"""CosmOS's own full-screen photo viewer - plain Gtk.Image + Pillow,
no playback engine needed at all (unlike media/vlc_player.py). Pillow
is already bundled into the AppImage for other features, so this adds
no new dependency."""
import sys
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk  # noqa: E402

from PIL import Image  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import debuglog  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "launcher"))
import scaling  # noqa: E402

log = debuglog.setup("photo_viewer")


def _load_pixbuf_fit(path, max_width, max_height):
	"""Loads path scaled down to fit inside max_width x max_height,
	preserving aspect ratio - GdkPixbuf can't decode every real-world
	photo format on its own (HEIC in particular), so this always goes
	through Pillow first and hands GdkPixbuf a plain RGB buffer rather
	than trying GdkPixbuf.new_from_file() directly."""
	image = Image.open(path)
	image = image.convert("RGB")
	image.thumbnail((max_width, max_height), Image.LANCZOS)
	data = image.tobytes()
	width, height = image.size
	return GdkPixbuf.Pixbuf.new_from_data(
		data, GdkPixbuf.Colorspace.RGB, False, 8, width, height, width * 3, None, None
	)


class PhotoViewerPage(Gtk.Box):
	"""A full page for app.py's own Gtk.Stack - construct once, reuse
	across every "view this album" action via show_album()."""

	def __init__(self, on_back):
		super().__init__(orientation=Gtk.Orientation.VERTICAL)
		self.on_back = on_back
		self.photos = []
		self.index = 0

		self.override_background_color(Gtk.StateFlags.NORMAL, Gdk.RGBA(0, 0, 0, 1))

		self.image = Gtk.Image()
		self.image.set_hexpand(True)
		self.image.set_vexpand(True)
		self.pack_start(self.image, True, True, 0)

		self.title_label = Gtk.Label(xalign=0.5)
		self.title_label.get_style_context().add_class("title")
		self.pack_start(self.title_label, False, False, scaling.px(8))

		controls = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=scaling.px(12), halign=Gtk.Align.CENTER)
		controls.set_border_width(scaling.px(16))

		prev_button = Gtk.Button(label="Previous")
		prev_button.connect("clicked", lambda _b: self.show_previous())
		controls.pack_start(prev_button, False, False, 0)

		back_button = Gtk.Button(label="Back")
		back_button.connect("clicked", lambda _b: self.on_back())
		controls.pack_start(back_button, False, False, 0)

		next_button = Gtk.Button(label="Next")
		next_button.connect("clicked", lambda _b: self.show_next())
		controls.pack_start(next_button, False, False, 0)

		self.pack_start(controls, False, False, 0)

	def show_album(self, photos, start_index=0):
		"""photos is a list of the same {"id", "title", "path"} dicts
		addon_backend.list_items()/list_photos() already returns. Sizing
		the pixbuf needs this page's real on-screen allocation, which a
		Gtk.Stack child that's only just now becoming visible may not
		have settled into within the same call - deferred to the next
		idle turn (after switch_to()'s own layout pass) rather than
		reading a stale/near-zero allocation and rendering solid black."""
		log.info(f"show_album: {len(photos)} photo(s), starting at {start_index}")
		self.photos = photos
		self.index = start_index
		GLib.idle_add(self._show_current)

	def show_next(self):
		if self.photos:
			self.index = (self.index + 1) % len(self.photos)
			self._show_current()

	def show_previous(self):
		if self.photos:
			self.index = (self.index - 1) % len(self.photos)
			self._show_current()

	def _show_current(self):
		if not self.photos:
			return
		photo = self.photos[self.index]
		self.title_label.set_text(photo.get("title", ""))
		allocation = self.get_allocation()
		max_width = allocation.width or 1920
		max_height = (allocation.height or 1080) - scaling.px(120)  # leave room for title+controls
		try:
			pixbuf = _load_pixbuf_fit(photo["path"], max_width, max_height)
			self.image.set_from_pixbuf(pixbuf)
		except Exception:
			log.exception(f"failed to load photo {photo['path']!r}")
			self.image.clear()
