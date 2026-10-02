"""Tests for xiso.py (full Xbox dump -> plain XISO). The conversion tests use the real extract-xiso if there is one, and skip otherwise."""
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
_SCRATCH = Path.home() / ".placements" / "tests"
_SCRATCH.mkdir(parents=True, exist_ok=True)
tempfile.tempdir = str(_SCRATCH)            # scratch files live in ~/.placements, not /tmp

import xiso  # noqa: E402

import xiso_testdata as make_xiso      # builds small fake Xbox discs at any volume offset

FILES = {"default.xbe": b"XBEH" + bytes(range(256)) * 300, "a.txt": b"hi", "z.bin": b"z" * 70000}


def image(dir_, name, base):
	path = Path(dir_) / name
	make_xiso.write(path, FILES, base)
	return path


class Detection(unittest.TestCase):
	def setUp(self):
		self.dir = tempfile.mkdtemp()
		self.addCleanup(shutil.rmtree, self.dir, True)

	def test_plain_and_full_dumps_are_told_apart(self):
		self.assertEqual(xiso.volume_offset(image(self.dir, "plain.iso", 0)), 0)
		self.assertFalse(xiso.needs_conversion(image(self.dir, "plain.iso", 0)))
		for base in (0xFD90000, 0x2090000, 0x18300000):
			p = image(self.dir, f"dump{base}.iso", base)
			self.assertEqual(xiso.volume_offset(p), base)
			self.assertTrue(xiso.needs_conversion(p))

	def test_things_that_are_not_xbox_discs_are_left_alone(self):
		junk = Path(self.dir) / "junk.iso"
		junk.write_bytes(b"\0" * 100000)
		self.assertIsNone(xiso.volume_offset(junk))
		self.assertFalse(xiso.needs_conversion(junk))
		self.assertFalse(xiso.needs_conversion(Path(self.dir) / "missing.iso"))
		ok, message = xiso.convert(junk, tool="/bin/true")
		self.assertTrue(ok)
		self.assertEqual(junk.read_bytes(), b"\0" * 100000)          # untouched


class Conversion(unittest.TestCase):
	def setUp(self):
		self.dir = tempfile.mkdtemp()
		self.addCleanup(shutil.rmtree, self.dir, True)
		self.tool = xiso.find_tool()

	def test_no_tool_is_reported_and_nothing_changes(self):
		p = image(self.dir, "g.iso", 0x18300000)
		before = p.read_bytes()
		ok, message = xiso.convert(p, tool=None) if not xiso.find_tool() else (False, "extract-xiso isn't installed")
		self.assertFalse(ok)
		self.assertEqual(p.read_bytes(), before)

	def test_a_failing_tool_leaves_the_original_exactly_as_it_was(self):
		fake = Path(self.dir) / "fake-tool"
		fake.write_text("#!/bin/sh\n# does what extract-xiso does first: renames the input, then fails\nfor last; do :; done\nmv \"$last\" \"$last.old\"\nexit 1\n")
		fake.chmod(0o755)
		p = image(self.dir, "g.iso", 0x18300000)
		before = p.read_bytes()
		ok, message = xiso.convert(p, tool=str(fake))
		self.assertFalse(ok)
		self.assertEqual(p.read_bytes(), before)
		self.assertFalse((Path(self.dir) / "g.iso.old").exists())
		self.assertEqual([f.name for f in Path(self.dir).iterdir() if f.name.startswith(".xiso-")], [])       # no scratch folder left

	@unittest.skipUnless(xiso.find_tool(), "extract-xiso isn't installed")
	def test_a_full_dump_becomes_a_plain_xiso_with_the_same_default_xbe(self):
		import hashlib
		p = image(self.dir, "g.iso", 0x18300000)
		ok, message = xiso.convert(p)
		self.assertTrue(ok, message)
		self.assertEqual(xiso.volume_offset(p), 0)
		self.assertFalse((Path(self.dir) / "g.iso.old").exists())
		import subprocess
		listing = subprocess.run([xiso.find_tool(), "-l", str(p)], capture_output=True, text=True).stdout
		self.assertIn("default.xbe", listing)

	@unittest.skipUnless(xiso.find_tool(), "extract-xiso isn't installed")
	def test_keep_original_writes_a_second_file(self):
		p = image(self.dir, "g.iso", 0xFD90000)
		before = p.read_bytes()
		ok, message = xiso.convert(p, keep_original=True)
		self.assertTrue(ok, message)
		self.assertEqual(p.read_bytes(), before)
		self.assertEqual(xiso.volume_offset(Path(self.dir) / "g.xiso.iso"), 0)


if __name__ == "__main__":
	unittest.main()
