"""Tests for cosmos_remote: key validation, add/remove, fingerprints (checked against ssh-keygen when present)."""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cosmos_remote as cr

HAVE_KEYGEN = shutil.which("ssh-keygen") is not None


def make_key(directory, name, comment="me@laptop", kind="ed25519"):
    path = os.path.join(directory, name)
    subprocess.run(["ssh-keygen", "-q", "-t", kind, "-N", "", "-C", comment, "-f", path], check=True)
    return open(path + ".pub").read().strip(), path


@unittest.skipUnless(HAVE_KEYGEN, "needs ssh-keygen to make sample keys")
class KeyTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.auth = os.path.join(self.dir, "home", ".ssh", "authorized_keys")
        self.k1, self.p1 = make_key(self.dir, "k1", "alice@laptop")
        self.k2, self.p2 = make_key(self.dir, "k2", "bob@phone", kind="ecdsa")

    def test_fingerprint_matches_ssh_keygen(self):
        for line, path in ((self.k1, self.p1), (self.k2, self.p2)):
            want = subprocess.run(["ssh-keygen", "-lf", path + ".pub"], capture_output=True, text=True).stdout.split()[1]
            self.assertEqual(cr.fingerprint(cr.parse_key(line)["blob"]), want)

    def test_add_list_dedupe_remove_and_permissions(self):
        fp1, added = cr.add_key(self.auth, self.k1)
        self.assertTrue(added)
        self.assertEqual(cr.add_key(self.auth, self.k1), (fp1, False))                       # the same key again: not duplicated
        fp2, _ = cr.add_key(self.auth, self.k2 + "\n")
        keys = cr.load_keys(self.auth)
        self.assertEqual([k["fingerprint"] for k in keys], [fp1, fp2])
        self.assertEqual([k["comment"] for k in keys], ["alice@laptop", "bob@phone"])
        self.assertEqual(oct(os.stat(self.auth).st_mode & 0o777), "0o600")
        self.assertEqual(oct(os.stat(os.path.dirname(self.auth)).st_mode & 0o777), "0o700")
        self.assertTrue(cr.remove_key(self.auth, fp1))
        self.assertFalse(cr.remove_key(self.auth, fp1))
        self.assertEqual([k["fingerprint"] for k in cr.load_keys(self.auth)], [fp2])

    def test_comments_and_hand_written_lines_survive(self):
        os.makedirs(os.path.dirname(self.auth))
        open(self.auth, "w").write("# my keys\n\n" + self.k1 + "\n# keep me\n")
        fp2, _ = cr.add_key(self.auth, self.k2)
        cr.remove_key(self.auth, fp2)
        text = open(self.auth).read()
        self.assertIn("# my keys", text)
        self.assertIn("# keep me", text)
        self.assertIn("alice@laptop", text)

    def test_only_plain_public_keys_are_accepted(self):
        blob = self.k1.split()[1]
        for bad, why in (("", "one key"), ("hello world", "public key"), (f"command=\"rm -rf /\" {self.k1}", "public key"),
                         (f"from=\"1.2.3.4\",no-pty {self.k1}", "public key"), ("ssh-ed25519", "missing"),
                         ("ssh-ed25519 !!!notbase64!!!", "valid"), (f"ssh-rsa {blob}", "match"),
                         (self.k1 + "\n" + self.k2, "one key"), (open(self.p1).read(), "PRIVATE key"), (None, "text"), ("ssh-ed25519 " + "A" * 9000, "too long")):
            with self.assertRaises(cr.KeyError_, msg=repr(bad)[:50]) as cm:
                cr.parse_key(bad)
            self.assertIn(why, str(cm.exception), repr(bad)[:50])
        self.assertFalse(os.path.exists(self.auth))                                         # nothing was written by the rejected ones

    def test_a_private_key_is_refused_with_a_warning(self):
        with self.assertRaises(cr.KeyError_) as cm:
            cr.parse_key(open(self.p1).read())
        self.assertIn("Never paste a private key", str(cm.exception))

    def test_host_fingerprints(self):
        hostdir = os.path.join(self.dir, "etc-ssh")
        os.makedirs(hostdir)
        _k, p = make_key(hostdir, "ssh_host_ed25519_key", "root@host")
        got = cr.host_fingerprints(hostdir)
        self.assertEqual(got[0][0], "ssh-ed25519")
        self.assertTrue(got[0][1].startswith("SHA256:"))
        self.assertEqual(cr.host_fingerprints(os.path.join(self.dir, "none")), [])


class MiscTests(unittest.TestCase):
    def test_sshd_state_and_command(self):
        class R:
            def __init__(self, out):
                self.stdout = out
        state = cr.sshd_state(lambda cmd, **kw: R("active\n" if cmd[1] == "is-active" else "enabled\n"))
        self.assertEqual(state, ("active", True))
        self.assertEqual(cr.sshd_state(lambda cmd, **kw: R("inactive\n" if cmd[1] == "is-active" else "disabled\n")), ("inactive", False))
        def broken(*a, **k):
            raise OSError("no systemctl")
        self.assertEqual(cr.sshd_state(broken), ("unknown", False))
        calls = []
        self.assertEqual(cr.set_sshd(True, lambda action, args: (calls.append((action, args["verb"])), {"status": "ok"})[1]), (True, ""))
        self.assertEqual(calls, [("service.control", "enable"), ("service.control", "start")])
        calls.clear()
        self.assertEqual(cr.set_sshd(False, lambda action, args: (calls.append(args["verb"]), {"status": "ok"})[1]), (True, ""))
        self.assertEqual(calls, ["stop", "disable"])
        # a refusal (cancelled passphrase) stops at the first step and says why
        self.assertEqual(cr.set_sshd(True, lambda a, args: {"status": "refused", "reason": "Cancelled."}), (False, "Cancelled."))

    def test_connect_hints_mention_the_tunnel(self):
        h = cr.connect_hints()
        self.assertTrue(h["ssh"].startswith("ssh user@"))
        self.assertIn("-L 3000:localhost:3000", h["webtop_tunnel"])

    def test_the_shipped_sshd_config_is_key_only(self):
        text = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "ssh", "50-cosmos.conf")).read()
        for line in ("PasswordAuthentication no", "PermitRootLogin no", "AllowUsers user", "KbdInteractiveAuthentication no"):
            self.assertIn(line, text)


if __name__ == "__main__":
    unittest.main()
