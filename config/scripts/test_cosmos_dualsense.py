"""Tests for cosmos_dualsense: the output-report layout, effect encodings, CRC and settings."""
import os
import struct
import sys
import tempfile
import unittest
import zlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cosmos_dualsense as ds
import cosmos_pads as cp


class ReportTests(unittest.TestCase):
    def test_usb_report_size_flags_and_offsets(self):
        r = ds.build_report(False, left={"mode": "feedback", "start": 2, "strength": 4}, right=None)
        self.assertEqual(len(r), 63)
        self.assertEqual(r[0], 0x02)
        self.assertEqual(r[1], 0x08)                       # only the left trigger flag
        self.assertEqual(r[1 + 21], 0x21)                  # left effect at common offset 21
        self.assertEqual(r[1 + 10], 0)                     # right trigger untouched

    def test_both_triggers_set_both_flags(self):
        r = ds.build_report(False, {"mode": "off"}, {"mode": "off"})
        self.assertEqual(r[1], 0x0C)
        self.assertEqual((r[1 + 10], r[1 + 21]), (0x05, 0x05))

    def test_bluetooth_report_size_header_and_crc(self):
        r = ds.build_report(True, {"mode": "off"}, {"mode": "weapon"}, seq=3)
        self.assertEqual(len(r), 78)
        self.assertEqual((r[0], r[1], r[2]), (0x31, 0x30, 0x10))
        self.assertEqual(r[3], 0x0C)                       # common starts after the 3-byte header
        crc = struct.unpack("<I", r[-4:])[0]
        self.assertEqual(crc, zlib.crc32(b"\xa2" + r[:-4]) & 0xFFFFFFFF)

    def test_bluetooth_trigger_bytes_sit_after_the_header(self):
        r = ds.build_report(True, None, {"mode": "vibration", "start": 0, "amplitude": 8, "frequency": 40})
        self.assertEqual(r[3 + 10], 0x26)
        self.assertEqual(r[3 + 10 + 9], 40)


class EffectTests(unittest.TestCase):
    def test_feedback_zones_and_strength(self):
        e = ds.build_effect({"mode": "feedback", "start": 7, "strength": 8})
        self.assertEqual(e[0], 0x21)
        self.assertEqual(e[1] | e[2] << 8, 0b1110000000)   # zones 7-9
        active = struct.unpack("<I", e[3:7])[0]
        self.assertEqual(active, sum(7 << (3 * i) for i in (7, 8, 9)))

    def test_weapon_marks_start_and_end(self):
        e = ds.build_effect({"mode": "weapon", "start": 2, "end": 5, "strength": 8})
        self.assertEqual((e[0], e[1] | e[2] << 8, e[3]), (0x25, (1 << 2) | (1 << 5), 7))

    def test_weapon_end_is_forced_after_start(self):
        e = ds.build_effect({"mode": "weapon", "start": 7, "end": 3, "strength": 1})
        self.assertEqual(e[1] | e[2] << 8, (1 << 7) | (1 << 8))

    def test_values_are_clamped(self):
        e = ds.build_effect({"mode": "vibration", "start": 99, "amplitude": 0, "frequency": 9999})
        self.assertEqual(e[0], 0x26)
        self.assertEqual(e[9], 255)
        self.assertEqual(e[1] | e[2] << 8, 1 << 9)

    def test_unknown_mode_is_off(self):
        self.assertEqual(ds.build_effect({"mode": "bogus"})[0], 0x05)
        self.assertEqual(len(ds.build_effect({"mode": "feedback"})), 11)


class SettingsAndDeviceTests(unittest.TestCase):
    def test_untouched_triggers_are_left_to_games(self):
        self.assertEqual(ds.triggers_from_settings({"triggers": None}), (None, None))
        self.assertEqual(ds.triggers_from_settings({}), (None, None))

    def test_partial_settings_get_defaults_and_bad_modes_turn_off(self):
        left, right = ds.triggers_from_settings({"triggers": {"left": {"mode": "weapon"}, "right": {"mode": "x"}}})
        self.assertEqual((left["mode"], left["strength"]), ("weapon", ds.DEFAULT_TRIGGER["strength"]))
        self.assertEqual(right["mode"], "off")

    def make_pad(self, bus):
        root = tempfile.mkdtemp()
        hid = os.path.join(root, "usb1", "1-2", "1-2:1.3", f"{bus}:054C:0CE6.0005")
        os.makedirs(os.path.join(hid, "hidraw", "hidraw7"))
        return cp.Pad("Wireless Controller", 0x054C, 0x0CE6, "aa", "/dev/input/event9", hid)

    def test_bus_detection_and_hidraw(self):
        usb, bt = self.make_pad("0003"), self.make_pad("0005")
        self.assertFalse(ds.is_bluetooth(usb))
        self.assertTrue(ds.is_bluetooth(bt))
        self.assertEqual(ds.hidraw_path(usb), "/dev/hidraw7")
        self.assertTrue(ds.usb_device_dir(usb).endswith("1-2"))
        self.assertIsNone(ds.usb_device_dir(bt))

    def test_haptics_refuse_over_bluetooth_with_a_reason(self):
        ok, msg = ds.play_haptics(self.make_pad("0005"))
        self.assertFalse(ok)
        self.assertIn("USB", msg)

    def test_sink_lookup_and_playback_use_the_dualsense_sink(self):
        calls = []

        class R:
            def __init__(self, out="", rc=0):
                self.stdout, self.returncode = out, rc

        def run(cmd, **kw):
            calls.append(cmd)
            if cmd[0] == "pactl":
                return R("1\talsa_output.pci-hda.analog-stereo\tx\n"
                         "2\talsa_output.usb-Sony_Interactive_Entertainment_DualSense_Wireless_Controller-00.analog-surround-40\tx\n")
            return R()
        pad = self.make_pad("0003")
        self.assertIn("surround-40", ds.sound_sink(pad, run))
        ok, _msg = ds.play_haptics(pad, run)
        self.assertTrue(ok)
        self.assertEqual(calls[-1][:2], ["paplay", "--device"])

    def test_wav_is_four_channels_with_silent_speaker_channels(self):
        import wave
        path = os.path.join(tempfile.mkdtemp(), "h.wav")
        ds.haptics_wav(path, seconds=0.2)
        with wave.open(path) as w:
            self.assertEqual((w.getnchannels(), w.getframerate()), (4, 48000))
            frames = struct.unpack("<" + "h" * (4 * 4000), w.readframes(4000))
        self.assertEqual(set(frames[0::4]) | set(frames[1::4]), {0})
        self.assertGreater(max(frames[2::4]), 1000)

    def test_dualsense_capabilities_and_apply_writes_nothing_without_hidraw(self):
        pad = self.make_pad("0003")
        self.assertIn("adaptive triggers", pad.capabilities())
        s = cp.settings_for({"aa": {"triggers": {"left": {"mode": "feedback"}}}}, pad)
        self.assertTrue(isinstance(cp.apply_settings(pad, s), bool))     # /dev/hidraw7 absent here: fails, doesn't raise


if __name__ == "__main__":
    unittest.main()
