#!/usr/bin/env python3
"""Theme Studio - a visual editor for CosmOS's own theme system
(theme.py/theme.css/launcher/themes/*.css). Since theme.css itself
never has a literal color in it (see theme.py's own docstring), a new
theme really is just a new launcher/themes/<id>.css palette file
declaring the same ~30 @define-color names to different values -
nothing structural to edit at all, which is exactly what makes this a
small, safe visual editor rather than a full CSS/layout tool.

Live preview applies the in-progress palette as a REAL GTK CssProvider
against a handful of actual theme.css classes (.dashboard-widget-tile,
.menu-icon-tile/.row-focused, .action-button, .dim-label) - what you
see while adjusting colors is the genuine, currently-shipping look of
those exact widgets, not a mocked-up approximation, exactly the same
mechanism theme.load_theme() itself uses (a palette CssProvider loaded
above the shared structural one)."""
import re
import shutil
import sys
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gtk  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import debuglog  # noqa: E402

import scaling  # noqa: E402
import theme  # noqa: E402

log = debuglog.setup("theme_studio")

# The real token list, grouped exactly the way every existing palette
# file (launcher/themes/console_collision.css) already visually groups
# them in its own section comments - not derived automatically (theme
# files don't machine-mark their own sections), but it's the same 30
# names either way, so a group only affects how this editor organizes
# its own form, never which tokens are valid.
TOKEN_GROUPS = [
	("Backgrounds", [
		"bg_primary", "bg_row_selected", "bg_card", "bg_panel", "bg_panel_active",
		"bg_panel_light", "bg_panel_light_active", "bg_panel_solid", "bg_input", "bg_deep",
		"bg_scrim", "bg_scrim_heavy", "bg_scrim_ceremony", "bg_caption", "shadow_color",
	]),
	("Text", ["text_primary", "text_bright", "text_dim", "text_muted", "text_summary", "text_line", "text_warning"]),
	("Borders", ["border_muted", "border_panel", "border_panel_light"]),
	("Accents", ["accent_1", "accent_2", "accent_3", "accent_4", "accent_2_bright"]),
]
ALL_TOKENS = [name for _group, names in TOKEN_GROUPS for name in names]

_HEX_RE = r"#([0-9a-fA-F]{6})"
_RGBA_RE = r"rgba\(\s*([0-9]+)\s*,\s*([0-9]+)\s*,\s*([0-9]+)\s*,\s*([0-9.]+)\s*\)"


def _parse_palette(css_text):
	"""{token: Gdk.RGBA} for every ALL_TOKENS entry @define-color'd in
	css_text - handles both plain #hex and rgba(...) values (a real
	mix in the one existing theme: most tokens are hex, several
	backgrounds are deliberately semi-transparent rgba() overlays).
	Missing tokens (a hand-edited or older palette file that's dropped
	one) just fall back to opaque white below, rather than crashing."""
	colors = {}
	for name in ALL_TOKENS:
		m = re.search(rf"@define-color\s+{re.escape(name)}\s+{_HEX_RE}\s*;", css_text)
		if m:
			rgba = Gdk.RGBA()
			rgba.parse(f"#{m.group(1)}")
			colors[name] = rgba
			continue
		m = re.search(rf"@define-color\s+{re.escape(name)}\s+{_RGBA_RE}\s*;", css_text)
		if m:
			r, g, b, a = m.groups()
			colors[name] = Gdk.RGBA(int(r) / 255, int(g) / 255, int(b) / 255, float(a))
	return colors


def _parse_background_image(css_text):
	m = re.search(r'url\("([^"]+)"\)', css_text)
	return m.group(1) if m else None


def _rgba_to_css(rgba):
	if rgba.alpha >= 0.999:
		return "#{:02x}{:02x}{:02x}".format(round(rgba.red * 255), round(rgba.green * 255), round(rgba.blue * 255))
	return "rgba({}, {}, {}, {:g})".format(
		round(rgba.red * 255), round(rgba.green * 255), round(rgba.blue * 255), round(rgba.alpha, 3),
	)


def _slugify(name):
	out = []
	for ch in name.strip().lower():
		if ch.isalnum():
			out.append(ch)
		elif out and out[-1] != "_":
			out.append("_")
	return "".join(out).strip("_") or "theme"


class ThemeStudio(Gtk.Window):
	def __init__(self):
		super().__init__(title="Theme Studio")
		self.set_default_size(1000, 640)
		self.connect("destroy", Gtk.main_quit)

		self.colors = {}
		self.background_rel_path = None
		self._palette_provider = None

		# theme.css's own structural rules, loaded ONCE (unscaled px
		# values matter no more here than in the real app - same
		# scaling.scale_css() call load_theme() itself uses) - never
		# swapped; only the palette provider gets replaced as colors
		# change, exactly mirroring theme.py's own two-provider design.
		self._structure_provider = Gtk.CssProvider()
		self._structure_provider.load_from_data(scaling.scale_css(theme.THEME_CSS_PATH.read_text()).encode())
		Gtk.StyleContext.add_provider_for_screen(
			Gdk.Screen.get_default(), self._structure_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
		)

		root_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12, margin=12)
		self.add(root_box)

		preview_frame = Gtk.Frame(label="Live preview")
		self.preview_box = self._build_preview_widgets()
		preview_frame.add(self.preview_box)
		root_box.pack_start(preview_frame, True, True, 0)

		side = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
		side.set_size_request(360, -1)
		root_box.pack_start(side, False, False, 0)

		base_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
		base_box.pack_start(Gtk.Label(label="Start from:"), False, False, 0)
		self.base_combo = Gtk.ComboBoxText()
		for theme_id in theme.list_themes():
			self.base_combo.append_text(theme_id)
		self.base_combo.set_active(0)
		self.base_combo.connect("changed", self._on_base_changed)
		base_box.pack_start(self.base_combo, True, True, 0)
		side.pack_start(base_box, False, False, 0)

		bg_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
		bg_import_btn = Gtk.Button(label="Import background image...")
		bg_import_btn.connect("clicked", self._on_import_background)
		bg_box.pack_start(bg_import_btn, False, False, 0)
		self.bg_label = Gtk.Label(label="(using base theme's background)", xalign=0)
		bg_box.pack_start(self.bg_label, True, True, 0)
		side.pack_start(bg_box, False, False, 0)

		scroller = Gtk.ScrolledWindow()
		scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
		form = Gtk.Grid(row_spacing=4, column_spacing=8, margin=4)
		scroller.add(form)
		side.pack_start(scroller, True, True, 0)

		self.color_buttons = {}
		row = 0
		for group_name, names in TOKEN_GROUPS:
			header = Gtk.Label(label=f"<b>{group_name}</b>", xalign=0, use_markup=True)
			form.attach(header, 0, row, 2, 1)
			row += 1
			for name in names:
				form.attach(Gtk.Label(label=name, xalign=0), 0, row, 1, 1)
				btn = Gtk.ColorButton(use_alpha=True)
				btn.connect("color-set", self._on_color_changed)
				form.attach(btn, 1, row, 1, 1)
				self.color_buttons[name] = btn
				row += 1

		bottom = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
		side.pack_start(bottom, False, False, 0)

		name_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
		name_box.pack_start(Gtk.Label(label="Theme name:"), False, False, 0)
		self.name_entry = Gtk.Entry()
		self.name_entry.connect("changed", self._on_name_changed)
		name_box.pack_start(self.name_entry, True, True, 0)
		bottom.pack_start(name_box, False, False, 0)

		self.id_label = Gtk.Label(label="id: (type a name above)", xalign=0)
		bottom.pack_start(self.id_label, False, False, 0)

		save_btn = Gtk.Button(label="Save Theme")
		save_btn.connect("clicked", self._on_save_clicked)
		bottom.pack_start(save_btn, False, False, 0)

		self.status_label = Gtk.Label(label="", xalign=0)
		self.status_label.set_line_wrap(True)
		bottom.pack_start(self.status_label, False, False, 0)

		self._load_base(theme.list_themes()[0] if theme.list_themes() else theme.DEFAULT_THEME)
		self.show_all()

	def _build_preview_widgets(self):
		box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, margin=16)

		tiles = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
		for i, label in enumerate(["Games", "Apps", "Settings"]):
			tile = Gtk.Label(label=label)
			tile.get_style_context().add_class("menu-icon-tile")
			if i == 0:
				tile.get_style_context().add_class("row-focused")
			tile.set_size_request(90, 60)
			tiles.pack_start(tile, False, False, 0)
		box.pack_start(tiles, False, False, 0)

		widget_box = Gtk.Box()
		widget_box.get_style_context().add_class("dashboard-widget-tile")
		widget_inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, margin=10)
		title = Gtk.Label(label="Dashboard Widget", xalign=0)
		title.get_style_context().add_class("dashboard-widget-title")
		summary = Gtk.Label(label="Some summary text sits here, at the muted/summary text color.", xalign=0)
		summary.get_style_context().add_class("dashboard-widget-summary")
		summary.set_line_wrap(True)
		widget_inner.pack_start(title, False, False, 0)
		widget_inner.pack_start(summary, False, False, 0)
		widget_box.add(widget_inner)
		box.pack_start(widget_box, False, False, 0)

		action_btn = Gtk.Button(label="Action Button")
		action_btn.get_style_context().add_class("action-button")
		box.pack_start(action_btn, False, False, 0)

		dim = Gtk.Label(label="Dim/secondary label text", xalign=0)
		dim.get_style_context().add_class("dim-label")
		box.pack_start(dim, False, False, 0)

		return box

	def _load_base(self, theme_id):
		theme_path = theme.THEMES_DIR / f"{theme_id}.css"
		text = theme_path.read_text() if theme_path.is_file() else ""
		parsed = _parse_palette(text)
		self.background_rel_path = _parse_background_image(text)
		self.bg_label.set_text(f"(using {theme_id}'s background)" if self.background_rel_path else "(no background image)")
		for name in ALL_TOKENS:
			rgba = parsed.get(name) or Gdk.RGBA(1, 1, 1, 1)
			self.colors[name] = rgba
			self.color_buttons[name].set_rgba(rgba)
		self._apply_live_palette()

	def _on_base_changed(self, combo):
		theme_id = combo.get_active_text()
		if theme_id:
			self._load_base(theme_id)

	def _on_import_background(self, _button):
		dialog = Gtk.FileChooserDialog(title="Import background image", parent=self, action=Gtk.FileChooserAction.OPEN)
		dialog.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL, Gtk.STOCK_OPEN, Gtk.ResponseType.OK)
		img_filter = Gtk.FileFilter()
		img_filter.set_name("Images")
		for pattern in ("*.jpg", "*.jpeg", "*.png"):
			img_filter.add_pattern(pattern)
		dialog.add_filter(img_filter)
		if dialog.run() == Gtk.ResponseType.OK:
			self._pending_bg_source = Path(dialog.get_filename())
			self.bg_label.set_text(f"(new: {self._pending_bg_source.name})")
		dialog.destroy()

	def _on_color_changed(self, button):
		for name, btn in self.color_buttons.items():
			if btn is button:
				self.colors[name] = btn.get_rgba()
				break
		self._apply_live_palette()

	def _apply_live_palette(self):
		lines = [f"@define-color {name} {_rgba_to_css(rgba)};" for name, rgba in self.colors.items()]
		bg_rule = "window { background-color: @bg_primary; }"
		if getattr(self, "_pending_bg_source", None) is not None:
			bg_rule = f'window {{ background-color: @bg_primary; background-image: linear-gradient(rgba(6,8,14,0.82), rgba(6,8,14,0.9)), url("file://{self._pending_bg_source}"); background-size: cover; background-position: center; }}'
		elif self.background_rel_path:
			bg_path = (theme.THEMES_DIR / self.background_rel_path).resolve()
			bg_rule = f'window {{ background-color: @bg_primary; background-image: linear-gradient(rgba(6,8,14,0.82), rgba(6,8,14,0.9)), url("file://{bg_path}"); background-size: cover; background-position: center; }}'
		css = "\n".join(lines) + "\n" + bg_rule
		provider = Gtk.CssProvider()
		try:
			provider.load_from_data(css.encode())
		except Exception:
			log.exception("failed to apply live palette preview")
			return
		screen = Gdk.Screen.get_default()
		if self._palette_provider is not None:
			Gtk.StyleContext.remove_provider_for_screen(screen, self._palette_provider)
		Gtk.StyleContext.add_provider_for_screen(screen, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)
		self._palette_provider = provider

	def _on_name_changed(self, _entry):
		name = self.name_entry.get_text()
		self.id_label.set_text(f"id: {_slugify(name)}" if name.strip() else "id: (type a name above)")

	def _on_save_clicked(self, _button):
		name = self.name_entry.get_text().strip()
		if not name:
			self.status_label.set_text("Give the theme a name first.")
			return
		theme_id = _slugify(name)
		out_path = theme.THEMES_DIR / f"{theme_id}.css"
		if out_path.is_file():
			self.status_label.set_text(f"A theme called '{theme_id}' already exists at {out_path} - pick a different name.")
			return
		try:
			lines = [f'/* "{name}" - generated by Theme Studio. */', ""]
			for group_name, names in TOKEN_GROUPS:
				lines.append(f"/* -- {group_name.lower()} -- */")
				for tname in names:
					lines.append(f"@define-color {tname} {_rgba_to_css(self.colors[tname])};")
				lines.append("")
			bg_source = getattr(self, "_pending_bg_source", None)
			if bg_source is not None:
				dest = theme.THEMES_DIR / f"{theme_id}_bg{bg_source.suffix}"
				shutil.copyfile(bg_source, dest)
				bg_url = dest.name
			elif self.background_rel_path:
				bg_url = self.background_rel_path
			else:
				bg_url = None
			if bg_url:
				lines.append("window {")
				lines.append("\tbackground-color: @bg_primary;")
				lines.append("\tbackground-image:")
				lines.append("\t\tlinear-gradient(rgba(6, 8, 14, 0.82), rgba(6, 8, 14, 0.9)),")
				lines.append(f'\t\turl("{bg_url}");')
				lines.append("\tbackground-size: cover;")
				lines.append("\tbackground-position: center;")
				lines.append("\tbackground-repeat: no-repeat;")
				lines.append("}")
			else:
				lines.append("window { background-color: @bg_primary; }")
			out_path.write_text("\n".join(lines) + "\n")
			self.status_label.set_text(f"Saved to {out_path} - pick '{theme_id}' from the profile's theme setting to use it.")
			log.info(f"exported theme studio theme {theme_id!r} to {out_path}")
		except Exception as exc:
			log.exception(f"failed to save theme {theme_id!r}")
			out_path.unlink(missing_ok=True)
			self.status_label.set_text(f"Save failed: {exc}")


if __name__ == "__main__":
	ThemeStudio()
	Gtk.main()
