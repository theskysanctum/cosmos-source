"""Tests for the Shop's game-download backend (cosmos_gamegrubber.py) and the module installer (cosmos_modules.py), with a fake bridge."""
import json
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

import cosmos_gamegrubber as gg  # noqa: E402
import cosmos_modules as cm  # noqa: E402
import xiso_testdata  # noqa: E402


class Env(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        self.saved = dict(os.environ)
        self.addCleanup(lambda: (os.environ.clear(), os.environ.update(self.saved)))
        os.environ.update({"COSMOS_GAMEGRUBBER_DIR": str(self.root / "state"), "COSMOS_MODULES_ROOT": str(self.root / "modules"),
                           "XDG_CONFIG_HOME": str(self.root / "config"), "COSMOS_GAMEBOX_DIR": str(self.root / "gamebox")})
        (self.root / "gamebox" / "library").mkdir(parents=True)
        self.roms = self.root / "roms"
        (self.root / "gamebox" / "library" / "systems.json").write_text(json.dumps({"categories": [{"manufacturers": [{"platforms": [
            {"id": "xbox", "roms_dir": str(self.roms / "xbox"), "xemu": True}, {"id": "ps2", "roms_dir": str(self.roms / "ps2")}]}]}]}))

    def install_module(self, bridge="pass"):
        d = self.root / "modules" / "gamegrubber"
        d.mkdir(parents=True, exist_ok=True)
        (d / "module.json").write_text(json.dumps({"name": "gamegrubber", "label": "GameGrubber"}))
        (d / "cosmos_bridge.py").write_text(bridge)

    def games(self, platform, rows):
        d = gg.config_dir() / "games"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{platform}.json").write_text(json.dumps(rows))


class Switch(Env):
    def test_off_until_installed_and_switched_on_and_switching_off_removes_the_marker(self):
        self.assertFalse(gg.visible())
        self.install_module()
        self.assertFalse(gg.visible())                 # installed but not switched on
        gg.set_enabled(True)
        self.assertTrue(gg.visible())
        gg.set_enabled(False)
        self.assertFalse(gg.flag_path().exists())      # the disclaimer shows again next time
        self.assertFalse(gg.visible())


class Lists(Env):
    def test_platforms_come_from_the_bridge_once_then_from_the_cache_and_bios_is_left_out(self):
        self.install_module()
        calls = []

        class Done:
            returncode = 0
            stdout = json.dumps([{"id": "PS2 (Archive)", "name": "PlayStation 2", "folder": "ps2"}, {"id": gg.BIOS_ID, "name": gg.BIOS_ID, "folder": "bios"},
                                 {"id": "Atari", "name": "atari 2600", "folder": "a"}])

        def run(cmd, **kw):
            calls.append(cmd)
            return Done()
        first = gg.platforms(run)
        self.assertEqual([p["name"] for p in first], ["atari 2600", "PlayStation 2"])          # sorted, no BIOS
        gg.platforms(run)
        self.assertEqual(len(calls), 1)                                                         # the second call read the cache

    def test_the_remembered_list_expires_when_the_module_is_updated(self):
        self.install_module()
        state = {"rows": [{"id": "old", "name": "Old", "folder": "o"}]}

        class Done:
            returncode = 0

            @property
            def stdout(self):
                return json.dumps(state["rows"])

        def run(cmd, **kw):
            return Done()
        ids = lambda: [p["id"] for p in gg.platforms(run)]                         # noqa: E731
        self.assertEqual(ids(), ["old"])
        state["rows"] = [{"id": "new", "name": "New", "folder": "n"}, {"id": "newer", "name": "Newer", "folder": "m"}]
        self.assertEqual(ids(), ["old"])                                           # remembered: the bridge is not asked again
        os.utime(gg.bridge_path(), (2_000_000_000, 2_000_000_000))                # the module was updated after the list was remembered
        self.assertEqual(ids(), ["new", "newer"])

    def test_a_systems_games_come_from_the_bridge_in_its_own_order(self):
        self.install_module()
        bridge_rows = [{"name": "Crash.iso", "display_name": "Crash Bandicoot", "url": "u1", "size": "4.0G"},
                       {"name": "Tekken.7z", "display_name": "Tekken", "url": "u2", "size": "300M"}]

        class Done:
            returncode = 0
            stdout = json.dumps(bridge_rows)

        seen = []

        def run(cmd, **kw):
            seen.append(cmd[2:])
            return Done()
        games = gg.list_games("PS2 (Archive)", run=run)
        self.assertEqual(seen, [["games", "PS2 (Archive)"]])
        self.assertEqual([g["display_name"] for g in games], ["Crash Bandicoot", "Tekken"])          # exactly the bridge's order

    def test_without_the_bridge_the_cached_list_is_used_sorted_by_title(self):
        self.install_module()
        self.games("PS2 (Archive)", [["Tekken.7z", "http://x/2", "300M"], ["crash (USA).iso", "http://x/1", "4.0G"], ["bad"], "junk"])

        class Failed:
            returncode = 1
            stdout = ""
        games = gg.list_games("PS2 (Archive)", run=lambda *a, **k: Failed())
        self.assertEqual([g["display_name"] for g in games], ["crash (USA)", "Tekken"])

    def test_search_needs_every_word_in_the_title_and_keeps_the_order(self):
        games = [{"name": "a", "display_name": "Crash Bandicoot"}, {"name": "b", "display_name": "Crash Team Racing"}, {"name": "c", "display_name": "Tekken"}]
        self.assertEqual([g["name"] for g in gg.search_games(games, "crash")], ["a", "b"])
        self.assertEqual([g["name"] for g in gg.search_games(games, "racing crash")], ["b"])
        self.assertEqual(len(gg.search_games(games, "")), 3)
        self.assertEqual(gg.search_games(games, "zzz"), [])

    def test_the_row_label_is_the_title_and_the_size(self):
        self.assertEqual(gg.game_label({"name": "x.iso", "display_name": "X", "size": "4.0G"}), "X  (4.0G)")
        self.assertEqual(gg.game_label({"name": "x.iso", "display_name": "", "size": ""}), "x.iso")

    def test_systems_can_be_filtered_by_name_or_folder(self):
        plats = [{"id": "a", "name": "PlayStation 2 (Archive)", "folder": "ps2"}, {"id": "b", "name": "Sega Dreamcast", "folder": "dreamcast"}]
        self.assertEqual([p["id"] for p in gg.search_systems(plats, "ps2")], ["a"])
        self.assertEqual([p["id"] for p in gg.search_systems(plats, "sega dream")], ["b"])
        self.assertEqual(len(gg.search_systems(plats, "")), 2)

    def test_a_missing_or_broken_list_is_just_empty(self):
        self.install_module()
        self.assertEqual(gg.read_games("nope"), [])
        d = gg.config_dir() / "games"
        d.mkdir(parents=True)
        (d / "broken.json").write_text("{not json")
        self.assertEqual(gg.read_games("broken"), [])

    def test_every_folder_belongs_to_exactly_one_manufacturer(self):
        seen = {}
        for maker, folders in gg.MAKER_FOLDERS.items():
            for folder in folders:
                self.assertNotIn(folder, seen, f"{folder} is under both {seen.get(folder)} and {maker}")
                seen[folder] = maker

    def test_systems_are_grouped_by_manufacturer_alphabetically_with_the_oddballs_last(self):
        plats = [{"id": "1", "name": "PlayStation 2 (Vimms)", "folder": "ps2"}, {"id": "2", "name": "Game Boy (Archive)", "folder": "gb"},
                 {"id": "3", "name": "NES (Archive)", "folder": "nes"}, {"id": "4", "name": "Windows (Torrent)", "folder": "windows"},
                 {"id": "5", "name": "Brand New Console (Archive)", "folder": "brandnew"}, {"id": "6", "name": "PlayStation (Archive)", "folder": "psx"}]
        groups = gg.makers(plats)
        self.assertEqual([m for m, _s in groups], ["Nintendo", "Sony", "PC & Arcade", "Other"])
        self.assertEqual([p["name"] for p in dict(groups)["Nintendo"]], ["Game Boy (Archive)", "NES (Archive)"])      # alphabetical inside
        self.assertEqual([p["name"] for p in dict(groups)["Sony"]], ["PlayStation (Archive)", "PlayStation 2 (Vimms)"])
        self.assertEqual(gg.maker_of({"folder": "MSX2+ (Archive)", "name": "x"}), "MSX")

    def test_manufacturers_can_be_searched_by_their_name_or_a_systems_name(self):
        groups = gg.makers([{"id": "1", "name": "Wii U (Torrent)", "folder": "wiiu"}, {"id": "2", "name": "PlayStation 3 (Archive)", "folder": "ps3"}])
        self.assertEqual([m for m, _s in gg.search_makers(groups, "nintendo")], ["Nintendo"])
        self.assertEqual([m for m, _s in gg.search_makers(groups, "playstation 3")], ["Sony"])
        self.assertEqual(len(gg.search_makers(groups, "")), 2)
        self.assertEqual(gg.search_makers(groups, "zzz"), [])

    def test_bios_is_one_system_of_its_own(self):
        self.assertEqual((gg.bios_platform()["id"], gg.bios_platform()["name"]), (gg.BIOS_ID, "BIOS"))


class Commands(Env):
    def test_the_download_command_quotes_everything(self):
        cmd = gg.download_command(gg.item_id("A (b)", "it's; rm -rf ~ $(x).iso"), python="python3")
        import shlex
        parts = shlex.split(cmd)
        self.assertEqual(parts[0], "python3")
        self.assertEqual(parts[2:], ["download", "A (b)", "it's; rm -rf ~ $(x).iso"])          # one argument each: nothing reaches a shell

    def test_progress_text(self):
        self.assertEqual(gg.progress_text({"downloaded": 5 * 1048576, "total": 10 * 1048576, "speed": 2.5}), "Downloading: 50% (5.0/10.0 MB) @ 2.5 MB/s")
        self.assertEqual(gg.progress_text({"downloaded": 1048576}), "Downloading: 1.0 MB")


class FakeProc:
    def __init__(self, lines, code=0):
        self.stdout = iter(lines)
        self.code = code

    def wait(self):
        return self.code


def messages(*items):
    return [json.dumps(i) + "\n" for i in items] + ["a stray warning\n"]


class Download(Env):
    def run_download(self, platform, game, lines, before=None):
        self.install_module()
        out = []
        if before:
            before()
        code = gg.download(platform, game, out=out.append, popen=lambda *a, **k: FakeProc(lines))
        return code, out

    def test_a_download_reports_progress_and_the_final_message(self):
        code, out = self.run_download("ps2", "Game.iso", messages({"type": "progress", "downloaded": 1048576, "total": 4194304},
                                                                 {"type": "done", "success": True, "message": "Downloaded Game.iso"}))
        self.assertEqual(code, 0)
        self.assertIn("Downloading: 25% (1.0/4.0 MB)", out[0])
        self.assertEqual(out[-1], "Downloaded Game.iso")

    def test_a_failed_download_says_why_and_fails(self):
        code, out = self.run_download("ps2", "Game.iso", messages({"type": "done", "success": False, "message": "No such file"}))
        self.assertEqual((code, out), (1, ["No such file"]))
        self.assertEqual(self.run_download("ps2", "G.iso", ["not json\n"])[0], 1)               # no done message at all is a failure too

    def test_without_the_module_it_says_how_to_get_it(self):
        out = []
        self.assertEqual(gg.download("ps2", "G.iso", out=out.append), 1)
        self.assertIn("Game Downloads pack", out[0])

    def test_a_full_xbox_dump_is_converted_to_a_plain_xiso_before_done(self):
        import xiso
        if not xiso.find_tool():
            self.skipTest("extract-xiso isn't installed")
        (self.roms / "xbox").mkdir(parents=True)

        def make_dump():
            xiso_testdata.write(self.roms / "xbox" / "Halo.iso", {"default.xbe": b"XBEH" + bytes(range(256)) * 300}, 0x18300000)
        code, out = self.run_download("xbox", "Halo.iso", messages({"type": "done", "success": True, "message": "Downloaded Halo.iso"}), before=make_dump)
        self.assertEqual(code, 0, out)
        self.assertTrue(any("Converting to XISO" in line for line in out))
        self.assertEqual(out[-1], "Downloaded Halo.iso")                                       # done is reported last, after the conversion
        self.assertEqual(xiso.volume_offset(self.roms / "xbox" / "Halo.iso"), 0)

    def test_other_platforms_are_never_converted(self):
        (self.roms / "ps2").mkdir(parents=True)
        (self.roms / "ps2" / "G.iso").write_bytes(b"x" * 10)
        code, out = self.run_download("ps2", "G.iso", messages({"type": "done", "success": True, "message": "ok"}))
        self.assertEqual((code, out), (0, ["ok"]))


class Roots(Env):
    def test_the_gamebox_files_are_found_on_the_image_on_a_plain_fedora_and_in_a_checkout(self):
        del os.environ["COSMOS_GAMEBOX_DIR"]
        found = gg.gamebox_dir()
        self.assertTrue((found / "library" / "systems.json").is_file(), found)             # this checkout's gamebox/ folder
        lite = gg.HERE / "gamebox-lite"
        self.assertNotEqual(found, lite)                                                       # (a checkout has no gamebox-lite)
        os.environ["COSMOS_GAMEBOX_DIR"] = "/elsewhere"
        self.assertEqual(str(gg.gamebox_dir()), "/elsewhere")                                  # an override always wins

    def test_a_crash_of_the_downloader_is_shown_not_swallowed(self):
        self.install_module()
        out = []
        lines = ["Traceback (most recent call last):\n", "ModuleNotFoundError: No module named 'realdebrid'\n"]
        code = gg.download("ps2", "G.iso", out=out.append, popen=lambda *a, **k: FakeProc(lines, 1))
        self.assertEqual(code, 1)
        self.assertEqual(out[0], "The download failed.")
        self.assertIn("  ModuleNotFoundError: No module named 'realdebrid'", out)


class Modules(Env):
    def test_names_are_validated_and_installed_modules_are_listed(self):
        self.assertFalse(cm.install("../evil", log=lambda s: None))
        self.assertFalse(cm.install("", log=lambda s: None))
        self.assertFalse(cm.uninstall("gamegrubber", log=lambda s: None))                      # not installed: refused, nothing run
        self.install_module()
        self.assertEqual(list(cm.list_installed()), ["gamegrubber"])
        self.assertTrue(cm.is_installed("gamegrubber"))

    def test_install_runs_the_clone_pull_and_sparse_add_in_order(self):
        seen = []
        (self.root / "modules" / ".git").mkdir(parents=True)                                    # already cloned

        def run(cmd, log=print):
            seen.append(cmd[3:] if cmd[0] == "git" else cmd)
            if cmd[-2:] == ["sparse-checkout", "add"] or cmd[-3:-1] == ["sparse-checkout", "add"]:
                self.install_module()
            return True
        self.assertTrue(cm.install("gamegrubber", log=lambda s: None, run=run))
        self.assertEqual(seen, [["pull"], ["sparse-checkout", "add", "gamegrubber"]])


class ShopIntegration(Env):
    """How the Shop uses this (skipped where GTK can't be imported)."""

    def setUp(self):
        super().setUp()
        try:
            import importlib.machinery
            import importlib.util
            loader = importlib.machinery.SourceFileLoader("cosmos_shop_gg_test", str(HERE / "cosmos-shop.py"))
            spec = importlib.util.spec_from_loader("cosmos_shop_gg_test", loader)
            self.shop = importlib.util.module_from_spec(spec)
            loader.exec_module(self.shop)
        except Exception as exc:
            self.skipTest(f"cosmos-shop can't be imported here: {exc}")

    def test_games_and_bios_appear_only_when_installed_and_switched_on(self):
        self.assertEqual(self.shop.visible_sources(), ["packs", "dnf", "appimage", "flatpak"])
        self.install_module()
        self.assertEqual(self.shop.visible_sources(), ["packs", "dnf", "appimage", "flatpak"])           # installed, but switched off
        gg.set_enabled(True)
        self.assertEqual(self.shop.visible_sources()[-2:], ["games", "bios"])

    def test_downloads_are_not_blocked_by_the_packaging_policy_and_say_download(self):
        self.assertEqual(self.shop.install_refusal("games", gg.item_id("Any (Archive)", "Game.iso")), "")
        self.assertEqual(self.shop.SOURCES["games"]["verb"], "Download")
        self.assertIn("download", self.shop.SOURCES["games"]["install_cmd"](gg.item_id("A", "B.iso")))

    def test_games_and_bios_are_list_pages_not_tiles(self):
        self.assertEqual(self.shop.LIST_SOURCES, ("games", "bios"))
        self.assertTrue(hasattr(self.shop, "GameListPage"))
        self.assertIsNone(self.shop.SOURCES["games"]["loader"])

    def test_search_takes_every_word_in_any_order_across_name_and_description(self):
        m = self.shop.matches_query
        self.assertTrue(m("ps2 crash", "Crash Bandicoot", "PlayStation 2 (ps2) - 4G"))
        self.assertTrue(m("", "anything"))
        self.assertFalse(m("crash dreamcast", "Crash Bandicoot", "PlayStation 2 (ps2)"))


if __name__ == "__main__":
    unittest.main()
