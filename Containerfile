# CosmOS: a Fedora Spin built as a bootc image (same pattern as
# the sibling GameBox project) - cosmos-wm's own custom
# desktop, Brave instead of Firefox, RPM Fusion + Terra (every extra RPM
# repo worth having, direct request), Flatpak/Flathub (CosmOS Shop's own
# Flatpak section, a deliberate reversal of this image's own former
# no-flatpak policy - see cosmos-shop.py's docstring), Steam, and the C
# Development / Sound and Video / Robotics package groups. Also carries
# every real host dependency the sibling GameBox repo
# document needing to actually run (see the two package blocks below,
# each sourced from that repo's own dependency manifest, not guessed).
#
# Build from the repo root:
#   ./build-bootc.sh
# The CosmOS package repository (built by packaging/rpm/prepare-image.sh into packaging/rpm-repo/); the final stage installs from it.
FROM scratch AS cosmos-rpms
COPY packaging/rpm-repo/ /

FROM quay.io/fedora/fedora-bootc:44

# Force fresh repo metadata before anything else installs - confirmed
# live this base image's own baked-in dnf cache can point at package
# NEVRAs mirrors have already dropped as superseded (hit this exact
# failure: the base image's cached metadata wanted
# mesa-dri-drivers-26.1.8-1.fc44.i686, "all mirrors tried" and failed,
# while a completely fresh `dnf install` on the same day pulled 26.2.2
# without issue) - a stale-cache problem, not a real mirror outage, so
# refreshing metadata once here is the actual fix, not just retrying
# the build and hoping.
#
# Build cache: every dnf/pip/npm step below mounts a cache directory that lives OUTSIDE the image's layers (/var/tmp/buildah-cache-* on the build
# host), so a rebuild after a small change re-downloads nothing that an earlier build already fetched (dnf is told to keep the packages it installs,
# `--setopt=keepcache=1`). The mount also hides this base image's baked-in, possibly stale metadata cache, which is what the old `dnf clean all` was for.
# Do not add `dnf clean all` to a step that mounts the cache: it would empty the cache. (bootc-studio's Clean up tab can clear it.)
RUN --mount=type=cache,target=/var/cache/libdnf5,sharing=locked dnf makecache --refresh

# RPM Fusion free+nonfree - needed for Steam, VLC's real codec plugins
# (vlc-plugins-video-out below pulls in RPM Fusion's own
# vlc-plugins-freeworld - without it GameBox's libvlc bindings register
# zero plugins and crash, a real bug that repo's own Containerfile
# already found and fixed the same way), and anything else
# codec-adjacent sound-and-video pulls in that Fedora's own repos can't
# ship. $(rpm -E %fedora) resolves to this base image's release number
# (44) rather than hardcoding it a second time.
RUN --mount=type=cache,target=/var/cache/libdnf5,sharing=locked dnf install -y --setopt=keepcache=1 \
        "https://mirrors.rpmfusion.org/free/fedora/rpmfusion-free-release-$(rpm -E %fedora).noarch.rpm" \
        "https://mirrors.rpmfusion.org/nonfree/fedora/rpmfusion-nonfree-release-$(rpm -E %fedora).noarch.rpm"

# Terra - direct requirement ("TerraPKG, RPM Fusion, RPM Free, and RPM
# Non free. Every RPM repo you can think of that's available"), a large
# real community repo (1000+ packages Fedora itself doesn't ship) with
# its own real dnf-based Fedora install command (confirmed live against
# its own current docs, not guessed - terra.fyralabs.com itself was
# down/522 at the time this was written, but the same install command is
# also published straight from the project's own docs repo on GitHub).
# --nogpgcheck is real and intentional here, not a corner cut - Terra's
# own documented reasoning is that the very first fetch has no key
# installed yet to verify against, and terra-gpg-keys (installed in the
# same command) is what makes every subsequent Terra package real and
# verified from here on.
RUN --mount=type=cache,target=/var/cache/libdnf5,sharing=locked dnf install -y --setopt=keepcache=1 --nogpgcheck --repofrompath 'terra,https://repos.fyralabs.com/terra$releasever' \
        terra-release terra-gpg-keys

# terra-gpg-keys' own terra.repo ships gpgkey=file:///etc/pki/rpm-gpg/
# RPM-GPG-KEY-terra$releasever, same local-file pattern Fedora's own
# repos and RPM Fusion use - fine for a normal `podman build`/`dnf`
# run inside the finished image, but confirmed live to break
# bootc-image-builder's own depsolve step later (building the
# installer ISO): `bootc-image-builder manifest --type anaconda-iso`
# against this exact image failed with "Could not read a file:// file
# for file:///etc/pki/rpm-gpg/RPM-GPG-KEY-terra44" - that depsolve
# runs in a separate context that can't read arbitrary paths out of
# this image's own filesystem, only fetch things over the network,
# unlike RPM Fusion/Fedora's own repos which happened to still have
# fresh, already-verified cached metadata in this image and so never
# needed to re-read their key files at all. Terra really does publish
# the identical key over plain HTTPS too (confirmed live: same 831
# bytes as the local file, at https://repos.fyralabs.com/terra$releasever
# /key.asc and .../terra$releasever-source/key.asc) - switching both
# repos' gpgkey to that URL sidesteps the whole local-file dependency
# the same way this file's own COPR/Brave/VS Code/Sublime repos already do.
RUN sed -i \
        -e 's#^gpgkey=file:///etc/pki/rpm-gpg/RPM-GPG-KEY-terra\$releasever-source#gpgkey=https://repos.fyralabs.com/terra$releasever-source/key.asc#' \
        -e 's#^gpgkey=file:///etc/pki/rpm-gpg/RPM-GPG-KEY-terra\$releasever#gpgkey=https://repos.fyralabs.com/terra$releasever/key.asc#' \
        /etc/yum.repos.d/terra.repo

# Brave's official repo. `dnf config-manager` needs dnf5-plugins,
# which (confirmed live via this exact build) the base fedora-bootc
# image does NOT ship by default. Firefox is never installed in this
# image at all (see the package list below) so there's nothing to
# remove - Brave is simply the only browser this spin ships.
RUN --mount=type=cache,target=/var/cache/libdnf5,sharing=locked dnf install -y --setopt=keepcache=1 dnf5-plugins && \
    rpm --import https://brave-browser-rpm-release.s3.brave.com/brave-core.asc && \
    dnf config-manager addrepo --from-repofile=https://brave-browser-rpm-release.s3.brave.com/brave-browser.repo

# (VS Code's and Sublime Text's own repos are gone: those editors, like every developer tool, are Expansion Packs now -
# Flatpaks the Shop installs on demand, see config/cosmos-apps/expansion-packs.json.)

# COPR repos, managed by bootc-studio's Packages tab (the block regenerates from
# its list). cherepavel/xpadneo ships the out-of-tree Bluetooth Xbox controller
# driver, installed - and built against the image's kernel - further down.
# >>> bootc-studio:copr-repos >>>
RUN dnf install -y 'dnf5-command(copr)'
RUN dnf -y copr enable cherepavel/xpadneo
# <<< bootc-studio:copr-repos <<<

# Package notes - the explanations that used to sit above each separate
# `RUN dnf install`, kept here now that bootc-studio's Packages tab owns ONE merged
# package list (the marked block below). Grouped by what each group is for; the
# wording is unchanged, so "above"/"below" refer to where things used to be.
#
# [linux-firmware, iwlwifi-dvm-firmware, iwlwifi-mvm-firmware, iwlwifi-mld-firmware, microcode_ctl]
# linux-firmware (the full package, every redistributable vendor blob -
# not a trimmed subset, matching Anaconda's "include all available
# firmware" install-time checkbox at the package level) plus the real
# gaps GameBox's own atomic/Containerfile found linux-firmware's weak
# deps DON'T cover on real hardware (confirmed live there, not
# guessed): Intel Wi-Fi (iwlwifi-*-firmware) and Intel CPU microcode
# (microcode_ctl - AMD's own ucode already rides in via linux-firmware's
# Recommends).
#
# [brave-browser]
# brave-browser is the only browser (firefox is never pulled in). There is no
# display manager in this list: greetd (below, with cosmos-greeter) is the login
# manager. i3, i3lock, picom, rofi, dmenu, xsetroot and i3status are not installed
# and never were needed once cosmos-wm, cosmos-lockscreen, cosmos-launcher and
# cosmos-bar existed; LightDM, stalonetray, feh, Xorg itself and the X11 compositor
# libraries went away with the move to Wayland.
#
# [xdotool, xset, xprop, xrandr, vlc-libs, vlc-plugins-base ...]
# Every real runtime dependency GameBox's own packaging/host_deps.py and
# atomic/Containerfile document (dnf side - xdotool/xset/xprop/
# xrandr for its own X control of the nested Xephyr display it runs in, vlc-libs+vlc-plugins for its native
# media apps, git-core+gh for its deploy GUI's "Clone Repos..." button,
# nodejs22 for its own build step, pulseaudio/pipewire/wireplumber for
# audio, gtk3+gobject-introspection for the PyGObject-based launcher UI,
# and the long shared-lib tail emulators/bin/dolphin-emu-nogui and
# emulators/bin/pcsx2-qt actually link against, confirmed there via a
# real `ldd` against those exact binaries) plus the devel headers
# PyGObject/pycairo need to build (BUILD_DEVEL_PACKAGES there) - kept
# installed permanently here rather than that repo's own
# install-then-strip pattern, since this is a persistent dev
# workstation image, not a size-minimized appliance.
#
# [gnupg2, openssl, gettext-envsubst, podman, qemu-system-x86, edk2-ovmf ...]
# Real host tooling that provisioning scripts shell out to (bootc, curl,
# envsubst, git, gpg, openssl, podman, python3, systemctl) plus
# qemu-system-x86/edk2-ovmf so this machine can run GameBox's and this repo's
# own test_bootc_boot.sh scripts. bootc/podman/systemctl/python3 already
# ship in the fedora-bootc base image itself.
#
# [nautilus, loupe, papers, file-roller, libreoffice-writer ...]
# A real, functional daily-driver desktop toolkit on top of the WM
# itself - i3 alone ships a window manager and nothing else (no file
# manager, no notification daemon, no tray applets, no office/PDF/
# image apps) - plus everything "dark theme absolutely everything"
# needs: Papirus (icon theme) and qt5ct/qt6ct (so Qt apps - kdenlive-
# style tools, qt6ct itself, anything Qt-based someone installs later -
# follow the same dark palette GTK apps do via Fusion + the bundled
# "darker" color scheme, set in config/qt5ct+qt6ct/*.conf).
#
# (nautilus is no longer installed: cosmos-files replaces it, see below.) loupe/papers (GNOME's image viewer/PDF viewer)
# over the older eog/evince/pcmanfm-style alternatives on purpose -
# they're GTK4/libadwaita, so they follow the org.gnome.desktop.interface
# color-scheme dconf key below automatically; legacy GTK3 apps need the
# separate gtk-3.0/settings.ini boolean instead (see that file's own
# comment) since GTK3 never grew a dconf-driven dark mode of its own.
#
# volumeicon, xfce4-power-manager, stalonetray and feh are no longer installed:
# the first three only speak the XEmbed systray protocol (cosmos-bar's tray hosts
# StatusNotifier items - nm-applet, blueman) and feh sets an X
# root window background, which does not exist here. brightnessctl stays as the
# real backend for backlight control; a bar-native volume/brightness control is
# still to be written.
#
# [audacity, lmms, qtractor, hydrogen, carla, strawberry ...]
# "A Studio for audio, video, gaming, software, music" - a real content
# toolkit on top of everything above, not just the OS shell. Every
# package here was checked live against this exact base image + RPM
# Fusion (`dnf list --available <pkg>`) before being added, not
# guessed - a couple of obvious candidates (rosegarden, ardour) don't
# actually exist as Fedora/RPM Fusion packages and were left out rather
# than silently breaking the build.
#
# Audio/music production: audacity (editor), lmms + qtractor + hydrogen
# (DAW / sequencer / drum machine), carla (plugin host), strawberry
# (music player/library), pipewire-jack-audio-connection-kit
# (JACK, via pipewire's own compatibility layer - no separate JACK
# server needed), fluid-soundfont-gm + timidity++ (a real General MIDI
# soundfont so MIDI actually makes sound out of the box), musescore
# (notation).
#
# Video: obs-studio (recording/streaming), kdenlive (NLE), blender
# (3D/VFX - also genuinely useful for gaming asset work), HandBrake-gui
# (transcoding), mpv (the other real media player besides VLC).
#
# Gaming (Steam is already installed above): lutris (non-Steam game
# manager/installer), wine (run Windows games/apps directly), gamemode
# + mangohud (Feral's perf daemon + the on-screen perf overlay every
# Linux gaming guide assumes exists), easyrpg-player (RPG Maker
# 2000/2003 - the one real engine gap GameBox doesn't already cover
# itself, confirmed live by reading its own emulators/ directory:
# GameBox bundles its own from-source RetroArch, dolphin-emu-nogui, and
# pcsx2-qt directly under emulators/bin/ - already covered by the
# shared-lib tail installed earlier in this file - and mkxp-z for RPG
# Maker XP/VX/VX Ace, wired up via its own emulators/mkxp_config.py and
# systems.json entries. Deliberately NOT installing retroarch/
# dolphin-emu/pcsx2 as their own dnf packages here - GameBox never calls
# those, only its own bundled copies, so they'd be dead weight).
#
# Graphics (rounds out "creative studio" alongside video/audio): gimp,
# inkscape, krita, darktable.
#
# Software: code (VS Code, via the Microsoft repo above) alongside the
# C Development group already installed - neovim/vim-enhanced (editors
# that work with no GUI at all), golang, rust + cargo,
# java-latest-openjdk-devel, ruby (the other mainstream languages, C/
# Python already covered), podman-compose (podman's
# already here; compose files are still the common format), git-gui +
# gitk (Tk-based GUI/history viewer that ship separately from git-core).
# unzip - needed to unpack the Android SDK cmdline-tools zip below (no
# other package on this image provides it; tar handles every other
# archive format used here). ImageMagick - needed to convert Eclipse's
# own icon.xpm (a real file it ships, confirmed live, but XPM isn't
# reliably rendered by rofi/GTK's own icon loaders - confirmed live via
# a blank icon in rofi despite the correct path) into a plain PNG at
# build time - was only ever used on the HOST for cursor/wallpaper work
# earlier, never actually added as a real dependency of this image
# itself until this exact need surfaced.
#
# Developer tools were preinstalled here on request ("even if my OS gets bloated"); they are Expansion Packs now (Shop >
# Expansion Packs) and only Tiled stays baked in (as a Flatpak). godot/love/tiled round out
# game dev specifically (also direct request) - all three are real
# current Fedora packages too: Godot (full 2D/3D engine), LÖVE (Lua
# 2D framework), Tiled (tile-map editor game engines commonly import).
# Deliberately skipped: Aseprite (commercially licensed, no free
# prebuilt binary distribution - building from its MIT-licensed source
# would be its own whole toolchain) and LDtk/libresprite (not packaged
# for Fedora at all).
#
# [plymouth, plymouth-plugin-script]
# Plymouth boot splash: the coin logo, a "STUDIO" wordmark set in
# Rajdhani (the same font GameBox's own launcher splash uses -
# launcher/run.py's .cosmos-splash-title CSS - rendered to a PNG ahead
# of time rather than at boot, since a custom TTF isn't reliably
# available inside the initramfs environment Plymouth actually runs
# in), and a spinning bar loading indicator - config/plymouth/
# cosmos's own comments have the full design notes. Confirmed
# live (rendered for real via plymouthd --debug + plymouth
# --show-splash in a test VM, screenshot captured via QEMU's own
# monitor) before this went in the Containerfile at all - a script
# syntax mistake here is a blank screen with no error, not something
# to find out about for the first time on a real boot.
#
# [telegram-desktop, pidgin, thunderbird, discord]
# Telegram, Pidgin, Thunderbird, Discord - direct requirement. All four
# are real Fedora-repo/RPM-Fusion packages, no special-casing needed the
# way Bitwarden/dbeaver above need (confirmed live: `dnf list` finds all
# four normally) - telegram-desktop specifically comes from
# rpmfusion-free-updates, discord from rpmfusion-nonfree-updates, both
# already enabled above.
#
# Signal and WhatsApp are still deliberately NOT baked in here (direct
# request - "no signal or whatsapp then"), including as a web app for
# the latter: Signal has no official Fedora/RPM Fusion package, only its
# own APT repo, Flatpak, or Snap - at the time this was written Flatpak
# wasn't available anywhere on this image either, but that's since
# reversed specifically for CosmOS Shop's own Flatpak section (direct
# request), and Signal is a real, official Flathub app - so it's just a
# CosmOS Shop install away now instead of needing an unofficial
# third-party COPR. WhatsApp has no native Linux client at all, official
# or otherwise, on any packaging format.
#
# [wlroots-devel, wayland-devel, wayland-protocols-devel, wlr-protocols-devel ...]
# cosmos-wm (config/wm/cosmos-wm.c, see its own docstring) is a wlroots compositor:
# wlroots-devel plus the Wayland/xkbcommon/libinput/seat/DRM/EGL/pixman headers it
# links against, xcb-util-wm/libxcb for the XWayland shim, and pango/cairo for the
# Alt+Tab overlay (pkgconf-pkg-config, cairo-devel and gcc come from the groups
# above). wlr-protocols-devel carries the layer-shell XML the build turns into a
# header with wayland-scanner. gtk-layer-shell is what cosmos-bar, cosmos-launcher,
# the on-screen keyboard and the lock screen use to be panels/overlays.
# xorg-x11-server-Xwayland (+ xkbcomp and xkeyboard-config, without which it
# refuses to start) is the hidden XWayland shim for Wine, Steam, JetBrains IDEs
# and GameBox; xorg-x11-server-Xephyr is what GameBox runs nested in.
# greetd (+ its SELinux policy) is the login manager. libayatana-appindicator-gtk3
# is what lets nm-applet --indicator export a
# StatusNotifier icon. grim/slurp/wl-clipboard are the Wayland screenshot and
# clipboard tools; xdg-desktop-portal(-wlr, plus -gtk above) provide screen
# capture and file choosers (see cosmos-portals.conf below).
# >>> bootc-studio:dnf-packages >>>
RUN --mount=type=cache,target=/var/cache/libdnf5,sharing=locked dnf install -y --setopt=keepcache=1 \
    ImageMagick \
    SDL3 \
    alsa-lib \
    blueman \
    bluez-libs \
    brave-browser \
    brightnessctl \
    c4core \
    cairo-devel \
    cairo-gobject-devel \
    curl \
    dbus-libs \
    dconf \
    discord \
    double-conversion \
    duktape \
    dunst \
    easyrpg-player \
    file-roller \
    fira-code-fonts \
    firewalld \
    flac-libs \
    fluid-soundfont-gm \
    fmt \
    fuse-overlayfs \
    fuse3 \
    gettext-envsubst \
    gh \
    git-core \
    gnupg2 \
    gobject-introspection \
    gobject-introspection-devel \
    google-noto-emoji-fonts \
    greetd \
    greetd-selinux \
    grim \
    gsettings-desktop-schemas \
    gsm \
    gtk-layer-shell \
    gtk3 \
    gtk3-devel \
    hidapi \
    iwlwifi-dvm-firmware \
    iwlwifi-mld-firmware \
    iwlwifi-mvm-firmware \
    jetbrains-mono-fonts \
    kddockwidgets-qt6 \
    kodi \
    lame-libs \
    libX11-devel \
    libasyncns \
    libayatana-appindicator-gtk3 \
    libb2 \
    libblkid \
    libbrotli \
    libdrm-devel \
    libevdev \
    libffi \
    libgomp \
    libibverbs \
    libinput-devel \
    libjpeg-turbo \
    libmount \
    libnl3 \
    libnotify \
    libogg \
    libpcap \
    libproxy \
    librsvg2-devel \
    libseat-devel \
    libsndfile \
    libusb1 \
    libvorbis \
    libwebp \
    libxcb-devel \
    libxkbcommon-devel \
    libxml2 \
    linux-firmware \
    loupe \
    mangohud \
    mesa-libEGL-devel \
    mesa-libgbm-devel \
    microcode_ctl \
    miniupnpc \
    mpg123-libs \
    mpv \
    network-manager-applet \
    nodejs22-bin \
    nodejs22-npm-bin \
    openssh-server \
    openssl \
    opus \
    pango-devel \
    papers \
    papirus-icon-theme \
    pavucontrol \
    pciutils \
    pipewire-jack-audio-connection-kit \
    pipewire-pulse \
    pixman-devel \
    pkgconf-pkg-config \
    plutosvg \
    plutovg \
    plymouth \
    plymouth-plugin-script \
    podman \
    podman-compose \
    pulseaudio-libs \
    pulseaudio-utils \
    python3-devel \
    python3-evdev \
    python3-pip \
    python3-xlib \
    qt5ct \
    qt6-qtbase \
    qt6-qtbase-gui \
    qt6-qtdeclarative \
    qt6ct \
    rapidyaml \
    slurp \
    spdlog \
    squashfs-tools \
    squashfuse \
    steam \
    sudo \
    timidity++ \
    unzip \
    vlc-libs \
    vlc-plugins-base \
    vlc-plugins-video-out \
    wayland-devel \
    wayland-protocols-devel \
    wget \
    wireplumber \
    wl-clipboard \
    wlr-protocols-devel \
    wlroots-devel \
    xcb-util-wm-devel \
    xdg-desktop-portal \
    xdg-desktop-portal-gtk \
    xdg-desktop-portal-wlr \
    xdg-user-dirs-gtk \
    xdotool \
    xkbcomp \
    xkeyboard-config \
    xorg-x11-server-Xephyr \
    xorg-x11-server-Xwayland \
    xprop \
    xrandr \
    xset
# <<< bootc-studio:dnf-packages <<<

# Proprietary NVIDIA driver - direct requirement, overriding the open
# nouveau default a general-purpose spin would otherwise ship (nouveau
# already rides in via linux-firmware above, so it's still there as a
# fallback on non-NVIDIA hardware). RPM Fusion nonfree's akmod-nvidia
# needs the matching kernel-devel package physically present at build
# time - not just resolvable as a same-version dependency - since
# akmods compiles the module against real headers on disk, same KVER
# pattern this Containerfile already uses for the dracut/initramfs step
# further down. xorg-x11-drv-nvidia-cuda pulls in the CUDA userspace
# bits, worth having alongside this image's own dev-tool stack.
# `akmods --force` builds the module right now against this image's
# own baked-in kernel instead of deferring to akmods.service's normal
# first-boot build, so it's already built and working the first time
# this thing actually boots, not after a first-boot delay/failure risk.
# If the base image's kernel is older than what the repos still carry (the -devel package
# for an old build is dropped once a newer one is released - hit for real: "No match for
# argument: kernel-devel-7.2.6-200.fc44"), the image's kernel is moved to the current one
# instead: the new kernel packages go in, and the old build's are removed so exactly one
# kernel is left in /usr/lib/modules (bootc needs that; every later step that reads the
# version with `rpm -q kernel-core` relies on it too).
RUN --mount=type=cache,target=/var/cache/libdnf5,sharing=locked KVER=$(rpm -q --qf '%{VERSION}-%{RELEASE}.%{ARCH}' kernel-core) \
    && if ! dnf install -y --setopt=keepcache=1 "kernel-devel-${KVER}"; then \
         echo "kernel-devel-${KVER} is no longer in the repos - moving the image to the current kernel" \
         && dnf upgrade -y --setopt=keepcache=1 kernel kernel-core kernel-modules kernel-modules-core \
         && NEW=$(rpm -q --qf '%{VERSION}-%{RELEASE}.%{ARCH}\n' kernel-core | sort -V | tail -1) \
         && test "${NEW}" != "${KVER}" \
         && dnf install -y --setopt=keepcache=1 "kernel-devel-${NEW}" \
         && rpm -qa --qf '%{NAME} %{VERSION}-%{RELEASE}.%{ARCH}\n' 'kernel*' \
              | awk -v old="${KVER}" '$2 == old { print $1 "-" $2 }' | xargs -r rpm -e --nodeps \
         && KVER="${NEW}" \
         && test "$(rpm -q kernel-core | wc -l)" = 1; \
       fi \
    && dnf install -y --setopt=keepcache=1 akmod-nvidia xorg-x11-drv-nvidia-cuda \
    && akmods --force --kernels "${KVER}"

# xpadneo - the out-of-tree Bluetooth Xbox controller driver mainline's
# own xpad/hid-microsoft drivers don't fully replace: rumble/force
# feedback, correct signed trigger axis range, and the Share/Mode
# buttons all need it specifically (confirmed against the driver's own
# real feature list, not assumed). Not in Fedora's own repos or RPM
# Fusion - cherepavel/xpadneo on COPR ships real fedora-44-x86_64
# builds (confirmed live via COPR's own build API, picked over other
# same-named COPR projects for having the more recent successful
# build). Same akmods build-now-not-on-first-boot reasoning as the
# NVIDIA driver above.
RUN --mount=type=cache,target=/var/cache/libdnf5,sharing=locked dnf install -y --setopt=keepcache=1 xpadneo \
    && KVER=$(rpm -q --qf '%{VERSION}-%{RELEASE}.%{ARCH}' kernel-core) \
    && akmods --force --kernels "${KVER}"

RUN --mount=type=cache,target=/var/cache/libdnf5,sharing=locked dnf group install -y --setopt=keepcache=1 c-development sound-and-video

# Rajdhani - direct requirement ("copy the font from GameBox and use it
# everywhere"), the same real font GameBox's own launcher/theme.css uses
# (font-family: "Rajdhani", ...) - not a Fedora package, the actual
# bundled .ttf files from that project's own launcher/fonts/ directory,
# copied into this repo's own config/fonts/ rather than referencing the
# other repo directly (this image has to build standalone). Registered
# system-wide via /usr/share/fonts (fc-cache picks up anything there
# for every user, not just one account's own fontconfig cache) and set
# as the actual default everywhere a font is chosen at all: gtk-font-name
# below (GTK's own general UI font, same mechanism gtk-cursor-theme-name
# already uses there for the cursor), plus an explicit font-family rule
# in gtk.css (see that file's own comment on why named-color-only
# theming wasn't enough for the cursor/color work either - CSS
# specificity can still lose to a widget's own hardcoded font choice
# without an explicit rule backing the settings.ini default up).
# Medium weight specifically as the default (not Bold/SemiBold, also
# bundled) - GameBox's own theme.css uses Rajdhani at regular UI text
# sizes as a normal-weight font, SemiBold/Bold reserved there for
# headings/titles this project has no equivalent of.
# ---- CosmOS itself: installed from its own RPMs -------------------------------------------------------------------------
# Everything CosmOS ships (the shell, compositor, Kronos, GameBox, the apps, the theme) is a cosmos-* package built from packaging/rpm/packages.json
# (one manifest, so the image and a normal Fedora get the same files). cosmos-image is the package that pulls in all of it. The repository is built
# before the image (packaging/rpm/prepare-image.sh, which Bootc Studio runs first: the project's "pre_build") into packaging/rpm-repo/, and it is
# mounted here rather than copied, so it does not stay in a layer. --refresh and metadata_expire=0 matter: the cache mount outlives builds, and a cached
# copy of an older build of this repository would otherwise be installed (every build has its own release number, see build.py). The packages and the repository metadata are signed, and dnf checks both.
RUN --mount=type=cache,target=/var/cache/libdnf5,sharing=locked --mount=type=bind,from=cosmos-rpms,target=/rpms \
    dnf install -y --refresh --setopt=keepcache=1 --repofrompath=cosmos,/rpms --setopt=cosmos.metadata_expire=0 \
        --setopt=cosmos.gpgcheck=1 --setopt=cosmos.repo_gpgcheck=1 --setopt=cosmos.gpgkey=file:///rpms/RPM-GPG-KEY-cosmos \
        cosmos-image


# Bibata-Modern-Classic - the actual mouse cursor theme, not just the
# icon/GTK/Qt theming above (direct request: "prettify" the plain
# default X11 cursor). Not in Fedora's own repos (confirmed live via
# dnf search - only adwaita/bluecurve/breeze/oxygen exist there, all
# older/plainer) - pinned to a real GitHub release tarball instead,
# same reasoning as gogdl/Heroic/itch above. Wired in three places so
# every subsystem actually picks it up: /usr/share/icons/default's own
# index.theme (the real X11 "default cursor" mechanism i3's own root
# window cursor uses, confirmed live this is what plain X11/non-GTK
# cursor resolution reads), gtk-3.0/gtk-4.0 settings.ini's
# gtk-cursor-theme-name (GTK apps), and the dconf db's cursor-theme key
# (GTK4/libadwaita apps, same reasoning as icon-theme/gtk-theme already
# there).
#
# The overwritten cursor.theme at the end is a real fix, not
# decoration: the upstream v2.0.7 release tarball ships its own
# cursor.theme self-referencing itself (`Inherits="Bibata-Modern-Classic"`
# pointing at Bibata-Modern-Classic from inside Bibata-Modern-Classic's
# own theme file - confirmed live by downloading and extracting the
# exact same release tarball independently, not assumed). Direct,
# live-observed symptom on a real booted image: the mouse pointer over
# the ENTIRE cosmos-greeter window (not just one widget) rendered as
# the bare X11 fallback "X" cursor shape instead of any Bibata cursor
# at all, exactly what a theme loader hitting a self-referencing
# Inherits cycle and giving up on loading the theme entirely - rather
# than just one missing cursor name - would produce. Inherits=core
# (the traditional built-in X cursor font, always present) is what
# this should have said instead.
RUN curl -fL -o /tmp/bibata.tar.xz \
        https://github.com/ful1e5/Bibata_Cursor/releases/download/v2.0.7/Bibata-Modern-Classic.tar.xz \
    && tar -xJf /tmp/bibata.tar.xz -C /usr/share/icons/ \
    && rm -f /tmp/bibata.tar.xz \
    && mkdir -p /usr/share/icons/default \
    && printf '[Icon Theme]\nInherits=Bibata-Modern-Classic\n' > /usr/share/icons/default/index.theme \
    && printf '[Icon Theme]\nName=Bibata-Modern-Classic\nInherits=core\n' \
        > /usr/share/icons/Bibata-Modern-Classic/cursor.theme

# gogdl (GameBox's GOG store backend, gog/gogdl_backend.py's own
# GOGDL_BIN) is heroic-gogdl (github.com/Heroic-Games-Launcher/
# heroic-gogdl) - despite what GameBox's own packaging/host_deps.py
# PIP_PACKAGES claims, this is NOT a real PyPI package (confirmed live:
# `pip install gogdl` 404s, no such distribution exists) - upstream only
# ships prebuilt binaries via GitHub releases, no source install at all.
# Pinned to a specific tag rather than "latest" for a reproducible build,
# same reasoning as pinning FROM fedora-bootc:44 above.
RUN curl -fL -o /usr/local/bin/gogdl \
        https://github.com/Heroic-Games-Launcher/heroic-gogdl/releases/download/v1.3.0/gogdl_linux_x86_64 \
    && chmod 0755 /usr/local/bin/gogdl

# Real pip dependencies: internetarchive (the `ia` CLI), legendary-gl
# (GameBox's Epic store backend, its own packaging/host_deps.py
# PIP_PACKAGES - unlike gogdl above, this one really is a real PyPI
# package, confirmed live), and GameBox's own packaging/requirements.txt
# in full (PyGObject/pycairo built here against the devel headers
# installed above, plus every other real import its launcher needs) -
# installed straight onto this image's system Python since, like the
# devel headers above, there's no reason to strip build tooling back
# out of a persistent dev workstation image. yt-dlp deliberately NOT
# pip-installed here any more - Fedora 44 now packages it natively (a
# real dnf package, pulled in as a Weak Dependency of the
# sound-and-video group - confirmed live), and pip's own "Requirement
# already satisfied" no-op left a genuinely broken half-state
# (site-packages present, but no /usr/local/bin/yt-dlp console-script)
# when both tried to coexist - the dnf-provided /usr/bin/yt-dlp is the
# one real copy this image ships now.
RUN --mount=type=cache,target=/var/cache/pip,sharing=locked PIP_CACHE_DIR=/var/cache/pip pip3 install \
        internetarchive \
        legendary-gl \
        PyGObject \
        pycairo \
        beautifulsoup4 \
        evdev \
        psutil \
        pyudev \
        qrcode \
        Pillow \
        pyyaml \
        tomli-w \
        requests \
        cryptography \
        python-vlc \
        panda3d \
        paramiko \
        webdavclient3

# Claude Code (the CLI), installed globally via the nodejs22-npm-bin above. CosmOS has no terminal emulator, so `claude` is
# used over SSH (see the sshd block below) or inside the Webtop; it needs no per-user setup beyond its own login on first run.
RUN --mount=type=cache,target=/var/cache/npm,sharing=locked npm_config_cache=/var/cache/npm npm install -g @anthropic-ai/claude-code

# Bitwarden, force-installed for every Brave profile on the machine via
# Brave's own managed-policy mechanism (brave-core hardcodes
# /etc/brave/policies/managed for every install type - rpm/deb/source -
# confirmed against brave-browser's own source, not the older, since-
# changed /etc/chromium/policies/managed path some docs still mention).
# clients2.google.com/service/update2/crx is the standard Chrome Web
# Store update URL every ExtensionInstallForcelist entry needs alongside
# the extension ID.
RUN mkdir -p /etc/brave/policies/managed
COPY config/brave/extensions.json /etc/brave/policies/managed/extensions.json
# No self-drawn title bar in Brave: cosmos-wm has no window decorations and Chromium's own
# frame would otherwise add one ("Use system title bar and borders" - the profile preference
# browser.custom_chrome_frame=false - is the only switch for it; policies and flags have none).
# Seeded into new accounts' default profile, so it applies from the first launch.
COPY config/brave/skel-preferences.json /etc/skel/.config/BraveSoftware/Brave-Browser/Default/Preferences


# Astronar Studio / Theme Studio - CosmOS's own native creative
# tools for the Astronar avatar system, not part of GameBox itself - a direct request
# ("this is not meant for GameBox directly"). config/astronar-studio/ is
# a real, self-contained slice of GameBox's own launcher/ (the avatar
# engine + debuglog.py/paths.py, the two root-level modules it needs -
# see astronar_studio.py's own module docstring for exactly what each
# tab does) - installed at /opt/astronar-studio/, the same real,
# already-proven-working /opt location the earlier CosmOSDeploy
# AppImage used, entirely independent of the /cosmos symlink below
# (both tools only ever touch /userdata, already created unconditionally
# further down, never /cosmos itself).
COPY config/astronar-studio/ /opt/astronar-studio/
RUN printf '[Desktop Entry]\nType=Application\nName=Astronar Studio\nExec=python3 /opt/astronar-studio/launcher/astronar_studio.py\nComment=Visually create/pose/customize/manage everything an Astronar can be\nIcon=preferences-desktop-personal\nCategories=Graphics;Development;\nTerminal=false\n' \
        > /usr/share/applications/astronar-studio.desktop \
    && printf '[Desktop Entry]\nType=Application\nName=Theme Studio\nExec=python3 /opt/astronar-studio/launcher/theme_studio.py\nComment=Visually edit GameBox launcher color themes\nIcon=preferences-desktop-theme\nCategories=Graphics;Development;\nTerminal=false\n' \
        > /usr/share/applications/theme-studio.desktop

# CosmOS's own design tool - a real, working aid, not a mockup (see its own
# module docstring for what it does and how it was verified). GameBox Add-on
# Studio works on the writable copy of GameBox (/cosmos/app/current, prepared by gamebox-dir), found
# via config/cosmos-tools/repo_paths.py - nothing to configure.
COPY config/cosmos-tools/ /opt/cosmos-tools/
RUN printf '[Desktop Entry]\nType=Application\nName=GameBox Add-on Studio\nExec=python3 /opt/cosmos-tools/gamebox_addon_studio.py\nComment=Build Music/Photos content-source add-ons for GameBox, no Python required\nIcon=folder-templates\nCategories=Development;\nTerminal=false\n' \
        > /usr/share/applications/gamebox-addon-studio.desktop


# The desktop wallpaper/lock screen background: GameBox's own real
# launcher/backgrounds/galaxy.jpg with a dark overlay gradient pre-baked in (see
# config/backgrounds/). cosmos-lockscreen.py uses it as its background.
# cosmos-greeter.py's own BACKGROUND_IMAGE - direct requirement ("just
# use the base image from nasa without the logo in login"): the same
# original, unmodified NASA/ESA Hubble photo of M51 (the Whirlpool
# Galaxy) cosmos-galaxy.jpg above was itself derived from
# (GameBox's own launcher/backgrounds/galaxy.jpg, confirmed identical
# subject/comment metadata, byte-for-byte different file) - that file
# has the "CosmOS" ringed-C logo and a dark gradient composited
# directly into its own pixels (see this block's own comment above on
# why: the old lightdm-gtk-greeter needed that baked in, this greeter draws its
# own separate card/UI instead and doesn't), which is exactly what a
# plain login screen with no other UI drawn over it shouldn't show.

# Dark theme, everywhere a GTK/Qt app might look for it:
# - GTK4/libadwaita apps (nautilus/loupe/papers) read the dconf key
#   below - config/dconf/db-local.d/00-dark-theme's own comment has why
#   this is a plain system db, not a user override.
# - Legacy GTK3 apps (LibreOffice, most everything else) don't read
#   dconf for this at all - gtk-application-prefer-dark-theme in
#   gtk-3.0/settings.ini is the actual switch for them. Copied to both
#   /etc/skel (the real desktop session) and the greeter's own $HOME
#   (/var/lib/greetd) so the greeter's own small login-card widget
#   matches too, not just its already-black background.
# - GTK4 non-libadwaita apps fall back to gtk-4.0/settings.ini the same
#   way GTK3 does.
# - Qt apps: QT_QPA_PLATFORMTHEME points them at qt5ct/qt6ct, already
#   pre-seeded with a dark palette above.
# GameBox's own accent_2 (#ff0090) as the GTK selection/accent color -
# see gtk.css's own comment - same treatment as settings.ini just above
# (skel + the greeter account's own $HOME, so the greeter's login card matches too).

# Brave has no supported way to set a default flag via policy or a
# flags file (confirmed live: no such mechanism exists in this exact
# binary, unlike upstream Chromium) - overriding its own .desktop
# launcher's Exec= lines directly is what actually reaches it, since
# every app launch in this spin goes through rofi's drun mode reading
# .desktop files anyway.
# --force-dark-mode is a real, confirmed-present flag in this exact
# binary (found via `strings` against it directly, not assumed).
RUN sed -i 's#^Exec=/usr/bin/brave-browser-stable#Exec=/usr/bin/brave-browser-stable --force-dark-mode --enable-features=WebUIDarkMode#' \
        /usr/share/applications/brave-browser.desktop

# Nine default "Brave apps" (direct request) - real installed-as-app
# PWAs live in Brave's own per-profile internal database (undocumented,
# version-fragile format), not something worth reproducing from a
# Containerfile - `--app=<url>` is a real, documented Chromium flag that
# opens any URL in the same chromeless app-style window with no formal
# PWA registration needed at all, which is all cosmos-bar's own "Web
# Applications" dropdown (config/scripts/cosmos-bar.py) or these files
# themselves actually need. Seeded into /etc/skel (not /usr/share) since
# they need to land in EACH account's own ~/.local/share/applications -
# the same real per-user location Brave itself writes genuine installed
# apps to, and the same directory cosmos-bar's own discover_brave_apps()
# scans. spotify.com itself is just a marketing page - open.spotify.com
# is the real web player, so that's what's actually used here.
# NoDisplay=true on every one - direct instruction ("I don't want rofi
# itself to render or pick up the brave apps"): rofi's drun mode (and
# any other standard freedesktop-menu-spec launcher) respects this
# field and hides the entry, but cosmos-bar's own discover_brave_apps()
# reads Name=/Exec= directly off the file and ignores it entirely, so
# the dropdown still lists every one of these normally.
# Icon=brave-<slug> (a bare icon NAME, resolved via the standard icon
# theme search path), not Icon=brave-browser any more - direct
# requirement ("replace the Brave Icon by the Favicons... so they are
# distinguishable from each other"). Each one's real favicon (Google's
# public favicon-resolution service, https://www.google.com/s2/favicons
# - handles a site's own actual icon path/format for us rather than
# guessing /favicon.ico exists) is cached at build time into
# /etc/skel/.local/share/icons/hicolor/128x128/apps/ - the real,
# standard per-user hicolor icon theme location, not a hardcoded
# absolute path baked into the .desktop file. That distinction matters
# because /etc/skel gets copied into whatever account this image's own
# useradd block creates - an absolute path would only resolve for
# whichever one account it was written for, while a bare icon name
# resolves correctly against that account's own $HOME once /etc/skel's
# own copy of this same file lands there. Falls back to plain
# Icon=brave-browser for any one
# favicon fetch that fails (offline build, service hiccup, etc.)
# instead of shipping a .desktop file pointing at an icon that was
# never actually fetched.
#
# X-CosmOS-Default=true - direct requirement ("the 9 defaults should
# NOT be deletable. My wife will lose her shit if she accidentally
# deleted them"), a real, live bug this fixes (confirmed live: exactly
# that happened, an accidental right-click on the Web Apps tab's own
# grid deleted Disney+ outright, no confirmation). A custom .desktop
# key cosmos_webapp_edit.py checks before
# allowing a removal to actually happen, rather than a
# hardcoded name list there - only add_web_app()'s own entries, which
# never set this key, are ever actually removable.
RUN mkdir -p /etc/skel/.local/share/applications \
             /etc/skel/.local/share/icons/hicolor/128x128/apps \
    && for entry in \
        "YouTube|https://youtube.com" \
        "YouTube Music|https://music.youtube.com" \
        "Spotify|https://open.spotify.com" \
        "Tubi|https://tubitv.com" \
        "Pluto TV|https://pluto.tv" \
        "Hulu|https://hulu.com" \
        "Disney+|https://disneyplus.com" \
        "Paramount+|https://paramountplus.com" \
        "Netflix|https://netflix.com" \
        "TikTok|https://www.tiktok.com" \
        "Pinterest|https://www.pinterest.com" \
        "Tumblr|https://www.tumblr.com" \
        "Etsy|https://www.etsy.com" \
        "Miniclip|https://www.miniclip.com" \
        "Twitch|https://www.twitch.tv" \
        "Crunchyroll|https://www.crunchyroll.com" \
        "DeviantArt|https://www.deviantart.com" \
        "FMHY|https://fmhy.net" \
        "ArtStation|https://www.artstation.com" \
        "Pixiv|https://www.pixiv.net" \
        "Newgrounds|https://www.newgrounds.com" \
        "Reddit|https://www.reddit.com" \
        "Instagram|https://www.instagram.com" \
        "Max|https://www.max.com" \
        "Amazon Prime Video|https://www.primevideo.com" \
        "Facebook|https://www.facebook.com" \
        "Gmail|https://mail.google.com" \
    ; do \
        name="${entry%%|*}"; \
        url="${entry##*|}"; \
        slug=$(echo "$name" | tr '[:upper:]' '[:lower:]' | tr -cs 'a-z0-9' '-' | sed 's/^-*//;s/-*$//'); \
        domain=$(echo "$url" | sed -E 's#^[a-zA-Z]+://##; s#/.*##'); \
        icon_path="/etc/skel/.local/share/icons/hicolor/128x128/apps/brave-${slug}.png"; \
        icon_name="brave-browser"; \
        if curl -fsSL "https://www.google.com/s2/favicons?domain=${domain}&sz=128" -o "$icon_path" \
            && [ -s "$icon_path" ]; then \
            icon_name="brave-${slug}"; \
        else \
            rm -f "$icon_path"; \
        fi; \
        printf '[Desktop Entry]\nType=Application\nName=%s\nExec=brave-browser --app=%s --profile-directory=Default\nIcon=%s\nCategories=Network;\nTerminal=false\nNoDisplay=true\nX-CosmOS-Default=true\n' \
            "$name" "$url" "$icon_name" > "/etc/skel/.local/share/applications/brave-${slug}.desktop"; \
    done


# Pluto TV and Gmail both fetch a real favicon (no fallback), but
# neither is actually right: confirmed live via an extracted contact
# sheet of every fetched icon that Pluto TV's own resolves to a plain
# yellow "tv" with no Pluto branding at all, and Gmail's resolves to
# the generic multicolor Google "G" instead of its own real red
# envelope mark. Same real-icon-override mechanism as the two below -
# both already fetch successfully, so only the icon FILE needs
# replacing, not the .desktop entry's Icon= value.
COPY config/webapp-icons/pluto-tv.png /etc/skel/.local/share/icons/hicolor/128x128/apps/brave-pluto-tv.png
COPY config/webapp-icons/gmail.png /etc/skel/.local/share/icons/hicolor/128x128/apps/brave-gmail.png

# Paramount+'s own fetched favicon has genuinely hard, non-anti-aliased
# edges even at 128x128 (confirmed live: its alpha channel is pure 0/255
# only, no gradient at all) - bitmap-tracing it (potrace) only ever
# reproduced the same pixel staircase at whatever scale it ran at,
# tried both before and after upscaling, neither gave a clean curve.
# The real icon supplied directly instead (a real 1024x1024 file the
# user built themselves, plain resized to this image's own standard
# 128x128 and otherwise left completely untouched, on purpose - direct
# instruction: "I made it myself, this wasn't a grab. So no, don't
# touch it").
COPY config/webapp-icons/paramount.png /etc/skel/.local/share/icons/hicolor/128x128/apps/brave-paramount.png

# TikTok's own fetched favicon is real (a solid black square tile with
# a white note glyph plus a barely-there cyan/magenta accent fringe -
# confirmed live via histogram: those two colors are a handful of
# pixels each, negligible at icon scale) but only 32x32 - same
# "trace it into a clean vector" treatment: isolate the white glyph, threshold, potrace, render at
# 800x800 onto a freshly-drawn solid black square, downscale to this
# image's own standard 128x128. The accent fringe is lost (three-color
# tracing wasn't worth it for pixels this sparse), but it was already
# imperceptible at real display sizes in the original.
COPY config/webapp-icons/tiktok.png /etc/skel/.local/share/icons/hicolor/128x128/apps/brave-tiktok.png

# --no-compress: confirmed live, the hard way, four straight OOM kills
# at exactly this step on a build host also running a real production
# podman stack alongside it - a --no-hostonly (every driver, not just
# this build container's own virtual hardware - the right choice for
# an image meant to boot on unknown real hardware) initramfs's default
# zstd/xz compression pass is a genuine multi-GB peak-memory spike, real
# memory pressure metrics (/proc/pressure/memory) stayed near zero the
# whole time otherwise. An uncompressed cpio initramfs is still
# perfectly bootable (the kernel doesn't care), just larger on disk -
# a real, worthwhile trade against repeatedly OOM-killing whatever else
# is running on the same machine at build time.
RUN plymouth-set-default-theme cosmos \
    && KVER=$(rpm -q --qf '%{VERSION}-%{RELEASE}.%{ARCH}' kernel-core) \
    && dracut --reproducible --add ostree --force --no-hostonly --no-compress --kver "$KVER" "/usr/lib/modules/$KVER/initramfs.img"

# rhgb/quiet on the kernel command line - without this the splash
# above never shows at all (confirmed live: plymouthd's own boot-time
# check requires "rhgb" or "splash" before it displays anything by
# default). /usr/lib/bootc/install/*.toml drop-ins are bootc's own,
# currently-documented mechanism for default kargs (merged in
# alphanumeric order at install time) - confirmed against this exact
# bootc version's own man bootc-install-config, not the older/unused
# /usr/lib/bootc/kargs.d/ path some other images ship an empty
# directory for.
COPY config/bootc-install/50-splash.toml config/bootc-install/51-nvidia-drm.toml config/bootc-install/52-iommu.toml \
     /usr/lib/bootc/install/

# os-release identity: NAME/PRETTY_NAME retitled to "CosmOS" -
# ID stays "fedora" untouched (RPM Fusion/Terra/COPR's own install
# commands, `rpm -E %fedora` above, and dnf's own repo variables all
# key off the distro's rpm macros/package metadata, not this file, so
# leaving ID alone keeps every repo/package step elsewhere in this
# Containerfile working exactly as before). Confirmed live, not
# assumed to cascade from this file: rebuilt a throwaway test image
# with just this change and regenerated bootc-image-builder's own
# manifest against it - both Anaconda's own displayed product name
# (the org.osbuild.buildstamp stage) and the installer ISO's boot-menu
# entry text (org.osbuild.grub2.iso and its .legacy/BIOS counterpart)
# read PRETTY_NAME/NAME straight from here and switched to "CosmOS" immediately, with no separate installer-branding step
# needed. VARIANT/VARIANT_ID are the real os-release fields meant for
# exactly this - naming a spin without renaming the base distro.
# DEFAULT_HOSTNAME (the fallback name before anyone sets a real one)
# gets the same treatment - everything else (LOGO, the isolabel/vendor
# fields bootc-image-builder generates internally, HOME_URL/etc.
# pointing at Fedora's own site) is left alone: LOGO names an icon
# that only resolves if some icon theme ships it, which none installed
# here do either way, and the isolabel/vendor fields come from
# bootc-image-builder's own code, not this file - not touched here.
RUN sed -i \
        -e 's/^NAME=.*/NAME="CosmOS"/' \
        -e 's/^PRETTY_NAME=.*/PRETTY_NAME="CosmOS"/' \
        -e 's/^DEFAULT_HOSTNAME=.*/DEFAULT_HOSTNAME="cosmos"/' \
        /usr/lib/os-release \
    && printf 'VARIANT="CosmOS"\nVARIANT_ID=cosmos\n' >> /usr/lib/os-release

# The one real user account this image ships (direct requirement: "a single user called
# user") - identical on every install; who is actually using it is a profile, below.
#
# One Unix account, no login screen and no password: the hash is genuinely empty
# (`usermod -p ''`, distinct from useradd's default locked `!`), greetd logs the account in
# automatically (cosmos-autologin) and the profile picker in the session asks who is
# using the machine - names, pictures and optional PINs live in the profiles, not in
# Unix accounts (cosmos_profiles.py). Nothing secret is baked in, so there is nothing to
# leak from this repo. Be clear about what this means: anyone at the keyboard has this
# account - but not root: it is not in wheel and has no sudo of its own, admin work goes through kronos (below) - a PIN keeps a stray hand out of someone's
# profile, it does not protect the machine from someone who sits down at it. Network
# access is unaffected: sshd refuses empty passwords by default.
# The account is deliberately NOT in the `input` group: that group can read every keyboard and mouse, so any program running as the account
# could watch every key typed - the admin passphrase too. Game controllers don't need it (logind's uaccess rules give the signed-in session
# the pads, and 72-cosmos-controller-input.rules the motion sensors), the controller LED files are writable by the harmless `cosmos-pads`
# group, and the one real use of a host keyboard/mouse - passing it through to a virtual machine - asks kronos for a temporary grant on that
# one device (input.grant, admin passphrase). `kvm` is for the VM manager's USB passthrough.
RUN { getent group kronos-clients >/dev/null || groupadd -r kronos-clients; } \
    && { getent group cosmos-pads >/dev/null || groupadd -r cosmos-pads; } \
    && useradd -m -G kvm,kronos-clients,cosmos-pads -s /bin/bash user \
    && usermod -p '' user \
    && rm -f /etc/sudoers.d/wheel

# No pam.d/greetd override needed for the empty hash to work: Fedora's stock
# /etc/pam.d/password-auth and system-auth (authselect-generated) already have
# `auth sufficient pam_unix.so nullok`, and greetd's own /etc/pam.d/greetd includes
# system-auth for auth, account, password and session, so an empty hash authenticates
# silently - which is what cosmos-autologin relies on (read from the packages; the full
# boot path is exercised by Full Pass and the Run tab).

# Default profile pictures for the profile picker (cosmos-profiles; direct requirement: a set
# of copyright-free defaults). Eight original, space-themed pictures drawn from scratch by
# config/user-face/generate_avatars.py - no photographs, characters or third-party artwork -
# released under CC0. They are plain cosmetic choices on the one shared account: the picker
# offers whatever .png files are in this directory, and only the chosen one is copied
# anywhere.

# Never type sudo: in an interactive shell, every ordinary command is answered by cosmos-priv
# (a symlink farm in /usr/libexec/cosmos-sudo/bin, built at the end of this file once everything
# is installed - see the RUN there), which runs it as you or as root according to what it
# needs: dnf/systemctl/mount... always as root, file and read commands only when the paths
# need it (so nothing in your home ends up root-owned), editors on files you cannot write, and
# anything else as you first and again as root if it fails with a permission error. Graphical
# apps, shells, interpreters, compilers, git/ssh/containers get no wrapper at all. /etc/profile.d
# puts the farm first in PATH for interactive shells only; the real command runs with PATH
# restored, so scripts are untouched. The account has no sudo: what needs root goes to kronos (`kronos exec`, below), so
# this is convenience, not a way around it. `raw CMD` skips it; `cosmos-autosudo off` turns
# it off; `... | put /etc/foo` is the way to write a system file from a redirect.

# Updates: one applet for Flatpak, AppImages (AM) and the system (DNF / bootc / rpm-ostree). A
# tray icon that checks every few hours and opens a window to update each source or all of them
# (running the commands in a cosmos-task window). It is started by the session; "Updates" is also
# in the grid. See cosmos_updates.py for what each source can and cannot do on an image-based
# system.

# The profile picker (cosmos-profiles) and the automatic login. There is no Unix login
# screen: greetd's default session is cosmos-autologin, which logs the one account in and
# starts cosmos-session; cosmos-session-apps then runs the picker (also the first-run "create
# your profile" step, replacing the old first-login wizard). cosmos-autologin falls back
# to the full login screen (cosmos-greeter) if PAM ever asks for anything - e.g. someone
# gives the account a password - so a machine can never be left with no way in.
# cosmos-task: a window that shows a command's output and takes a reply - what stands in for
# a terminal everywhere CosmOS used to open one (Shop installs, updates, asset downloads).

# The account's own real Claude Code settings (auto-mode config,
# permissions allow-list) - this image ships `claude` pre-installed
# (see the npm install further down/README.md), so it should already
# be configured the way its one real user actually runs it, not
# starting cold on first launch. Both `/etc/skel` (any future account
# this image ever creates) and the real already-created `user` home
# (useradd -m already ran above) get a copy - skel alone wouldn't touch
# the account that already exists at this point in the build.
COPY config/claude/settings.json /etc/skel/.claude/settings.json
COPY config/claude/settings.json /home/user/.claude/settings.json
RUN chown -R user:user /home/user/.claude

# GameBox's own real install target (atomic/Containerfile's exact
# rationale, reproduced here so packaging/cosmos_install.py's install()
# - which hardcodes APP_ROOT=/cosmos/app/current and
# paths.py's DATA_ROOT=/userdata - works unmodified on this image too):
# bootc's / is effectively read-only, so both are plain directories
# under /var (the real persistent, writable area) with a symlink at the
# path the app itself expects.
RUN mkdir -p /var/lib/cosmos && ln -s /var/lib/cosmos /cosmos
RUN mkdir -p /var/userdata && chmod 777 /var/userdata && ln -s /var/userdata /userdata

# GameBox itself, from the gamebox/ folder of this repo. It goes to /usr/share/gamebox, read-only; gamebox-dir makes a writable
# copy at /cosmos/app/current (GameBox's own install path, above) on first use and refreshes it
# when this image changes - GameBox writes credentials and settings into its own tree, so it
# cannot run from /usr ($GAMEBOX_REPO_DIR points it at another copy, for developing it). gamebox/wm/consolewm.c is compiled here: the binary is not in the repo. The
# stamp is what tells gamebox-dir the image carries a newer copy. The copy is about 300 MB.
# The source-built emulators (xemu, Flycast) and the libraries they load; GameBox launches them through {emu} (library.py, /opt/cosmos-emulators).
RUN install -d -o user -g user /var/lib/cosmos/app
# No API keys and no .env are shipped: anything in an image can be read out of the ISO. Box art comes
# from the Directory API instead (an IGDB proxy that holds the project's secret; needs a signed-in
# CosmOS account), and a key a person enters in GameBox's own Settings > API is stored on their own
# machine. GameBox treats a missing .env as "no default keys".

# (Heroic Games Launcher used to be installed here. It is gone: CosmOS uses GameBox's own game-store client (Epic, GOG and more,
# with gogdl and legendary as its backends above). app-policy.json blocks Heroic in every packaging and find-blocked-apps fails the build if
# it turns up.)

# itch (itch.io's own desktop app) - no native .rpm/.deb upstream (only
# a self-contained linux-amd64 tarball, confirmed live against itchio/
# itch's own GitHub releases) and no flatpak, direct request - extracted
# straight to /opt/itch, same real /opt precedent Astronar Studio uses.
# chrome-sandbox needs real root-owned setuid (standard Electron
# requirement, the same fix Discord's own .rpm postinstall applies) -
# without it Chromium's sandbox init fails outright; this is the correct
# fix, not disabling the sandbox with a --no-sandbox flag instead.
RUN curl -fL -o /tmp/itch.tar.gz \
        https://github.com/itchio/itch/releases/download/v26.20.0/itch-v26.20.0-linux-amd64.tar.gz \
    && mkdir -p /opt/itch \
    && tar -xzf /tmp/itch.tar.gz -C /opt/itch \
    && rm -f /tmp/itch.tar.gz \
    && chown root:root /opt/itch/chrome-sandbox \
    && chmod 4755 /opt/itch/chrome-sandbox \
    && printf '[Desktop Entry]\nType=Application\nName=itch\nExec=/opt/itch/itch\nComment=itch.io desktop app - browse, buy, and play indie games\nIcon=/opt/itch/resources/app/src/static/images/window/itch/icon.png\nCategories=Game;\nTerminal=false\n' \
        > /usr/share/applications/itch.desktop

# Android Studio, IntelliJ IDEA, PyCharm, Eclipse and DBeaver used to be baked in here as pinned downloads. They are the
# "Developer Tools" Expansion Pack now (Flatpaks, installed from the Shop on demand), so the base image doesn't carry them.

# Bitwarden desktop - direct requirement ("I want native Bitwarden too" -
# on top of the browser-extension install already covered above via
# Brave's own managed-policy mechanism, see that comment). Same real
# GitHub-release .rpm pattern as dbeaver.rpm just above - Bitwarden
# doesn't publish to Fedora's own repos or RPM Fusion (confirmed live:
# `dnf list bitwarden` comes back empty), but the clients repo's own
# desktop-vX.Y.Z release does carry a real x86_64.rpm asset alongside
# its .deb/.AppImage/etc.
RUN --mount=type=cache,target=/var/cache/libdnf5,sharing=locked curl -fL -o /tmp/bitwarden.rpm \
        https://github.com/bitwarden/clients/releases/download/desktop-v2026.9.0/Bitwarden-2026.9.0-x86_64.rpm \
    && dnf install -y --setopt=keepcache=1 /tmp/bitwarden.rpm \
    && rm -f /tmp/bitwarden.rpm

# GameBox itself, directly launchable/testable on this OS.
# launcher/run_on_vt.sh is GameBox's own already-documented dev entry
# point for exactly this (see its own docstring: opens its own X server
# on :8, specifically so it coexists with a real desktop session already
# on :0, no install/stage_build.sh/cosmos_install.py step needed - hot-
# reload stays on, so this runs the actual live checkout). Both land as
# plain PATH commands (no .py/.sh suffix) - direct request, run by hand
# from a terminal rather than wired to fire automatically off a desktop
# icon (gamebox-fetch-assets in particular hits the real libretro
# buildbot over the network every time it's run, not a free local
# check, so it should only run when actually wanted). The desktop entry
# for launch-gamebox itself is now "GameBox" - see its own comment below.
# cosmos-vm-manager - direct requirement ("let's wrap qemu in our own
# UI to match cosmos-bar") wrapping the qemu-system-x86/edk2-ovmf combo
# already installed above (see that earlier RUN's own comment on why
# they're there at all) in a real GTK app instead of hand-typed qemu
# flags every time - see config/scripts/cosmos-vm-manager.py's own
# docstring for the full design (per-VM persistent OVMF_VARS.fd,
# boot-order/disk-bus/network-mode options added after direct
# request). "computer" - a real, confirmed-present Papirus icon,
# same verification bar every other icon choice this session used.
# Host-device passthrough (the VM dialog's "Pass through host devices" box, logic in
# vm_passthrough.py - next to it in /usr/local/bin, where cosmos-vm-manager imports it):
# audio through the host's PipeWire/PulseAudio, USB devices, host keyboards/mice (a temporary
# per-device grant from kronos, admin passphrase - the desktop user is not in the input group) and whole PCI devices such as a GPU through
# VFIO. The udev rule gives the kvm group the VFIO and USB device nodes, the limits file
# the unlimited locked memory VFIO needs; the qemu-* modules are the GTK window, the
# audio backends and usb-host, which Fedora ships as separate packages. What it cannot do
# from here: turn the IOMMU on or bind a GPU to vfio-pci - those are host choices (kernel
# arguments), and the app explains them instead of doing them.
# Android (Waydroid), from the Virtualization pack: setup and gaming settings (cosmos_android.py). Its entry appears once Waydroid is installed (TryExec).
# The button mapper: controller buttons and keys become touches in the Android window (cosmos_touchmap.py is the engine and the daemon).

# cosmos-wallpaper - direct requirement ("I want users to be able to set their own
# wallpaper in the desktop") - see config/scripts/cosmos-wallpaper.py's own docstring:
# it records the choice in ~/.config/cosmos/wallpaper and cosmos-launcher, the desktop's
# permanent home screen, paints it.

# CosmOS Files (cosmos-files) replaces Nautilus: a file manager that only ever shows the active
# profile's own files folder, USB drives, and the machine's other internal drives - never / or
# anything else (Nautilus can't be restricted that way). cosmos_places.py decides what is
# allowed (lsblk; the system disk is never listed; symlinks and .. are resolved before any path
# is accepted), cosmos_fileops.py does the copying/moving/deleting inside that. It is also what
# "open folder" from other apps runs (inode/directory below); a folder outside those places just
# opens the place list. Drives are mounted when clicked (udisks; the polkit rule lets this
# account mount internal drives without a password prompt).
#
# The same view is the file dialog for other apps: cosmos-portal-filechooser is an
# xdg-desktop-portal FileChooser backend (D-Bus activated; config/portals/), preferred for
# FileChooser in cosmos-portals.conf, and the session sets GTK_USE_PORTAL=1 so GTK apps and
# Chromium-based browsers use the portal.
RUN printf '[Default Applications]\ninode/directory=cosmos-files.desktop\n' > /etc/xdg/mimeapps.list

# cosmos-input / cosmos-osk - direct requirement ("cosmos to have the same osk
# and gamepad unification as gamebox so the os itself can be used with nothing
# but a game controller"). A vendored, desktop-trimmed copy of GameBox's input
# daemon and on-screen keyboard (config/input/): gamepad -> virtual keyboard +
# mouse system-wide via UInput, so every app, the greeter and the lock screen
# respond to a controller. Baked into the image rather than read from a GameBox
# checkout because a controller-only OS has to work before anything has been
# pulled. python-evdev is already in the pip list above. The udev rule gives
# the active session (including the greeter's) access to /dev/uinput via
# logind's uaccess ACLs - the same rule Steam's udev package ships, repeated
# here so it doesn't depend on Steam being installed.
# cosmos_remap.py is the per-player button/stick remapping for GameBox's four virtual pads: the
# Controller Customizer writes it, GameBox's inputd.py (input/) reads it from here.

# Controller Customizer (cosmos-controllers): light colour/brightness/effects on the DualSense and
# DualShock 4 lightbar, player lights (DualSense, Switch), the Switch home light, a rumble test,
# battery, a live gyroscope/accelerometer view, and (DualSense) adaptive triggers and haptics, saved per controller in the active profile. All
# through the kernel's own drivers (LED class, force feedback, the motion-sensor input node); the
# session starts its background half, which puts the saved look on each controller when it
# connects and plays the animated effects. The udev rule lets the `cosmos-pads` group (the desktop user
# is in it) write the LED files of Sony/Nintendo controllers, which are root-only by default.

# GameBox - direct request: GameBox presented as a proper, launchable
# frontend of this OS rather than the old "Launch GameBox (Test)" dev
# entry, which ran under a terminal so a startup error stayed visible. GameBox runs
# without one (Terminal=false) and
# launch-gamebox itself reports failures via notify-send, so a click on
# the icon still never silently does nothing. Same launch mechanism as
# before in spirit (launch-gamebox -> gamebox-nested: the GameBox launcher inside a
# full-screen Xephyr, since a Wayland session can't hand its display to a second X
# server), and still deliberately no automatic gamebox-fetch-assets - run that
# ("GameBox: fetch assets" in the Dev tab, which runs it in a task window) when
# engines/cores/webshell are actually missing.
# Pinned in every new account's launcher/bar favorites by default
# (cosmos_favorites.py's own file format: one desktop-id per line).
RUN mkdir -p /etc/skel/.config/cosmos \
    && printf 'gamebox.desktop\n' > /etc/skel/.config/cosmos/favorites.txt


# cosmos_favorites - a real, live bug this fixes (reported traceback:
# `cosmos-bar`, line 56, `import cosmos_favorites` ->
# ModuleNotFoundError), root-caused to exactly the missing COPY line
# that module's own docstring already says should be here. Shared
# favorites-list storage for cosmos-launcher/cosmos-bar (see that
# file's own docstring for why it's one real shared module instead of
# separate copies) - keeps its .py extension and lands directly in
# /usr/local/bin, same directory as the scripts below that `import` it
# with no extension of their own, because Python puts the running
# script's own directory first on sys.path regardless of that script's
# own filename - nothing else needed for the import to resolve. Must be
# copied before both of its consumers below.

# cosmos-launcher - direct requirement ("let's replace rofi with a full
# screen alphabetic grid" - see that file's own docstring for the full
# design). Three tabs in one window (Apps/Web Apps/Studio) - former
# separate cosmos-webapps.py/cosmos-grid.py scripts merged in
# directly (direct requirement: "combine apps, web apps, and studio
# apps into the bottom left apps icon, and just separate the grids by
# tabs at the top... this needs to be easy enough for my wife to use").
# No .desktop entry of its own - unlike everything else this session
# installed into /usr/share/applications, this IS the applications menu
# itself, launched directly by cosmos-bar.py's own bottom-bar button
# (see that function's own comment), not something meant to appear as
# an entry inside its own grid.

# cosmos-shop - the hand-made GTK3 App Store (DNF/AppImage/Flatpak, see
# that file's own docstring for the full design), the same full-screen
# grid treatment as cosmos-launcher above, for cosmos-bar.py's own
# "Shop" button. Also no .desktop entry of its own, same reasoning as
# cosmos-launcher.

# The real, final bar (direct request, replacing xfce4-panel entirely - see
# config/scripts/cosmos-bar.py's own docstring for the full why): a bottom-left
# Applications button, a left bar with Shop/Brave/the shortcuts hint/Favorite Apps,
# and a top bar with the centered clock and the system tray. Three gtk-layer-shell
# panels whose exclusive zones reserve every screen edge in cosmos-wm, which is what
# _NET_WM_STRUT_PARTIAL used to do on X11. The tray is a StatusNotifierItem host
# (cosmos_sni_host.py) - there is no XEmbed tray any more.
#
# The Python modules the bar, launcher, lock screen and greeter import sit next to
# them in /usr/local/bin (that is the script's own directory, so they resolve with no
# path setup).

# cosmos-wm - CosmOS's Wayland compositor, built on wlroots (see
# config/wm/cosmos-wm.c's own docstring for what it does and why). The two protocol
# headers wlroots' own headers include are generated here with wayland-scanner from
# the XML wayland-protocols / wlr-protocols ship. The pkg-config name pins the
# wlroots API version (0.20 on this Fedora release): a newer base image whose
# wlroots has moved on fails here, loudly, instead of building against the wrong
# API. Everything it needs is in the managed package list above.


# The session scripts. cosmos-session (what the login manager runs for the "CosmOS"
# session) sets the environment (cosmos-session-env) and replaces itself with
# cosmos-wm, whose startup command is cosmos-session-apps - the autostart list the
# old i3 config's `exec --no-startup-id` lines turned into. gamebox-session is the
# other login mode: the same compositor with only GameBox, run full screen by
# gamebox-nested inside Xephyr (shown by the compositor's XWayland shim), ending the
# session when GameBox exits. launch-gamebox (above) uses the same nested runner from
# the desktop. Xephyr is in the managed package list.

# The two sessions the greeter's Desktop/GameBox switch chooses between (its session
# keys are these file names minus ".desktop").

# xdg-desktop-portal backends for the session (see the file's own comment).

# cosmos-greeter - direct requirement ("I want to make my own light greeter too"),
# the login screen. greetd runs it (inside cosmos-wm, as the unprivileged greetd
# account - see config/greetd/config.toml) and it talks to greetd over its IPC
# socket (cosmos_login.py, tested against a fake greetd); greetd does the actual
# PAM work, including the forced first-login password change below. The greeter
# account's restart/shutdown buttons need the polkit rule; its remembered
# per-account session choice lives in /var/lib/cosmos-greeter.
RUN mkdir -p /var/lib/cosmos-greeter \
    && chown greetd:greetd /var/lib/cosmos-greeter \
    && cosmos-login-enable

# Build the "never type sudo" symlink farm now that every package and desktop entry is in place
# (see the COPY of cosmos-priv above). Takes about two minutes: it asks rpm which package owns
# each command so graphical apps' helper binaries are left out too.
RUN /usr/libexec/cosmos-sudo/cosmos-sudo-shims

# Flatpak + the real Flathub remote - direct requirement (a policy
# reversal: "no flatpak anywhere on this image" was the standing rule
# everywhere else in this file, deliberately dropped specifically for
# CosmOS Shop's own Flatpak section - see config/scripts/cosmos-shop.py's
# own docstring). The remote is added system-wide at build time (so it
# already exists for every real account with nothing to configure by
# hand first), matching Flathub's own documented flatpakrepo URL;
# CosmOS Shop's own installs still go through `flatpak install --user`
# at runtime, not root, same per-account philosophy as AM/AppMan.
# >>> bootc-studio:flatpak-apps >>>
RUN --mount=type=cache,target=/var/cache/libdnf5,sharing=locked dnf install -y --setopt=keepcache=1 flatpak \
    && flatpak remote-add --if-not-exists flathub https://flathub.org/repo/flathub.flatpakrepo
RUN flatpak install --system -y flathub com.rustdesk.RustDesk
# <<< bootc-studio:flatpak-apps <<<

RUN systemctl set-default graphical.target && \
    systemctl enable greetd.service

# >>> bootc-studio:appman-apps >>>
# ar/notify-send/wget: AM shells out to these itself (ar and
# notify-send for some install paths, wget as its own curl
# fallback) and, missing any of them, stops to ask interactively
# whether to fetch static replacements - which hangs a build with
# no terminal. Installing the real packages up front avoids that
# prompt entirely. Not confirmed exhaustive for every app in AM's
# catalog - some entries may need other tools this doesn't cover.
RUN --mount=type=cache,target=/var/cache/libdnf5,sharing=locked dnf install -y --setopt=keepcache=1 wget binutils libnotify
# AM's system-wide install script (https://github.com/ivan-hc/AM),
# run directly rather than through AM-INSTALLER: that wrapper is an
# interactive menu that calls tput (fails with no $TERM in a build)
# and re-runs this same script via sudo. INSTALL only insists that
# SUDO_USER is set, and uses it as /opt/am's owner - root here.
# curl, not wget: confirmed live that plain quay.io/fedora/fedora-
# bootc images ship curl but not wget, so wget only worked before
# by coincidence, on images (like this repo's own) that happen to
# install it earlier for unrelated reasons.
# TERM=xterm: am itself calls tput too, not just AM-INSTALLER.
# HOME=/tmp/am-build-home, not /root: confirmed live that /root is
# a symlink to /var/roothome on this ostree/bootc base, and /var
# only gets its real FHS layout (roothome included) from
# systemd-tmpfiles at first real boot, not inside a `podman build`
# RUN step - so /root is a dangling symlink here, and anything AM
# writes under $HOME (its cache, its third-party sources file)
# fails outright. A plain scratch directory sidesteps this the
# same way for any build-time tool that assumes a real, writable
# $HOME - none of them get one during a container build.
RUN curl -fsSL https://raw.githubusercontent.com/ivan-hc/AM/main/INSTALL -o /tmp/AM-INSTALL \
    && chmod a+x /tmp/AM-INSTALL \
    && TERM=xterm HOME=/tmp/am-build-home SUDO_USER=root /tmp/AM-INSTALL \
    && rm -f /tmp/AM-INSTALL
# <<< bootc-studio:appman-apps <<<

# ---- One packaging per app, and sandboxed Flatpak versions of the Qt apps ----------------------------
# Qt/KDE apps (Kdenlive, Krita, OBS, MuseScore, Telegram, ... - the list is config/cosmos-apps/app-policy.json)
# are Flatpaks, installed system-wide in the flatpak-apps block above. Qt's own file dialog can browse
# anywhere and cannot be pointed at the portal for a normal app, but a sandboxed app has no other way to
# open a file: it asks xdg-desktop-portal, which here is cosmos-portal-filechooser (your profile's files,
# USB drives, other drives - never /). The RPM of the same app is not installed, and the rules below keep
# a second copy from being installed later:
#   - dnf's excludepkgs refuses the RPM of anything that is a Flatpak here;
#   - the Flathub remote has a filter refusing the Flatpak of anything that is an RPM here (no app is
#     an RPM-policy app right now, so the filter is empty, but the mechanism stays);
#   - cosmos-priv and the Shop explain the refusal (and refuse a user copy of a system Flatpak);
# test_cosmos_app_policy.py checks this file, the package lists and the policy agree.
# The global Flatpak override removes the broad file access apps ask for (home, host, the XDG folders),
# so what a sandboxed app can read is only what it is handed through the file dialog portal.
# The curated catalog: the only Flatpaks, packages and AppImages that can be installed (config/cosmos-apps/catalog.json). The Flathub filter below is
# generated from it as an allowlist, and Kronos, cosmos-priv and the Shop read it. The build fails if it is missing or has mistakes, instead of
# quietly curating nothing.
# Game downloads (the Shop's Games and BIOS, installed by the Game Downloads pack) and the module installer behind it; xiso.py converts a downloaded Xbox
# dump (extract-xiso is built in the emulators stage). GameBox itself has no shop any more.
RUN python3 /usr/lib/cosmos/cosmos_catalog.py validate
# Expansion Packs (Shop > Expansion Packs): the definitions and the module the Shop uses to install/complete a pack.
RUN python3 /usr/lib/cosmos/cosmos_app_policy.py flatpak-filter > /etc/flatpak/cosmos-flathub.filter \
    && if grep -q '^deny' /etc/flatpak/cosmos-flathub.filter; then flatpak remote-modify --system --filter=/etc/flatpak/cosmos-flathub.filter flathub; fi \
    && { grep -q '^\[main\]' /etc/dnf/dnf.conf || printf '[main]\n' >> /etc/dnf/dnf.conf; } \
    && sed -i "/^\[main\]/a excludepkgs=$(python3 /usr/lib/cosmos/cosmos_app_policy.py dnf-exclude)" /etc/dnf/dnf.conf \
    && flatpak override --system --nofilesystem=host --nofilesystem=host-os --nofilesystem=host-etc --nofilesystem=home \
        --nofilesystem=xdg-desktop --nofilesystem=xdg-documents --nofilesystem=xdg-download --nofilesystem=xdg-music \
        --nofilesystem=xdg-pictures --nofilesystem=xdg-public-share --nofilesystem=xdg-templates --nofilesystem=xdg-videos

# Expansion Packs baked into THIS build. The image is slim on purpose: everything that isn't the gaming and entertainment core is a pack the Shop installs
# on demand (config/cosmos-apps/expansion-packs.json). To make a "full" image instead, build with e.g.  --build-arg PACKS="creative office"  - the same
# pack definitions are used here and by the Shop, so there is one list. A mistyped pack id or a pack the app policy refuses fails the build.
ARG PACKS=""
RUN --mount=type=cache,target=/var/cache/libdnf5,sharing=locked eval "$(python3 /usr/lib/cosmos/cosmos_packs.py bake ${PACKS})"

# ---- Remote access (SSH) and the Webtop ---------------------------------------------------------------------------
# SSH: key-only, for the desktop account only, on by default with no key installed (so nothing can log in until you add one).
# Keys are added from Settings > Remote Access (cosmos-remote, no terminal needed) or in config/ssh/authorized_keys before a
# build. This is also how `claude` (Claude Code, above) and anything else command-line is used from another computer. The
# Webtop is the development environment now that the image has no developer tools: a desktop in a rootless container, shown in
# a browser window, listening on 127.0.0.1 only (reach it from elsewhere through an SSH tunnel). It downloads on first use.
COPY --chown=user:user config/ssh/authorized_keys /home/user/.ssh/authorized_keys
RUN chown user:user /home/user/.ssh && chmod 0700 /home/user/.ssh && chmod 0600 /home/user/.ssh/authorized_keys \
    && cosmos-remote-enable user

# ---- Firewall ------------------------------------------------------------------------------------------------------
# firewalld with one zone, "cosmos" (config/firewalld/): everything coming in from the network is rejected except named doors. SSH
# (key-only) is open by default; the Webtop's HTTPS port is a service that Remote Access opens only while network access is on, and
# Sunshine's streaming ports are a temporary door GameBox opens only while it hosts a Play Together session.
# Nothing else listens for the network by default. cosmos_firewall.py (and kronos's firewall.door action) open and close doors.
RUN cosmos-firewall-enable

# ---- Kronos: the only route to admin rights -------------------------------------------------------------------------------
# docs/kronos-design.md. The desktop account has NO root path of its own (no wheel, no sudoers, no polkit admin). One system account,
# `kronos` (no shell, locked), runs kronosd, which takes typed requests over /run/kronos/kronos.sock from the accounts in
# /etc/kronos/clients, decides (automatic / admin passphrase / refused), writes every decision to the hash-chained audit log
# (/var/log/kronos/audit.log, append-only) and only then acts - through /usr/libexec/kronos/act, the one program sudoers lets it run,
# which re-validates every action as root. `kronos` is the command; cosmos_kronos.py is the same thing for programs. The passphrase is
# created the first time a powerful action asks for it. It is asked for by the DAEMON, through kronos-agent (started with the session), so it
# never passes through the program that asked; kernel.yama.ptrace_scope=1 stops one program of the account from reading another's memory
# (the prompt's, or the agent's). The recovery app (cosmos-recovery.service, started only when the kernel command
# line has cosmos.recovery=1) resets it without a shell.
# The build proves the audit log writes, chains and verifies, so a broken one fails the build, not a later admin action.
RUN visudo -cf /etc/sudoers.d/kronos \
    && systemctl enable kronos.socket cosmos-recovery.service \
    && d=$(mktemp -d) \
    && python3 -c "import sys; sys.path.insert(0,'/usr/lib/kronos'); import kronos_audit as a; l=a.AuditLog('$d/audit.log'); l.append('decision', action='selftest'); l.append('result', action='selftest'); ok,p=a.AuditLog.verify(l.files()); sys.exit(0 if ok else 'audit self-test failed: %s' % p)" \
    && kronos-audit --log "$d/audit.log" verify && rm -rf "$d" \
    && getent passwd kronos >/dev/null && ! id -nG user | tr ' ' '\n' | grep -qx wheel && test ! -e /etc/sudoers.d/wheel

# ---- Game Packs (cosmos-winpack) -------------------------------------------------------------------------------------------
# A Windows game folder squeezed into one read-only SquashFS pack (zstd) that runs under ANY Proton version: cosmos_proton.py imports builds (a folder,
# an archive, or a checked GE-Proton / Proton-CachyOS download), cosmos_protondb.py finds the game's ProtonDB rating, the launch options and Proton
# version that worked for others and any automatic fix (umu-protonfixes) by itself, and cosmos_winpack.py builds the image (mksquashfs) and runs it
# (squashfuse + fuse-overlayfs, no root; the Wine prefix and saves live beside the pack). Packs and prefixes are per-user data under
# ~/.local/share/cosmos/winpacks, Proton builds under ~/.local/share/cosmos/proton. `umu-run` is used when it is installed (it is not in Fedora's repos).

# find-qt-apps: lists any graphical Qt app in the image that is not a Flatpak (or one of the two hand-built
# emulators in qt-app-exceptions.txt), because such an app has Qt's own, unscoped file dialog. Run at the end
# of the build so the result is in the build log; it only warns - run `find-qt-apps` (or
# `find-qt-apps --root <mounted image>`) yourself and it exits 1 when something needs attention.
COPY config/cosmos-apps/qt-app-exceptions.txt /usr/share/cosmos/qt-app-exceptions.txt
COPY config/scripts/find-qt-apps /usr/local/bin/find-qt-apps
RUN chmod 0755 /usr/local/bin/find-qt-apps && { /usr/local/bin/find-qt-apps || echo "WARNING: the Qt apps above are not Flatpaks (see find-qt-apps)"; }

# find-terminals: CosmOS has no terminal, so the build FAILS if any terminal emulator (or remote-shell client) ended
# up in the image - by desktop category, by the blocked names in app-policy.json, or as a program on its own. The
# install paths refuse the known ones (dnf excludepkgs above, the Flathub filter, the Shop, cosmos-priv, kronos);
# this catches anything a dependency dragged in. Programs with a terminal built in (IDEs) are a separate question.
RUN find-terminals
# Heroic must not be in the image either (GameBox's own client replaces it) - same kind of check, same failure.
RUN find-blocked-apps
