#!/usr/bin/env python3
"""Xbox disc images: is one a plain XISO (what xemu reads), or a full dump with the video partition in front, and turning the second into the first.

A full redump keeps the game's volume at 0xFD90000, 0x2090000 or 0x18300000; an XISO starts it at 0. xemu wants the XISO, and the rewrite
(extract-xiso -r) also drops the padding and the $SystemUpdate folder, roughly halving a 7.4 GB dump. The tool is extract-xiso, built from source
in the image (see emulators/build/build-emulators.sh) and installed under {emu}/tools/.

    xiso.py IMAGE.iso [--keep-original]        converts in place (or writes IMAGE.xiso.iso beside it and keeps the original)
"""
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

MAGIC = b"MICROSOFT*XBOX*MEDIA"
SECTOR = 2048
VOLUME_OFFSETS = (0, 0xFD90000, 0x2090000, 0x18300000)


def volume_offset(path):
	"""Where the XDVDFS volume starts in the image, or None if it isn't an Xbox disc image."""
	try:
		with open(path, "rb") as fh:
			for base in VOLUME_OFFSETS:
				fh.seek(base + 32 * SECTOR)
				if fh.read(20) == MAGIC:
					return base
	except OSError:
		pass
	return None


def needs_conversion(path):
	"""True for a full dump; False for a plain XISO, and for anything that isn't an Xbox image at all."""
	base = volume_offset(path)
	return base is not None and base != 0


def find_tool():
	emu = os.environ.get("COSMOS_EMULATORS_DIR", "/opt/cosmos-emulators")
	for candidate in (Path(emu) / "tools" / "extract-xiso", Path.home() / ".local" / "bin" / "extract-xiso"):
		if candidate.is_file() and os.access(candidate, os.X_OK):
			return str(candidate)
	return shutil.which("extract-xiso")


def convert(path, keep_original=False, tool=None, log=print):
	"""Rewrites the full dump at `path` as a plain XISO. In place by default (the big original is deleted once the new file is complete);
	with keep_original the result is written beside it as NAME.xiso.iso. Returns (ok, message); never raises, and never loses the original
	unless the new file is fully written."""
	path = Path(path)
	tool = tool or find_tool()
	if not tool:
		return False, "extract-xiso isn't installed"
	if not needs_conversion(path):
		return True, "already a plain XISO" if volume_offset(path) == 0 else "not an Xbox disc image"
	size = path.stat().st_size
	free = shutil.disk_usage(path.parent).free
	if free < size // 2 + (256 << 20):
		return False, f"not enough free space to convert ({size // 2 >> 20} MB needed)"
	work = Path(tempfile.mkdtemp(prefix=".xiso-", dir=path.parent))
	old = path.with_name(path.name + ".old")      # extract-xiso renames the input to this while it works
	try:
		log(f"converting {path.name} to XISO ...")
		proc = subprocess.run([tool, "-r", "-s", "-q", "-d", str(work), str(path)], capture_output=True, text=True)
		made = work / path.name
		if old.exists() and not path.exists():
			os.replace(old, path)                 # put the original back first; what happens next only adds a file
		if proc.returncode != 0 or not made.is_file() or volume_offset(made) != 0:
			return False, "extract-xiso couldn't convert it: " + ((proc.stderr or proc.stdout).strip().splitlines() or ["unknown error"])[-1]
		if keep_original:
			target = path.with_name(path.stem + ".xiso.iso")
			os.replace(made, target)
			return True, f"wrote {target.name}"
		os.replace(made, path)                    # atomic: the game is never missing, and never half-written
		return True, f"converted {path.name} ({size >> 20} MB -> {path.stat().st_size >> 20} MB)"
	except OSError as exc:
		return False, f"conversion failed: {exc}"
	finally:
		shutil.rmtree(work, ignore_errors=True)


def main(argv):
	keep = "--keep-original" in argv
	paths = [a for a in argv if not a.startswith("--")]
	if not paths:
		print(__doc__)
		return 2
	ok, message = convert(paths[0], keep_original=keep)
	print(message)
	return 0 if ok else 1


if __name__ == "__main__":
	sys.exit(main(sys.argv[1:]))
