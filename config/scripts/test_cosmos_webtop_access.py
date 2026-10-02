"""Tests for cosmos_webtop_access: the password rules, what gets written, that it can't be on without a password, and (when the
quadlet generator is available) that the user's override really replaces the shipped definition."""
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import cosmos_webtop_access as wa

BASE = open(os.path.join(HERE, "..", "webtop", "cosmos-webtop.container"), encoding="utf-8").read()
GENERATOR = "/usr/lib/systemd/user-generators/podman-user-generator"


class PasswordTests(unittest.TestCase):
    def test_rules(self):
        self.assertEqual(wa.validate_password("blue-heron-4242"), "blue-heron-4242")
        for bad in ("short", "", None, "a" * 129, "line\nbreak-password", "nul\0password1", "aaaaaaaaaaaa", "password123", "Administrator"):
            with self.assertRaises(wa.AccessError, msg=repr(bad)):
                wa.validate_password(bad)


class FileTests(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp()

    def test_activate_writes_both_files_with_the_right_modes(self):
        p = wa.activate("blue-heron-4242", home=self.home, base_text=BASE)
        self.assertEqual(open(p["auth"]).read(), "CUSTOM_USER=user\nPASSWORD=blue-heron-4242\n")
        self.assertEqual(stat.S_IMODE(os.stat(p["auth"]).st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(os.stat(os.path.dirname(p["auth"])).st_mode), 0o700)
        text = open(p["override"]).read()
        self.assertIn("PublishPort=127.0.0.1:3000:3000", text)              # the plain port stays local
        self.assertIn("PublishPort=0.0.0.0:3001:3001", text)                # only HTTPS is opened
        self.assertNotIn("0.0.0.0:3000", text)
        self.assertIn("EnvironmentFile=%h/.config/cosmos-webtop/auth.env", text)
        self.assertIn("Image=lscr.io/linuxserver/webtop", text)
        self.assertEqual(wa.state(self.home), {"network": True, "password_set": True})

    def test_a_bad_password_writes_nothing_and_it_is_never_on_without_one(self):
        with self.assertRaises(wa.AccessError):
            wa.activate("short", home=self.home, base_text=BASE)
        with self.assertRaises(wa.AccessError):
            wa.activate("blue-heron-4242", home=self.home, base_text="[Container]\nImage=x\n")            # unrecognised definition
        self.assertFalse(os.path.exists(wa.paths(self.home)["auth"]))
        self.assertFalse(os.path.exists(wa.paths(self.home)["override"]))
        p = wa.activate("blue-heron-4242", home=self.home, base_text=BASE)
        os.unlink(p["auth"])                                                  # the login file goes missing: not "network" any more
        self.assertFalse(wa.state(self.home)["network"])

    def test_changing_the_password_replaces_it(self):
        wa.activate("blue-heron-4242", home=self.home, base_text=BASE)
        wa.activate("green-otter-7777", home=self.home, base_text=BASE)
        self.assertIn("green-otter-7777", open(wa.paths(self.home)["auth"]).read())
        self.assertNotIn("blue-heron", open(wa.paths(self.home)["auth"]).read())

    def test_deactivate_forgets_the_password_and_restores_local_only(self):
        wa.activate("blue-heron-4242", home=self.home, base_text=BASE)
        self.assertTrue(wa.deactivate(self.home))
        self.assertEqual(wa.state(self.home), {"network": False, "password_set": False})
        self.assertFalse(wa.deactivate(self.home))                              # nothing left to remove: fine

    def test_odd_characters_survive_unchanged(self):
        pw = 'p@ss w0rd"with$odd\\chars#'
        p = wa.activate(pw, home=self.home, base_text=BASE)
        self.assertEqual(open(p["auth"]).read().split("\n")[1], "PASSWORD=" + pw)

    def test_systemd_reload_and_restart(self):
        calls = []

        class R:
            def __init__(self, rc=0, err=""):
                self.returncode, self.stderr = rc, err
        self.assertEqual(wa.apply_systemd(lambda cmd, **kw: (calls.append(cmd), R())[1]), "")
        self.assertEqual([c[2] for c in calls], ["daemon-reload", "try-restart"])
        self.assertEqual(wa.apply_systemd(lambda cmd, **kw: R(1, "boom") if cmd[2] == "try-restart" else R()), "boom")


@unittest.skipUnless(os.path.exists(GENERATOR), "podman's quadlet generator isn't installed")
class GeneratorTests(unittest.TestCase):
    def generate(self, dirs):
        env = dict(os.environ, QUADLET_UNIT_DIRS=":".join(dirs))
        return subprocess.run([GENERATOR, "--user", "--dryrun"], env=env, capture_output=True, text=True).stdout

    def test_the_users_override_replaces_the_shipped_definition(self):
        home = tempfile.mkdtemp()
        p = wa.activate("blue-heron-4242", home=home, base_text=BASE)
        shipped = tempfile.mkdtemp()
        shutil.copy(os.path.join(HERE, "..", "webtop", "cosmos-webtop.container"), shipped)
        user_dir = os.path.dirname(p["override"])
        out = self.generate([user_dir, shipped])                               # the user's directory first, as quadlet searches it
        self.assertEqual(out.count("---cosmos-webtop.service---"), 1)
        self.assertRegex(out, r"--publish 0\.0\.0\.0:3001:3001")
        self.assertRegex(out, r"--publish 127\.0\.0\.1:3000:3000")
        self.assertIn("--env-file", out)
        self.assertNotRegex(out, r"--publish 0\.0\.0\.0:3000")
        local_only = self.generate([shipped])
        self.assertNotIn("0.0.0.0", local_only)                                 # without the override: local only, as shipped
        self.assertNotIn("--env-file", local_only)


if __name__ == "__main__":
    unittest.main()
