"""Tests for find-uncurated: what is installed but not in the catalog, and removing the user-level part."""
import importlib.machinery
import importlib.util
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
loader = importlib.machinery.SourceFileLoader("find_uncurated", os.path.join(HERE, "find-uncurated"))
spec = importlib.util.spec_from_loader("find_uncurated", loader)
fu = importlib.util.module_from_spec(spec)
loader.exec_module(fu)

CAT = {"flatpak_families": ["org.freedesktop.Platform"], "apps": [
    {"name": "Krita", "category": "Creative", "flatpak": "org.kde.krita"},
    {"name": "Ventoy", "category": "Utilities", "appimage": ["ventoy"]}]}


class R:
    def __init__(self, out="", code=0):
        self.stdout, self.returncode = out, code


def fake(flatpaks="", appimages=None, calls=None):
    def run(cmd, **kw):
        if calls is not None:
            calls.append(cmd)
        if cmd[0] == "flatpak" and cmd[1] == "list":
            return R(flatpaks)
        if cmd[0] in ("appman", "am") and cmd[1] == "-f":
            return R(appimages, 0) if appimages is not None else R("", 1)
        return R()
    return run


class Scan(unittest.TestCase):
    def test_only_what_the_catalog_does_not_list_is_reported(self):
        run = fake("org.kde.krita\tsystem\norg.mozilla.firefox\tuser\ncom.foo.Bar\tsystem\n", "◆ ventoy  10 MiB\n◆ evil-app  5 MiB\n")
        found = fu.scan(run, CAT)
        self.assertEqual({(f["kind"], f["id"], f["where"]) for f in found},
                         {("flatpak", "org.mozilla.firefox", "user"), ("flatpak", "com.foo.Bar", "system"), ("appimage", "evil-app", "user")})

    def test_a_clean_system_reports_nothing_and_no_catalog_means_no_curation(self):
        self.assertEqual(fu.scan(fake("org.kde.krita\tuser\n", "◆ ventoy\n"), CAT), [])
        self.assertEqual(fu.scan(fake("org.mozilla.firefox\tuser\n"), {}), [])

    def test_missing_tools_are_not_an_error(self):
        def run(cmd, **kw):
            raise FileNotFoundError(cmd[0])
        self.assertEqual(fu.scan(run, CAT), [])


class Purge(unittest.TestCase):
    def test_user_flatpaks_and_appimages_are_removed_but_system_ones_only_reported(self):
        calls = []
        run = fake("org.mozilla.firefox\tuser\ncom.foo.Bar\tsystem\n", "◆ evil-app\n", calls)
        removed, left = fu.purge(fu.scan(run, CAT), run)
        self.assertEqual(sorted(removed), ["evil-app", "org.mozilla.firefox"])
        self.assertEqual(left, ["com.foo.Bar"])
        self.assertIn(["flatpak", "uninstall", "--user", "-y", "--noninteractive", "org.mozilla.firefox"], calls)
        self.assertIn(["appman", "-r", "evil-app"], calls)
        self.assertFalse([c for c in calls if c[:2] == ["flatpak", "uninstall"] and "com.foo.Bar" in c])          # never touches system installs


if __name__ == "__main__":
    unittest.main()
