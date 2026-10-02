"""The kronos admin passphrase and admin sessions.

What it is for: the few admin actions that must not happen just because some program asked (see
docs/kronos-design.md, section 5). It is NOT a login and it is never asked at boot, at sign-in or for ordinary
things like installing an app from the Shop. It is asked the first time a sensitive action is requested (that is
also when it is created), and then the answer is remembered for a short admin session, so a burst of admin work
costs one entry.

Storage: one small JSON file (owned by the kronos account, mode 0600) holding a random salt and a PBKDF2-HMAC-SHA256
hash - never the passphrase. Wrong guesses are counted; five in a row lock it for ten minutes (longer with each
repeat), and the lockout survives a restart. A damaged file is never treated as "no passphrase" - that would let
whoever damaged it choose a new one - it stays unusable until the recovery app resets it.

Pure Python, no root needed: the clock and the file path are injectable, so it is tested like any library.
"""
import hashlib
import hmac
import json
import os
import secrets
import time

ITERATIONS = 390_000
MIN_LENGTH = 6
MAX_FAILS = 5
BASE_LOCK_S = 600
SESSION_S = 900
COMMON = {"123456", "1234567", "12345678", "123456789", "password", "passw0rd", "qwerty", "qwerty123", "letmein",
          "admin", "administrator", "kronos", "cosmos", "abc123", "000000", "111111", "iloveyou", "welcome"}


class PassphraseError(Exception):
    """Base for everything below; str() is a message fit to show a person."""


class Weak(PassphraseError):
    pass


class Wrong(PassphraseError):
    pass


class Locked(PassphraseError):
    def __init__(self, seconds):
        super().__init__(f"Too many wrong tries. Try again in {max(1, int(seconds) // 60 + (1 if int(seconds) % 60 else 0))} minute(s).")
        self.seconds = int(seconds)


class Damaged(PassphraseError):
    pass


def check_strength(text):
    if not isinstance(text, str) or len(text) < MIN_LENGTH:
        raise Weak(f"Use at least {MIN_LENGTH} characters.")
    if len(set(text)) < 3:
        raise Weak("Use more than two different characters.")
    if text.lower() in COMMON:
        raise Weak("That one is too easy to guess.")


def _hash(text, salt, iterations):
    return hashlib.pbkdf2_hmac("sha256", text.encode("utf-8"), salt, iterations)


class Passphrase:
    def __init__(self, path, now=time.time, iterations=ITERATIONS):
        self.path = path
        self.state_path = path + ".state"
        self.now = now
        self.iterations = iterations

    # ---- files
    def _read(self, path):
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            return data if isinstance(data, dict) else None
        except FileNotFoundError:
            return {}
        except (OSError, ValueError):
            return None

    def _write(self, path, data):
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        tmp = path + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        os.replace(tmp, path)

    def status(self):
        """"unset", "set" or "damaged"."""
        data = self._read(self.path)
        if data is None:
            return "damaged"
        if not data:
            return "unset"
        try:
            bytes.fromhex(data["salt"])
            bytes.fromhex(data["hash"])
            int(data["iterations"])
        except (KeyError, ValueError, TypeError):
            return "damaged"
        return "set"

    def is_set(self):
        return self.status() == "set"

    # ---- lockout
    def _fail_state(self):
        return self._read(self.state_path) or {"fails": 0, "locked_until": 0, "locks": 0}

    def locked_for(self):
        """Seconds left on a lockout, 0 if none."""
        return max(0, int(self._fail_state().get("locked_until", 0) - self.now()))

    def _record_failure(self):
        st = self._fail_state()
        st["fails"] = st.get("fails", 0) + 1
        if st["fails"] >= MAX_FAILS:
            st["locks"] = st.get("locks", 0) + 1
            st["locked_until"] = self.now() + BASE_LOCK_S * min(st["locks"], 6)
            st["fails"] = 0
        self._write(self.state_path, st)

    def _record_success(self):
        st = self._fail_state()
        st.update(fails=0, locked_until=0, locks=0)
        self._write(self.state_path, st)

    # ---- use
    def verify(self, candidate):
        """True/False; raises Locked while locked out and Damaged if the file can't be trusted."""
        status = self.status()
        if status == "damaged":
            raise Damaged("The admin passphrase file is damaged - use the recovery app to reset it.")
        if status == "unset":
            return False
        remaining = self.locked_for()
        if remaining:
            raise Locked(remaining)
        data = self._read(self.path)
        good = hmac.compare_digest(_hash(candidate or "", bytes.fromhex(data["salt"]), int(data["iterations"])), bytes.fromhex(data["hash"]))
        if good:
            self._record_success()
        else:
            self._record_failure()
        return good

    def set(self, new, old=None):
        """Create the passphrase (when none is set) or change it (needs the old one)."""
        status = self.status()
        if status == "damaged":
            raise Damaged("The admin passphrase file is damaged - use the recovery app to reset it.")
        check_strength(new)
        if status == "set" and not self.verify(old):
            raise Wrong("The current passphrase is wrong.")
        salt = secrets.token_bytes(16)
        self._write(self.path, {"salt": salt.hex(), "hash": _hash(new, salt, self.iterations).hex(),
                                "iterations": self.iterations, "set": int(self.now())})
        self._record_success()

    def reset(self):
        """Forget the passphrase (the recovery app calls this). The next sensitive action asks for a new one."""
        for path in (self.path, self.state_path):
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass


class AdminSessions:
    """Who has unlocked admin recently. Held in the daemon's memory only, so a restart locks everything.
    The key is whatever identifies the asker (uid + program path), so unlocking in one program doesn't unlock another."""

    def __init__(self, now=time.monotonic, lifetime=SESSION_S):
        self.now, self.lifetime = now, lifetime
        self._until = {}

    def grant(self, key):
        self._until[key] = self.now() + self.lifetime

    def valid(self, key):
        until = self._until.get(key)
        if until is None:
            return False
        if self.now() >= until:
            del self._until[key]
            return False
        return True

    def touch(self, key):
        """Use of a session slides it forward, so steady work isn't interrupted; it still ends after `lifetime` idle."""
        if self.valid(key):
            self._until[key] = self.now() + self.lifetime
            return True
        return False

    def revoke(self, key=None):
        if key is None:
            self._until.clear()
        else:
            self._until.pop(key, None)
