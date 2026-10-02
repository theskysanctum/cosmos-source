"""greetd backend for cosmos-greeter.

The greeter used to talk to LightDM through liblightdm-gobject; greetd replaces
LightDM (LightDM's greeter needs an X server, and CosmOS is Wayland-only). This
module gives the greeter the small API surface it actually uses - a conversation
object with the same shape (authenticate / respond / cancel / start_session, plus
show-prompt / show-message / authentication-complete callbacks), a user list and
a session list - on top of greetd's IPC: length-prefixed JSON over the unix
socket named by $GREETD_SOCK.

greetd protocol (see greetd-ipc(7)), one request per round trip:
  create_session{username}                    -> auth_message | success | error
  post_auth_message_response{response}        -> auth_message | success | error
  start_session{cmd, env}                     -> success | error
  cancel_session                              -> success
auth_message carries auth_message_type visible|secret|info|error. `success` after
a create/post means authentication is complete and start_session may follow.
"""
import json
import os
import pwd
import socket
import struct
import subprocess

SESSION_DIRS = ("/usr/share/wayland-sessions",)
STATE_DIR = os.environ.get("COSMOS_GREETER_STATE", "/var/lib/cosmos-greeter")
ACCOUNTS_ICONS = "/var/lib/AccountsService/icons"
FIRST_UID, LAST_UID = 1000, 60000
NOLOGIN_SHELLS = ("nologin", "false", "sync", "halt", "shutdown")

PROMPT_SECRET, PROMPT_QUESTION = "secret", "question"
MESSAGE_INFO, MESSAGE_ERROR = "info", "error"


class GreetdError(Exception):
    pass


class GreetdConnection:
    def __init__(self, path=None):
        path = path or os.environ.get("GREETD_SOCK")
        if not path:
            raise GreetdError("GREETD_SOCK is not set - not running under greetd")
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.connect(path)

    def _read(self, count):
        data = b""
        while len(data) < count:
            chunk = self.sock.recv(count - len(data))
            if not chunk:
                raise GreetdError("greetd closed the connection")
            data += chunk
        return data

    def request(self, message):
        payload = json.dumps(message).encode()
        self.sock.sendall(struct.pack("=I", len(payload)) + payload)
        (length,) = struct.unpack("=I", self._read(4))
        return json.loads(self._read(length))

    def close(self):
        self.sock.close()


class Conversation:
    """One login attempt at a time. Callbacks: on_prompt(text, kind),
    on_message(text, kind), on_complete(authenticated)."""

    def __init__(self, connection, on_prompt, on_message, on_complete):
        self.conn = connection
        self.on_prompt, self.on_message, self.on_complete = on_prompt, on_message, on_complete
        self.in_authentication = False
        self.authenticated = False

    def authenticate(self, username):
        self.cancel()
        self.authenticated = False
        self.in_authentication = True
        self._handle(self.conn.request({"type": "create_session", "username": username}))

    def respond(self, text):
        if not self.in_authentication:
            return
        self._handle(self.conn.request({"type": "post_auth_message_response", "response": text}))

    def cancel(self):
        if self.in_authentication:
            self.in_authentication = False
            try:
                self.conn.request({"type": "cancel_session"})
            except (GreetdError, OSError):
                pass

    def _handle(self, reply):
        kind = reply.get("type")
        if kind == "auth_message":
            message_type, text = reply["auth_message_type"], reply["auth_message"]
            if message_type in ("secret", "visible"):
                self.on_prompt(text, PROMPT_SECRET if message_type == "secret" else PROMPT_QUESTION)
            else:
                self.on_message(text, MESSAGE_ERROR if message_type == "error" else MESSAGE_INFO)
                # info/error messages expect an (empty) acknowledgement to go on
                self._handle(self.conn.request({"type": "post_auth_message_response", "response": None}))
        elif kind == "success":
            self.in_authentication = False
            self.authenticated = True
            self.on_complete(True)
        else:  # error - a failed login, a rejected password change, ...
            self.in_authentication = False
            try:
                self.conn.request({"type": "cancel_session"})
            except (GreetdError, OSError):
                pass
            self.on_complete(False)

    def start_session(self, command, env=()):
        reply = self.conn.request({"type": "start_session", "cmd": list(command), "env": list(env)})
        if reply.get("type") != "success":
            raise GreetdError(reply.get("description", "start_session failed"))


class User:
    def __init__(self, entry):
        self.name = entry.pw_name
        gecos = entry.pw_gecos.split(",")[0].strip()
        self.display_name = gecos or entry.pw_name
        self.home = entry.pw_dir

    def get_name(self):
        return self.name

    def get_display_name(self):
        return self.display_name

    def get_image(self):
        for path in (os.path.join(ACCOUNTS_ICONS, self.name), os.path.join(self.home, ".face")):
            if os.access(path, os.R_OK):
                return path
        return None


def list_users():
    users = []
    for entry in pwd.getpwall():
        if FIRST_UID <= entry.pw_uid < LAST_UID and os.path.basename(entry.pw_shell) not in NOLOGIN_SHELLS:
            users.append(User(entry))
    return sorted(users, key=lambda u: u.name)


def list_sessions():
    """{key: [command...]} from the wayland-sessions desktop entries; the key is
    the file name minus .desktop ("cosmos", "gamebox")."""
    sessions = {}
    for directory in SESSION_DIRS:
        try:
            names = sorted(os.listdir(directory))
        except OSError:
            continue
        for name in names:
            if not name.endswith(".desktop"):
                continue
            exec_line = None
            with open(os.path.join(directory, name), encoding="utf-8") as fh:
                for line in fh:
                    if line.startswith("Exec="):
                        exec_line = line[5:].strip()
                        break
            if exec_line:
                sessions[name[:-len(".desktop")]] = exec_line.split()
    return sessions


def _state_path():
    return os.path.join(STATE_DIR, "last-sessions.json")


def remembered_session(username):
    try:
        with open(_state_path(), encoding="utf-8") as fh:
            return json.load(fh).get(username)
    except (OSError, ValueError):
        return None


def remember_session(username, session):
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        try:
            with open(_state_path(), encoding="utf-8") as fh:
                state = json.load(fh)
        except (OSError, ValueError):
            state = {}
        state[username] = session
        with open(_state_path(), "w", encoding="utf-8") as fh:
            json.dump(state, fh)
    except OSError:
        pass


def can_power():
    """Restart/shut down go through logind; a polkit rule (see the Containerfile)
    lets the greeter account do that."""
    return True


def restart():
    subprocess.Popen(["systemctl", "reboot"])


def shutdown():
    subprocess.Popen(["systemctl", "poweroff"])


# ---------------------------------------------------------------------
# Saying why a login failed
# ---------------------------------------------------------------------
PASSWORD_RULES = "at least 4 characters, and not a common word or a simple pattern"

_PWQUALITY_REASONS = (
    ("no password supplied", "You didn't type a password."),
    ("dictionary", "That password is too common - it is based on a dictionary word."),
    ("shorter than", "That password is too short."),
    ("too short", "That password is too short."),
    ("too similar", "That password is too similar to the old one."),
    ("same as the old", "That is the same as your old password."),
    ("do not match", "The two entries didn't match."),
    ("palindrome", "That password is a palindrome."),
    ("simplistic", "That password is too simple."),
    ("systematic", "That password is too simple - it is a sequence."),
    ("case changes only", "That password only changes upper/lower case."),
    ("rotated", "That password is just a rotation of the old one."),
    ("repeat", "That password repeats the same characters."),
    ("username", "That password contains your username."),
)


def describe_failure(changing_password, pam_error="", typed_something=True):
    """(headline, detail) for a failed login attempt - the message the login screen
    shows in its big banner. `changing_password` is whether the forced first-login
    password change was underway; `pam_error` the last error PAM sent (pwquality's
    "BAD PASSWORD: ..." for a rejected new password)."""
    text = (pam_error or "").strip()
    reason = ""
    lowered = text.lower()
    for needle, friendly in _PWQUALITY_REASONS:
        if needle in lowered:
            reason = friendly
            break
    if changing_password:
        headline = "New password not accepted"
        if not reason:
            reason = text.replace("BAD PASSWORD: ", "") if "BAD PASSWORD" in text else "It was rejected."
        return headline, f"{reason} Choose {PASSWORD_RULES}, then try again."
    if not typed_something:
        return "Enter your password", "Type your password and press Enter."
    return "Wrong password", "That password wasn't right. Try again."
