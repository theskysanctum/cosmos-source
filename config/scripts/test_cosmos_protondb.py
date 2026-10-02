import json
import os
import sys
import tempfile
import unittest
import urllib.error

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cosmos_protondb as pdb
import cosmos_proton as cp


class Options(unittest.TestCase):
    def test_extracts_env_wrappers_and_args_around_command(self):
        got = pdb.parse_launch_options("Use PROTON_USE_WINED3D=1 WINEDLLOVERRIDES=\"d3d11=n;dxgi=n\" gamemoderun %command% -dx11 -nojoy")
        self.assertEqual(got["env"], {"PROTON_USE_WINED3D": "1", "WINEDLLOVERRIDES": "d3d11=n;dxgi=n"})
        self.assertEqual((got["wrappers"], got["args"]), (["gamemoderun"], ["-dx11", "-nojoy"]))

    def test_the_plain_launch_option_from_a_real_report(self):
        got = pdb.parse_launch_options('Just use "WINEDLLOVERRIDES=winedbg.exe=d %command%"')
        self.assertEqual(got["env"], {"WINEDLLOVERRIDES": "winedbg.exe=d"})

    def test_nothing_dangerous_gets_through(self):
        hostile = ["WINELOADER=/tmp/evil %command%", "LD_PRELOAD=/tmp/evil.so %command%", "PATH=/tmp:$PATH %command%",
                   "WINEPREFIX=/etc %command%", "PROTON_LOG_DIR=/etc %command%", "DXVK_HUD=$(rm -rf ~) %command%",
                   "WINEDEBUG=`id` %command%", "MANGOHUD_CONFIGFILE=/tmp/x %command%", "PROTON_X=../../x %command%",
                   "%command% ; rm -rf ~", "%command% --config=`id`", "sudo %command%", "%command% -a$(id)"]
        for text in hostile:
            got = pdb.parse_launch_options(text)
            self.assertEqual(got["env"], {}, text)
            self.assertEqual(got["wrappers"], [], text)
            self.assertEqual(got["args"], [], text)

    def test_only_two_wrappers_and_short_switches(self):
        got = pdb.parse_launch_options("gamescope -f -- mangohud gamemoderun %command% -windowed --skip=intro rm -rf")
        self.assertEqual(got["wrappers"], ["mangohud", "gamemoderun"])
        self.assertEqual(got["args"], ["-windowed", "--skip=intro"])


def report(rating, notes, version="9.0-4", ts=1700000000):
    return {"rating": rating, "notes": notes, "protonVersion": version, "timestamp": ts}


class Analysis(unittest.TestCase):
    def test_auto_when_three_good_reports_agree_and_only_one_value_per_setting(self):
        rows = [report("Platinum", "PROTON_NO_ESYNC=1 %command%")] * 3 + [report("Gold", "PROTON_NO_ESYNC=0")] * 2 \
            + [report("Gold", "-dx11 %command%")] + [report("Borked", "does not start; PROTON_FOO_BAR=1 %command%")] * 2 + [report("Gold", "nothing useful")]
        a = pdb.analyze(rows)
        by = {s["text"]: s for s in a["suggestions"]}
        self.assertTrue(by["PROTON_NO_ESYNC=1"]["auto"])
        self.assertFalse(by["PROTON_NO_ESYNC=0"]["auto"])                    # the other value is only offered
        self.assertFalse(by["PROTON_FOO_BAR=1"]["auto"])                     # reports that failed don't switch anything on
        self.assertNotIn("-dx11", [s["text"] for s in a["suggestions"] if s["auto"]])
        self.assertEqual(a["reports"], len(rows))
        self.assertEqual(a["newest"], 1700000000)
        self.assertTrue(a["problems"])

    def test_versions_are_ranked_and_matched_to_what_is_installed(self):
        rows = [report("Gold", "", "GE-Proton8-25")] * 3 + [report("Borked", "", "9.0-4")] * 2 + [report("Platinum", "", "9.0-4")]
        installed = [cp.Build("GE-Proton8-25", "/x", "cosmos"), cp.Build("Proton 9.0-4", "/y", "steam")]
        a = pdb.analyze(rows, installed)
        self.assertEqual(a["versions"][0]["version"], "ge-proton8-25")
        self.assertEqual(a["versions"][0]["installed"], "GE-Proton8-25")
        self.assertEqual(pdb.choose_version(a, installed).name, "GE-Proton8-25")
        self.assertIsNone(pdb.choose_version(a, [cp.Build("GE-Proton9-1", "/z", "cosmos")]))

    def test_empty_and_odd_reports_do_not_crash(self):
        self.assertEqual(pdb.analyze([])["suggestions"], [])
        a = pdb.analyze([{"rating": None, "notes": None}, {"notes": 5}, {}])
        self.assertEqual(a["reports"], 3)


class Fetching(unittest.TestCase):
    def setUp(self):
        self.cache = tempfile.mkdtemp()
        self.calls = []

    def fetch(self, url, timeout=20):
        self.calls.append(url)
        if "summaries" in url:
            return json.dumps({"tier": "gold", "bestReportedTier": "platinum", "score": 0.79, "confidence": "strong", "total": 2100,
                               "trendingTier": "platinum"}).encode()
        if "max-p" in url:
            return json.dumps([report("Gold", "PROTON_NO_ESYNC=1 %command%")] * 3).encode()
        if "gamefixes-steam/1245620" in url:
            return b'"""Game fix for Elden Ring: DLC file"""\nimport os\n'
        if "gamefixes-steam" in url:
            raise urllib.error.HTTPError(url, 404, "nf", {}, None)
        if "storesearch" in url:
            return json.dumps({"items": [{"type": "app", "id": 1245620, "name": "ELDEN RING"}, {"type": "bundle", "id": 5, "name": "B"}]}).encode()
        if "umu-database" in url:
            return b"TITLE,STORE,CODENAME,UMU_ID,COMMON ACRONYM (Optional),NOTE (Optional),EXE_STRINGS (Optional)\nGrand Theft Auto V,egs,abc,umu-271590,gtav,,\n"
        raise OSError("unexpected " + url)

    def test_info_combines_summary_fix_and_reports(self):
        i = pdb.info(1245620, fetch=self.fetch, cache=self.cache)
        self.assertEqual(i["summary"]["tier"], "gold")
        self.assertIn("Elden Ring", i["fix"]["doc"])
        self.assertTrue(i["analysis"]["suggestions"][0]["auto"])
        self.assertEqual(i["warnings"], [])

    def test_no_fix_is_none_not_an_error(self):
        self.assertIsNone(pdb.protonfix(1, fetch=self.fetch, cache=self.cache))

    def test_the_cache_is_used_and_then_survives_being_offline(self):
        pdb.summary(1245620, fetch=self.fetch, cache=self.cache)
        pdb.summary(1245620, fetch=self.fetch, cache=self.cache)
        self.assertEqual(len([c for c in self.calls if "summaries" in c]), 1)
        offline = lambda url, timeout=20: (_ for _ in ()).throw(OSError("no network"))
        stale = pdb.summary(1245620, fetch=offline, cache=self.cache, now=lambda: 10 ** 12)          # long past the day
        self.assertEqual(stale["tier"], "gold")
        self.assertIn("offline", stale["note"])
        i = pdb.info(999, fetch=offline, cache=self.cache)
        self.assertTrue(i["warnings"])                                                                 # never cached: reported, not raised

    def test_search_umu_and_appid_from_folder(self):
        self.assertEqual(pdb.search_steam("Elden Ring", fetch=self.fetch, cache=self.cache), [{"appid": 1245620, "name": "ELDEN RING"}])
        self.assertEqual(pdb.search_steam("  ", fetch=self.fetch, cache=self.cache), [])
        self.assertEqual(pdb.umu_lookup("grand theft auto v", fetch=self.fetch, cache=self.cache)[0]["umu_id"], "umu-271590")
        d = tempfile.mkdtemp()
        os.makedirs(os.path.join(d, "bin"))
        open(os.path.join(d, "bin", "steam_appid.txt"), "w").write("1245620\n")
        self.assertEqual(pdb.appid_from_folder(d), 1245620)
        open(os.path.join(d, "bin", "steam_appid.txt"), "w").write("not a number")
        self.assertIsNone(pdb.appid_from_folder(d))


if __name__ == "__main__":
    unittest.main()
