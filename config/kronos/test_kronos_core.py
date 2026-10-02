"""Tests for the kronos decision engine: the whole request flow against a fake runner and a real (temp) audit log."""
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))
import cosmos_app_policy as pol
import kronos_audit as ka
import kronos_core as kc
import kronos_passphrase as pp

USER = {"uid": 1000, "pid": 4242, "exe": "/usr/local/bin/cosmos-updates", "cmdline": "cosmos-updates"}
OTHER_APP = {"uid": 1000, "pid": 99, "exe": "/usr/bin/other", "cmdline": "other"}
STRANGER = {"uid": 1001, "pid": 1, "exe": "/usr/bin/x", "cmdline": "x"}


class FakeRunner:
    def __init__(self):
        self.calls, self.files, self.fail = [], {"/etc/hosts": "127.0.0.1 localhost\n"}, set()

    def run(self, argv, stdin=None, cwd=None):
        self.calls.append((argv, stdin, cwd))
        return (1, "boom") if argv[0] in self.fail or " ".join(argv) in self.fail else (0, "ran " + " ".join(argv[:2]) + "\n")

    def read_file(self, path):
        if path not in self.files:
            raise FileNotFoundError(path)
        return self.files[path]

    def write_file(self, path, text):
        self.files[path] = text


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.t = [1000.0]
        self.audit = ka.AuditLog(os.path.join(self.dir, "audit.log"), now=lambda: self.t[0])
        self.pass_ = pp.Passphrase(os.path.join(self.dir, "unlock.json"), now=lambda: self.t[0], iterations=1000)
        self.sessions = pp.AdminSessions(now=lambda: self.t[0])
        self.runner = FakeRunner()
        self.k = kc.Kronos(self.audit, self.pass_, self.sessions, self.runner, clients=(1000,),
                           app_check=lambda kind, name: pol.check(kind, name, pol.load(), pol.load_blocked()))

    def req(self, action, args=None, peer=USER, **extra):
        return self.k.handle({"id": "r1", "action": action, "args": args or {}, **extra}, peer)

    def records(self):
        return [json.loads(l) for l in open(self.audit.path).read().split("\n") if l]

    def chain_ok(self):
        return ka.AuditLog.verify(self.audit.files())[0]


class AutomaticTests(Base):
    def test_an_automatic_action_runs_and_leaves_a_decision_and_a_result(self):
        r = self.req("pkg.install", {"packages": ["htop"]})
        self.assertEqual((r["status"], r["exit"]), ("ok", 0))
        self.assertEqual(self.runner.calls[0][0][:4], ["rpm-ostree", "install", "--idempotent", "--apply-live"])
        recs = self.records()
        self.assertEqual([(x["event"], x.get("decision")) for x in recs], [("decision", "auto"), ("result", "auto")])
        self.assertEqual(recs[0]["peer"]["exe"], "/usr/local/bin/cosmos-updates")
        self.assertEqual(recs[1]["exit"], 0)
        self.assertTrue(self.chain_ok())

    def test_fallback_alternative_is_used_when_the_first_fails(self):
        self.runner.fail = {"rpm-ostree install --idempotent --apply-live htop"}
        r = self.req("pkg.install", {"packages": ["htop"]})
        self.assertEqual(r["status"], "ok")
        self.assertEqual(len(self.runner.calls), 2)
        self.assertNotIn("--apply-live", self.runner.calls[1][0])

    def test_a_failing_command_is_reported_and_logged(self):
        self.runner.fail = {"flatpak"}
        r = self.req("flatpak.install", {"id": "org.kde.krita"})
        self.assertEqual((r["status"], r["exit"]), ("failed", 1))
        self.assertEqual(len(self.runner.calls), 1)                        # the terminal check after it did not run
        self.assertEqual(self.records()[-1]["exit"], 1)

    def test_a_step_allowed_to_fail_does_not_fail_the_action(self):
        self.runner.fail = {"/usr/local/bin/find-terminals"}
        r = self.req("flatpak.install", {"id": "org.kde.krita"})
        self.assertEqual(r["status"], "ok")

    def test_file_read_returns_the_content(self):
        r = self.req("file.read", {"path": "/etc/hosts"})
        self.assertEqual((r["status"], r["output"]), ("ok", "127.0.0.1 localhost\n"))
        self.assertEqual(self.req("file.read", {"path": "/etc/os-release"})["status"], "failed")       # allowlisted, but not there


class RefusalTests(Base):
    def test_strangers_and_bad_requests_are_refused_and_logged_without_running_anything(self):
        for r in (self.req("pkg.install", {"packages": ["htop"]}, peer=STRANGER), self.req("nope"), self.req("pkg.install", {"packages": ["-y"]}),
                  self.req("pkg.install", {"packages": ["xterm"]}), self.k.handle("not a dict", USER), self.k.handle({"action": ["x"]}, USER)):
            self.assertEqual(r["status"], "refused")
            self.assertTrue(r["reason"])
        self.assertEqual(self.runner.calls, [])
        self.assertTrue(all(x["decision"] == "refused" for x in self.records()))
        self.assertTrue(self.chain_ok())

    def test_the_reason_is_specific(self):
        self.assertIn("can't be installed", self.req("flatpak.install", {"id": "org.gnome.Terminal"})["reason"])
        self.assertIn("isn't allowed", self.req("pkg.upgrade", peer=STRANGER)["reason"])


class PassphraseTests(Base):
    def test_a_gated_action_first_asks_to_set_a_passphrase(self):
        r = self.req("os.rollback")
        self.assertEqual(r["status"], "needs_setup")
        self.assertEqual(self.runner.calls, [])
        self.assertEqual(self.records()[-1]["decision"], "passphrase-required")

    def test_set_then_unlock_then_run_and_the_session_covers_the_next_request(self):
        self.assertEqual(self.k.set_passphrase(USER, "blue-heron-42")["status"], "ok")
        self.assertEqual(self.req("os.rollback")["status"], "ok")                 # setting it opened a session for this program
        self.sessions.revoke()
        self.assertEqual(self.req("os.rollback")["status"], "needs_passphrase")
        self.assertEqual(self.req("os.rollback", passphrase="blue-heron-42")["status"], "ok")
        before = sum(1 for c in self.runner.calls if c[0][0] == "bootc")
        self.assertEqual(self.req("os.rollback")["status"], "ok")                 # no passphrase needed again
        self.assertEqual(sum(1 for c in self.runner.calls if c[0][0] == "bootc"), before + 1)

    def test_a_session_belongs_to_one_program_and_ends_after_it_idles(self):
        self.k.set_passphrase(USER, "blue-heron-42")
        self.assertEqual(self.req("os.rollback", peer=OTHER_APP)["status"], "needs_passphrase")
        self.t[0] += 5000                                                            # (the session clock is separate: use it)
        self.sessions.now = lambda: 10_000_000.0
        self.assertEqual(self.req("os.rollback")["status"], "needs_passphrase")

    def test_wrong_locked_and_the_passphrase_never_appears_in_the_log(self):
        self.k.set_passphrase(USER, "blue-heron-42")
        self.sessions.revoke()
        r = self.req("os.rollback", passphrase="wrong-guess-1")
        self.assertEqual(r["status"], "wrong_passphrase")
        for _ in range(pp.MAX_FAILS):
            r = self.req("os.rollback", passphrase="wrong-guess-1")
        self.assertEqual(r["status"], "locked")
        self.assertEqual(self.req("os.rollback", passphrase="blue-heron-42")["status"], "locked")     # right one refused while locked
        self.assertEqual(self.runner.calls, [])
        raw = open(self.audit.path).read()
        for secret in ("blue-heron-42", "wrong-guess-1"):
            self.assertNotIn(secret, raw)
        self.assertIn("passphrase-wrong", raw)

    def test_changing_the_passphrase_ends_other_sessions(self):
        self.k.set_passphrase(USER, "blue-heron-42")
        self.k.set_passphrase(OTHER_APP, "new-secret-77", old="blue-heron-42")
        self.assertEqual(self.req("os.rollback")["status"], "needs_passphrase")       # USER's session was revoked
        self.assertEqual(self.req("os.rollback", peer=OTHER_APP)["status"], "ok")
        self.assertEqual(self.k.set_passphrase(USER, "abc")["status"], "refused")       # weak
        self.assertEqual(self.k.set_passphrase(STRANGER, "blue-heron-99")["status"], "refused")

    def test_a_damaged_passphrase_file_blocks_gated_actions_but_not_automatic_ones(self):
        self.k.set_passphrase(USER, "blue-heron-42")
        open(self.pass_.path, "w").write("garbage")
        self.sessions.revoke()
        self.assertEqual(self.req("os.rollback", passphrase="blue-heron-42")["status"], "damaged")
        self.assertEqual(self.req("pkg.upgrade")["status"], "ok")

    def test_run_needs_the_passphrase_and_never_a_shell(self):
        self.k.set_passphrase(USER, "blue-heron-42")
        self.assertEqual(self.req("run", {"argv": ["/usr/bin/true"]})["status"], "ok")
        self.assertEqual(self.req("run", {"argv": ["bash", "-c", "id"]})["status"], "refused")        # not even with the passphrase
        self.assertEqual(self.req("file.write", {"path": "/etc/hosts", "content": "x\n"})["status"], "ok")
        self.assertEqual(self.runner.files["/etc/hosts"], "x\n")


class FailClosedTests(Base):
    def test_if_the_decision_cannot_be_logged_nothing_runs(self):
        class Broken(ka.AuditLog):
            def append(self, *a, **k):
                raise ka.AuditError("disk full")
        self.k.audit = Broken(os.path.join(self.dir, "b.log"))
        r = self.req("pkg.install", {"packages": ["htop"]})
        self.assertEqual(r["status"], "refused")
        self.assertIn("disk full", r["reason"])
        self.assertEqual(self.runner.calls, [])
        self.assertEqual(self.req("nope")["status"], "refused")            # refusing still works with a broken log

    def test_the_log_line_for_the_decision_exists_before_the_command_runs(self):
        seen = []
        original = self.runner.run

        def spy(argv, stdin=None, cwd=None):
            seen.append(len(self.records()))
            return original(argv, stdin, cwd)
        self.runner.run = spy
        self.req("pkg.upgrade")
        self.assertEqual(seen, [1])                                          # the decision line was already on disk


if __name__ == "__main__":
    unittest.main()
