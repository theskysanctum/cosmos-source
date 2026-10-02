import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "kronos"))
import cosmos_recovery as rec
import kronos_audit as ka
import kronos_passphrase as pp


class Recovery(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()

    def test_reset_passphrase_forgets_it_and_the_lockout_but_keeps_and_extends_the_audit_log(self):
        state, log = os.path.join(self.d, "state"), os.path.join(self.d, "log", "audit.log")
        p = pp.Passphrase(os.path.join(state, "passphrase.json"), iterations=1000)
        p.set("correct horse staple")
        for _ in range(pp.MAX_FAILS):
            p.verify("nope nope")
        al = ka.AuditLog(log)
        al.append("decision", action="run")
        removed = rec.reset_kronos_passphrase(state, log)
        self.assertTrue(removed)
        p2 = pp.Passphrase(os.path.join(state, "passphrase.json"), iterations=1000)
        self.assertEqual(p2.status(), "unset")
        p2.set("another good phrase")                                 # not locked out any more
        ok, problems = ka.AuditLog.verify(al.files())
        self.assertTrue(ok, problems)
        last = ka.AuditLog(log).read_recent(1)[0]
        self.assertEqual(last["decision"], "passphrase-reset")

    def test_reset_still_works_when_the_audit_log_is_damaged(self):
        state, log = os.path.join(self.d, "state"), os.path.join(self.d, "audit.log")
        os.makedirs(state)
        open(os.path.join(state, "passphrase.json"), "w").write("{garbage")
        open(log, "w").write("not json\n")
        rec.reset_kronos_passphrase(state, log)
        self.assertFalse(os.path.exists(os.path.join(state, "passphrase.json")))

    def test_clear_pin(self):
        prof = os.path.join(self.d, "profiles", "sam")
        os.makedirs(prof)
        json.dump({"name": "Sam", "pin": {"salt": "00", "hash": "00", "iterations": 1}}, open(os.path.join(prof, "profile.json"), "w"))
        self.assertEqual(rec.list_profiles(os.path.join(self.d, "profiles")), [("sam", "Sam", True)])
        rec.clear_profile_pin("sam", os.path.join(self.d, "profiles"))
        self.assertEqual(rec.list_profiles(os.path.join(self.d, "profiles")), [("sam", "Sam", False)])
        with self.assertRaises(rec.RecoveryError):
            rec.clear_profile_pin("../x", os.path.join(self.d, "profiles"))

    def test_factory_reset_needs_the_word_and_restores_skel(self):
        home, skel = os.path.join(self.d, "home"), os.path.join(self.d, "skel")
        os.makedirs(os.path.join(home, "Documents"))
        open(os.path.join(home, "Documents", "a.txt"), "w").write("mine")
        os.makedirs(os.path.join(skel, ".config"))
        open(os.path.join(skel, ".config", "x"), "w").write("fresh")
        with self.assertRaises(rec.RecoveryError):
            rec.factory_reset_user_data("yes", home, skel)
        self.assertTrue(os.path.exists(os.path.join(home, "Documents", "a.txt")))
        rec.factory_reset_user_data("RESET", home, skel)
        self.assertFalse(os.path.exists(os.path.join(home, "Documents")))
        self.assertEqual(open(os.path.join(home, ".config", "x")).read(), "fresh")

    def test_factory_reset_refuses_dangerous_folders(self):
        for bad in ("/", "/home", "/etc", ""):
            with self.assertRaises(rec.RecoveryError):
                rec.factory_reset_user_data("RESET", bad, "/nonexistent")

    def test_rollback_reports_failure(self):
        class R:
            returncode, stdout, stderr = 1, "", "no rollback target"
        with self.assertRaises(rec.RecoveryError):
            rec.rollback_os(run=lambda *a, **k: R())


if __name__ == "__main__":
    unittest.main()
