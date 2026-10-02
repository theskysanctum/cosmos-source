import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cosmos_proton as cp
import cosmos_protondb as pdb
import cosmos_winpack as wp

HAVE_SQUASH = shutil.which("mksquashfs") and shutil.which("unsquashfs")


def game(root, exes=("Game.exe",), extra=()):
    os.makedirs(os.path.join(root, "bin"), exist_ok=True)
    for e in exes:
        path = os.path.join(root, e)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        open(path, "wb").write(b"MZ" + b"\0" * 5000)
    for name, size in extra:
        path = os.path.join(root, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        open(path, "wb").write(b"x" * size)
    return root


class Detect(unittest.TestCase):
    def test_the_game_exe_comes_first_and_installers_are_left_out(self):
        d = tempfile.mkdtemp()
        game(os.path.join(d, "Cool Game"), ("bin/CoolGame.exe", "unins000.exe", "vcredist_x64.exe", "UnityCrashHandler64.exe", "_CommonRedist/x.exe",
                                            "tools/helper.exe"), [("bin/data.pak", 500000)])
        got = wp.detect_executables(os.path.join(d, "Cool Game"), "Cool Game")
        self.assertEqual(got[0], os.path.join("bin", "CoolGame.exe"))
        for skipped in ("unins000.exe", "vcredist_x64.exe", "UnityCrashHandler64.exe"):
            self.assertFalse(any(skipped in g for g in got), skipped)
        self.assertFalse(any("_CommonRedist" in g for g in got))


class Settings(unittest.TestCase):
    def test_a_good_manifest(self):
        m = wp.make_manifest("Cool Game!", "bin/CoolGame.exe", 12345, ["-dx11"], "auto", {"PROTON_NO_ESYNC": "1"}, ["gamemoderun"])
        self.assertEqual((m["id"], m["steam_appid"], m["env"], m["exe"]), ("cool-game", 12345, {"PROTON_NO_ESYNC": "1"}, "bin/CoolGame.exe"))

    def test_bad_input_is_refused_with_a_sentence(self):
        bad = [dict(exe="../x.exe"), dict(exe="/abs.exe"), dict(exe="a/../../b.exe"), dict(exe="readme.txt"), dict(exe="a.exe", env={"WINELOADER": "x"}),
               dict(exe="a.exe", env={"PROTON_X": "/etc/passwd"}), dict(exe="a.exe", env={"LD_PRELOAD": "x"}), dict(exe="a.exe", wrappers=["rm"]),
               dict(exe="a.exe", args=["; id"]), dict(exe="a.exe", appid="12"), dict(exe="a.exe", appid=-1)]
        for kw in bad:
            with self.assertRaises(wp.PackError, msg=str(kw)):
                wp.make_manifest("G", **kw)
        with self.assertRaises(wp.PackError):
            wp.make_manifest("   ", "a.exe")

    def test_protondb_suggestions_become_settings(self):
        rows = [{"rating": "Gold", "notes": "PROTON_NO_ESYNC=1 mangohud %command% -dx11", "protonVersion": "GE-Proton8-25", "timestamp": 1}] * 3
        installed = [cp.Build("GE-Proton8-25", "/x", "cosmos")]
        env, wrappers, args, proton = wp.from_protondb(pdb.analyze(rows, installed), installed)
        self.assertEqual((env, wrappers, args, proton), ({"PROTON_NO_ESYNC": "1"}, ["mangohud"], ["-dx11"], "GE-Proton8-25"))
        self.assertEqual(wp.from_protondb(None, [])[:3], ({}, [], []))


@unittest.skipUnless(HAVE_SQUASH, "squashfs-tools isn't installed")
class RealSquashfs(unittest.TestCase):
    def test_create_list_read_back_and_delete(self):
        d = tempfile.mkdtemp()
        src = game(os.path.join(d, "g"), ("bin/Game.exe",), [("data/big.bin", 400000), ("data/small.txt", 10)])
        os.symlink("data/small.txt", os.path.join(src, "link"))
        m = wp.make_manifest("Test Game", "bin/Game.exe", 4242, [], "auto", {"PROTON_NO_ESYNC": "1"})
        seen = []
        out = os.path.join(d, "packs")
        r = wp.create(src, m, out, progress=seen.append)
        self.assertTrue(os.path.getsize(r["image"]) > 0)
        self.assertEqual(seen[-1], 100)
        self.assertLess(r["bytes_out"], r["bytes_in"])                                  # it really compressed
        # the settings are inside the image too, not only beside it
        os.unlink(r["settings"])
        self.assertEqual(wp.read_manifest(r["image"])["steam_appid"], 4242)
        listing = subprocess.run(["unsquashfs", "-l", r["image"]], capture_output=True, text=True).stdout
        for needle in ("bin/Game.exe", "data/big.bin", ".winpack.json", "link"):
            self.assertIn(needle, listing)
        with open(r["settings"], "w") as fh:
            json.dump(m, fh)
        packs = wp.list_packs(out)
        self.assertEqual([p["id"] for p in packs], ["test-game"])
        with self.assertRaises(wp.PackError):
            wp.create(src, m, out)                                                       # already exists
        wp.create(src, m, out, replace=True, comp="xz")
        wp.delete_pack("test-game", out)
        self.assertEqual(wp.list_packs(out), [])

    def test_refusals(self):
        d = tempfile.mkdtemp()
        src = game(os.path.join(d, "g"), ("Game.exe",))
        m = wp.make_manifest("G", "Game.exe")
        with self.assertRaises(wp.PackError):
            wp.create(os.path.join(d, "missing"), m, d)
        with self.assertRaises(wp.PackError):
            wp.create(src, wp.make_manifest("G", "Other.exe"), d)
        with self.assertRaises(wp.PackError):
            wp.create(src, m, d, comp="rot13")
        with self.assertRaises(wp.PackError):
            wp.create(src, m, d, comp="zstd", level=99)
        with self.assertRaises(wp.PackError):
            wp.create(src, m, d, block="3K")
        with self.assertRaises(wp.PackError):
            wp.create(src, m, d, which=lambda n: None)
        self.assertFalse([n for n in os.listdir(d) if n.endswith((".sqfs", ".partial"))])       # nothing half-made is left

    def test_a_failed_squash_leaves_no_image(self):
        d = tempfile.mkdtemp()
        src = game(os.path.join(d, "g"), ("Game.exe",))

        class Boom:
            def __init__(self, *a, **k):
                import io
                self.stdout = io.StringIO("Fatal error: no space left\n")

            def wait(self):
                return 1
        with self.assertRaises(wp.PackError) as ctx:
            wp.create(src, wp.make_manifest("G", "Game.exe"), os.path.join(d, "o"), popen=Boom)
        self.assertIn("no space left", str(ctx.exception))
        self.assertEqual(os.listdir(os.path.join(d, "o")), [])

    def test_not_a_pack(self):
        d = tempfile.mkdtemp()
        p = os.path.join(d, "x.sqfs")
        subprocess.run(["mksquashfs", d, p, "-quiet", "-no-progress"], capture_output=True)
        with self.assertRaises(wp.PackError):
            wp.read_manifest(p)


class Prepare(unittest.TestCase):
    def fetch(self, url, timeout=20):
        if "storesearch" in url:
            return json.dumps({"items": [{"type": "app", "id": 111, "name": "Cool Game"}, {"type": "app", "id": 222, "name": "Cool Game 2"}]}).encode()
        if "summaries/111" in url:
            return json.dumps({"tier": "gold", "bestReportedTier": "platinum", "score": 0.8, "confidence": "good", "total": 50}).encode()
        if "max-p" in url:
            return json.dumps([{"rating": "Gold", "notes": "PROTON_NO_ESYNC=1 %command%", "protonVersion": "GE-Proton8-25", "timestamp": 5}] * 3).encode()
        if "gamefixes-steam/111" in url:
            return b'"""Fix for Cool Game"""\n'
        if "gamefixes-steam" in url:
            import urllib.error
            raise urllib.error.HTTPError(url, 404, "nf", {}, None)
        raise OSError("unexpected " + url)

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.cache = tempfile.mkdtemp()
        self.src = game(os.path.join(self.d, "Cool Game"), ("bin/CoolGame.exe",))
        self.builds = [cp.Build("GE-Proton8-25", "/x", "cosmos")]

    def test_the_whole_thing_is_worked_out_from_the_name(self):
        p = wp.prepare(self.src, builds=self.builds, fetch=self.fetch, cache=self.cache)
        self.assertEqual((p["appid"], p["appid_source"]), (111, "the one Steam store match for that name"))
        self.assertEqual((p["env"], p["proton"]), ({"PROTON_NO_ESYNC": "1"}, "GE-Proton8-25"))
        self.assertEqual(p["info"]["summary"]["tier"], "gold")
        self.assertTrue(p["info"]["fix"])
        self.assertEqual(p["executables"][0], os.path.join("bin", "CoolGame.exe"))
        m = wp.settings_for(p, p["executables"][0])
        self.assertEqual((m["steam_appid"], m["env"]), (111, {"PROTON_NO_ESYNC": "1"}))
        self.assertEqual((m["protondb"]["tier"], m["protondb"]["favoured"], m["protondb"]["fix"]), ("gold", "GE-Proton8-25", True))

    def test_steam_appid_txt_wins_and_ambiguity_is_never_guessed(self):
        open(os.path.join(self.src, "steam_appid.txt"), "w").write("111")
        self.assertEqual(wp.prepare(self.src, builds=self.builds, fetch=self.fetch, cache=self.cache)["appid_source"], "steam_appid.txt in the game folder")
        other = game(os.path.join(self.d, "Cool"), ("Cool.exe",))
        p = wp.prepare(other, builds=self.builds, fetch=self.fetch, cache=self.cache)
        self.assertIsNone(p["appid"])                                                         # 'Cool' matches neither exactly
        self.assertEqual(len(p["candidates"]), 2)
        self.assertIsNone(p["info"])

    def test_a_game_steam_does_not_know_is_matched_in_the_umu_fix_database(self):
        def fetch(url, timeout=20):
            if "storesearch" in url:
                return json.dumps({"items": []}).encode()
            if "umu-database" in url:
                return b"TITLE,STORE,CODENAME,UMU_ID,COMMON ACRONYM (Optional),NOTE (Optional),EXE_STRINGS (Optional)\nOdd Game,gog,123,umu-777,,,\nOdd Game,egs,abc,umu-777,,,\n"
            return self.fetch(url, timeout)
        other = game(os.path.join(self.d, "Odd Game"), ("odd.exe",))
        p = wp.prepare(other, builds=self.builds, fetch=fetch, cache=tempfile.mkdtemp())
        self.assertEqual((p["appid"], p["appid_source"]), (777, "the umu fix database (matched by title)"))

    def test_the_person_can_switch_suggestions_off_and_add_their_own(self):
        p = wp.prepare(self.src, builds=self.builds, fetch=self.fetch, cache=self.cache)
        m = wp.settings_for(p, "bin/CoolGame.exe", skip={"PROTON_NO_ESYNC=1"}, extra_env={"DXVK_HUD": "fps"}, extra_args=["-windowed"])
        self.assertEqual((m["env"], m["args"]), ({"DXVK_HUD": "fps"}, ["-windowed"]))

    def test_offline_mode_and_a_dead_network_still_give_a_usable_result(self):
        p = wp.prepare(self.src, builds=self.builds, offline=True, cache=self.cache)
        self.assertIsNone(p["appid"])
        self.assertTrue(p["warnings"])
        self.assertTrue(p["executables"])
        p = wp.prepare(self.src, appid=111, builds=self.builds, offline=True, cache=tempfile.mkdtemp())
        self.assertEqual(p["appid"], 111)
        self.assertTrue(p["warnings"])


BUILD_GE = cp.Build("GE-Proton9-27", "/proton/ge", "cosmos")
BUILD_OLD = cp.Build("Proton 8.0-5", "/proton/old", "steam")


class Launch(unittest.TestCase):
    def manifest(self, **kw):
        base = wp.make_manifest("Test Game", "bin/Game.exe", 4242, ["-dx11"], "auto", {"PROTON_NO_ESYNC": "1"}, ["gamemoderun", "mangohud"])
        base.update(kw)
        return base

    def plan(self, m=None, build=BUILD_GE, tools=("gamemoderun", "fuse-overlayfs"), base_env=None):
        return wp.plan_launch(m or self.manifest(), build, "/packs", "/run/x", base_env or {"PATH": "/usr/bin"}, which=lambda n: "/usr/bin/" + n if n in tools else None)

    def test_a_proton_launch(self):
        p = self.plan()
        self.assertEqual(p["argv"], ["gamemoderun", "/proton/ge/proton", "waitforexitandrun", "/run/x/test-game/merged/bin/Game.exe", "-dx11"])
        e = p["env"]
        self.assertEqual((e["STEAM_COMPAT_DATA_PATH"], e["STEAM_COMPAT_INSTALL_PATH"]), ("/packs/data/test-game/prefix", "/run/x/test-game/merged"))
        self.assertEqual((e["SteamAppId"], e["STEAM_COMPAT_APP_ID"], e["GAMEID"], e["UMU_ID"]), ("4242", "4242", "umu-4242", "umu-4242"))
        self.assertEqual(e["PROTON_NO_ESYNC"], "1")
        self.assertEqual(p["cwd"], "/run/x/test-game/merged/bin")
        self.assertEqual(p["notes"], ["mangohud isn't installed, so it was skipped."])
        self.assertEqual((p["lower"], p["upper"]), ("/run/x/test-game/lower", "/packs/data/test-game/upper"))

    def test_umu_run_is_preferred_when_installed_and_sets_the_prefix_and_build(self):
        p = self.plan(tools=("umu-run", "fuse-overlayfs"))
        self.assertEqual(p["argv"][0], "umu-run")
        self.assertEqual((p["env"]["PROTONPATH"], p["env"]["WINEPREFIX"]), ("/proton/ge", "/packs/data/test-game/prefix/pfx"))

    def test_without_overlay_the_game_runs_from_the_read_only_mount(self):
        p = self.plan(tools=())
        self.assertEqual(p["merged"], p["lower"])
        self.assertFalse(p["overlay"])

    def test_no_steam_id_means_the_generic_game_id_and_nothing_left_over_from_another_launch(self):
        p = self.plan(self.manifest(steam_appid=None), base_env={"SteamAppId": "999", "STEAM_COMPAT_APP_ID": "999", "PATH": "/usr/bin", "WINEPREFIX": "/other"})
        self.assertNotIn("SteamAppId", p["env"])
        self.assertNotIn("WINEPREFIX", p["env"])
        self.assertEqual(p["env"]["GAMEID"], "umu-0")
        self.assertEqual(p["env"]["PATH"], "/usr/bin")

    def test_a_hand_edited_settings_file_cannot_smuggle_things_in(self):
        m = self.manifest(env={"WINELOADER": "/tmp/evil", "PROTON_OK": "1", "LD_PRELOAD": "/tmp/x"}, args=["-ok", "; rm -rf ~"], wrappers=["gamemoderun", "sh"])
        p = self.plan(m)
        self.assertNotIn("WINELOADER", p["env"])
        self.assertNotIn("LD_PRELOAD", p["env"])
        self.assertEqual(p["env"]["PROTON_OK"], "1")
        self.assertEqual(p["argv"][-1], "-ok")
        self.assertNotIn("sh", p["argv"])

    def test_choosing_the_build(self):
        builds = [BUILD_GE, BUILD_OLD]
        self.assertEqual(wp.choose_build({"proton": "Proton 8.0-5"}, builds)[0].name, "Proton 8.0-5")
        self.assertEqual(wp.choose_build({"proton": "auto"}, builds)[0].name, "GE-Proton9-27")
        self.assertEqual(wp.choose_build({"proton": "auto", "protondb": {"favoured": "Proton 8.0-5"}}, builds)[1], "what ProtonDB's reports favour")
        self.assertEqual(wp.choose_build({"proton": "auto"}, [BUILD_OLD])[0].name, "Proton 8.0-5")
        with self.assertRaises(wp.PackError):
            wp.choose_build({"proton": "GE-Proton1-1"}, builds)
        with self.assertRaises(wp.PackError):
            wp.choose_build({"proton": "auto"}, [])


class Mounting(unittest.TestCase):
    def test_mount_order_and_always_unmounts_even_when_the_game_fails(self):
        d = tempfile.mkdtemp()
        m = wp.make_manifest("G", "Game.exe")
        plan = wp.plan_launch(m, BUILD_GE, d, os.path.join(d, "rt"), {}, which=lambda n: "/usr/bin/" + n, has_overlay=True)
        calls = []

        class Done:
            returncode, stderr = 0, ""

        def run(argv, **kw):
            calls.append(argv[0] if not argv[0].endswith("fusermount3") else "unmount " + argv[-1].rsplit("/", 1)[-1])
            return Done()
        with self.assertRaises(RuntimeError):
            with wp.mounted("/x.sqfs", plan, run=run, which=lambda n: "/usr/bin/" + n, is_mounted=lambda p: False):
                raise RuntimeError("the game crashed")
        self.assertEqual(calls, ["squashfuse", "fuse-overlayfs", "unmount merged", "unmount lower"])
        for sub in ("upper", "work", "prefix"):
            self.assertTrue(os.path.isdir(os.path.join(d, "data", "g", sub)))

    def test_a_failed_mount_is_a_sentence(self):
        d = tempfile.mkdtemp()
        plan = wp.plan_launch(wp.make_manifest("G", "Game.exe"), BUILD_GE, d, os.path.join(d, "rt"), {}, which=lambda n: "/usr/bin/" + n, has_overlay=False)

        class Bad:
            returncode, stderr = 1, "fuse: device not found\n"
        with self.assertRaises(wp.PackError) as ctx:
            with wp.mounted("/x.sqfs", plan, run=lambda *a, **k: Bad(), which=lambda n: "/usr/bin/" + n, is_mounted=lambda p: False):
                pass
        self.assertIn("device not found", str(ctx.exception))
        with self.assertRaises(wp.PackError):
            with wp.mounted("/x.sqfs", plan, which=lambda n: None):
                pass

    def test_launch_runs_in_the_mount_and_returns_the_exit_code(self):
        d = tempfile.mkdtemp()
        image = os.path.join(d, "g.sqfs")
        m = {**wp.make_manifest("G", "Game.exe"), "image": image}
        rt = os.path.join(d, "rt")
        os.environ["XDG_RUNTIME_DIR"] = rt
        seen = {}

        class Done:
            returncode, stderr = 0, ""

        def run(argv, **kw):
            if argv[0] == "squashfuse":                                             # the "mount": put the game file where the plan says
                os.makedirs(argv[-1], exist_ok=True)
                open(os.path.join(argv[-1], "Game.exe"), "w").write("x")
            return Done()

        class Proc:
            def __init__(self, argv, cwd=None, env=None):
                seen["argv"], seen["cwd"] = argv, cwd

            def wait(self):
                return 7
        said = []
        code = wp.launch(m, [BUILD_GE], d, run=run, popen=Proc, which=lambda n: "/usr/bin/" + n if n in ("squashfuse", "fusermount3") else None, log=said.append)
        self.assertEqual(code, 7)
        self.assertEqual(seen["argv"][:2], ["/proton/ge/proton", "waitforexitandrun"])
        self.assertTrue(any("GE-Proton9-27" in s for s in said))


if __name__ == "__main__":
    unittest.main()
