#!/usr/bin/env python3
"""Tests for cosmos_login against a fake greetd on a unix socket.
Run:  python3 test_cosmos_login.py"""
import json
import os
import socket
import struct
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cosmos_login as cl  # noqa: E402


class FakeGreetd:
    """Answers each request from a script of replies, recording the requests."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.requests = []
        self.path = os.path.join(tempfile.mkdtemp(), "greetd.sock")
        self.server = socket.socket(socket.AF_UNIX)
        self.server.bind(self.path)
        self.server.listen(1)
        threading.Thread(target=self.serve, daemon=True).start()

    def serve(self):
        conn, _ = self.server.accept()
        while True:
            head = conn.recv(4)
            if len(head) < 4:
                return
            (length,) = struct.unpack("=I", head)
            body = b""
            while len(body) < length:
                body += conn.recv(length - len(body))
            self.requests.append(json.loads(body))
            reply = json.dumps(self.replies.pop(0) if self.replies else {"type": "success"}).encode()
            conn.sendall(struct.pack("=I", len(reply)) + reply)


def make(replies):
    fake = FakeGreetd(replies)
    events = []
    conv = cl.Conversation(
        cl.GreetdConnection(fake.path),
        lambda text, kind: events.append(("prompt", text, kind)),
        lambda text, kind: events.append(("message", text, kind)),
        lambda ok: events.append(("complete", ok)),
    )
    return fake, conv, events


SECRET = {"type": "auth_message", "auth_message_type": "secret", "auth_message": "Password: "}


class ConversationTests(unittest.TestCase):
    def test_password_login_then_session(self):
        fake, conv, events = make([SECRET, {"type": "success"}, {"type": "success"}])
        conv.authenticate("user")
        self.assertEqual(events, [("prompt", "Password: ", "secret")])
        self.assertTrue(conv.in_authentication)
        conv.respond("hunter2")
        self.assertEqual(events[-1], ("complete", True))
        self.assertTrue(conv.authenticated and not conv.in_authentication)
        conv.start_session(["cosmos-session"])
        self.assertEqual(fake.requests, [
            {"type": "create_session", "username": "user"},
            {"type": "post_auth_message_response", "response": "hunter2"},
            {"type": "start_session", "cmd": ["cosmos-session"], "env": []},
        ])

    def test_wrong_password_cancels_and_reports_failure(self):
        fake, conv, events = make([SECRET, {"type": "error", "error_type": "auth_error", "description": "no"}])
        conv.authenticate("user")
        conv.respond("bad")
        self.assertEqual(events[-1], ("complete", False))
        self.assertFalse(conv.authenticated)
        self.assertEqual(fake.requests[-1], {"type": "cancel_session"})

    def test_expired_password_flow_prompts_twice(self):
        info = {"type": "auth_message", "auth_message_type": "info", "auth_message": "expired"}
        retype = {"type": "auth_message", "auth_message_type": "secret", "auth_message": "Retype new password: "}
        fake, conv, events = make([info, SECRET, retype, {"type": "success"}])
        conv.authenticate("user")
        # the info message is shown and acknowledged, which yields the first prompt
        self.assertEqual(events[0], ("message", "expired", "info"))
        self.assertEqual(events[1][0], "prompt")
        conv.respond("newpw")
        self.assertEqual(events[-1], ("prompt", "Retype new password: ", "secret"))
        conv.respond("newpw")
        self.assertEqual(events[-1], ("complete", True))

    def test_visible_prompt_is_a_question(self):
        q = {"type": "auth_message", "auth_message_type": "visible", "auth_message": "Code: "}
        _fake, conv, events = make([q])
        conv.authenticate("user")
        self.assertEqual(events, [("prompt", "Code: ", "question")])

    def test_new_attempt_cancels_running_one(self):
        fake, conv, _events = make([SECRET, {"type": "success"}, SECRET])
        conv.authenticate("a")
        conv.authenticate("b")
        self.assertEqual([r["type"] for r in fake.requests], ["create_session", "cancel_session", "create_session"])

    def test_start_session_failure_raises(self):
        _fake, conv, _events = make([{"type": "error", "error_type": "error", "description": "boom"}])
        with self.assertRaises(cl.GreetdError):
            conv.start_session(["x"])

    def test_no_greetd_socket(self):
        os.environ.pop("GREETD_SOCK", None)
        with self.assertRaises(cl.GreetdError):
            cl.GreetdConnection()


class AutologinTests(unittest.TestCase):
    def load(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("al", os.path.join(os.path.dirname(os.path.abspath(__file__)), "cosmos-autologin.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_blank_password_account_is_logged_in_with_no_questions(self):
        fake = FakeGreetd([{"type": "success"}, {"type": "success"}])
        ok = self.load().autologin(cl.GreetdConnection(fake.path), "user", {"cosmos": ["/usr/local/bin/cosmos-session"]})
        self.assertTrue(ok)
        self.assertEqual(fake.requests, [{"type": "create_session", "username": "user"},
                                         {"type": "start_session", "cmd": ["/usr/local/bin/cosmos-session"], "env": []}])

    def test_a_password_prompt_falls_back_to_the_login_screen(self):
        fake = FakeGreetd([SECRET, {"type": "success"}])
        ok = self.load().autologin(cl.GreetdConnection(fake.path), "user", {})
        self.assertFalse(ok)
        self.assertEqual(fake.requests[-1], {"type": "cancel_session"})      # nothing left half-open

    def test_a_refusal_falls_back_too(self):
        fake = FakeGreetd([{"type": "error", "error_type": "auth_error", "description": "no"}, {"type": "success"}])
        self.assertFalse(self.load().autologin(cl.GreetdConnection(fake.path), "user", {}))


class FailureMessageTests(unittest.TestCase):
    def test_wrong_password_is_plain(self):
        head, detail = cl.describe_failure(False)
        self.assertEqual(head, "Wrong password")

    def test_empty_password_asks_for_one(self):
        self.assertEqual(cl.describe_failure(False, typed_something=False)[0], "Enter your password")

    def test_rejected_new_password_says_why(self):
        head, detail = cl.describe_failure(True, "BAD PASSWORD: The password fails the dictionary check - it is based on a dictionary word")
        self.assertEqual(head, "New password not accepted")
        self.assertIn("too common", detail)
        self.assertIn("at least 4 characters", detail)

    def test_other_reasons(self):
        for pam, word in (("BAD PASSWORD: No password supplied", "didn't type"),
                          ("BAD PASSWORD: The password is shorter than 4 characters", "too short"),
                          ("Sorry, passwords do not match.", "didn't match")):
            self.assertIn(word, cl.describe_failure(True, pam)[1])

    def test_unknown_pam_error_is_passed_through_not_hidden(self):
        self.assertIn("weird reason", cl.describe_failure(True, "BAD PASSWORD: weird reason")[1])
        self.assertIn("rejected", cl.describe_failure(True, "")[1])


class SessionTests(unittest.TestCase):
    def test_sessions_and_remembered_choice(self):
        d = tempfile.mkdtemp()
        for key, exe in (("cosmos", "/usr/local/bin/cosmos-session"), ("gamebox", "/usr/local/bin/gamebox-session")):
            with open(os.path.join(d, f"{key}.desktop"), "w") as fh:
                fh.write(f"[Desktop Entry]\nName={key}\nExec={exe}\nType=Application\n")
        old = cl.SESSION_DIRS
        cl.SESSION_DIRS = (d,)
        cl.STATE_DIR = tempfile.mkdtemp()
        try:
            self.assertEqual(cl.list_sessions(), {"cosmos": ["/usr/local/bin/cosmos-session"],
                                                  "gamebox": ["/usr/local/bin/gamebox-session"]})
            self.assertIsNone(cl.remembered_session("user"))
            cl.remember_session("user", "gamebox")
            cl.remember_session("other", "cosmos")
            self.assertEqual(cl.remembered_session("user"), "gamebox")
        finally:
            cl.SESSION_DIRS = old


if __name__ == "__main__":
    unittest.main()
