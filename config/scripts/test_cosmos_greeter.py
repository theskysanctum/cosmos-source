"""Tests for cosmos-greeter.py's own UI logic (not cosmos_login's greetd protocol - see test_cosmos_login.py for that)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def gtk_available():
    """True only with a real display to construct widgets against: a GTK-ERROR from trying to without one is fatal at the C level and aborts
    the whole process - not something a try/except can catch - so this checks for WAYLAND_DISPLAY/DISPLAY first."""
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
class GreeterEscape(unittest.TestCase):
    """Escape on the login screen's password field cancels an in-progress attempt instead of leaving you stuck watching it fail - there is
    nothing behind the login screen to back out TO (it's the first thing greetd starts), so this cancels the attempt, not the screen."""

    def greeter_module(self):
        import importlib.machinery
        import importlib.util
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cosmos-greeter.py")
        loader = importlib.machinery.SourceFileLoader("cosmos_greeter_under_test", path)
        module = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
        loader.exec_module(module)
        return module

    def make_greeter(self, module, in_authentication):
        win = module.CosmosGreeter.__new__(module.CosmosGreeter)

        class FakeConversation:
            def __init__(self):
                self.in_authentication = in_authentication
                self.cancelled = False

            def cancel(self):
                self.cancelled = True
                self.in_authentication = False

        win.greeter = FakeConversation()
        win.password_entry = module.Gtk.Entry()
        win.password_entry.set_text("wr0ngP1N")
        win.error_label = module.Gtk.Label()
        win.error_label.set_text("That wasn't right.")
        win.error_label.set_visible(True)
        win.status_label = module.Gtk.Label()
        win.status_label.set_text("Checking...")
        return win

    def escape_event(self, module):
        event = module.Gdk.Event.new(module.Gdk.EventType.KEY_PRESS)
        event.keyval = module.Gdk.KEY_Escape
        return event

    def test_escape_cancels_an_attempt_in_progress_and_clears_the_field(self):
        module = self.greeter_module()
        win = self.make_greeter(module, in_authentication=True)
        handled = win.on_password_key_press(win.password_entry, self.escape_event(module))
        self.assertTrue(handled)
        self.assertTrue(win.greeter.cancelled)
        self.assertEqual(win.password_entry.get_text(), "")
        self.assertEqual(win.error_label.get_text(), "")
        self.assertFalse(win.error_label.get_visible())
        self.assertEqual(win.status_label.get_text(), "")

    def test_escape_with_nothing_in_progress_still_clears_the_field_and_is_handled(self):
        module = self.greeter_module()
        win = self.make_greeter(module, in_authentication=False)
        handled = win.on_password_key_press(win.password_entry, self.escape_event(module))
        self.assertTrue(handled)
        self.assertFalse(win.greeter.cancelled)  # nothing was running, so cancel() was never called
        self.assertEqual(win.password_entry.get_text(), "")

    def test_other_keys_are_unaffected(self):
        module = self.greeter_module()
        win = self.make_greeter(module, in_authentication=True)
        event = module.Gdk.Event.new(module.Gdk.EventType.KEY_PRESS)
        event.keyval = module.Gdk.KEY_a
        handled = win.on_password_key_press(win.password_entry, event)
        self.assertFalse(handled)
        self.assertFalse(win.greeter.cancelled)
        self.assertEqual(win.password_entry.get_text(), "wr0ngP1N")


if __name__ == "__main__":
    unittest.main()
