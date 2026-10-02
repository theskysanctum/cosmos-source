#!/usr/bin/env python3
"""act - the root half of kronos (installed as /usr/libexec/kronos/act; the only thing sudoers lets the kronos account run).

It reads ONE JSON request on standard input and does it:

  {"action": "pkg.install", "args": {...}}   re-validates the action with the same code as the daemon (kronos_actions), then runs its
                                             plan. It does not trust the daemon's checks - the daemon has already decided WHO may ask
                                             and whether a passphrase was needed, but WHAT is done is decided here, from the typed action.
  {"op": "chattr", "mode": "+a", "path": "/var/log/kronos/audit.log"}   the audit log's append-only flag; nothing else can be flagged.

and answers with JSON lines on standard output: {"o": "live output"} while it runs, then one {"rc", "detail", "output"} line.
Nothing here starts a shell, and every command is an argv list built by kronos_actions from validated data.
"""
import json
import os
import re
import select
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, "/usr/local/lib/kronos")
import kronos_actions as actions  # noqa: E402
import kronos_config as config  # noqa: E402
import kronos_core as core  # noqa: E402

MAX_REQUEST = 4 * 1024 * 1024
MAX_OUTPUT = 1024 * 1024
TIMEOUT_S = 3600
CHATTR_PATH = re.compile(r"/var/log/kronos/audit\.log(\.[0-9]{1,3})?")
ENV = {"PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C.UTF-8", "HOME": "/root"}


class LocalRunner:
    """Really runs things. Standard input is the request's own `stdin` text (or nothing); output is merged and streamed."""

    def __init__(self, env=None, timeout=TIMEOUT_S):
        self.env = ENV if env is None else env
        self.timeout = timeout

    def run(self, argv, stdin=None, cwd=None, emit=None):
        try:
            proc = subprocess.Popen(argv, stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, cwd=cwd, env=self.env, close_fds=True)
        except OSError as exc:
            return 127, f"{argv[0]}: {exc.strerror or exc}\n"
        if stdin is not None:
            try:
                proc.stdin.write(stdin.encode("utf-8"))
                proc.stdin.close()
            except OSError:
                pass
        chunks, size, deadline = [], 0, time.monotonic() + self.timeout
        fd = proc.stdout.fileno()
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                proc.kill()
                chunks.append("\n[stopped: it ran for more than an hour]\n")
                break
            ready, _, _ = select.select([fd], [], [], min(left, 5))
            if not ready:
                if proc.poll() is not None:                # it has exited; something it started may still hold the pipe open - don't wait for that
                    break
                continue
            data = os.read(fd, 65536)
            if not data:
                break
            text = data.decode("utf-8", "replace")
            if emit:
                emit(text)
            if size < MAX_OUTPUT:
                chunks.append(text)
                size += len(text)
        return proc.wait(), "".join(chunks)

    def read_file(self, path):
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, encoding="utf-8", errors="replace") as fh:
            return fh.read(actions.MAX_FILE_BYTES + 1)[:actions.MAX_FILE_BYTES]

    def write_file(self, path, text):
        """Replace the file in one step, refusing a symlink at the path (a link planted there can't redirect the write)."""
        if os.path.islink(path):
            raise OSError(f"{path} is a symbolic link")
        try:
            mode = os.stat(path).st_mode & 0o7777
        except FileNotFoundError:
            mode = 0o644
        tmp = f"{path}.kronos-{os.getpid()}"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(text)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)


def send(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def do_action(request, runner=None, ctx=None):
    ctx = ctx or actions.Context(config.load_policy(), config.app_check())
    try:
        plan = actions.plan(request.get("action"), request.get("args", {}), ctx)
    except actions.Refused as exc:
        return {"rc": 1, "detail": "refused by the helper", "output": str(exc)}
    rc, output, detail = core.execute_plan(plan, runner or LocalRunner(), emit=lambda text: send({"o": text}))
    return {"rc": rc, "detail": detail, "output": output[:MAX_OUTPUT]}


def do_chattr(request):
    mode, path = request.get("mode"), request.get("path")
    if mode not in ("+a", "-a") or not isinstance(path, str) or not CHATTR_PATH.fullmatch(path) or not os.path.isfile(path):
        return {"rc": 1, "detail": "refused", "output": "That file can't be flagged."}
    done = subprocess.run(["chattr", mode, path], capture_output=True, text=True, env=ENV)
    return {"rc": done.returncode, "detail": "chattr", "output": done.stdout + done.stderr}


def main():
    if os.geteuid() != 0 and os.environ.get("KRONOS_HELPER_TEST") != "1":
        print("act: this is the root half of kronos and is not meant to be run by hand", file=sys.stderr)
        return 2
    if os.geteuid() != 0 and os.environ.get("KRONOS_TEST_PATH"):         # tests only: never honoured as root, where sudo cleans the environment anyway
        ENV["PATH"] = os.environ["KRONOS_TEST_PATH"]
    raw = sys.stdin.read(MAX_REQUEST + 1)
    try:
        request = json.loads(raw) if len(raw) <= MAX_REQUEST else None
    except ValueError:
        request = None
    if not isinstance(request, dict):
        send({"rc": 1, "detail": "bad request", "output": "The request was not understood."})
        return 1
    result = do_chattr(request) if request.get("op") == "chattr" else do_action(request)
    send(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
