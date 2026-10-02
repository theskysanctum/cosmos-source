"""What the CosmOS recovery app can do (docs/kronos-design.md, section 7). It runs as root from the "CosmOS recovery" boot entry, with no
shell and no password - anyone at the machine with the disk in their hands could do all of this anyway - and it is deliberately short:

  reset_kronos_passphrase()   forget the admin passphrase (and any lockout); the next passphrase-gated action asks for a new one.
                              The audit log is NOT touched, and a note about the reset is added to it.
  rollback_os()               boot the previous operating system deployment next time (bootc rollback), then restart
  clear_profile_pin(slug)     remove one profile's PIN
  factory_reset_user_data()   empty the desktop account's home and put the fresh-install files back - only with the typed word RESET

Every path is a parameter so the tests run in a temp folder. Nothing here is offered to normal programs: it is not a kronos action.
"""
import json
import os
import shutil
import subprocess
import time

STATE_DIR = "/var/lib/kronos"
AUDIT_LOG = "/var/log/kronos/audit.log"
HOME = "/home/user"
SKEL = "/etc/skel"
CONFIRM_WORD = "RESET"


class RecoveryError(Exception):
    pass


def reset_kronos_passphrase(state_dir=STATE_DIR, audit_log=AUDIT_LOG, now=time.time):
    """Remove the passphrase file and its lockout state. Returns the files removed."""
    removed = []
    for name in ("passphrase.json", "passphrase.json.state", "passphrase.json.tmp", "passphrase.json.state.tmp"):
        path = os.path.join(state_dir, name)
        if os.path.lexists(path):
            os.unlink(path)
            removed.append(path)
    note_in_audit_log(audit_log, "the admin passphrase was reset from the recovery app", now)
    return removed


def note_in_audit_log(path, reason, now=time.time):
    """Append a chained line so the reset is on record (kronos_audit does the chaining). The log may be append-only: this only appends."""
    try:
        import sys
        for extra in ("/usr/local/lib/kronos", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "kronos")):
            if os.path.isdir(extra) and extra not in sys.path:
                sys.path.append(extra)
        import kronos_audit
        kronos_audit.AuditLog(path, now=now).append("passphrase", decision="passphrase-reset", reason=reason, peer={"uid": 0, "exe": "cosmos-recovery"})
    except Exception:                       # the log is a record, not a gate: the reset itself must still work when it is damaged
        pass


def rollback_os(run=subprocess.run):
    done = run(["bootc", "rollback"], capture_output=True, text=True)
    if done.returncode != 0:
        raise RecoveryError((done.stdout + done.stderr).strip() or "bootc rollback failed")
    return (done.stdout + done.stderr).strip()


def clear_profile_pin(slug, profiles_dir=os.path.join(HOME, ".local/share/cosmos/profiles")):
    if not slug or "/" in slug or slug.startswith("."):
        raise RecoveryError("That isn't a profile name.")
    path = os.path.join(profiles_dir, slug, "profile.json")
    try:
        with open(path, encoding="utf-8") as fh:
            profile = json.load(fh)
    except (OSError, ValueError):
        raise RecoveryError("That profile couldn't be read.")
    profile["pin"] = None
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(profile, fh, indent=2)
    os.replace(tmp, path)


def list_profiles(profiles_dir=os.path.join(HOME, ".local/share/cosmos/profiles")):
    """[(slug, name, has_pin)]"""
    rows = []
    try:
        for slug in sorted(os.listdir(profiles_dir)):
            try:
                with open(os.path.join(profiles_dir, slug, "profile.json"), encoding="utf-8") as fh:
                    p = json.load(fh)
                rows.append((slug, p.get("name", slug), bool(p.get("pin"))))
            except (OSError, ValueError):
                continue
    except OSError:
        pass
    return rows


def factory_reset_user_data(confirm, home=HOME, skel=SKEL, owner=None):
    """Empty `home` and copy `skel` back. `confirm` must be exactly RESET."""
    if confirm != CONFIRM_WORD:
        raise RecoveryError(f"Type {CONFIRM_WORD} to confirm.")
    if not home or os.path.realpath(home) in ("/", "/home", "/root", "/var", "/usr", "/etc") or os.path.islink(home):
        raise RecoveryError("Refusing to reset that folder.")
    for name in os.listdir(home):
        path = os.path.join(home, name)
        if os.path.isdir(path) and not os.path.islink(path):
            shutil.rmtree(path)
        else:
            os.unlink(path)
    if os.path.isdir(skel):
        for name in os.listdir(skel):
            src, dst = os.path.join(skel, name), os.path.join(home, name)
            if os.path.isdir(src) and not os.path.islink(src):
                shutil.copytree(src, dst, symlinks=True)
            else:
                shutil.copy2(src, dst, follow_symlinks=False)
    if owner:
        for root, dirs, files in os.walk(home):
            for name in dirs + files:
                os.lchown(os.path.join(root, name), *owner)
        os.chown(home, *owner)
