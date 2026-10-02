"""Tiny client for cosmos-wm's control socket ($XDG_RUNTIME_DIR/cosmos-wm.sock).

The Wayland stand-in for what the X11 window manager exposed as
_NET_CLIENT_LIST / _NET_ACTIVE_WINDOW: one line-based request per connection,
answered with plain text (see config/wm/cosmos-wm.c for the commands). Every
function fails soft - a missing compositor or socket means "nothing to talk to",
never an exception in the bar or launcher that called it.
"""
import os
import socket

SOCKET_PATH = os.path.join(os.environ.get("XDG_RUNTIME_DIR") or "/tmp", "cosmos-wm.sock")


def request(command, timeout=1.0):
    """Sends one command and returns the reply text, or None if unreachable."""
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            sock.connect(SOCKET_PATH)
            sock.sendall(command.encode() + b"\n")
            chunks = []
            while True:
                data = sock.recv(4096)
                if not data:
                    break
                chunks.append(data)
            return b"".join(chunks).decode(errors="replace")
    except OSError:
        return None


def windows():
    """[(id, is_active, title)] for every swappable window, most recent first."""
    reply = request("windows") or ""
    result = []
    for line in reply.splitlines():
        parts = line.split("\t", 2)
        if len(parts) == 3 and parts[0].isdigit():
            result.append((int(parts[0]), parts[1] == "1", parts[2]))
    return result


def activate(window_id):
    return (request(f"activate {int(window_id)}") or "").startswith("ok")


def close(window_id):
    return (request(f"close {int(window_id)}") or "").startswith("ok")


def output_size():
    """(width, height) of the screen, or None."""
    reply = (request("output") or "").strip()
    try:
        width, height = reply.split("x")
        return int(width), int(height)
    except ValueError:
        return None


def show_desktop():
    """Minimizes every window so the background launcher shows."""
    return (request("desktop") or "").startswith("ok")


if __name__ == "__main__":
    # Command line: `cosmos_wm_ipc.py <command...>` - prints the reply and exits 1 if
    # cosmos-wm could not be reached (e.g. `cosmos_wm_ipc.py quit`, `... output`).
    import sys
    reply = request(" ".join(sys.argv[1:]))
    if reply is None:
        sys.exit(1)
    sys.stdout.write(reply)
