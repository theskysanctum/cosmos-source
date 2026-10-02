#!/usr/bin/env python3
"""Standalone gamepad-native Epic Games client - the "own frontend"
alternative to the Epic Games Launcher's desktop UI, backed by
legendary_backend.py (which wraps the open-source `legendary` CLI).

Deliberately its own process/window, launched by CosmOS the same way
Steam is (see library/systems.json's "PC" category "launch" entries),
not a screen bolted onto launcher/app.py's Gtk.Stack - Epic's library
browsing/install/launch concerns are unrelated to the ROM-scanning
menu's, and a short-lived process re-spawned fresh from disk on every
launch (same as MAME) doesn't need hot-reload machinery: there's no
long-lived state here worth preserving across an edit the way the
launcher's own persistent window has.

Visually this reuses launcher/theme.py + theme.css directly (the same
"Nebula Engine" look - console-collision colors, Rajdhani font, galaxy
background) rather than recreating anything from Epic's own branding:
no Epic logo, no Epic color palette, no attempt to reproduce Epic's
actual (nonexistent) desktop UI. The only Epic-sourced visuals are cover
art thumbnails for games the signed-in account actually owns, the same
way any legitimate library client (Heroic, Lutris, GOG Galaxy) displays
box art for owned titles."""
import json
import sys
import threading
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("WebKit2", "4.1")
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk, WebKit2  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
import legendary_backend  # noqa: E402
import freegames_backend  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "launcher"))
import theme  # noqa: E402
import scaling  # noqa: E402

import debuglog  # noqa: E402
import paths  # noqa: E402

log = debuglog.setup("epic_app")

COVER_CACHE_DIR = paths.DATA_ROOT / "cache" / "epic-covers"
COVER_WIDTH = scaling.px(280)  # 10-foot-interface sizing - see launcher/theme.css's own base-font-size comment
COVER_HEIGHT = scaling.px(375)

EPIC_LOGIN_URL = "https://www.epicgames.com/id/login"

# Mirrors legendary's own pywebview-based login bridge (legendary/utils/
# webview_login.py) so Epic's real login page - which checks for this
# window.ue API to decide it's running inside a "launcher" webview - hands
# the exchange code straight back via JS instead of showing the raw JSON
# a plain browser visit would. No manual copy-paste: a captcha and Epic's
# own anti-automation checks make the non-webview flow unreliable.
LOGIN_BRIDGE_JS = """
window.ue = {
	signinprompt: {
		requestexchangecodesignin: function(exchangeCode, param) {
			window.webkit.messageHandlers.epicAuth.postMessage(JSON.stringify({type: "exchange_code", code: exchangeCode}));
		},
		registersignincompletecallback: function() {
			window.webkit.messageHandlers.epicAuth.postMessage(JSON.stringify({type: "sid_trigger"}));
		}
	},
	common: {
		launchexternalurl: function(url) {
			window.webkit.messageHandlers.epicAuth.postMessage(JSON.stringify({type: "external_url", url: url}));
		}
	}
};
"""

# Fallback path: some sign-in routes (e.g. platform-account sign-in)
# trigger registersignincompletecallback instead of handing over an
# exchange code directly - legendary's own login does this same follow-up
# XHR to fetch a session id (SID) in that case.
SID_FETCH_JS = """
(function() {
	var req = new XMLHttpRequest();
	req.addEventListener("load", function() {
		window.webkit.messageHandlers.epicAuth.postMessage(JSON.stringify({type: "sid", sid_response: this.responseText}));
	});
	req.open("GET", "/id/api/redirect?");
	req.send();
})();
"""


def run_async(work, on_done):
	"""Runs `work` (no-arg callable) on a thread, delivers its result to
	on_done on the GTK thread via GLib.idle_add - same shape as
	launcher/app.py's App.run_async, duplicated locally rather than
	imported since this process has no App instance to hang it off of."""

	def target():
		result = work()
		GLib.idle_add(on_done, result)

	threading.Thread(target=target, daemon=True).start()


def _fetch_cover(app_name, cover_url):
	"""Downloads and caches cover art to a local file, returns the local
	path (or None if there's no URL or the fetch fails) - called from a
	background thread, never touches GTK. Cached indefinitely by
	app_name; a game's box art doesn't change often enough to justify
	re-fetching every launch."""
	if not cover_url:
		return None
	COVER_CACHE_DIR.mkdir(parents=True, exist_ok=True)
	suffix = Path(cover_url).suffix or ".jpg"
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


class EpicApp(Gtk.Window):
	def __init__(self):
		super().__init__(title="Epic Games")
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
		self.installed = set()

		self.build_library_page()

		authenticated, _ = legendary_backend.is_authenticated()
		if authenticated:
			self.stack.set_visible_child_name("library")
			GLib.idle_add(self.refresh_library)
		else:
			# Built lazily, only when actually needed - it owns a live
			# WebView that starts loading Epic's real login page the
			# moment it's constructed, no point paying that cost (or the
			# network request) on every launch when already logged in.
			self.build_login_page()
			self.stack.set_visible_child_name("login")

	def on_key(self, _widget, event):
		"""Escape quits, unconditionally - not gated on typing, unlike 'q'
		used to be, specifically so it still works while the login
		screen's WebView (a real DOM page, including its own password
		field) or the options Gtk.Entry has focus. 'o' jumps to the
		launch-options entry, gated on typing since it's a real letter
		that must reach the options Gtk.Entry/the login WebView's own DOM
		input fields normally instead of being intercepted. 'f' starts
		the one-time free-games auto-claim login (see
		start_freegames_login()) - only meaningful on the library screen,
		same reasoning as 'o'."""
		focus = self.get_focus()
		typing = isinstance(focus, (Gtk.Entry, WebKit2.WebView))
		if event.keyval == Gdk.KEY_Escape:
			log.info("quit requested")
			Gtk.main_quit()
			return True
		if event.keyval == Gdk.KEY_o and not typing and self.selected_app is not None:
			self.options_entry.grab_focus()
			return True
		if event.keyval == Gdk.KEY_f and not typing and self.stack.get_visible_child_name() == "library":
			self.start_freegames_login()
			return True
		return False

	def start_freegames_login(self):
		"""Kicks off freegames_backend.run_interactive_login() - a real
		Firefox window opens (on this process's own DISPLAY, the shared X
		session) for the user to log into Epic by hand, once. Nothing
		else in CosmOS needs this to be interactive ever again: the
		launcher's own passive background check (see launcher/app.py's
		refresh_dashboard()) reuses the saved session headless from then
		on. Already logged in? Re-running it is still safe (Playwright
		reuses the existing session, likely without even prompting), so
		this doesn't bother checking freegames_backend.is_logged_in()
		first - simpler than building a separate "already enabled"
		status path for what's just a one-time setup action."""
		self.library_hint.set_text("Opening a browser to log into Epic for free-games auto-claim...")
		run_async(freegames_backend.run_interactive_login, self.on_freegames_login_done)

	def on_freegames_login_done(self, success):
		if success:
			self.library_hint.set_text("Free-games auto-claim enabled - it'll check quietly from now on.")
		else:
			self.library_hint.set_text("Free-games login didn't complete - see logs, or press f to try again.")

	# -- login ------------------------------------------------------------

	def build_login_page(self):
		"""Embeds Epic's real login page (WebKit2, same engine class as the
		actual Epic Games Launcher's own embedded browser) rather than a
		manual "paste this code" form - typing/2FA/captcha all happen for
		real, on Epic's own page; injected JS (LOGIN_BRIDGE_JS) intercepts
		the exchange code the page hands back once sign-in succeeds, the
		same way legendary's own pywebview-based login works. Real
		keyboard/gamepad-OSK input reaches the page normally: the OSK types
		via a genuine UInput virtual keyboard device at the kernel level
		(see osk/osk.py), landing wherever real X focus already is -
		WebKit's own DOM text fields included, no special-casing needed."""
		box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=scaling.px(6))
		box.set_border_width(scaling.px(22))

		title = Gtk.Label(label="Epic Games - Sign In", xalign=0)
		title.get_style_context().add_class("title")
		box.pack_start(title, False, False, 0)

		stripe = Gtk.Box()
		stripe.get_style_context().add_class("header-stripe")
		box.pack_start(stripe, False, False, scaling.px(8))

		self.login_status = Gtk.Label(xalign=0)
		self.login_status.get_style_context().add_class("dim-label")
		box.pack_start(self.login_status, False, False, 0)

		manager = WebKit2.UserContentManager()
		manager.register_script_message_handler("epicAuth")
		manager.connect("script-message-received::epicAuth", self.on_epic_auth_message)

		self.login_webview = WebKit2.WebView.new_with_user_content_manager(manager)
		self.login_webview.connect("load-changed", self.on_login_load_changed)
		self.login_webview.set_vexpand(True)
		self.login_webview.load_uri(EPIC_LOGIN_URL)
		box.pack_start(self.login_webview, True, True, 0)

		hint = Gtk.Label(label="Sign in above with your real Epic account   Escape: quit")
		hint.get_style_context().add_class("dim-label")
		box.pack_start(hint, False, False, 0)

		box.show_all()
		self.stack.add_named(box, "login")

	def on_login_load_changed(self, webview, load_event):
		if load_event == WebKit2.LoadEvent.FINISHED:
			webview.evaluate_javascript(LOGIN_BRIDGE_JS, len(LOGIN_BRIDGE_JS), None, None, None, None, None)

	def on_epic_auth_message(self, _manager, js_result):
		try:
			payload = json.loads(js_result.get_js_value().to_string())
		except (json.JSONDecodeError, AttributeError):
			log.exception("could not parse epicAuth message")
			return
		msg_type = payload.get("type")
		log.debug(f"epicAuth message: {msg_type}")

		if msg_type == "exchange_code":
			self.login_status.set_text("Signing in...")
			code = payload["code"]
			run_async(lambda: legendary_backend.auth_via_exchange_token(code), self.on_login_done)
		elif msg_type == "sid_trigger":
			self.login_webview.evaluate_javascript(SID_FETCH_JS, len(SID_FETCH_JS), None, None, None, None, None)
		elif msg_type == "sid":
			try:
				sid = json.loads(payload["sid_response"])["sid"]
			except (json.JSONDecodeError, KeyError):
				log.warning(f"could not extract sid from response: {payload.get('sid_response')!r}")
				return
			self.login_status.set_text("Signing in...")
			run_async(lambda: legendary_backend.auth_via_sid(sid), self.on_login_done)
		elif msg_type == "external_url":
			log.info(f"Epic login page requested an external URL (not opened automatically): {payload.get('url')}")

	def on_login_done(self, success):
		if not success:
			self.login_status.set_text("Login failed - see logs for details. Reloading sign-in page...")
			self.login_webview.load_uri(EPIC_LOGIN_URL)
			return
		self.login_status.set_text("")
		self.stack.set_visible_child_name("library")
		self.refresh_library()

	# -- library ------------------------------------------------------------

	def build_library_page(self):
		box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=scaling.px(6))
		box.set_border_width(scaling.px(22))

		title = Gtk.Label(label="Epic Games", xalign=0)
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
		"""Background-thread work: fetch the library list, installed set,
		and every cover-art image (downloading/caching any not already
		local) - all before touching GTK, so building the grid on the main
		thread is just reading already-local files, no network stalls."""
		library = legendary_backend.list_library()
		installed = legendary_backend.list_installed()
		for game in library:
			game["cover_path"] = _fetch_cover(game["app_name"], game.get("cover_url"))
		return library, installed

	def on_library_loaded(self, result):
		self.library, self.installed = result
		for child in self.library_flow.get_children():
			self.library_flow.remove(child)

		if not self.library:
			self.library_hint.set_text("No games in this library yet (or the fetch failed - see logs).")
			self._render_detail(None)
			return

		for game in self.library:
			self.library_flow.add(self._make_tile(game))
		# Newly-added widgets default to hidden in GTK3 even when their
		# parent is already visible - without this, the grid silently
		# stays empty despite the FlowBox correctly holding all the
		# children (confirmed by an earlier test: selection/data worked,
		# nothing was actually drawn).
		self.library_flow.show_all()
		self.library_flow.select_child(self.library_flow.get_child_at_index(0))
		self.library_hint.set_text("Arrows: move   Enter: install/play   O: launch options   F: free-games auto-claim   Escape: quit")

	def _make_tile(self, game):
		"""A DVD/Blu-ray case standing on a shelf: a thin spine beside a
		tall face, cover art filling the face edge-to-edge with the title
		captioned over its bottom edge (see theme.css's .dvd-* classes) -
		not the plain gradient-bordered rectangle this used to be, which
		read as a picture frame around empty space rather than a case."""
		child = Gtk.FlowBoxChild()
		child.item = game
		# Without this, FlowBoxChild stretches to fill its row's full
		# height (confirmed visually - tiles ballooned well past the
		# cover art's real size) since a homogeneous FlowBox still hands
		# each cell the row's available height by default.
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
			face.add(Gtk.Box())  # no art - .dvd-face's own dark background shows through

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

		installed = game["app_name"] in self.installed
		action_label = "play" if installed else "install"
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

		options_label = Gtk.Label(label="Launch options (Steam-style: VAR=val ... %command% extra args):", xalign=0)
		options_label.set_line_wrap(True)
		options_label.get_style_context().add_class("dim-label")
		self.detail_box.pack_start(options_label, False, False, 0)

		self.options_entry = Gtk.Entry()
		self.options_entry.set_text(legendary_backend.get_launch_options(game["app_name"]))
		self.options_entry.connect("activate", lambda _e: self.save_launch_options(game))
		self.detail_box.pack_start(self.options_entry, False, False, 0)

		self.options_status = Gtk.Label(xalign=0)
		self.options_status.get_style_context().add_class("dim-label")
		self.detail_box.pack_start(self.options_status, False, False, 0)

		self.detail_box.show_all()
		self.progress_bar.hide()
		self.progress_log.hide()

	def save_launch_options(self, game):
		legendary_backend.set_launch_options(game["app_name"], self.options_entry.get_text())
		self.options_status.set_text("Saved.")
		self.library_flow.grab_focus()

	def activate_selected_game(self):
		game = self.selected_app
		if not game:
			return
		if game["app_name"] in self.installed:
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

			return legendary_backend.install(game["app_name"], on_line)

		run_async(work, lambda success: self.on_install_done(game, success))

	def _on_install_progress(self, line, percent):
		self.progress_log.set_text(line)
		if percent is not None:
			self.progress_bar.set_fraction(min(percent / 100.0, 1.0))

	def on_install_done(self, game, success):
		self.library_flow.set_sensitive(True)
		if success:
			self.installed.add(game["app_name"])
			self.progress_log.set_text("Install complete.")
		else:
			self.progress_log.set_text("Install failed - see logs for details.")
		self._render_detail(game)

	def launch_selected_game(self, game):
		log.info(f"launching {game['app_name']} ({game['app_title']})")
		self.hide()
		run_async(lambda: legendary_backend.launch(game["app_name"]), lambda _r: self.show())


def main():
	theme.load_theme()
	win = EpicApp()
	win.show_all()
	log.info("entering Gtk.main()")
	Gtk.main()


if __name__ == "__main__":
	main()
