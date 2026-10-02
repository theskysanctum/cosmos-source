"""The kronos audit log: one JSON line per event, hash-chained, append-only, fail-closed.

  {"seq": 41, "ts": 1790000000.1, "prev": "<sha256 of the previous line>", "id": "...", "event": "decision" | "result" | ...,
   "peer": {"uid", "pid", "exe", "cmdline"}, "action": "pkg.install", "args": {...redacted...},
   "decision": "auto|passphrase-ok|passphrase-required|passphrase-wrong|refused", "reason": "...", "exit": 0, "ms": 812}

* Chained: each line carries the SHA-256 of the previous line's exact text, so an edited, deleted or reordered line
  is found by verify(). The chain runs across rotated files (the first line of a new file points at the last line of
  the old one), so `verify` accepts a list of files oldest first.
* Append-only: the file is opened O_APPEND with mode 0640; the helper that installs kronos also marks it
  append-only (`chattr +a`), which is why rotation calls the injectable unprotect/protect hooks.
* Fail-closed: if a line can't be written, AuditError is raised and the caller must not do the thing - an unlogged
  admin action is worse than a failed one.
* Never holds a secret: args are redacted (anything called *pass*, *secret*, *token*, *key*, *auth*; file contents and
  stdin are recorded as length + hash only).
"""
import fcntl
import hashlib
import json
import os
import re
import time

GENESIS = "0" * 64
SECRET_KEY = re.compile(r"pass|secret|token|key|auth|credential", re.I)
BULK_KEYS = ("content", "stdin", "data")
MAX_STR = 300


class AuditError(Exception):
    pass


def redact(value, key=""):
    """A copy of `value` that is safe to store."""
    if isinstance(value, dict):
        return {k: redact(v, k) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(v, key) for v in value]
    if SECRET_KEY.search(key or ""):
        return "<redacted>"
    if key in BULK_KEYS and isinstance(value, (str, bytes)):
        raw = value.encode() if isinstance(value, str) else value
        return {"len": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
    if isinstance(value, str) and len(value) > MAX_STR:
        return value[:MAX_STR] + f"...({len(value)} chars)"
    return value


def line_hash(line):
    return hashlib.sha256(line.encode("utf-8")).hexdigest()


def _canonical(record):
    return json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


class AuditLog:
    def __init__(self, path, now=time.time, max_bytes=2_000_000, keep=20, protect=None, unprotect=None):
        self.path = path
        self.now = now
        self.max_bytes = max_bytes
        self.keep = keep
        self.protect = protect or (lambda p: None)
        self.unprotect = unprotect or (lambda p: None)
        self._seq, self._prev = self._resume()

    # ---- reading the tail so a restart continues the chain
    def _resume(self):
        last = self._last_line(self.path)
        if last is None:
            rotated = self._last_line(self.path + ".1")
            if rotated is None:
                return 0, GENESIS
            last = rotated
        try:
            return int(json.loads(last)["seq"]), line_hash(last)
        except (ValueError, KeyError, TypeError):
            raise AuditError("the audit log's last line is unreadable - not appending to a damaged chain")

    @staticmethod
    def _last_line(path):
        try:
            with open(path, "rb") as fh:
                data = fh.read()
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise AuditError(f"can't read the audit log: {exc}")
        lines = [ln for ln in data.decode("utf-8", "replace").split("\n") if ln.strip()]
        return lines[-1] if lines else None

    # ---- writing
    def append(self, event, **fields):
        """Write one event; returns the record. Raises AuditError if it could not be written durably."""
        try:
            os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
            if not os.path.exists(self.path):
                os.close(os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o640))
                os.chmod(self.path, 0o640)
            if os.path.getsize(self.path) >= self.max_bytes:
                self._rotate()
            record = {"seq": self._seq + 1, "ts": round(self.now(), 3), "prev": self._prev, "event": event}
            for k, v in fields.items():
                record[k] = redact(v, k)
            line = _canonical(record)
            fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o640)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX)
                os.write(fd, (line + "\n").encode("utf-8"))
                os.fsync(fd)
            finally:
                os.close(fd)
            if self._seq == 0:
                self.protect(self.path)
        except OSError as exc:
            raise AuditError(f"can't write the audit log: {exc}")
        self._seq, self._prev = record["seq"], line_hash(line)
        return record

    def _rotate(self):
        self.unprotect(self.path)
        for n in range(self.keep - 1, 0, -1):
            src = f"{self.path}.{n}"
            if os.path.exists(src):
                self.unprotect(src)
                os.replace(src, f"{self.path}.{n + 1}")
        os.replace(self.path, self.path + ".1")
        self.protect(self.path + ".1")
        stale = f"{self.path}.{self.keep + 1}"
        if os.path.exists(stale):
            self.unprotect(stale)
            os.unlink(stale)
        with open(self.path, "a", encoding="utf-8"):
            pass
        os.chmod(self.path, 0o640)
        self.protect(self.path)

    # ---- reading / verifying
    def files(self):
        """Log files oldest first."""
        out = []
        n = 1
        while os.path.exists(f"{self.path}.{n}"):
            out.append(f"{self.path}.{n}")
            n += 1
        return list(reversed(out)) + ([self.path] if os.path.exists(self.path) else [])

    @staticmethod
    def verify(paths):
        """(ok, problems) for the chain across `paths` (oldest first). The first line of the first file is the anchor: its
        `prev` is trusted (it is the genesis, or the hash of a line that has since been rotated away)."""
        problems, prev, seq = [], None, None
        for path in paths:
            try:
                with open(path, encoding="utf-8") as fh:
                    lines = fh.read().split("\n")
            except OSError as exc:
                problems.append(f"{path}: unreadable ({exc})")
                continue
            for i, line in enumerate(lines, 1):
                if not line.strip():
                    continue
                try:
                    rec = json.loads(line)
                    if _canonical(rec) != line:
                        problems.append(f"{path}:{i}: line was altered (not in canonical form)")
                except ValueError:
                    problems.append(f"{path}:{i}: not valid JSON")
                    prev = line_hash(line)
                    continue
                if prev is not None and rec.get("prev") != prev:
                    problems.append(f"{path}:{i}: chain broken (a line before this one was changed, removed or reordered)")
                if seq is not None and rec.get("seq") != seq + 1:
                    problems.append(f"{path}:{i}: sequence jumps from {seq} to {rec.get('seq')}")
                prev, seq = line_hash(line), rec.get("seq", seq)
        return (not problems), problems

    def read_recent(self, limit=100, decision=None, action=None):
        """Newest first, optionally filtered - what the Admin activity viewer shows."""
        rows = []
        for path in reversed(self.files()):
            try:
                with open(path, encoding="utf-8") as fh:
                    lines = [ln for ln in fh.read().split("\n") if ln.strip()]
            except OSError:
                continue
            for line in reversed(lines):
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if decision and rec.get("decision") != decision:
                    continue
                if action and rec.get("action") != action:
                    continue
                rows.append(rec)
                if len(rows) >= limit:
                    return rows
        return rows
