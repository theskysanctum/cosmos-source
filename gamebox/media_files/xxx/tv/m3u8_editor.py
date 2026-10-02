#!/usr/bin/env python3
"""A small Tkinter GUI for editing cosmictv.m3u8 by hand-filling a form
instead of hand-typing #EXTINF lines. Understands this playlist's own
existing shape (a fixed, ordered set of "group" sections, each a bar/
label/bar header followed by zero or more channel entries) - loads it,
lets you add/edit/delete/reorder channels per group through the GUI,
and writes it back out in the same shape.

Standard library only (tkinter + re) - no extra dependencies to install."""
import re
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

PLAYLIST_PATH = Path(__file__).resolve().parent / "cosmictv.m3u8"

# The playlist's own real group order (matches what's already in
# cosmictv.m3u8 today) - kept as the canonical order so groups stay in
# the same place on every save, even ones that are currently empty.
DEFAULT_GROUPS = [
	"General", "News", "Sports", "Kids", "Entertainment", "Movies", "Local",
	"Music", "Documentary", "Reality", "International", "Retro", "Premium",
	"Comedy", "Drama", "Crime", "Lifestyle", "Travel", "Cooking", "Weather",
	"Shopping", "Faith", "XXX",
]

HEADER_BAR = "#" * 15

EXTINF_RE = re.compile(r"^#EXTINF:-?\d+\s*(?P<attrs>.*?),\s*(?P<display>.*)$")
ATTR_RE = re.compile(r'([\w-]+)="([^"]*)"')
HEADER_BAR_RE = re.compile(r"^#{5,}$")
HEADER_LABEL_RE = re.compile(r"^#\s+(.+?)\s*$")

_CANONICAL_GROUP_BY_UPPER = {g.upper(): g for g in DEFAULT_GROUPS}


class Channel:
	def __init__(self, name, logo, url):
		self.name = name
		self.logo = logo
		self.url = url


def _canonical_group_name(label):
	"""Matches a parsed header label back to DEFAULT_GROUPS' own casing
	(so "GENERAL" -> "General", "XXX" stays "XXX" rather than
	.title()-casing it into "Xxx") - falls back to .title() for a group
	the file didn't already know about."""
	return _CANONICAL_GROUP_BY_UPPER.get(label.upper(), label.title())


def parse_playlist(path):
	"""Returns an ordered dict of group name -> [Channel, ...], in
	whatever order the group headers actually appear in the file -
	that's the real, authoritative order (including anything saved
	after moving groups around in the GUI), not DEFAULT_GROUPS' own
	fixed order. DEFAULT_GROUPS is only a fallback: it seeds a fresh
	file with every known section as an addable bucket, and it fills in
	any of those that a real file happens not to mention at all,
	appended after whatever the file actually has - it never overrides
	real on-disk order for groups the file does mention."""
	if not path.is_file():
		return {g: [] for g in DEFAULT_GROUPS}

	groups = {}
	lines = path.read_text(encoding="utf-8").splitlines()
	# Fallback only - for a channel entry that somehow appears before any
	# group header at all (shouldn't happen in a real file). Not pre-
	# registered into `groups` here, on purpose: doing that would always
	# pin "General" to position 0 regardless of where its real header
	# actually sits in the file, which is exactly the bug this function
	# exists to avoid.
	current_group = "General"
	i = 0
	while i < len(lines):
		line = lines[i].strip()

		if HEADER_BAR_RE.match(line) and i + 1 < len(lines):
			label_match = HEADER_LABEL_RE.match(lines[i + 1].strip())
			if label_match:
				current_group = _canonical_group_name(label_match.group(1))
				groups.setdefault(current_group, [])
				i += 2
				if i < len(lines) and HEADER_BAR_RE.match(lines[i].strip()):
					i += 1
				continue

		m = EXTINF_RE.match(line)
		if m:
			attrs = dict(ATTR_RE.findall(m.group("attrs")))
			name = attrs.get("tvg-name") or m.group("display").strip()
			logo = attrs.get("tvg-logo", "")
			j = i + 1
			while j < len(lines) and not lines[j].strip():
				j += 1
			url = lines[j].strip() if j < len(lines) else ""
			group = _canonical_group_name(attrs.get("group-title") or current_group)
			groups.setdefault(group, [])
			groups[group].append(Channel(name, logo, url))
			i = j + 1
			continue

		i += 1

	for g in DEFAULT_GROUPS:
		groups.setdefault(g, [])
	return groups


def write_playlist(path, groups):
	lines = ["#EXTM3U", ""]
	for group, channels in groups.items():
		lines.append(HEADER_BAR)
		lines.append(f"#   {group.upper()}")
		lines.append(HEADER_BAR)
		lines.append("")
		for channel in channels:
			lines.append(
				f'#EXTINF:-1 tvg-name="{channel.name}" tvg-logo="{channel.logo}" '
				f'group-title="{group}", {channel.name}'
			)
			lines.append(channel.url)
			lines.append("")
		lines.append("")
	path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


class PlaylistEditor(tk.Tk):
	def __init__(self):
		super().__init__()
		self.title(f"CosmicTV Playlist Editor - {PLAYLIST_PATH}")
		self.geometry("1000x620")
		self.minsize(820, 500)

		self.groups = parse_playlist(PLAYLIST_PATH)
		self.editing = None  # (group, Channel) currently loaded in the form, or None = adding new

		self._build_widgets()
		self._refresh_tree()
		self._set_status(f"Loaded {PLAYLIST_PATH}" if PLAYLIST_PATH.is_file() else "No existing file yet - starting fresh")

	# -- layout -----------------------------------------------------------

	def _build_widgets(self):
		root = ttk.Frame(self, padding=10)
		root.pack(fill="both", expand=True)
		root.columnconfigure(0, weight=3)
		root.columnconfigure(1, weight=2)
		root.rowconfigure(0, weight=1)

		# -- left: the grouped channel tree --
		left = ttk.Frame(root)
		left.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
		left.rowconfigure(0, weight=1)
		left.columnconfigure(0, weight=1)

		self.tree = ttk.Treeview(left, columns=("url",), show="tree headings", selectmode="browse")
		self.tree.heading("#0", text="Group / Channel")
		self.tree.heading("url", text="Stream URL")
		self.tree.column("#0", width=260)
		self.tree.column("url", width=340)
		self.tree.grid(row=0, column=0, sticky="nsew")
		self.tree.bind("<<TreeviewSelect>>", self._on_tree_select)

		scroll = ttk.Scrollbar(left, orient="vertical", command=self.tree.yview)
		self.tree.configure(yscrollcommand=scroll.set)
		scroll.grid(row=0, column=1, sticky="ns")

		move_row = ttk.Frame(left)
		move_row.grid(row=1, column=0, columnspan=2, sticky="w", pady=(6, 0))
		ttk.Button(move_row, text="Move Up", command=lambda: self._move_selected(-1)).pack(side="left")
		ttk.Button(move_row, text="Move Down", command=lambda: self._move_selected(1)).pack(side="left", padx=(6, 0))
		ttk.Button(move_row, text="Delete", command=self._delete_selected).pack(side="left", padx=(6, 0))

		# -- right: the add/edit form --
		right = ttk.LabelFrame(root, text="Channel", padding=10)
		right.grid(row=0, column=1, sticky="nsew")
		right.columnconfigure(1, weight=1)

		ttk.Label(right, text="Group").grid(row=0, column=0, sticky="w", pady=4)
		self.group_var = tk.StringVar(value=DEFAULT_GROUPS[0])
		self.group_box = ttk.Combobox(right, textvariable=self.group_var, values=list(self.groups.keys()))
		self.group_box.grid(row=0, column=1, sticky="ew", pady=4)

		ttk.Label(right, text="Channel Name").grid(row=1, column=0, sticky="w", pady=4)
		self.name_var = tk.StringVar()
		ttk.Entry(right, textvariable=self.name_var).grid(row=1, column=1, sticky="ew", pady=4)

		ttk.Label(right, text="Logo URL").grid(row=2, column=0, sticky="w", pady=4)
		self.logo_var = tk.StringVar()
		ttk.Entry(right, textvariable=self.logo_var).grid(row=2, column=1, sticky="ew", pady=4)

		ttk.Label(right, text="Stream URL").grid(row=3, column=0, sticky="w", pady=4)
		self.url_var = tk.StringVar()
		ttk.Entry(right, textvariable=self.url_var).grid(row=3, column=1, sticky="ew", pady=4)

		button_row = ttk.Frame(right)
		button_row.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(12, 0))
		self.submit_button = ttk.Button(button_row, text="Add Channel", command=self._submit_form)
		self.submit_button.pack(side="left")
		ttk.Button(button_row, text="Clear Form", command=self._clear_form).pack(side="left", padx=(6, 0))

		ttk.Separator(right, orient="horizontal").grid(row=5, column=0, columnspan=2, sticky="ew", pady=14)

		new_group_row = ttk.Frame(right)
		new_group_row.grid(row=6, column=0, columnspan=2, sticky="ew")
		ttk.Label(new_group_row, text="New group name:").pack(side="left")
		self.new_group_var = tk.StringVar()
		ttk.Entry(new_group_row, textvariable=self.new_group_var, width=16).pack(side="left", padx=(6, 6))
		ttk.Button(new_group_row, text="Add Group", command=self._add_group).pack(side="left")

		ttk.Separator(right, orient="horizontal").grid(row=7, column=0, columnspan=2, sticky="ew", pady=14)
		ttk.Button(right, text="Save cosmictv.m3u8", command=self._save).grid(row=8, column=0, columnspan=2, sticky="ew")

		self.status_var = tk.StringVar()
		ttk.Label(self, textvariable=self.status_var, anchor="w", relief="sunken").pack(fill="x", side="bottom")

	# -- tree ---------------------------------------------------------------

	def _refresh_tree(self):
		selection = self.tree.selection()
		selected_key = selection[0] if selection else None
		for item in self.tree.get_children():
			self.tree.delete(item)
		for group, channels in self.groups.items():
			group_id = self.tree.insert("", "end", iid=f"group:{group}", text=f"{group} ({len(channels)})", open=bool(channels))
			for index, channel in enumerate(channels):
				self.tree.insert(group_id, "end", iid=f"chan:{group}:{index}", text=channel.name, values=(channel.url,))
		self.group_box["values"] = list(self.groups.keys())
		if selected_key and self.tree.exists(selected_key):
			self.tree.selection_set(selected_key)

	def _selected_channel_key(self):
		selection = self.tree.selection()
		if selection and selection[0].startswith("chan:"):
			return selection[0]
		return None

	def _selected_channel(self):
		key = self._selected_channel_key()
		if not key:
			return None, None
		_, group, index = key.split(":", 2)
		return group, int(index)

	def _on_tree_select(self, _event):
		group, index = self._selected_channel()
		if group is None:
			return
		channel = self.groups[group][index]
		self.editing = (group, index)
		self.group_var.set(group)
		self.name_var.set(channel.name)
		self.logo_var.set(channel.logo)
		self.url_var.set(channel.url)
		self.submit_button.config(text="Update Channel")

	# -- form actions ---------------------------------------------------------

	def _submit_form(self):
		group = self.group_var.get().strip()
		name = self.name_var.get().strip()
		logo = self.logo_var.get().strip()
		url = self.url_var.get().strip()

		if not group:
			messagebox.showwarning("Missing group", "Pick or type a group first.")
			return
		if not name:
			messagebox.showwarning("Missing name", "Channel name can't be empty.")
			return
		if not url:
			messagebox.showwarning("Missing URL", "Stream URL can't be empty.")
			return

		self.groups.setdefault(group, [])

		if self.editing:
			old_group, index = self.editing
			channel = self.groups[old_group][index]
			channel.name, channel.logo, channel.url = name, logo, url
			if old_group != group:
				del self.groups[old_group][index]
				self.groups[group].append(channel)
			self._set_status(f"Updated \"{name}\"")
		else:
			self.groups[group].append(Channel(name, logo, url))
			self._set_status(f"Added \"{name}\" to {group}")

		self._clear_form()
		self._refresh_tree()

	def _clear_form(self):
		self.editing = None
		self.name_var.set("")
		self.logo_var.set("")
		self.url_var.set("")
		self.submit_button.config(text="Add Channel")
		self.tree.selection_remove(self.tree.selection())

	def _delete_selected(self):
		"""Deletes whichever's selected - a whole group (and every channel
		in it) if a group row is selected, or one channel otherwise. Same
		Delete button either way, same as Move Up/Down's own dispatch."""
		selection = self.tree.selection()
		if selection and selection[0].startswith("group:"):
			group = selection[0].split(":", 1)[1]
			channels = self.groups[group]
			warning = f' and its {len(channels)} channel(s)' if channels else ""
			if not messagebox.askyesno("Delete group", f'Delete group "{group}"{warning}?'):
				return
			del self.groups[group]
			self._clear_form()
			self._refresh_tree()
			self._set_status(f'Deleted group "{group}"')
			return

		group, index = self._selected_channel()
		if group is None:
			messagebox.showinfo("Nothing selected", "Select a group or channel in the list first.")
			return
		channel = self.groups[group][index]
		if not messagebox.askyesno("Delete channel", f'Delete "{channel.name}" from {group}?'):
			return
		del self.groups[group][index]
		self._clear_form()
		self._refresh_tree()
		self._set_status(f'Deleted "{channel.name}"')

	def _move_selected(self, direction):
		"""Moves whichever's selected - a whole group (reordering the
		playlist's own section order) if a group row is selected, or one
		channel within its group otherwise. Same Move Up/Down buttons
		either way - which one happens just depends on what's picked in
		the tree."""
		selection = self.tree.selection()
		if selection and selection[0].startswith("group:"):
			self._move_group(selection[0].split(":", 1)[1], direction)
			return

		group, index = self._selected_channel()
		if group is None:
			return
		channels = self.groups[group]
		new_index = index + direction
		if not (0 <= new_index < len(channels)):
			return
		channels[index], channels[new_index] = channels[new_index], channels[index]
		self._refresh_tree()
		self.tree.selection_set(f"chan:{group}:{new_index}")

	def _move_group(self, group, direction):
		keys = list(self.groups.keys())
		index = keys.index(group)
		new_index = index + direction
		if not (0 <= new_index < len(keys)):
			return
		keys[index], keys[new_index] = keys[new_index], keys[index]
		self.groups = {key: self.groups[key] for key in keys}
		self._refresh_tree()
		self.tree.selection_set(f"group:{group}")
		self.tree.see(f"group:{group}")
		self._set_status(f'Moved "{group}" {"up" if direction < 0 else "down"}')

	def _add_group(self):
		name = self.new_group_var.get().strip()
		if not name:
			return
		if name not in self.groups:
			self.groups[name] = []
			self._refresh_tree()
			self._set_status(f'Added group "{name}"')
		self.group_var.set(name)
		self.new_group_var.set("")

	def _save(self):
		write_playlist(PLAYLIST_PATH, self.groups)
		self._set_status(f"Saved to {PLAYLIST_PATH}")

	def _set_status(self, text):
		self.status_var.set(text)


if __name__ == "__main__":
	PlaylistEditor().mainloop()
