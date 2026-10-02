#!/usr/bin/env python3
"""Tests for cosmos_updates with canned command output. Run: python3 test_cosmos_updates.py"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cosmos_updates as cu  # noqa: E402


def fake_run(table):
    """A run() that answers by substring of the command."""
    def run(command, timeout=0):
        for needle, (rc, out) in table.items():
            if needle in command:
                return rc, out
        return 1, ""
    return run


class ParserTests(unittest.TestCase):
    def test_flatpak(self):
        text = "Name\tApplication ID\tVersion\nGIMP\torg.gimp.GIMP\t3.0.4\nInkscape\torg.inkscape.Inkscape\t\n"
        ups = cu.parse_flatpak_updates(text, "user")
        self.assertEqual([u.name for u in ups], ["GIMP", "Inkscape"])
        self.assertEqual(ups[0].new, "3.0.4")

    def test_dnf4_and_dnf5(self):
        dnf4 = "bash.x86_64   5.2.37-1.fc44   updates\nkernel.x86_64  7.2.7-200.fc44  updates\n"
        self.assertEqual([u.name for u in cu.parse_dnf_check_update(dnf4)], ["bash", "kernel"])
        dnf5 = "Repositories loaded.\nName   Arch   Version   Repository   Size\nbash   x86_64   5.2.37-1.fc44   updates   1.8 MiB\n"
        ups = cu.parse_dnf_check_update(dnf5)
        self.assertEqual([(u.name, u.new, u.source) for u in ups], [("bash", "5.2.37-1.fc44", "updates")])

    def test_am_list(self):
        text = "Installed apps:\n\x1b[32m◆ krita\x1b[0m  120 MiB\n◆ obsidian  90 MiB\n"
        self.assertEqual(cu.parse_am_installed(text), ["krita", "obsidian"])


class ProviderTests(unittest.TestCase):
    def test_missing_tools_are_reported_not_fatal(self):
        results = cu.check_all(run=fake_run({}), tool_present=lambda t: False)
        self.assertTrue(all(not r.available for r in results))
        self.assertEqual(cu.summary(results)[0], 0)

    def test_flatpak_updates_found_in_both_scopes(self):
        run = fake_run({"--user": (0, "GIMP\torg.gimp.GIMP\t3.0\n"), "--system": (0, "Krita\torg.kde.krita\t5.2\n")})
        r = cu.check_flatpak(run, lambda t: t == "flatpak")
        self.assertEqual(r.count, 2)
        self.assertIn("flatpak update --user", r.apply_command)

    def test_appimage_has_apply_but_no_count(self):
        r = cu.check_appimages(fake_run({"am -f": (0, "◆ krita  1 MiB\n")}), lambda t: t == "am")
        self.assertEqual(r.count, 0)
        self.assertEqual(r.apply_command, "am -u")
        self.assertIn("1 AppImage", r.note)

    def test_system_registry_image_can_be_upgraded(self):
        status = '{"spec": {"image": {"image": "quay.io/me/cosmos:latest", "transport": "registry"}}}'
        run = fake_run({"check-update": (100, "bash.x86_64 5.2 updates\n"), "bootc status": (0, status)})
        r = cu.check_system(run, lambda t: True)
        self.assertEqual(r.count, 1)
        self.assertEqual(r.apply_command, "kronos exec -- bootc upgrade")

    def test_an_ordinary_user_who_cannot_run_bootc_status_is_answered_from_rpm_ostree(self):
        """`bootc status` needs root; the CosmOS desktop account isn't root, and had no Update button for the system because of it."""
        ostree = ('{"deployments": [{"booted": false, "container-image-reference": "ostree-unverified-registry:old/one:1"},'
                  ' {"booted": true, "container-image-reference": "ostree-unverified-registry:ghcr.io/theskysanctum/cosmos:latest"}]}')
        run = fake_run({"check-update": (100, "bash.x86_64 5.2 updates\n"), "bootc status": (1, "error: this command requires root"), "rpm-ostree status": (0, ostree)})
        r = cu.check_system(run, lambda t: True)
        self.assertEqual(r.apply_command, "kronos exec -- bootc upgrade")
        self.assertIn("ghcr.io/theskysanctum/cosmos:latest", r.note)

    def test_image_references_from_rpm_ostree(self):
        cases = {"ostree-unverified-registry:ghcr.io/a/b:latest": ("registry", "ghcr.io/a/b:latest"),
                 "ostree-image-signed:docker://quay.io/fedora/fedora-bootc:44": ("registry", "quay.io/fedora/fedora-bootc:44"),
                 "ostree-remote-registry:fedora:quay.io/fedora/fedora-bootc:44": ("registry", "quay.io/fedora/fedora-bootc:44"),
                 "ostree-unverified-image:containers-storage:localhost/cosmos:latest": ("local", "localhost/cosmos:latest"),
                 "ostree-unverified-registry:localhost/cosmos:latest": ("local", "localhost/cosmos:latest"),
                 "": (None, ""), "nonsense": (None, "")}
        for reference, expected in cases.items():
            self.assertEqual(cu.parse_image_reference(reference), expected, reference)

    def test_no_bootc_and_no_rpm_ostree_means_not_a_bootc_system(self):
        self.assertEqual(cu.bootc_source(fake_run({})), (None, ""))

    def test_system_local_image_says_rebuild(self):
        status = '{"spec": {"image": {"image": "localhost/cosmos:latest", "transport": "containers-storage"}}}'
        run = fake_run({"check-update": (100, "bash.x86_64 5.2 updates\n"), "bootc status": (0, status)})
        r = cu.check_system(run, lambda t: True)
        self.assertEqual(r.apply_command, "")
        self.assertIn("rebuild", r.note)

    def test_local_image_with_layered_packages_can_upgrade_those(self):
        status = '{"spec": {"image": {"image": "localhost/cosmos:latest", "transport": "containers-storage"}}}'
        ostree = '{"deployments": [{"requested-packages": ["htop", "tmux"]}]}'
        run = fake_run({"check-update": (100, "bash.x86_64 5.2 updates\n"), "bootc status": (0, status),
                        "rpm-ostree status": (0, ostree)})
        r = cu.check_system(run, lambda t: True)
        self.assertEqual(r.apply_command, "kronos exec -- rpm-ostree upgrade")
        self.assertIn("2 package(s) were added", r.note)

    def test_dnf_error_is_carried_not_hidden(self):
        r = cu.check_system(fake_run({"check-update": (1, "Failed to download metadata\n")}), lambda t: t == "dnf")
        self.assertIn("Failed to download", r.error)

    def test_apply_all_chains_only_what_has_a_command(self):
        a = cu.Result("flatpak", "Flatpak apps", True, [cu.Update("x")], apply_command="flatpak update -y")
        b = cu.Result("system", "System", True, [], apply_command="")
        cmd = cu.apply_all_command([a, b])
        self.assertIn("flatpak update -y", cmd)
        self.assertNotIn("System", cmd)

    def test_summary_and_signature(self):
        a = cu.Result("flatpak", "Flatpak apps", True, [cu.Update("x", new="1")])
        self.assertEqual(cu.summary([a]), (1, "1 flatpak apps"))
        self.assertEqual(cu.signature([a]), "flatpak:x:1")
        self.assertEqual(cu.summary([])[1], "Everything is up to date")


if __name__ == "__main__":
    unittest.main()
