# CosmOS

CosmOS is an image-based Linux desktop and game console in one. It is a Fedora bootc image with its own Wayland desktop that works with a
mouse and keyboard, a touch of a controller, or a controller alone, and it runs Windows games, emulators, Flatpaks and AppImages without a
terminal. Everyone signs in with a profile (a name, a picture and an optional PIN), and everything that needs administrator rights goes
through one small, audited service.

## The desktop

- **Its own compositor and shell.** `cosmos-wm` (built on wlroots) shows one maximized window at a time; dialogs float. The **Windows key**
  (or a controller's **Guide** button) raises the home grid over every window and lowers it again; **Alt+Tab** cycles windows, **Alt+X**
  closes one. `cosmos-bar` provides the top, left and bottom panels (clock, tray, Shop, favorites, apps); `cosmos-launcher` is the home
  screen, a full-screen grid with three tabs: **Apps**, **Web** and **Dev**. Notifications use dunst; the tray hosts StatusNotifier items
  (network, Bluetooth, updates).
- **Web apps.** Brave web apps sit in the Web tab. Right-click one to **rename** it, **change its icon** (a PNG of exactly 128 x 128;
  anything else is refused with the reason), favorite it or remove it. The built-in ones can be renamed and re-iconed but not removed.
- **Profiles.** The desktop signs in automatically and the profile picker asks who is using the machine. A profile keeps its own favorites,
  wallpaper, settings, controller setup and files. A PIN unlocks the lock screen, which the compositor enforces. A profile can start in the
  desktop or straight into GameBox.
- **Looks.** Dark everywhere (GTK 3/4, Qt, Brave, notifications), the Papirus icon theme, the Bibata cursor, and a Plymouth boot splash.
- **Login.** greetd runs `cosmos-greeter`; a fallback login screen is available if the automatic sign-in ever fails.

### Screen size and scale

`cosmos-wm` chooses each output's video mode and scale by itself. The mode is the display's preferred one, except that a preferred mode below
1280x720 is not trusted (QEMU's virtio-vga reports its window size, often 640x480, as "preferred"): the largest mode that fits 1920x1080 is used
instead. The scale is 1x for an ordinary monitor and 2x for a 4K TV (by density up to 32 inches, by height above that), always whole quarters, and never
leaving fewer than 1280 logical pixels across. Apps fit themselves into the logical size (`cosmos_display.py`). To choose yourself, put lines in
`~/.config/cosmos/display.conf` (`mode = 1920x1080`, `scale = 1.5`, or per output `mode Virtual-1 = 1920x1080`, `scale HDMI-A-1 = 2`) or set
`COSMOS_OUTPUT_MODE` / `COSMOS_OUTPUT_SCALE`; the control socket takes `mode WxH` and `scale VALUE|auto` at run time. What was chosen is in the
journal (`journalctl -t cosmos-wm | grep "output "`).

## Controller first

- **`cosmos-input`** turns any gamepad into a system-wide keyboard and mouse, so the greeter, the desktop and the lock screen work with
  nothing else plugged in. A = Enter, B = Esc, X = Delete, Y = Tab, L1/R1 = `[` `]`, d-pad and left stick = arrows, right stick = scroll,
  **hold L2** for pointer mode (A and B click), **Select** opens the on-screen keyboard, **hold R2** for the window switcher, **Guide** toggles
  the home grid.
- **On-screen keyboard**, opened with Select, usable with a pad or a pointer.
- **Controller Customizer** (`cosmos-controllers`), per controller and saved per profile:
  - DualSense and DualShock 4 lightbar: colour, brightness, animation speed, effects (solid, breathe, blink, rainbow, off), or the battery
    level shown on the light.
  - Player-number lights (DualSense, Switch) and the Switch Pro home light.
  - Rumble test with separate strong and weak motors, battery level, and a live gyroscope and accelerometer view.
  - DualSense adaptive triggers (off, resistance, weapon click, buzz) and a haptics test.
  - A **button remapper** for GameBox's four virtual pads: any button to any button or nothing, swapped sticks, inverted axes.

## Apps and software

- **Shop** (`cosmos-shop`). DNF, AppImage (the AM/AppMan catalog) and Flatpak (Flathub) sources, plus **Expansion Packs**: one click installs
  a whole pack. The base image is a gaming and entertainment system; everything else is a pack: **Creative** (GIMP, Krita, Inkscape, darktable,
  Blender, Kdenlive, HandBrake, Audacity, Flameshot), **Music Studio**, **Office** (LibreOffice), **Communication** (Thunderbird, Pidgin,
  Telegram), **Virtualization** (QEMU with the Virtual Machines app, and Waydroid for Android), **Wine**, **Robotics**, **Developer Tools**, **Language Toolchains**, **Game Development**, and **Everything**.
- **One Shop.** The CosmOS Shop is the only shop (GameBox has none). Its **Game Downloads** pack (an Expansion Pack that fetches the GameGrubber
  module) adds **Games** and **BIOS**: pick a system, browse its games in a sorted, paged list (the way GameBox's list worked) or type to search
  within it, and download straight into GameBox's library. A full Xbox disc dump is converted to a plain XISO on the way so xemu can boot it. Both stay hidden until switched on in Settings (one-time disclaimer;
  that switch will live in the universal Settings app). On an ordinary Fedora (installed from the RPMs, see Packaging) its package installs use `sudo dnf`.
- **A curated catalog.** `config/cosmos-apps/catalog.json` is the list of everything that can be installed: about 90 Flatpaks, 75 packages and
  18 AppImages (games, emulators, streaming, media, chat, reading, utilities, system tools, and everything in the Expansion Packs), each with a
  category. Nothing else can be installed from the desktop account: the Shop shows only the catalog, the Flathub remote is filtered to it, Kronos
  refuses other packages and Flatpaks, and `dnf`, `flatpak` and `am`/`appman` typed in a shell check it first (a local `.rpm`, a `.flatpakref` or a URL
  can't be checked, so they are refused). There is no switch to turn it off. Edit the file, run `cosmos_catalog.py verify` (it checks every id against
  Flathub, Fedora/RPM Fusion and AM's list), and rebuild. `find-uncurated` lists (or, with `--purge`, removes) anything installed that isn't in it.
- **One packaging per app.** `config/cosmos-apps/app-policy.json` says whether each app is an RPM or a Flatpak; a second copy of the same app
  in another packaging is refused everywhere (dnf, Flathub, the Shop, the command line).
- **No terminal emulator, anywhere.** None is installed and none can be installed: RPM, Flatpak and AppImage terminals are blocked, a Flatpak
  install is followed by a scan that removes any that slipped through, and the image build fails if one is present. Work that used to need a
  terminal (installs, updates, asset downloads) runs in `cosmos-task`, a window that streams a command's output and takes
  answers to its questions. The grid hides any app that needs a terminal.
- **Updates** (`cosmos-updates`, window and tray). Flatpaks, AppImages and the system (`bootc upgrade`, or `rpm-ostree upgrade` for layered
  packages) in one place, with an Update button per source and one for everything.
- **Included.** GameBox, Steam, itch, Discord, MangoHud, Brave (Bitwarden pre-installed, with the Web tab's web apps), VLC, mpv, Kodi and RPM
  Fusion codecs. Qt apps are Flatpaks; there is no Nautilus.
- **`dnf install` in a shell** is turned into `rpm-ostree install` (applied live where possible); packages that should be permanent belong in
  the Containerfile.

## Files

- **`cosmos-files`** shows only your profile's files, USB and removable drives, and the machine's other internal drives; never `/`, the system
  disk or the rest of the home folder. Paths are resolved (symlinks, `..`) before they are accepted. Unmounted drives mount when clicked.
- **File dialogs for other apps** come from the same view: `cosmos-portal-filechooser` is the xdg-desktop-portal FileChooser, so GTK apps,
  Brave and Flatpak apps can only pick from those places.

## Games

- **GameBox**, the game and emulator front end, runs full screen from a profile or the grid (source in `gamebox/`, baked into the image and
  copied to a writable location on first use). Its Epic and GOG clients replace Heroic; game stores can only be installed with dnf.
- **Game Packs** (`cosmos-winpack`, `cosmos-winpack-cli`). A Windows game folder becomes one compressed SquashFS file that runs under any
  Proton version without root. Creating a pack finds the game's executable, Steam id, ProtonDB rating, the launch options and Proton versions
  that worked for other players, and any automatic fix, and fills in the settings for you. Proton builds come from Steam, a folder, an archive
  or a checksummed GE-Proton / Proton-CachyOS download. The pack's Wine prefix and saves live beside it, never inside the image.
- **Emulators.** GameBox launches five standalone emulators, with no emulator menus of their own: **RetroArch** (cores for the older systems),
  **Dolphin** (GameCube), **PCSX2** (PlayStation 2), **Flycast** (Dreamcast, Atomiswave) and **xemu** (Xbox). RetroArch, Dolphin and PCSX2 are bundled
  in `gamebox/emulators/bin`; Flycast and xemu are compiled from pinned source by the image build itself (`gamebox/emulators/build/`, its own build
  stage, unattended) and installed under `/opt/cosmos-emulators`. xemu needs your own Xbox BIOS files in `/cosmos/bios/xbox` (GameBox writes xemu's config for each
  game and logs any BIOS file that is missing).
- **Play Together** and **Steam Remote Play / game transfers** each open their own temporary firewall door only while in use.
- **Virtual machines** (`cosmos-vm-manager`, a front end for QEMU; Virtualization pack) with host device passthrough: audio, USB devices, host
  keyboard and mouse, and whole PCI devices such as a GPU through VFIO. It checks first and says what is missing.
- **Android** (`cosmos-android`, Waydroid; Virtualization pack). Set up once from the app (one admin passphrase), then open Android full screen.
  Gaming settings: resolution (native, 1080p, 720p), keep the container awake, let Android see controllers directly, animations off, and
  per-app mouse-as-touch and always-Wi-Fi - each one a documented Waydroid property.
- **Button mapper** (`cosmos-touchmap-editor`, from the Android app). Android games expect a touch screen; a profile places touch points on a
  picture of the screen and says what drives each: a **tap** (any controller button, trigger, d-pad direction or key), a **joystick** (a
  stick and/or WASD) or a **look** area (a stick drags the view around). Click **Listen** and press the button or key to fill it in.
  While an Android window has focus, the mapper takes the keys and the controllers and the compositor delivers the touches to that window
  (up to ten fingers at once); the moment focus leaves, everything goes back to normal. Guide still goes Home. Profiles are per game.

## Security and administration

- **No root for the desktop account.** `user` has no `wheel`, no sudo and no password. Anything that needs root asks **Kronos**.
- **Kronos** is a small admin service with a fixed list of typed actions: package changes, OS upgrade, rollback and switch, Flatpaks, services,
  firewall doors, allowlisted files and logs, keyboard grants for VMs, and a passphrase-gated `run` that never starts a shell. Every request
  is checked, written to a tamper-evident, hash-chained **audit log**, and only then carried out by a root helper that checks it again.
  Powerful actions need an **admin passphrase**, which the service asks for itself through a prompt in your session, so it never passes
  through the program that asked. **Admin Activity** (`cosmos-admin`) shows what has been done and lets you change the passphrase.
- **`kronos exec -- COMMAND`** runs an admin command the way sudo would; in a shell the ordinary commands (`dnf`, `systemctl`, `mount`, ...)
  are routed there for you (`cosmos-priv`), and look-only commands like `systemctl status` never ask.
- **Recovery** (`cosmos-recovery`, started with `cosmos.recovery=1` on the kernel command line) is a no-shell app that can roll back the OS,
  reset the admin passphrase, clear a profile PIN or reset the home folder.
- **Firewall.** firewalld rejects everything from the network except named doors. SSH (key-only) is the one default door; the Webtop's HTTPS
  port, Play Together and Steam each have a door that opens only while used and closes itself.
- **Hardening.** The desktop account is not in the `input` group (so no program can read keystrokes), programs can't read each other's memory
  (`ptrace_scope=1`), and a program's admin session belongs to that one process.

## Remote access and development

- **SSH** (key-only, no root login) is managed in **Remote Access** (`cosmos-remote`): switch it on or off, add a public key by pasting it or
  picking a `.pub` file (a private key is refused), and see the host fingerprints.
- **Webtop** runs a full Linux desktop with its own terminal and IDEs in a rootless Podman container, shown in a browser window. It listens
  locally by default; Remote Access can open it to your network over HTTPS behind a password you choose.
- **Claude Code** is installed and used over SSH or in the Webtop.

## Building

```
./cosmos-builder                   # menu: build, install in a VM, test boot, ISO
./build-bootc.sh                   # build localhost/cosmos:latest  (./build-bootc.sh some:tag for another tag)
./build_progress.sh [-f]           # check on a running build
./install_in_disposable_vm.sh      # install and boot the image in a throwaway VM
./test-boot.sh --boot-only         # boot build/test-disk.raw on this machine
./build-iso.sh                     # Anaconda installer ISO
```

- The image is slim by default. `--build-arg PACKS="creative office"` (Bootc Studio: "Packs to bake in") bakes those Expansion Packs into a
  build; the pack definitions in `config/cosmos-apps/expansion-packs.json` are the same ones the Shop uses. Bootc Studio's **Size report** shows
  the image's total size and its biggest build steps.
- The Containerfile's package, pip and npm steps use build cache mounts, so a rebuild after a small change downloads nothing again.
- No API keys are baked into the image or the ISO (anything in an image can be read out of it). GameBox gets box art through the Directory
  API's IGDB proxy when you are signed in to CosmOS online, and a key you enter in GameBox's Settings > API stays on your machine.
- The boot test's install step writes to firmware variables, so it must run inside a disposable VM with its own virtual UEFI firmware
  (`install_in_disposable_vm.sh` does this). It refuses to run on real hardware unless `COSMOS_ALLOW_EFI_NVRAM_CHANGES=1` is set.
- **Bootc Studio** (a separate project) is the GUI for all of this: Full Pass (build, lint, VM install and boot), a Clean up tab that resets
  podman and the test VMs, and background downloads of the base image so builds start faster.

## Packaging

CosmOS is built from RPMs, so every piece also runs on a normal Fedora and the image installs exactly what a Fedora user would. Each package is
described in `packaging/rpm/packages.json` (where every file comes from and goes, its dependencies, licence GPL-3.0-or-later);
`packaging/rpm/build.py` turns that into specs, builds the RPMs, signs them and the repository metadata with the CosmOS key (private half in
`~/.config/cosmos-signing`, outside every repository; the public half is `packaging/repo-site/RPM-GPG-KEY-cosmos`) and leaves a dnf repository under
`~/.placements/rpm/repo`. `packaging/rpm/test-install.sh PACKAGE` installs from it into a clean Fedora container and smoke-tests it. Files install
into one flat `/usr/lib/cosmos/` (the modules import each other by name) with CLI names as symlinks in `/usr/bin`; GameBox is in `/usr/share/gamebox`.

**The image installs them.** The Containerfile's `cosmos-rpms` stage holds `packaging/rpm-repo/`, which `packaging/rpm/prepare-image.sh` fills (Bootc
Studio runs it before every build: the project's `pre_build` in `.bootc-studio.json`), and one `dnf install cosmos-image` puts everything on the
image. What stays in the Containerfile is what is specific to the image: the base packages, third-party downloads (Brave, itch, Bitwarden, Bibata,
gogdl, AM), the web apps, the account, and switching the system parts on (`cosmos-login-enable`, `cosmos-remote-enable`, `cosmos-firewall-enable`).

Packages (all `noarch` except the compositor, GameBox's native pieces and the emulators):

- **Core:** `cosmos-common` (catalog, policy, Expansion Packs, `cosmos-task`), `cosmos-wm` (the compositor), `cosmos-shell` (bar, launcher, lock screen,
  profiles, the session), `cosmos-input`, `cosmos-controllers`, `cosmos-theme` (fonts, backgrounds), `cosmos-portal` (file dialog for other apps).
- **Apps:** `cosmos-shop`, `cosmos-files`, `cosmos-updates`, `cosmos-winpack` (Game Packs), `cosmos-android`, `cosmos-vm-manager`, `cosmos-remote`
  (SSH keys, Webtop), `cosmos-firewall` (the zone and its doors).
- **GameBox:** `cosmos-gamebox` (code and session) plus `-wm`, `-tools`, `-bundled-emulators`, `-streaming`, `-wine`, `-music` (two parts) and
  `-emulators` (xemu, Flycast and extract-xiso built from source by `build-emulators.sh`, kept between builds); `cosmos-gamebox-full` installs all.
- **System-level, opt-in** (they change how a machine works, so installing the desktop never installs them): `cosmos-kronos` (the admin service),
  `cosmos-sudo-shims`, `cosmos-login`, `cosmos-recovery`, `cosmos-theme-defaults`, `cosmos-files-udisks`, `cosmos-plymouth`; each has an enable script
  where one is needed.
- **Meta:** `cosmos-desktop` (the desktop and apps), `cosmos-image` (everything above).
- **`cosmos-repo`** adds the public repository, `https://theskysanctum.github.io/cosmos-repo`, to dnf and installs its signing key.
  `packaging/repo-site/publish.sh` assembles the site (dry run by default; `--push` publishes only the binary packages to the public repository's
  `gh-pages`), so the source repository stays private.

## Tests

Each area has plain Python or shell tests that need no hardware:

```
python3 config/kronos/test_*.py                       # Kronos: actions, decisions, audit log, daemon, image checks
python3 config/scripts/test_cosmos_*.py               # Shop packs, updates, firewall, remote access, files, portal, Game Packs, ...
python3 config/scripts/test_find_terminals.py         # the no-terminal scan
python3 config/scripts/test_cosmos_catalog.py         # the curated catalog, its filter and checks
python3 config/scripts/test_find_uncurated.py         # the scan for things outside the catalog
python3 config/scripts/test_cosmos_gamegrubber.py     # Games and BIOS in the Shop, the module installer
python3 config/scripts/test_xiso.py                   # converting Xbox disc dumps
python3 packaging/rpm/test_build.py                   # the RPM builder (manifest checks, specs, staging)
python3 config/scripts/test_containerfile_cache.py    # the build cache mounts
python3 config/input/test_cosmos_input.py             # controller input and remapping
python3 gamebox/emulators/test_xemu_config.py         # the source-built emulators' launch config
bash config/cosmos-sudo/test_cosmos_priv.sh           # the command-line admin routing
bash config/wm/test_cosmos_wm.sh                      # compositor tests (need the wlroots dev container)
bash config/wm/test_touch_keys.sh                     # touch injection and key subscriptions (same container)
bash config/wm/test_touchmapd.sh                      # the Android button mapper end to end (same container)
```

## Repository layout

| Path | What is there |
|---|---|
| `Containerfile` | the whole image |
| `config/wm/` | the `cosmos-wm` compositor |
| `config/scripts/` | the desktop apps and helpers: bar, launcher, Shop, Files, Updates, Remote Access, Game Packs, session scripts |
| `config/kronos/` | the admin service, its helper, audit log, prompt and recovery |
| `config/input/` | controller input, on-screen keyboard, remapping |
| `config/cosmos-apps/` | the app policy, Expansion Packs, exceptions |
| `config/firewalld/`, `config/ssh/`, `config/webtop/` | firewall zone and doors, SSH, Webtop |
| `gamebox/` | GameBox, baked into the image |
| `docs/` | design notes (`kronos-design.md`) |

## Status

The desktop, Kronos, Game Packs and the controller tools have automated tests and were exercised in a headless compositor. Parts that need
real hardware or a full boot to confirm: controller light and trigger effects, DualSense haptics, GPU passthrough, the recovery boot entry and
the on-image Kronos services.

## About this repository

This is a public snapshot of CosmOS's source, published as a single commit (no development history) from a private working
repository. A few vendored third-party binaries tracked in that private repo are left out here - `gamebox/emulators/bin/`,
`gamebox/streaming/bin/`, `gamebox/wine/bin/` and `gamebox/skyscraper/bin/` (RetroArch, Dolphin, PCSX2, Sunshine, Wine, Skyscraper
and friends) - they're each already public from their own upstream projects; a real build pulls them in separately rather than
vendoring them here.
