"""Tests for the kronos actions: validation, tiers, and that every plan is argv lists built from validated data."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))
import cosmos_app_policy as pol
import kronos_actions as ka


def real_check(kind, name):
    return pol.check(kind, name, pol.load(), pol.load_blocked())


class Base(unittest.TestCase):
    def setUp(self):
        self.ctx = ka.Context(app_check=real_check)

    def plan(self, action, args=None):
        return ka.plan(action, args if args is not None else {}, self.ctx)

    def refused(self, action, args, contains=""):
        with self.assertRaises(ka.Refused) as cm:
            self.plan(action, args)
        self.assertIn(contains, str(cm.exception))


class PackageTests(Base):
    def test_install_builds_argv_with_a_fallback_and_is_automatic(self):
        p = self.plan("pkg.install", {"packages": ["htop", "stow", "htop"]})
        self.assertEqual(p.tier, ka.AUTO)
        self.assertEqual(p.args["packages"], ["htop", "stow"])                          # sorted, de-duplicated
        alts = p.steps[0].alternatives
        self.assertEqual(alts[0][:4], ["rpm-ostree", "install", "--idempotent", "--apply-live"])
        self.assertEqual(alts[1][:3], ["rpm-ostree", "install", "--idempotent"])
        self.assertNotIn("--apply-live", alts[1])

    def test_bad_names_are_refused_before_anything_is_built(self):
        for bad in ("-y", "--installroot=/x", "a b", "a;rm", "a\nb", "htop\n", "$(id)", "", "x" * 200, "../etc", "a/b"):
            self.refused("pkg.install", {"packages": [bad]}, "valid package name")
        self.refused("pkg.install", {"packages": []})
        self.refused("pkg.install", {"packages": "htop"})
        self.refused("pkg.install", {"packages": ["p%d" % i for i in range(60)]}, "At most")
        self.refused("pkg.install", {})

    def test_policy_stops_terminals_and_duplicate_copies(self):
        self.refused("pkg.install", {"packages": ["htop", "xterm"]}, "can't be installed")
        self.refused("pkg.install", {"packages": ["kdenlive"]}, "Flatpak")
        p = self.plan("pkg.remove", {"packages": ["kdenlive"]})                          # removing is never refused
        self.assertEqual(p.steps[0].alternatives, [["rpm-ostree", "uninstall", "kdenlive"]])


class OsTests(Base):
    def test_upgrade_is_automatic_rollback_and_switch_need_the_passphrase(self):
        self.assertEqual(self.plan("os.upgrade").steps[-1].alternatives, [["bootc", "upgrade"]])
        self.assertEqual(self.plan("os.upgrade", {"method": "rpm-ostree"}).steps[0].alternatives, [["rpm-ostree", "upgrade"]])
        self.refused("os.upgrade", {"method": "curl"})
        # the flags the GameBox update screen needs, and the /boot automount wake-up before bootc touches the boot files
        self.assertEqual(self.plan("os.upgrade", {"check": True}).steps[-1].alternatives, [["bootc", "upgrade", "--check"]])
        self.assertEqual(self.plan("os.upgrade", {"apply": True}).steps[-1].alternatives, [["bootc", "upgrade", "--apply"]])
        self.assertEqual(self.plan("os.upgrade", {"apply": True}).steps[0].alternatives, [["ls", "/boot"]])
        self.refused("os.upgrade", {"check": True, "apply": True})
        self.refused("os.upgrade", {"method": "rpm-ostree", "apply": True})
        self.refused("os.upgrade", {"apply": "yes"})
        rb = self.plan("os.rollback", {"apply": True})
        self.assertEqual((rb.tier, rb.steps[-1].alternatives), (ka.PASSPHRASE, [["bootc", "rollback", "--apply"]]))
        self.assertEqual(self.plan("os.rollback").tier, ka.PASSPHRASE)
        p = self.plan("os.switch", {"ref": "ghcr.io/me/cosmos:latest"})
        self.assertEqual((p.tier, p.steps[-1].alternatives), (ka.PASSPHRASE, [["bootc", "switch", "ghcr.io/me/cosmos:latest"]]))

    def test_switch_refs_are_strict(self):
        for good in ("quay.io/x/y:1", "docker://ghcr.io/a/b:latest", "localhost:5000/x/y:t", "ghcr.io/a/b@sha256:" + "a" * 64):
            self.plan("os.switch", {"ref": good})
        for bad in ("ghcr.io/a/b", "--transport=x", "ghcr.io/a b/c:1", "ghcr.io/a/b:1;id", "file:///x", "ghcr.io/a/b:1\n", "cosmos:latest", ""):
            self.refused("os.switch", {"ref": bad})


class FlatpakTests(Base):
    def test_install_is_system_wide_from_flathub_and_followed_by_the_terminal_check(self):
        p = self.plan("flatpak.install", {"id": "org.kde.krita"})
        self.assertEqual(p.tier, ka.AUTO)
        self.assertEqual(p.steps[0].alternatives[0], ["flatpak", "install", "--system", "-y", "--noninteractive", "flathub", "org.kde.krita"])
        self.assertEqual(p.steps[1].alternatives[0], ["/usr/local/bin/find-terminals", "--purge-flatpaks"])

    def test_ids_remotes_and_policy(self):
        self.refused("flatpak.install", {"id": "org.gnome.Terminal"}, "can't be installed")
        self.refused("flatpak.install", {"id": "not an id"})
        self.refused("flatpak.install", {"id": "-y"})
        self.refused("flatpak.install", {"id": "-y.evil.app"})           # would read as an option
        self.refused("flatpak.install", {"id": "org.kde.krita", "remote": "evil"})
        self.refused("flatpak.install", {})
        self.assertEqual(self.plan("flatpak.update").steps[0].alternatives[0][-1], "--noninteractive")
        self.assertEqual(self.plan("flatpak.remove", {"id": "org.kde.krita"}).steps[0].alternatives[0][:3], ["flatpak", "uninstall", "--system"])


class PowerTests(Base):
    def test_reboot_and_poweroff_are_automatic(self):
        r = self.plan("system.power", {"verb": "reboot"})
        self.assertEqual((r.tier, r.steps[0].alternatives[0]), (ka.AUTO, ["systemctl", "reboot"]))
        p = self.plan("system.power", {"verb": "poweroff"})
        self.assertEqual((p.tier, p.steps[0].alternatives[0]), (ka.AUTO, ["systemctl", "poweroff"]))

    def test_only_those_two_verbs(self):
        for bad in ("halt", "suspend", "reboot; rm -rf /", "", "Reboot"):
            self.refused("system.power", {"verb": bad})


class ServiceTests(Base):
    def test_tiers(self):
        self.assertEqual(self.plan("service.control", {"unit": "NetworkManager.service", "verb": "restart"}).tier, ka.AUTO)
        self.assertEqual(self.plan("service.control", {"unit": "NetworkManager.service", "verb": "disable"}).tier, ka.PASSPHRASE)
        self.assertEqual(self.plan("service.control", {"unit": "nginx.service", "verb": "start"}).tier, ka.PASSPHRASE)

    def test_protected_and_malformed_units(self):
        self.refused("service.control", {"unit": "kronos.service", "verb": "stop"}, "can't be changed")
        self.plan("service.control", {"unit": "kronos.service", "verb": "status"})            # looking is fine
        for bad in ("x.service; id", "../x.service", "-x.service", "x", "a b.service", ""):
            self.refused("service.control", {"unit": bad, "verb": "start"})
        self.refused("service.control", {"unit": "NetworkManager.service", "verb": "mask"})
        p = self.plan("service.control", {"unit": "NetworkManager.service", "verb": "restart"})
        self.assertEqual(p.steps[0].alternatives[0], ["systemctl", "restart", "--", "NetworkManager.service"])


class FileTests(Base):
    def test_read_and_write_allowlists(self):
        self.assertEqual(self.plan("file.read", {"path": "/etc/hosts"}).tier, ka.AUTO)
        self.assertEqual(self.plan("file.read", {"path": "/etc/cosmos/x.conf"}).steps[0].kind, "read_file")
        w = self.plan("file.write", {"path": "/etc/hosts", "content": "127.0.0.1 localhost\n"})
        self.assertEqual((w.tier, w.steps[0].kind, w.steps[0].data), (ka.PASSPHRASE, "write_file", "127.0.0.1 localhost\n"))
        self.refused("file.read", {"path": "/etc/shadow"}, "can't be read")
        self.refused("file.write", {"path": "/etc/passwd", "content": "x"}, "can't be written")
        self.refused("file.write", {"path": "/etc/fstab", "content": "x"}, "can't be written")           # readable, not writable

    def test_paths_cannot_escape(self):
        for bad in ("/etc/cosmos/../shadow", "etc/hosts", "/etc/hosts\0", "/etc/cosmos/../../etc/passwd", "", None, 5, "/etc/hosts\n"):
            with self.assertRaises(ka.Refused, msg=repr(bad)):
                self.plan("file.read", {"path": bad})
        self.refused("file.write", {"path": "/etc/hosts", "content": "x" * (ka.MAX_FILE_BYTES + 1)}, "too large")
        self.refused("file.write", {"path": "/etc/hosts", "content": "a\0b"}, "NUL")
        self.refused("file.write", {"path": "/etc/hosts", "content": 5}, "text")


class FirewallTests(Base):
    def test_closing_is_automatic_and_opening_needs_the_passphrase(self):
        closed = self.plan("firewall.door", {"door": "ssh", "open": False})
        self.assertEqual(closed.tier, ka.AUTO)
        self.assertEqual(closed.steps[0].alternatives[0], ["firewall-cmd", "--zone=cosmos", "--remove-service=ssh"])
        self.assertEqual(closed.steps[1].alternatives[0], ["firewall-cmd", "--permanent", "--zone=cosmos", "--remove-service=ssh"])
        opened = self.plan("firewall.door", {"door": "webtop", "open": True})
        self.assertEqual(opened.tier, ka.PASSPHRASE)
        self.assertEqual(opened.steps[1].alternatives[0][-1], "--add-service=cosmos-webtop")

    def test_the_hosting_door_is_temporary_and_needs_no_passphrase(self):
        opened = self.plan("firewall.door", {"door": "play-together", "open": True})
        self.assertEqual(opened.tier, ka.AUTO)
        self.assertEqual(len(opened.steps), 1)                                             # running firewall only, nothing saved
        self.assertEqual(opened.steps[0].alternatives[0], ["firewall-cmd", "--zone=cosmos", "--add-service=cosmos-play-together", "--timeout=6h"])
        closed = self.plan("firewall.door", {"door": "play-together", "open": False})
        self.assertEqual(closed.steps[0].alternatives[0], ["firewall-cmd", "--zone=cosmos", "--remove-service=cosmos-play-together"])

    def test_the_steam_door_is_temporary_too(self):
        p = self.plan("firewall.door", {"door": "steam-remote-play", "open": True})
        self.assertEqual((p.tier, len(p.steps)), (ka.AUTO, 1))
        self.assertEqual(p.steps[0].alternatives[0][-2:], ["--add-service=cosmos-steam-remote-play", "--timeout=1h"])       # renewed by its watcher

    def test_the_steam_lan_transfer_door(self):
        p = self.plan("firewall.door", {"door": "steam-lan-transfer", "open": True})
        self.assertEqual((p.tier, len(p.steps)), (ka.AUTO, 1))
        self.assertEqual(p.steps[0].alternatives[0][-2:], ["--add-service=cosmos-steam-lan-transfer", "--timeout=1h"])

    def test_only_named_doors_and_real_booleans(self):
        for bad in ({"door": "telnet", "open": True}, {"door": "ssh"}, {"door": "ssh", "open": "yes"}, {"door": "ssh", "open": 1},
                    {"door": "22/tcp", "open": True}, {"door": "--zone=public", "open": True}, {"open": True}):
            with self.assertRaises(ka.Refused, msg=str(bad)):
                self.plan("firewall.door", bad)

    def test_the_door_table_matches_the_firewall_module(self):
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))
        import cosmos_firewall
        self.assertEqual(ka.FIREWALL_DOORS, {n: d["service"] for n, d in cosmos_firewall.DOORS.items()})
        self.assertEqual(ka.FIREWALL_TEMPORARY, {n for n, d in cosmos_firewall.DOORS.items() if d.get("temporary")})
        self.assertEqual(ka.FIREWALL_TIMEOUT, cosmos_firewall.TEMPORARY_TIMEOUT)
        self.assertEqual(ka.FIREWALL_TIMEOUTS, {n: d["timeout"] for n, d in cosmos_firewall.DOORS.items() if d.get("timeout")})
        self.assertEqual(ka.FIREWALL_ZONE, cosmos_firewall.ZONE)


class LogTests(Base):
    def test_log_read(self):
        p = self.plan("log.read", {"unit": "NetworkManager.service", "lines": 50, "boot": True})
        self.assertEqual(p.steps[0].alternatives[0], ["journalctl", "--no-pager", "-n", "50", "-b", "-u", "NetworkManager.service"])
        self.assertEqual(p.tier, ka.AUTO)
        for bad in (0, 5000, "9", True, -1):
            self.refused("log.read", {"lines": bad})


class RunTests(Base):
    def test_run_is_gated_and_an_argv_list(self):
        p = self.plan("run", {"argv": ["/usr/bin/true", "--flag"], "cwd": "/var/tmp"})
        self.assertEqual((p.tier, p.steps[0].alternatives, p.steps[0].cwd), (ka.PASSPHRASE, [["/usr/bin/true", "--flag"]], "/var/tmp"))
        self.refused("run", {"argv": "true --flag"})
        self.refused("run", {"argv": []})
        self.refused("run", {"argv": ["true", 5]})
        self.refused("run", {"argv": ["a/b"]}, "bare command")

    def test_no_shells_no_way_to_one_no_terminals(self):
        for prog in ("bash", "/bin/sh", "/usr/bin/zsh", "su", "sudo", "nsenter", "chroot", "systemd-run", "tmux", "script", "machinectl", "runuser"):
            self.refused("run", {"argv": [prog, "-c", "id"]}, "can't be started")
        self.refused("run", {"argv": ["xterm"]}, "can't be installed")                      # a terminal emulator
        self.refused("run", {"argv": ["gnome-terminal", "--"]}, "can't be installed")


class CatalogTests(unittest.TestCase):
    """The production wiring (kronos_config.app_check): the curated catalog limits installs, but not the programs `run` starts."""

    def setUp(self):
        import kronos_config
        self.ctx = ka.Context(app_check=kronos_config.app_check())

    def refused(self, action, args, contains):
        with self.assertRaises(ka.Refused) as cm:
            ka.plan(action, args, self.ctx)
        self.assertIn(contains, str(cm.exception))

    def test_only_catalog_packages_and_flatpaks_can_be_installed(self):
        self.assertEqual(ka.plan("pkg.install", {"packages": ["htop"]}, self.ctx).args["packages"], ["htop"])
        self.refused("pkg.install", {"packages": ["htop", "not-in-the-catalog"]}, "catalog")
        self.assertEqual(ka.plan("flatpak.install", {"id": "org.kde.krita"}, self.ctx).args["id"], "org.kde.krita")
        self.refused("flatpak.install", {"id": "org.mozilla.firefox"}, "catalog")

    def test_running_a_program_is_not_an_install(self):
        ka.plan("run", {"argv": ["systemctl", "status", "sshd"]}, self.ctx)                 # not a catalog package, and not blocked
        self.refused("run", {"argv": ["xterm"]}, "can't be installed")                    # still refused: a terminal emulator


class GeneralTests(Base):
    def test_unknown_actions_and_bad_shapes(self):
        for action in ("format.disk", "", None, 7, "PKG.INSTALL"):
            self.refused("x", {}, "Unknown") if False else None
            with self.assertRaises(ka.Refused):
                ka.plan(action, {}, self.ctx)
        with self.assertRaises(ka.Refused):
            ka.plan("pkg.upgrade", ["not", "an", "object"], self.ctx)

    def test_tier_overrides_can_only_make_things_stricter(self):
        strict = ka.Context(policy={"tier_overrides": {"pkg.install": "passphrase", "os.rollback": "auto"}}, app_check=real_check)
        self.assertEqual(ka.plan("pkg.install", {"packages": ["htop"]}, strict).tier, ka.PASSPHRASE)
        self.assertEqual(ka.plan("os.rollback", {}, strict).tier, ka.PASSPHRASE)                # can't be relaxed

    def test_every_step_is_argv_lists_of_strings(self):
        samples = [("pkg.install", {"packages": ["htop"]}), ("pkg.remove", {"packages": ["htop"]}), ("pkg.upgrade", {}), ("os.upgrade", {}),
                   ("os.rollback", {}), ("os.switch", {"ref": "ghcr.io/a/b:1"}), ("flatpak.install", {"id": "org.kde.krita"}),
                   ("flatpak.update", {}), ("flatpak.remove", {"id": "org.kde.krita"}), ("service.control", {"unit": "a.service", "verb": "start"}),
                   ("firewall.door", {"door": "ssh", "open": True}), ("system.power", {"verb": "reboot"}), ("log.read", {}), ("run", {"argv": ["/usr/bin/true"]})]
        for action, args in samples:
            for step in self.plan(action, args).steps:
                for argv in step.alternatives:
                    self.assertTrue(argv and all(isinstance(a, str) for a in argv), (action, argv))
        self.assertEqual(set(ka.ACTIONS), {a for a, _ in samples} | {"file.read", "file.write", "input.grant", "input.revoke"})    # (the input ones are tested in InputTests)


class InputTests(Base):
    GOOD = "/dev/input/by-id/usb-Logitech_K120-event-kbd"

    def setUp(self):
        super().setUp()
        self._real = os.path.realpath
        ka.os.path.realpath = lambda p: "/dev/input/event7" if p == self.GOOD else self._real(p)

    def tearDown(self):
        ka.os.path.realpath = self._real
        super().tearDown()

    def test_grant_needs_the_passphrase_and_touches_exactly_one_node(self):
        p = self.plan("input.grant", {"path": self.GOOD})
        self.assertEqual(p.tier, ka.PASSPHRASE)
        self.assertEqual(p.steps[0].alternatives, [["setfacl", "-m", "u:user:r", "/dev/input/event7"]])

    def test_revoke_is_automatic(self):
        p = self.plan("input.revoke", {"path": self.GOOD})
        self.assertEqual((p.tier, p.steps[0].alternatives), (ka.AUTO, [["setfacl", "-x", "u:user", "/dev/input/event7"]]))

    def test_only_keyboards_and_mice_by_id_and_only_when_they_resolve_to_an_event_node(self):
        for bad in ("/dev/input/event3", "/dev/sda", "/dev/input/by-id/../../sda-event-kbd", "/dev/input/by-id/x-event-joystick",
                    "/dev/input/by-id/x-event-kbd\n", "", None, "/etc/shadow", "/dev/input/by-id/x y-event-kbd"):
            self.refused("input.grant", {"path": bad})
            self.refused("input.revoke", {"path": bad})
        ka.os.path.realpath = lambda p: "/dev/sda"                                     # a by-id link pointing somewhere else
        self.refused("input.grant", {"path": self.GOOD}, "isn't a keyboard or mouse")


if __name__ == "__main__":
    unittest.main()
