#!/usr/bin/env python3
"""One packaging per app: an app that is an RPM in the image cannot also be installed as a Flatpak or
AppImage, and one that is a Flatpak cannot also be installed as an RPM or AppImage - so there is never
a second, conflicting copy with its own settings, menu entry and file associations.

The rules live in app-policy.json (installed at /usr/share/cosmos/app-policy.json). This module is the
one reader of it, and it is used in four places:

  build          `cosmos_app_policy.py dnf-exclude` / `flatpak-filter` write the dnf excludepkgs line and the
                 Flathub remote filter, so dnf and `flatpak install` themselves refuse the wrong copy;
  cosmos-priv    the never-type-sudo wrapper asks `cosmos_app_policy.py check rpm|flatpak NAME` before a
                 dnf/rpm-ostree/flatpak install (exit 1 and the reason on stderr when it is not allowed);
  CosmOS Shop    calls check() before installing from any of its sources;
  tests          test_cosmos_app_policy.py checks the Containerfile agrees with the file.

  cosmos_app_policy.py check rpm|flatpak|appimage NAME   exit 0 allowed, 1 refused (reason on stderr)
  cosmos_app_policy.py check flatpak-scope ID user|system  refused when it is already installed in the other scope
  cosmos_app_policy.py dnf-exclude                        the excludepkgs value (comma separated globs)
  cosmos_app_policy.py flatpak-filter                     a Flathub remote filter file
  cosmos_app_policy.py flatpak-apps                       the Flatpak ids that are the app (use=flatpak), one per line
"""
import fnmatch
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))          # cosmos_catalog.py sits beside this file, installed or not

POLICY_PATHS = (os.environ.get("COSMOS_APP_POLICY"), "/usr/share/cosmos/app-policy.json",
                os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "cosmos-apps", "app-policy.json"))


def _read(path=None):
    for candidate in ([path] if path else POLICY_PATHS):
        if candidate and os.path.isfile(candidate):
            try:
                with open(candidate, encoding="utf-8") as fh:
                    data = json.load(fh)
                return data if isinstance(data, dict) else {}
            except (OSError, ValueError):
                return {}
    return {}


def load(path=None):
    """The per-app entries (use=flatpak|rpm)."""
    return [a for a in _read(path).get("apps", []) if isinstance(a, dict)]


def load_blocked(path=None):
    """Things that may never be installed in any packaging (terminal emulators)."""
    return [b for b in _read(path).get("blocked", []) if isinstance(b, dict)]


def _matches(patterns, name, case_sensitive=False):
    if not case_sensitive:
        name = name.lower()
    return any(fnmatch.fnmatchcase(name, p if case_sensitive else p.lower()) for p in patterns or [])


def load_dnf_only(path=None):
    """Groups that may only be installed as packages (game store clients): their Flatpaks and AppImages are refused."""
    return [b for b in _read(path).get("dnf_only", []) if isinstance(b, dict)]


def check(kind, name, apps=None, blocked=None, dnf_only=None, catalog=None):
    """(allowed, reason). kind is "rpm", "flatpak" or "appimage".

    The catalog (cosmos_catalog.py) is the list of everything that may be installed at all. It applies when the policy is read from its files;
    a caller that hands in its own `apps` is testing the packaging rules alone, so the catalog is skipped unless it passes one (or catalog=False to
    skip it explicitly)."""
    use_catalog = catalog is not False and (catalog is not None or apps is None)
    apps = load() if apps is None else apps
    blocked = load_blocked() if blocked is None else blocked
    dnf_only = load_dnf_only() if dnf_only is None else dnf_only
    name = (name or "").strip()
    for group in dnf_only:
        if kind in ("flatpak", "appimage") and _matches(group.get(kind), name.split("/", 1)[0] if kind == "flatpak" else name,
                                                        case_sensitive=(kind == "flatpak")):
            return False, f"{group.get('name', 'This')} can't be installed that way. {group.get('why', '')}".strip()
    for group in blocked:
        if kind in ("rpm", "flatpak", "appimage") and _matches(group.get(kind), name.split("/", 1)[0] if kind == "flatpak" else name, case_sensitive=(kind == "flatpak")):
            return False, f"{group.get('name', 'This')} can't be installed on CosmOS. {group.get('why', '')}".strip()
    if use_catalog:
        import cosmos_catalog
        ok, reason = cosmos_catalog.check(kind, name, catalog)
        if not ok:
            return False, reason
    for app in apps:
        use = app.get("use")
        if kind == "flatpak":
            hit = name.lower() == (app.get("flatpak") or "").lower() or name.lower().startswith((app.get("flatpak") or "\0").lower() + ".")
            if hit and use == "rpm":
                return False, (f"{app['name']} is installed as a system package here ({', '.join(app.get('rpm', [])[:1])}); "
                               f"a second copy as a Flatpak would conflict with it." + (f" ({app['why']})" if app.get("why") else ""))
        elif kind in ("rpm", "appimage"):
            if _matches(app.get(kind), name) and use == "flatpak":
                return False, (f"{app['name']} is installed as a Flatpak ({app['flatpak']}) on this system; "
                               f"a second copy as {'a package' if kind == 'rpm' else 'an AppImage'} would conflict with it. "
                               f"Use the Flatpak (flatpak run {app['flatpak']}).")
    return True, ""


def check_flatpak_scope(ref, scope, run=subprocess.run):
    """(allowed, reason): a Flatpak may not be installed for `scope` (user|system) while the same app is
    installed in the other one - the user copy would silently shadow the system one, two versions with two
    sets of settings."""
    other = "system" if scope == "user" else "user"
    try:
        done = run(["flatpak", "info", f"--{other}", ref], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return True, ""
    if done.returncode == 0:
        return False, (f"{ref} is already installed {other}-wide; a second copy for the {scope} would shadow it "
                       f"and keep its own settings. Update or remove the existing one instead.")
    return True, ""


def dnf_exclude(apps=None, blocked=None):
    apps = load() if apps is None else apps
    blocked = load_blocked() if blocked is None else blocked
    names = {p for a in apps if a.get("use") == "flatpak" for p in a.get("rpm", [])}
    names |= {p for b in blocked for p in b.get("rpm", [])}
    return ",".join(sorted(names))


def flatpak_filter(apps=None, blocked=None, dnf_only=None):
    curated = apps is None                                  # read from the files (not handed in as test data): the catalog decides
    apps = load() if apps is None else apps
    blocked = load_blocked() if blocked is None else blocked
    dnf_only = load_dnf_only() if dnf_only is None else dnf_only
    import cosmos_catalog
    if curated and cosmos_catalog.apps():
        # a curated system: only what the catalog lists is visible at all (Flatpak's allow beats deny, so blocked apps are simply never listed)
        return cosmos_catalog.flatpak_filter()
    lines = ["# Generated from app-policy.json (cosmos_app_policy.py flatpak-filter): Flatpaks that would",
             "# duplicate an app installed as an RPM here, terminal emulators, blocked programs and game stores (dnf only). Everything else on Flathub is allowed."]
    for app in apps:
        if app.get("use") == "rpm" and app.get("flatpak"):
            lines += [f"deny {app['flatpak']}", f"deny {app['flatpak']}.*"]
    for group in list(blocked) + list(dnf_only):
        for pattern in group.get("flatpak", []):
            lines.append(f"deny {pattern}")
    return "\n".join(lines) + "\n"


def flatpak_apps(apps=None):
    apps = load() if apps is None else apps
    return sorted(a["flatpak"] for a in apps if a.get("use") == "flatpak" and a.get("flatpak"))


def main(argv):
    if len(argv) == 4 and argv[:2] == ["check", "flatpak-scope"]:
        ok, reason = check_flatpak_scope(argv[2], argv[3])
        if not ok:
            print(reason, file=sys.stderr)
        return 0 if ok else 1
    if len(argv) == 3 and argv[0] == "check":
        ok, reason = check(argv[1], argv[2])
        if not ok:
            print(reason, file=sys.stderr)
        return 0 if ok else 1
    if argv == ["dnf-exclude"]:
        print(dnf_exclude())
        return 0
    if argv == ["flatpak-filter"]:
        sys.stdout.write(flatpak_filter())
        return 0
    if argv == ["flatpak-apps"]:
        print("\n".join(flatpak_apps()))
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
