#!/usr/bin/env python3
"""Optional feature modules: code that is not in the image and is fetched on demand from one shared GitHub repo (MODULES_REPO). Each module is a
top-level folder there with a module.json; installing one adds that folder to a sparse checkout, so no other module's files are downloaded, and
uninstalling removes it again. Nothing outside MODULES_ROOT can ever be touched. Today the only module is `gamegrubber` (see cosmos_gamegrubber.py);
the Expansion Pack "game-downloads" is how it is installed from the Shop.

Needs `gh` signed in to an account that can read the repo (it is private). Every function here logs its output through `log` (a callable taking one
line, default print) and returns True/False; none of them raises for the everyday failures (no network, no gh login, repo missing).

  cosmos_modules.py install MODULE     fetch the module (clones the repo the first time)
  cosmos_modules.py uninstall MODULE
  cosmos_modules.py status [MODULE]    exit 0 when installed (prints what is installed)
  cosmos_modules.py available          module names in the repo
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

MODULES_REPO = "theskysanctum/CosmOS-modules"
MANIFEST = "module.json"
MODULE_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")


def modules_root():
    """Where the sparse clone lives. /userdata is the data partition on the image (the old GameBox location, so existing installs are found)."""
    override = os.environ.get("COSMOS_MODULES_ROOT")
    if override:
        return Path(override)
    if os.path.isdir("/userdata"):
        return Path("/userdata/modules_repo")
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "cosmos" / "modules_repo"


def _run(cmd, log=print):
    """Runs cmd, sending each output line to log as it appears. True on exit status 0; False if it can't be started."""
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
    except OSError as exc:
        log(f"{cmd[0]}: {exc}")
        return False
    for line in proc.stdout:
        log(line.rstrip())
    return proc.wait() == 0


def _git(root, *args):
    return ["git", "-C", str(root), *args]


def ensure_repo(log=print, run=_run):
    """The sparse clone, made on first use (nothing is checked out until a module is added)."""
    root = modules_root()
    if (root / ".git").is_dir():
        return True
    root.parent.mkdir(parents=True, exist_ok=True)
    if not run(["gh", "repo", "clone", MODULES_REPO, str(root), "--", "--filter=blob:none", "--no-checkout", "--sparse"], log):
        return False
    if not run(_git(root, "sparse-checkout", "init", "--cone"), log):
        return False
    branch = subprocess.run(_git(root, "symbolic-ref", "--short", "HEAD"), capture_output=True, text=True).stdout.strip() or "main"
    return run(_git(root, "checkout", branch), log)


def list_installed():
    """{module id: manifest} for every module that is checked out (a folder without a readable module.json is ignored)."""
    root, found = modules_root(), {}
    if not root.is_dir():
        return found
    for entry in root.iterdir():
        manifest = entry / MANIFEST
        if entry.is_dir() and entry.name != ".git" and manifest.is_file():
            try:
                found[entry.name] = json.loads(manifest.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
    return found


def is_installed(module):
    return module in list_installed()


def list_available(run=subprocess.run):
    try:
        done = run(["gh", "api", f"repos/{MODULES_REPO}/contents", "--jq", '.[] | select(.type=="dir") | .name'], capture_output=True, text=True)
    except OSError:
        return []
    return [n for n in done.stdout.splitlines() if MODULE_RE.match(n)] if done.returncode == 0 else []


def install(module, log=print, run=_run):
    if not MODULE_RE.match(module or ""):
        log(f"'{module}' isn't a module name.")
        return False
    if not ensure_repo(log, run):
        log("Couldn't get the modules repository. Is `gh` signed in (gh auth login)?")
        return False
    root = modules_root()
    if not run(_git(root, "pull"), log) or not run(_git(root, "sparse-checkout", "add", module), log):
        return False
    requirements = root / module / "requirements.txt"
    if requirements.is_file() and not run([sys.executable, "-m", "pip", "install", "--user", "-r", str(requirements)], log):
        return False
    return is_installed(module)


def uninstall(module, log=print, run=_run):
    if not MODULE_RE.match(module or "") or not is_installed(module):
        log(f"{module} isn't installed.")
        return False
    root = modules_root()
    listed = subprocess.run(_git(root, "sparse-checkout", "list"), capture_output=True, text=True).stdout.split()
    return run(_git(root, "sparse-checkout", "set", *[m for m in listed if m != module]), log)


def main(argv):
    if len(argv) == 2 and argv[0] == "install":
        return 0 if install(argv[1]) else 1
    if len(argv) == 2 and argv[0] == "uninstall":
        return 0 if uninstall(argv[1]) else 1
    if argv and argv[0] == "status":
        installed = list_installed()
        for name, manifest in sorted(installed.items()):
            print(name, "-", manifest.get("label", name))
        return 0 if (installed if len(argv) == 1 else argv[1] in installed) else 1
    if argv == ["available"]:
        print("\n".join(list_available()))
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
