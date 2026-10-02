"""Tests for Expansion Packs (cosmos_packs.py) and the real pack definitions."""
import json
import os
import re
import sys
import tempfile
import unittest

os.environ.setdefault("COSMOS_IMAGE_BASED", "1")          # the pack commands below are the image's (kronos + rpm-ostree); host mode is tested separately
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import cosmos_app_policy as pol
import cosmos_packs as cp

PACKS = [
    {"id": "a", "name": "Pack A", "flatpak": ["org.x.One", "org.x.Two"], "rpm": ["tool1"]},
    {"id": "b", "name": "Pack B", "rpm": ["tool2", "tool1"], "includes": ["a"]},
    {"id": "loop1", "name": "L1", "includes": ["loop2"], "flatpak": ["org.l.One"]},
    {"id": "loop2", "name": "L2", "includes": ["loop1"], "flatpak": ["org.l.Two"]},
    {"id": "bad", "name": "Bad", "rpm": ["xterm"], "flatpak": ["org.gnome.Terminal"]},
]


class BakeTests(unittest.TestCase):
    def test_bake_installs_system_flatpaks_and_dnf_for_the_chosen_packs_and_what_they_include(self):
        cmd = cp.bake_command(PACKS, "b", apps=[], blocked=[])
        self.assertIn("flatpak install --system -y --noninteractive flathub org.x.One org.x.Two", cmd)
        self.assertIn("dnf install -y --setopt=keepcache=1 tool2 tool1", cmd)
        self.assertIn("find-terminals --purge-flatpaks", cmd)

    def test_nothing_chosen_is_a_no_op_and_ids_may_be_comma_or_space_separated(self):
        self.assertEqual(cp.bake_command(PACKS, ""), "true")
        self.assertEqual(cp.bake_command(PACKS, None), "true")
        self.assertEqual(cp.bake_command(PACKS, "a,b", apps=[], blocked=[]), cp.bake_command(PACKS, "a b", apps=[], blocked=[]))

    def test_a_typo_or_a_hostile_id_fails_the_build_instead_of_baking_nothing(self):
        for bad in ("nosuchpack", "a; rm -rf /", "../x", "A"):
            with self.assertRaises(ValueError, msg=bad):
                cp.bake_command(PACKS, bad, apps=[], blocked=[])

    def test_a_pack_with_a_refused_member_cannot_be_baked_in(self):
        with self.assertRaises(ValueError):
            cp.bake_command(PACKS, "bad")


class ModulePackTests(unittest.TestCase):
    """A pack that installs an optional module (code from the modules repository) instead of Flatpaks and packages."""
    PACKS = [{"id": "mod", "name": "A Module", "description": "d", "module": "gamegrubber"}]

    def setUp(self):
        import shutil
        import tempfile
        self.root = tempfile.mkdtemp(dir=os.path.expanduser("~/.placements") if os.path.isdir(os.path.expanduser("~/.placements")) else None)
        self.addCleanup(shutil.rmtree, self.root, True)
        saved = os.environ.get("COSMOS_MODULES_ROOT")
        self.addCleanup(lambda: os.environ.pop("COSMOS_MODULES_ROOT", None) if saved is None else os.environ.__setitem__("COSMOS_MODULES_ROOT", saved))
        os.environ["COSMOS_MODULES_ROOT"] = self.root

    def add_module(self):
        d = os.path.join(self.root, "gamegrubber")
        os.makedirs(d)
        with open(os.path.join(d, "module.json"), "w") as fh:
            fh.write('{"name": "gamegrubber"}')

    def test_state_follows_whether_the_module_is_installed(self):
        self.assertEqual(cp.status(self.PACKS, "mod")["state"], "none")
        self.add_module()
        st = cp.status(self.PACKS, "mod")
        self.assertEqual((st["state"], st["have"]), ("installed", ["gamegrubber"]))

    def test_install_runs_the_module_installer_and_remove_the_uninstaller(self):
        cmd = cp.install_command(self.PACKS, "mod")
        self.assertIn("cosmos_modules.py install gamegrubber", cmd)
        self.assertNotIn("flatpak", cmd)
        self.assertNotIn("dnf", cmd)
        self.assertIn("Nothing from this pack is installed", cp.remove_command(self.PACKS, "mod"))
        self.add_module()
        self.assertIn("Already installed", cp.install_command(self.PACKS, "mod"))
        self.assertIn("cosmos_modules.py uninstall gamegrubber", cp.remove_command(self.PACKS, "mod"))

    def test_a_module_can_not_be_baked_into_an_image_and_has_no_members(self):
        self.assertEqual(cp.members(self.PACKS, "mod"), ([], []))
        with self.assertRaises(ValueError) as cm:
            cp.bake_command(self.PACKS, "mod")
        self.assertIn("can't be baked", str(cm.exception))

    def test_the_real_game_downloads_pack_is_a_module_and_is_not_part_of_everything(self):
        real = cp.load(os.path.join(HERE, "..", "cosmos-apps", "expansion-packs.json"))
        self.assertEqual(cp.module_of(real, "game-downloads"), "gamegrubber")
        self.assertNotIn("game-downloads", cp.by_id(real, "everything").get("includes", []))      # it fetches copyrighted content: never implied
        self.assertIn("gamegrubber", cp.info_text(real, "game-downloads"))


class HostTests(unittest.TestCase):
    def test_an_ordinary_fedora_uses_dnf_and_the_image_uses_kronos(self):
        saved = os.environ["COSMOS_IMAGE_BASED"]
        self.addCleanup(os.environ.__setitem__, "COSMOS_IMAGE_BASED", saved)
        os.environ["COSMOS_IMAGE_BASED"] = "0"
        self.assertFalse(cp.image_based())
        cmd = cp.install_command(PACKS, "a", (set(), set()), apps=[], blocked=[])
        self.assertIn("sudo dnf install -y tool1", cmd)
        self.assertNotIn("kronos", cmd)
        self.assertEqual(cp.rpm_remove_command("x"), "sudo dnf remove -y x")
        os.environ["COSMOS_IMAGE_BASED"] = "1"
        self.assertIn("kronos exec -- rpm-ostree install --idempotent tool1", cp.install_command(PACKS, "a", (set(), set()), apps=[], blocked=[]))


class LogicTests(unittest.TestCase):
    def test_members_follow_includes_without_duplicates_or_loops(self):
        self.assertEqual(cp.members(PACKS, "b"), (["org.x.One", "org.x.Two"], ["tool2", "tool1"]))
        self.assertEqual(cp.members(PACKS, "loop1"), (["org.l.One", "org.l.Two"], []))
        self.assertEqual(cp.members(PACKS, "missing"), ([], []))

    def test_state_is_none_partial_or_installed(self):
        self.assertEqual(cp.status(PACKS, "a", (set(), set()))["state"], "none")
        self.assertEqual(cp.status(PACKS, "a", ({"org.x.One"}, set()))["state"], "partial")
        full = cp.status(PACKS, "a", ({"org.x.One", "org.x.Two"}, {"tool1"}))
        self.assertEqual((full["state"], full["missing_flatpak"], full["missing_rpm"]), ("installed", [], []))

    def test_install_only_fetches_what_is_missing(self):
        cmd = cp.install_command(PACKS, "a", ({"org.x.One"}, set()), apps=[], blocked=[])
        self.assertIn("flatpak install --user -y --noninteractive flathub org.x.Two", cmd)
        self.assertNotIn("org.x.One", cmd.split("flathub", 1)[1].split("&&")[0])
        self.assertIn("kronos exec -- rpm-ostree install --idempotent tool1", cmd)      # kronos applies it live if it can, else stages it
        self.assertNotIn("sudo", cmd)
        self.assertIn("find-terminals --purge-flatpaks", cmd)
        done = cp.install_command(PACKS, "a", ({"org.x.One", "org.x.Two"}, {"tool1"}), apps=[], blocked=[])
        self.assertNotIn("flatpak install", done)
        self.assertIn("already installed", done)

    def test_a_pack_with_a_forbidden_member_cannot_be_installed(self):
        with self.assertRaises(ValueError) as cm:
            cp.install_command(PACKS, "bad", (set(), set()))
        self.assertIn("can't be installed", str(cm.exception))
        with self.assertRaises(ValueError):
            cp.install_command(PACKS, "nope", (set(), set()))

    def test_remove_only_removes_what_is_there(self):
        cmd = cp.remove_command(PACKS, "a", ({"org.x.One"}, {"tool1"}))
        self.assertIn("flatpak uninstall --user -y --noninteractive org.x.One", cmd)
        self.assertIn("rpm-ostree uninstall tool1", cmd)
        self.assertNotIn("org.x.Two", cmd)
        self.assertIn("Nothing from this pack", cp.remove_command(PACKS, "a", (set(), set())))

    def test_names_are_quoted(self):
        cmd = cp.install_command([{"id": "q", "name": "Q; rm -rf /", "flatpak": ["org.a.B"]}], "q", (set(), set()), apps=[], blocked=[])
        self.assertIn("'== Q; rm -rf / =='", cmd)                                   # a name can't become a command

    def test_info_lists_members_with_their_state(self):
        text = cp.info_text(PACKS, "a", ({"org.x.One"}, set()))
        self.assertIn("[installed] org.x.One", text)
        self.assertIn("[ ]         tool1", text)

    def test_installed_reads_flatpak_rpm_and_layered_packages(self):
        class R:
            def __init__(self, out):
                self.stdout = out

        def run(cmd, **kw):
            if cmd[0] == "flatpak":
                return R("org.x.One\n" if "--user" in cmd else "org.x.Two\n")
            if cmd[0] == "rpm":
                return R("bash\ntool9\n")
            return R(json.dumps({"deployments": [{"requested-packages": ["tool1"]}]}))
        self.assertEqual(cp.installed(run), ({"org.x.One", "org.x.Two"}, {"bash", "tool9", "tool1"}))


class RealPackTests(unittest.TestCase):
    def setUp(self):
        self.packs = cp.load(os.path.join(HERE, "..", "cosmos-apps", "expansion-packs.json"))

    def test_the_real_packs_load_and_every_member_passes_the_policy(self):
        self.assertTrue({"developer-tools", "language-toolchains", "game-development", "everything"} <= {p["id"] for p in self.packs})
        for p in self.packs:
            self.assertEqual(cp.policy_problems(self.packs, p["id"]), [], p["id"])
            self.assertTrue(p["name"] and p["description"])

    def test_names_and_ids_are_well_formed(self):
        for p in self.packs:
            flatpaks, rpms = cp.members(self.packs, p["id"])
            for f in flatpaks:
                self.assertRegex(f, r"^[A-Za-z0-9_][A-Za-z0-9_-]*(\.[A-Za-z0-9_-]+){2,}$")
            for r in rpms:
                self.assertRegex(r, r"^[A-Za-z0-9][A-Za-z0-9._+-]*$")

    def test_the_everything_pack_is_everything(self):
        everything = cp.members(self.packs, "everything")
        for pid in ("developer-tools", "language-toolchains", "game-development", "creative", "music-studio", "office", "communication", "wine", "robotics"):
            f, r = cp.members(self.packs, pid)
            self.assertTrue(set(f) <= set(everything[0]) and set(r) <= set(everything[1]), pid)

    def test_pack_apps_have_a_policy_entry_that_blocks_their_rpm(self):
        apps = pol.load()
        by_flatpak = {a["flatpak"]: a for a in apps}
        for p in self.packs:
            for fid in p.get("flatpak", []):
                self.assertIn(fid, by_flatpak, f"{fid} is in pack {p['id']} but has no policy entry, so its RPM twin isn't blocked")
                self.assertEqual(by_flatpak[fid].get("pack"), p["id"])

    def test_nothing_in_the_base_image_is_also_in_a_pack(self):
        text = open(os.path.join(HERE, "..", "..", "Containerfile"), encoding="utf-8").read()
        body = re.search(r"# >>> bootc-studio:dnf-packages >>>\n(.*?)# <<< bootc-studio:dnf-packages <<<", text, re.S).group(1)
        baked = {t.strip("\\ ") for t in re.sub(r"^RUN .*$", "", body, flags=re.M).split()}
        for p in self.packs:
            for r in p.get("rpm", []):
                self.assertNotIn(r, baked, f"{r} is in pack {p['id']} but still installed in the base image")


if __name__ == "__main__":
    unittest.main()
