"""Expansion Packs: bundles of apps the Shop installs (and removes) in one go.

The base image is small; things like the developer tools are packs. A pack lists Flatpaks (GUI apps: sandboxed, no
reboot, easy to remove) and RPMs (command-line tools with no Flatpak, layered with rpm-ostree). Packs can include
other packs. The definitions are config/cosmos-apps/expansion-packs.json (installed at /usr/share/cosmos/).

Every member is checked against the app policy first (no terminals, no second copy of an app) - a pack can't be a
way around it. Installing only fetches what is missing, so a partly installed pack is completed, and the same
button re-installs anything that was removed.

  cosmos_packs.py list                       one line per pack with its state
  cosmos_packs.py install PACK               print the shell command that installs what's missing
  cosmos_packs.py remove PACK                print the shell command that removes the pack's members
"""
import json
import os
import re
import shlex
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PACK_PATHS = (os.environ.get("COSMOS_PACKS"), "/usr/share/cosmos/expansion-packs.json",
              os.path.join(HERE, "..", "cosmos-apps", "expansion-packs.json"))
sys.path.insert(0, HERE)
import cosmos_app_policy as policy  # noqa: E402


def load(path=None):
    for candidate in ([path] if path else PACK_PATHS):
        if candidate and os.path.isfile(candidate):
            try:
                with open(candidate, encoding="utf-8") as fh:
                    return [p for p in json.load(fh).get("packs", []) if isinstance(p, dict) and p.get("id")]
            except (OSError, ValueError):
                return []
    return []


def by_id(packs, pack_id):
    return next((p for p in packs if p["id"] == pack_id), None)


def members(packs, pack_id, _seen=None):
    """(flatpak ids, rpm names) of a pack including everything it includes, without duplicates, in a stable order."""
    seen = _seen if _seen is not None else set()
    pack = by_id(packs, pack_id)
    if pack is None or pack_id in seen:
        return [], []
    seen.add(pack_id)
    flatpaks, rpms = list(pack.get("flatpak", [])), list(pack.get("rpm", []))
    for inc in pack.get("includes", []):
        f, r = members(packs, inc, seen)
        flatpaks += f
        rpms += r
    return list(dict.fromkeys(flatpaks)), list(dict.fromkeys(rpms))


def module_of(packs, pack_id):
    """The optional module a pack installs ("module": "gamegrubber" in expansion-packs.json), or None. Such a pack is code fetched by cosmos_modules.py
    rather than Flatpaks and packages; it has no members, and it can't be baked into an image."""
    pack = by_id(packs, pack_id)
    return pack.get("module") if pack else None


def _module_installed(module):
    try:
        import cosmos_modules
        return cosmos_modules.is_installed(module)
    except Exception:
        return False


def _modules_script():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "cosmos_modules.py")


def policy_problems(packs, pack_id, apps=None, blocked=None):
    """Reasons a member may not be installed (empty = fine)."""
    apps = policy.load() if apps is None else apps
    blocked = policy.load_blocked() if blocked is None else blocked
    flatpaks, rpms = members(packs, pack_id)
    problems = []
    for fid in flatpaks:
        ok, why = policy.check("flatpak", fid, apps, blocked)
        if not ok:
            problems.append(f"{fid}: {why}")
    for name in rpms:
        ok, why = policy.check("rpm", name, apps, blocked)
        if not ok:
            problems.append(f"{name}: {why}")
    return problems


def installed(run=subprocess.run):
    """(installed Flatpak ids in either scope, installed or layered RPM names)."""
    flatpaks = set()
    for scope in ("--user", "--system"):
        try:
            out = run(["flatpak", "list", scope, "--app", "--columns=application"], capture_output=True, text=True, timeout=30).stdout
            flatpaks.update(line.strip() for line in out.splitlines() if line.strip())
        except (OSError, subprocess.SubprocessError):
            pass
    rpms = set()
    try:
        out = run(["rpm", "-qa", "--qf", "%{NAME}\n"], capture_output=True, text=True, timeout=60).stdout
        rpms.update(line.strip() for line in out.splitlines() if line.strip())
    except (OSError, subprocess.SubprocessError):
        pass
    try:                                   # layered but waiting for the next boot
        status = json.loads(run(["rpm-ostree", "status", "--json"], capture_output=True, text=True, timeout=30).stdout)
        rpms.update(status["deployments"][0].get("requested-packages", []))
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, IndexError, TypeError):
        pass
    return flatpaks, rpms


def image_based():
    """True on an image-based system (the CosmOS image, where packages are layered with rpm-ostree through kronos); False on an ordinary Fedora, where
    dnf installs them. COSMOS_IMAGE_BASED=1|0 overrides the check (tests, odd setups)."""
    forced = os.environ.get("COSMOS_IMAGE_BASED")
    return forced == "1" if forced is not None else os.path.exists("/run/ostree-booted")


def rpm_install_command(names):
    """The shell command that installs packages (`names` is already shell-quoted, space separated)."""
    if image_based():
        return f"kronos exec -- rpm-ostree install --idempotent {names}"      # kronos applies it live if it can, else stages it
    return f"sudo dnf install -y {names}"                                      # cosmos-task's reply box takes the password


def rpm_remove_command(names):
    return f"kronos exec -- rpm-ostree uninstall {names}" if image_based() else f"sudo dnf remove -y {names}"


def status(packs, pack_id, have=None):
    """{"state": "installed" | "partial" | "none", "have": [...], "missing_flatpak": [...], "missing_rpm": [...]}"""
    module = module_of(packs, pack_id)
    if module:
        here = _module_installed(module)
        return {"state": "installed" if here else "none", "have": [module] if here else [], "missing_flatpak": [], "missing_rpm": [], "module": module}
    flatpaks, rpms = members(packs, pack_id)
    have_flatpaks, have_rpms = installed() if have is None else have
    miss_f = [f for f in flatpaks if f not in have_flatpaks]
    miss_r = [r for r in rpms if r not in have_rpms]
    total = len(flatpaks) + len(rpms)
    missing = len(miss_f) + len(miss_r)
    state = "installed" if total and not missing else "none" if missing == total else "partial"
    return {"state": state, "have": [f for f in flatpaks if f in have_flatpaks] + [r for r in rpms if r in have_rpms],
            "missing_flatpak": miss_f, "missing_rpm": miss_r}


def install_command(packs, pack_id, have=None, apps=None, blocked=None):
    """The shell command that installs what is missing (for a task window). Raises ValueError with a readable
    reason if the pack is unknown or a member isn't allowed."""
    pack = by_id(packs, pack_id)
    if pack is None:
        raise ValueError(f"There is no expansion pack called {pack_id!r}.")
    problems = policy_problems(packs, pack_id, apps, blocked)
    if problems:
        raise ValueError("This pack can't be installed: " + "; ".join(problems))
    st = status(packs, pack_id, have)
    q = shlex.quote
    steps = [f"echo {q('== ' + pack['name'] + ' ==')}"]
    if st.get("module"):
        if st["state"] == "installed":
            return steps[0] + " && echo " + q("Already installed.")
        return " && ".join(steps + [f"{q(sys.executable)} {q(_modules_script())} install {q(st['module'])}", f"echo {q('Done. It appears in the Shop once it is switched on in Settings.')}"])
    if st["missing_flatpak"]:
        steps.append("flatpak install --user -y --noninteractive flathub " + " ".join(q(f) for f in st["missing_flatpak"]))
        steps.append("(find-terminals --purge-flatpaks || true)")
    if st["missing_rpm"]:
        pkgs = " ".join(q(r) for r in st["missing_rpm"])
        steps.append(rpm_install_command(pkgs))
    if len(steps) == 1:
        steps.append(f"echo {q('Everything in this pack is already installed.')}")
    else:
        steps.append(f"echo {q('Done. Anything installed for the next boot needs a restart.' if st['missing_rpm'] else 'Done.')}")
    return " && ".join(steps)


def remove_command(packs, pack_id, have=None):
    pack = by_id(packs, pack_id)
    if pack is None:
        raise ValueError(f"There is no expansion pack called {pack_id!r}.")
    st = status(packs, pack_id, have)
    q = shlex.quote
    if st.get("module"):
        header = f"echo {q('== Removing ' + pack['name'] + ' ==')}"
        if st["state"] != "installed":
            return header + " && echo " + q("Nothing from this pack is installed.")
        return f"{header} && {q(sys.executable)} {q(_modules_script())} uninstall {q(st['module'])}"
    flatpaks, rpms = members(packs, pack_id)
    have_flatpaks, have_rpms = installed() if have is None else have
    steps = [f"echo {q('== Removing ' + pack['name'] + ' ==')}"]
    gone_f = [f for f in flatpaks if f in have_flatpaks]
    gone_r = [r for r in rpms if r in have_rpms]
    if gone_f:
        steps.append("flatpak uninstall --user -y --noninteractive " + " ".join(q(f) for f in gone_f))
    if gone_r:
        steps.append(rpm_remove_command(" ".join(q(r) for r in gone_r)))
    if len(steps) == 1:
        steps.append(f"echo {q('Nothing from this pack is installed.')}")
    return " && ".join(steps)


def info_text(packs, pack_id, have=None):
    pack = by_id(packs, pack_id)
    if pack is None:
        return "Unknown pack."
    if pack.get("module"):
        state = "installed" if _module_installed(pack["module"]) else "not installed"
        return "\n".join([pack["name"], "", pack.get("description", ""), "", f"  [{state}] module {pack['module']}",
                          "  Fetched from the modules repository (needs `gh` signed in to your GitHub account)."])
    flatpaks, rpms = members(packs, pack_id)
    have_f, have_r = installed() if have is None else have
    lines = [pack["name"], "", pack.get("description", ""), ""]
    if pack.get("size"):
        lines += [f"Size: {pack['size']}", ""]
    for f in flatpaks:
        lines.append(("  [installed] " if f in have_f else "  [ ]         ") + f + "  (Flatpak)")
    for r in rpms:
        lines.append(("  [installed] " if r in have_r else "  [ ]         ") + r + "  (package)")
    return "\n".join(lines)


def bake_command(packs, pack_ids, apps=None, blocked=None):
    """The shell command that installs whole packs INTO AN IMAGE while it is being built (system Flatpaks and plain dnf), for the Containerfile's
    `ARG PACKS`. Unknown ids and packs with a member the app policy refuses raise ValueError, so a typo fails the build instead of quietly
    baking in nothing. Empty input means nothing to do."""
    ids = [i for i in re.split(r"[,\s]+", pack_ids or "") if i]
    flatpaks, rpms = [], []
    for pack_id in ids:
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", pack_id) or by_id(packs, pack_id) is None:
            raise ValueError(f"There is no expansion pack called {pack_id!r}.")
        if module_of(packs, pack_id):
            raise ValueError(f"{pack_id} is a module: it is fetched after the system is installed and can't be baked into an image.")
        problems = policy_problems(packs, pack_id, apps, blocked)
        if problems:
            raise ValueError(f"{pack_id} can't be installed: " + "; ".join(problems))
        f, r = members(packs, pack_id)
        flatpaks += [x for x in f if x not in flatpaks]
        rpms += [x for x in r if x not in rpms]
    q = shlex.quote
    steps = []
    if flatpaks:
        steps.append("flatpak install --system -y --noninteractive flathub " + " ".join(q(f) for f in flatpaks))
        steps.append("(find-terminals --purge-flatpaks || true)")
    if rpms:
        steps.append("dnf install -y --setopt=keepcache=1 " + " ".join(q(r) for r in rpms))
    return " && ".join(steps) if steps else "true"


def main(argv):
    packs = load()
    if argv[:1] == ["bake"]:
        try:
            print(bake_command(packs, " ".join(argv[1:])))
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        return 0
    if argv[:1] == ["list"]:
        have = installed()
        for p in packs:
            print(f"{p['id']:24} {status(packs, p['id'], have)['state']:10} {p['name']}")
        return 0
    if len(argv) == 2 and argv[0] in ("install", "remove"):
        try:
            print(install_command(packs, argv[1]) if argv[0] == "install" else remove_command(packs, argv[1]))
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
