"""Letting other computers on your network open the Webtop, behind a password.

By default the Webtop listens on this machine only (127.0.0.1:3000, config/webtop/cosmos-webtop.container). "Activate" does two things
together, so it can never be open without a password:

  * writes ~/.config/cosmos-webtop/auth.env (mode 0600) with CUSTOM_USER / PASSWORD - the Webtop container's own login, checked on
    both its web ports (verified against the real container: 401 without or with a wrong password, 200 with the right one);
  * writes ~/.config/containers/systemd/cosmos-webtop.container, a copy of the shipped one that also publishes the HTTPS port
    (3001) on the network and reads that env file. A file in the user's own directory wins over the one in /etc, so removing it
    puts everything back exactly as shipped.

The password never leaves this account's files and is never in the repository. It has to be stored readable (the container needs
it as-is), which is why the file is 0600 and why this is only offered together with HTTPS: plain HTTP on the network would send it
in the clear. The certificate is self-signed, so the browser warns the first time.
"""
import os
import re
import subprocess

HTTP_PORT, HTTPS_PORT = 3000, 3001
UNIT = "cosmos-webtop.service"
MIN_LENGTH = 10
COMMON = {"password12", "1234567890", "qwertyuiop", "password123", "letmein123", "administrator", "webtop1234", "cosmos1234"}
LOCAL_PUBLISH = f"PublishPort=127.0.0.1:{HTTP_PORT}:{HTTP_PORT}"
BASE_PATHS = ("/etc/containers/systemd/users/cosmos-webtop.container",
              os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "webtop", "cosmos-webtop.container"))


class AccessError(ValueError):
    """Something that can't be done; str() is fit to show."""


def paths(home=None):
    home = home or os.path.expanduser("~")
    return {"auth": os.path.join(home, ".config", "cosmos-webtop", "auth.env"),
            "override": os.path.join(home, ".config", "containers", "systemd", "cosmos-webtop.container")}


def validate_password(password):
    if not isinstance(password, str) or len(password) < MIN_LENGTH:
        raise AccessError(f"Use at least {MIN_LENGTH} characters - this password protects a desktop that can run anything.")
    if len(password) > 128:
        raise AccessError("That password is too long (128 characters at most).")
    if any(ch in password for ch in "\r\n\0"):
        raise AccessError("The password can't contain line breaks.")
    if len(set(password)) < 5 or password.lower() in COMMON:
        raise AccessError("That one is too easy to guess.")
    return password


def lan_quadlet(base_text, auth_ref="%h/.config/cosmos-webtop/auth.env"):
    """The shipped quadlet with the HTTPS port published to the network and the login file added."""
    if LOCAL_PUBLISH not in base_text or "\nImage=" not in base_text:
        raise AccessError("The shipped Webtop definition isn't in the expected form.")
    text = base_text.replace(LOCAL_PUBLISH, f"{LOCAL_PUBLISH}\nPublishPort=0.0.0.0:{HTTPS_PORT}:{HTTPS_PORT}", 1)
    return re.sub(r"(?m)^(Image=.*)$", lambda m: f"{m.group(1)}\nEnvironmentFile={auth_ref}", text, count=1)


def read_base(candidates=BASE_PATHS):
    for path in candidates:
        try:
            with open(path, encoding="utf-8") as fh:
                return fh.read()
        except OSError:
            continue
    raise AccessError("Couldn't find the Webtop definition.")


def _write(path, text, mode):
    os.makedirs(os.path.dirname(path), mode=0o755 if mode != 0o600 else 0o700, exist_ok=True)
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.chmod(tmp, mode)
    os.replace(tmp, path)


def activate(password, home=None, user="user", base_text=None):
    """Set the password and open the HTTPS port to the network. Both files are written or neither is used."""
    validate_password(password)
    p = paths(home)
    override = lan_quadlet(base_text if base_text is not None else read_base())
    _write(p["auth"], f"CUSTOM_USER={user}\nPASSWORD={password}\n", 0o600)
    _write(p["override"], override, 0o644)
    return p


def deactivate(home=None):
    """Back to this-machine-only: both files are removed (the password is forgotten)."""
    p = paths(home)
    removed = False
    for path in (p["override"], p["auth"]):
        try:
            os.unlink(path)
            removed = True
        except FileNotFoundError:
            pass
    return removed


def state(home=None):
    """{"network": bool, "password_set": bool} - network is on only when the override AND the login file are both there."""
    p = paths(home)
    have_override, have_auth = os.path.exists(p["override"]), os.path.exists(p["auth"])
    return {"network": have_override and have_auth, "password_set": have_auth}


def apply_systemd(run=subprocess.run):
    """Make systemd re-read the definition and restart the Webtop if it is running. Returns an error message or ""."""
    try:
        run(["systemctl", "--user", "daemon-reload"], capture_output=True, text=True, timeout=30)
        done = run(["systemctl", "--user", "try-restart", UNIT], capture_output=True, text=True, timeout=120)
        return "" if done.returncode == 0 else (done.stderr.strip() or "The Webtop didn't restart.")
    except (OSError, subprocess.SubprocessError) as exc:
        return str(exc)


def network_url(address):
    return f"https://{address}:{HTTPS_PORT}"
