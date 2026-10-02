#!/usr/bin/env python3
"""CosmOS Shop - a hand-made GTK3 App Store, direct requirement ("I do
want a hand made gtk3/4 App Store called CosmOS Shop that filters out
all things already included in the OS and leaves everything open").

Three real sources, one shared browsing UI, switched via the button row
at the top (direct requirement: "implement 3 sections at the top dnf,
appimage, flatpaks all the same basic listing"):

  - DNF: every package in this image's own enabled repos (Fedora,
    RPM Fusion free/nonfree, Terra) not already installed - direct
    requirement ("TerraPKG, RPM Fusion, RPM Free, and RPM Non free.
    Every RPM repo you can think of that's available" - see the
    Containerfile's own comment on what's actually enabled). ~70,000
    packages total, confirmed live (`dnf repoquery --available`) -
    grouped by Fedora's own real comps groups (`dnf group info`) where
    a package actually belongs to one (direct requirement: "Use
    Fedora's ~30-40 comps groups" once it was confirmed live that DNF
    has no real per-package category data anywhere - Fedora's own repo
    metadata has no "appstream" type at all, only primary/filelists/
    other/group). Everything not in any comps group (the vast
    majority) is real and searchable, just not eagerly built as
    widgets - see OverflowSection's own docstring for why.

  - AppImage: the real "AM"/AppMan database (see the original version
    of this docstring, preserved in git history, for the fuller
    reasoning already worked out for this one - no real category data
    exists here either, so it stays one flat searchable list).

  - Flatpak: the real Flathub catalog, via its own bulk AppStream XML
    (https://dl.flathub.org/repo/appstream/x86_64/appstream.xml.gz) -
    confirmed live this has genuine structured categories (standard
    freedesktop menu categories, e.g. Graphics/2DGraphics/
    RasterGraphics for GIMP) unlike AM or DNF, so this section gets
    real category grouping for its entire ~4700-app catalog, no
    overflow bucket needed. Flatpak itself, and the Flathub remote,
    are a deliberate reversal of this image's own former "no flatpak
    anywhere" policy (direct request) - added specifically so this
    section has something real to browse, nowhere else on this image
    uses it.

"Filters out all things already included in the OS": each section
excludes by comparing against that section's own real already-installed
set (DNF: `rpm -qa` package names, exact match, the same names DNF's
own catalog itself uses - Flatpak: `flatpak list` application ids,
exact match - AppImage: this image's own installed Gio.AppInfo display
names, see is_excluded()'s own docstring for why that one specifically
is exact-match-only rather than fuzzy).

AM/AppMan itself is direct-request user-triggered, not auto-installed
at first login (see cosmos-session's own onboarding-only autostart
list) - switching to the AppImage section is what offers to run the
real upstream AM-INSTALLER first if `am`/`appman` isn't on $PATH yet,
in a task window since it asks its own local-vs-system-wide
question interactively. DNF and Flatpak need no such bootstrap (both
are already fully set up at build time), so neither one blocks on this
at all.

Every actual install (any section) runs in a task window, not
silently in the background - package managers occasionally need real
interactive confirmation themselves, and a person watching something
install is the same expectation this project's own other Terminal=true
tools (Secrets Manager, GameBox Add-on Studio) already set. DNF's own
installs go through `kronos exec -- rpm-ostree install` (the admin service; this account has no
sudo of its own); Flatpak and
AppImage both install `--user`/local per-account, matching the
AppMan-style "no root needed" philosophy this whole app already leans
on for AppImages."""
import gzip
import os
import queue
import re
import shlex
import subprocess
import threading
import time
import urllib.error
import urllib.request

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Gtk, Pango  # noqa: E402
import cosmos_display  # noqa: E402

BG = "#0a0e16"
FG = "#e8ecf4"
ACCENT = "#ff0090"
BORDER = "#2a3a56"
ICON_SIZE = cosmos_display.px(48)
TILE_ICON_SIZE = cosmos_display.px(40)

CACHE_DIR = os.path.expanduser("~/.cache/cosmos-shop")
CACHE_MAX_AGE = 24 * 60 * 60  # a day - none of these three catalogs move faster than that

# --- AppImage (AM/AppMan) ---------------------------------------------
APPSDBLIST_URL = "https://raw.githubusercontent.com/ivan-hc/AM/main/programs/x86_64-apps"
APPSDBLIST_CACHE_PATH = os.path.join(CACHE_DIR, "x86_64-apps")
AM_INSTALLER_URL = "https://raw.githubusercontent.com/ivan-hc/AM/main/AM-INSTALLER"

# Real per-app icons - direct requirement ("organized by category, use
# screenshots, descriptions, etc", narrowed down to "just use real icons"
# once checking live confirmed neither AM nor its companion site expose
# real category or screenshot data anywhere (no categories.json, no
# per-app screenshot field in AM's own database - the companion site
# generates its own category pages some other way, not from data this
# script can read). Real per-app icons DO genuinely exist though,
# confirmed live: AM's own companion cataloging site (the
# "portable-linux-apps.github.io/apps" link the AM README itself points
# at) keeps one real icon per program-id in its own repo, same slug this
# database already uses. AppImage-only - DNF/Flatpak tiles use a plain
# generic icon, since a per-package icon fetch for ~70,000 DNF packages
# specifically was never going to be practical.
ICON_URL_TEMPLATE = "https://raw.githubusercontent.com/Portable-Linux-Apps/Portable-Linux-Apps.github.io/main/icons/{}.png"
ICON_CACHE_DIR = os.path.join(CACHE_DIR, "icons")
ICON_FETCH_WORKERS = 8

# --- Flatpak (Flathub) --------------------------------------------------
FLATHUB_APPSTREAM_URL = "https://dl.flathub.org/repo/appstream/x86_64/appstream.xml.gz"
FLATHUB_CACHE_PATH = os.path.join(CACHE_DIR, "flathub-appstream.xml.gz")

# --- DNF ------------------------------------------------------------
DNF_AVAILABLE_CACHE_PATH = os.path.join(CACHE_DIR, "dnf-available.txt")
DNF_GROUPS_CACHE_PATH = os.path.join(CACHE_DIR, "dnf-groups.txt")

OVERFLOW_SEARCH_LIMIT = 200

# GtkFlowBox's own real default for max-children-per-line is 7 (confirmed
# live, never overridden anywhere in this file before) - on any screen
# wider than ~1150px that wastes most of the window as dead space to the
# right of a narrow 7-wide column of tiles, confirmed live via a
# screenshot at 1920px showing exactly 7 columns with the remaining
# ~1650px empty. A generous constant (well past anything a real
# monitor/tile-width combination could ever actually fill) removes the
# cap entirely - GTK still only lays out as many columns as the
# FlowBox's real allocated width and each tile's own natural width
# actually fit, this only stops it from refusing to add more.
FLOWBOX_MAX_COLUMNS = 32

CSS = f"""
window.cosmos-shop {{
    background-color: {BG};
}}
.cosmos-shop-query {{
    background-color: {BG};
    color: {FG};
    font-weight: bold;
    font-size: 20px;
    border: 2px solid {BORDER};
    border-radius: 6px;
    padding: 10px 20px;
}}
.cosmos-shop-hint {{
    color: {FG};
    opacity: 0.6;
}}
.cosmos-shop-count {{
    color: {FG};
    opacity: 0.6;
}}
.cosmos-shop-section-header {{
    color: {ACCENT};
    font-weight: bold;
    font-size: 14px;
}}
flowboxchild {{
    padding: 12px;
    border-radius: 6px;
}}
flowboxchild:selected {{
    background-color: {ACCENT};
}}
.cosmos-shop-list {{
    background-color: {BG};
}}
.cosmos-shop-list row {{
    padding: 8px 12px;
    color: {FG};
    border-radius: 6px;
}}
.cosmos-shop-list row:selected {{
    background-color: {ACCENT};
    color: {BG};
}}
.cosmos-shop-tile-label {{
    color: {FG};
    font-weight: bold;
}}
flowboxchild:selected .cosmos-shop-tile-label {{
    color: {BG};
}}
.cosmos-shop-menu {{
    background-color: {BG};
    background-image: none;
    border: 1px solid {BORDER};
}}
.cosmos-shop-menu menuitem {{
    background-color: {BG};
    background-image: none;
    color: {FG};
}}
.cosmos-shop-menu menuitem:hover {{
    background-color: {ACCENT};
    background-image: none;
    color: {BG};
}}
.cosmos-shop-entry {{
    background-color: {BG};
    background-image: none;
    color: {FG};
    caret-color: {FG};
    border: 1px solid {BORDER};
    box-shadow: none;
    font-weight: bold;
    padding: 10px 12px;
}}
.cosmos-shop-button {{
    background-color: {BG};
    background-image: none;
    color: {FG};
    border: 1px solid {BORDER};
    box-shadow: none;
    font-weight: bold;
    padding: 8px 14px;
}}
.cosmos-shop-button-accent {{
    background-color: {ACCENT};
    background-image: none;
    color: {BG};
    border: 1px solid {ACCENT};
    box-shadow: none;
    font-weight: bold;
    padding: 8px 14px;
}}
.cosmos-shop-section-button {{
    background-color: {BG};
    background-image: none;
    color: {FG};
    border: 1px solid {BORDER};
    box-shadow: none;
    font-weight: bold;
    padding: 10px 20px;
}}
.cosmos-shop-section-button-active {{
    background-color: {ACCENT};
    background-image: none;
    color: {BG};
    border: 1px solid {ACCENT};
    box-shadow: none;
    font-weight: bold;
    padding: 10px 20px;
}}
"""


# ======================================================================
# Shared helpers
# ======================================================================

def slugify(name):
    """Same real slugify cosmos-launcher.py's own add_web_app() uses -
    reused here (duplicated, not imported - see this file's own
    docstring on why every cosmos-* script here is self-contained) so
    an installed app's display name and an AM program-id land in the
    same comparable shape."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "app"


def stripped(name):
    """Same idea as slugify() but with every separator removed outright
    instead of collapsed to a hyphen - catches a real, live-confirmed
    class of match slugify() alone misses (AM's own "qtcreator" vs the
    installed "Qt Creator" -> "qt-creator" - different only in whether
    a hyphen exists at all, not in the actual words)."""
    return re.sub(r"[^a-z0-9]+", "", name.lower())


def run_in_terminal(command, hold_message=None):
    """Runs an install/info command in a cosmos-task window (a live log with a reply box -
    CosmOS has no terminal emulator). The name and the unused hold_message stay so the
    call sites read the same; the window itself stays open when the command ends."""
    task = GLib.find_program_in_path("cosmos-task") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "cosmos-task.py")
    subprocess.Popen([task, "--title", "CosmOS Shop", "--shell", command])


def matches_query(query, name, desc=""):
    """Every word of the query has to appear in the name or the description, in any order ("ps2 crash" finds "Crash Bandicoot - PlayStation 2")."""
    haystack = f"{name} {desc}".lower()
    return all(word in haystack for word in query.lower().split())


def cache_is_fresh(path):
    return os.path.exists(path) and time.time() - os.path.getmtime(path) < CACHE_MAX_AGE


def already_installed_gio_slugs():
    """Every real installed app's own display name, in both comparable
    forms is_excluded_appimage() below cross-references AM's own ids
    against - the plain hyphenated slug and the fully-stripped form
    (see stripped()'s own docstring for why both)."""
    names = [app.get_display_name() for app in Gio.AppInfo.get_all() if app.should_show()]
    return {slugify(n) for n in names}, {stripped(n) for n in names}


def is_excluded_appimage(program_id, installed_slugs, installed_stripped):
    """Exact match only (both the hyphenated slug and the fully-
    stripped form) - direct requirement ("filters out all things
    already included in the OS... leaves everything open"). A fuzzy
    substring/word-overlap version was tried and measured first against
    the real ~3600-entry AM database and this project's own real
    installed-app list, not assumed: it caught a handful more genuine
    duplicates but at the cost of dozens of real false positives from
    ordinary word fragments coincidentally embedded in longer names -
    confirmed live examples: "paper" excluded merely because "Set
    Wallpaper" is installed, "install" because of "Wine Software
    Uninstaller", "flare" because of "Cloudflared Ingress Editor",
    "gedit" because of "Regedit", "monit" because of "RoboCup 2D Soccer
    Monitor" - none of them actually related. Exact-match-only measured
    at zero false positives across that same real installed list, so
    "leaves everything open" (the more important half of the direct
    request when the two are in tension) wins here - a few genuine
    duplicates slipping through beats hiding apps that were never
    actually already installed."""
    pid = slugify(program_id)
    if pid in installed_slugs:
        return True
    return stripped(program_id) in installed_stripped


# ======================================================================
# AppImage (AM/AppMan) data
# ======================================================================

def fetch_appsdblist():
    """The real APPSDBLIST AM/AppMan itself reads for `-l`/`-q`/`-u`
    (see README's own "Environment variables" section) - fetched
    directly here rather than shelling out to `am -l`, which matters
    specifically because this needs to work even before AM/AppMan is
    installed at all (see check_am_installed())."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    if cache_is_fresh(APPSDBLIST_CACHE_PATH):
        with open(APPSDBLIST_CACHE_PATH, encoding="utf-8", errors="replace") as f:
            return f.read()
    try:
        with urllib.request.urlopen(APPSDBLIST_URL, timeout=15) as resp:
            text = resp.read().decode("utf-8", errors="replace")
        with open(APPSDBLIST_CACHE_PATH, "w", encoding="utf-8") as f:
            f.write(text)
        return text
    except OSError:
        if os.path.exists(APPSDBLIST_CACHE_PATH):
            with open(APPSDBLIST_CACHE_PATH, encoding="utf-8", errors="replace") as f:
                return f.read()
        raise


def parse_appsdblist(text):
    """Each real line looks like '◆ program-id : description text'."""
    entries = []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("◆"):
            continue
        line = line[1:].strip()
        if " : " not in line:
            continue
        name, desc = line.split(" : ", 1)
        entries.append((name.strip(), desc.strip()))
    return entries


def fetch_icon(name):
    """Real icon for one program-id, cached forever locally once fetched
    - unlike the APPSDBLIST cache above, icons never expire/refetch on a
    timer, since a program's own icon essentially never changes and
    there's no point re-downloading ~3600 of them on some schedule.
    Returns the local cached path, or None if there's genuinely no icon
    for this program (a real 404, not a transient network failure) -
    a ".missing" marker file records that permanently too, the same
    reasoning as the real icon cache: no point re-requesting a 404 on
    every single future launch forever."""
    os.makedirs(ICON_CACHE_DIR, exist_ok=True)
    icon_path = os.path.join(ICON_CACHE_DIR, f"{name}.png")
    missing_marker = os.path.join(ICON_CACHE_DIR, f"{name}.missing")
    if os.path.exists(icon_path):
        return icon_path
    if os.path.exists(missing_marker):
        return None
    try:
        with urllib.request.urlopen(ICON_URL_TEMPLATE.format(name), timeout=10) as resp:
            data = resp.read()
        if not data:
            raise urllib.error.URLError("empty response")
        tmp_path = icon_path + ".tmp"
        with open(tmp_path, "wb") as f:
            f.write(data)
        os.replace(tmp_path, icon_path)
        return icon_path
    except (urllib.error.URLError, OSError, TimeoutError):
        try:
            with open(missing_marker, "w", encoding="utf-8"):
                pass
        except OSError:
            pass
        return None


def check_am_installed():
    return GLib.find_program_in_path("am") is not None or GLib.find_program_in_path("appman") is not None


def am_command():
    """Prefers the real `am` binary (both AM and AppMan install it -
    "AppMan" is the same tool run as a non-root user, see this file's
    own docstring), falling back to `appman` only if that literal name
    is somehow the only one on $PATH."""
    return "am" if GLib.find_program_in_path("am") else "appman"


def catalog_sections(kind, is_installed):
    """The curated catalog (cosmos_catalog.py) as Shop sections: (sections, overflow, total), or None when no catalog is installed (a dev checkout),
    in which case the caller lists everything as before. kind is "rpm", "flatpak" or "appimage"; is_installed(pkg_id) hides what is already here.
    The catalog's own categories are the sections, in the order the file lists them."""
    try:
        import cosmos_catalog
    except ImportError:
        return None
    entries = cosmos_catalog.apps()
    if not entries:
        return None
    key = {"rpm": "rpm", "flatpak": "flatpak", "appimage": "appimage"}[kind]
    by_category, total = {}, 0
    for entry in entries:
        ids = entry.get(key) or []
        ids = [ids] if isinstance(ids, str) else list(ids)
        for pkg_id in ids:
            total += 1
            if is_installed(pkg_id):
                continue
            name = entry["name"] if len(ids) == 1 else pkg_id
            by_category.setdefault(entry["category"], []).append((pkg_id, name, entry.get("why", "")))
    sections = [(category, sorted(items, key=lambda t: t[1].lower())) for category, items in by_category.items()]
    return sections, [], total


def load_appimage_section():
    """Split by first letter (A-Z, plus one "0-9" bucket for anything
    else) rather than one giant flat list - AM's own database has no
    real per-app category data (see this file's own docstring on why),
    but a single ~3600-entry FlowBox is a real, confirmed bug of its
    own, independent of that: measured live via a GtkFlowBox harness at
    this section's real entry count, that many tiles requests a content
    height of ~62,000px - more than double X11's hard 32767px
    native-window limit (the same "Native Windows wider or taller than
    32767 pixels are not supported" GDK warns about for any window that
    size), which corrupts rendering partway down the list regardless of
    how many columns fit across the screen. Splitting by each entry's
    own first letter is a real partition of real data, not invented
    metadata - it keeps every bucket small enough (a few hundred at
    most, even for the most common letters) that this can never
    recur."""
    installed_slugs, installed_stripped = already_installed_gio_slugs()
    curated = catalog_sections("appimage", lambda name: is_excluded_appimage(name, installed_slugs, installed_stripped))
    if curated is not None:
        return curated
    text = fetch_appsdblist()
    all_entries = parse_appsdblist(text)
    kept = [
        (name, name, desc)
        for name, desc in all_entries
        if not is_excluded_appimage(name, installed_slugs, installed_stripped)
    ]
    buckets = {}
    for pkg_id, name, desc in kept:
        first = name[:1].upper()
        bucket = first if first.isalpha() else "0-9"
        buckets.setdefault(bucket, []).append((pkg_id, name, desc))
    sections = [(bucket, buckets[bucket]) for bucket in sorted(buckets)]
    return sections, [], len(all_entries)


# ======================================================================
# Flatpak (Flathub) data
# ======================================================================

def fetch_flathub_appstream():
    """Flathub's own real bulk AppStream catalog - confirmed live this
    is genuinely how software centers get Flathub's real names/
    descriptions/categories (`flatpak remote-ls` itself exposes none of
    these beyond a plain one-line description, confirmed live via
    `flatpak remote-ls --columns=help`). Cached compressed (~10MB) -
    decompressed and parsed fresh every launch rather than caching the
    ~50MB parsed XML, cheap enough (a few seconds) not to bother."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    if cache_is_fresh(FLATHUB_CACHE_PATH):
        with open(FLATHUB_CACHE_PATH, "rb") as f:
            return f.read()
    try:
        with urllib.request.urlopen(FLATHUB_APPSTREAM_URL, timeout=30) as resp:
            data = resp.read()
        with open(FLATHUB_CACHE_PATH, "wb") as f:
            f.write(data)
        return data
    except OSError:
        if os.path.exists(FLATHUB_CACHE_PATH):
            with open(FLATHUB_CACHE_PATH, "rb") as f:
                return f.read()
        raise


def parse_flathub_appstream(gz_data):
    """Streaming XML parse (xml.etree.ElementTree.iterparse), not a full
    DOM load - the decompressed catalog is ~50MB with dozens of
    <name xml:lang="..."> translations per app that this only needs to
    skip past, not hold onto. Returns {category: [(app_id, name, desc)]}
    - the FIRST <category> listed for each app is treated as its
    primary one (a real app can list several, e.g. GIMP lists Graphics,
    2DGraphics, RasterGraphics - only the first groups it here)."""
    import xml.etree.ElementTree as ET

    by_category = {}
    current_id = None
    current_name = None
    current_summary = None
    current_category = None
    depth_in_component = False

    with gzip.GzipFile(fileobj=__import__("io").BytesIO(gz_data)) as gz:
        for event, elem in ET.iterparse(gz, events=("start", "end")):
            tag = elem.tag
            if event == "start" and tag == "component":
                depth_in_component = True
                current_id = None
                current_name = None
                current_summary = None
                current_category = None
                continue
            if not depth_in_component:
                continue
            if event == "end":
                if tag == "id" and current_id is None:
                    current_id = (elem.text or "").strip()
                elif tag == "name" and elem.get("{http://www.w3.org/XML/1998/namespace}lang") is None:
                    if current_name is None:
                        current_name = (elem.text or "").strip()
                elif tag == "summary" and elem.get("{http://www.w3.org/XML/1998/namespace}lang") is None:
                    if current_summary is None:
                        current_summary = (elem.text or "").strip()
                elif tag == "category" and current_category is None:
                    current_category = (elem.text or "").strip()
                elif tag == "component":
                    if current_id and current_name:
                        cat = current_category or "Other"
                        by_category.setdefault(cat, []).append(
                            (current_id, current_name, current_summary or "")
                        )
                    depth_in_component = False
                    elem.clear()
    return by_category


def flathub_installed_ids():
    """Both --user and --system Flatpak installs count as "already
    installed" - a real app could be in either scope on a given
    machine, and this only needs to know it's there, not which scope."""
    ids = set()
    for scope in ("--user", "--system"):
        try:
            result = subprocess.run(
                ["flatpak", "list", scope, "--columns=application"],
                capture_output=True, text=True, timeout=15, check=False,
            )
            ids.update(line.strip() for line in result.stdout.splitlines() if line.strip())
        except (OSError, subprocess.TimeoutExpired):
            pass
    return ids


def load_flatpak_section():
    """Flatpak's entire catalog IS real category data (unlike AppImage/
    DNF) - every app gets grouped, no overflow/search-only bucket
    needed at all."""
    installed_ids = flathub_installed_ids()
    curated = catalog_sections("flatpak", lambda app_id: app_id in installed_ids)
    if curated is not None:
        return curated
    gz_data = fetch_flathub_appstream()
    by_category = parse_flathub_appstream(gz_data)
    total = sum(len(v) for v in by_category.values())
    sections = []
    for category in sorted(by_category):
        kept = [(app_id, name, desc) for app_id, name, desc in by_category[category] if app_id not in installed_ids]
        if kept:
            sections.append((category, kept))
    return sections, [], total


# ======================================================================
# DNF data
# ======================================================================

def dnf_installed_names():
    try:
        result = subprocess.run(
            ["rpm", "-qa", "--qf", "%{NAME}\n"], capture_output=True, text=True, timeout=30, check=False,
        )
        return {line.strip() for line in result.stdout.splitlines() if line.strip()}
    except (OSError, subprocess.TimeoutExpired):
        return set()


def fetch_dnf_available():
    """Every package available for install, real summary included -
    confirmed live (`dnf repoquery --available --qf ...`) this covers
    every enabled repo at once (Fedora, RPM Fusion free/nonfree, Terra -
    see the Containerfile's own comment on what's actually enabled),
    ~70,000 total. Cached the same way the other two catalogs are - a
    real `dnf repoquery` run is a few hundred ms once repo metadata is
    already local, but there's still no reason to re-run it every
    single launch."""
    if cache_is_fresh(DNF_AVAILABLE_CACHE_PATH):
        with open(DNF_AVAILABLE_CACHE_PATH, encoding="utf-8", errors="replace") as f:
            return f.read()
    os.makedirs(CACHE_DIR, exist_ok=True)
    result = subprocess.run(
        ["dnf", "repoquery", "--available", "--qf", "%{NAME}|%{SUMMARY}\n"],
        capture_output=True, text=True, timeout=120, check=False,
    )
    text = result.stdout
    if text.strip():
        with open(DNF_AVAILABLE_CACHE_PATH, "w", encoding="utf-8") as f:
            f.write(text)
        return text
    if os.path.exists(DNF_AVAILABLE_CACHE_PATH):
        with open(DNF_AVAILABLE_CACHE_PATH, encoding="utf-8", errors="replace") as f:
            return f.read()
    return text


def fetch_dnf_groups():
    """Fedora's own real comps groups (`dnf group list` + `dnf group
    info` per group) - direct requirement ("Use Fedora's ~30-40 comps
    groups") once it was confirmed live that DNF has no real per-package
    category data anywhere else (Fedora's own repo metadata has no
    "appstream" type at all - checked directly against the real
    repomd.xml, only primary/filelists/other/group exist). Cached as a
    plain "group name|pkg1,pkg2,pkg3" text file, same convention as
    every other cache here, since this needs one `dnf group info` call
    per group (~30-40 of them) which is real, measurable work worth not
    repeating on every launch."""
    if cache_is_fresh(DNF_GROUPS_CACHE_PATH):
        groups = {}
        with open(DNF_GROUPS_CACHE_PATH, encoding="utf-8", errors="replace") as f:
            for line in f:
                if "|" not in line:
                    continue
                name, pkgs = line.rstrip("\n").split("|", 1)
                groups[name] = pkgs.split(",") if pkgs else []
        return groups

    os.makedirs(CACHE_DIR, exist_ok=True)
    list_result = subprocess.run(
        ["dnf", "group", "list"], capture_output=True, text=True, timeout=60, check=False,
    )
    group_ids = []
    for line in list_result.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[-1] in ("yes", "no"):
            group_ids.append(parts[0])

    groups = {}
    for group_id in group_ids:
        info_result = subprocess.run(
            ["dnf", "group", "info", group_id], capture_output=True, text=True, timeout=30, check=False,
        )
        group_name = group_id
        packages = []
        in_packages = False
        for line in info_result.stdout.splitlines():
            if line.startswith("Name "):
                group_name = line.split(":", 1)[-1].strip()
            elif re.match(r"^(Mandatory|Default|Optional) packages", line):
                in_packages = True
                pkg = line.split(":", 1)[-1].strip()
                if pkg:
                    packages.append(pkg)
            elif in_packages and line.startswith("      "):
                pkg = line.split(":", 1)[-1].strip()
                if pkg:
                    packages.append(pkg)
            else:
                in_packages = False
        if packages:
            groups[group_name] = packages

    with open(DNF_GROUPS_CACHE_PATH, "w", encoding="utf-8") as f:
        for name, pkgs in groups.items():
            f.write(f"{name}|{','.join(pkgs)}\n")
    return groups


def load_dnf_section():
    """Real comps-group categories for the (small) subset of packages
    that are actually in one, everything else goes to the overflow
    bucket (searchable, never eagerly built as tiles - see
    OverflowSection's own docstring)."""
    installed = dnf_installed_names()
    curated = catalog_sections("rpm", lambda name: name in installed)
    if curated is not None:
        return curated
    text = fetch_dnf_available()
    all_entries = []
    for line in text.splitlines():
        if "|" not in line:
            continue
        name, summary = line.split("|", 1)
        all_entries.append((name.strip(), summary.strip()))

    groups = fetch_dnf_groups()
    package_to_group = {}
    for group_name, pkgs in groups.items():
        for pkg in pkgs:
            package_to_group.setdefault(pkg, group_name)

    by_category = {}
    overflow = []
    for name, summary in all_entries:
        if name in installed:
            continue
        category = package_to_group.get(name)
        if category:
            by_category.setdefault(category, []).append((name, name, summary))
        else:
            overflow.append((name, name, summary))

    sections = [(cat, entries) for cat, entries in sorted(by_category.items())]
    return sections, overflow, len(all_entries)


# ======================================================================
# GTK UI
# ======================================================================

def load_packs_section():
    """Expansion Packs: bundles (the developer tools, ...) that are not in the base image. One section, every pack, with its
    state in the description so a partly installed pack can be completed."""
    import cosmos_packs
    packs = cosmos_packs.load()
    have = cosmos_packs.installed()
    entries = []
    for pack in packs:
        state = cosmos_packs.status(packs, pack["id"], have)["state"]
        mark = {"installed": "Installed. ", "partial": "Partly installed - install to complete it. ", "none": ""}[state]
        size = f" ({pack['size']})" if pack.get("size") else ""
        entries.append((pack["id"], pack["name"], f"{mark}{pack.get('description', '')}{size}"))
    return ([("Expansion Packs", entries)] if entries else []), [], len(entries)


def packs_install_cmd(pack_id):
    import cosmos_packs
    return cosmos_packs.install_command(cosmos_packs.load(), pack_id)


def packs_info_cmd(pack_id):
    import cosmos_packs
    import shlex
    return "printf '%s\\n' " + shlex.quote(cosmos_packs.info_text(cosmos_packs.load(), pack_id))


def games_download_cmd(pkg_id):
    import cosmos_gamegrubber
    return cosmos_gamegrubber.download_command(pkg_id)


SOURCES = {
    "packs": {
        "label": "Expansion Packs",
        "loader": load_packs_section,
        "install_cmd": packs_install_cmd,
        "info_cmd": packs_info_cmd,
    },
    "dnf": {
        "label": "DNF Packages",
        "loader": load_dnf_section,
        # on the image dnf can't write /usr, so packages are layered with rpm-ostree through kronos; on an ordinary Fedora it is `sudo dnf install`
        "install_cmd": lambda pkg_id: __import__("cosmos_packs").rpm_install_command(shlex.quote(pkg_id)),
        "info_cmd": lambda pkg_id: f"dnf info '{pkg_id}'",
    },
    "appimage": {
        "label": "AppImages (AM)",
        "loader": load_appimage_section,
        "install_cmd": lambda pkg_id: f"{am_command()} -i --user '{pkg_id}'",
        "info_cmd": lambda pkg_id: f"{am_command()} -a '{pkg_id}'",
    },
    "games": {
        "label": "Games",
        "loader": None,                                 # a paged list (GameListPage), not tiles
        "install_cmd": games_download_cmd,
        "info_cmd": lambda pkg_id: "printf '%s\\n' " + shlex.quote("Downloads to /cosmos/roms, where GameBox finds it."),
        "verb": "Download",
    },
    "bios": {
        "label": "BIOS",
        "loader": None,
        "install_cmd": games_download_cmd,
        "info_cmd": lambda pkg_id: "printf '%s\\n' " + shlex.quote("System files some emulators need; downloads to /cosmos/roms/bios."),
        "verb": "Download",
    },
    "flatpak": {
        "label": "Flatpak (Flathub)",
        "loader": load_flatpak_section,
        "install_cmd": lambda pkg_id: f"flatpak install --user -y flathub '{pkg_id}' && (find-terminals --purge-flatpaks || true)",
        "info_cmd": lambda pkg_id: f"flatpak remote-info --user flathub '{pkg_id}'",
    },
}
def visible_sources():
    """The sources this window shows. Games and BIOS stay hidden until the game-download module is installed and switched on (the switch and its
    one-time disclaimer live in the universal Settings app, cosmos_gamegrubber.enabled())."""
    order = ["packs", "dnf", "appimage", "flatpak"]
    try:
        import cosmos_gamegrubber
        if cosmos_gamegrubber.visible():
            order += ["games", "bios"]
    except ImportError:
        pass
    return order


SOURCE_ORDER = visible_sources()

# One packaging per app (cosmos_app_policy.py): the Shop won't offer a second copy of an app that is
# already here another way, and says why.
POLICY_KIND = {"dnf": "rpm", "appimage": "appimage", "flatpak": "flatpak"}


def install_refusal(key, pkg_id):
    """Why this install must not go ahead (a conflicting copy exists), or "" when it is fine."""
    try:
        import cosmos_app_policy
    except ImportError:
        return ""
    if key == "packs":                      # every member of the pack must pass the same checks
        import cosmos_packs
        packs = cosmos_packs.load()
        if cosmos_packs.by_id(packs, pkg_id) is None:
            return "That expansion pack doesn't exist."
        problems = cosmos_packs.policy_problems(packs, pkg_id)
        return "; ".join(problems)
    if key not in POLICY_KIND:              # games and BIOS files are downloads, not packages
        return ""
    ok, reason = cosmos_app_policy.check(POLICY_KIND[key], pkg_id)
    if ok and key == "flatpak":
        ok, reason = cosmos_app_policy.check_flatpak_scope(pkg_id, "user")
    return "" if ok else reason


class InstallAMDialog(Gtk.Box):
    """Shown in place of the AppImage section's own content when
    `am`/`appman` isn't on $PATH yet - direct requirement ("Trigger it
    myself"): AppMan/AM is never auto-installed at login, switching to
    this one section is the only place that ever offers to. A plain
    Gtk.Box embedded inline, not its own top-level window - DNF/Flatpak
    both work with no bootstrap at all, so this only ever blocks this
    one section, never the whole app."""

    def __init__(self, on_installed):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=20)
        self.set_halign(Gtk.Align.CENTER)
        self.set_valign(Gtk.Align.CENTER)
        self.on_installed = on_installed

        title = Gtk.Label(label="AppMan isn't installed yet")
        title.get_style_context().add_class("cosmos-shop-query")
        self.pack_start(title, False, False, 0)

        body = Gtk.Label(
            label=(
                "The AppImage section browses the real AM/AppMan catalog, but\n"
                "actually installing anything needs AM/AppMan itself first.\n"
                "This runs the real upstream installer in a terminal - it asks\n"
                "its own local-vs-system-wide question, so it isn't silent."
            )
        )
        body.get_style_context().add_class("cosmos-shop-hint")
        body.set_justify(Gtk.Justification.CENTER)
        self.pack_start(body, False, False, 0)

        button_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        button_row.set_halign(Gtk.Align.CENTER)
        self.pack_start(button_row, False, False, 0)

        install_button = Gtk.Button(label="Install AM/AppMan")
        install_button.get_style_context().add_class("cosmos-shop-button-accent")
        install_button.connect("clicked", self.on_install_clicked)
        button_row.pack_start(install_button, False, False, 0)

    def on_install_clicked(self, _button):
        cmd = (
            f"wget -q {AM_INSTALLER_URL} -O /tmp/AM-INSTALLER "
            "&& chmod a+x /tmp/AM-INSTALLER && /tmp/AM-INSTALLER && rm -f /tmp/AM-INSTALLER"
        )
        run_in_terminal(cmd, "Installer finished - press Enter to return to CosmOS Shop...")
        GLib.timeout_add(500, self._poll_for_install)

    def _poll_for_install(self):
        if check_am_installed():
            self.on_installed()
            return False
        return True


LIST_SOURCES = ("games", "bios")          # shown as a paged list (a system, then its games) instead of tiles


class GameListPage(Gtk.Box):
    """Games and BIOS, laid out the way GameBox's own list was, with manufacturers on top: pick a manufacturer, then one of its systems, then its games as
    rows ("Title  (size)") in GameGrubber's own order, a page of GAME_PAGE_SIZE at a time, with Prev/Next. Typing (the Shop's own search) filters whichever
    list is showing; Enter opens or downloads the selected row; "< Back", Backspace on an empty search and Escape each step back up a level. BIOS has one
    "system" and opens straight onto its files."""

    def __init__(self, key, shop):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.key, self.shop = key, shop
        self.mode = "loading"              # loading | makers | systems | games
        self.platforms, self.platform = [], None
        self.groups, self.maker = [], None          # [(manufacturer, [systems])], and the one that is open
        self.games, self.filtered, self.page = None, [], 0
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        self.back_button = Gtk.Button(label="< Back")
        self.back_button.get_style_context().add_class("cosmos-shop-button")
        self.back_button.set_no_show_all(True)               # only while a system's games are showing
        self.back_button.connect("clicked", lambda _b: self.go_back())
        header.pack_start(self.back_button, False, False, 0)
        self.heading = Gtk.Label(label="")
        self.heading.get_style_context().add_class("cosmos-shop-section-header")
        header.pack_start(self.heading, False, False, 0)
        self.pack_start(header, False, False, 0)
        self.status = Gtk.Label(label="Loading...")
        self.status.get_style_context().add_class("cosmos-shop-count")
        self.pack_start(self.status, False, False, 0)
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.pack_start(scroller, True, True, 0)
        self.listbox = Gtk.ListBox()
        self.listbox.set_selection_mode(Gtk.SelectionMode.BROWSE)
        self.listbox.get_style_context().add_class("cosmos-shop-list")
        self.listbox.connect("row-activated", lambda _lb, row: self.activate(row))
        scroller.add(self.listbox)
        self.pager = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.prev_button = Gtk.Button(label="< Prev Page")
        self.next_button = Gtk.Button(label="Next Page >")
        self.page_label = Gtk.Label(label="")
        for button, step in ((self.prev_button, -1), (self.next_button, 1)):
            button.get_style_context().add_class("cosmos-shop-button")
            button.connect("clicked", lambda _b, d=step: self.change_page(d))
        self.page_label.get_style_context().add_class("cosmos-shop-count")
        self.pager.pack_start(self.prev_button, False, False, 0)
        self.pager.pack_start(self.page_label, True, True, 0)
        self.pager.pack_start(self.next_button, False, False, 0)
        self.pack_start(self.pager, False, False, 0)

    # -- loading (off the GTK thread, like every source) --
    def start(self):
        threading.Thread(target=self._load_platforms, daemon=True).start()

    def _load_platforms(self):
        import cosmos_gamegrubber
        try:
            platforms = [cosmos_gamegrubber.bios_platform()] if self.key == "bios" else cosmos_gamegrubber.platforms()
        except Exception as exc:
            GLib.idle_add(self.status.set_text, f"Couldn't load the game systems: {exc}")
            return
        GLib.idle_add(self._on_platforms, platforms)

    def _on_platforms(self, platforms):
        self.platforms = platforms
        if not platforms:
            self.status.set_text("Nothing to show yet. Is the Game Downloads module installed and its data downloaded?")
        elif self.key == "bios":
            self.open_platform(platforms[0])
        else:
            import cosmos_gamegrubber
            self.groups = cosmos_gamegrubber.makers(platforms)
            self.show_makers()
        return False

    # -- the two views --
    def clear_rows(self):
        for child in list(self.listbox.get_children()):
            self.listbox.remove(child)

    def add_row(self, text, item):
        row = Gtk.ListBoxRow()
        row.item = item
        label = Gtk.Label(label=text, xalign=0)
        label.set_line_wrap(True)
        row.add(label)
        self.listbox.add(row)

    def finish_render(self):
        self.listbox.show_all()
        first = self.listbox.get_row_at_index(0)
        if first is not None:
            self.listbox.select_row(first)
            first.grab_focus()

    def show_makers(self):
        import cosmos_gamegrubber
        self.mode, self.maker, self.platform = "makers", None, None
        self.back_button.hide()
        self.heading.set_text("Games - pick a manufacturer")
        shown = cosmos_gamegrubber.search_makers(self.groups, self.shop.query)
        self.clear_rows()
        for maker, systems in shown:
            self.add_row(f"{maker}  ({len(systems)} system{'' if len(systems) == 1 else 's'})", (maker, systems))
        self.pager.hide()
        self.status.set_text(f"{len(shown)} manufacturer(s) - type to filter, Enter to open" if shown else "No manufacturers match.")
        self.finish_render()

    def show_systems(self):
        """The systems of the open manufacturer."""
        import cosmos_gamegrubber
        self.mode, self.platform = "systems", None
        self.back_button.set_visible(self.key == "games")
        self.heading.set_text(f"Games - {self.maker[0]}" if self.maker else "Games")
        shown = cosmos_gamegrubber.search_systems(self.maker[1] if self.maker else self.platforms, self.shop.query)
        self.clear_rows()
        for platform in shown:
            self.add_row(platform["name"], platform)
        self.pager.hide()
        self.status.set_text(f"{len(shown)} system(s) - type to filter, Enter to open" if shown else "No systems match.")
        self.finish_render()

    def open_platform(self, platform):
        import cosmos_gamegrubber
        self.mode, self.platform, self.games, self.page = "games", platform, None, 0
        self.back_button.set_visible(self.key == "games")                  # BIOS has nothing to go back to
        self.heading.set_text("BIOS" if platform["id"] == cosmos_gamegrubber.BIOS_ID else f"Games - {platform['name']}")
        self.status.set_text("Loading games...")
        self.clear_rows()
        self.pager.hide()
        if self.key == "games":
            self.shop.update_query("")                        # start each system with an empty search
        threading.Thread(target=lambda: GLib.idle_add(self._on_games, platform, cosmos_gamegrubber.list_games(platform["id"])), daemon=True).start()

    def _on_games(self, platform, games):
        if self.platform is not platform:                     # went back before it finished
            return False
        self.games = games
        self.apply_query(self.shop.query)
        return False

    def apply_query(self, query):
        """The Shop's search text changed: filter the list that is showing."""
        import cosmos_gamegrubber
        if self.mode == "makers":
            self.show_makers()
        elif self.mode == "systems":
            self.show_systems()
        elif self.mode == "games" and self.games is not None:
            self.filtered = cosmos_gamegrubber.search_games(self.games, query)
            self.page = 0
            self.render_games()

    def page_count(self):
        import cosmos_gamegrubber
        return max(1, -(-len(self.filtered) // cosmos_gamegrubber.GAME_PAGE_SIZE))

    def render_games(self):
        import cosmos_gamegrubber
        size = cosmos_gamegrubber.GAME_PAGE_SIZE
        start = self.page * size
        rows = self.filtered[start:start + size]
        self.clear_rows()
        for game in rows:
            self.add_row(cosmos_gamegrubber.game_label(game), game)
        if rows:
            self.status.set_text(f"{start + 1}-{start + len(rows)} of {len(self.filtered)} result(s) - Down to browse, Enter to download")
        else:
            self.status.set_text("No games found.")
        self.page_label.set_text(f"Page {self.page + 1} of {self.page_count()}")
        self.prev_button.set_sensitive(self.page > 0)
        self.next_button.set_sensitive(self.page < self.page_count() - 1)
        self.pager.show_all()
        self.finish_render()

    def change_page(self, step):
        new = self.page + step
        if 0 <= new < self.page_count():
            self.page = new
            self.render_games()

    # -- choosing --
    def activate(self, row):
        import cosmos_gamegrubber
        if row is None:
            return
        if self.mode == "makers":
            maker = row.item
            self.shop.update_query("")               # clears the search (which redraws the manufacturers), so only then note which one was picked
            self.maker = maker
            self.show_systems()
        elif self.mode == "systems":
            self.open_platform(row.item)
        elif self.mode == "games":
            game = row.item
            pkg_id = cosmos_gamegrubber.item_id(self.platform["id"], game["name"])
            title = game.get("display_name") or game["name"]
            self.shop.confirm_install(self.key, pkg_id, title, f"{self.platform['name']}" + (f" - {game['size']}" if game.get("size") else ""))

    def activate_selected(self):
        rows = self.listbox.get_selected_rows()
        self.activate(rows[0] if rows else None)

    def go_back(self):
        """Up one level: a system's games -> that manufacturer's systems -> the manufacturers (the "< Back" button, Backspace on an empty search, or
        Escape). True when it went up, so the window stays open; False at the top."""
        if self.key != "games":
            return False
        if self.mode == "games":
            self.shop.update_query("")
            self.show_systems()
            return True
        if self.mode == "systems":
            self.shop.update_query("")
            self.show_makers()
            return True
        return False


class CosmosShop(Gtk.Window):
    def __init__(self):
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        self.set_title("CosmOS Shop")
        cosmos_display.size_window(self, 1180, 780)               # an ordinary window: resizable, with a title bar, in the taskbar; Escape still closes it
        self.set_position(Gtk.WindowPosition.CENTER)
        self.get_style_context().add_class("cosmos-shop")
        self.connect("destroy", Gtk.main_quit)

        self.query = ""
        self.active_source = "packs"                   # the first tab, and the quick one (DNF's 70,000-package list loads in the background)
        # source -> bool: whether load_*_section() has run for it yet
        self.loaded = {key: False for key in SOURCE_ORDER}
        # source -> [(header_label_or_None, flowbox), ...] for the
        # eagerly-built category sections
        self.category_flowboxes = {key: [] for key in SOURCE_ORDER}
        # source -> [(pkg_id, name, desc), ...] not eagerly built (DNF
        # only, in practice) - see OverflowSection's own docstring
        self.overflow_entries = {key: [] for key in SOURCE_ORDER}
        # source -> Gtk.FlowBox used only for dynamic overflow search
        # results, rebuilt from scratch on every query change
        self.overflow_flowboxes = {}
        # source -> Gtk.Label, "N of M" style
        self.count_labels = {}
        # (pkg_id, name, desc) -> Gtk.Image, AppImage-only (see
        # start_icon_fetching()'s own docstring on why just that one)
        self.icon_images = {}

        root_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        root_box.set_border_width(32)
        self.add(root_box)

        source_button_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        source_button_row.set_halign(Gtk.Align.CENTER)
        root_box.pack_start(source_button_row, False, False, 0)

        self.source_buttons = {}
        for key in SOURCE_ORDER:
            button = Gtk.Button(label=SOURCES[key]["label"])
            button.get_style_context().add_class("cosmos-shop-section-button")
            button.connect("clicked", self.on_source_button_clicked, key)
            source_button_row.pack_start(button, False, False, 0)
            self.source_buttons[key] = button

        self.query_label = Gtk.Label(label="Type to search...")
        self.query_label.get_style_context().add_class("cosmos-shop-query")
        self.query_label.set_halign(Gtk.Align.CENTER)
        root_box.pack_start(self.query_label, False, False, 0)

        self.stack = Gtk.Stack()
        root_box.pack_start(self.stack, True, True, 0)

        for key in SOURCE_ORDER:
            self.stack.add_named(self.build_source_page(key), key)

        hint_label = Gtk.Label(
            label=(
                "Type to search · Arrow keys/Tab to browse · Enter to install · "
                "Right-click for details · Escape or Backspace to go back, then close"
            )
        )
        hint_label.get_style_context().add_class("cosmos-shop-hint")
        hint_label.set_halign(Gtk.Align.CENTER)
        root_box.pack_start(hint_label, False, False, 0)

        self.connect("key-press-event", self.on_key_press)

        self.set_active_source("packs")
        for key in SOURCE_ORDER:
            self.load_source(key)

    # -- page/section construction ---------------------------------

    def build_source_page(self, key):
        if key in LIST_SOURCES:
            return GameListPage(key, self)
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)

        count_label = Gtk.Label(label="Loading...")
        count_label.get_style_context().add_class("cosmos-shop-count")
        count_label.set_halign(Gtk.Align.CENTER)
        page.pack_start(count_label, False, False, 0)
        self.count_labels[key] = count_label

        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        page.pack_start(scroller, True, True, 0)

        sections_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=20)
        scroller.add(sections_box)
        page._sections_box = sections_box  # populated once load_source() runs
        return page

    def load_source(self, key):
        """The actual loader (SOURCES[key]["loader"]()) runs on a
        background thread, not GLib.idle_add straight on the main loop -
        real, confirmed-live bug this fixes: fetch_dnf_groups() alone
        (52 sequential `dnf group info` subprocess calls, each paying
        dnf5's own repo-reload overhead) measured ~40s cold, and it used
        to run inline on the GTK main thread, freezing the entire window
        - no redraws, no input, nothing - for the whole time, on every
        cold cache (once a day, see CACHE_MAX_AGE) for every account
        that ever opens this app. Only the actual GTK widget
        construction (_on_source_loaded()) still runs on the main
        thread, via GLib.idle_add from the worker - the same pattern
        start_icon_fetching() already uses for the AppImage icon
        workers, just applied to the catalog load itself too."""
        if key in LIST_SOURCES:
            self.stack.get_child_by_name(key).start()
            self.loaded[key] = True
            return
        self.count_labels[key].set_text("Loading...")
        threading.Thread(target=self._load_source_worker, args=(key,), daemon=True).start()

    def _load_source_worker(self, key):
        try:
            categories, overflow, total_available = SOURCES[key]["loader"]()
        except (OSError, subprocess.SubprocessError) as exc:
            GLib.idle_add(self._on_source_load_failed, key, str(exc))
            return
        GLib.idle_add(self._on_source_loaded, key, categories, overflow, total_available)

    def _on_source_load_failed(self, key, message):
        self.count_labels[key].set_text(f"Couldn't load this catalog: {message}")
        return False

    def _on_source_loaded(self, key, categories, overflow, total_available):
        page = self.stack.get_child_by_name(key)
        sections_box = page._sections_box

        # A real, dynamic-only search-results section, always first -
        # see OverflowSection's own docstring. Built even for sources
        # with no overflow (harmless, just never shown) to keep the
        # per-source bookkeeping uniform.
        overflow_header = Gtk.Label(label="Search Results")
        overflow_header.get_style_context().add_class("cosmos-shop-section-header")
        overflow_header.set_halign(Gtk.Align.START)
        overflow_header.set_no_show_all(True)
        overflow_flowbox = Gtk.FlowBox()
        overflow_flowbox.set_valign(Gtk.Align.START)
        overflow_flowbox.set_halign(Gtk.Align.START)
        overflow_flowbox.set_selection_mode(Gtk.SelectionMode.SINGLE)
        overflow_flowbox.set_homogeneous(True)
        overflow_flowbox.set_row_spacing(4)
        overflow_flowbox.set_column_spacing(4)
        overflow_flowbox.set_max_children_per_line(FLOWBOX_MAX_COLUMNS)
        overflow_flowbox.connect("child-activated", self.on_child_activated, key)
        overflow_flowbox.connect("selected-children-changed", self.on_selection_changed, key)
        overflow_flowbox.set_no_show_all(True)
        sections_box.pack_start(overflow_header, False, False, 0)
        sections_box.pack_start(overflow_flowbox, False, False, 0)
        self.overflow_flowboxes[key] = (overflow_header, overflow_flowbox)
        self.overflow_entries[key] = overflow

        multiple_categories = len(categories) > 1
        for category_name, entries in categories:
            header = None
            if multiple_categories:
                header = Gtk.Label(label=category_name)
                header.get_style_context().add_class("cosmos-shop-section-header")
                header.set_halign(Gtk.Align.START)
                sections_box.pack_start(header, False, False, 0)

            flowbox = Gtk.FlowBox()
            flowbox.set_valign(Gtk.Align.START)
            flowbox.set_halign(Gtk.Align.START)
            flowbox.set_selection_mode(Gtk.SelectionMode.SINGLE)
            flowbox.set_homogeneous(True)
            flowbox.set_row_spacing(4)
            flowbox.set_column_spacing(4)
            flowbox.set_max_children_per_line(FLOWBOX_MAX_COLUMNS)
            local_entries = list(entries)
            flowbox._local_entries = local_entries
            flowbox.set_filter_func(self.make_filter_func(flowbox))
            flowbox.connect("child-activated", self.on_child_activated, key)
            flowbox.connect("selected-children-changed", self.on_selection_changed, key)
            for pkg_id, name, desc in local_entries:
                tile = self.build_tile(key, pkg_id, name, desc)
                flowbox.add(tile)
            sections_box.pack_start(flowbox, False, False, 0)
            self.category_flowboxes[key].append((header, flowbox))

        sections_box.show_all()
        overflow_header.hide()
        overflow_flowbox.hide()

        shown = sum(len(entries) for _cat, entries in categories) + len(overflow)
        self.count_labels[key].set_text(f"{shown} available (not already on this system) of {total_available}")
        self.loaded[key] = True
        if key == self.active_source:
            self.update_query(self.query)
            # Same real gap as set_active_source()'s own AM-bootstrap
            # check, just on the other trigger: if the user switches to
            # AppImages before this source's background load finishes,
            # set_active_source() itself sees self.loaded["appimage"]
            # still False and skips the check entirely - nothing here
            # picked it back up once loading actually completed, so
            # AppMan-not-installed would never show the bootstrap dialog
            # at all for that ordering.
            if key == "appimage" and not check_am_installed():
                self.show_am_bootstrap()
        if key == "appimage":
            self.start_icon_fetching(key)
        return False

    def make_filter_func(self, flowbox):
        def filter_func(child):
            idx = child.get_index()
            _pkg_id, name, desc = flowbox._local_entries[idx]
            return matches_query(self.query, name, desc)

        return filter_func

    def build_tile(self, key, pkg_id, name, desc):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.set_size_request(140, 110)

        image = Gtk.Image.new_from_icon_name("package-x-generic", Gtk.IconSize.DIALOG)
        image.set_pixel_size(TILE_ICON_SIZE)
        image.set_halign(Gtk.Align.CENTER)
        box.pack_start(image, False, False, 0)
        if key == "appimage":
            self.icon_images[pkg_id] = image

        label = Gtk.Label(label=name)
        label.get_style_context().add_class("cosmos-shop-tile-label")
        label.set_line_wrap(True)
        label.set_justify(Gtk.Justification.CENTER)
        label.set_lines(2)
        # Confirmed live: passing a plain bool here silently coerces to
        # Pango.EllipsizeMode.START (True == 1, the same integer value) -
        # every overflowing name got truncated from the front ("...ist"
        # instead of "Full Metal Alch...") instead of the intended end.
        label.set_ellipsize(Pango.EllipsizeMode.END)
        label.set_max_width_chars(16)
        box.pack_start(label, False, False, 0)

        event_box = Gtk.EventBox()
        event_box.add(box)
        event_box.set_tooltip_text(desc)
        event_box.connect("button-press-event", self.on_tile_button_press, key, pkg_id, name, desc)
        return event_box

    # -- source switching --------------------------------------------

    def on_source_button_clicked(self, _button, key):
        self.set_active_source(key)

    def set_active_source(self, key):
        self.active_source = key
        self.stack.set_visible_child_name(key)
        for source_key, button in self.source_buttons.items():
            ctx = button.get_style_context()
            if source_key == key:
                ctx.remove_class("cosmos-shop-section-button")
                ctx.add_class("cosmos-shop-section-button-active")
            else:
                ctx.remove_class("cosmos-shop-section-button-active")
                ctx.add_class("cosmos-shop-section-button")
        # update_query() must run BEFORE show_am_bootstrap(), not after -
        # real, confirmed-live regression from this file's own search
        # fix (update_query() now sets each category header/flowbox's
        # visibility to match the current query, where it used to leave
        # visibility alone entirely): calling it after
        # show_am_bootstrap()'s own hide-everything loop immediately
        # re-showed every tile behind the "AppMan isn't installed yet"
        # dialog instead of hiding them.
        self.update_query(self.query)
        if key == "appimage" and self.loaded["appimage"] and not check_am_installed():
            self.show_am_bootstrap()

    def show_am_bootstrap(self):
        page = self.stack.get_child_by_name("appimage")
        for child in page._sections_box.get_children():
            child.hide()
        if getattr(page, "_am_dialog", None) is None:
            def on_installed():
                page._am_dialog.hide()
                for c in page._sections_box.get_children():
                    c.show()

            page._am_dialog = InstallAMDialog(on_installed)
            page.pack_start(page._am_dialog, True, True, 0)
        page._am_dialog.show_all()

    # -- icons (AppImage only) ----------------------------------------

    def start_icon_fetching(self, key):
        """Real per-app icons, AppImage-only (see ICON_URL_TEMPLATE's
        own comment on why not DNF/Flatpak too) - background worker
        pool, not one request at a time or all at once, so browsing/
        searching stays responsive regardless of how far fetching has
        gotten."""
        work_queue = queue.Queue()
        for pkg_id in list(self.icon_images):
            work_queue.put(pkg_id)

        def worker():
            while True:
                try:
                    name = work_queue.get_nowait()
                except queue.Empty:
                    return
                icon_path = fetch_icon(name)
                if icon_path:
                    GLib.idle_add(self.apply_icon, name, icon_path)
                work_queue.task_done()

        for _ in range(ICON_FETCH_WORKERS):
            threading.Thread(target=worker, daemon=True).start()

    def apply_icon(self, name, icon_path):
        image = self.icon_images.get(name)
        if image is None:
            return False
        try:
            cosmos_display.set_image_from_file(image, icon_path, TILE_ICON_SIZE, TILE_ICON_SIZE)
        except GLib.Error:
            pass  # a corrupt/truncated download - the generic fallback icon stays
        return False

    # -- search / overflow ---------------------------------------------

    def update_query(self, new_query):
        self.query = new_query
        self.query_label.set_text(self.query if self.query else "Type to search...")

        key = self.active_source
        for header, flowbox in self.category_flowboxes.get(key, []):
            flowbox.invalidate_filter()
            # Real, confirmed-live bug this fixes: invalidate_filter()
            # only hides the individual tiles that stop matching - the
            # category's own header label had no visibility logic tied
            # to that at all, so searching for anything left every
            # category with zero matches still showing its header with
            # nothing underneath (confirmed live: searching "openscad"
            # showed the 2 real matches under "3D Printing" followed by
            # ~40 more empty category headers with no tiles at all,
            # all the way down).
            has_match = any(child.get_child_visible() for child in flowbox.get_children())
            if header is not None:
                header.set_visible(has_match)
            flowbox.set_visible(has_match)

        self.rebuild_overflow_results(key)
        page = self.stack.get_child_by_name(key)
        if isinstance(page, GameListPage):
            page.apply_query(self.query)
            return
        self.select_first_visible()

    def rebuild_overflow_results(self, key):
        pair = self.overflow_flowboxes.get(key)
        if pair is None:
            return
        header, flowbox = pair
        for child in list(flowbox.get_children()):
            flowbox.remove(child)
        if not self.query:
            header.hide()
            flowbox.hide()
            return
        matches = []
        for pkg_id, name, desc in self.overflow_entries.get(key, []):
            if matches_query(self.query, name, desc):
                matches.append((pkg_id, name, desc))
                if len(matches) >= OVERFLOW_SEARCH_LIMIT:
                    break
        if not matches:
            header.hide()
            flowbox.hide()
            return
        flowbox._local_entries = matches
        for pkg_id, name, desc in matches:
            tile = self.build_tile(key, pkg_id, name, desc)
            tile.show_all()
            flowbox.add(tile)
        # the results box has no-show-all set (it starts hidden), and show_all() does nothing to such a widget: show the box itself, and each tile above
        flowbox.show()
        header.show()

    def select_first_visible(self):
        key = self.active_source
        overflow_pair = self.overflow_flowboxes.get(key)
        flowboxes = [fb for _h, fb in self.category_flowboxes.get(key, [])]
        if overflow_pair is not None and overflow_pair[1].get_visible():
            flowboxes.insert(0, overflow_pair[1])
        for flowbox in flowboxes:
            flowbox.unselect_all()
        for flowbox in flowboxes:
            for i in range(len(getattr(flowbox, "_local_entries", []))):
                child = flowbox.get_child_at_index(i)
                if child is not None and child.get_child_visible():
                    flowbox.select_child(child)
                    child.grab_focus()
                    return

    # -- tile interactions ---------------------------------------------

    def on_tile_button_press(self, _widget, event, key, pkg_id, name, desc):
        if event.button != 3:
            return False
        menu = Gtk.Menu()
        menu.get_style_context().add_class("cosmos-shop-menu")

        info_item = Gtk.MenuItem(label="Details")

        def on_info(_item):
            run_in_terminal(SOURCES[key]["info_cmd"](pkg_id), "Press Enter to close...")

        info_item.connect("activate", on_info)
        menu.append(info_item)

        install_item = Gtk.MenuItem(label="Install")
        install_item.connect("activate", lambda _i: self.confirm_install(key, pkg_id, name, desc))
        menu.append(install_item)

        menu.show_all()
        menu.popup_at_pointer(event)
        return True

    def confirm_install(self, key, pkg_id, name, desc):
        window = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
        window.set_title("cosmos-shop-confirm")
        window.set_type_hint(Gdk.WindowTypeHint.DIALOG)
        window.set_decorated(False)
        window.set_skip_taskbar_hint(True)
        window.set_skip_pager_hint(True)
        window.set_transient_for(self)
        window.set_position(Gtk.WindowPosition.CENTER_ON_PARENT)
        window.get_style_context().add_class("cosmos-shop")
        window.set_size_request(420, -1)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        box.set_border_width(20)
        window.add(box)

        refusal = install_refusal(key, pkg_id)
        label = Gtk.Label(label=(f'Can\'t install "{name}" via {SOURCES[key]["label"]}.\n{refusal}' if refusal
                                 else f'{SOURCES[key].get("verb", "Install")} "{name}" via {SOURCES[key]["label"]}?\n{desc}'))
        label.get_style_context().add_class("cosmos-shop-tile-label")
        label.set_line_wrap(True)
        label.set_justify(Gtk.Justification.CENTER)
        box.pack_start(label, False, False, 0)

        button_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        button_row.set_halign(Gtk.Align.END)
        box.pack_start(button_row, False, False, 0)

        no_button = Gtk.Button(label="OK" if refusal else "Cancel")
        no_button.get_style_context().add_class("cosmos-shop-button")
        no_button.connect("clicked", lambda _b: window.destroy())
        button_row.pack_start(no_button, False, False, 0)

        yes_button = Gtk.Button(label=SOURCES[key].get("verb", "Install"))
        yes_button.get_style_context().add_class("cosmos-shop-button-accent")

        def on_yes(_b):
            window.destroy()
            run_in_terminal(SOURCES[key]["install_cmd"](pkg_id))

        yes_button.connect("clicked", on_yes)
        if not refusal:
            button_row.pack_start(yes_button, False, False, 0)

        window.connect(
            "key-press-event",
            lambda _w, event: window.destroy() if event.keyval == Gdk.KEY_Escape else None,
        )
        window.show_all()
        window.present()
        no_button.grab_focus()

    def launch_selected(self):
        key = self.active_source
        page = self.stack.get_child_by_name(key)
        if isinstance(page, GameListPage):
            page.activate_selected()
            return
        overflow_pair = self.overflow_flowboxes.get(key)
        flowboxes = [fb for _h, fb in self.category_flowboxes.get(key, [])]
        if overflow_pair is not None:
            flowboxes.insert(0, overflow_pair[1])
        for flowbox in flowboxes:
            selected = flowbox.get_selected_children()
            if not selected:
                continue
            idx = selected[0].get_index()
            pkg_id, name, desc = flowbox._local_entries[idx]
            self.confirm_install(key, pkg_id, name, desc)
            return

    def on_child_activated(self, flowbox, child, key):
        """Uses the exact (flowbox, child) the signal itself already
        gives us, not a re-scan through every flowbox for "whichever
        one still has something selected" - real, confirmed-live bug
        this fixes: each category is its own separate Gtk.FlowBox, each
        with its own independent SelectionMode.SINGLE, so nothing ever
        cleared a category's selection when a different category's tile
        got clicked. select_first_visible() leaves the very first tile
        of the very first category selected on load - clicking any tile
        in a later category confirmed live left BOTH tiles visibly
        highlighted at once, and activating the second one actually
        opened the install-confirm dialog for the FIRST (stale-selected)
        one instead, since launch_selected()'s old scan just took
        whichever flowbox it reached first with anything selected. Now
        fixed at the source too (see on_selection_changed()) so
        launch_selected() itself - still needed for the Enter-key path,
        which has no specific child to hand it - is reliably correct
        again as well."""
        idx = child.get_index()
        pkg_id, name, desc = flowbox._local_entries[idx]
        self.confirm_install(key, pkg_id, name, desc)

    def on_selection_changed(self, changed_flowbox, key):
        """Enforces one selected tile across the WHOLE page, not one
        per category - every category flowbox plus the overflow one are
        separate Gtk.FlowBox widgets, each independently
        SelectionMode.SINGLE, so GTK itself only ever guarantees
        single-selection within each of them individually. Only acts
        when the changed box just GAINED a selection - unselecting a
        sibling here fires this same signal on it too (going from
        selected to empty), so returning early whenever the box that
        just changed has nothing selected is what stops that from
        recursing forever, not a depth guard."""
        if not changed_flowbox.get_selected_children():
            return
        flowboxes = [fb for _h, fb in self.category_flowboxes.get(key, [])]
        overflow_pair = self.overflow_flowboxes.get(key)
        if overflow_pair is not None:
            flowboxes.append(overflow_pair[1])
        for flowbox in flowboxes:
            if flowbox is not changed_flowbox and flowbox.get_selected_children():
                flowbox.unselect_all()

    def on_key_press(self, _widget, event):
        keyval = event.keyval
        if keyval == Gdk.KEY_Escape:
            page = self.stack.get_child_by_name(self.active_source)
            if isinstance(page, GameListPage) and page.go_back():
                return True
            self.destroy()
            return True
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            self.launch_selected()
            return True
        if keyval == Gdk.KEY_BackSpace:
            page = self.stack.get_child_by_name(self.active_source)
            if not self.query and isinstance(page, GameListPage) and page.go_back():
                return True                                   # nothing left to delete: Backspace steps back up
            self.update_query(self.query[:-1])
            return True
        if keyval in (
            Gdk.KEY_Up, Gdk.KEY_Down, Gdk.KEY_Left, Gdk.KEY_Right, Gdk.KEY_Tab, Gdk.KEY_ISO_Left_Tab,
        ):
            return False
        unicode_char = Gdk.keyval_to_unicode(keyval)
        if unicode_char and chr(unicode_char).isprintable():
            self.update_query(self.query + chr(unicode_char))
            return True
        return False


def main():
    provider = Gtk.CssProvider()
    provider.load_from_data(cosmos_display.scale_css(CSS).encode())
    Gtk.StyleContext.add_provider_for_screen(
        Gdk.Screen.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_USER
    )
    win = CosmosShop()
    win.show_all()
    win.present()
    Gtk.main()


if __name__ == "__main__":
    main()
