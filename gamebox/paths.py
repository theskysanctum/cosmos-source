#!/usr/bin/env python3
"""The one shared root for every piece of CosmOS-owned *user* data -
profiles, the media library, installed emulator AppImages, caches,
credentials. Every other module that used to derive a data path from
Path.home() imports DATA_ROOT from here instead.

/userdata is a fixed, chmod 777 directory rather than something derived
from whichever real user account happens to be running the app - same
reasoning as always ("wherever the real user's home happens to be" isn't
a meaningful concept for a self-contained appliance), but now also a real,
separate partition on the real installed OS (see atomic/
install_to_filesystem.py's own install()): the whole point of splitting
it out from /cosmos is that reinstalling or upgrading the OS itself -
or a bad update - never has to touch a single profile, save file, or
browser cookie. /cosmos still exists, on the *system* partition -
it's just narrower now, holding only the swappable CosmOS app layer
(see packaging/cosmos_install.py) and the stable boot scripts
(atomic/Containerfile's own "/cosmos/boot" step), nothing this
module's own DATA_ROOT ever points at. This module deliberately doesn't
create /userdata itself (see the project's own notes on bootstrapping
it) - it only names where things go.

Not every Path.home() use in this project belongs here - things that
read *real*, pre-existing system/user data CosmOS doesn't own (the
file manager's default browse directory, Steam/Proton installation
probing for Epic/GOG Windows compatibility) stay Path.home()-based on
purpose."""
from pathlib import Path

DATA_ROOT = Path("/userdata")

# Shared $HOME for launching any emulator that isn't already portable/
# self-contained (RetroArch's own AppImage wrapper redirects its internal
# $HOME on its own, regardless of what's passed in) - see graphical.py's
# env_overrides param and library.py's launch_game().
EMULATOR_HOME = DATA_ROOT / "emulator-home"
