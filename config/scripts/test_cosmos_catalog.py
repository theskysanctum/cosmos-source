"""Tests for the curated catalog (cosmos_catalog.py) and for the real catalog.json."""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import cosmos_app_policy as pol
import cosmos_catalog as cat
import cosmos_packs as cp

REAL = os.path.join(HERE, "..", "cosmos-apps", "catalog.json")
SMALL = {
    "flatpak_families": ["org.freedesktop.Platform"],
    "apps": [
        {"name": "Krita", "category": "Creative", "flatpak": "org.kde.krita"},
        {"name": "htop", "category": "System", "rpm": ["htop", "HandBrake-cli"]},
        {"name": "Ventoy", "category": "Utilities", "appimage": ["ventoy"]},
    ],
}


class Checks(unittest.TestCase):
    def test_flatpaks_are_allowed_with_their_extensions_and_the_runtime_families(self):
        for ref in ("org.kde.krita", "org.kde.krita/x86_64/stable", "app/org.kde.krita/x86_64/stable", "org.kde.krita.Locale",
                    "org.freedesktop.Platform", "runtime/org.freedesktop.Platform.GL.default/x86_64/24.08"):
            self.assertTrue(cat.check("flatpak", ref, SMALL)[0], ref)

    def test_anything_else_is_refused_and_lookalikes_do_not_slip_through(self):
        for ref in ("org.gimp.GIMP", "org.kde.kritax", "org.kde", "krita", "", "org.kde.krita;rm", "../org.kde.krita", "org.freedesktop.Platformer"):
            ok, why = cat.check("flatpak", ref, SMALL)
            self.assertFalse(ok, ref)
            self.assertIn("catalog", why)

    def test_packages_are_exact_names_only(self):
        self.assertTrue(cat.check("rpm", "htop", SMALL)[0])
        self.assertTrue(cat.check("rpm", "htop.x86_64", SMALL)[0])
        self.assertTrue(cat.check("rpm", "HandBrake-cli", SMALL)[0])              # case is part of an rpm name
        for name in ("hto", "htop2", "HTOP", "htop-*", "gcc", "", "hto*"):
            self.assertFalse(cat.check("rpm", name, SMALL)[0], name)
        ok, why = cat.check("rpm", "htop-3.4.1", SMALL)
        self.assertFalse(ok)
        self.assertIn("no version", why)

    def test_appimages_are_exact_ids_and_case_does_not_matter(self):
        self.assertTrue(cat.check("appimage", "ventoy", SMALL)[0])
        self.assertTrue(cat.check("appimage", "Ventoy", SMALL)[0])
        for name in ("ventoy2", "vent", "obs-studio", ""):
            self.assertFalse(cat.check("appimage", name, SMALL)[0], name)

    def test_no_catalog_at_all_means_nothing_is_curated(self):
        self.assertTrue(cat.check("rpm", "anything", {})[0])
        self.assertTrue(cat.check("flatpak", "any.thing.At.All", {"apps": []})[0])

    def test_the_filter_hides_everything_but_the_catalog(self):
        lines = cat.flatpak_filter(SMALL).splitlines()
        self.assertEqual([l for l in lines if not l.startswith("#")][0], "deny *")
        for ident in ("org.kde.krita", "org.freedesktop.Platform"):
            self.assertIn(f"allow {ident}", lines)
            self.assertIn(f"allow {ident}.*", lines)                            # extensions such as .Locale (Flatpak's id.* does not match the app itself)
        self.assertEqual([l for l in lines if l.startswith("deny")], ["deny *"])

    def test_the_file_is_checked_for_mistakes(self):
        bad = {"apps": [{"name": "A", "category": "X", "flatpak": "notanid"}, {"name": "A", "category": "X", "rpm": ["ok"]},
                        {"name": "B", "category": "X"}, {"name": "C", "category": "X", "flatpak": "a.b.C", "appimage": ["c"]},
                        {"name": "D", "category": "X", "rpm": ["bad name"]}]}
        text = "\n".join(cat.problems(bad))
        for expected in ("A: listed twice", "'notanid' is not a Flatpak id", "B: has no flatpak id", "C: an app is one packaging only", "'bad name' is not a package name"):
            self.assertIn(expected, text)
        self.assertEqual(cat.problems(SMALL), [])


class PolicyIntegration(unittest.TestCase):
    def test_the_policy_check_applies_the_catalog_when_it_reads_its_own_files(self):
        saved = cat.CATALOG_PATHS
        self.addCleanup(setattr, cat, "CATALOG_PATHS", saved)
        cat.CATALOG_PATHS = (REAL,)
        self.assertFalse(pol.check("rpm", "definitely-not-a-package-in-the-catalog")[0])
        self.assertTrue(pol.check("rpm", "htop")[0])
        self.assertTrue(pol.check("flatpak", "org.kde.krita")[0])
        self.assertFalse(pol.check("flatpak", "org.mozilla.firefox")[0])
        self.assertFalse(pol.check("appimage", "some-random-appimage")[0])

    def test_test_data_handed_to_the_policy_skips_the_catalog(self):
        self.assertTrue(pol.check("rpm", "kritanope", [])[0])                                # explicit apps: packaging rules only
        self.assertFalse(pol.check("rpm", "kritanope", [], catalog=SMALL)[0])                # unless a catalog is handed in too
        self.assertTrue(pol.check("rpm", "kritanope", None, catalog=False)[0])

    def test_blocked_reasons_come_before_the_catalog_message(self):
        ok, why = pol.check("flatpak", "org.gnome.Terminal", None, None, None, SMALL)
        self.assertFalse(ok)
        self.assertIn("can't be installed on CosmOS", why)


class RealCatalog(unittest.TestCase):
    def setUp(self):
        self.catalog = cat.load(REAL)
        self.policy_apps, self.blocked, self.dnf_only = pol.load(), pol.load_blocked(), pol.load_dnf_only()

    def test_the_file_has_no_mistakes(self):
        self.assertGreater(len(cat.apps(self.catalog)), 50)
        self.assertEqual(cat.problems(self.catalog), [])

    def test_every_expansion_pack_member_is_in_the_catalog(self):
        packs = cp.load(os.path.join(HERE, "..", "cosmos-apps", "expansion-packs.json"))
        for p in packs:
            flatpaks, rpms = cp.members(packs, p["id"])
            for f in flatpaks:
                self.assertTrue(cat.flatpak_allowed(f, self.catalog), f"{f} (pack {p['id']}) is not in the catalog")
            for r in rpms:
                self.assertTrue(cat.rpm_allowed(r, self.catalog), f"{r} (pack {p['id']}) is not in the catalog")

    def test_every_app_with_a_policy_entry_is_in_the_catalog(self):
        for a in self.policy_apps:
            self.assertTrue(cat.flatpak_allowed(a["flatpak"], self.catalog), a["name"])

    def test_nothing_blocked_or_dnf_only_is_in_the_catalog(self):
        # Flatpak's allow rules beat deny rules, so the only way to keep these out of the filter is to never list them.
        import fnmatch
        for group in list(self.blocked) + list(self.dnf_only):
            for pattern in group.get("flatpak", []):
                for ident in cat.flatpak_ids(self.catalog):
                    self.assertFalse(fnmatch.fnmatchcase(ident, pattern), f"{ident} matches the blocked pattern {pattern} ({group.get('name')})")
            for pattern in group.get("appimage", []):
                for name in cat.appimage_names(self.catalog):
                    self.assertFalse(fnmatch.fnmatchcase(name.lower(), pattern.lower()), f"AppImage {name} matches {pattern} ({group.get('name')})")
            if group in self.blocked:                                                          # game stores may be installed as packages; blocked things may not
                for pattern in group.get("rpm", []):
                    for name in cat.rpm_names(self.catalog):
                        self.assertFalse(fnmatch.fnmatchcase(name.lower(), pattern.lower()), f"{name} matches {pattern} ({group.get('name')})")

    def test_a_flatpak_app_is_never_also_offered_as_a_package_or_appimage(self):
        for a in cat.apps(self.catalog):
            self.assertLessEqual(sum(1 for k in ("flatpak", "rpm", "appimage") if a.get(k)), 1, a["name"] if not a.get("pack") else a["name"])
        for a in self.policy_apps:                                                             # and the policy's rpm/appimage twins are not listed
            for r in cat.rpm_names(self.catalog):
                if a.get("use") == "flatpak":
                    self.assertFalse(pol._matches(a.get("rpm"), r), f"{r} is the RPM twin of the Flatpak {a['name']}")
            for n in cat.appimage_names(self.catalog):
                if a.get("use") == "flatpak":
                    self.assertFalse(pol._matches(a.get("appimage"), n), f"AppImage {n} is the twin of the Flatpak {a['name']}")

    def test_the_generated_filter_contains_no_terminal_and_the_terminal_scan_still_agrees(self):
        text = cat.flatpak_filter(self.catalog)
        for banned in ("Terminal", "Ptyxis", "BlackBox", "Konsole", "konsole"):
            self.assertNotIn(banned, text)

    def test_the_base_image_dependencies_are_not_broken_by_the_runtime_families(self):
        families = cat.families(self.catalog)
        for needed in ("org.freedesktop.Platform", "org.gnome.Platform", "org.kde.Platform"):
            self.assertIn(needed, families)


def load_shop():
    import importlib.machinery
    import importlib.util
    loader = importlib.machinery.SourceFileLoader("cosmos_shop_under_test", os.path.join(HERE, "cosmos-shop.py"))
    spec = importlib.util.spec_from_loader("cosmos_shop_under_test", loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class ShopSections(unittest.TestCase):
    """The Shop lists only the catalog when one is installed (the module needs GTK, so this is skipped where it can't be imported)."""

    def setUp(self):
        try:
            self.shop = load_shop()
        except Exception as exc:          # no GTK / display in this environment
            self.skipTest(f"cosmos-shop can't be imported here: {exc}")
        saved = cat.CATALOG_PATHS
        self.addCleanup(setattr, cat, "CATALOG_PATHS", saved)
        cat.CATALOG_PATHS = (REAL,)

    def test_each_source_lists_only_its_own_kind_under_the_catalogs_categories(self):
        sections, overflow, total = self.shop.catalog_sections("flatpak", lambda i: False)
        ids = [i for _, items in sections for i, _, _ in items]
        self.assertEqual(sorted(ids), cat.flatpak_ids())
        self.assertEqual((overflow, total), ([], len(cat.flatpak_ids())))
        self.assertEqual(sections[0][0], "Games")                                      # the catalog's own order, not alphabetical
        rpm_ids = [i for _, items in self.shop.catalog_sections("rpm", lambda i: False)[0] for i, _, _ in items]
        self.assertEqual(sorted(rpm_ids), cat.rpm_names())
        ai = [i for _, items in self.shop.catalog_sections("appimage", lambda i: False)[0] for i, _, _ in items]
        self.assertEqual(sorted(ai), cat.appimage_names())

    def test_what_is_installed_is_hidden_but_still_counted(self):
        all_sections, _, total = self.shop.catalog_sections("rpm", lambda i: False)
        sections, _, total_hidden = self.shop.catalog_sections("rpm", lambda i: i in ("htop", "steam"))
        shown = [i for _, items in sections for i, _, _ in items]
        self.assertNotIn("htop", shown)
        self.assertNotIn("steam", shown)
        self.assertEqual(total, total_hidden)

    def test_without_a_catalog_the_caller_lists_everything_as_before(self):
        cat.CATALOG_PATHS = (os.path.join(HERE, "no-such-catalog.json"),)
        self.assertIsNone(self.shop.catalog_sections("rpm", lambda i: False))


if __name__ == "__main__":
    unittest.main()
