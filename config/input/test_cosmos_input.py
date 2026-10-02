#!/usr/bin/env python3
"""Tests for cosmos-input / cosmos-osk. No real input device is ever created:
the translator gets a recording emitter, and the OSK a recording keyboard.
Run:  python3 test_cosmos_input.py   (the OSK test needs a DISPLAY, e.g. Xvfb)"""
import os
import subprocess
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from evdev import ecodes as E  # noqa: E402

import cosmos_input as ci  # noqa: E402


class Recorder:
    def __init__(self):
        self.events = []

    def key(self, code, value):
        self.events.append(("key", code, value))

    def rel(self, dx=0, dy=0, wheel=0):
        self.events.append(("rel", dx, dy, wheel))

    def taps(self):
        return [c for k, c, v in (e for e in self.events if e[0] == "key") if v == 1]


def press(t, code):
    t.on_event(E.EV_KEY, code, 1)
    t.on_event(E.EV_KEY, code, 0)


class TranslatorTests(unittest.TestCase):
    def setUp(self):
        self.rec = Recorder()
        self.t = ci.Translator(self.rec)

    def test_action_buttons_match_gamebox(self):
        for btn, key in [(E.BTN_SOUTH, E.KEY_ENTER), (E.BTN_EAST, E.KEY_ESC), (E.BTN_WEST, E.KEY_DELETE),
                         (E.BTN_NORTH, E.KEY_TAB), (E.BTN_TL, E.KEY_LEFTBRACE), (E.BTN_TR, E.KEY_RIGHTBRACE),
                         (E.BTN_MODE, E.KEY_HELP)]:
            self.rec.events.clear()
            press(self.t, btn)
            self.assertEqual(self.rec.events, [("key", key, 1), ("key", key, 0)], ci.ecodes.BTN[btn])

    def test_l2_types_nothing_and_r2_only_holds_alt(self):
        press(self.t, E.BTN_TL2)
        self.assertEqual(self.rec.events, [])
        press(self.t, E.BTN_TR2)          # a chord modifier: Alt down then up, no character keys
        self.assertEqual(self.rec.events, [("key", E.KEY_LEFTALT, 1), ("key", E.KEY_LEFTALT, 0)])

    def test_dpad_repeat(self):
        self.t.on_event(E.EV_ABS, E.ABS_HAT0Y, 1)
        self.t.tick(0.0)
        self.assertEqual(self.rec.taps(), [E.KEY_DOWN])
        self.t.tick(0.3)                      # inside the initial delay: no repeat
        self.assertEqual(len(self.rec.taps()), 1)
        self.t.tick(0.6)                      # past 0.5s: repeats
        self.t.tick(0.75)
        self.assertEqual(len(self.rec.taps()), 3)
        self.t.on_event(E.EV_ABS, E.ABS_HAT0Y, 0)
        self.t.tick(0.9)
        self.assertEqual(len(self.rec.taps()), 3)

    def test_dpad_as_buttons(self):
        self.t.on_event(E.EV_KEY, E.BTN_DPAD_LEFT, 1)
        self.t.tick(0.0)
        self.assertEqual(self.rec.taps(), [E.KEY_LEFT])

    def test_left_stick_navigates_by_default(self):
        self.t.on_event(E.EV_ABS, E.ABS_X, 30000)
        self.t.tick(0.0)
        self.assertEqual(self.rec.taps(), [E.KEY_RIGHT])
        self.assertFalse([e for e in self.rec.events if e[0] == "rel"])

    def test_pointer_mode_via_button_moves_cursor_not_arrows(self):
        self.t.on_event(E.EV_KEY, E.BTN_TL2, 1)
        self.assertTrue(self.t.pointer_mode)
        self.t.on_event(E.EV_ABS, E.ABS_X, 32000)
        self.t.on_event(E.EV_ABS, E.ABS_Y, -32000)
        self.t.tick(0.0)
        rels = [e for e in self.rec.events if e[0] == "rel"]
        self.assertEqual(len(rels), 1)
        self.assertGreater(rels[0][1], 0)     # right
        self.assertLess(rels[0][2], 0)        # up
        self.assertEqual(self.rec.taps(), [])

    def test_pointer_mode_via_analog_trigger(self):
        self.t.on_event(E.EV_ABS, E.ABS_Z, 32000)
        self.assertTrue(self.t.pointer_mode)
        self.t.on_event(E.EV_ABS, E.ABS_Z, -32000)
        self.assertFalse(self.t.pointer_mode)

    def test_deadzone(self):
        self.t.on_event(E.EV_KEY, E.BTN_TL2, 1)
        self.t.on_event(E.EV_ABS, E.ABS_X, 2000)
        self.t.tick(0.0)
        self.assertEqual(self.rec.events, [])

    def test_pointer_mode_clicks_and_drag(self):
        self.t.on_event(E.EV_KEY, E.BTN_TL2, 1)
        self.t.on_event(E.EV_KEY, E.BTN_SOUTH, 1)          # left button down (drag start)
        self.assertEqual(self.rec.events[-1], ("key", E.BTN_LEFT, 1))
        self.t.on_event(E.EV_KEY, E.BTN_SOUTH, 0)
        self.assertEqual(self.rec.events[-1], ("key", E.BTN_LEFT, 0))
        self.t.on_event(E.EV_KEY, E.BTN_EAST, 1)
        self.t.on_event(E.EV_KEY, E.BTN_EAST, 0)
        self.assertEqual(self.rec.events[-2:], [("key", E.BTN_RIGHT, 1), ("key", E.BTN_RIGHT, 0)])
        self.assertNotIn(E.KEY_ENTER, self.rec.taps())      # A is a click here, not Enter

    def test_releasing_l2_while_dragging_releases_the_button(self):
        self.t.on_event(E.EV_KEY, E.BTN_TL2, 1)
        self.t.on_event(E.EV_KEY, E.BTN_SOUTH, 1)
        self.t.on_event(E.EV_KEY, E.BTN_TL2, 0)
        self.assertEqual(self.rec.events[-1], ("key", E.BTN_LEFT, 0))
        self.assertEqual(self.t.mouse_down, set())
        self.rec.events.clear()
        press(self.t, E.BTN_SOUTH)                           # back to normal: Enter
        self.assertEqual(self.rec.taps(), [E.KEY_ENTER])

    def test_reset_releases_held_mouse_buttons(self):
        self.t.on_event(E.EV_KEY, E.BTN_TL2, 1)
        self.t.on_event(E.EV_KEY, E.BTN_SOUTH, 1)
        self.t.reset()
        self.assertEqual(self.rec.events[-1], ("key", E.BTN_LEFT, 0))
        self.assertFalse(self.t.pointer_mode)

    def test_right_stick_scrolls_rate_limited(self):
        self.t.on_event(E.EV_ABS, E.ABS_RY, -30000)         # up
        self.t.tick(0.0)
        self.t.tick(0.05)                                    # too soon
        self.t.tick(0.2)
        self.assertEqual([e for e in self.rec.events if e[0] == "rel"], [("rel", 0, 0, 1), ("rel", 0, 0, 1)])
        self.rec.events.clear()
        self.t.on_event(E.EV_ABS, E.ABS_RY, 30000)          # down
        self.t.tick(1.0)
        self.assertEqual(self.rec.events, [("rel", 0, 0, -1)])


class ChordTests(unittest.TestCase):
    def setUp(self):
        self.rec = Recorder()
        self.t = ci.Translator(self.rec)

    def keys(self):
        return [(c, v) for k, c, v in self.rec.events if k == "key"]

    def test_holding_r2_holds_alt_and_release_lets_go(self):
        self.t.on_event(E.EV_KEY, E.BTN_TR2, 1)
        self.assertEqual(self.keys(), [(E.KEY_LEFTALT, 1)])
        self.t.on_event(E.EV_KEY, E.BTN_TR2, 0)
        self.assertEqual(self.keys()[-1], (E.KEY_LEFTALT, 0))

    def test_r2_as_an_axis_works_the_same(self):
        self.t.on_event(E.EV_ABS, E.ABS_RZ, ci.STICK_MAX)
        self.assertEqual(self.keys(), [(E.KEY_LEFTALT, 1)])
        self.t.on_event(E.EV_ABS, E.ABS_RZ, 0)
        self.assertEqual(self.keys()[-1], (E.KEY_LEFTALT, 0))

    def test_dpad_cycles_the_switcher_while_r2_is_held(self):
        self.t.on_event(E.EV_KEY, E.BTN_TR2, 1)
        self.rec.events.clear()
        self.t.on_event(E.EV_ABS, E.ABS_HAT0X, 1)          # right = next
        self.t.tick(10.0)
        self.assertEqual(self.keys(), [(E.KEY_TAB, 1), (E.KEY_TAB, 0)])
        self.t.on_event(E.EV_ABS, E.ABS_HAT0X, 0)
        self.rec.events.clear()
        self.t.on_event(E.EV_ABS, E.ABS_HAT0X, -1)         # left = previous: Shift+Tab
        self.t.tick(11.0)
        self.assertEqual(self.keys(), [(E.KEY_LEFTSHIFT, 1), (E.KEY_TAB, 1), (E.KEY_TAB, 0), (E.KEY_LEFTSHIFT, 0)])

    def test_x_closes_the_window_while_r2_is_held(self):
        self.t.on_event(E.EV_KEY, E.BTN_TR2, 1)
        self.rec.events.clear()
        press(self.t, E.BTN_WEST)
        self.assertEqual(self.keys(), [(E.KEY_X, 1), (E.KEY_X, 0)])   # with Alt down: Alt+X

    def test_normal_navigation_is_unchanged_without_r2(self):
        self.t.on_event(E.EV_ABS, E.ABS_HAT0X, 1)
        self.t.tick(10.0)
        self.assertEqual(self.keys(), [(E.KEY_RIGHT, 1), (E.KEY_RIGHT, 0)])
        press(self.t, E.BTN_WEST)
        self.assertIn(E.KEY_DELETE, self.rec.taps())

    def test_reset_releases_alt(self):
        self.t.on_event(E.EV_KEY, E.BTN_TR2, 1)
        self.rec.events.clear()
        self.t.reset()
        self.assertEqual(self.keys(), [(E.KEY_LEFTALT, 0)])


class HelperTests(unittest.TestCase):
    def test_normalize_abs_rescales_unsigned_range(self):
        class Info:
            min, max = 0, 255

        class Dev:
            def absinfo(self, code):
                return Info

        self.assertEqual(ci.normalize_abs(Dev(), E.ABS_X, 0), ci.STICK_MIN)
        self.assertEqual(ci.normalize_abs(Dev(), E.ABS_X, 255), ci.STICK_MAX)
        self.assertGreater(ci.normalize_abs(Dev(), E.ABS_X, 240), ci.NAV_THRESHOLD)   # pushed right now counts

    def test_gamebox_daemon_detection(self):
        self.assertFalse(ci.gamebox_input_running())
        d = tempfile.mkdtemp()
        os.makedirs(f"{d}/input")
        script = f"{d}/input/run.py"
        with open(script, "w") as fh:
            fh.write("import time\ntime.sleep(30)\n")
        p = subprocess.Popen([sys.executable, script])
        try:
            time.sleep(0.4)
            self.assertTrue(ci.gamebox_input_running())
        finally:
            p.kill()
            p.wait()
        self.assertFalse(ci.gamebox_input_running())
        # something that merely mentions the path must not count
        q = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(5)", f"{d}/input/run.py"])
        try:
            time.sleep(0.2)
            self.assertFalse(ci.gamebox_input_running())
        finally:
            q.kill()
            q.wait()

    def test_ui_claim_needs_a_live_other_process(self):
        d = tempfile.mkdtemp()
        old = os.environ.get("XDG_RUNTIME_DIR")
        os.environ["XDG_RUNTIME_DIR"] = d
        try:
            self.assertFalse(ci.ui_claimed_pad())                      # no file
            p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
            try:
                with open(f"{d}/cosmos-gamepad-claimed-by", "w") as fh:
                    fh.write(str(p.pid))
                self.assertTrue(ci.ui_claimed_pad())                   # live claimant
            finally:
                p.kill()
                p.wait()
            self.assertFalse(ci.ui_claimed_pad())                      # dead claimant can't strand us
            with open(f"{d}/cosmos-gamepad-claimed-by", "w") as fh:
                fh.write(str(os.getpid()))
            self.assertFalse(ci.ui_claimed_pad())                      # our own pid never counts
        finally:
            if old is None:
                del os.environ["XDG_RUNTIME_DIR"]
            else:
                os.environ["XDG_RUNTIME_DIR"] = old

    def test_virtual_pads_are_not_physical_pads(self):
        class Dev:
            name = "CosmOS Pad 1"

            def capabilities(self):
                return {E.EV_KEY: [E.BTN_SOUTH]}

        self.assertFalse(ci.is_gamepad(Dev()))
        Dev.name = "Xbox Wireless Controller"
        self.assertTrue(ci.is_gamepad(Dev()))


@unittest.skipUnless(os.environ.get("DISPLAY"), "needs a display (run under Xvfb)")
class OskTests(unittest.TestCase):
    def setUp(self):
        import cosmos_osk
        self.osk_mod = cosmos_osk
        self.rec = Recorder()
        self.osk = cosmos_osk.Osk(out=self.rec)

    def typed(self):
        return self.rec.taps()

    def test_typing_letters_and_shift(self):
        o = self.osk
        o.press("h")
        self.assertEqual(self.typed(), [E.KEY_H])
        self.rec.events.clear()
        o.press("SHIFT")
        o.press("h")
        self.assertEqual(self.rec.events[:4], [("key", E.KEY_LEFTSHIFT, 1), ("key", E.KEY_H, 1),
                                                ("key", E.KEY_H, 0), ("key", E.KEY_LEFTSHIFT, 0)])

    def test_symbols_need_their_own_shift(self):
        self.osk.press("!")
        self.assertEqual(self.rec.events[:4], [("key", E.KEY_LEFTSHIFT, 1), ("key", E.KEY_1, 1),
                                                ("key", E.KEY_1, 0), ("key", E.KEY_LEFTSHIFT, 0)])

    def test_grid_navigation_and_confirm(self):
        o = self.osk
        o.handle(E.EV_ABS, E.ABS_HAT0X, 1)       # q -> w
        o.handle(E.EV_KEY, E.BTN_SOUTH, 1)
        self.assertEqual(self.typed(), [E.KEY_W])
        self.rec.events.clear()
        o.handle(E.EV_ABS, E.ABS_HAT0Y, -1)      # wraps up to the bottom row
        self.assertEqual(o.rows[o.selected_row][0], "123")

    def test_shortcuts_and_mode_switch(self):
        o = self.osk
        o.handle(E.EV_KEY, E.BTN_WEST, 1)
        o.handle(E.EV_KEY, E.BTN_NORTH, 1)
        self.assertEqual(self.typed(), [E.KEY_BACKSPACE, E.KEY_SPACE])
        o.press("123")
        self.assertEqual(o.mode, "symbols")
        o.press("ABC")
        self.assertEqual(o.mode, "letters")

    def test_start_closes(self):
        self.osk.handle(E.EV_KEY, E.BTN_START, 1)
        self.assertFalse(self.osk.running)

    def test_every_grid_key_is_typeable(self):
        for rows in (self.osk_mod.LETTERS_ROWS, self.osk_mod.SYMBOLS_ROWS):
            for row in rows:
                for key in row:
                    if key not in self.osk_mod.ACTION_KEYS:
                        self.assertIn(key, self.osk_mod.CHAR_KEYS, key)


if __name__ == "__main__":
    unittest.main(verbosity=1)
