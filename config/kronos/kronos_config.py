"""Where kronos reads its settings from: the policy file and the app-policy check. Shared by the daemon and the root helper so
both decide the same way."""
import json
import os
import sys

POLICY_PATHS = ("/etc/kronos/policy.json", "/usr/share/kronos/policy.json")
CLIENTS_FILE = "/etc/kronos/clients"


def load_policy(paths=POLICY_PATHS):
    """The policy dict from the first file that exists and parses; {} (the built-in defaults) otherwise. A damaged policy file is never
    read as "everything allowed": the defaults are the strict ones, and Context only accepts known keys."""
    for path in paths:
        if path and os.path.isfile(path):
            try:
                with open(path, encoding="utf-8") as fh:
                    data = json.load(fh)
                return data if isinstance(data, dict) else {}
            except (OSError, ValueError):
                return {}
    return {}


def app_check():
    """(kind, name) -> (ok, why), from the one-packaging-per-app policy and the curated catalog that the Shop and cosmos-priv also use."""
    for extra in ("/usr/local/bin", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts")):
        if extra not in sys.path and os.path.isdir(extra):
            sys.path.append(extra)
    import cosmos_app_policy as pol
    import cosmos_catalog
    apps, blocked, catalog = pol.load(), pol.load_blocked(), cosmos_catalog.load()
    # the catalog is handed in explicitly (the policy data above is too, and a check given its own data skips the catalog unless it is passed)
    def check(kind, name):
        return pol.check(kind, name, apps, blocked, None, catalog)

    # `run` asks whether a program is a blocked one (a terminal emulator); it is not an install, so the catalog does not apply to it
    check.programs = lambda kind, name: pol.check(kind, name, apps, blocked, None, False)
    return check


def load_clients(path=CLIENTS_FILE):
    """uids allowed to talk to kronos: one user name or number per line in /etc/kronos/clients ('#' comments)."""
    import pwd
    uids = set()
    try:
        with open(path, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
    except OSError:
        return uids
    for line in lines:
        word = line.split("#", 1)[0].strip()
        if not word:
            continue
        if word.isdigit():
            uids.add(int(word))
        else:
            try:
                uids.add(pwd.getpwnam(word).pw_uid)
            except KeyError:
                pass
    return uids
