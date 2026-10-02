#!/usr/bin/env python3
"""Resolves a game's YouTube preview video to real, directly-playable
stream URL(s) via the `yt-dlp` CLI (packaging/host_deps.py's own
PIP_PACKAGES entry) - a subprocess wrapper, not an import, matching this
project's existing style of shelling out to well-tested CLIs (see
epic/legendary_backend.py's own docstring for the same reasoning).

Modern YouTube almost never serves a single combined audio+video URL any
more (confirmed live) - most videos are video-only + audio-only DASH
streams that get merged client-side. --get-url can't merge two URLs
into one file, so resolve_stream_url() tries a combined format first and
falls back to a (video_url, audio_url) pair, which media/vlc_player.py's
play() plays back as a real libVLC ":input-slave=" secondary audio
track - genuinely two live network streams muxed by the player, never a
download. --js-runtimes node is passed explicitly since yt-dlp needs a
real JS runtime to solve YouTube's signature challenges and node is
already a required CosmOS dependency (packaging/host_deps.py's own
PACKAGES) - nothing extra to install just for this.

Never downloads or caches anything - resolve_stream_url() is only ever
called when the library's Game Detail page's own "Watch Preview" button
is actually pressed (never automatically/eagerly). Never raises - no
result, an age-gated/region-locked video, or yt-dlp itself being
unavailable all just mean "no preview for this game"."""
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import debuglog  # noqa: E402

log = debuglog.setup("preview_video")

YTDLP_BIN = "yt-dlp"
REQUEST_TIMEOUT_S = 20


def _get_url(query, format_selector):
	cmd = [YTDLP_BIN, "--js-runtimes", "node", query, "-f", format_selector, "-g", "--no-playlist"]
	log.debug(f"running: {cmd}")
	try:
		result = subprocess.run(cmd, capture_output=True, text=True, timeout=REQUEST_TIMEOUT_S)
	except (subprocess.TimeoutExpired, OSError) as e:
		log.error(f"yt-dlp invocation failed: {e}")
		return None
	if result.returncode != 0:
		log.debug(f"yt-dlp -f {format_selector!r} found nothing for {query!r}: {result.stderr.strip()[-300:]}")
		return None
	lines = [line for line in result.stdout.strip().splitlines() if line]
	return lines[0] if lines else None


def resolve_stream_url(title):
	"""(video_url, audio_url) - audio_url is None when a single combined
	stream was found (nothing to mux), and the whole result is None on
	total failure (no search match, offline, yt-dlp unavailable).
	Blocking - real network calls, run this off the GTK thread (see
	app.py's run_async())."""
	query = f"ytsearch1:{title} gameplay"
	combined = _get_url(query, "b")
	if combined:
		log.info(f"resolved a combined YouTube preview for {title!r}")
		return combined, None
	video_url = _get_url(query, "bestvideo")
	if not video_url:
		log.debug(f"no YouTube preview found for {title!r}")
		return None
	audio_url = _get_url(query, "bestaudio")
	log.info(f"resolved a split video+audio YouTube preview for {title!r} (audio={'yes' if audio_url else 'no'})")
	return video_url, audio_url
