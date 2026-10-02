"""Tests for the kronos admin passphrase and sessions."""
import json
import os
import stat
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kronos_passphrase as kp


class Clock:
    def __init__(self):
        self.t = 1_000_000.0

    def __call__(self):
        return self.t


class PassphraseTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.clock = Clock()
        self.pp = kp.Passphrase(os.path.join(self.dir, "unlock.json"), now=self.clock, iterations=1000)   # fast for tests

    def test_unset_then_set_then_verify(self):
        self.assertEqual(self.pp.status(), "unset")
        self.assertFalse(self.pp.verify("anything"))
        self.pp.set("blue-heron-42")
        self.assertEqual(self.pp.status(), "set")
        self.assertTrue(self.pp.verify("blue-heron-42"))
        self.assertFalse(self.pp.verify("blue-heron-43"))

    def test_only_a_hash_is_stored_and_the_file_is_private(self):
        self.pp.set("blue-heron-42")
        raw = open(self.pp.path).read()
        self.assertNotIn("blue-heron-42", raw)
        self.assertEqual(stat.S_IMODE(os.stat(self.pp.path).st_mode), 0o600)
        data = json.loads(raw)
        self.assertEqual(len(bytes.fromhex(data["salt"])), 16)
        self.pp.set("another-one-9", old="blue-heron-42")
        self.assertNotEqual(json.loads(open(self.pp.path).read())["salt"], data["salt"])      # a fresh salt each time

    def test_weak_ones_are_refused(self):
        for bad in ("", "abc", "aaaaaa", "121212"[:2], "password", "Qwerty123", "abab", None):
            with self.assertRaises(kp.Weak, msg=repr(bad)):
                self.pp.set(bad)
        self.assertEqual(self.pp.status(), "unset")

    def test_changing_needs_the_old_one(self):
        self.pp.set("blue-heron-42")
        with self.assertRaises(kp.Wrong):
            self.pp.set("new-secret-7", old="nope-nope")
        with self.assertRaises(kp.Wrong):
            self.pp.set("new-secret-7")
        self.pp.set("new-secret-7", old="blue-heron-42")
        self.assertTrue(self.pp.verify("new-secret-7"))
        self.assertFalse(self.pp.verify("blue-heron-42"))

    def test_five_wrong_tries_lock_it_and_the_lock_expires(self):
        self.pp.set("blue-heron-42")
        for _ in range(kp.MAX_FAILS):
            self.assertFalse(self.pp.verify("wrong-guess"))
        with self.assertRaises(kp.Locked) as ctx:
            self.pp.verify("blue-heron-42")                     # even the right one is refused while locked
        self.assertEqual(ctx.exception.seconds, kp.BASE_LOCK_S)
        self.clock.t += kp.BASE_LOCK_S + 1
        self.assertTrue(self.pp.verify("blue-heron-42"))

    def test_the_lockout_survives_a_restart_and_grows(self):
        self.pp.set("blue-heron-42")
        for _ in range(kp.MAX_FAILS):
            self.pp.verify("wrong-guess")
        again = kp.Passphrase(self.pp.path, now=self.clock, iterations=1000)      # a new process reading the same files
        with self.assertRaises(kp.Locked):
            again.verify("blue-heron-42")
        self.clock.t += kp.BASE_LOCK_S + 1
        for _ in range(kp.MAX_FAILS):
            again.verify("wrong-guess")
        self.assertEqual(again.locked_for(), kp.BASE_LOCK_S * 2)                   # the second lock is longer

    def test_a_right_answer_clears_the_count(self):
        self.pp.set("blue-heron-42")
        for _ in range(kp.MAX_FAILS - 1):
            self.pp.verify("wrong")
        self.assertTrue(self.pp.verify("blue-heron-42"))
        for _ in range(kp.MAX_FAILS - 1):
            self.pp.verify("wrong")
        self.assertTrue(self.pp.verify("blue-heron-42"))                           # never reached 5 in a row

    def test_a_damaged_file_is_not_the_same_as_unset(self):
        self.pp.set("blue-heron-42")
        open(self.pp.path, "w").write("{not json")
        self.assertEqual(self.pp.status(), "damaged")
        with self.assertRaises(kp.Damaged):
            self.pp.verify("blue-heron-42")
        with self.assertRaises(kp.Damaged):
            self.pp.set("attacker-chosen-1")                # can't be used to take over by breaking the file
        open(self.pp.path, "w").write('{"salt": "zz", "hash": "00", "iterations": 5}')
        self.assertEqual(self.pp.status(), "damaged")

    def test_recovery_reset_starts_over(self):
        self.pp.set("blue-heron-42")
        for _ in range(kp.MAX_FAILS):
            self.pp.verify("wrong")
        self.pp.reset()
        self.assertEqual(self.pp.status(), "unset")
        self.assertEqual(self.pp.locked_for(), 0)
        self.pp.set("fresh-start-1")
        self.assertTrue(self.pp.verify("fresh-start-1"))
        self.pp.reset()                                    # resetting twice is fine


class SessionTests(unittest.TestCase):
    def test_grant_expire_touch_revoke(self):
        t = [0.0]
        s = kp.AdminSessions(now=lambda: t[0], lifetime=100)
        key = (1000, "/usr/bin/cosmos-updates")
        self.assertFalse(s.valid(key))
        s.grant(key)
        self.assertTrue(s.valid(key))
        self.assertFalse(s.valid((1000, "/usr/bin/other")))            # another program is not unlocked
        t[0] = 60
        self.assertTrue(s.touch(key))                                  # use slides it forward
        t[0] = 150
        self.assertTrue(s.valid(key))
        t[0] = 161
        self.assertFalse(s.valid(key))                                 # 100 s idle: over
        self.assertFalse(s.touch(key))
        s.grant(key)
        s.revoke()
        self.assertFalse(s.valid(key))


if __name__ == "__main__":
    unittest.main()
