#!/usr/bin/env python3
"""kronosd - the kronos daemon (docs/kronos-design.md, section 3). Runs as the `kronos` account, never as root.

It listens on a unix socket (systemd socket activation: /run/kronos/kronos.sock, 0660 root:kronos-clients), checks who is asking with
SO_PEERCRED (never with anything in the request), and hands the request to kronos_core.Kronos, which validates it, decides whether a
passphrase is needed, writes the decision to the audit log and only then acts - through the root helper (`sudo -n /usr/libexec/kronos/act`),
which re-validates the action itself.

Protocol: one JSON object per line each way. A client sends one request line; the daemon answers with zero or more
{"event": "output", "text": ...} lines while the action runs, then one final line with a "status".

  {"op": "request", "id", "action", "args", "passphrase"?}     the normal thing (op may be left out)
  {"op": "unlock", "passphrase"}                                  open an admin session for this program
  {"op": "set_passphrase", "new", "old"?}                         create or change the admin passphrase
  {"op": "lock"}                                                  end this program's admin session ("all": true ends every session)
  {"op": "status"}                                                {"passphrase": "unset|set|damaged", "unlocked": bool}
  {"op": "audit", "limit", "decision"?, "action"?}                the newest audit entries (Admin activity)
  {"op": "audit_verify"}                                          is the hash chain intact
  {"op": "agent"}                                                 a prompt agent (kronos-agent, in the desktop session) announces itself
                                                                  and keeps the connection open. When a passphrase is needed the daemon
                                                                  sends it {"id", "mode", "text", "program", "error"} and it answers
                                                                  {"id", "passphrase": text or null}. The passphrase goes from the agent
                                                                  to the daemon only - never through the program that asked.
"""
import json
import os
import socket
import socketserver
import struct
import subprocess
import sys
import tempfile
import threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kronos_audit as audit  # noqa: E402
import kronos_config as config  # noqa: E402
import kronos_core as core  # noqa: E402
import kronos_passphrase as pp  # noqa: E402

HELPER = ["sudo", "-n", "/usr/libexec/kronos/act"]
MAX_REQUEST = 4 * 1024 * 1024
STATE_DIR = "/var/lib/kronos"
AUDIT_PATH = "/var/log/kronos/audit.log"
INTERPRETERS = ("python", "perl", "ruby", "node", "bash", "sh", "dash", "java", "lua")


def peer_of(sock):
    """{"uid", "pid", "exe", "cmdline", "script"} of whoever is on the other end - from the kernel, not from the request."""
    pid, uid, _gid = struct.unpack("3i", sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i")))
    peer = {"uid": uid, "pid": pid, "start": "", "exe": "", "cmdline": "", "script": ""}
    try:
        with open(f"/proc/{pid}/stat", "rb") as fh:                 # field 22 is when the process started: (pid, start) can't be borrowed by a later process
            peer["start"] = fh.read().rsplit(b")", 1)[1].split()[19].decode()
    except (OSError, IndexError):
        pass
    try:
        peer["exe"] = os.readlink(f"/proc/{pid}/exe")
        with open(f"/proc/{pid}/cmdline", "rb") as fh:
            words = [w.decode("utf-8", "replace") for w in fh.read().split(b"\0") if w]
        peer["cmdline"] = " ".join(words)[:300]
        if os.path.basename(peer["exe"]).startswith(INTERPRETERS):
            script = next((w for w in words[1:] if not w.startswith("-")), "")
            peer["script"] = os.path.realpath(script) if script.startswith("/") else script
    except OSError:
        pass                                    # the program already exited; uid and pid are still what the kernel said
    return peer


class HelperRunner:
    """Asks the root helper to do an action (it re-validates it). One helper process per action."""

    def __init__(self, command=HELPER, env=None):
        self.command = command
        self.env = env

    def _call(self, request, emit=None):
        try:
            errors = tempfile.TemporaryFile("w+")          # a file, not a pipe: a chatty helper can't fill a pipe nobody is reading and hang
            proc = subprocess.Popen(self.command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=errors, text=True, env=self.env)
        except OSError as exc:
            return {"rc": 1, "detail": "helper missing", "output": f"The admin helper could not be started: {exc}"}
        proc.stdin.write(json.dumps(request))
        proc.stdin.close()
        final = None
        for line in proc.stdout:
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if "o" in msg:
                if emit:
                    emit(msg["o"])
            elif "rc" in msg:
                final = msg
        proc.wait()
        errors.seek(0)
        err = errors.read(4000)
        errors.close()
        if final is None:
            return {"rc": 1, "detail": "helper failed", "output": (err or "The admin helper stopped without an answer.")[-500:]}
        return final

    def run_action(self, action, args, emit=None):
        final = self._call({"action": action, "args": args}, emit)
        return int(final.get("rc", 1)), str(final.get("output", "")), str(final.get("detail", ""))

    def chattr(self, mode, path):
        self._call({"op": "chattr", "mode": mode, "path": path})


class AgentHub:
    """The one connected prompt agent (kronos-agent, running in the desktop session). Its connection stays open; the daemon writes a
    question to it and reads the answer, so the passphrase reaches the daemon directly. No agent connected -> NoAgent, and the
    requesting program has to ask by itself (a terminal over SSH)."""
    TIMEOUT_S = 180

    def __init__(self):
        self._lock = threading.Lock()
        self._sock, self._closed, self._n, self._buf = None, None, 0, b""

    def attach(self, sock):
        """Adopt an agent's connection and wait until it goes away (the caller is the connection's handler thread)."""
        closed = threading.Event()
        with self._lock:
            if self._sock is not None:
                self._drop_locked()
            self._sock, self._closed, self._buf = sock, closed, b""
        closed.wait()

    def _drop_locked(self):
        if self._sock is not None:
            try:
                self._sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            self._closed.set()
        self._sock = self._closed = None

    def connected(self):
        return self._sock is not None

    def ask(self, info):
        """The passphrase the person typed, or None if they cancelled or nobody answered in time. Raises core.NoAgent if there's no agent."""
        with self._lock:
            sock = self._sock
            if sock is None:
                raise core.NoAgent()
            self._n += 1
            qid = self._n
            try:
                sock.settimeout(10)
                sock.sendall((json.dumps({"id": qid, **info}) + "\n").encode("utf-8"))
                sock.settimeout(self.TIMEOUT_S)
                while b"\n" not in self._buf:
                    data = sock.recv(4096)
                    if not data:
                        self._drop_locked()
                        raise core.NoAgent()
                    self._buf += data
            except socket.timeout:
                return None                                           # nobody answered: same as cancelling
            except OSError:
                self._drop_locked()
                raise core.NoAgent()
            line, _, self._buf = self._buf.partition(b"\n")
        try:
            answer = json.loads(line)
        except ValueError:
            return None
        if not isinstance(answer, dict) or answer.get("id") != qid:
            return None
        phrase = answer.get("passphrase")
        return phrase if isinstance(phrase, str) and phrase else None


class JournalAudit(audit.AuditLog):
    """The same log, and every record also goes to the journal (kronos.service's stderr) - a second copy the kronos account can't edit."""

    def append(self, event, **fields):
        record = super().append(event, **fields)
        print(json.dumps(record, sort_keys=True), file=sys.stderr, flush=True)
        return record


class Daemon:
    def __init__(self, kronos, lock=None, hub=None):
        self.k = kronos
        self.lock = lock or threading.Lock()
        self.hub = hub or AgentHub()
        self.k.prompter = self.hub.ask

    def handle(self, request, peer, emit):
        """The final response dict for one request line (output goes through emit as it happens)."""
        if not isinstance(request, dict):
            return {"status": "refused", "reason": "Malformed request."}
        op = request.get("op", "request")
        if peer.get("uid") not in self.k.clients:
            return self.k._refuse(request, peer, "This account isn't allowed to use kronos.", request.get("action"))
        if op == "status":
            return {"status": "ok", "passphrase": self.k.pass_.status(), "unlocked": self.k.sessions.valid(core.peer_key(peer))}
        if op == "audit":
            limit = request.get("limit", 50)
            limit = limit if isinstance(limit, int) and not isinstance(limit, bool) and 1 <= limit <= 500 else 50
            rows = self.k.audit.read_recent(limit, decision=request.get("decision"), action=request.get("action"))
            return {"status": "ok", "entries": rows}
        if op == "audit_verify":
            ok, problems = audit.AuditLog.verify(self.k.audit.files())
            return {"status": "ok", "intact": ok, "problems": problems[:20]}
        with self.lock:                          # one admin thing at a time: the decisions, sessions and package tools all assume it
            if op == "request":
                return self.k.handle(request, peer, emit)
            if op == "unlock":
                return self.k.unlock(peer, request.get("passphrase") or "", request)
            if op == "set_passphrase":
                new, old = request.get("new"), request.get("old")
                if not isinstance(new, str) or not (old is None or isinstance(old, str)):
                    return {"status": "refused", "reason": "Malformed request."}
                return self.k.set_passphrase(peer, new, old=old)
            if op == "lock":
                self.k.sessions.revoke(None if request.get("all") is True else core.peer_key(peer))
                return {"status": "ok"}
        return {"status": "refused", "reason": "Unknown request."}


class Handler(socketserver.StreamRequestHandler):
    daemon_obj = None

    def handle(self):
        peer = peer_of(self.request)
        line = self.rfile.readline(MAX_REQUEST + 1)
        try:
            request = json.loads(line) if 0 < len(line) <= MAX_REQUEST else None
        except ValueError:
            request = None

        def emit(text):
            self._send({"event": "output", "text": text})
        if isinstance(request, dict) and request.get("op") == "agent":
            if peer.get("uid") in self.daemon_obj.k.clients:
                self._send({"status": "ok", "event": "agent-attached"})
                self.daemon_obj.hub.attach(self.request)
            else:
                self._send({"status": "refused", "reason": "This account isn't allowed to use kronos."})
            return
        try:
            response = self.daemon_obj.handle(request, peer, emit)
        except Exception as exc:                # never leave a client hanging, and never let a bug look like success
            print(f"kronosd: internal error: {exc!r}", file=sys.stderr, flush=True)
            response = {"status": "refused", "reason": "kronos hit an internal error; nothing was done."}
        self._send(response)

    def _send(self, obj):
        try:
            self.wfile.write((json.dumps(obj) + "\n").encode("utf-8"))
            self.wfile.flush()
        except OSError:
            pass                                 # the client went away; an action already started still finishes and is logged


class Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True

    def __init__(self, sock=None, path=None, handler=Handler):
        if sock is not None:                     # systemd handed us the listening socket
            socketserver.BaseServer.__init__(self, None, handler)
            self.socket = sock
        else:
            socketserver.UnixStreamServer.__init__(self, path, handler)


def build(state_dir=STATE_DIR, audit_path=AUDIT_PATH, runner=None, clients=None, policy=None, app_check=None, iterations=pp.ITERATIONS):
    runner = runner or HelperRunner()
    protect = (lambda p: runner.chattr("+a", p)) if hasattr(runner, "chattr") else None
    unprotect = (lambda p: runner.chattr("-a", p)) if hasattr(runner, "chattr") else None
    log = JournalAudit(audit_path, protect=protect, unprotect=unprotect)
    passphrase = pp.Passphrase(os.path.join(state_dir, "passphrase.json"), iterations=iterations)
    k = core.Kronos(log, passphrase, pp.AdminSessions(), runner,
                    policy=policy if policy is not None else config.load_policy(),
                    app_check=app_check if app_check is not None else config.app_check(),
                    clients=clients if clients is not None else config.load_clients())
    return Daemon(k)


def main(argv):
    if os.geteuid() == 0:
        print("kronosd: refusing to run as root - it is meant to run as the kronos account", file=sys.stderr)
        return 2
    daemon = build()
    Handler.daemon_obj = daemon
    if os.environ.get("LISTEN_FDS") == "1" and os.environ.get("LISTEN_PID") == str(os.getpid()):
        server = Server(sock=socket.socket(fileno=3))
    else:
        path = argv[0] if argv else "/run/kronos/kronos.sock"
        if os.path.exists(path):
            os.unlink(path)
        server = Server(path=path)
        os.chmod(path, 0o660)
    print("kronosd: ready", file=sys.stderr, flush=True)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
