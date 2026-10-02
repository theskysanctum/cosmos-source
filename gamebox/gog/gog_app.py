#!/usr/bin/env python3
"""Standalone gamepad-native GOG client - same shape as epic/epic_app.py
(its own process, launched externally by CosmOS - see library/
systems.json's "PC" category), backed by gogdl_backend.py. Visually
identical DVD-case library grid (theme.css's .dvd-* classes are shared,
not duplicated) so the two storefront clients read as the same system.

GOG's login is simpler than Epic's: no JS bridge needed: the OAuth flow
just redirects the embedded webview to a URL carrying a `code` query
parameter once sign-in succeeds, so this only has to watch the WebView's
current URL rather than inject anything - see on_login_uri_changed()."""
import sys
import threading
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("WebKit2", "4.1")
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk, WebKit2  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gogdl_backend  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "launcher"))
import theme  # noqa: E402
import scaling  # noqa: E402

import debuglog  # noqa: E402
import paths  # noqa: E402

log = debuglog.setup("gog_app")

COVER_CACHE_DIR = paths.DATA_ROOT / "cache" / "gog-covers"
COVER_WIDTH = scaling.px(280)  # 10-foot-interface sizing - see launcher/theme.css's own base-font-size comment
COVER_HEIGHT = scaling.px(375)


def run_async(work, on_done):
	def target():
		result = work()
		GLib.idle_add(on_done, result)

	threading.Thread(target=target, daemon=True).start()


def _fetch_cover(app_name, cover_url):
	if not cover_url:
		return None
	COVER_CACHE_DIR.mkdir(parents=True, exist_ok=True)
	suffix = Path(urlparse(cover_url).path).suffix or ".jpg"
	cache_path = COVER_CACHE_DIR / f"{app_name}{suffix}"
	if cache_path.exists():
		return str(cache_path)
	try:
		import requests

		response = requests.get(cover_url, timeout=6)
		response.raise_for_status()
		cache_path.write_bytes(response.content)
		return str(cache_path)
	except Exception:
		log.exception(f"failed to fetch cover art for {app_name} from {cover_url}")
		return None


def _cover_pixbuf(path, width, height):
	if not path:
		return None
	try:
		return GdkPixbuf.Pixbuf.new_from_file_at_scale(path, width, height, False)
	except GLib.Error:
		log.exception(f"failed to decode cover art at {path}")
		return None


class GogApp(Gtk.Window):
	def __init__(self):
		super().__init__(title="GOG")
		self.fullscreen()
		self.connect("key-press-event", self.on_key)

		self.stack = Gtk.Stack()
		# A real GTK-native transition between login and library instead
		# of a snap cut - matches launcher/app.py's own stack (this app
		# reuses that same theme, see this module's own docstring).
		self.stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
		self.stack.set_transition_duration(220)
		self.add(self.stack)

		self.selected_app = None
		self.library = []

		self.build_library_page()

		authenticated, _ = gogdl_backend.is_authenticated()
		if authenticated:
			self.stack.set_visible_child_name("library")
			GLib.idle_add(self.refresh_library)
		else:
			self.build_login_page()
			self.stack.set_visible_child_name("login")

	def on_key(self, _widget, event):
		"""Escape quits, unconditionally - not gated on typing, unlike 'q'
		used to be, specifically so it still works while the login
		screen's WebView (a real DOM page, including its own password
		field) or the options Gtk.Entry has focus. 'o' jumps to the
		launch-options entry, gated on typing since it's a real letter
		that must reach the options Gtk.Entry/the login WebView's own DOM
		input fields normally instead of being intercepted."""
		focus = self.get_focus()
		typing = isinstance(focus, (Gtk.Entry, WebKit2.WebView))
		if event.keyval == Gdk.KEY_Escape:
			log.info("quit requested")
			Gtk.main_quit()
			return True
		if event.keyval == Gdk.KEY_o and not typing and self.selected_app is not None:
			self.options_entry.grab_focus()
			return True
		return False

	# -- login ------------------------------------------------------------

	def build_login_page(self):
		box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=scaling.px(6))
		box.set_border_width(scaling.px(22))

		title = Gtk.Label(label="GOG - Sign In", xalign=0)
		title.get_style_context().add_class("title")
		box.pack_start(title, False, False, 0)

		stripe = Gtk.Box()
		stripe.get_style_context().add_class("header-stripe")
		box.pack_start(stripe, False, False, scaling.px(8))

		self.login_status = Gtk.Label(xalign=0)
		self.login_status.get_style_context().add_class("dim-label")
		box.pack_start(self.login_status, False, False, 0)

		self.login_webview = WebKit2.WebView()
		self.login_webview.connect("notify::uri", self.on_login_uri_changed)
		self.login_webview.set_vexpand(True)
		self.login_webview.load_uri(gogdl_backend.LOGIN_URL)
		box.pack_start(self.login_webview, True, True, 0)

		hint = Gtk.Label(label="Sign in above with your real GOG account   Escape: quit")
		hint.get_style_context().add_class("dim-label")
		box.pack_start(hint, False, False, 0)

		box.show_all()
		self.stack.add_named(box, "login")

	def on_login_uri_changed(self, webview, _pspec):
		uri = webview.get_uri() or ""
		if not uri.startswith(gogdl_backend.LOGIN_SUCCESS_PREFIX):
			return
		code = parse_qs(urlparse(uri).query).get("code", [None])[0]
		if not code:
			log.warning(f"reached login-success URL but no code param found: {uri}")
			return
		log.debug("got GOG login code, exchanging for tokens")
		self.login_status.set_text("Signing in...")
		run_async(lambda: gogdl_backend.auth_via_code(code), self.on_login_done)

	def on_login_done(self, success):
		if not success:
			self.login_status.set_text("Login failed - see logs for details. Reloading sign-in page...")
			self.login_webview.load_uri(gogdl_backend.LOGIN_URL)
			return
		self.login_status.set_text("")
		self.stack.set_visible_child_name("library")
		self.refresh_library()

	# -- library ------------------------------------------------------------

	def build_library_page(self):
		box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=scaling.px(6))
		box.set_border_width(scaling.px(22))

		title = Gtk.Label(label="GOG", xalign=0)
		title.get_style_context().add_class("title")
		box.pack_start(title, False, False, 0)

		stripe = Gtk.Box()
		stripe.get_style_context().add_class("header-stripe")
		box.pack_start(stripe, False, False, scaling.px(8))

		paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)

		grid_scroller = Gtk.ScrolledWindow()
		grid_scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
		self.library_flow = Gtk.FlowBox()
		self.library_flow.set_selection_mode(Gtk.SelectionMode.BROWSE)
		self.library_flow.set_homogeneous(True)
		self.library_flow.set_row_spacing(scaling.px(12))
		self.library_flow.set_column_spacing(scaling.px(12))
		self.library_flow.set_activate_on_single_click(False)
		self.library_flow.connect("selected-children-changed", self.on_library_selection_changed)
		self.library_flow.connect("child-activated", lambda _fb, _child: self.activate_selected_game())
		grid_scroller.add(self.library_flow)
		paned.pack1(grid_scroller, resize=True, shrink=False)

		self.detail_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=scaling.px(10))
		self.detail_box.set_border_width(scaling.px(17))
		paned.pack2(self.detail_box, resize=True, shrink=False)
		paned.set_position(1064)

		box.pack_start(paned, True, True, 0)

		self.library_hint = Gtk.Label(label="Loading library...")
		self.library_hint.get_style_context().add_class("dim-label")
		box.pack_start(self.library_hint, False, False, 0)

		box.show_all()
		self.stack.add_named(box, "library")
		self._render_detail(None)

	def refresh_library(self):
		self.library_hint.set_text("Loading library...")
		run_async(self._load_library_work, self.on_library_loaded)

	def _load_library_work(self):
		library = gogdl_backend.list_library()
		for game in library:
			game["cover_path"] = _fetch_cover(game["app_name"], game.get("cover_url"))
			game["installed"] = gogdl_backend.is_installed(game["app_name"])
		return library

	def on_library_loaded(self, library):
		self.library = library
		for child in self.library_flow.get_children():
			self.library_flow.remove(child)

		if not self.library:
			self.library_hint.set_text("No games in this library yet (or the fetch failed - see logs).")
			self._render_detail(None)
			return

		for game in self.library:
			self.library_flow.add(self._make_tile(game))
		self.library_flow.show_all()
		self.library_flow.select_child(self.library_flow.get_child_at_index(0))
		self.library_hint.set_text("Arrows: move   Enter: install/play   O: launch options   Escape: quit")

	def _make_tile(self, game):
		child = Gtk.FlowBoxChild()
		child.item = game
		child.set_valign(Gtk.Align.START)

		case = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
		case.get_style_context().add_class("dvd-case")

		spine = Gtk.Box()
		spine.get_style_context().add_class("dvd-spine")
		spine.set_size_request(-1, COVER_HEIGHT)
		case.pack_start(spine, False, False, 0)

		face = Gtk.Overlay()
		face.get_style_context().add_class("dvd-face")
		face.set_size_request(COVER_WIDTH, COVER_HEIGHT)

		pixbuf = _cover_pixbuf(game.get("cover_path"), COVER_WIDTH, COVER_HEIGHT)
		if pixbuf:
			face.add(Gtk.Image.new_from_pixbuf(pixbuf))
		else:
			face.add(Gtk.Box())

		caption = Gtk.Box()
		caption.get_style_context().add_class("dvd-caption")
		caption.set_valign(Gtk.Align.END)
		caption.set_halign(Gtk.Align.FILL)
		label = Gtk.Label(label=game["app_title"], xalign=0)
		label.set_line_wrap(True)
		label.get_style_context().add_class("dvd-title")
		caption.pack_start(label, True, True, 0)
		face.add_overlay(caption)

		case.pack_start(face, False, False, 0)
		child.add(case)
		return child

	def on_library_selection_changed(self, flowbox):
		selected = flowbox.get_selected_children()
		game = selected[0].item if selected else None
		self._render_detail(game)

	def _render_detail(self, game):
		for child in self.detail_box.get_children():
			self.detail_box.remove(child)
		self.selected_app = game
		if not game:
			placeholder = Gtk.Label(label="Select a game", xalign=0)
			placeholder.get_style_context().add_class("dim-label")
			self.detail_box.pack_start(placeholder, False, False, 0)
			self.detail_box.show_all()
			return

		name = Gtk.Label(label=game["app_title"], xalign=0)
		name.get_style_context().add_class("dashboard-card-title")
		name.set_line_wrap(True)
		self.detail_box.pack_start(name, False, False, 0)

		action_label = "play" if game.get("installed") else "install"
		action_hint = Gtk.Label(label=f"Enter: {action_label}   O: launch options", xalign=0)
		action_hint.get_style_context().add_class("dashboard-line")
		self.detail_box.pack_start(action_hint, False, False, 0)

		self.progress_bar = Gtk.ProgressBar()
		self.progress_bar.set_no_show_all(True)
		self.detail_box.pack_start(self.progress_bar, False, False, 0)

		self.progress_log = Gtk.Label(xalign=0, wrap=True)
		self.progress_log.get_style_context().add_class("dim-label")
		self.progress_log.set_no_show_all(True)
		self.detail_box.pack_start(self.progress_log, False, False, 0)

		# gogdl's launch command has no way to pass extra arguments to the
		# game itself (only a %command%-style wrapper) - the hint says so
		# up front rather than letting someone set "-windowed" here and
		# quietly have it do nothing (see _parse_launch_options()'s
		# extra_args warning in gogdl_backend.py).
		options_label = Gtk.Label(label="Launch options (VAR=val ... %command% wrapper - extra game args aren't supported by gogdl):", xalign=0)
		options_label.set_line_wrap(True)
		options_label.get_style_context().add_class("dim-label")
		self.detail_box.pack_start(options_label, False, False, 0)

		self.options_entry = Gtk.Entry()
		self.options_entry.set_text(gogdl_backend.get_launch_options(game["app_name"]))
		self.options_entry.connect("activate", lambda _e: self.save_launch_options(game))
		self.detail_box.pack_start(self.options_entry, False, False, 0)

		self.options_status = Gtk.Label(xalign=0)
		self.options_status.get_style_context().add_class("dim-label")
		self.detail_box.pack_start(self.options_status, False, False, 0)

		self.detail_box.show_all()
		self.progress_bar.hide()
		self.progress_log.hide()

	def save_launch_options(self, game):
		gogdl_backend.set_launch_options(game["app_name"], self.options_entry.get_text())
		self.options_status.set_text("Saved.")
		self.library_flow.grab_focus()

	def activate_selected_game(self):
		game = self.selected_app
		if not game:
			return
		if game.get("installed"):
			self.launch_selected_game(game)
		else:
			self.install_selected_game(game)

	def install_selected_game(self, game):
		log.info(f"installing {game['app_name']} ({game['app_title']})")
		self.progress_bar.show()
		self.progress_bar.set_fraction(0.0)
		self.progress_log.show()
		self.progress_log.set_text("Starting install...")
		self.library_flow.set_sensitive(False)

		def work():
			def on_line(line, percent):
				GLib.idle_add(self._on_install_progress, line, percent)

			return gogdl_backend.install(game["app_name"], on_line)

		run_async(work, lambda success: self.on_install_done(game, success))

	def _on_install_progress(self, line, percent):
		self.progress_log.set_text(line)
		if percent is not None:
			self.progress_bar.set_fraction(min(percent / 100.0, 1.0))

	def on_install_done(self, game, success):
		self.library_flow.set_sensitive(True)
		if success:
			game["installed"] = True
			self.progress_log.set_text("Install complete.")
		else:
			self.progress_log.set_text("Install failed - see logs for details.")
		self._render_detail(game)

	def launch_selected_game(self, game):
		log.info(f"launching {game['app_name']} ({game['app_title']})")
		self.hide()
		run_async(lambda: gogdl_backend.launch(game["app_name"]), lambda _r: self.show())


def main():
	theme.load_theme()
	win = GogApp()
	win.show_all()
	log.info("entering Gtk.main()")
	Gtk.main()


if __name__ == "__main__":
	main()
