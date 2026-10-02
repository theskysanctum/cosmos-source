#!/usr/bin/env python3
"""cosmos-autologin - greetd's default session: logs the one Unix account in without
asking anything.

CosmOS has no Unix login screen. The single account (`user`, blank password) is logged
in automatically at boot and again after every logout, and the profile picker in the
session (cosmos-profiles) is what asks who is using the machine. greetd runs this in
place of a greeter: it opens a session for the account over greetd's IPC (a blank
password passes PAM's `nullok` without a prompt) and starts cosmos-session.

If PAM does ask something - someone gave the account a password, or the account is
locked or expired - it hands over to the full login screen (cosmos-greeter, inside
cosmos-wm) instead of leaving a black screen, so a machine can never be locked out
of a way to sign in.
"""
import os
import sys

import cosmos_login as login

ACCOUNT = os.environ.get("COSMOS_AUTOLOGIN_USER", "user")
SESSION_KEY = "cosmos"
FALLBACK = ["cosmos-wm", "-s", "cosmos-greeter"]


def autologin(connection=None, account=None, sessions=None):
    """Tries the automatic login; True if the session was started. False means the
    account wants something (a prompt or a refusal) and the login screen is needed."""
    account = account or ACCOUNT
    events = {"asked": False}
    connection = connection or login.GreetdConnection()
    conversation = login.Conversation(
        connection,
        lambda text, kind: events.update(asked=True),
        lambda text, kind: None,
        lambda ok: None,
    )
    conversation.authenticate(account)
    if events["asked"] or not conversation.authenticated:
        conversation.cancel()
        return False
    sessions = sessions if sessions is not None else login.list_sessions()
    command = sessions.get(SESSION_KEY) or ["/usr/local/bin/cosmos-session"]
    conversation.start_session(command)
    return True


def main():
    try:
        if autologin():
            return 0
    except (login.GreetdError, OSError) as exc:
        print(f"cosmos-autologin: {exc}", file=sys.stderr)
    print("cosmos-autologin: the account needs more than an automatic login - showing the login screen",
          file=sys.stderr)
    os.execvp(FALLBACK[0], FALLBACK)


if __name__ == "__main__":
    sys.exit(main())
