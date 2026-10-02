#!/usr/bin/env python3
"""Filesystem backend for the file manager screen: directory listing and
basic file operations (copy/move/delete/rename/mkdir). No UI here."""
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import debuglog  # noqa: E402

log = debuglog.setup("filemanager")


def list_dir(path):
	"""Returns (dirs, files) as lists of {"name", "path"} dicts (files also
	get "size"), each sorted alphabetically, case-insensitive. Entries that
	raise on stat() (permission errors, broken symlinks) are skipped rather
	than crashing the whole listing."""
	dirs, files = [], []
	for entry in Path(path).iterdir():
		try:
			is_dir = entry.is_dir()
		except OSError as e:
			log.debug(f"skipping {entry}: {e}")
			continue
		item = {"name": entry.name, "path": str(entry)}
		if is_dir:
			dirs.append(item)
		else:
			try:
				item["size"] = entry.stat().st_size
			except OSError as e:
				log.debug(f"could not stat {entry}: {e}")
				item["size"] = 0
			files.append(item)
	dirs.sort(key=lambda d: d["name"].lower())
	files.sort(key=lambda f: f["name"].lower())
	log.debug(f"listed {path}: {len(dirs)} dir(s), {len(files)} file(s)")
	return dirs, files


def format_size(size):
	value = float(size)
	for unit in ("B", "KB", "MB", "GB"):
		if value < 1024 or unit == "GB":
			return f"{value:.0f}{unit}" if unit == "B" else f"{value:.1f}{unit}"
		value /= 1024
	return f"{value:.1f}TB"


def copy(src, dst_dir):
	src = Path(src)
	dst = Path(dst_dir) / src.name
	log.info(f"copying {src} -> {dst}")
	if src.is_dir():
		shutil.copytree(src, dst)
	else:
		shutil.copy2(src, dst)


def move(src, dst_dir):
	dst = Path(dst_dir) / Path(src).name
	log.info(f"moving {src} -> {dst}")
	shutil.move(str(src), str(dst))


def delete(path):
	path = Path(path)
	log.info(f"deleting {path}")
	if path.is_dir():
		shutil.rmtree(path)
	else:
		path.unlink()


def rename(path, new_name):
	path = Path(path)
	log.info(f"renaming {path} -> {new_name}")
	path.rename(path.parent / new_name)


def make_dir(parent, name):
	log.info(f"creating directory {Path(parent) / name}")
	(Path(parent) / name).mkdir()
