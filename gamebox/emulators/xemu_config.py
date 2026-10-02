#!/usr/bin/env python3
"""Writes xemu's own config (xemu.toml) before every Xbox launch. xemu takes no game on its command line: the disc image, the BIOS files and
"start full screen" are all settings in $HOME/.local/share/xemu/xemu/xemu.toml (HOME is the emulator home, see library.launch_game). GameBox is the
only front end, so this file is owned here and rewritten each time; xemu fills in every setting not named.

The Xbox BIOS is not shipped (it is Microsoft's). Put these three files in BIOS_DIR:
  mcpx_1.0.bin          the boot ROM
  Complex_4627v1.03.bin the flash ROM (any Xbox BIOS image works; the name here is the common one)
  xbox_hdd.qcow2        the hard disk image (an empty formatted one from xemu's docs works)
(eeprom.bin, the console's 256-byte EEPROM, is created by xemu the first time it runs.)
missing_files() says which of those three are absent, so GameBox can tell the player instead of showing xemu's own error."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import paths  # noqa: E402

BIOS_DIR = Path("/cosmos/bios/xbox")
FILES = {"bootrom_path": "mcpx_1.0.bin", "flashrom_path": "Complex_4627v1.03.bin", "eeprom_path": "eeprom.bin", "hdd_path": "xbox_hdd.qcow2"}
CONFIG_PATH = paths.EMULATOR_HOME / ".local" / "share" / "xemu" / "xemu" / "xemu.toml"


REQUIRED = [name for key, name in FILES.items() if key != "eeprom_path"]       # xemu makes the EEPROM itself


def missing_files(bios_dir=BIOS_DIR):
	return [name for name in REQUIRED if not (Path(bios_dir) / name).is_file()]


def _quote(value):
	# JSON's string escapes are TOML's too, so a file name can never end the string or add a setting
	return json.dumps(str(value), ensure_ascii=False)


def render(dvd_path, bios_dir=BIOS_DIR):
	files = "\n".join(f"{key} = {_quote(Path(bios_dir) / name)}" for key, name in FILES.items())
	return (
		"[general]\nshow_welcome = false\n\n[general.updates]\ncheck = false\n\n"
		f"[sys.files]\n{files}\ndvd_path = {_quote(dvd_path)}\n\n"
		"[display.window]\nfullscreen_on_startup = true\n\n"
		# a fresh xemu has nothing bound to any controller port, so no input reaches the game until something is
		'[input.bindings]\nport1_driver = "usb-xbox-gamepad"\nport1 = "keyboard"\n'
	)


def ensure_config(dvd_path, bios_dir=BIOS_DIR, config_path=CONFIG_PATH):
	config_path = Path(config_path)
	config_path.parent.mkdir(parents=True, exist_ok=True)
	config_path.write_text(render(dvd_path, bios_dir), encoding="utf-8")
	return config_path
