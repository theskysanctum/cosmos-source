"""Tests for cosmos_pads (the Controller Customizer's hardware layer) against a fake sysfs tree."""
import importlib.util
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cosmos_pads as cp


def make_led(hid, name, maxb=255, multi=False, value="0"):
    d = os.path.join(hid, "leds", name)
    os.makedirs(d)
    open(os.path.join(d, "brightness"), "w").write(value)
    open(os.path.join(d, "max_brightness"), "w").write(str(maxb))
    if multi:
        open(os.path.join(d, "multi_intensity"), "w").write("0 0 0")
    return d


def read(path):
    return open(path).read().strip()


class LedTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def pad(self, hid, vendor=0x054C, product=0x0CE6, uniq="AA:BB"):
        return cp.Pad("Wireless Controller", vendor, product, uniq, "/dev/input/event9", hid)

    def test_dualsense_leds_are_sorted_into_kinds(self):
        hid = os.path.join(self.tmp, "hid")
        make_led(hid, "input9:rgb:indicator", multi=True)
        for n in (3, 1, 2, 4, 5):
            make_led(hid, f"input9:white:player-{n}", maxb=1)
        pad = self.pad(hid)
        self.assertEqual(pad.family, "dualsense")
        self.assertEqual(pad.rgb().kind, "rgb")
        self.assertEqual([led.name[-1] for led in pad.players()], list("12345"))
        self.assertIn("light colour", pad.capabilities())

    def test_switch_pro_has_players_and_home(self):
        hid = os.path.join(self.tmp, "hid")
        for n in range(1, 5):
            make_led(hid, f"input3:green:player-{n}", maxb=1)
        make_led(hid, "input3:green:home", maxb=15)
        pad = self.pad(hid, 0x057E, 0x2009)
        self.assertEqual(pad.family, "switch")
        self.assertEqual(len(pad.players()), 4)
        self.assertEqual(pad.home().max, 15)
        self.assertIsNone(pad.rgb())

    def test_legacy_sony_red_green_blue_become_one_light(self):
        hid = os.path.join(self.tmp, "hid")
        for c in ("red", "green", "blue"):
            make_led(hid, f"0003:054C:09CC.0005:{c}")
        pad = self.pad(hid, 0x054C, 0x09CC)
        rgb = pad.rgb()
        self.assertEqual(pad.family, "ds4")
        self.assertFalse(rgb.multicolor)
        rgb.set_rgb(255, 128, 0, 0.5)
        got = [read(os.path.join(d, "brightness")) for d in rgb.paths]
        self.assertEqual(got, ["128", "64", "0"])

    def test_multicolor_write(self):
        hid = os.path.join(self.tmp, "hid")
        d = make_led(hid, "input9:rgb:indicator", multi=True)
        led = self.pad(hid).rgb()
        self.assertTrue(led.set_rgb(10, 20, 300, 0.5))
        self.assertEqual(read(os.path.join(d, "multi_intensity")), "10 20 255")
        self.assertEqual(read(os.path.join(d, "brightness")), "128")

    def test_no_leds_dir_is_fine(self):
        self.assertEqual(cp.find_leds(os.path.join(self.tmp, "none")), [])

    def test_hid_dir_from_the_input_node(self):
        hid = os.path.join(self.tmp, "devices", "0003:054C:0CE6.0005")
        inputdir = os.path.join(hid, "input", "input12")
        os.makedirs(inputdir)
        cls = os.path.join(self.tmp, "class", "input", "event9")
        os.makedirs(cls)
        os.symlink(inputdir, os.path.join(cls, "device"))
        os.environ["COSMOS_SYSFS_ROOT"] = self.tmp
        try:
            self.assertEqual(cp._hid_dir_of("event9"), os.path.realpath(hid))
        finally:
            del os.environ["COSMOS_SYSFS_ROOT"]

    def test_battery(self):
        hid = os.path.join(self.tmp, "hid")
        ps = os.path.join(hid, "power_supply", "ps-controller-battery-aa")
        os.makedirs(ps)
        open(os.path.join(ps, "capacity"), "w").write("73\n")
        open(os.path.join(ps, "status"), "w").write("Charging\n")
        self.assertEqual(self.pad(hid).battery(), (73, "Charging"))
        self.assertIsNone(self.pad(os.path.join(self.tmp, "other")).battery())


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        hid = os.path.join(self.tmp, "hid")
        self.rgb_dir = make_led(hid, "input9:rgb:indicator", multi=True)
        self.p = [make_led(hid, f"input9:white:player-{n}", maxb=1) for n in (1, 2, 3)]
        self.pad = cp.Pad("Wireless Controller", 0x054C, 0x0CE6, "AA:BB:CC", "/dev/input/event9", hid)

    def test_defaults_and_fallback_to_model(self):
        self.assertEqual(cp.settings_for({}, self.pad)["effect"], "solid")
        data = {"054c:0ce6": {"color": [1, 2, 3]}}
        self.assertEqual(cp.settings_for(data, self.pad)["color"], [1, 2, 3])
        data["aa:bb:cc"] = {"color": [9, 9, 9]}          # the specific controller wins
        self.assertEqual(cp.settings_for(data, self.pad)["color"], [9, 9, 9])

    def test_bad_values_are_repaired(self):
        s = cp.settings_for({"aa:bb:cc": {"color": "red", "brightness": "x", "effect": "disco"}}, self.pad)
        self.assertEqual((s["effect"], s["brightness"]), ("solid", 100))
        self.assertEqual(len(s["color"]), 3)

    def test_save_load_roundtrip(self):
        path = os.path.join(self.tmp, "sub", "controllers.json")
        cp.save_settings({"a": {"color": [1, 2, 3]}}, path)
        self.assertEqual(cp.load_settings(path), {"a": {"color": [1, 2, 3]}})
        self.assertEqual(cp.load_settings(os.path.join(self.tmp, "missing.json")), {})

    def test_apply_writes_colour_brightness_and_players(self):
        s = cp.settings_for({"aa:bb:cc": {"color": [0, 200, 255], "brightness": 50, "players": 2}}, self.pad)
        self.assertTrue(cp.apply_settings(self.pad, s))
        self.assertEqual(read(os.path.join(self.rgb_dir, "multi_intensity")), "0 200 255")
        self.assertEqual(read(os.path.join(self.rgb_dir, "brightness")), "128")
        self.assertEqual([read(os.path.join(d, "brightness")) for d in self.p], ["1", "1", "0"])

    def test_off_effect_darkens(self):
        s = cp.settings_for({"aa:bb:cc": {"effect": "off"}}, self.pad)
        cp.apply_settings(self.pad, s)
        self.assertEqual(read(os.path.join(self.rgb_dir, "brightness")), "0")

    def test_battery_colour_overrides_the_saved_look(self):
        ps = os.path.join(self.tmp, "hid", "power_supply", "ps-controller-battery-aa")
        os.makedirs(ps)
        open(os.path.join(ps, "capacity"), "w").write("8")
        open(os.path.join(ps, "status"), "w").write("Discharging")
        s = cp.settings_for({"aa:bb:cc": {"color": [0, 0, 255], "battery_color": True}}, self.pad)
        cp.apply_settings(self.pad, s, t=0.0)                # t=0 is the "on" half of the blink
        self.assertEqual(read(os.path.join(self.rgb_dir, "multi_intensity")), "255 0 0")
        s2 = cp.settings_for({"aa:bb:cc": {"color": [0, 0, 255]}}, self.pad)     # switched off: the saved colour
        cp.apply_settings(self.pad, s2)
        self.assertEqual(read(os.path.join(self.rgb_dir, "multi_intensity")), "0 0 255")

    def test_battery_colour_without_a_battery_keeps_the_saved_colour(self):
        s = cp.settings_for({"aa:bb:cc": {"color": [1, 2, 3], "battery_color": True}}, self.pad)
        cp.apply_settings(self.pad, s)
        self.assertEqual(read(os.path.join(self.rgb_dir, "multi_intensity")), "1 2 3")

    def test_players_none_leaves_them_alone(self):
        open(os.path.join(self.p[0], "brightness"), "w").write("1")
        cp.apply_settings(self.pad, cp.settings_for({}, self.pad))
        self.assertEqual(read(os.path.join(self.p[0], "brightness")), "1")


class EffectTests(unittest.TestCase):
    def test_breathe_stays_within_range_and_moves(self):
        levels = [cp.effect_frame("breathe", (255, 0, 0), t / 10)[3] for t in range(0, 40)]
        self.assertTrue(all(0.14 <= v <= 1.0 for v in levels))
        self.assertGreater(max(levels) - min(levels), 0.5)

    def test_rainbow_changes_hue_at_full_level(self):
        a, b = cp.effect_frame("rainbow", (0, 0, 0), 0), cp.effect_frame("rainbow", (0, 0, 0), 2)
        self.assertNotEqual(a[:3], b[:3])
        self.assertEqual(a[3], 1.0)

    def test_blink_is_on_then_off_and_speed_changes_the_rate(self):
        levels = [cp.effect_frame("blink", (255, 0, 0), t / 20)[3] for t in range(0, 40)]
        self.assertEqual(set(levels), {0.0, 1.0})
        fast = sum(cp.effect_frame("blink", (1, 1, 1), t / 20, speed=10)[3] for t in range(40))
        self.assertAlmostEqual(fast, 20, delta=2)               # still half on, just more cycles
        slow_edges = sum(1 for a, b in zip(levels, levels[1:]) if a != b)
        fast_levels = [cp.effect_frame("blink", (1, 1, 1), t / 20, speed=10)[3] for t in range(40)]
        self.assertGreater(sum(1 for a, b in zip(fast_levels, fast_levels[1:]) if a != b), slow_edges)

    def test_battery_look(self):
        self.assertIsNone(cp.battery_look(None))
        self.assertIsNone(cp.battery_look((None, "Discharging")))
        self.assertEqual(cp.battery_look((100, "Discharging"))[1], "solid")
        full = cp.battery_look((100, "Full"))
        self.assertGreater(full[0][1], full[0][0])                       # green
        low = cp.battery_look((10, "Discharging"))
        self.assertEqual(low, ((255, 0, 0), "blink"))
        self.assertEqual(cp.battery_look((10, "Charging"))[1], "breathe")   # charging is not an alarm
        mid = cp.battery_look((50, "Discharging"))[0]
        self.assertGreater(mid[0], 100)                                   # yellowish: red and green both up
        self.assertGreater(mid[1], 100)

    def test_solid_is_constant(self):
        self.assertEqual(cp.effect_frame("solid", (1, 2, 3), 5), (1, 2, 3, 1.0))

    def test_families(self):
        self.assertEqual(cp.family_of(0x054C, 0x09CC)[0], "ds4")
        self.assertEqual(cp.family_of(0x057E, 0x2006)[0], "switch")
        self.assertEqual(cp.family_of(0x1234, 1)[0], "other")


def load_gui():
    try:
        spec = importlib.util.spec_from_file_location("cosmos_controllers", os.path.join(os.path.dirname(__file__), "cosmos-controllers.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    except (ImportError, ValueError):
        return None


class ApplierTests(unittest.TestCase):
    def test_applies_on_connect_and_when_settings_change(self):
        gui = load_gui()
        if gui is None:
            self.skipTest("GTK not available")
        tmp = tempfile.mkdtemp()
        hid = os.path.join(tmp, "hid")
        rgb = make_led(hid, "input9:rgb:indicator", multi=True)
        pad = cp.Pad("Wireless Controller", 0x054C, 0x0CE6, "AA", "/dev/input/event9", hid)
        settings = os.path.join(tmp, "controllers.json")
        cp.save_settings({"aa": {"color": [5, 6, 7]}}, settings)
        ap = gui.Applier(discover=lambda: [pad], settings_file=settings)
        ap.poll_settings()
        ap.rescan()
        self.assertEqual(read(os.path.join(rgb, "multi_intensity")), "5 6 7")
        cp.save_settings({"aa": {"color": [8, 9, 10]}}, settings)
        os.utime(settings, ns=(1, 1))                    # make the change visible whatever the clock
        ap.poll_settings()
        self.assertEqual(read(os.path.join(rgb, "multi_intensity")), "8 9 10")

    def test_animate_follows_the_battery_and_skips_identical_frames(self):
        gui = load_gui()
        if gui is None:
            self.skipTest("GTK not available")
        tmp = tempfile.mkdtemp()
        hid = os.path.join(tmp, "hid")
        rgb = make_led(hid, "input9:rgb:indicator", multi=True)
        ps = os.path.join(hid, "power_supply", "b")
        os.makedirs(ps)
        open(os.path.join(ps, "capacity"), "w").write("90")
        open(os.path.join(ps, "status"), "w").write("Discharging")
        pad = cp.Pad("Wireless Controller", 0x054C, 0x09CC, "AA", "/dev/input/event9", hid)
        settings = os.path.join(tmp, "controllers.json")
        cp.save_settings({"aa": {"battery_color": True}}, settings)
        ap = gui.Applier(discover=lambda: [pad], settings_file=settings)
        ap.poll_settings()
        ap.rescan()
        ap.animate()
        first = read(os.path.join(rgb, "multi_intensity"))
        r, g, b = (int(x) for x in first.split())
        self.assertGreater(g, r)                                        # 90 % is green
        open(os.path.join(rgb, "multi_intensity"), "w").write("9 9 9")   # would be rewritten only if a frame is sent
        ap.animate()
        self.assertEqual(read(os.path.join(rgb, "multi_intensity")), "9 9 9")


if __name__ == "__main__":
    unittest.main()
