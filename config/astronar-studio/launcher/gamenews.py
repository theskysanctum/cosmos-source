#!/usr/bin/env python3
"""Fetches recent headlines from a small set of gaming-news RSS feeds for
the main menu's dashboard - plain RSS 2.0 over HTTP, no API key needed,
so this runs standalone with zero credentials setup."""
import hashlib
import sys
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from xml.etree import ElementTree

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import debuglog  # noqa: E402
import paths  # noqa: E402

log = debuglog.setup("gamenews")

FEEDS = {
    "https://www.pcgamer.com/rss/": "PC Gamer",
    "https://www.eurogamer.net/feed": "Eurogamer",
    "https://www.gamespot.com/feeds/mashup/": "GameSpot",
}
REQUEST_TIMEOUT_S = 6
MAX_HEADLINES = 8

THUMB_CACHE_DIR = paths.DATA_ROOT / "cache" / "gamenews-thumbs"
THUMB_FETCH_TIMEOUT_S = 6
MEDIA_NS = "{http://search.yahoo.com/mrss/}"


def _image_url(item_el):
    """Prefers a real media:content image (all three configured feeds
    provide one); falls back to a plain RSS <enclosure> for any feed
    that only has that instead. None if neither is present - that
    headline just renders with no thumbnail, the same "degrade
    gracefully, don't error out" treatment a missing quick-launch
    favicon already gets elsewhere in this app."""
    media = item_el.find(f"{MEDIA_NS}content")
    if media is not None and media.get("url"):
        return media.get("url")
    enclosure = item_el.find("enclosure")
    if enclosure is not None and enclosure.get("url"):
        return enclosure.get("url")
    return None


def _fetch_feed(url, source):
    response = requests.get(url, timeout=REQUEST_TIMEOUT_S)
    response.raise_for_status()
    root = ElementTree.fromstring(response.content)
    items = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        if not title:
            continue
        try:
            pub_date = parsedate_to_datetime(item.findtext("pubDate") or "")
        except (TypeError, ValueError):
            pub_date = None
        items.append(
            {
                "title": title,
                "link": link,
                "source": source,
                "pub_date": pub_date,
                "image_url": _image_url(item),
            }
        )
    log.debug(f"{url}: {len(items)} item(s)")
    return items


def fetch_thumbnail(item):
    """Downloads and caches one headline's thumbnail to a local file,
    returns the local path (or None if there's no image or the fetch
    fails) - same "cache to disk on a background thread, decode the
    pixbuf on the GTK thread afterward" split as epic_app.py's
    _fetch_cover. Cached by a hash of the article's own link (stable
    and filesystem-safe, unlike a headline's title) rather than
    re-downloaded on every dashboard refresh."""
    url = item.get("image_url")
    if not url:
        return None
    THUMB_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_key = hashlib.sha1(item["link"].encode()).hexdigest()[:16]
    suffix = Path(url.split("?")[0]).suffix or ".jpg"
    cache_path = THUMB_CACHE_DIR / f"{cache_key}{suffix}"
    if cache_path.exists():
        return str(cache_path)
    try:
        response = requests.get(url, timeout=THUMB_FETCH_TIMEOUT_S)
        response.raise_for_status()
        cache_path.write_bytes(response.content)
        return str(cache_path)
    except Exception:
        log.exception(f"failed to fetch thumbnail for {item['link']} from {url}")
        return None


def fetch_headlines():
    """Pulls every configured feed, merges, sorts newest-first, and caps
    to MAX_HEADLINES. Each feed's own failure is logged and skipped - one
    dead/renamed feed shouldn't blank the whole widget. Called from a
    background thread (see app.py's run_async); returns a plain list of
    dicts, never raises."""
    all_items = []
    for url, source in FEEDS.items():
        try:
            all_items.extend(_fetch_feed(url, source))
        except Exception:
            log.exception(f"failed to fetch/parse feed {url}, skipping")

    epoch = datetime.min.replace(tzinfo=timezone.utc)
    all_items.sort(key=lambda item: item["pub_date"] or epoch, reverse=True)
    log.debug(f"merged {len(all_items)} headline(s) from {len(FEEDS)} feed(s)")
    return all_items[:MAX_HEADLINES]
