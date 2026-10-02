#!/usr/bin/env python3
"""Headless equivalent of GameBox's own Deploy GUI dependency-fetch
step (packaging/deploy_gui/deploy_gui.py calls these exact same four
functions from its own "Install deps" button) - lets launch-gamebox run
a real, fully-working GameBox straight off a fresh `git clone`, without
needing that Tkinter GUI tool at all.

Real gap this closes: RetroArch/dolphin-emu-nogui/pcsx2-qt/mkxp-z are
committed straight into the GameBox repo (confirmed by reading
emulators/mkxp_config.py's own docstring), so those work immediately
off a plain clone - but libretro cores (emulators/cores.py), the
non-RGSS game engines like Ren'Py/Love2D/TIC-80/AGS/NW.js
(emulators/engines.py), misc assets (emulators/assets.py), and the
webshell/browser's own Electron runtime (webshell/electron_runtime.py)
are all deliberately NOT committed to git ("fetch a real prebuilt
release at deploy time" - each module's own docstring) - without this,
those specific features would be missing/broken on a first launch.

Every fetch function here is already idempotent (each module's own
is_fetched() check skips anything already present) - confirmed by
reading them, not assumed - so running this before every single launch
is cheap and safe on repeat runs, not just a one-time setup step. The
Electron runtime is the one real exception worth knowing about: ~122MB
to download, ~283MB unpacked, so the very first run on a given machine
takes a while - the project's own accepted tradeoff (electron_runtime.py's
own docstring), not a bug in this script."""
import sys
from pathlib import Path

if len(sys.argv) != 2:
	print(f"usage: {sys.argv[0]} <gamebox-checkout-dir>", file=sys.stderr)
	sys.exit(1)

GAMEBOX_DIR = Path(sys.argv[1]).resolve()

sys.path.insert(0, str(GAMEBOX_DIR / "emulators"))
import assets  # noqa: E402
import cores  # noqa: E402
import engines  # noqa: E402

sys.path.insert(0, str(GAMEBOX_DIR / "webshell"))
import electron_runtime  # noqa: E402


def log(msg):
	print(msg, flush=True)


log("== Fetching GameBox's deploy-time assets (engines/cores/assets/webshell) ==")
failed_engines = engines.fetch_all_engines(log_fn=log)
failed_cores = cores.fetch_all_cores(log_fn=log)
assets.fetch_all_assets(log_fn=log)
electron_runtime.fetch_electron(log_fn=log)

if failed_engines or failed_cores:
	log(f"Done, with some failures - engines: {failed_engines or 'none'}, cores: {failed_cores or 'none'}")
else:
	log("Done - everything fetched (or already present).")
