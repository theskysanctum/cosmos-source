#!/usr/bin/env python3
"""CosmOS's own embedded player - libVLC (via python-vlc) rendered
directly into a GTK widget inside the launcher's own window, not a
foreign app's own separate window (Kodi and Stremio were both tried and
dropped for exactly that reason - see the plan file). Shared between
Video and Music: the same underlying vlc.MediaPlayer, Music just has no
real reason to show its own video surface (there isn't one) so the
drawing area stays a plain black rectangle behind the same controls.

Real, standard libVLC/GTK embedding technique (VLC's own official
examples do exactly this, confirmed live against a real X11 session):
realize a Gtk.DrawingArea, read its real X11 window id
(widget.get_window().get_xid()), hand that to
vlc.MediaPlayer.set_xwindow() - libVLC then renders straight into that
already-mapped GTK-owned window, no separate top-level window at all."""
import sys
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import debuglog  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "launcher"))
import scaling  # noqa: E402

import vlc  # noqa: E402

log = debuglog.setup("vlc_player")

# How often the progress bar/time label poll the real player state -
# cheap enough (get_time()/get_length() are simple libvlc getters, no
# real I/O) to do on a plain GLib timeout rather than needing libvlc's
# own event manager for this.
POLL_INTERVAL_MS = 500


class PlayerPage(Gtk.Box):
	"""A full page for app.py's own Gtk.Stack - construct once, reuse
	across every Play action (play() resets it to a new item each time,
	matching how the rest of this project reuses one page and just
	changes its content rather than rebuilding widgets per launch)."""

	def __init__(self, on_back):
		"""on_back is called (no args) when the user backs out - app.py
		wires this to its own go_back()/switch_to("..."), matching every
		other page's own on_back handling."""
		super().__init__(orientation=Gtk.Orientation.VERTICAL)
		self.on_back = on_back
		self.on_end_of_media = None
		self._poll_source_id = None

		self.instance = vlc.Instance()
		self.player = self.instance.media_player_new()
		event_manager = self.player.event_manager()
		event_manager.event_attach(vlc.EventType.MediaPlayerEndReached, self._on_vlc_end_reached)

		self.video_area = Gtk.DrawingArea()
		self.video_area.set_hexpand(True)
		self.video_area.set_vexpand(True)
		# Plain black background behind Music (no real video surface) and
		# while a video is still loading, before the first real frame.
		self.video_area.override_background_color(Gtk.StateFlags.NORMAL, _BLACK)
		self.video_area.connect("realize", self._on_video_area_realize)
		self.pack_start(self.video_area, True, True, 0)

		# Title + control row share one panel (theme.css's own
		# .player-controls) so they read as a single HUD bar over the
		# video - not two separate unstyled rows floating with no visual
		# grouping between them.
		panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=scaling.px(8))
		panel.get_style_context().add_class("player-controls")

		self.title_label = Gtk.Label(xalign=0)
		self.title_label.get_style_context().add_class("player-title")
		panel.pack_start(self.title_label, False, False, 0)

		controls = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=scaling.px(12))

		self.play_pause_button = Gtk.Button(label="Pause")
		self.play_pause_button.get_style_context().add_class("player-button")
		self.play_pause_button.connect("clicked", lambda _b: self.toggle_play_pause())
		controls.pack_start(self.play_pause_button, False, False, 0)

		self.time_label = Gtk.Label(label="0:00 / 0:00")
		self.time_label.get_style_context().add_class("dim-label")
		controls.pack_start(self.time_label, False, False, 0)

		self.seek_scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 1000, 1)
		self.seek_scale.get_style_context().add_class("player-seek")
		self.seek_scale.set_hexpand(True)
		self.seek_scale.set_draw_value(False)
		self._seek_scale_handler = self.seek_scale.connect("value-changed", self._on_seek_scale_changed)
		controls.pack_start(self.seek_scale, True, True, 0)

		volume_down = Gtk.Button(label="Vol-")
		volume_down.get_style_context().add_class("player-button")
		volume_down.connect("clicked", lambda _b: self.adjust_volume(-10))
		controls.pack_start(volume_down, False, False, 0)

		volume_up = Gtk.Button(label="Vol+")
		volume_up.get_style_context().add_class("player-button")
		volume_up.connect("clicked", lambda _b: self.adjust_volume(10))
		controls.pack_start(volume_up, False, False, 0)

		back_button = Gtk.Button(label="Back")
		back_button.get_style_context().add_class("player-button")
		back_button.connect("clicked", lambda _b: self._handle_back())
		controls.pack_start(back_button, False, False, 0)

		panel.pack_start(controls, False, False, 0)

		# Every other screen in this app shows a hint row for its own
		# input scheme (see make_list_page()/build_menu_page()'s own
		# hint rows in app.py) - this player is no exception, and its
		# real controls are the d-pad/keyboard bindings in app.py's own
		# on_key(), not the small on-screen buttons above (those stay
		# mouse/touch-clickable, but a real console session is gamepad-
		# first - see this project's own established "handle input
		# explicitly per screen" pattern, not GTK's native per-widget
		# keynav, which this codebase has repeatedly found unreliable).
		hint = Gtk.Label(label="Left/Right: seek 10s   Up/Down: volume   Enter: play/pause   Backspace: back")
		hint.get_style_context().add_class("dim-label")
		panel.pack_start(hint, False, False, 0)

		self.pack_start(panel, False, False, 0)

	def _on_video_area_realize(self, widget):
		xid = widget.get_window().get_xid()
		log.debug(f"embedding libvlc into xid {xid}")
		self.player.set_xwindow(xid)

	def play(self, path, title="", audio_url=None):
		"""path is a real, local filesystem path or a real HTTP(S) URL -
		vlc.Instance.media_new() accepts either directly, no separate
		"is this local or remote" branch needed. audio_url, if given, is
		muxed in live as a real libVLC secondary audio track via
		":input-slave=" - never a download - for library/preview_video.py's
		own split video-only+audio-only YouTube resolution (modern
		YouTube rarely serves one combined URL any more), which
		otherwise plays back silently since path alone would only be the
		video stream."""
		log.info(f"play: {title!r} ({path}, audio_url={audio_url!r})")
		options = [f":input-slave={audio_url}"] if audio_url else []
		media = self.instance.media_new(path, *options)
		self.player.set_media(media)
		self.title_label.set_text(title)
		self.player.play()
		self.play_pause_button.set_label("Pause")
		self._start_polling()

	def toggle_play_pause(self):
		self.player.pause()
		# Not self.player.is_playing() - confirmed live that libvlc's own
		# pause() is asynchronous, so reading is_playing() synchronously
		# right after calling it almost always returns the *pre-toggle*
		# state, leaving the label consistently wrong (e.g. still
		# reading "Pause" a full 500ms after actually pausing). We're
		# the one calling pause() here, so we already know which
		# direction this toggle is going - flipping the label's own
		# current text is simple and doesn't depend on libvlc's timing
		# at all.
		self.play_pause_button.set_label("Play" if self.play_pause_button.get_label() == "Pause" else "Pause")

	def adjust_volume(self, delta):
		self.player.audio_set_volume(max(0, min(100, self.player.audio_get_volume() + delta)))

	def seek_relative(self, delta_ms):
		"""Jumps the current position by delta_ms (negative to rewind) -
		the d-pad/keyboard Left-Right binding (see app.py's on_key), not
		just the on-screen seek_scale drag. Also nudges seek_scale's own
		displayed position immediately rather than waiting for the next
		_poll_progress() tick, the same "don't wait on the poll" reasoning
		adjust_volume() doesn't need (it has no matching on-screen slider
		to keep in sync)."""
		length_ms = self.player.get_length()
		if length_ms <= 0:
			return
		new_time_ms = max(0, min(length_ms, self.player.get_time() + delta_ms))
		self.player.set_time(new_time_ms)
		self.seek_scale.handler_block(self._seek_scale_handler)
		self.seek_scale.set_value((new_time_ms / length_ms) * 1000)
		self.seek_scale.handler_unblock(self._seek_scale_handler)

	def stop(self):
		self._stop_polling()
		self.player.stop()

	def _handle_back(self):
		self.stop()
		self.on_back()

	def _on_vlc_end_reached(self, _event):
		"""Fires on libvlc's own event thread, not the GTK main loop - has
		to hop back over via GLib.idle_add before touching any widget or
		calling app.py's own callback, the same rule every other GTK
		callback in this project already follows for cross-thread work."""
		GLib.idle_add(self._handle_end_reached)

	def _handle_end_reached(self):
		self._stop_polling()
		if self.on_end_of_media:
			self.on_end_of_media()

	def _start_polling(self):
		self._stop_polling()
		self._poll_source_id = GLib.timeout_add(POLL_INTERVAL_MS, self._poll_progress)

	def _stop_polling(self):
		if self._poll_source_id is not None:
			GLib.source_remove(self._poll_source_id)
			self._poll_source_id = None

	def _poll_progress(self):
		length_ms = self.player.get_length()
		time_ms = self.player.get_time()
		if length_ms > 0:
			self.seek_scale.handler_block(self._seek_scale_handler)
			self.seek_scale.set_value((time_ms / length_ms) * 1000)
			self.seek_scale.handler_unblock(self._seek_scale_handler)
			self.time_label.set_text(f"{_format_ms(time_ms)} / {_format_ms(length_ms)}")
		return True  # keep polling until _stop_polling() removes this source

	def _on_seek_scale_changed(self, scale):
		length_ms = self.player.get_length()
		if length_ms > 0:
			self.player.set_time(int((scale.get_value() / 1000) * length_ms))


def _format_ms(ms):
	total_seconds = ms // 1000
	minutes, seconds = divmod(total_seconds, 60)
	return f"{minutes}:{seconds:02d}"


_BLACK = Gdk.RGBA(0, 0, 0, 1)
