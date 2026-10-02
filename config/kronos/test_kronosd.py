"""Integration test: the real daemon, the real root helper (run as you, with KRONOS_HELPER_TEST=1) and the real client, over a real unix
socket, with fake `rpm-ostree`/`systemctl`/`bootc` programs on PATH. Nothing here needs root and nothing touches the system."""
import json
import os
import sys
import tempfile
import threading
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))
import cosmos_kronos as ck
import kronos_audit as ka
import kronos_helper as helper
import kronosd


def fake_tools(dirpath, names):
    for name in names:
        path = os.path.join(dirpath, name)
        with open(path, "w") as fh:
            fh.write('#!/bin/sh\necho "fake %s $@"\n[ -e "$(dirname "$0")/fail" ] && exit 3\nexit 0\n' % name)
        os.chmod(path, 0o755)


class Live(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.mkdtemp()
        cls.bin = os.path.join(cls.dir, "bin")
        os.makedirs(cls.bin)
        fake_tools(cls.bin, ["rpm-ostree", "systemctl", "bootc", "flatpak", "firewall-cmd", "journalctl", "echo-tool", "ls"])
        env = {"PATH": cls.bin + ":/usr/bin:/bin", "KRONOS_HELPER_TEST": "1", "KRONOS_TEST_PATH": cls.bin + ":/usr/bin:/bin", "LANG": "C.UTF-8"}
        cmd = [sys.executable, os.path.join(HERE, "kronos_helper.py")]
        cls.runner = kronosd.HelperRunner(command=cmd, env=env)
        helper.ENV = {"PATH": cls.bin + ":/usr/bin:/bin", "LANG": "C.UTF-8", "HOME": cls.dir}
        cls.audit_path = os.path.join(cls.dir, "log", "audit.log")
        cls.daemon = kronosd.build(state_dir=os.path.join(cls.dir, "state"), audit_path=cls.audit_path, runner=cls.runner,
                                   clients={os.getuid()}, iterations=1000, policy={}, app_check=lambda kind, name: (True, ""))
        kronosd.Handler.daemon_obj = cls.daemon
        cls.sock_path = os.path.join(cls.dir, "k.sock")
        cls.server = kronosd.Server(path=cls.sock_path)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        os.environ["KRONOS_SOCKET"] = cls.sock_path

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        self._stderr = sys.stderr
        sys.stderr = open(os.devnull, "w")           # the journal copy of each audit line
        self.daemon.k.sessions.revoke()
        self.daemon.k.pass_.reset()

    def tearDown(self):
        sys.stderr.close()
        sys.stderr = self._stderr

    def prompt_script(self, *answers):
        given = list(answers)
        calls = []

        def prompt(mode, text, error=""):
            calls.append((mode, text, error))
            return given.pop(0) if given else None
        prompt.calls = calls
        return prompt


class FlowTests(Live):
    def test_an_automatic_action_streams_output_and_is_logged(self):
        seen = []
        r = ck.ask("pkg.install", {"packages": ["htop"]}, on_output=seen.append)
        self.assertEqual((r["status"], r["exit"]), ("ok", 0))
        self.assertIn("fake rpm-ostree install --idempotent --apply-live htop", "".join(seen))
        self.assertTrue(ka.AuditLog.verify(self.daemon.k.audit.files())[0])
        rows = self.daemon.k.audit.read_recent(5)
        self.assertEqual({r_["event"] for r_ in rows[:2]}, {"decision", "result"})
        self.assertEqual(rows[0]["peer"]["uid"], os.getuid())

    def test_a_failing_command_reports_its_exit_status(self):
        marker = os.path.join(self.bin, "fail")
        open(marker, "w").close()
        try:
            r = ck.ask("pkg.upgrade")
        finally:
            os.unlink(marker)
        self.assertEqual(r["status"], "failed")
        self.assertEqual(ck.exit_code(r), 3)

    def test_the_first_passphrase_action_asks_you_to_create_one_then_carries_on(self):
        prompt = self.prompt_script("correct horse staple")
        r = ck.ask("run", {"argv": ["echo-tool", "hello"]}, prompt=prompt)
        self.assertEqual(r["status"], "ok")
        self.assertIn("fake echo-tool hello", r["output"])
        self.assertEqual([c[0] for c in prompt.calls], ["create"])
        # the session it opened means the next one doesn't ask again
        again = self.prompt_script()
        self.assertEqual(ck.ask("run", {"argv": ["echo-tool", "again"]}, prompt=again)["status"], "ok")
        self.assertEqual(again.calls, [])

    def test_locked_again_asks_for_the_passphrase_and_a_wrong_one_is_reported(self):
        self.daemon.k.pass_.set("correct horse staple")
        self.daemon.k.sessions.revoke()
        prompt = self.prompt_script("wrong one", "correct horse staple")
        r = ck.ask("run", {"argv": ["echo-tool", "x"]}, prompt=prompt)
        self.assertEqual(r["status"], "ok")
        self.assertEqual([c[0] for c in prompt.calls], ["unlock", "unlock"])
        self.assertIn("wrong", prompt.calls[1][2].lower())

    def test_cancelling_the_prompt_does_nothing(self):
        self.daemon.k.pass_.set("correct horse staple")
        r = ck.ask("run", {"argv": ["echo-tool", "x"]}, prompt=self.prompt_script())
        self.assertEqual(r["status"], "refused")

    def test_refused_things_stay_refused(self):
        r = ck.ask("run", {"argv": ["bash", "-c", "id"]}, prompt=self.prompt_script("x"))
        self.assertEqual(r["status"], "refused")
        self.assertEqual(ck.exit_code(r), 126)
        self.assertEqual(ck.ask("no.such.action")["status"], "refused")

    def test_status_lock_and_audit_verify(self):
        self.daemon.k.pass_.set("correct horse staple")
        self.assertEqual(ck.call({"op": "status"})["passphrase"], "set")
        ck.ask("run", {"argv": ["echo-tool"]}, prompt=self.prompt_script("correct horse staple"))
        self.assertTrue(ck.call({"op": "status"})["unlocked"])
        ck.call({"op": "lock"})
        self.assertFalse(ck.call({"op": "status"})["unlocked"])
        self.assertTrue(ck.call({"op": "audit_verify"})["intact"])
        self.assertTrue(ck.call({"op": "audit", "limit": 3})["entries"])

    def test_the_helper_rechecks_even_if_the_daemon_were_wrong(self):
        """Ask the helper directly for something kronos_actions refuses: it says no on its own."""
        r = self.runner.run_action("run", {"argv": ["bash", "-c", "id"]})
        self.assertEqual(r[0], 1)
        self.assertIn("can't be started", r[1])

    def test_a_stranger_uid_is_refused(self):
        self.daemon.k.clients = {os.getuid() + 12345}
        try:
            self.assertEqual(ck.call({"op": "request", "action": "pkg.upgrade"})["status"], "refused")
            self.assertEqual(ck.call({"op": "status"})["status"], "refused")
        finally:
            self.daemon.k.clients = {os.getuid()}

    def test_no_service_means_unavailable_and_nothing_done(self):
        r = ck.ask("pkg.upgrade", path=os.path.join(self.dir, "nope.sock"))
        self.assertEqual((r["status"], ck.exit_code(r)), ("unavailable", 125))

    def test_garbage_gets_a_refusal_not_a_crash(self):
        import socket
        with socket.socket(socket.AF_UNIX) as s:
            s.connect(self.sock_path)
            s.sendall(b"this is not json\n")
            s.shutdown(socket.SHUT_WR)
            self.assertEqual(json.loads(s.makefile().readline())["status"], "refused")

    def test_the_kronos_command_maps_streams_and_sets_the_exit_status(self):
        import subprocess
        env = dict(os.environ, KRONOS_SOCKET=self.sock_path)
        ran = subprocess.run([sys.executable, os.path.join(HERE, "kronos"), "exec", "--", "dnf", "install", "-y", "htop"], capture_output=True, text=True, env=env)
        self.assertEqual(ran.returncode, 0, ran.stderr[-300:])
        self.assertIn("fake rpm-ostree install --idempotent --apply-live htop", ran.stdout)
        refused = subprocess.run([sys.executable, os.path.join(HERE, "kronos"), "exec", "--", "bash", "-c", "id"], capture_output=True, text=True, env=env, input="")
        self.assertEqual(refused.returncode, 126)
        self.assertIn("can't be started", refused.stderr)
        gone = subprocess.run([sys.executable, os.path.join(HERE, "kronos"), "status"], capture_output=True, text=True,
                              env=dict(os.environ, KRONOS_SOCKET=os.path.join(self.dir, "nope.sock")))
        self.assertEqual(gone.returncode, 125)

    def test_the_sessions_belong_to_one_process(self):
        import kronos_core as kc
        base = {"uid": 1, "exe": "/usr/bin/python3", "script": "/usr/local/bin/cosmos-shop", "pid": 100, "start": "5000"}
        self.assertEqual(kc.peer_key(base), kc.peer_key(dict(base, script="/tmp/evil.py")))          # what a program says about itself is not the key
        self.assertNotEqual(kc.peer_key(base), kc.peer_key(dict(base, pid=101)))                    # another process
        self.assertNotEqual(kc.peer_key(base), kc.peer_key(dict(base, start="5001")))               # the same pid reused later

    def test_stdin_travels_with_a_run_request(self):
        import subprocess
        self.daemon.k.pass_.set("correct horse staple")
        agent = FakeAgent(self.sock_path, ["correct horse staple"])
        try:
            ran = subprocess.run([sys.executable, os.path.join(HERE, "kronos"), "exec", "--", "echo-tool"], capture_output=True, text=True,
                                  env=dict(os.environ, KRONOS_SOCKET=self.sock_path), input="hello\n")
            self.assertEqual(ran.returncode, 0, ran.stderr[-300:])
            self.assertIn("fake echo-tool", ran.stdout)
            self.assertEqual(len(agent.questions), 1)
        finally:
            agent.close()

    def test_peer_of_reads_pid_and_start_time_from_the_kernel(self):
        import socket
        a, b = socket.socketpair(socket.AF_UNIX)
        try:
            peer = kronosd.peer_of(a)
        finally:
            a.close()
            b.close()
        self.assertEqual((peer["uid"], peer["pid"]), (os.getuid(), os.getpid()))
        self.assertTrue(peer["start"].isdigit())


class FakeAgent:
    """Stands in for kronos-agent: connects, announces itself, answers each question from `answers` (None = cancel)."""

    def __init__(self, sock_path, answers):
        import socket
        self.answers, self.questions = list(answers), []
        self.sock = socket.socket(socket.AF_UNIX)
        self.sock.connect(sock_path)
        self.sock.sendall(json.dumps({"op": "agent"}).encode() + b"\n")
        self.f = self.sock.makefile("rw", encoding="utf-8")
        assert json.loads(self.f.readline())["event"] == "agent-attached"
        self.thread = threading.Thread(target=self.loop, daemon=True)
        self.thread.start()

    def loop(self):
        for line in self.f:
            q = json.loads(line)
            self.questions.append(q)
            answer = self.answers.pop(0) if self.answers else None
            self.f.write(json.dumps({"id": q["id"], "passphrase": answer}) + "\n")
            self.f.flush()

    def close(self):
        try:
            self.sock.shutdown(2)
        except OSError:
            pass
        self.f.close()
        self.sock.close()


class AgentTests(Live):
    def setUp(self):
        super().setUp()
        self.agents = []

    def tearDown(self):
        for a in self.agents:
            a.close()
        import time
        for _ in range(50):                                   # let the daemon notice the agent left
            if not self.daemon.hub.connected():
                break
            time.sleep(0.02)
        super().tearDown()

    def agent(self, *answers):
        a = FakeAgent(self.sock_path, answers)
        self.agents.append(a)
        import time
        for _ in range(50):
            if self.daemon.hub.connected():
                break
            time.sleep(0.02)
        return a

    def test_the_daemon_asks_the_person_itself_and_the_asker_never_sees_the_passphrase(self):
        agent = self.agent("correct horse staple")
        r = ck.ask("run", {"argv": ["echo-tool", "hi"]}, prompt=lambda *a: self.fail("the requesting program was asked for the passphrase"))
        self.assertEqual(r["status"], "ok")
        q = agent.questions[0]
        self.assertEqual(q["mode"], "create")                                 # nothing was set yet
        self.assertIn("Run echo-tool hi", q["text"])
        self.assertIn(f"process {os.getpid()}", q["program"])                # what the KERNEL says is asking
        self.assertNotIn("correct horse staple", json.dumps(r))
        log = open(self.audit_path).read()
        self.assertNotIn("correct horse staple", log)
        # the session is this process's now, so a second request asks nobody
        self.assertEqual(ck.ask("run", {"argv": ["echo-tool", "again"]})["status"], "ok")
        self.assertEqual(len(agent.questions), 1)

    def test_unlock_mode_wrong_then_right(self):
        self.daemon.k.pass_.set("correct horse staple")
        agent = self.agent("nope nope nope", "correct horse staple")
        r = ck.ask("run", {"argv": ["echo-tool"]})
        self.assertEqual(r["status"], "ok")
        self.assertEqual([q["mode"] for q in agent.questions], ["unlock", "unlock"])
        self.assertIn("wrong", agent.questions[1]["error"].lower())

    def test_cancelling_at_the_agent_refuses(self):
        self.daemon.k.pass_.set("correct horse staple")
        self.agent(None)
        r = ck.ask("run", {"argv": ["echo-tool"]})
        self.assertEqual(r["status"], "refused")
        self.assertIn("Cancelled", r["reason"])

    def test_no_agent_falls_back_to_the_asker_prompting_as_over_ssh(self):
        self.daemon.k.pass_.set("correct horse staple")
        prompt = self.prompt_script("correct horse staple")
        self.assertEqual(ck.ask("run", {"argv": ["echo-tool"]}, prompt=prompt)["status"], "ok")
        self.assertEqual([c[0] for c in prompt.calls], ["unlock"])

    def test_an_agent_that_disappears_mid_conversation_means_no_agent(self):
        self.daemon.k.pass_.set("correct horse staple")
        a = self.agent()
        a.sock.shutdown(2)                                                  # dies before answering
        prompt = self.prompt_script("correct horse staple")
        self.assertEqual(ck.ask("run", {"argv": ["echo-tool"]}, prompt=prompt)["status"], "ok")

    def test_a_stranger_cannot_be_the_agent(self):
        import socket
        self.daemon.k.clients = {os.getuid() + 999}
        try:
            with socket.socket(socket.AF_UNIX) as s:
                s.connect(self.sock_path)
                s.sendall(json.dumps({"op": "agent"}).encode() + b"\n")
                self.assertEqual(json.loads(s.makefile().readline())["status"], "refused")
            self.assertFalse(self.daemon.hub.connected())
        finally:
            self.daemon.k.clients = {os.getuid()}


class MappingTests(unittest.TestCase):
    def m(self, *argv):
        return ck.map_command(list(argv))

    def test_package_commands(self):
        self.assertEqual(self.m("dnf", "install", "-y", "htop", "vim"), ("pkg.install", {"packages": ["htop", "vim"]}))
        self.assertEqual(self.m("rpm-ostree", "install", "htop"), ("pkg.install", {"packages": ["htop"]}))
        self.assertEqual(self.m("dnf", "remove", "htop"), ("pkg.remove", {"packages": ["htop"]}))
        self.assertEqual(self.m("dnf", "upgrade"), ("pkg.upgrade", {}))
        self.assertEqual(self.m("dnf", "install", "./local.rpm")[0], "run")          # a file isn't a package name
        self.assertEqual(self.m("dnf", "install", "https://x/y.rpm")[0], "run")
        self.assertEqual(self.m("dnf", "search", "htop")[0], "run")

    def test_service_flatpak_bootc(self):
        self.assertEqual(self.m("systemctl", "restart", "sshd.service"), ("service.control", {"unit": "sshd.service", "verb": "restart"}))
        self.assertEqual(self.m("systemctl", "enable", "--now", "sshd.service")[0], "run")
        self.assertEqual(self.m("flatpak", "install", "flathub", "org.kde.krita"), ("flatpak.install", {"id": "org.kde.krita"}))
        self.assertEqual(self.m("flatpak", "install", "--user", "org.kde.krita")[0], "run")
        self.assertEqual(self.m("bootc", "upgrade", "--check"), ("os.upgrade", {"method": "bootc", "check": True}))
        self.assertEqual(self.m("bootc", "upgrade", "--apply"), ("os.upgrade", {"method": "bootc", "apply": True}))
        self.assertEqual(self.m("bootc", "rollback", "--apply"), ("os.rollback", {"apply": True}))
        self.assertEqual(self.m("bootc", "switch", "ghcr.io/me/cosmos:latest"), ("os.switch", {"ref": "ghcr.io/me/cosmos:latest"}))
        self.assertEqual(self.m("cat", "/etc/hosts"), ("file.read", {"path": "/etc/hosts"}))
        self.assertEqual(self.m("journalctl", "-b", "-u", "greetd", "-n", "50", "--no-pager"), ("log.read", {"lines": 50, "boot": True, "unit": "greetd.service"}))
        self.assertEqual(self.m("journalctl", "--vacuum-size=1M")[0], "run")

    def test_anything_else_is_run_with_the_exact_argv(self):
        self.assertEqual(self.m("modprobe", "-r", "foo"), ("run", {"argv": ["modprobe", "-r", "foo"]}))


if __name__ == "__main__":
    unittest.main(verbosity=1)
