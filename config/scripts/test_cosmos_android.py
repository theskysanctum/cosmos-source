import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cosmos_android as ca


class Settings(unittest.TestCase):
    def test_defaults_give_the_gaming_props_from_waydroids_own_documentation(self):
        p = ca.gaming_props({})
        self.assertEqual(p["persist.waydroid.multi_windows"], "false")
        self.assertEqual(p["persist.waydroid.suspend"], "false")
        self.assertEqual(p["persist.waydroid.uevent"], "true")
        self.assertEqual((p["persist.waydroid.width"], p["persist.waydroid.height"]), ("", ""))          # native: cleared
        self.assertTrue(all(k.startswith("persist.waydroid.") for k in p))

    def test_resolutions_and_switches(self):
        p = ca.gaming_props({"resolution": "720p", "keep_awake": False, "gamepads": False})
        self.assertEqual((p["persist.waydroid.width"], p["persist.waydroid.height"]), ("1280", "720"))
        self.assertEqual((p["persist.waydroid.suspend"], p["persist.waydroid.uevent"]), ("true", "false"))
        self.assertEqual(ca.gaming_props({"resolution": "1080p"})["persist.waydroid.width"], "1920")

    def test_bad_settings_are_refused_with_a_sentence(self):
        for bad in ({"resolution": "8k"}, {"keep_awake": "yes"}, {"fake_touch": "com.x; rm -rf /"}, {"fake_wifi": "a" * 92}, {"fake_touch": 5}, {"fake_touch": "a,,b"}):
            with self.assertRaises(ca.AndroidError, msg=str(bad)):
                ca.gaming_props(bad)
        self.assertEqual(ca.gaming_props({"fake_touch": "com.rovio.*,com.gameloft.*"})["persist.waydroid.fake_touch"], "com.rovio.*,com.gameloft.*")

    def test_unknown_keys_are_ignored_and_a_broken_file_means_defaults(self):
        self.assertEqual(ca.clean_settings({"nope": 1}), ca.DEFAULTS)
        path = os.path.join(tempfile.mkdtemp(), "android.json")
        self.assertEqual(ca.load_settings(path), ca.DEFAULTS)
        open(path, "w").write("{broken")
        self.assertEqual(ca.load_settings(path), ca.DEFAULTS)
        ca.save_settings({"resolution": "720p", "fake_wifi": "com.a.b"}, path)
        self.assertEqual(ca.load_settings(path)["resolution"], "720p")
        with self.assertRaises(ca.AndroidError):
            ca.save_settings({"resolution": "nope"}, path)
        self.assertEqual(json.load(open(path))["resolution"], "720p")                     # a refused save changes nothing

    def test_animation_commands(self):
        off = ca.animation_commands({"animations_off": True})
        self.assertEqual(len(off), 3)
        self.assertEqual(off[0], ["waydroid", "shell", "settings", "put", "global", "window_animation_scale", "0"])
        self.assertEqual({c[-1] for c in ca.animation_commands({"animations_off": False})}, {"1"})


class Running(unittest.TestCase):
    def test_apply_sets_each_prop_and_reports_each_result(self):
        calls = []

        def run(argv, timeout=0):
            calls.append(argv)
            return (1, "Session is not running\n") if "persist.waydroid.uevent" in argv else (0, "")
        results = ca.apply_gaming({}, run)
        self.assertIn(["waydroid", "prop", "set", "persist.waydroid.suspend", "false"], calls)
        self.assertIn(["waydroid", "prop", "set", "persist.waydroid.width", ""], calls)
        self.assertEqual({(w, ok) for w, ok, _ in results if not ok}, {("persist.waydroid.uevent", False)})
        self.assertIn("not running", [m for w, ok, m in results if not ok][0])

    def test_status_reads_waydroids_words(self):
        seen = []

        def run(argv, timeout=0):
            seen.append(argv)
            return 0, "Session:\tRUNNING\nContainer:\tFROZEN\nVendor type: MAINLINE\n"
        real = ca.installed
        ca.installed = lambda which=None: True
        try:
            s = ca.status(run)
        finally:
            ca.installed = real
        self.assertEqual((s["session"], s["container"]), ("RUNNING", "FROZEN"))

    def test_setup_asks_once_per_step_in_order_and_downloads_only_when_needed(self):
        asked, said = [], []
        ask = lambda action, args, **kw: (asked.append((action, args)), {"status": "ok"})[1]      # noqa: E731
        self.assertTrue(ca.setup(ask, said.append, is_initialized=lambda: False))
        self.assertEqual(asked, [("service.control", {"unit": ca.SERVICE, "verb": "enable"}), ("service.control", {"unit": ca.SERVICE, "verb": "start"}),
                                 ("run", {"argv": ["waydroid", "init"]})])
        asked.clear()
        ca.setup(ask, said.append, is_initialized=lambda: True)
        self.assertEqual([a for a, _ in asked], ["service.control", "service.control"])

    def test_a_refusal_stops_setup_and_says_why(self):
        asked = []

        def ask(action, args, **kw):
            asked.append(action)
            return {"status": "refused", "reason": "Cancelled - the admin passphrase wasn't entered."}
        with self.assertRaises(ca.AndroidError) as ctx:
            ca.setup(ask, is_initialized=lambda: False)
        self.assertIn("Cancelled", str(ctx.exception))
        self.assertEqual(asked, ["service.control"])                                        # nothing after the first refusal


if __name__ == "__main__":
    unittest.main()
