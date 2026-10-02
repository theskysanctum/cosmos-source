import os
import shutil
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cosmos_display_settings as ds

SCRATCH = os.path.expanduser("~/.placements/tests/display-settings")
MODES = "640x480\t143.99\tpreferred\n1920x1080\t60.00\tcurrent\n1920x1080\t50.00\t\n1280x720\t60.00\t\n3840x2160\t30.00\t\n3840x2160\t59.94\t\n"


class Parsing(unittest.TestCase):
    def test_modes(self):
        modes = ds.parse_modes(MODES)
        self.assertEqual(len(modes), 6)
        self.assertEqual(ds.current_size(modes), (1920, 1080))

    def test_junk_lines_are_skipped(self):
        self.assertEqual(ds.parse_modes("error: nope\n\n1920x1080\tfast\t\n"), [])

    def test_each_size_once_with_the_rate_nearest_60_largest_first(self):
        listed = [(w, h, m["hz"]) for w, h, m in ds.sizes(ds.parse_modes(MODES))]
        self.assertEqual(listed, [(3840, 2160, 59.94), (1920, 1080, 60.0), (1280, 720, 60.0), (640, 480, 143.99)])

    def test_scale_reply(self):
        self.assertEqual(ds.parse_scale_reply("Virtual-1 1920x1080 1.50 1280x720\n"), ("Virtual-1", (1920, 1080), 1.5, (1280, 720)))
        self.assertIsNone(ds.parse_scale_reply("error: something"))

    def test_labels(self):
        self.assertEqual(ds.scale_label("1.25"), "125%")
        self.assertEqual(ds.scale_label("auto"), "Automatic")


class Config(unittest.TestCase):
    def setUp(self):
        shutil.rmtree(SCRATCH, ignore_errors=True)
        os.makedirs(SCRATCH)
        self.addCleanup(shutil.rmtree, SCRATCH, True)
        self.path = os.path.join(SCRATCH, "cosmos", "display.conf")

    def test_a_missing_file_reads_as_automatic(self):
        self.assertEqual(ds.read_conf(self.path), {"mode": None, "scale": None, "animations": None})

    def test_writing_creates_the_file_and_reads_back(self):
        ds.write_conf(mode="1920x1080", scale="1.5", animations="off", path=self.path)
        self.assertEqual(ds.read_conf(self.path), {"mode": "1920x1080", "scale": "1.5", "animations": "off"})

    def test_other_lines_are_kept_and_our_lines_replaced_not_duplicated(self):
        os.makedirs(os.path.dirname(self.path))
        with open(self.path, "w") as fh:
            fh.write("# mine\nmode Virtual-1 = 1280x720\nscale = 2\nmode = 800x600\nanimations = on\n")
        ds.write_conf(mode="1920x1080", scale="1.25", animations="off", path=self.path)
        text = open(self.path).read()
        self.assertIn("# mine", text)
        self.assertIn("mode Virtual-1 = 1280x720", text)
        self.assertEqual(text.count("scale ="), 1)
        self.assertEqual(text.count("animations ="), 1)
        self.assertEqual(ds.read_conf(self.path), {"mode": "1920x1080", "scale": "1.25", "animations": "off"})

    def test_none_removes_a_line_and_keep_leaves_it(self):
        ds.write_conf(mode="1920x1080", scale="2", animations="off", path=self.path)
        ds.write_conf(mode=None, path=self.path)
        self.assertEqual(ds.read_conf(self.path), {"mode": None, "scale": "2", "animations": "off"})

    def test_animations_alone_does_not_touch_mode_or_scale(self):
        ds.write_conf(mode="1920x1080", scale="1.5", path=self.path)
        ds.write_conf(animations="off", path=self.path)
        self.assertEqual(ds.read_conf(self.path), {"mode": "1920x1080", "scale": "1.5", "animations": "off"})


if __name__ == "__main__":
    unittest.main()
