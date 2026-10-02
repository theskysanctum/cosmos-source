"""Remote access (SSH) for CosmOS, without a terminal: read/validate/add/remove authorized public keys, show what to connect
to and the host key fingerprints to check the first time. Pure Python (the fingerprint is computed here, not by ssh-keygen),
so it is tested like any library; cosmos-remote is the window on top of it.

Access is by key only (see config/ssh/50-cosmos.conf): a key line is accepted only if it is a plain, well-formed public key -
no leading options (`command="..."`, `from=...`), one line, a known type, a blob whose own type field matches.
"""
import base64
import binascii
import glob
import hashlib
import os
import re
import socket
import subprocess

KEY_TYPES = ("ssh-ed25519", "ssh-rsa", "ecdsa-sha2-nistp256", "ecdsa-sha2-nistp384", "ecdsa-sha2-nistp521",
             "sk-ssh-ed25519@openssh.com", "sk-ecdsa-sha2-nistp256@openssh.com")
MAX_LINE = 8192


class KeyError_(ValueError):
    """A key line that can't be accepted; str() says why, fit to show."""


def parse_key(line):
    """{"type", "blob", "comment"} for a plain public key line, or raises KeyError_."""
    if not isinstance(line, str):
        raise KeyError_("That isn't text.")
    if "PRIVATE KEY" in line:
        raise KeyError_("That is a PRIVATE key. Never paste a private key - use the public one (the .pub file).")
    line = line.strip()
    if not line or "\n" in line or "\r" in line or "\0" in line:
        raise KeyError_("Paste exactly one key, on one line.")
    if len(line) > MAX_LINE:
        raise KeyError_("That line is too long to be a public key.")
    parts = line.split(None, 2)
    if parts[0] not in KEY_TYPES:
        raise KeyError_("That doesn't look like a public key (it should start with ssh-ed25519, ssh-rsa, ecdsa-sha2-... or sk-...). "
                        "Never paste a private key.")
    if len(parts) < 2:
        raise KeyError_("The key data is missing.")
    try:
        blob = base64.b64decode(parts[1], validate=True)
    except (binascii.Error, ValueError):
        raise KeyError_("The key data isn't valid.")
    if len(blob) < 8 or blob[4:4 + int.from_bytes(blob[:4], "big")].decode("ascii", "replace") != parts[0]:
        raise KeyError_("The key data doesn't match its type.")
    return {"type": parts[0], "blob": blob, "comment": parts[2].strip() if len(parts) > 2 else ""}


def fingerprint(blob):
    return "SHA256:" + base64.b64encode(hashlib.sha256(blob).digest()).decode().rstrip("=")


def _lines(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read().splitlines()
    except FileNotFoundError:
        return []


def load_keys(path):
    """[{"type","fingerprint","comment","line"}] for every valid key in an authorized_keys file (others are ignored)."""
    keys = []
    for line in _lines(path):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        try:
            k = parse_key(line)
        except KeyError_:
            continue
        keys.append({"type": k["type"], "fingerprint": fingerprint(k["blob"]), "comment": k["comment"], "line": line.strip()})
    return keys


def _write(path, lines):
    directory = os.path.dirname(path)
    os.makedirs(directory, mode=0o700, exist_ok=True)
    os.chmod(directory, 0o700)
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + ("\n" if lines else ""))
    os.replace(tmp, path)


def add_key(path, line):
    """Add a key; returns (fingerprint, added) - added is False when that key was already there."""
    k = parse_key(line)
    fp = fingerprint(k["blob"])
    if any(existing["fingerprint"] == fp for existing in load_keys(path)):
        return fp, False
    _write(path, _lines(path) + [f"{k['type']} {base64.b64encode(k['blob']).decode()}" + (f" {k['comment']}" if k["comment"] else "")])
    return fp, True


def remove_key(path, fp):
    """Remove the key with this fingerprint; comments and other lines are kept. Returns True if one was removed."""
    kept, removed = [], False
    for line in _lines(path):
        try:
            hit = parse_key(line) if line.strip() and not line.lstrip().startswith("#") else None
        except KeyError_:
            hit = None
        if hit and fingerprint(hit["blob"]) == fp:
            removed = True
            continue
        kept.append(line)
    if removed:
        _write(path, kept)
    return removed


def host_fingerprints(directory="/etc/ssh"):
    """[(type, fingerprint)] of this machine's SSH host keys - what to compare the first time you connect."""
    out = []
    for pub in sorted(glob.glob(os.path.join(directory, "ssh_host_*_key.pub"))):
        try:
            with open(pub, encoding="utf-8") as fh:
                k = parse_key(fh.read().split("\n")[0])
            out.append((k["type"], fingerprint(k["blob"])))
        except (OSError, KeyError_):
            continue
    return out


def addresses():
    """This machine's non-loopback IPv4 addresses."""
    try:
        out = subprocess.run(["hostname", "-I"], capture_output=True, text=True, timeout=5).stdout
        return [a for a in out.split() if re.fullmatch(r"\d+\.\d+\.\d+\.\d+", a) and not a.startswith("127.")]
    except (OSError, subprocess.SubprocessError):
        return []


def connect_hints(user="user", port=3000):
    host = socket.gethostname()
    addrs = addresses()
    first = addrs[0] if addrs else host
    return {"ssh": f"ssh {user}@{first}", "webtop_tunnel": f"ssh -L {port}:localhost:{port} {user}@{first}",
            "hostname": host, "addresses": addrs}


def sshd_state(run=subprocess.run):
    """("active"|"inactive"|"unknown", enabled bool)."""
    try:
        active = run(["systemctl", "is-active", "sshd.service"], capture_output=True, text=True, timeout=10).stdout.strip()
        enabled = run(["systemctl", "is-enabled", "sshd.service"], capture_output=True, text=True, timeout=10).stdout.strip() == "enabled"
    except (OSError, subprocess.SubprocessError):
        return "unknown", False
    return ("active" if active == "active" else "inactive"), enabled


def set_sshd(turn_on, ask=None):
    """(ok, message): start SSH and keep it on at every boot, or stop it and keep it off - two kronos service.control requests (making it
    permanent asks for the admin passphrase)."""
    if ask is None:
        import cosmos_kronos
        ask = cosmos_kronos.ask
    for verb in (("enable", "start") if turn_on else ("stop", "disable")):
        result = ask("service.control", {"unit": "sshd.service", "verb": verb})
        if result.get("status") != "ok":
            return False, result.get("reason") or (result.get("output") or "").strip() or "That didn't work."
    return True, ""