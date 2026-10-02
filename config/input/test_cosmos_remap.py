"""Tests for cosmos_remap (per-virtual-pad button/stick remapping) and its use in GameBox's inputd."""
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cosmos_remap as cr

GAMEBOX_INPUT = os.environ.get("GAMEBOX_INPUT_DIR") or os.path.expanduser("~/CosmOS/input")


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.path = os.path.join(tempfile.mkdtemp(), "sub", "controller-remap.json")

    def test_roundtrip_stores_only_changes(self):
        cfg = {"buttons": {"BTN_SOUTH": "BTN_EAST", "BTN_EAST": "BTN_SOUTH", "BTN_START": None},
               "swap_sticks": True, "invert": ["ABS_Y"]}
        cr.save({1: cfg, 2: {"buttons": {}, "swap_sticks": False, "invert": []}}, self.path)
        loaded = cr.load(self.path)
        self.assertEqual(list(loaded), [1])                     # slot 2 was all-default: not stored
        self.assertEqual(loaded[1], cr.normalize(cfg))

    def test_garbage_is_ignored(self):
        cfg = cr.normalize({"buttons": {"BTN_NOPE": "BTN_A", "BTN_SOUTH": "BTN_SOUTH", "BTN_TL": "BTN_FAKE", "BTN_TR": "BTN_TL"},
                            "invert": ["ABS_Z", "ABS_X"], "swap_sticks": 1})
        self.assertEqual(cfg, {"buttons": {"BTN_TR": "BTN_TL"}, "swap_sticks": True, "invert": ["ABS_X"]})
        self.assertEqual(cr.load(os.path.join(tempfile.mkdtemp(), "missing.json")), {})
        open(self.path.replace("sub/", ""), "w").write("not json")
        self.assertEqual(cr.load(self.path.replace("sub/", "")), {})

    def test_codes_are_the_kernels(self):
        try:
            from evdev import ecodes
        except ImportError:
            self.skipTest("no evdev")
        for name, code, _ in cr.BUTTONS:
            self.assertEqual(getattr(ecodes, name), code, name)
        for name, code, _ in cr.AXES:
            self.assertEqual(getattr(ecodes, name), code, name)


class SlotRemapTests(unittest.TestCase):
    def test_swap_face_buttons_and_disable(self):
        r = cr.SlotRemap({"buttons": {"BTN_SOUTH": "BTN_EAST", "BTN_EAST": "BTN_SOUTH", "BTN_START": None}})
        self.assertEqual(r.button(304), 305)
        self.assertEqual(r.button(305), 304)
        self.assertIsNone(r.button(315))
        self.assertEqual(r.button(308), 308)                     # untouched buttons pass through

    def test_swap_sticks_moves_both_axes_and_leaves_the_dpad(self):
        r = cr.SlotRemap({"swap_sticks": True})
        self.assertEqual(r.axis(cr.ABS_X, 5), (cr.ABS_RX, 5))
        self.assertEqual(r.axis(cr.ABS_RY, -7), (cr.ABS_Y, -7))
        self.assertEqual(r.axis(16, 1), (16, 1))                 # ABS_HAT0X

    def test_invert_applies_to_the_axis_the_game_sees(self):
        r = cr.SlotRemap({"swap_sticks": True, "invert": ["ABS_RX"]})
        self.assertEqual(r.axis(cr.ABS_X, 1000), (cr.ABS_RX, -1000))      # left stick, swapped onto the right axis
        self.assertEqual(r.axis(cr.ABS_RX, 1000), (cr.ABS_X, 1000))
        inv = cr.SlotRemap({"invert": ["ABS_Y"]})
        self.assertEqual(inv.axis(cr.ABS_Y, -32768), (cr.ABS_Y, 32767))   # no overflow at the end of the range
        self.assertEqual(inv.axis(cr.ABS_Y, 0), (cr.ABS_Y, 0))

    def test_identity(self):
        self.assertEqual(cr.IDENTITY.button(304), 304)
        self.assertEqual(cr.IDENTITY.axis(0, 12), (0, 12))


class RemapperTests(unittest.TestCase):
    def test_slots_are_numbered_from_zero_and_the_file_is_reloaded(self):
        path = os.path.join(tempfile.mkdtemp(), "r.json")
        cr.save({2: {"buttons": {"BTN_SOUTH": "BTN_WEST"}}}, path)
        rm = cr.Remapper(path)
        self.assertEqual(rm.for_slot(1).button(304), 308)          # slot index 1 = "Player 2"
        self.assertEqual(rm.for_slot(0).button(304), 304)
        cr.save({1: {"buttons": {"BTN_SOUTH": "BTN_NORTH"}}}, path)
        os.utime(path, ns=(time.time_ns() + 5_000_000_000,) * 2)
        rm.checked = 0                                             # skip the 1 s throttle
        self.assertEqual(rm.for_slot(0).button(304), 307)
        self.assertEqual(rm.for_slot(1).button(304), 304)


class FakePad:
    def __init__(self):
        self.events = []

    def write(self, t, c, v):
        self.events.append((t, c, v))

    def syn(self):
        pass


class Ev:
    def __init__(self, t, c, v):
        self.type, self.code, self.value = t, c, v


class Dev:
    def absinfo(self, code):
        return None


class InputdIntegrationTests(unittest.TestCase):
    """GameBox's own mirror_event with a remap - skipped when the GameBox checkout isn't here."""

    @classmethod
    def setUpClass(cls):
        if not os.path.exists(os.path.join(GAMEBOX_INPUT, "inputd.py")):
            raise unittest.SkipTest("no GameBox input/ directory")
        sys.path.insert(0, GAMEBOX_INPUT)
        try:
            import inputd
        except Exception as exc:                     # evdev missing, ...
            raise unittest.SkipTest(f"inputd not importable: {exc}")
        cls.inputd = inputd
        cls.ecodes = inputd.ecodes

    def mirror(self, remap, *events):
        pad = FakePad()
        for e in events:
            if not hasattr(self.inputd, "_make_remapper"):
                self.skipTest("this inputd has no remap hook")
            self.inputd.mirror_event(pad, e, Dev(), {}, remap)
        return pad.events

    def test_button_remap_and_drop(self):
        e = self.ecodes
        remap = cr.SlotRemap({"buttons": {"BTN_SOUTH": "BTN_EAST", "BTN_START": None}})
        out = self.mirror(remap, Ev(e.EV_KEY, e.BTN_SOUTH, 1), Ev(e.EV_KEY, e.BTN_START, 1), Ev(e.EV_KEY, e.BTN_WEST, 1))
        self.assertEqual(out, [(e.EV_KEY, e.BTN_EAST, 1), (e.EV_KEY, e.BTN_WEST, 1)])

    def test_stick_swap_and_invert(self):
        e = self.ecodes
        remap = cr.SlotRemap({"swap_sticks": True, "invert": ["ABS_Y"]})
        out = self.mirror(remap, Ev(e.EV_ABS, e.ABS_X, 1000), Ev(e.EV_ABS, e.ABS_RY, 500))
        self.assertEqual(out, [(e.EV_ABS, e.ABS_RX, 1000), (e.EV_ABS, e.ABS_Y, -500)])

    def test_no_remap_mirrors_exactly_as_before(self):
        e = self.ecodes
        out = self.mirror(None, Ev(e.EV_KEY, e.BTN_SOUTH, 1), Ev(e.EV_ABS, e.ABS_X, 42))
        self.assertEqual(out, [(e.EV_KEY, e.BTN_SOUTH, 1), (e.EV_ABS, e.ABS_X, 42)])


if __name__ == "__main__":
    unittest.main()
