"""Tests for the kronos audit log: chain, tamper detection, rotation, fail-closed, redaction."""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kronos_audit as ka


class AuditTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "log", "audit.log")
        self.t = [1000.0]
        self.log = ka.AuditLog(self.path, now=lambda: self.t[0])

    def lines(self, path=None):
        return [l for l in open(path or self.path).read().split("\n") if l]

    def test_records_are_chained_and_verify(self):
        for i in range(5):
            self.log.append("decision", id=str(i), action="pkg.install", decision="auto")
        recs = [json.loads(l) for l in self.lines()]
        self.assertEqual([r["seq"] for r in recs], [1, 2, 3, 4, 5])
        self.assertEqual(recs[0]["prev"], ka.GENESIS)
        self.assertEqual(recs[1]["prev"], ka.line_hash(self.lines()[0]))
        self.assertEqual(ka.AuditLog.verify([self.path]), (True, []))

    def test_file_mode_and_a_restart_continues_the_chain(self):
        self.log.append("decision", id="a")
        self.assertEqual(oct(os.stat(self.path).st_mode & 0o777), "0o640")
        again = ka.AuditLog(self.path, now=lambda: self.t[0])
        rec = again.append("decision", id="b")
        self.assertEqual(rec["seq"], 2)
        self.assertEqual(ka.AuditLog.verify([self.path]), (True, []))

    def test_editing_deleting_or_reordering_a_line_is_detected(self):
        for i in range(6):
            self.log.append("decision", id=str(i), decision="auto")
        original = self.lines()

        def rewrite(lines):
            with open(self.path, "w") as fh:
                fh.write("\n".join(lines) + "\n")
        edited = list(original)
        edited[2] = edited[2].replace('"auto"', '"refused"')
        rewrite(edited)
        self.assertFalse(ka.AuditLog.verify([self.path])[0])
        rewrite(original[:2] + original[3:])                      # a line removed
        self.assertFalse(ka.AuditLog.verify([self.path])[0])
        rewrite(original[:2] + [original[3], original[2]] + original[4:])       # two swapped
        self.assertFalse(ka.AuditLog.verify([self.path])[0])
        rewrite(original)
        self.assertTrue(ka.AuditLog.verify([self.path])[0])
        rewrite(original + [json.dumps({"seq": 7, "prev": "x"})])              # a forged line on the end
        self.assertFalse(ka.AuditLog.verify([self.path])[0])

    def test_cosmetic_edits_are_caught_too(self):
        self.log.append("decision", id="a")
        text = open(self.path).read()
        open(self.path, "w").write(text.replace(",", ", ", 1))                # same JSON, different text
        self.assertFalse(ka.AuditLog.verify([self.path])[0])

    def test_rotation_keeps_the_chain_across_files(self):
        small = ka.AuditLog(self.path, now=lambda: self.t[0], max_bytes=600, keep=3)
        for i in range(30):
            small.append("decision", id=str(i), action="pkg.install", decision="auto", reason="x" * 40)
        files = small.files()
        self.assertGreater(len(files), 1)
        self.assertLessEqual(len(files), 4)
        ok, problems = ka.AuditLog.verify(files)                # oldest kept file is the anchor
        self.assertTrue(ok, problems)
        self.assertEqual(json.loads(self.lines(files[-1])[-1])["seq"], 30)
        again = ka.AuditLog(self.path, max_bytes=600, keep=3)   # a restart right after a rotation still chains
        again.append("decision", id="z")
        self.assertTrue(ka.AuditLog.verify(again.files())[0])

    def test_protect_hooks_are_called_around_rotation(self):
        calls = []
        log = ka.AuditLog(self.path, max_bytes=200, protect=lambda p: calls.append(("+a", os.path.basename(p))),
                          unprotect=lambda p: calls.append(("-a", os.path.basename(p))))
        for i in range(6):
            log.append("decision", id=str(i), reason="y" * 60)
        self.assertIn(("+a", "audit.log"), calls)
        self.assertIn(("-a", "audit.log"), calls)

    def test_fail_closed(self):
        os.makedirs(os.path.dirname(self.path))
        os.chmod(os.path.dirname(self.path), 0o500)
        try:
            if os.access(os.path.dirname(self.path), os.W_OK):
                self.skipTest("running as a user that ignores directory permissions")
            log = ka.AuditLog(self.path)
            with self.assertRaises(ka.AuditError):
                log.append("decision", id="a")
        finally:
            os.chmod(os.path.dirname(self.path), 0o700)

    def test_a_damaged_tail_stops_appending(self):
        self.log.append("decision", id="a")
        with open(self.path, "a") as fh:
            fh.write("garbage that is not json\n")
        with self.assertRaises(ka.AuditError):
            ka.AuditLog(self.path)

    def test_secrets_and_bulk_data_never_reach_the_log(self):
        self.log.append("decision", id="a", args={"passphrase": "hunter2hunter2", "token": "t0k", "api_key": "k", "content": "line one\nline two",
                                                  "stdin": "s3cret input", "packages": ["htop"], "nested": {"client_secret": "zzz"}})
        raw = open(self.path).read()
        for leaked in ("hunter2hunter2", "t0k", "s3cret input", "line one", "zzz"):
            self.assertNotIn(leaked, raw)
        rec = json.loads(raw)
        self.assertEqual(rec["args"]["content"]["len"], len("line one\nline two"))
        self.assertEqual(rec["args"]["packages"], ["htop"])
        self.assertEqual(len(rec["args"]["content"]["sha256"]), 64)

    def test_long_values_are_trimmed(self):
        self.log.append("decision", id="a", reason="x" * 5000)
        self.assertLess(len(open(self.path).read()), 1000)

    def test_read_recent_filters_and_orders_newest_first(self):
        for i, d in enumerate(["auto", "refused", "auto", "refused", "auto"]):
            self.log.append("decision", id=str(i), action="pkg.install" if d == "auto" else "run", decision=d)
        rows = self.log.read_recent(10)
        self.assertEqual([r["id"] for r in rows], ["4", "3", "2", "1", "0"])
        self.assertEqual([r["id"] for r in self.log.read_recent(10, decision="refused")], ["3", "1"])
        self.assertEqual([r["id"] for r in self.log.read_recent(2)], ["4", "3"])
        self.assertEqual(len(self.log.read_recent(10, action="run")), 2)


if __name__ == "__main__":
    unittest.main()
