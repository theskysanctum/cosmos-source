"""Tests for the xemu config GameBox writes before an Xbox launch, and for the emulators built from source (paths and launch lines)."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
_SCRATCH = Path.home() / ".placements" / "tests"
_SCRATCH.mkdir(parents=True, exist_ok=True)
tempfile.tempdir = str(_SCRATCH)            # scratch files live in ~/.placements, not /tmp

import xemu_config as xc  # noqa: E402


class ConfigTests(unittest.TestCase):
	def test_the_config_names_the_disc_bios_files_and_fullscreen(self):
		text = xc.render("/cosmos/roms/xbox/Halo.iso", "/b")
		self.assertIn('dvd_path = "/cosmos/roms/xbox/Halo.iso"', text)
		self.assertIn('bootrom_path = "/b/mcpx_1.0.bin"', text)
		self.assertIn('hdd_path = "/b/xbox_hdd.qcow2"', text)
		self.assertIn("fullscreen_on_startup = true", text)
		self.assertIn("show_welcome = false", text)
		self.assertIn('port1 = "keyboard"', text)          # without a binding the game gets no input at all

	def test_a_hostile_file_name_cannot_break_out_of_its_string_or_add_settings(self):
		text = xc.render('/x/a"\nfullscreen_on_startup = false\n[evil]\n.iso', "/b")
		self.assertEqual(len(text.splitlines()), len(xc.render("/x/a.iso", "/b").splitlines()))
		self.assertNotIn("\n[evil]", text)
		self.assertEqual(text.count("fullscreen_on_startup"), 2)      # the real setting, and the text inside the quoted name

	def test_missing_bios_files_are_named(self):
		d = tempfile.mkdtemp()
		self.assertEqual(sorted(xc.missing_files(d)), ["Complex_4627v1.03.bin", "mcpx_1.0.bin", "xbox_hdd.qcow2"])
		Path(d, "mcpx_1.0.bin").write_bytes(b"x")
		self.assertNotIn("mcpx_1.0.bin", xc.missing_files(d))
		self.assertNotIn("eeprom.bin", xc.missing_files(d))            # xemu creates it

	def test_ensure_config_writes_the_file(self):
		d = tempfile.mkdtemp()
		path = xc.ensure_config("/r/g.iso", "/b", Path(d, "a", "b", "xemu.toml"))
		self.assertIn("/r/g.iso", path.read_text())


class SystemsTests(unittest.TestCase):
	def platforms(self):
		data = json.load(open(HERE.parent / "library" / "systems.json", encoding="utf-8"))
		return {p["id"]: p for c in data["categories"] for m in c["manufacturers"] for p in m["platforms"]}

	def test_xbox_and_sega_use_the_source_built_emulators(self):
		p = self.platforms()
		self.assertEqual(p["xbox"]["launch_template"], ["{emu}/xemu/xemu"])
		self.assertTrue(p["xbox"]["xemu"])
		for name in ("dreamcast", "atomiswave"):
			self.assertEqual(p[name]["launch_template"][0], "{emu}/flycast/flycast")
			self.assertIn("{rom}", p[name]["launch_template"])

	def test_the_default_emulators_are_all_present(self):
		text = json.dumps(self.platforms())
		for needle in ("emulators/bin/retroarch", "dolphin-emu-nogui", "pcsx2-qt", "{emu}/xemu/xemu", "{emu}/flycast/flycast"):
			self.assertIn(needle, text)

	def test_bundled_binaries_exist_for_the_other_three(self):
		for name in ("retroarch", "dolphin-emu-nogui", "pcsx2-qt"):
			self.assertTrue((HERE / "bin" / name).exists(), name)


if __name__ == "__main__":
	unittest.main()
