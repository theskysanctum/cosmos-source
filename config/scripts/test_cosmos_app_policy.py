"""Tests for cosmos_app_policy and that the Containerfile, the policy file and the package lists agree:
every app is installed exactly one way, and the way that is not used cannot be installed."""
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, HERE)
import cosmos_app_policy as pol

POLICY = os.path.join(ROOT, "config", "cosmos-apps", "app-policy.json")


def containerfile():
    with open(os.path.join(ROOT, "Containerfile"), encoding="utf-8") as fh:
        return fh.read()


def dnf_packages(text):
    body = re.search(r"# >>> bootc-studio:dnf-packages >>>\n(.*?)# <<< bootc-studio:dnf-packages <<<", text, re.S).group(1)
    return {tok.strip("\\ ") for tok in body.replace("RUN dnf install -y", "").split() if tok.strip("\\ ")}


def flatpak_ids(text):
    body = re.search(r"# >>> bootc-studio:flatpak-apps >>>\n(.*?)# <<< bootc-studio:flatpak-apps <<<", text, re.S).group(1)
    m = re.search(r"flatpak install --system -y flathub (.*)", body)
    return set(m.group(1).split()) if m else set()


# No app is an RPM-policy app right now, but the mechanism stays, so it is tested with a stand-in.
RPM_APP = {"name": "CopyQ", "use": "rpm", "flatpak": "com.github.hluk.copyq", "rpm": ["copyq", "copyq-*"], "appimage": ["copyq"]}


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.apps = pol.load(POLICY)
        self.with_rpm_app = self.apps + [RPM_APP]

    def test_the_policy_file_loads_and_is_well_formed(self):
        self.assertGreater(len(self.apps), 5)
        for app in self.apps:
            self.assertIn(app["use"], ("flatpak", "rpm"), app["name"])
            self.assertRegex(app["flatpak"], r"^[A-Za-z0-9_-]+(\.[A-Za-z0-9_-]+)+$", app["name"])
            self.assertTrue(app["rpm"], app["name"])

    def test_rpm_and_appimage_of_a_flatpak_app_are_refused(self):
        self.assertFalse(pol.check("rpm", "kdenlive", self.apps)[0])
        self.assertFalse(pol.check("rpm", "krita-libs", self.apps)[0])
        self.assertFalse(pol.check("appimage", "krita", self.apps)[0])
        self.assertIn("flatpak run org.kde.kdenlive", pol.check("rpm", "kdenlive", self.apps)[1])
        self.assertTrue(pol.check("rpm", "vlc", self.apps)[0])
        self.assertTrue(pol.check("rpm", "kritanope", self.apps)[0])                    # not a glob match

    def test_the_flatpak_of_an_rpm_app_is_refused_but_a_flatpak_app_is_fine(self):
        self.assertFalse(pol.check("flatpak", "com.github.hluk.copyq", self.with_rpm_app)[0])
        self.assertFalse(pol.check("flatpak", "com.github.hluk.copyq.Locale", self.with_rpm_app)[0])
        self.assertTrue(pol.check("flatpak", "org.kde.kdenlive", self.apps)[0])
        self.assertTrue(pol.check("flatpak", "org.videolan.VLC", self.apps)[0])

    def test_matching_is_case_insensitive(self):
        self.assertFalse(pol.check("rpm", "KDENLIVE", self.apps)[0])

    def test_scope_duplicates(self):
        class R:
            def __init__(self, rc):
                self.returncode = rc
        installed_system = lambda cmd, **kw: R(0 if "--system" in cmd else 1)
        self.assertFalse(pol.check_flatpak_scope("org.kde.krita", "user", installed_system)[0])
        self.assertTrue(pol.check_flatpak_scope("org.kde.krita", "system", installed_system)[0])
        self.assertTrue(pol.check_flatpak_scope("x.y.z", "user", lambda *a, **k: R(1))[0])
        def broken(*a, **k):
            raise OSError("no flatpak")
        self.assertTrue(pol.check_flatpak_scope("x.y.z", "user", broken)[0])

    def test_generated_build_inputs(self):
        exclude = pol.dnf_exclude(self.with_rpm_app).split(",")
        self.assertIn("kdenlive", exclude)
        self.assertIn("kdenlive-*", exclude)
        self.assertNotIn("copyq", exclude)                      # an RPM app is never excluded
        filt = pol.flatpak_filter(self.with_rpm_app)
        self.assertIn("deny com.github.hluk.copyq\n", filt)
        self.assertNotIn("org.kde.kdenlive", filt)              # a Flatpak app is never filtered
        self.assertFalse([l for l in filt.splitlines() if l.startswith("allow")])     # an allow rule would override the denies
        self.assertIn("org.kde.kdenlive", pol.flatpak_apps(self.apps))

    def test_with_no_rpm_apps_the_filter_only_denies_the_blocked_list(self):
        denies = [l for l in pol.flatpak_filter(self.apps).splitlines() if l.startswith("deny")]
        self.assertTrue(denies)
        self.assertTrue(all("kdenlive" not in l and "krita" not in l for l in denies))     # Flatpak apps are never filtered

    def test_the_dnf_conf_edit_the_containerfile_does(self):
        conf = os.path.join(tempfile.mkdtemp(), "dnf.conf")
        with open(conf, "w") as fh:
            fh.write("[main]\ngpgcheck=1\n")
        line = pol.dnf_exclude(self.apps)
        subprocess.run(["sed", "-i", f"/^\\[main\\]/a excludepkgs={line}", conf], check=True)
        text = open(conf).read()
        self.assertTrue(text.startswith("[main]\nexcludepkgs="))
        self.assertIn("kdenlive", text)
        self.assertIn("gpgcheck=1", text)


class ContainerfileAgreementTests(unittest.TestCase):
    def setUp(self):
        self.apps = pol.load(POLICY)
        self.text = containerfile()
        self.rpms, self.flatpaks = dnf_packages(self.text), flatpak_ids(self.text)

    def test_flatpak_apps_are_flatpaks_and_not_rpms(self):
        import cosmos_packs
        packs = cosmos_packs.load()
        for app in self.apps:
            if app["use"] != "flatpak":
                continue
            if app.get("pack"):          # not in the image: it is an Expansion Pack member instead
                self.assertNotIn(app["flatpak"], self.flatpaks, f"{app['name']} is a pack app but is also baked in")
                self.assertIn(app["flatpak"], cosmos_packs.members(packs, app["pack"])[0], f"{app['name']} is not in pack {app['pack']}")
            else:
                self.assertIn(app["flatpak"], self.flatpaks, f"{app['name']}: Flatpak not installed by the Containerfile")
            for name in app["rpm"]:
                if "*" not in name:
                    self.assertNotIn(name, self.rpms, f"{app['name']}: RPM {name} is still installed as well")

    def test_rpm_apps_are_rpms_and_not_flatpaks(self):
        for app in self.apps:
            if app["use"] != "rpm":
                continue
            self.assertNotIn(app["flatpak"], self.flatpaks, app["name"])
            self.assertTrue(any(n in self.rpms for n in app["rpm"] if "*" not in n), f"{app['name']}: its RPM is not installed")

    def test_every_baked_flatpak_is_in_the_policy(self):
        known = {a["flatpak"] for a in self.apps}
        self.assertEqual(self.flatpaks - known, set(), "a Flatpak in the image has no policy entry, so its RPM twin is not blocked")

    def test_the_enforcement_steps_are_in_the_containerfile(self):
        for needle in ("cosmos_app_policy.py flatpak-filter", "remote-modify --system --filter", "excludepkgs=",
                       "app-policy.json", "flatpak override --system --nofilesystem=host", "--nofilesystem=home"):
            self.assertIn(needle, self.text)
        self.assertLess(self.text.rindex("cosmos_app_policy.py dnf-exclude"), len(self.text))
        # the excludepkgs step must come after the last dnf install, or it would refuse its own packages
        last_install = max(m.start() for m in re.finditer(r"dnf install -y", self.text))
        self.assertGreater(self.text.index("cosmos_app_policy.py dnf-exclude"), last_install)


if __name__ == "__main__":
    unittest.main()
