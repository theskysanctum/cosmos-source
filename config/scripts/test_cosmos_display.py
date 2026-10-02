import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cosmos_display as d


class Factor(unittest.TestCase):
    def test_the_reference_screen_is_1(self):
        self.assertEqual(d.ui_factor(1920, 1080), 1.0)

    def test_a_smaller_screen_never_shrinks_the_layout(self):
        self.assertEqual(d.ui_factor(1280, 720), 1.0)
        self.assertEqual(d.ui_factor(800, 600), 1.0)

    def test_a_bigger_logical_screen_grows_it_by_the_smaller_ratio(self):
        self.assertEqual(d.ui_factor(3840, 2160), 2.0)
        self.assertAlmostEqual(d.ui_factor(2560, 1440), 4 / 3)
        self.assertEqual(d.ui_factor(3840, 1080), 1.0)          # an ultrawide is only as tall as 1080

    def test_it_is_capped(self):
        self.assertEqual(d.ui_factor(15360, 8640), d.MAX_FACTOR)

    def test_nonsense_sizes_are_1(self):
        self.assertEqual(d.ui_factor(0, 0), 1.0)


class Fit(unittest.TestCase):
    def test_a_window_that_fits_is_left_alone_on_the_reference_screen(self):
        self.assertEqual(d.fit_size(1000, 640, 1920, 1080), (1000, 640))

    def test_a_window_bigger_than_the_screen_is_clamped(self):
        self.assertEqual(d.fit_size(1180, 780, 1280, 720), (1177, 662))

    def test_a_tiny_screen_still_gets_a_window_that_fits(self):
        w, h = d.fit_size(1180, 780, 800, 600)
        self.assertLessEqual(w, 800)
        self.assertLessEqual(h, 600)

    def test_windows_grow_on_a_big_logical_screen(self):
        self.assertEqual(d.fit_size(1000, 600, 3840, 2160), (2000, 1200))

    def test_natural_height_stays_natural(self):
        self.assertEqual(d.fit_size(480, -1, 1920, 1080), (480, -1))
        self.assertEqual(d.fit_size(480, -1, 300, 300)[1], -1)

    def test_a_portrait_screen_clamps_the_width(self):
        self.assertEqual(d.fit_size(1180, 780, 720, 1280)[0], 662)


class ScaleCss(unittest.TestCase):
    def setUp(self):
        self._factor = d.factor
        d.factor = lambda widget=None: 2.0
    def tearDown(self):
        d.factor = self._factor

    def test_px_lengths_are_multiplied(self):
        self.assertEqual(d.scale_css("a { padding: 6px 10px; font-size: 14px; margin: 0px; }"), "a { padding: 12px 20px; font-size: 28px; margin: 0px; }")

    def test_other_units_and_names_are_left_alone(self):
        self.assertEqual(d.scale_css("a { font-size: 12pt; width: 50%; } .w-6px { color: #fff6px; }"), "a { font-size: 12pt; width: 50%; } .w-6px { color: #fff6px; }")

    def test_bytes_in_bytes_out(self):
        self.assertEqual(d.scale_css(b"a { padding: 3px; }"), b"a { padding: 6px; }")

    def test_nothing_changes_at_the_reference_size(self):
        d.factor = lambda widget=None: 1.0
        self.assertEqual(d.scale_css("a { padding: 3px; }"), "a { padding: 3px; }")

    def test_a_one_pixel_border_never_vanishes(self):
        d.factor = lambda widget=None: 1.25
        self.assertEqual(d.scale_css("a { border: 1px solid red; }"), "a { border: 1px solid red; }")


class NoDisplay(unittest.TestCase):
    def test_no_compositor_means_factor_1(self):
        old = os.environ.get("XDG_RUNTIME_DIR")
        os.environ["XDG_RUNTIME_DIR"] = "/nonexistent"
        try:
            self.assertEqual(d.compositor_factor(), 1.0)
        finally:
            if old is None:
                del os.environ["XDG_RUNTIME_DIR"]
            else:
                os.environ["XDG_RUNTIME_DIR"] = old

    def test_without_a_display_everything_falls_back_to_the_reference_screen(self):
        old = os.environ.pop("WAYLAND_DISPLAY", None), os.environ.pop("DISPLAY", None)
        try:
            self.assertEqual(d.logical_size(), (1920, 1080))
            self.assertEqual(d.monitor_scale(), 1)
            self.assertEqual(d.px(10), 10)
        except Exception as exc:                                  # a machine with no usable Gdk at all: the helpers must not be what breaks
            self.skipTest(f"no Gdk: {exc}")
        finally:
            for name, value in zip(("WAYLAND_DISPLAY", "DISPLAY"), old):
                if value is not None:
                    os.environ[name] = value


if __name__ == "__main__":
    unittest.main()
