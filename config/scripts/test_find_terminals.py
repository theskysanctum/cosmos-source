"""Tests for find-terminals and the terminal entries of the app policy."""
import importlib.machinery
import importlib.util
import os
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
loader = importlib.machinery.SourceFileLoader("find_terminals", os.path.join(HERE, "find-terminals"))
spec = importlib.util.spec_from_loader("find_terminals", loader)
ft = importlib.util.module_from_spec(spec)
loader.exec_module(ft)
import cosmos_app_policy as pol


class Root(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        for d in ("usr/bin", "usr/share/applications", "var/lib/flatpak/exports/share/applications", "etc/alternatives"):
            os.makedirs(os.path.join(self.root, d))

    def desktop(self, name, exec_line, categories="", where="usr/share/applications"):
        with open(os.path.join(self.root, where, name + ".desktop"), "w") as fh:
            fh.write(f"[Desktop Entry]\nType=Application\nName={name}\nExec={exec_line}\nCategories={categories}\n")

    def program(self, name):
        with open(os.path.join(self.root, "usr/bin", name), "w") as fh:
            fh.write("#!/bin/sh\n")

    def ids(self):
        return {(f["kind"], f["id"]) for f in ft.scan(self.root)}


class ScanTests(Root):
    def test_a_clean_system_reports_nothing(self):
        self.desktop("org.kde.krita", "krita %F", "Graphics;Qt;")
        self.program("krita")
        self.desktop("org.kde.krita.flatpak", "/usr/bin/flatpak run --command=krita org.kde.krita", where="var/lib/flatpak/exports/share/applications")
        self.assertEqual(ft.scan(self.root), [])
        self.assertEqual(ft.main(["--root", self.root]), 0)

    def test_the_desktop_category_alone_is_enough(self):
        self.desktop("brandnew", "mystery-term", "System;TerminalEmulator;")     # a name no list has ever seen
        self.assertEqual(self.ids(), {("desktop", "brandnew")})
        self.assertEqual(ft.main(["--root", self.root]), 1)

    def test_blocked_names_without_the_category(self):
        self.desktop("weird", "alacritty --class x %U", "Utility;")
        self.program("kitty")
        self.assertEqual(self.ids(), {("desktop", "weird"), ("program", "kitty")})

    def test_flatpak_terminals_are_found_by_id_even_with_no_category(self):
        self.desktop("org.gnome.Terminal", "/usr/bin/flatpak run --branch=stable --command=gnome-terminal org.gnome.Terminal @@u %U @@",
                     where="var/lib/flatpak/exports/share/applications")
        self.desktop("com.acme.NewTerminal", "/usr/bin/flatpak run com.acme.NewTerminal", where="var/lib/flatpak/exports/share/applications")
        self.assertEqual({i for k, i in self.ids() if k == "flatpak"}, {"org.gnome.Terminal", "com.acme.NewTerminal"})

    def test_the_x_terminal_emulator_alternative(self):
        os.symlink("/usr/bin/xterm", os.path.join(self.root, "etc/alternatives/x-terminal-emulator"))
        self.assertIn(("program", "x-terminal-emulator"), self.ids())

    def test_purge_removes_flatpak_terminals_only(self):
        self.desktop("org.gnome.Terminal", "/usr/bin/flatpak run org.gnome.Terminal", where="var/lib/flatpak/exports/share/applications")
        self.program("xterm")
        calls = []

        class R:
            returncode = 0
        removed = ft.purge_flatpaks(ft.scan(self.root), run=lambda cmd, **kw: (calls.append(cmd), R())[1])
        self.assertEqual(removed, ["org.gnome.Terminal"])
        self.assertEqual(calls[0][:3], ["flatpak", "uninstall", "--user"])


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.apps, self.blocked = pol.load(), pol.load_blocked()

    def check(self, kind, name):
        return pol.check(kind, name, self.apps, self.blocked)[0]

    def test_known_terminals_are_refused_in_every_packaging(self):
        for rpm in ("gnome-terminal", "xterm", "alacritty", "kitty-terminfo", "konsole", "putty", "foot"):
            self.assertFalse(self.check("rpm", rpm), rpm)
        for fp in ("org.gnome.Terminal", "org.gnome.Terminal/x86_64/stable", "com.raggesilver.BlackBox", "app.devsuite.Ptyxis",
                   "com.acme.NewTerminal", "org.kde.konsole", "page.codeberg.dnkl.foot"):
            self.assertFalse(self.check("flatpak", fp), fp)
        for am in ("alacritty", "wezterm", "tabby"):
            self.assertFalse(self.check("appimage", am), am)

    def test_ordinary_apps_are_not_caught(self):
        for rpm in ("htop", "vlc", "stow", "footnote", "ffmpeg", "terminus-fonts", "mpv"):
            self.assertTrue(self.check("rpm", rpm), rpm)
        for fp in ("org.videolan.VLC", "org.kde.krita", "org.mozilla.firefox", "com.spotify.Client", "io.itsterminal.WebPConverter"):
            self.assertTrue(self.check("flatpak", fp), fp)

    def test_the_build_inputs_include_the_terminals(self):
        exclude = pol.dnf_exclude(self.apps, self.blocked).split(",")
        for name in ("xterm", "gnome-terminal", "konsole", "alacritty"):
            self.assertIn(name, exclude)
        filt = pol.flatpak_filter(self.apps, self.blocked)
        for line in ("deny org.gnome.Terminal", "deny *Terminal*", "deny app.devsuite.Ptyxis"):
            self.assertIn(line + "\n", filt)
        self.assertFalse([l for l in filt.splitlines() if l.startswith("allow")])


class HeroicAndStoreTests(Root):
    """The other blocked program (Heroic) and the game-stores-by-dnf-only rule."""

    def setUp(self):
        super().setUp()
        bloader = importlib.machinery.SourceFileLoader("find_blocked_apps", os.path.join(HERE, "find-blocked-apps"))
        bspec = importlib.util.spec_from_loader("find_blocked_apps", bloader)
        self.fb = importlib.util.module_from_spec(bspec)
        bloader.exec_module(self.fb)

    def found(self):
        import cosmos_blocked_scan as scanner
        return scanner.scan(self.root, self.fb.groups())

    def test_heroic_is_found_by_desktop_entry_program_and_flatpak_id(self):
        self.desktop("heroic", "/opt/Heroic/heroic %U", "Game;")
        self.program("heroic")
        self.desktop("com.heroicgameslauncher.hgl", "/usr/bin/flatpak run com.heroicgameslauncher.hgl", where="var/lib/flatpak/exports/share/applications")
        found = self.found()
        self.assertEqual({f["group"] for f in found}, {"Heroic Games Launcher"})
        self.assertEqual({(f["kind"], f["id"]) for f in found}, {("desktop", "heroic"), ("program", "heroic"), ("flatpak", "com.heroicgameslauncher.hgl")})
        self.assertEqual(self.fb.main(["--root", self.root]), 1)

    def test_the_two_scans_do_not_mix(self):
        self.desktop("brandnew", "mystery-term", "System;TerminalEmulator;")
        self.desktop("heroic", "heroic", "Game;")
        self.assertEqual({f["id"] for f in ft.scan(self.root)}, {"brandnew"})                # find-terminals: terminals only
        self.assertEqual({f["id"] for f in self.found()}, {"heroic"})                          # find-blocked-apps: Heroic only

    def test_gameboxs_own_gog_backend_is_not_mistaken_for_heroic(self):
        self.program("gogdl")                                                                   # heroic-gogdl, installed as `gogdl`, is GameBox's backend
        self.desktop("gamebox", "launch-gamebox", "Game;")
        self.assertEqual(self.found(), [])
        self.assertEqual(self.fb.main(["--root", self.root]), 0)

    def test_policy_refuses_heroic_everywhere(self):
        for kind, name in (("rpm", "heroic"), ("rpm", "heroic-games-launcher"), ("flatpak", "com.heroicgameslauncher.hgl"),
                           ("flatpak", "com.heroicgameslauncher.hgl/x86_64/stable"), ("appimage", "heroic")):
            ok, why = pol.check(kind, name, pol.load(), pol.load_blocked(), pol.load_dnf_only())
            self.assertFalse(ok, (kind, name))
            self.assertIn("GameBox's own", why)

    def test_game_stores_are_dnf_only(self):
        args = (pol.load(), pol.load_blocked(), pol.load_dnf_only())
        for name in ("com.valvesoftware.Steam", "net.lutris.Lutris", "com.usebottles.bottles", "io.github.sharkwouter.Minigalaxy", "io.itch.itch"):
            ok, why = pol.check("flatpak", name, *args)
            self.assertFalse(ok, name)
            self.assertIn("dnf only", why)
        for name in ("steam", "lutris", "bottles", "minigalaxy", "itch"):
            self.assertFalse(pol.check("appimage", name, *args)[0], name)
        for name in ("steam", "lutris", "minigalaxy"):
            self.assertTrue(pol.check("rpm", name, *args)[0], name)                              # the dnf route stays open
        self.assertTrue(pol.check("flatpak", "org.videolan.VLC", *args)[0])                      # only stores are affected

    def test_the_build_inputs_carry_both_rules(self):
        filt = pol.flatpak_filter(pol.load(), pol.load_blocked(), pol.load_dnf_only())
        for line in ("deny com.heroicgameslauncher.hgl", "deny com.valvesoftware.Steam", "deny net.lutris.Lutris", "deny io.itch.itch"):
            self.assertIn(line + "\n", filt)
        exclude = pol.dnf_exclude(pol.load(), pol.load_blocked()).split(",")
        self.assertIn("heroic", exclude)
        self.assertNotIn("steam", exclude)                                                       # stores are excluded as Flatpaks, never as packages
        self.assertNotIn("lutris", exclude)


if __name__ == "__main__":
    unittest.main()
