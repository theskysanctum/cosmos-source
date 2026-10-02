"""Proton builds for CosmOS: find the ones that are installed (Steam's, a compatibilitytools.d folder, or CosmOS's own store), import any
other one (a folder, an archive, or a download of GE-Proton / Proton-CachyOS), and pick one for a game.

The store is ~/.local/share/cosmos/proton/<name>/, one folder per build, so any number of versions sit side by side and a game pack can be pinned to
one. A build is anything with an executable `proton` script and a Wine underneath (`files/` or `dist/`) - Valve's, GE's, CachyOS's, an old one.

Nothing here needs root. Archives are unpacked with a safe extractor (no absolute paths, no `..`, no links pointing outside, no device files),
and a download is checked against the release's .sha512sum before it is unpacked. Network access goes through an injectable `fetch`, so the
tests run offline.
"""
import hashlib
import json
import os
import re
import shutil
import tarfile
import tempfile
import urllib.request
from dataclasses import dataclass

HOME = os.path.expanduser("~")
STORE = os.path.join(HOME, ".local/share/cosmos/proton")
CACHE = os.path.join(HOME, ".cache/cosmos/proton-downloads")
USER_AGENT = "cosmos-winpack (+https://github.com/theskysanctum/cosmos)"
# where downloadable builds come from: label -> GitHub "owner/repo"
SOURCES = {"GE-Proton": "GloriousEggroll/proton-ge-custom", "Proton-CachyOS": "CachyOS/proton-cachyos"}
ARCH = "x86_64"
NAME_OK = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+ ()-]{0,80}$")
MAX_ARCHIVE_BYTES = 6 * 1024 ** 3


class ProtonError(Exception):
    """Something the person can be told in one sentence."""


@dataclass
class Build:
    name: str
    path: str
    source: str            # "cosmos" | "steam" | "tool"

    @property
    def script(self):
        return os.path.join(self.path, "proton")

    @property
    def sort_key(self):
        return version_key(self.name)


def is_build(path):
    """A Proton build: an executable `proton` script and a Wine inside (`files/bin/wine*` or `dist/bin/wine*`)."""
    script = os.path.join(path, "proton")
    if not (os.path.isfile(script) and os.access(script, os.X_OK)):
        return False
    return any(os.path.isdir(os.path.join(path, sub, "bin")) for sub in ("files", "dist"))


# ---- version ordering ---------------------------------------------------------------------------------------------------------------

def version_key(name):
    """A sortable key: newer versions of the same family sort higher; GE-Proton and CachyOS (newer projects, versions in the name) beat
    Valve's numbering only by their own numbers, so within a family it's by number. 'Proton - Experimental' / 'Proton Hotfix' sort above
    the numbered Valve builds. Unparseable names sort lowest."""
    text = name.lower()
    numbers = tuple(int(n) for n in re.findall(r"\d+", text)[:6])
    family = ("ge" if text.startswith("ge-proton") else "cachyos" if "cachyos" in text else "experimental" if "experimental" in text
              else "valve" if text.startswith("proton") else "other")
    return (family, numbers + (0,) * (6 - len(numbers)), text)


def newest(builds, family=None):
    pool = [b for b in builds if family is None or b.sort_key[0] == family]
    return max(pool, key=lambda b: b.sort_key[1:]) if pool else None


# ---- what is installed --------------------------------------------------------------------------------------------------------------

def steam_roots(home=HOME):
    """Steam installs to look in, the plain ones and the Flatpak's."""
    flatpak = os.path.join(home, ".var/app/com.valvesoftware.Steam")
    roots = []
    for base in (home, flatpak):
        for sub in (".steam/steam", ".local/share/Steam", ".steam/root"):
            path = os.path.join(base, sub)
            if os.path.isdir(path) and os.path.realpath(path) not in [os.path.realpath(r) for r in roots]:
                roots.append(path)
    return roots


def discover(store=STORE, roots=None):
    """Every Proton build found, one per name (the store wins over Steam's copies of the same name), best-first is up to the caller."""
    found = {}
    for source, folder in _candidate_folders(store, roots if roots is not None else steam_roots()):
        try:
            names = sorted(os.listdir(folder))
        except OSError:
            continue
        for name in names:
            path = os.path.join(folder, name)
            if os.path.isdir(path) and is_build(path) and name not in found:
                found[name] = Build(name, path, source)
    return sorted(found.values(), key=lambda b: b.sort_key, reverse=True)


def _candidate_folders(store, roots):
    yield "cosmos", store
    for root in roots:
        yield "tool", os.path.join(root, "compatibilitytools.d")
        yield "steam", os.path.join(root, "steamapps/common")
    yield "tool", os.path.join(HOME, ".steam/root/compatibilitytools.d")


def find(name, builds):
    return next((b for b in builds if b.name == name), None)


# ---- importing ----------------------------------------------------------------------------------------------------------------------

def _clean_name(name):
    name = (name or "").strip()
    if not NAME_OK.match(name) or name in (".", "..") or "/" in name:
        raise ProtonError(f"'{name}' isn't a usable name for a Proton build.")
    return name


def import_folder(src, store=STORE, name=None, replace=False):
    """Copy a Proton build folder into the store. Returns the Build."""
    src = os.path.abspath(src)
    if not is_build(src):
        raise ProtonError(f"{src} isn't a Proton build (it needs a 'proton' script and a files/ or dist/ folder).")
    name = _clean_name(name or os.path.basename(src))
    dest = os.path.join(store, name)
    if os.path.exists(dest):
        if not replace:
            raise ProtonError(f"{name} is already imported.")
        shutil.rmtree(dest)
    os.makedirs(store, exist_ok=True)
    tmp = tempfile.mkdtemp(prefix=".import-", dir=store)
    try:
        shutil.copytree(src, os.path.join(tmp, name), symlinks=True)
        os.replace(os.path.join(tmp, name), dest)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return Build(name, dest, "cosmos")


def _safe_members(tar, dest):
    """The members of `tar` that are safe to extract under `dest`; raises ProtonError for anything that isn't."""
    root = os.path.realpath(dest)
    total = 0
    for member in tar:
        target = os.path.realpath(os.path.join(root, member.name))
        if os.path.isabs(member.name) or not (target == root or target.startswith(root + os.sep)):
            raise ProtonError(f"The archive has a path outside its folder ({member.name}) - not unpacked.")
        if member.isdev() or member.isfifo():
            raise ProtonError(f"The archive has a device file ({member.name}) - not unpacked.")
        if member.issym() or member.islnk():
            link = member.linkname if member.islnk() else os.path.join(os.path.dirname(member.name), member.linkname)
            resolved = os.path.realpath(os.path.join(root, link)) if not os.path.isabs(member.linkname) else None
            if resolved is None or not (resolved == root or resolved.startswith(root + os.sep)):
                raise ProtonError(f"The archive has a link that points outside itself ({member.name}) - not unpacked.")
        total += max(member.size, 0)
        if total > MAX_ARCHIVE_BYTES:
            raise ProtonError("The archive unpacks to more than 6 GB - not unpacked.")
        yield member


def import_archive(path, store=STORE, name=None, replace=False):
    """Unpack a .tar.gz/.tar.xz/.tar.zst/.tar.bz2 of a Proton build into the store. Returns the Build."""
    try:
        tar = tarfile.open(path, "r:*")
    except (tarfile.TarError, OSError, ValueError) as exc:
        raise ProtonError(f"Couldn't open {os.path.basename(path)} as an archive ({exc}).")
    os.makedirs(store, exist_ok=True)
    tmp = tempfile.mkdtemp(prefix=".unpack-", dir=store)
    try:
        with tar:
            members = list(_safe_members(tar, tmp))
            tar.extractall(tmp, members=members, filter="tar")
        tops = [d for d in os.listdir(tmp) if os.path.isdir(os.path.join(tmp, d))]
        source = os.path.join(tmp, tops[0]) if len(tops) == 1 and is_build(os.path.join(tmp, tops[0])) else tmp if is_build(tmp) else None
        if source is None:
            raise ProtonError("That archive doesn't contain a Proton build (no 'proton' script with a files/ or dist/ folder).")
        final = _clean_name(name or (os.path.basename(source) if source != tmp else re.sub(r"\.tar(\.\w+)?$", "", os.path.basename(path))))
        dest = os.path.join(store, final)
        if os.path.exists(dest):
            if not replace:
                raise ProtonError(f"{final} is already imported.")
            shutil.rmtree(dest)
        os.replace(source, dest)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return Build(final, dest, "cosmos")


def remove(name, store=STORE):
    """Delete a build from the store (never one that lives in Steam's folders)."""
    name = _clean_name(name)
    path = os.path.join(store, name)
    if not os.path.isdir(path):
        raise ProtonError(f"{name} isn't in the CosmOS store.")
    shutil.rmtree(path)


# ---- downloads ----------------------------------------------------------------------------------------------------------------------

def http_get(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def list_releases(source, fetch=http_get, limit=15):
    """[{"name", "url", "size", "sha512_url"}] newest first, for one of SOURCES (a key, or an "owner/repo")."""
    repo = SOURCES.get(source, source)
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        raise ProtonError("That isn't a GitHub project name.")
    try:
        data = json.loads(fetch(f"https://api.github.com/repos/{repo}/releases?per_page={limit}"))
    except (OSError, ValueError) as exc:
        raise ProtonError(f"Couldn't get the list of releases ({exc}).")
    out = []
    for release in data if isinstance(data, list) else []:
        assets = {a.get("name", ""): a for a in release.get("assets", []) if isinstance(a, dict)}
        archive = next((a for n, a in assets.items() if re.search(rf"(^|[-_]){ARCH}\.tar\.(gz|xz|zst)$", n)), None)
        if archive is None:
            archive = next((a for n, a in assets.items() if re.search(r"\.tar\.(gz|xz|zst)$", n) and "arm64" not in n and "aarch64" not in n
                            and "_v3" not in n and "_v4" not in n), None)
        if archive is None:
            continue
        stem = re.sub(r"\.tar\.(gz|xz|zst)$", "", archive["name"])
        checksum = assets.get(stem + ".sha512sum") or assets.get(archive["name"] + ".sha512sum")
        out.append({"name": release.get("tag_name") or stem, "url": archive.get("browser_download_url", ""), "size": archive.get("size", 0),
                    "sha512_url": (checksum or {}).get("browser_download_url", ""), "archive": archive["name"]})
    return out


def http_download(url, dest, timeout=600, chunk=1 << 20):
    """Stream a URL to a file; returns its SHA-512 (hex)."""
    digest = hashlib.sha512()
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp, open(dest, "wb") as out:
        while True:
            block = resp.read(chunk)
            if not block:
                break
            digest.update(block)
            out.write(block)
    return digest.hexdigest()


def download_release(release, store=STORE, fetch=http_get, fetch_file=http_download, cache=CACHE, progress=None):
    """Download, check and import one entry of list_releases(). Returns the Build. `progress(text)` gets short status lines. The archive
    is streamed to disk (they are over half a gigabyte) and its SHA-512 is compared with the release's .sha512sum before anything is unpacked."""
    say = progress or (lambda text: None)
    url = release.get("url", "")
    if not (url.startswith("https://github.com/") or url.startswith("https://objects.githubusercontent.com/")):
        raise ProtonError("Downloads only come from GitHub releases.")
    os.makedirs(cache, exist_ok=True)
    archive = os.path.join(cache, re.sub(r"[^A-Za-z0-9._-]", "_", release.get("archive") or os.path.basename(url)))
    say(f"Downloading {release['name']} ...")
    try:
        actual = fetch_file(url, archive)
    except OSError as exc:
        raise ProtonError(f"The download failed ({exc}).")
    try:
        sha_url = release.get("sha512_url")
        if sha_url:
            say("Checking the download ...")
            try:
                expected = fetch(sha_url).decode("utf-8", "replace").split()[0].lower()
            except (OSError, IndexError):
                raise ProtonError("Couldn't get the checksum to check the download against - not unpacked.")
            if actual != expected:
                raise ProtonError("The download doesn't match its checksum - not unpacked.")
        else:
            say("(this release has no checksum to check it against)")
        say("Unpacking ...")
        return import_archive(archive, store, name=release["name"], replace=True)
    finally:
        try:
            os.unlink(archive)
        except OSError:
            pass
