"""GameBox uses CosmOS's profiles: one list, one active profile, one folder each, PINs honoured; GameBox's own older profile folders are carried over."""
import importlib
import os
import shutil
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
SCRATCH = Path(os.path.expanduser("~/.placements/tests/gamebox-profiles"))


class Shared(unittest.TestCase):
    def setUp(self):
        shutil.rmtree(SCRATCH, ignore_errors=True)
        (SCRATCH / "home").mkdir(parents=True)
        self.addCleanup(shutil.rmtree, SCRATCH, True)
        self.old_home = os.environ.get("HOME")
        os.environ["HOME"] = str(SCRATCH / "home")         # cosmos_profiles reads ~ when it is imported
        for name in ("cosmos_profiles", "profiles"):
            sys.modules.pop(name, None)
        self.profiles = importlib.import_module("profiles")        # finds cosmos_profiles.py itself (/usr/lib/cosmos, or config/scripts in a checkout)
        self.assertTrue(self.profiles.shared(), "cosmos_profiles.py was not found")
        self.cp = sys.modules["cosmos_profiles"]
        self.profiles.PROFILES_ROOT = SCRATCH / "legacy"    # GameBox's older own folder
        self.profiles._migrated = False
        self.profiles._forget()

    def tearDown(self):
        if self.old_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = self.old_home
        for name in ("cosmos_profiles", "profiles"):
            sys.modules.pop(name, None)

    def test_a_cosmos_profile_is_a_gamebox_profile(self):
        self.cp.create_profile("Tester")
        self.profiles._forget()
        self.assertEqual(self.profiles.list_profiles(), ["Tester"])

    def test_the_active_profile_is_cosmoss(self):
        self.assertIsNone(self.profiles.get_active_profile())
        made = self.cp.create_profile("Sam")
        self.cp.set_active(made["slug"])
        self.assertEqual(self.profiles.get_active_profile(), "Sam")

    def test_switching_in_gamebox_switches_for_cosmos_too(self):
        one, two = self.cp.create_profile("One"), self.cp.create_profile("Two")
        self.cp.set_active(one["slug"])
        self.profiles._forget()
        self.profiles.set_active_profile("Two")
        self.assertEqual(self.cp.active_slug(), two["slug"])

    def test_a_profile_made_in_gamebox_is_a_cosmos_profile_with_a_saves_folder(self):
        self.profiles.create_profile("Kid")
        self.assertEqual([p["name"] for p in self.cp.list_profiles()], ["Kid"])
        self.assertTrue((self.profiles.profile_dir("Kid") / "saves").is_dir())

    def test_its_files_live_in_the_cosmos_profile_folder(self):
        made = self.cp.create_profile("Ann")
        self.cp.set_active(made["slug"])
        self.profiles._forget()
        self.assertEqual(self.profiles.profile_dir(), Path(self.cp.profile_dir(made["slug"])))

    def test_deleting_removes_the_cosmos_profile(self):
        self.cp.create_profile("Gone")
        self.profiles._forget()
        self.profiles.delete_profile("Gone")
        self.assertEqual(self.cp.list_profiles(), [])

    def test_pins_are_the_cosmos_pins(self):
        self.cp.create_profile("Locked", pin="4321")
        self.cp.create_profile("Open")
        self.profiles._forget()
        self.assertTrue(self.profiles.has_pin("Locked"))
        self.assertFalse(self.profiles.has_pin("Open"))
        self.assertTrue(self.profiles.verify_pin("Locked", "4321"))
        self.assertFalse(self.profiles.verify_pin("Locked", "0000"))

    def fake_renderer(self, colour):
        """A stand-in for avatar3d (which needs a GPU context): render_portrait returns a small solid-colour picture."""
        import types
        from PIL import Image
        module = types.ModuleType("avatar3d")
        module.render_portrait = lambda traits, size=256: Image.new("RGBA", (8, 8), colour)
        sys.modules["avatar3d"] = module
        self.addCleanup(sys.modules.pop, "avatar3d", None)

    def test_the_moment_an_astronar_is_made_its_portrait_becomes_the_profile_picture(self):
        self.fake_renderer((255, 0, 0, 255))
        made = self.cp.create_profile("Nova")
        self.cp.set_active(made["slug"])
        self.profiles._forget()
        self.profiles.set_picture_traits({"skin": "x"}, "Nova")
        from PIL import Image
        avatar = self.cp.load(made["slug"])["avatar"]
        self.assertTrue(avatar.endswith("avatar.png"))
        self.assertEqual(Image.open(avatar).convert("RGBA").getpixel((0, 0)), (255, 0, 0, 255))
        self.assertEqual(Image.open(self.cp.FACE_PATH).convert("RGBA").getpixel((0, 0)), (255, 0, 0, 255))     # the bar's picture too

    def test_changing_the_astronar_replaces_the_picture_again(self):
        self.fake_renderer((0, 255, 0, 255))
        made = self.cp.create_profile("Nova")
        self.profiles._forget()
        self.profiles.set_picture_traits({"a": 1}, "Nova")
        self.fake_renderer((0, 0, 255, 255))
        self.profiles.set_picture_traits({"a": 2}, "Nova")
        from PIL import Image
        self.assertEqual(Image.open(self.cp.load(made["slug"])["avatar"]).convert("RGBA").getpixel((0, 0)), (0, 0, 255, 255))

    def test_a_failing_render_leaves_the_old_picture_and_does_not_raise(self):
        import types
        module = types.ModuleType("avatar3d")
        def broken(*a, **k):
            raise RuntimeError("no GPU")
        module.render_portrait = broken
        sys.modules["avatar3d"] = module
        self.addCleanup(sys.modules.pop, "avatar3d", None)
        made = self.cp.create_profile("Nova")
        self.profiles._forget()
        self.profiles.set_picture_traits({"a": 1}, "Nova")             # must not raise
        self.assertIsNone(self.cp.load(made["slug"])["avatar"])

    def test_older_gamebox_profile_folders_are_carried_over_once(self):
        legacy = SCRATCH / "legacy" / "Old Timer"
        (legacy / "saves").mkdir(parents=True)
        (legacy / "quick_launch.ini").write_text("[x]\n")
        self.assertEqual(self.profiles.list_profiles(), ["Old Timer"])
        made = self.cp.list_profiles()[0]
        self.assertEqual((Path(self.cp.profile_dir(made["slug"])) / "quick_launch.ini").read_text(), "[x]\n")
        self.profiles._migrated = False
        self.profiles._forget()
        self.assertEqual(self.profiles.list_profiles(), ["Old Timer"])           # not made a second time

    def test_an_existing_cosmos_profile_of_the_same_name_is_not_duplicated_by_the_carry_over(self):
        self.cp.create_profile("Twin")
        (SCRATCH / "legacy" / "Twin").mkdir(parents=True)
        self.assertEqual(self.profiles.list_profiles(), ["Twin"])
        self.assertEqual(len(self.cp.list_profiles()), 1)


if __name__ == "__main__":
    unittest.main()
