#!/usr/bin/env python3
"""Tests for cosmos_profiles. Run: python3 test_cosmos_profiles.py"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cosmos_profiles as cp  # noqa: E402


class ProfileTests(unittest.TestCase):
    def setUp(self):
        home = tempfile.mkdtemp()
        cp.DATA_DIR = os.path.join(home, "profiles")
        cp.CONFIG_DIR = os.path.join(home, "config")
        cp.ACTIVE_FILE = os.path.join(cp.CONFIG_DIR, "active-profile")
        cp.FACE_PATH = os.path.join(home, ".face")
        self.pic = os.path.join(home, "pic.png")
        with open(self.pic, "wb") as fh:
            fh.write(b"PNGDATA")

    def test_create_list_and_unique_slugs(self):
        a = cp.create_profile("Skye", avatar=self.pic)
        b = cp.create_profile("Skye")                       # same name: distinct profile
        self.assertNotEqual(a["slug"], b["slug"])
        self.assertEqual([p["name"] for p in cp.list_profiles()], ["Skye", "Skye"])
        self.assertTrue(os.path.isfile(a["avatar"]))
        with self.assertRaises(cp.ProfileError):
            cp.create_profile("   ")

    def test_set_avatar_file_replaces_the_picture_and_the_face_of_the_active_profile(self):
        p = cp.create_profile("Astro")
        cp.set_active(p["slug"])
        newer = os.path.join(os.path.dirname(self.pic), "astronar.png")
        with open(newer, "wb") as fh:
            fh.write(b"ASTRONAR")
        updated = cp.set_avatar_file(p["slug"], newer)
        self.assertEqual(open(updated["avatar"], "rb").read(), b"ASTRONAR")
        self.assertEqual(cp.load(p["slug"])["avatar"], updated["avatar"])
        self.assertEqual(open(cp.FACE_PATH, "rb").read(), b"ASTRONAR")

    def test_set_avatar_file_leaves_the_face_alone_for_a_profile_that_is_not_active(self):
        active, other = cp.create_profile("Me", avatar=self.pic), cp.create_profile("Them")
        cp.set_active(active["slug"])
        newer = os.path.join(os.path.dirname(self.pic), "theirs.png")
        with open(newer, "wb") as fh:
            fh.write(b"THEIRS")
        cp.set_avatar_file(other["slug"], newer)
        self.assertEqual(open(cp.FACE_PATH, "rb").read(), b"PNGDATA")
        with self.assertRaises(cp.ProfileError):
            cp.set_avatar_file("nobody", newer)

    def test_pin_verifies_and_is_not_stored_in_clear(self):
        p = cp.create_profile("Wife", pin="4321")
        stored = open(os.path.join(cp.profile_dir(p["slug"]), "profile.json")).read()
        self.assertNotIn("4321", stored)
        loaded = cp.load(p["slug"])
        self.assertTrue(cp.verify_pin(loaded, "4321"))
        self.assertFalse(cp.verify_pin(loaded, "1234"))
        self.assertFalse(cp.verify_pin(loaded, ""))
        self.assertTrue(cp.verify_pin(cp.create_profile("NoPin"), "anything"))   # nothing to check
        cp.set_pin(p["slug"], None)
        self.assertFalse(cp.has_pin(cp.load(p["slug"])))

    def test_pin_rules(self):
        for bad in ("12", "abcd", "1" * 13):
            with self.assertRaises(cp.ProfileError):
                cp.create_profile("X", pin=bad)

    def test_active_profile_paths_and_face(self):
        self.assertTrue(cp.profile_path("favorites.txt").startswith(cp.CONFIG_DIR))   # no profile yet: shared
        p = cp.create_profile("Skye", avatar=self.pic)
        cp.set_active(p["slug"])
        self.assertEqual(cp.active_slug(), p["slug"])
        self.assertEqual(cp.profile_path("favorites.txt"), os.path.join(cp.profile_dir(p["slug"]), "favorites.txt"))
        self.assertEqual(open(cp.FACE_PATH, "rb").read(), b"PNGDATA")
        self.assertEqual(cp.session_environment()["COSMOS_PROFILE"], p["slug"])

    def test_two_profiles_keep_separate_files(self):
        a, b = cp.create_profile("A"), cp.create_profile("B")
        cp.set_active(a["slug"]); pa = cp.profile_path("favorites.txt")
        cp.set_active(b["slug"]); pb = cp.profile_path("favorites.txt")
        self.assertNotEqual(pa, pb)

    def test_delete_clears_active(self):
        p = cp.create_profile("Gone")
        cp.set_active(p["slug"])
        cp.delete_profile(p["slug"])
        self.assertIsNone(cp.active_slug())
        self.assertEqual(cp.list_profiles(), [])

    def test_stale_active_file_is_ignored(self):
        os.makedirs(cp.CONFIG_DIR)
        open(cp.ACTIVE_FILE, "w").write("ghost\n")
        self.assertIsNone(cp.active_slug())

    def test_migrating_pre_profile_state(self):
        os.makedirs(cp.CONFIG_DIR)
        open(os.path.join(cp.CONFIG_DIR, "favorites.txt"), "w").write("a.desktop\n")
        open(cp.FACE_PATH, "wb").write(b"OLDFACE")
        p = cp.migrate_legacy("Skye Morrison")
        self.assertEqual(p["name"], "Skye Morrison")
        self.assertEqual(open(os.path.join(cp.profile_dir(p["slug"]), "favorites.txt")).read(), "a.desktop\n")
        self.assertIsNone(cp.migrate_legacy("again"))        # only once

    def test_no_migration_when_nothing_to_carry(self):
        self.assertIsNone(cp.migrate_legacy("x"))


def gtk_available():
    """True only with a real display to construct widgets against: a GTK-ERROR from trying to without one (e.g. a plain GtkLabel needing a
    GtkStyleContext) is fatal at the C level and aborts the whole process - not something a try/except can catch - so this checks for
    WAYLAND_DISPLAY/DISPLAY first and never actually constructs anything when neither is set."""
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
class PowerButtons(unittest.TestCase):
    """The Restart/Shutdown buttons cosmos-profiles.py adds to the pick page: they must go through kronos's system.power action,
    not a raw systemctl call - the desktop account has no other path to a power action, and kronos audits it either way."""

    def picker_module(self):
        import importlib.machinery
        import importlib.util
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cosmos-profiles.py")
        loader = importlib.machinery.SourceFileLoader("cosmos_profiles_app_under_test", path)
        module = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
        loader.exec_module(module)
        return module

    def test_restart_and_shutdown_ask_kronos_not_systemctl_directly(self):
        module = self.picker_module()
        win = module.Picker.__new__(module.Picker)  # no __init__: nothing here touches the widgets it would build
        win.pick_error = module.Gtk.Label()
        asked = []
        real_ask = module.cosmos_kronos.ask
        module.cosmos_kronos.ask = lambda action, args=None, **kw: (asked.append((action, args)), {"status": "ok"})[1]
        try:
            win.power("reboot")
            win.power("poweroff")
        finally:
            module.cosmos_kronos.ask = real_ask
        self.assertEqual(asked, [("system.power", {"verb": "reboot"}), ("system.power", {"verb": "poweroff"})])

    def test_a_refusal_is_shown_not_swallowed(self):
        module = self.picker_module()
        win = module.Picker.__new__(module.Picker)
        win.pick_error = module.Gtk.Label()
        module.cosmos_kronos.ask = lambda action, args=None, **kw: {"status": "refused", "reason": "no admin session"}
        win.power("reboot")
        self.assertEqual(win.pick_error.get_text(), "no admin session")
        self.assertTrue(win.pick_error.get_visible())


@unittest.skipUnless(gtk_available(), "needs GTK")
class DeleteEventGuard(unittest.TestCase):
    """A window-close request (Alt+X, chiefly) must not be able to quit out of first-run profile
    creation - there is no profile picker behind it yet to fall back to, so closing here would
    strand the session with no way to ever sign in."""

    def picker_module(self):
        import importlib.machinery
        import importlib.util
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cosmos-profiles.py")
        loader = importlib.machinery.SourceFileLoader("cosmos_profiles_delete_event_under_test", path)
        module = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
        loader.exec_module(module)
        return module

    def win_on_page(self, module, page, has_profiles):
        win = module.Picker.__new__(module.Picker)
        win.stack = module.Gtk.Stack()
        for name in ("pick", "pin", "create"):
            win.stack.add_named(module.Gtk.Box(), name)
        win.stack.show_all()                    # set_visible_child_name needs the children shown to take effect
        win.stack.set_visible_child_name(page)
        while module.Gtk.events_pending():
            module.Gtk.main_iteration()
        real_list_profiles = module.profiles.list_profiles
        module.profiles.list_profiles = lambda: (["someone"] if has_profiles else [])
        self.addCleanup(setattr, module.profiles, "list_profiles", real_list_profiles)
        return win

    def test_closing_first_run_create_is_refused(self):
        module = self.picker_module()
        win = self.win_on_page(module, "create", has_profiles=False)
        self.assertTrue(win.on_delete(win, None))

    def test_closing_the_pin_page_is_refused_if_it_is_somehow_the_only_thing_left(self):
        module = self.picker_module()
        win = self.win_on_page(module, "pin", has_profiles=False)
        self.assertTrue(win.on_delete(win, None))

    def test_closing_create_is_allowed_once_a_profile_exists_to_fall_back_to(self):
        module = self.picker_module()
        win = self.win_on_page(module, "create", has_profiles=True)
        self.assertFalse(win.on_delete(win, None))

    def test_closing_the_pick_page_is_always_allowed(self):
        module = self.picker_module()
        win = self.win_on_page(module, "pick", has_profiles=False)
        self.assertFalse(win.on_delete(win, None))


if __name__ == "__main__":
    unittest.main()
