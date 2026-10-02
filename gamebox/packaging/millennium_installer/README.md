# Millennium Installer (built from source)

`millennium-installer` is a real, unmodified build of
[SteamClientHomebrew/Installer](https://github.com/SteamClientHomebrew/Installer)
(the `main` branch at build time), Millennium's own official GUI
installer - built from source and committed here the same way
RetroArch/Wine/mkxp-z are, because reimplementing its actual Linux
install logic (patching Steam's launch script with an `LD_PRELOAD`
injection) in Python would risk corrupting a real Steam install if
gotten wrong. This reuses Millennium's own tested code exactly instead.

Launched from Settings > "Theme Steam (Millennium)" (see
`launcher/app.py`'s `build_settings_page()`) - a real, interactive GUI
tool, not something CosmOS drives automatically. Point it at the
real Steam install path (confirmed on this project's own dev machine:
`~/.local/share/Steam`, i.e. `paths.DATA_ROOT`-unrelated - this is
Steam's own real data directory, not anything CosmOS owns) and click
Install.

## Why this exists

Steam moved from a Flatpak to a native RPM Fusion package
(`packaging/steam_native.py`) specifically to make this possible -
Millennium's own maintainers ruled out Flatpak Steam support entirely
as a fundamental sandbox-contract conflict, not a bug that might get
fixed.

## Building it again

```
git clone https://github.com/SteamClientHomebrew/Installer
cd Installer
CMAKE_POLICY_VERSION_MINIMUM=3.10 cmake -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build -j$(nproc)
strip build/Installer
```

Real host deps needed beyond a C++20 compiler + CMake (confirmed live
on Fedora): `glew-devel` (the one dependency this project's own
`bootstrap_deps.cmake` expects as a system package on Linux, not
vendored - everything else is a tarball already checked into
`Installer/thirdparty/`), plus the usual GLFW/X11 stack
(`libX11-devel`, `libXext-devel`, `libXrandr-devel`, `libXcursor-devel`,
`libXi-devel`, `libXinerama-devel`, `libXScrnSaver-devel`,
`libXxf86vm-devel`, `mesa-libGL-devel`, `mesa-libEGL-devel`,
`wayland-devel`, `wayland-protocols-devel`, `libxkbcommon-devel`) and
`openssl-devel` (curl's own TLS backend on Linux). `CMAKE_POLICY_VERSION_MINIMUM=3.10`
works around glew-cmake's old `cmake_minimum_required` on modern CMake
(4.x), the one real build hiccup hit - everything else configured and
built clean on the first real attempt.

## Verified live (Xvfb, real mouse-driven navigation)

Built successfully from a clean clone; the resulting binary runs as a
real GLFW/OpenGL GUI (confirmed via screenshot: a real "Steam Homebrew"
window, not a crash or blank surface) and correctly responds to real
mouse clicks navigating through Install/Remove selection and the
install-prompt screen (Steam path field, real size/version info,
working Back/Next navigation).

**Not verified end-to-end**: actually completing a real install (typing
a path into the ImGui text field and clicking through to a finished
Millennium injection) - GLFW's text input didn't receive synthetic
XTest key events in this project's own headless Xvfb test harness (a
known category of limitation testing ImGui/GLFW apps without a real
desktop session or `xdg-desktop-portal`, not a defect in the built
binary itself - every mouse-driven interaction worked correctly). This
is a one-time, interactive step meant for a human at a real desktop
session; run it there.
