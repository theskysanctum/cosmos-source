#!/usr/bin/env python3
"""The curated catalog: the only Flatpaks, packages and AppImages that can be installed on CosmOS.

The list is one file, catalog.json (installed at /usr/share/cosmos/catalog.json; the repo copy is config/cosmos-apps/catalog.json). Each entry has a
name, a category, and a `flatpak` id and/or exact `rpm` package names and/or `appimage` AM/AppMan program ids. This module is the one reader of it, and it is used in the same places as
the app policy (cosmos_app_policy.check() calls it, so they all get it):

  build          `cosmos_catalog.py flatpak-filter` writes the Flathub remote filter as an allowlist: everything hidden except the catalog's apps,
                 their extensions (`ID.*`: locales, plugins) and the runtime families they need. Note that Flatpak's allow rules win over deny rules,
                 so a blocked app is blocked by never being listed here (test_cosmos_catalog.py checks that);
  Kronos         a root install is refused unless its Flatpak id or package name is in the catalog;
  cosmos-priv    the same check, with a friendly message, before dnf/rpm-ostree/flatpak installs in a shell;
  CosmOS Shop    lists only catalog entries and checks before installing.
  (AppImages install as the user, so there is no root or remote to filter: the Shop and the wrappers refuse, and find-uncurated removes leftovers.)

  cosmos_catalog.py check rpm|flatpak|appimage NAME   exit 0 when in the catalog, 1 when not (reason on stderr)
  cosmos_catalog.py flatpak-filter             the Flathub remote filter (allowlist)
  cosmos_catalog.py validate                   offline: the file is there and has no mistakes (the image build runs this)
  cosmos_catalog.py list [flatpak|rpm|appimage]   what is in it
  cosmos_catalog.py verify [--cache DIR]       online: every Flatpak id exists on Flathub, every package in the Fedora / RPM Fusion repositories and
                                               every AppImage in AM's list (run after editing the catalog; downloads the lists into DIR, default
                                               ~/.placements/catalog-cache)
"""
import gzip
import json
import os
import re
import subprocess
import sys

CATALOG_PATHS = (os.environ.get("COSMOS_CATALOG"), "/usr/share/cosmos/catalog.json",
                 os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "cosmos-apps", "catalog.json"))
FLATHUB_APPSTREAM_URL = "https://dl.flathub.org/repo/appstream/x86_64/appstream.xml.gz"
AM_LIST_URL = "https://raw.githubusercontent.com/ivan-hc/AM/main/programs/x86_64-apps"
AM_RE = re.compile(r"^[a-z0-9][a-z0-9._+-]*$")
ID_RE = re.compile(r"^[A-Za-z0-9_-]+(\.[A-Za-z0-9_-]+)+$")
RPM_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]*$")
ARCH_SUFFIX = re.compile(r"\.(x86_64|noarch|i686|aarch64)$")


def load(path=None):
    """The catalog as a dict ({"apps": [...], "flatpak_families": [...]}); empty if there is none."""
    for candidate in ([path] if path else CATALOG_PATHS):
        if candidate and os.path.isfile(candidate):
            try:
                with open(candidate, encoding="utf-8") as fh:
                    data = json.load(fh)
            except (OSError, ValueError):
                return {}
            return data if isinstance(data, dict) else {}
    return {}


def apps(catalog=None):
    catalog = load() if catalog is None else catalog
    return [a for a in catalog.get("apps", []) if isinstance(a, dict)]


def flatpak_ids(catalog=None):
    return sorted({a["flatpak"] for a in apps(catalog) if a.get("flatpak")})


def rpm_names(catalog=None):
    return sorted({r for a in apps(catalog) for r in a.get("rpm", []) if isinstance(r, str)})


def appimage_names(catalog=None):
    return sorted({n for a in apps(catalog) for n in a.get("appimage", []) if isinstance(n, str)})


def families(catalog=None):
    catalog = load() if catalog is None else catalog
    return [f for f in catalog.get("flatpak_families", []) if isinstance(f, str)]


def flatpak_allowed(ref, catalog=None):
    """True for a catalog app, its extensions (ID.Locale, ID.Plugin.x) and the runtime families. `ref` may be a full ref (org.x.Y/x86_64/stable)."""
    ident = (ref or "").strip()
    if ident.startswith(("app/", "runtime/")):
        ident = ident.split("/", 1)[1]
    ident = ident.split("/", 1)[0]
    if not ID_RE.match(ident):
        return False
    for known in flatpak_ids(catalog) + families(catalog):
        if ident == known or ident.startswith(known + "."):
            return True
    return False


def rpm_allowed(name, catalog=None):
    """True for an exact package name in the catalog (a trailing .x86_64/.noarch is ignored)."""
    return ARCH_SUFFIX.sub("", (name or "").strip()) in set(rpm_names(catalog))


def appimage_allowed(name, catalog=None):
    """True for an exact AM/AppMan program id in the catalog (case does not matter: AM ids are lower case)."""
    return (name or "").strip().lower() in set(appimage_names(catalog))


def check(kind, name, catalog=None):
    """(allowed, reason) for kind "rpm", "flatpak" or "appimage"."""
    catalog = load() if catalog is None else catalog
    if not apps(catalog):
        return True, ""                       # no catalog installed: nothing is curated (a dev checkout without the file)
    if kind == "flatpak":
        if flatpak_allowed(name, catalog):
            return True, ""
        return False, (f"{name} isn't in the CosmOS catalog, so it can't be installed. The catalog is the list of apps this system offers "
                       f"(config/cosmos-apps/catalog.json in the image's source).")
    if kind == "rpm":
        if rpm_allowed(name, catalog):
            return True, ""
        plain = ARCH_SUFFIX.sub("", (name or "").strip())
        hint = " Use the plain package name, with no version." if re.search(r"-\d", plain) and rpm_allowed(re.sub(r"-\d.*$", "", plain), catalog) else ""
        return False, f"{name} isn't in the CosmOS catalog, so it can't be installed.{hint}"
    if kind == "appimage":
        if appimage_allowed(name, catalog):
            return True, ""
        return False, f"{name} isn't in the CosmOS catalog, so it can't be installed as an AppImage."
    return True, ""


def flatpak_filter(catalog=None):
    """A Flathub remote filter that hides everything except the catalog. Flatpak's `allow` beats `deny`, so nothing else is needed."""
    catalog = load() if catalog is None else catalog
    lines = ["# Generated from catalog.json (cosmos_catalog.py flatpak-filter): only the curated apps, their extensions and the runtime families they use.",
             "deny *"]
    for ident in flatpak_ids(catalog) + families(catalog):
        lines += [f"allow {ident}", f"allow {ident}.*"]
    return "\n".join(lines) + "\n"


def problems(catalog=None):
    """Mistakes in the file itself (bad ids, duplicates, entries with nothing to install)."""
    catalog = load() if catalog is None else catalog
    found, names, seen_fp, seen_rpm = [], set(), set(), set()
    for entry in apps(catalog):
        label = entry.get("name") or "(unnamed)"
        if not entry.get("name") or not entry.get("category"):
            found.append(f"{label}: needs a name and a category")
        if label in names:
            found.append(f"{label}: listed twice")
        names.add(label)
        fp, rpms, ais = entry.get("flatpak"), entry.get("rpm", []), entry.get("appimage", [])
        if not fp and not rpms and not ais:
            found.append(f"{label}: has no flatpak id, packages or AppImage")
        if ais and (fp or rpms):
            found.append(f"{label}: an app is one packaging only; it has an AppImage as well as a Flatpak or packages")
        for n in ais:
            if not isinstance(n, str) or not AM_RE.match(n):
                found.append(f"{label}: '{n}' is not an AM program id")
        if fp:
            if not ID_RE.match(fp):
                found.append(f"{label}: '{fp}' is not a Flatpak id")
            if fp in seen_fp:
                found.append(f"{label}: Flatpak {fp} is in the catalog twice")
            seen_fp.add(fp)
        for r in rpms:
            if not isinstance(r, str) or not RPM_RE.match(r):
                found.append(f"{label}: '{r}' is not a package name")
            elif r in seen_rpm:
                found.append(f"{label}: package {r} is in the catalog twice")
            seen_rpm.add(r)
    return found


def verify(cache_dir, run=subprocess.run):
    """Online check: (missing_flatpaks, missing_packages, missing_appimages). Downloads Flathub's and AM's lists; asks dnf about the packages."""
    os.makedirs(cache_dir, exist_ok=True)
    path = os.path.join(cache_dir, "appstream.xml.gz")
    if run(["curl", "-sfL", "-m", "120", "-o", path, FLATHUB_APPSTREAM_URL]).returncode != 0:
        raise OSError("couldn't download Flathub's app list")
    with gzip.open(path, "rt", errors="ignore") as fh:
        text = fh.read()
    known = set(re.findall(r"<id>([^<]+)</id>", text))
    known |= {i[:-len(".desktop")] for i in known if i.endswith(".desktop")}
    missing_fp = [i for i in flatpak_ids() if i not in known]
    names = rpm_names()
    done = run(["dnf", "repoquery", "--available", "--latest-limit=1", "--qf", "%{name}\n", *names], capture_output=True, text=True)
    have = set(done.stdout.split())
    am_path = os.path.join(cache_dir, "x86_64-apps")
    if run(["curl", "-sfL", "-m", "120", "-o", am_path, AM_LIST_URL]).returncode != 0:
        raise OSError("couldn't download AM's program list")
    with open(am_path, encoding="utf-8", errors="ignore") as fh:
        am_known = set(re.findall(r"^◆ (\S+) :", fh.read(), re.M))
    return missing_fp, [n for n in names if n not in have], [n for n in appimage_names() if n not in am_known]


def main(argv):
    if len(argv) == 3 and argv[0] == "check" and argv[1] in ("rpm", "flatpak", "appimage"):
        ok, reason = check(argv[1], argv[2])
        if not ok:
            print(reason, file=sys.stderr)
        return 0 if ok else 1
    if argv == ["flatpak-filter"]:
        sys.stdout.write(flatpak_filter())
        return 0
    if argv and argv[0] == "list":
        if len(argv) == 1 or argv[1] == "flatpak":
            print("\n".join(flatpak_ids()))
        if len(argv) == 1 or argv[1] == "rpm":
            print("\n".join(rpm_names()))
        if len(argv) == 1 or argv[1] == "appimage":
            print("\n".join(appimage_names()))
        return 0
    if argv == ["validate"]:                       # the image build runs this: a missing or broken catalog must fail the build, not curate nothing
        bad = problems() if apps() else ["no catalog found"]
        for line in bad:
            print("problem:", line, file=sys.stderr)
        return 1 if bad else 0
    if argv and argv[0] == "verify":
        cache = os.path.expanduser("~/.placements/catalog-cache")
        if "--cache" in argv:
            cache = argv[argv.index("--cache") + 1]
        bad = problems()
        for line in bad:
            print("problem:", line)
        try:
            missing_fp, missing_rpm, missing_ai = verify(cache)
        except OSError as exc:
            print(exc, file=sys.stderr)
            return 2
        for i in missing_fp:
            print("not on Flathub:", i)
        for n in missing_rpm:
            print("not in the Fedora/RPM Fusion repositories:", n)
        for n in missing_ai:
            print("not in AM's list:", n)
        print(f"{len(flatpak_ids())} Flatpaks, {len(rpm_names())} packages and {len(appimage_names())} AppImages checked")
        return 1 if bad or missing_fp or missing_rpm or missing_ai else 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
