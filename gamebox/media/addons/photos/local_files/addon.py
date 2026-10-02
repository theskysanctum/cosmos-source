"""CosmOS's own built-in Photos add-on - scans media_backend.py's real
photos_dir(). "All Photos" is a flat scan of files sitting directly in
photos_dir(); "Albums" is one level of subfolders, same generic
drill-down as video/local_files.py's TV Shows."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
import media_backend  # noqa: E402

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".tiff", ".heic"}


def list_albums():
	return [
		{"id": "all", "name": "All Photos"},
		{"id": "albums", "name": "Albums"},
	]


def _image_files(directory):
	if not directory.is_dir():
		return []
	return sorted(p for p in directory.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS)


def list_photos(album_id):
	photos_dir = media_backend.photos_dir()

	if album_id == "all":
		return [{"id": str(p), "title": p.stem, "path": str(p)} for p in _image_files(photos_dir)]

	if album_id == "albums":
		if not photos_dir.is_dir():
			return []
		return [{"id": str(album_dir), "title": album_dir.name, "is_folder": True} for album_dir in sorted(photos_dir.iterdir()) if album_dir.is_dir()]

	# An album folder's own path (drilled into from "albums" above).
	album_dir = Path(album_id)
	return [{"id": str(p), "title": p.stem, "path": str(p)} for p in _image_files(album_dir)]
