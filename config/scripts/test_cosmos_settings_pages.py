"""The Settings pages that embed other CosmOS apps: they must not start copies of Settings, and every page's module has to load."""
import os
import subprocess
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
class Embedding(unittest.TestCase):
    def test_the_controller_daemon_is_started_from_the_controllers_script_not_from_whatever_is_running(self):
        import importlib.machinery
        import importlib.util
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cosmos-controllers.py")
        loader = importlib.machinery.SourceFileLoader("cosmos_controllers_under_test", path)
        module = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
        loader.exec_module(module)
        started = []
        real_popen, real_running, real_argv = subprocess.Popen, module.daemon_running, sys.argv
        module.subprocess.Popen = lambda cmd, **kw: started.append((cmd, kw))
        module.daemon_running = lambda: False
        sys.argv = ["/usr/bin/cosmos-settings", "--page", "controllers"]         # what argv is when Settings is the running program
        try:
            module.start_daemon()
        finally:
            module.subprocess.Popen, module.daemon_running, sys.argv = real_popen, real_running, real_argv
        self.assertEqual([c for c, _ in started], [[os.path.realpath(path), "--daemon"]])
        streams = started[0][1]                                    # a daemon must not keep its parent's pipes open (a caller reading our output would wait for ever)
        self.assertEqual((streams["stdin"], streams["stdout"], streams["stderr"]), (subprocess.DEVNULL,) * 3)

    def test_every_page_in_the_sidebar_has_a_builder_and_a_unique_id(self):
        import cosmos_settings_pages as pages
        ids = [item[0] for item in pages.NAV if not isinstance(item, str)]
        self.assertEqual(len(ids), len(set(ids)))
        for item in pages.NAV:
            if not isinstance(item, str):
                self.assertTrue(callable(item[2]), item[0])

    def test_the_nav_names_pages_the_redirects_use(self):
        import cosmos_settings_pages as pages
        ids = {item[0] for item in pages.NAV if not isinstance(item, str)}
        for page in ("controllers", "remote", "wallpaper", "admin", "android", "winpack", "vm", "updates", "display", "downloads", "about"):
            self.assertIn(page, ids)


if __name__ == "__main__":
    unittest.main()
