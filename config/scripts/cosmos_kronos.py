"""The client for kronos, the admin service (docs/kronos-design.md). Everything on CosmOS that needs root asks through here: the Shop,
Updates, Remote Access, the firewall doors, GameBox, and `kronos` on the command line.

  result = ask("pkg.install", {"packages": ["htop"]}, on_output=print)
  result["status"]   ok | failed | refused | needs_passphrase | needs_setup | wrong_passphrase | locked | damaged | unavailable
  result["exit"]     the command's exit status (ok/failed), "output" its text, "reason" why not when refused

When kronos wants the admin passphrase (only for the few powerful actions) the DAEMON asks the person through the desktop session's prompt
agent - so the passphrase never passes through the program that asked - and `ask` just waits. Only over SSH, where there is no agent, does
`ask` read it from the terminal itself. A correct one opens an admin session for that process so a burst of work asks once.
Fail closed: if the service isn't there the answer is status "unavailable" and nothing was done.
"""
import getpass
import json
import os
import re
import shutil
import socket
import subprocess
import sys

SOCKET = "/run/kronos/kronos.sock"
PROMPT = "kronos-prompt"
MAX_TRIES = 3
PKG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,127}$")


def socket_path():
    return os.environ.get("KRONOS_SOCKET", SOCKET)


def call(request, on_output=None, path=None, timeout=None):
    """Send one request; stream output to on_output(text); return the final response dict. Never raises: no service is a response."""
    path = path or socket_path()
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            sock.connect(path)
            sock.sendall((json.dumps(request) + "\n").encode("utf-8"))
            sock.shutdown(socket.SHUT_WR)
            with sock.makefile("r", encoding="utf-8") as fh:
                final = None
                for line in fh:
                    try:
                        msg = json.loads(line)
                    except ValueError:
                        continue
                    if msg.get("event") == "output":
                        if on_output:
                            on_output(msg.get("text", ""))
                    else:
                        final = msg
    except (FileNotFoundError, ConnectionRefusedError, PermissionError):
        return {"status": "unavailable", "reason": "The admin service isn't available, so nothing was done."}
    except OSError as exc:
        return {"status": "unavailable", "reason": f"Couldn't reach the admin service ({exc.strerror or exc})."}
    return final or {"status": "unavailable", "reason": "The admin service closed the connection without answering."}


def tty_prompt(mode, text, error=""):
    """The passphrase typed in the terminal (over SSH), or None. On the desktop nobody asks here: the kronos daemon asks through the
    session's prompt agent, so the passphrase never passes through this program."""
    if not sys.stdin.isatty():
        return None
    try:
        print(text, file=sys.stderr)
        if error:
            print(error, file=sys.stderr)
        first = getpass.getpass("Admin passphrase: " if mode == "unlock" else "Choose an admin passphrase: ")
        if mode == "create" and getpass.getpass("Again: ") != first:
            print("They didn't match.", file=sys.stderr)
            return None
        return first
    except (EOFError, KeyboardInterrupt):
        return None


def default_prompt(mode, text, error=""):
    """The window (or the terminal) - for programs whose whole job is the passphrase itself: the Admin activity app and
    `kronos passphrase`. Everything else lets the daemon do the asking."""
    have_display = bool(os.environ.get("WAYLAND_DISPLAY") or os.environ.get("DISPLAY"))
    if have_display and shutil.which(PROMPT):
        argv = [PROMPT, "--mode", mode, "--text", text] + (["--error", error] if error else [])
        done = subprocess.run(argv, capture_output=True, text=True)
        return done.stdout.rstrip("\n") if done.returncode == 0 else None
    return tty_prompt(mode, text, error)


def ask(action, args=None, on_output=None, prompt=None, path=None, request_id="", timeout=None):
    """Ask kronos to do an action, handling the passphrase conversation. `prompt(mode, text, error)` may be given (tests, GUIs)."""
    prompt = prompt or tty_prompt
    body = {"op": "request", "id": request_id, "action": action, "args": args or {}}
    error = ""
    for _ in range(MAX_TRIES + 1):
        result = call(body, on_output, path, timeout)
        status = result.get("status")
        if status == "needs_setup":
            new = prompt("create", (result.get("description") or action) + "\n\nThis needs an admin passphrase, and you haven't chosen one yet.", error)
            if new is None:
                return {"status": "refused", "reason": "Cancelled - no passphrase was chosen."}
            made = call({"op": "set_passphrase", "new": new}, path=path)
            if made.get("status") != "ok":
                error = made.get("reason", "That passphrase can't be used.")
                if made.get("status") != "refused":
                    return made
                continue
            error = ""
            body.pop("passphrase", None)
            continue
        if status == "needs_passphrase" or status == "wrong_passphrase":
            error = result.get("reason", "") if status == "wrong_passphrase" else ""
            phrase = prompt("unlock", result.get("description") or action, error)
            if phrase is None:
                return {"status": "refused", "reason": "The admin passphrase wasn't entered, so nothing was done. (On the desktop it is asked by kronos-agent - if no window appeared, that isn't running.)"}
            body["passphrase"] = phrase
            continue
        return result
    return {"status": "refused", "reason": "Too many wrong passphrases."}


# ---- `sudo`-shaped commands to typed actions ---------------------------------------------------------------------------------------

def _plain_names(words):
    return [w for w in words if not w.startswith("-")]


def map_command(argv):
    """(action, args) for an ordinary admin command line when kronos has a typed action for it, else ("run", {"argv": argv}).
    Options that only change how a tool behaves (-y, --quiet ...) are dropped; anything with a path or URL argument is left as `run`."""
    if not argv:
        return "run", {"argv": argv}
    prog, rest = os.path.basename(argv[0]), argv[1:]
    words = _plain_names(rest)
    sub, names = (words[0], words[1:]) if words else ("", [])
    if prog in ("dnf", "dnf5", "yum", "rpm-ostree"):
        installing = sub == "install" if prog == "rpm-ostree" else sub in ("install", "in", "add")
        if installing and names and all(PKG.match(n) for n in names):
            return "pkg.install", {"packages": names}
        if sub in ("remove", "erase", "rm", "uninstall") and names and all(PKG.match(n) for n in names):
            return "pkg.remove", {"packages": names}
        if sub in ("upgrade", "update", "up", "distro-sync", "dsync", "upgrade-minimal") and not names:
            return "pkg.upgrade", {}
    elif prog == "flatpak":
        if sub == "install" and len(names) in (1, 2) and re.fullmatch(r"[A-Za-z0-9_.-]+", names[-1]) and "--user" not in rest:
            return "flatpak.install", {"id": names[-1]}
        if sub == "update" and len(names) <= 1 and "--user" not in rest:
            return "flatpak.update", ({"id": names[0]} if names else {})
        if sub in ("uninstall", "remove") and len(names) == 1 and "--user" not in rest:
            return "flatpak.remove", {"id": names[0]}
    elif prog == "systemctl":
        if sub in ("start", "stop", "restart", "reload", "enable", "disable", "status") and len(names) == 1 and not (set(rest) - set(words) - {"--now", "--no-pager"}):
            if "--now" in rest and sub in ("enable", "disable"):
                return "run", {"argv": argv}         # the two-step form isn't one typed action
            return "service.control", {"unit": names[0], "verb": sub}
    elif prog == "bootc":
        if sub == "upgrade" and not names and not (set(rest) - {"upgrade", "--check", "--apply"}):
            return "os.upgrade", {"method": "bootc", **({"check": True} if "--check" in rest else {}), **({"apply": True} if "--apply" in rest else {})}
        if sub == "rollback" and not names and not (set(rest) - {"rollback", "--apply"}):
            return "os.rollback", ({"apply": True} if "--apply" in rest else {})
        if sub == "switch" and len(names) == 1 and not (set(rest) - set(words)):
            return "os.switch", {"ref": names[0]}
    elif prog == "journalctl":
        # the log of one unit / this boot, newest N lines - the log.read action (the system journal isn't readable by this account)
        args, i, ok = {"lines": 200}, 0, True
        while i < len(rest):
            a = rest[i]
            if a in ("--no-pager", "-e", "-x"):
                pass
            elif a == "-b" or a == "--boot":
                args["boot"] = True
            elif a in ("-u", "--unit") and i + 1 < len(rest):
                i += 1
                args["unit"] = rest[i] if rest[i].endswith((".service", ".socket", ".timer", ".target")) else rest[i] + ".service"
            elif a == "-n" and i + 1 < len(rest) and rest[i + 1].isdigit():
                i += 1
                args["lines"] = min(int(rest[i]), 2000)
            else:
                ok = False
            i += 1
        if ok:
            return "log.read", args
    elif prog == "cat" and len(rest) == 1 and rest[0].startswith("/"):
        return "file.read", {"path": rest[0]}
    return "run", {"argv": list(argv)}


def run_command(argv, on_output=None, prompt=None, path=None):
    """Do an admin command line the way `sudo` would have: a typed action where one fits, else the passphrase-gated `run`."""
    action, args = map_command(list(argv))
    return ask(action, args, on_output=on_output, prompt=prompt, path=path)


def exit_code(result):
    """A shell-style exit status for a result: the command's own, 126 refused, 125 service unavailable."""
    status = result.get("status")
    if status in ("ok", "failed"):
        return int(result.get("exit", 0 if status == "ok" else 1))
    return 125 if status == "unavailable" else 126
