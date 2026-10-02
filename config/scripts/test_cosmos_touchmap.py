"""Tests for the button/key to touch mapper engine and its profiles (no compositor or controller needed)."""
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import cosmos_touchmap as tm

PROFILE = {"name": "Shooter", "aspect": 2.0, "elements": [
    {"type": "tap", "inputs": ["BTN_SOUTH", "KEY_SPACE"], "x": 0.8, "y": 0.7},
    {"type": "tap", "inputs": ["R2"], "x": 0.9, "y": 0.5},
    {"type": "stick", "stick": "left", "keys": {"up": "KEY_W", "down": "KEY_S", "left": "KEY_A", "right": "KEY_D"}, "x": 0.2, "y": 0.6, "radius": 0.1},
    {"type": "look", "stick": "right", "x": 0.7, "y": 0.4, "radius": 0.2, "sensitivity": 1.0},
]}


def make():
    sent = []
    return tm.Mapper(PROFILE, sent.append), sent


class TapTests(unittest.TestCase):
    def test_a_button_or_key_taps_and_holds_its_point(self):
        m, sent = make()
        m.on_input("BTN_SOUTH", True)
        self.assertEqual(sent, ["touch down 0 0.8000 0.7000"])
        m.on_input("BTN_SOUTH", False)
        self.assertEqual(sent[-1], "touch up 0")

    def test_two_inputs_for_one_point_hold_it_until_both_are_up(self):
        m, sent = make()
        m.on_input("BTN_SOUTH", True)
        m.on_input("KEY_SPACE", True)
        m.on_input("BTN_SOUTH", False)
        self.assertEqual(sent, ["touch down 0 0.8000 0.7000"])       # still held by the key, no second touch
        m.on_input("KEY_SPACE", False)
        self.assertEqual(sent[-1], "touch up 0")

    def test_repeats_and_unmapped_inputs_do_nothing(self):
        m, sent = make()
        m.on_input("BTN_SOUTH", True)
        m.on_input("BTN_SOUTH", True)
        m.on_input("BTN_NORTH", True)
        m.on_input("KEY_Q", False)
        self.assertEqual(len(sent), 1)

    def test_each_point_gets_its_own_finger(self):
        m, sent = make()
        m.on_input("BTN_SOUTH", True)
        m.on_input("R2", True)
        self.assertEqual([s.split()[2] for s in sent], ["0", "1"])
        m.on_input("BTN_SOUTH", False)
        m.on_input("BTN_SOUTH", True)
        self.assertTrue(sent[-1].startswith("touch down 0 "))       # the freed finger is reused

    def test_release_all_lifts_everything(self):
        m, sent = make()
        m.on_input("BTN_SOUTH", True)
        m.on_input("KEY_W", True)
        m.tick(0.016)
        m.release_all()
        self.assertEqual({s for s in sent if s.startswith("touch up")}, {"touch up 0", "touch up 1"})
        m.on_input("BTN_SOUTH", True)                                  # and the held state was forgotten
        self.assertTrue(sent[-1].startswith("touch down"))


class StickTests(unittest.TestCase):
    def test_wasd_drives_the_joystick(self):
        m, sent = make()
        m.on_input("KEY_D", True)
        m.tick(0.016)
        self.assertEqual(sent[0], "touch down 0 0.2000 0.6000")
        self.assertEqual(sent[1], "touch move 0 0.2500 0.6000")       # radius 0.1 of the height, width is twice as wide: 0.05
        m.on_input("KEY_D", False)
        m.tick(0.016)
        self.assertEqual(sent[-1], "touch up 0")

    def test_diagonals_are_no_faster_than_straight(self):
        m, sent = make()
        m.on_input("KEY_W", True)
        m.on_input("KEY_D", True)
        m.tick(0.016)
        x, y = map(float, sent[1].split()[3:])
        self.assertAlmostEqual(((x - 0.2) * 2) ** 2 + (y - 0.6) ** 2, 0.1 ** 2, places=3)

    def test_the_analogue_stick_moves_in_proportion_and_has_a_dead_zone(self):
        m, sent = make()
        m.on_axis("left", 0.1, 0.0)
        m.tick(0.016)
        self.assertEqual(sent, [])
        m.on_axis("left", 0.0, -0.5)
        m.tick(0.016)
        self.assertEqual(sent[-1], "touch move 0 0.2000 0.5500")       # up is negative y; half a radius
        m.on_axis("left", 0.0, 0.0)
        m.tick(0.016)
        self.assertEqual(sent[-1], "touch up 0")

    def test_an_unchanged_stick_is_not_resent(self):
        m, sent = make()
        m.on_axis("left", 1.0, 0.0)
        m.tick(0.016)
        n = len(sent)
        m.tick(0.016)
        self.assertEqual(len(sent), n)

    def test_the_look_finger_drifts_and_is_put_back_at_the_edge(self):
        m, sent = make()
        m.on_axis("right", 1.0, 0.0)
        for _ in range(200):
            m.tick(0.016)
        self.assertGreater(len([s for s in sent if s.startswith("touch down")]), 1)      # it was re-placed at least once
        self.assertTrue(any(s.startswith("touch up") for s in sent))
        for s in sent:                                                                   # and never left the area around its centre
            if s.startswith("touch move"):
                x, y = map(float, s.split()[3:])
                self.assertLessEqual(abs(x - 0.7) * 2, 0.2001)
        m.on_axis("right", 0, 0)
        m.tick(0.016)
        self.assertTrue(sent[-1].startswith("touch up"))

    def test_running_out_of_fingers_is_harmless(self):
        elements = [{"type": "tap", "inputs": ["KEY_%s" % c], "x": 0.1, "y": 0.1} for c in "ABCDEFGHIJKL"]
        sent = []
        m = tm.Mapper({"elements": elements}, sent.append)
        for c in "ABCDEFGHIJKL":
            m.on_input("KEY_%s" % c, True)
        self.assertEqual(len(sent), tm.MAX_TOUCHES)


class ProfileTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        tm.PROFILE_DIR = self.dir
        tm.CONFIG_DIR = self.dir
        tm.SETTINGS_PATH = os.path.join(self.dir, "s.json")

    def test_clean_profile_drops_junk_and_clamps(self):
        p = tm.clean_profile({"name": "../x", "aspect": 99, "elements": [
            {"type": "tap", "inputs": ["BTN_A", "no way", 7], "x": 5, "y": -1},
            {"type": "tap", "inputs": []}, {"type": "nope"}, "text", {"type": "stick"},
            {"type": "look", "stick": "right", "radius": 9, "sensitivity": "fast"}]})
        self.assertEqual(p["name"], "Untitled")
        self.assertEqual(p["aspect"], 4)
        self.assertEqual(len(p["elements"]), 2)
        self.assertEqual((p["elements"][0]["inputs"], p["elements"][0]["x"], p["elements"][0]["y"]), (["BTN_A"], 1, 0))
        self.assertEqual((p["elements"][1]["radius"], p["elements"][1]["sensitivity"]), (0.5, 1.0))
        self.assertEqual(tm.clean_profile(None), {"name": "Untitled", "aspect": 16 / 9, "elements": []})

    def test_save_load_list_delete_roundtrip(self):
        saved = tm.save_profile(PROFILE)
        self.assertEqual(tm.list_profiles(), ["Shooter"])
        self.assertEqual(tm.load_profile("Shooter"), saved)
        tm.delete_profile("Shooter")
        tm.delete_profile("Shooter")
        self.assertEqual(tm.list_profiles(), [])

    def test_names_cannot_escape_the_folder(self):
        for bad in ("../evil", "a/b", "", ".hidden", "x" * 41, None):
            with self.assertRaises(ValueError, msg=bad):
                tm.profile_path(bad)

    def test_settings_default_off_and_survive_garbage(self):
        self.assertEqual(tm.load_settings(), {"enabled": False, "profile": ""})
        open(tm.SETTINGS_PATH, "w").write("[1,2")
        self.assertEqual(tm.load_settings(), {"enabled": False, "profile": ""})
        tm.save_settings({"enabled": True, "profile": "Shooter"})
        self.assertEqual(tm.load_settings(), {"enabled": True, "profile": "Shooter"})
        tm.save_settings({"enabled": True, "profile": "../x"})
        self.assertEqual(tm.load_settings()["profile"], "")


class WindowTests(unittest.TestCase):
    def test_android_windows_are_recognised_by_app_id_or_title(self):
        self.assertTrue(tm.is_android("Waydroid", "x"))
        self.assertTrue(tm.is_android("", "Waydroid"))
        self.assertFalse(tm.is_android("cosmos-launcher", "Home"))


class SubscriptionParsing(unittest.TestCase):
    def test_key_lines_are_parsed_even_when_split_across_reads(self):
        import socket
        a, b = socket.socketpair()
        sub = tm.Subscription.__new__(tm.Subscription)
        sub.sock, sub.buf = b, b""
        b.setblocking(False)
        a.sendall(b"key 30 1\nke")
        self.assertEqual(sub.read_keys(), [(30, True)])
        a.sendall(b"y 30 0\nok\ngarbage here now\n")
        self.assertEqual(sub.read_keys(), [(30, False)])
        a.close()
        with self.assertRaises(EOFError):
            sub.read_keys()


if __name__ == "__main__":
    unittest.main()
