"""Tests for cosmos-launcher.py's own UI logic (controller cursor support, mainly) - see
test_cosmos_pad_native.py for the input-translation layer this builds on."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("COSMOS_INPUT_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "input"))


def gtk_available():
    """True only with a real display to construct widgets against: a GTK-ERROR from trying to
    without one is fatal at the C level and aborts the whole process - not something a try/except
    can catch - so this checks for WAYLAND_DISPLAY/DISPLAY first and never actually constructs
    anything when neither is set."""
    if not (os.environ.get("WAYLAND_DISPLAY") or os.environ.get("DISPLAY")):
        return False
    try:
        import gi
        gi.require_version("Gtk", "3.0")
        from gi.repository import Gtk  # noqa: F401
        return True
    except (ImportError, ValueError):
        return False


@unittest.skipUnless(gtk_available(), "needs GTK")
class LauncherPadMouse(unittest.TestCase):
    """LauncherPadEmitter.rel(): the gridscreen's own controller-driven cursor (see
    cosmos_pad_native.py's own docstring on why NativePads calls this unconditionally). A real
    report ("game controllers dont have a mouse... in gridscreen") was this sitting as a bare
    `pass` - never emitting anything."""

    def picker_module(self):
        import importlib.machinery
        import importlib.util
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cosmos-launcher.py")
        loader = importlib.machinery.SourceFileLoader("cosmos_launcher_under_test", path)
        module = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
        loader.exec_module(module)
        return module

    def make_emitter(self, module):
        emitter = module.LauncherPadEmitter.__new__(module.LauncherPadEmitter)
        emitter.launcher = None

        class FakeUInput:
            def __init__(self):
                self.events = []

            def write(self, etype, code, value):
                self.events.append((etype, code, value))

            def syn(self):
                self.events.append(("SYN",))

        emitter.ui = FakeUInput()
        return emitter

    def test_rel_writes_real_relative_motion_events(self):
        module = self.picker_module()
        emitter = self.make_emitter(module)
        from evdev import ecodes
        emitter.rel(dx=5, dy=-3)
        self.assertEqual(
            emitter.ui.events,
            [(ecodes.EV_REL, ecodes.REL_X, 5), (ecodes.EV_REL, ecodes.REL_Y, -3), ("SYN",)],
        )

    def test_rel_with_nothing_to_move_still_syncs_once(self):
        module = self.picker_module()
        emitter = self.make_emitter(module)
        emitter.rel()
        self.assertEqual(emitter.ui.events, [("SYN",)])

    def test_rel_is_a_safe_no_op_when_no_virtual_device_could_be_created(self):
        """The constructor's own except-and-warn path (no /dev/uinput access, say) - confirms a
        failed UInput create degrades to silence, the same way it always did, rather than crashing
        the whole launcher the first time a controller's right stick moves."""
        module = self.picker_module()
        emitter = module.LauncherPadEmitter.__new__(module.LauncherPadEmitter)
        emitter.ui = None
        emitter.rel(dx=5, dy=5)  # must not raise


if __name__ == "__main__":
    unittest.main()
