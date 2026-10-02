"""CosmOS's own built-in Music add-on - scans media_backend.py's real
music_dir(). No fixed Artist/Album hierarchy is assumed (nothing
established that convention yet) - "All Songs" is a flat scan of files
sitting directly in music_dir(), "Albums" is one level of subfolders
(however the library happens to be organized), drilled into the same
generic way video/local_files/addon.py's TV Shows does."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
import media_backend  # noqa: E402

AUDIO_EXTENSIONS = {".mp3", ".flac", ".m4a", ".ogg", ".opus", ".wav", ".aac", ".wma"}


def list_categories():
	return [
		{"id": "all", "name": "All Songs"},
		{"id": "albums", "name": "Albums"},
	]


def _audio_files(directory):
	if not directory.is_dir():
		return []
	return sorted(p for p in directory.iterdir() if p.is_file() and p.suffix.lower() in AUDIO_EXTENSIONS)


def list_items(category_id):
	music_dir = media_backend.music_dir()

	if category_id == "all":
		return [{"id": str(p), "title": p.stem, "path": str(p)} for p in _audio_files(music_dir)]

	if category_id == "albums":
		if not music_dir.is_dir():
			return []
		return [{"id": str(album_dir), "title": album_dir.name, "is_folder": True} for album_dir in sorted(music_dir.iterdir()) if album_dir.is_dir()]

	# An album folder's own path (drilled into from "albums" above).
	album_dir = Path(category_id)
	return [{"id": str(p), "title": p.stem, "path": str(p)} for p in _audio_files(album_dir)]
