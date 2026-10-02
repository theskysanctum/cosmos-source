"""CosmOS's own built-in Video add-on - scans media_backend.py's real
movies_dir()/tv_dir() folders (the same ones Shop's own downloads land
in - see app.py's _on_shop_video_destination_chosen()). Movies is flat
(one file per movie); TV Shows is one subfolder per show, so its items
are folders you drill into for episodes - handled generically via the
same list_items(category_id) call, just passing a show's own id back in
as the next category_id (see addon_backend.py's own docstring)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
import media_backend  # noqa: E402

VIDEO_EXTENSIONS = {".mp4", ".mkv", ".avi", ".mov", ".webm", ".m4v", ".mpg", ".mpeg", ".flv", ".wmv", ".ts"}


def list_categories():
	return [
		{"id": "movies", "name": "Movies"},
		{"id": "tv", "name": "TV Shows"},
	]


def _video_files(directory):
	if not directory.is_dir():
		return []
	return sorted(p for p in directory.iterdir() if p.is_file() and p.suffix.lower() in VIDEO_EXTENSIONS)


def list_items(category_id):
	if category_id == "movies":
		return [{"id": str(p), "title": p.stem, "path": str(p)} for p in _video_files(media_backend.movies_dir())]

	if category_id == "tv":
		tv_dir = media_backend.tv_dir()
		if not tv_dir.is_dir():
			return []
		return [{"id": str(show_dir), "title": show_dir.name, "is_folder": True} for show_dir in sorted(tv_dir.iterdir()) if show_dir.is_dir()]

	# Anything else is a show folder's own path (drilled into from "tv"
	# above) - list its episodes directly, sorted so multi-part/numbered
	# episodes play back in the right order.
	show_dir = Path(category_id)
	return [{"id": str(p), "title": p.stem, "path": str(p)} for p in _video_files(show_dir)]
