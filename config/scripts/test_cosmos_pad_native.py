#!/usr/bin/env python3
"""Tests for the launcher's native controller path (cosmos_pad_native) and the
shared session-state flags. No real device: events are fed in by hand.
Run:  python3 test_cosmos_pad_native.py"""
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("COSMOS_INPUT_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "input"))
from evdev import ecodes as E  # noqa: E402
from evdev import InputEvent  # noqa: E402

import cosmos_pad_native as cpn  # noqa: E402


class Emitter:
    def __init__(self):
        self.keys = []
        self.rels = []

    def key(self, code, value):
        if value == 1:
            self.keys.append(code)

    def rel(self, dx=0, dy=0, wheel=0):
        self.rels.append((dx, dy, wheel))


def ev(etype, code, value):
    return InputEvent(0, 0, etype, code, value)


class NativePadTests(unittest.TestCase):
    def setUp(self):
        self.out = Emitter()
        self.selected = []
        self.on = True
        self.pads = cpn.NativePads(self.out, lambda: self.on, on_select=lambda: self.selected.append(1))

    def press(self, code):
        self.pads.feed(None, ev(E.EV_KEY, code, 1))
        self.pads.feed(None, ev(E.EV_KEY, code, 0))

    def test_buttons_reach_the_emitter_as_cosmos_input_keys(self):
        for button, key in ((E.BTN_SOUTH, E.KEY_ENTER), (E.BTN_EAST, E.KEY_ESC), (E.BTN_WEST, E.KEY_DELETE),
                            (E.BTN_NORTH, E.KEY_TAB), (E.BTN_TL, E.KEY_LEFTBRACE), (E.BTN_TR, E.KEY_RIGHTBRACE)):
            self.press(button)
            self.assertEqual(self.out.keys[-1], key)

    def test_dpad_navigates_with_repeat(self):
        self.pads.feed(None, ev(E.EV_ABS, E.ABS_HAT0Y, 1))
        self.pads.translator.tick(100.0)
        self.assertEqual(self.out.keys, [E.KEY_DOWN])
        self.pads.translator.tick(100.2)     # before the initial repeat delay
        self.assertEqual(self.out.keys, [E.KEY_DOWN])
        self.pads.translator.tick(100.6)
        self.assertEqual(self.out.keys, [E.KEY_DOWN, E.KEY_DOWN])

    def test_select_opens_keyboard_and_pointer_mode_is_off(self):
        self.press(E.BTN_SELECT)
        self.assertEqual(self.selected, [1])
        self.pads.feed(None, ev(E.EV_KEY, E.BTN_TL2, 1))      # would turn A into a mouse click
        self.press(E.BTN_SOUTH)
        self.assertEqual(self.out.keys, [E.KEY_ENTER])

    def test_r2_goes_back_once_per_press_and_never_reaches_the_translator(self):
        calls = []
        self.pads.on_r2 = lambda: calls.append(1)
        self.pads.feed(None, ev(E.EV_KEY, E.BTN_TR2, 1))
        self.pads.feed(None, ev(E.EV_KEY, E.BTN_TR2, 1))     # still held: no repeat
        self.pads.feed(None, ev(E.EV_KEY, E.BTN_TR2, 0))
        self.pads.feed(None, ev(E.EV_KEY, E.BTN_TR2, 1))
        self.assertEqual(len(calls), 2)
        self.assertEqual(self.out.keys, [])                   # no Alt/Tab typed at the launcher

    def test_right_stick_moves_a_real_cursor_even_with_l2_held(self):
        # L2 held would normally turn A/B into mouse clicks (pointer_mode) - the point of this
        # feature is that cursor movement never depends on that, so hold it throughout and still
        # expect A to mean Enter (test_select_opens_keyboard_and_pointer_mode_is_off's own
        # assertion), not a click, while the stick still drives the cursor.
        self.pads.feed(None, ev(E.EV_KEY, E.BTN_TL2, 1))
        self.pads.feed(None, ev(E.EV_ABS, E.ABS_RX, 20000))
        self.pads.tick()
        self.assertEqual(self.out.rels, [(7, 0, 0)])
        self.press(E.BTN_SOUTH)
        self.assertEqual(self.out.keys, [E.KEY_ENTER])  # still navigation, never a click

    def test_small_stick_deflection_stays_in_the_deadzone(self):
        self.pads.feed(None, ev(E.EV_ABS, E.ABS_RX, 2000))
        self.pads.feed(None, ev(E.EV_ABS, E.ABS_RY, -2000))
        self.pads.tick()
        self.assertEqual(self.out.rels, [])

    def test_cursor_movement_stops_once_focus_is_lost(self):
        self.pads.feed(None, ev(E.EV_ABS, E.ABS_RX, 20000))
        self.pads.tick()
        self.assertEqual(self.out.rels, [(7, 0, 0)])
        self.on = False
        self.pads.tick()   # focus lost: reset zeroes rx/ry, same as every other held state
        self.assertEqual(self.pads.translator.rx, 0)
        self.assertEqual(self.out.rels, [(7, 0, 0)])  # no new call while not enabled

    def test_losing_focus_releases_everything_held(self):
        self.pads.feed(None, ev(E.EV_ABS, E.ABS_HAT0X, 1))
        self.pads.tick()
        self.assertEqual(self.out.keys, [E.KEY_RIGHT])
        self.on = False
        self.pads.tick()                                       # focus lost: reset, no more output
        self.assertEqual(self.pads.translator.hat_x, 0)
        self.pads.tick()
        self.assertEqual(self.out.keys, [E.KEY_RIGHT])


class SessionStateTests(unittest.TestCase):
    def test_pad_claim_lifecycle(self):
        d = tempfile.mkdtemp()
        # a claim by a live process is reported; releasing removes only our own
        code = (f"import os,sys; os.environ['XDG_RUNTIME_DIR']={d!r}; sys.path.insert(0, {os.path.dirname(os.path.abspath(__file__))!r})\n"
                "import cosmos_session_state as s\n"
                "assert not s.pad_claimed()\n"
                "s.claim_pad(); assert s.pad_claimed()\n"
                "open(s.PAD_CLAIM_PATH,'w').write('1')   # someone else's claim\n"
                "s.release_pad(); assert os.path.exists(s.PAD_CLAIM_PATH)   # not ours: left alone\n"
                "open(s.PAD_CLAIM_PATH,'w').write(str(os.getpid())); s.release_pad(); assert not os.path.exists(s.PAD_CLAIM_PATH)\n"
                "open(s.HIDE_REQUEST_PATH,'w').write('999999999'); assert not s.hide_requested()   # dead pid\n"
                "open(s.HIDE_REQUEST_PATH,'w').write(str(os.getpid())); assert s.hide_requested()\n"
                "open(s.HOME_SHOWN_PATH,'w').close(); assert not s.hide_requested()   # went Home: bar and launcher come back\n"
                "os.remove(s.HOME_SHOWN_PATH); assert s.hide_requested()\n")
        r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)


if __name__ == "__main__":
    unittest.main()
