"""Tests for the CosmOS firewall: the zone and service files (parsed by firewalld's own reader when it's installed), the door commands,
status parsing, and that the Containerfile installs and enables it."""
import json
import os
import re
import sys
import unittest
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, HERE)
import cosmos_firewall as fw

ZONE = os.path.join(ROOT, "config", "firewalld", "zones", "cosmos.xml")
SERVICE = os.path.join(ROOT, "config", "firewalld", "services", "cosmos-webtop.xml")
PLAY = os.path.join(ROOT, "config", "firewalld", "services", "cosmos-play-together.xml")
STEAM = os.path.join(ROOT, "config", "firewalld", "services", "cosmos-steam-remote-play.xml")
STEAM_LAN = os.path.join(ROOT, "config", "firewalld", "services", "cosmos-steam-lan-transfer.xml")
GAMEBOX_STREAMING = os.path.join(ROOT, "gamebox", "streaming")


class FilesTests(unittest.TestCase):
    def test_the_zone_lets_in_only_ssh_and_the_dhcpv6_reply(self):
        root = ET.parse(ZONE).getroot()
        self.assertEqual(root.tag, "zone")
        self.assertEqual({e.get("name") for e in root.findall("service")}, {"ssh", "dhcpv6-client"})
        for forbidden in ("port", "source-port", "protocol", "rule", "forward-port", "masquerade", "interface", "source", "forward"):
            self.assertEqual(root.findall(forbidden), [], forbidden)                      # no port ranges, no blanket rules
        self.assertIsNone(root.get("target"))                                            # the default target: reject what isn't listed
        self.assertNotIn(root.get("target"), ("ACCEPT",))

    def test_the_webtop_service_is_one_https_port(self):
        root = ET.parse(SERVICE).getroot()
        self.assertEqual([(p.get("protocol"), p.get("port")) for p in root.findall("port")], [("tcp", "3001")])
        self.assertEqual(root.findall("destination"), [])

    def test_the_webtop_door_is_not_in_the_zone_by_default_and_the_plain_port_is_never_a_door(self):
        zone_services = {e.get("name") for e in ET.parse(ZONE).getroot().findall("service")}
        self.assertNotIn("cosmos-webtop", zone_services)
        self.assertNotIn("3000/tcp", [d["port"] for d in fw.DOORS.values()])

    def test_firewalld_itself_accepts_the_files(self):
        try:
            from firewall.core.io.service import service_reader
            from firewall.core.io.zone import zone_reader
        except ImportError:
            self.skipTest("firewalld's python library isn't installed")
        zone = zone_reader("cosmos.xml", os.path.dirname(ZONE))
        self.assertEqual(sorted(zone.services), ["dhcpv6-client", "ssh"])
        self.assertEqual(service_reader("cosmos-webtop.xml", os.path.dirname(SERVICE)).ports, [("3001", "tcp")])

    def test_the_hosting_service_has_exactly_sunshines_streaming_ports_and_not_its_web_port(self):
        root = ET.parse(PLAY).getroot()
        ports = {(p.get("protocol"), p.get("port")) for p in root.findall("port")}
        self.assertEqual(ports, {("tcp", "47984"), ("tcp", "47989"), ("tcp", "48010"), ("udp", "47998"), ("udp", "47999"),
                                 ("udp", "48000"), ("udp", "48002"), ("udp", "48010")})
        self.assertNotIn(("tcp", "47990"), ports)                                        # the web/API port stays local
        self.assertEqual({int(p) for _proto, p in ports}, set(fw.DOORS["play-together"]["ports"]) | {48010})
        self.assertNotIn("cosmos-play-together", {e.get("name") for e in ET.parse(ZONE).getroot().findall("service")})   # never open by default
        try:
            from firewall.core.io.service import service_reader
        except ImportError:
            return
        self.assertEqual(len(service_reader("cosmos-play-together.xml", os.path.dirname(PLAY)).ports), 8)

    def test_the_steam_service_is_valves_published_remote_play_list_and_never_in_the_zone(self):
        root = ET.parse(STEAM).getroot()
        ports = {(p.get("protocol"), p.get("port")) for p in root.findall("port")}
        self.assertEqual(ports, {("tcp", "27036"), ("tcp", "27037"), ("udp", "27031"), ("udp", "27036")})
        self.assertNotIn("cosmos-steam-remote-play", {e.get("name") for e in ET.parse(ZONE).getroot().findall("service")})
        self.assertEqual({int(p) for _proto, p in ports}, set(fw.DOORS["steam-remote-play"]["ports"]))
        self.assertTrue(fw.DOORS["steam-remote-play"]["temporary"])
        opened = fw.door_commands("steam-remote-play", True)
        self.assertEqual((len(opened), "--permanent" in opened[0], "--timeout=1h" in opened[0]), (1, False, True))    # short: its watcher renews it
        try:
            from firewall.core.io.service import service_reader
        except ImportError:
            return
        self.assertEqual(len(service_reader("cosmos-steam-remote-play.xml", os.path.dirname(STEAM)).ports), 4)

    def test_the_steam_lan_transfer_service_and_door(self):
        root = ET.parse(STEAM_LAN).getroot()
        self.assertEqual({(p.get("protocol"), p.get("port")) for p in root.findall("port")}, {("tcp", "27040"), ("udp", "27036")})
        self.assertNotIn("cosmos-steam-lan-transfer", {e.get("name") for e in ET.parse(ZONE).getroot().findall("service")})
        door = fw.DOORS["steam-lan-transfer"]
        self.assertTrue(door["temporary"])
        self.assertEqual(set(door["ports"]), {27040, 27036})
        cmd = fw.door_commands("steam-lan-transfer", True)
        self.assertEqual((len(cmd), "--permanent" in cmd[0], "--timeout=1h" in cmd[0]), (1, False, True))
        try:
            from firewall.core.io.service import service_reader
        except ImportError:
            return
        self.assertEqual(len(service_reader("cosmos-steam-lan-transfer.xml", os.path.dirname(STEAM_LAN)).ports), 2)

    def test_only_steam_has_a_game_store_door_the_others_are_outgoing_only(self):
        store_doors = [n for n in fw.DOORS if n.startswith("steam")]
        self.assertEqual(sorted(store_doors), ["steam-lan-transfer", "steam-remote-play"])
        for name in fw.DOORS:
            for word in ("epic", "gog", "battle", "ubisoft", "itch", "heroic"):
                self.assertNotIn(word, name)

    def test_every_door_has_a_definition(self):
        builtin = {"ssh"}
        for name, door in fw.DOORS.items():
            self.assertTrue(door["service"] in builtin or os.path.exists(os.path.join(ROOT, "config", "firewalld", "services", door["service"] + ".xml")), name)


class CommandTests(unittest.TestCase):
    def test_commands_change_the_running_firewall_and_the_saved_one(self):
        run, saved = fw.door_commands("webtop", True)
        self.assertEqual(run, ["firewall-cmd", "--zone=cosmos", "--add-service=cosmos-webtop"])
        self.assertEqual(saved, ["firewall-cmd", "--permanent", "--zone=cosmos", "--add-service=cosmos-webtop"])
        self.assertEqual(fw.door_commands("ssh", False)[0], ["firewall-cmd", "--zone=cosmos", "--remove-service=ssh"])

    def test_the_temporary_door_is_runtime_only_and_expires(self):
        opened = fw.door_commands("play-together", True)
        self.assertEqual(len(opened), 1)                                                  # nothing saved to disk
        self.assertNotIn("--permanent", opened[0])
        self.assertIn("--timeout=6h", opened[0])
        closed = fw.door_commands("play-together", False)
        self.assertEqual(len(closed), 1)
        self.assertNotIn("--timeout=6h", closed[0])
        self.assertIn("--remove-service=cosmos-play-together", closed[0])

    def test_only_named_doors(self):
        for bad in ("telnet", "22/tcp", "", "ssh;id", "--zone=public"):
            with self.assertRaises(fw.DoorError):
                fw.door_commands(bad, True)

    def test_set_door_goes_through_kronos_and_reports_why_not(self):
        seen = []
        ok = lambda action, args: (seen.append((action, args)), {"status": "ok"})[1]
        self.assertEqual(fw.set_door("ssh", True, ok), (True, ""))
        self.assertEqual(seen, [("firewall.door", {"door": "ssh", "open": True})])
        self.assertEqual(fw.set_door("webtop", False, ok), (True, ""))
        self.assertEqual(fw.set_door("webtop", True, lambda a, args: {"status": "refused", "reason": "Cancelled - the admin passphrase wasn't entered."}),
                         (False, "Cancelled - the admin passphrase wasn't entered."))
        ok_, msg = fw.set_door("webtop", True, lambda a, args: {"status": "failed", "output": "Error: not allowed\n"})
        self.assertEqual((ok_, msg), (False, "Error: not allowed"))
        self.assertEqual(fw.set_door("ssh", True, lambda a, args: {"status": "unavailable", "reason": "The admin service isn't available, so nothing was done."})[0], False)
        with self.assertRaises(fw.DoorError):
            fw.set_door("telnet", True, ok)

    def test_the_door_names_are_the_ones_kronos_knows(self):
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "kronos"))
        import kronos_actions as ka
        self.assertEqual(set(fw.DOORS), set(ka.FIREWALL_DOORS))


class StatusTests(unittest.TestCase):
    def runner(self, state_rc=0, services="ssh dhcpv6-client", ss="", systemd_active=False):
        class R:
            def __init__(self, rc, out=""):
                self.returncode, self.stdout, self.stderr = rc, out, ""

        def run(cmd, **kw):
            if cmd[0] == "systemctl":
                return R(0 if systemd_active else 3)
            if cmd[0] == "firewall-cmd" and cmd[1] == "--state":
                return R(state_rc)
            if cmd[0] == "firewall-cmd":
                return R(0, services + "\n")
            return R(0, ss)
        return run

    def test_status_and_description(self):
        st = fw.status(self.runner())
        self.assertEqual((st["running"], st["doors"], st["unexpected"]), (True, {n: n == "ssh" for n in fw.DOORS}, []))
        self.assertIn("SSH sign-in (22/tcp)", fw.describe(st))
        self.assertNotIn("Webtop", fw.describe(st))
        both = fw.status(self.runner(services="ssh cosmos-webtop dhcpv6-client"))
        self.assertIn("Webtop over HTTPS (3001/tcp)", fw.describe(both))

    def test_a_service_nobody_set_up_is_called_out(self):
        st = fw.status(self.runner(services="ssh samba dhcpv6-client"))
        self.assertEqual(st["unexpected"], ["samba"])
        self.assertIn("samba", fw.describe(st))

    def test_not_running(self):
        st = fw.status(self.runner(state_rc=252))
        self.assertFalse(st["running"])
        self.assertEqual(fw.describe(st), "The firewall is not running.")

    def test_an_ordinary_user_who_is_refused_still_sees_the_firewall_running(self):
        """firewall-cmd is refused for an unprivileged user; systemd says the service is up and the zone's file says which doors are configured."""
        import tempfile
        os.makedirs(os.path.expanduser("~/.placements/tests"), exist_ok=True)
        with tempfile.TemporaryDirectory(dir=os.path.expanduser("~/.placements/tests")) as folder:
            with open(os.path.join(folder, "cosmos.xml"), "w") as fh:
                fh.write('<zone target="%%REJECT%%"><service name="ssh"/><service name="cosmos-webtop"/><service name="dhcpv6-client"/></zone>')
            st = fw.status(self.runner(state_rc=252, systemd_active=True), zone_dirs=(folder,))
        self.assertTrue(st["running"])
        self.assertTrue(st["doors"]["ssh"])
        self.assertTrue(st["doors"]["webtop"])
        self.assertNotIn("not running", fw.describe(st))

    def test_refused_and_not_active_is_not_running(self):
        self.assertFalse(fw.status(self.runner(state_rc=252, systemd_active=False), zone_dirs=("/nonexistent",))["running"])

    def test_active_but_no_zone_file_is_running_with_nothing_known(self):
        st = fw.status(self.runner(state_rc=252, systemd_active=True), zone_dirs=("/nonexistent",))
        self.assertTrue(st["running"])
        self.assertFalse(st["zone_ok"])

    def test_audit_lists_network_listeners_and_what_happens_to_each(self):
        ss = ("tcp LISTEN 0 128 0.0.0.0:22 0.0.0.0:*\n"
              "tcp LISTEN 0 128 127.0.0.1:3000 0.0.0.0:*\n"
              "tcp LISTEN 0 128 [::]:8080 [::]:*\n"
              "udp UNCONN 0 0 0.0.0.0:5353 0.0.0.0:*\n"
              "tcp LISTEN 0 128 [::1]:631 [::]:*\n")
        listed = fw.listeners(self.runner(ss=ss))
        self.assertEqual([(p, port) for p, _a, port in listed], [("tcp", 22), ("tcp", 8080), ("udp", 5353)])       # loopback ones are left out
        text = fw.audit(self.runner(ss=ss))
        self.assertRegex(text, r"tcp\s+0\.0\.0\.0:22\s+let in")
        self.assertRegex(text, r"tcp\s+::\:8080\s+rejected by the firewall")
        stopped = fw.audit(self.runner(state_rc=252, ss=ss))
        self.assertIn("NOT protected", stopped)                                               # never claims a rejection it can't back up
        self.assertRegex(text, r"udp\s+0\.0\.0\.0:5353\s+rejected by the firewall")


class GameBoxHookTests(unittest.TestCase):
    """GameBox's Sunshine manager opens the door when it hosts and closes it when it stops."""

    def setUp(self):
        import importlib
        import tempfile
        for extra in (GAMEBOX_STREAMING, os.path.join(ROOT, "gamebox")):
            if extra not in sys.path:
                sys.path.insert(0, extra)
        try:
            self.sm = importlib.import_module("sunshine_manager")
        except Exception as exc:                                    # GameBox's own deps (requests, ...) may be missing
            self.skipTest(f"sunshine_manager isn't importable here: {exc}")
        from pathlib import Path
        tmp = Path(tempfile.mkdtemp())
        self.sm.PID_PATH, self.sm.LOG_PATH = tmp / "pid", tmp / "log"
        self.sm.ensure_config = lambda: None
        self.calls = []

        class Fake:
            @staticmethod
            def set_door(name, open_):
                self_calls.append((name, open_))
                return True, ""
        self_calls = self.calls
        self.sm._firewall = Fake

    def test_start_opens_and_stop_closes(self):
        import os as _os
        import subprocess

        class Proc:
            pid = _os.getpid()                                         # a pid that exists, so is_running() is true
        original = subprocess.Popen
        self.sm.subprocess.Popen = lambda *a, **k: Proc()
        self.sm.time.sleep = lambda s: None
        try:
            self.assertTrue(self.sm.start())
        finally:
            self.sm.subprocess.Popen = original
        self.assertEqual(self.calls, [("play-together", True)])
        self.assertTrue(self.sm.start())                               # already running: the door is renewed
        self.assertEqual(self.calls[-1], ("play-together", True))
        killed = []
        real_kill = _os.kill
        _os.kill = lambda pid, sig: killed.append(sig) if sig else None
        try:
            self.sm.stop()
        finally:
            _os.kill = real_kill
        self.assertEqual(self.calls[-1], ("play-together", False))

    def test_a_missing_or_broken_firewall_never_stops_hosting(self):
        self.sm._firewall = False
        self.sm._door(True)                                            # no firewall module: quietly nothing

        class Broken:
            @staticmethod
            def set_door(name, open_):
                raise RuntimeError("boom")
        self.sm._firewall = Broken
        self.sm._door(True)                                            # raises inside: logged, not propagated


class SteamWatcherTests(unittest.TestCase):
    """cosmos_steam_door: the door follows Steam, only when switched on."""

    def setUp(self):
        import cosmos_steam_door as sd
        self.sd = sd
        self.t = [0.0]
        self.state = {"armed": True, "steam": True, "fw_running": True, "door_open": False, "fail": False}
        self.calls = []

        def set_door(name, open_):
            self.calls.append((name, open_))
            if self.state["fail"]:
                return False, "no sudo"
            self.state["door_open"] = open_
            return True, ""

        def status():
            return {"running": self.state["fw_running"], "doors": {"steam-remote-play": self.state["door_open"]}}
        self.w = sd.Watcher(armed=lambda: self.state["armed"], running=lambda: self.state["steam"], set_door=set_door, status=status,
                            now=lambda: self.t[0])

    def test_opens_when_steam_runs_and_closes_when_it_quits(self):
        self.assertEqual(self.w.tick(), "opened")
        self.assertEqual(self.w.tick(), "")                                            # nothing more to do
        self.state["steam"] = False
        self.assertEqual(self.w.tick(), "closed")
        self.assertEqual(self.calls, [("steam-remote-play", True), ("steam-remote-play", False)])

    def test_nothing_at_all_happens_while_it_is_switched_off(self):
        self.state["armed"] = False
        for _ in range(5):
            self.assertEqual(self.w.tick(), "")
        self.assertEqual(self.calls, [])                                               # no firewall calls, so no sudo either

    def test_switching_off_closes_it_even_with_steam_running(self):
        self.w.tick()
        self.state["armed"] = False
        self.assertEqual(self.w.tick(), "closed")

    def test_steam_not_running_keeps_it_shut_even_when_switched_on(self):
        self.state["steam"] = False
        self.assertEqual(self.w.tick(), "")
        self.assertEqual(self.calls, [])

    def test_the_door_is_renewed_before_its_timeout(self):
        self.w.tick()
        self.t[0] = self.sd.RENEW_S - 1
        self.assertEqual(self.w.tick(), "")
        self.t[0] = self.sd.RENEW_S + 1
        self.assertEqual(self.w.tick(), "renewed")
        self.assertEqual(self.calls[-2:], [("steam-remote-play", False), ("steam-remote-play", True)])
        self.assertLess(self.sd.RENEW_S, 3600)                                         # well inside the one-hour timeout

    def test_a_restarted_firewall_is_noticed(self):
        self.w.tick()
        self.state["door_open"] = False                                                # firewalld restarted and forgot it
        self.t[0] = self.sd.VERIFY_S + 1
        self.assertEqual(self.w.tick(), "fixed")
        self.assertTrue(self.state["door_open"])

    def test_a_failed_change_is_retried_not_forgotten(self):
        self.state["fail"] = True
        self.assertEqual(self.w.tick(), "")
        self.assertEqual(self.w.last_error, "no sudo")
        self.state["fail"] = False
        self.assertEqual(self.w.tick(), "opened")

    def test_shutdown_closes_what_it_opened_and_only_that(self):
        self.w.shutdown()
        self.assertEqual(self.calls, [])
        self.w.tick()
        self.w.shutdown()
        self.assertEqual(self.calls[-1], ("steam-remote-play", False))

    def test_steam_detection_reads_process_names(self):
        import tempfile
        proc = tempfile.mkdtemp()
        for pid, name in (("100", "bash"), ("200", "steamwebhelper"), ("notapid", "steam")):
            os.makedirs(os.path.join(proc, pid))
            open(os.path.join(proc, pid, "comm"), "w").write(name + "\n")
        self.assertFalse(self.sd.steam_running(proc))                                  # a helper alone (or a stray name) isn't Steam
        os.makedirs(os.path.join(proc, "300"))
        open(os.path.join(proc, "300", "comm"), "w").write("steam\n")
        self.assertTrue(self.sd.steam_running(proc))
        self.assertFalse(self.sd.steam_running(os.path.join(proc, "missing")))

    def test_each_steam_door_has_its_own_switch_and_watcher(self):
        self.assertEqual(self.sd.DOORS, ("steam-remote-play", "steam-lan-transfer"))
        self.assertNotEqual(self.sd.arm_file("steam-remote-play", "/c"), self.sd.arm_file("steam-lan-transfer", "/c"))
        calls = []
        armed = {"steam-lan-transfer": True, "steam-remote-play": False}
        watchers = [self.sd.Watcher(door, armed=(lambda d=door: armed[d]), running=lambda: True,
                                    set_door=lambda n, o: (calls.append((n, o)), (True, ""))[1], status=lambda: {}, now=lambda: 0.0)
                    for door in self.sd.DOORS]
        for w in watchers:
            w.tick()
        self.assertEqual(calls, [("steam-lan-transfer", True)])                          # only the switched-on door opened

    def test_arming_is_a_small_file(self):
        import tempfile
        path = os.path.join(tempfile.mkdtemp(), "cfg", "steam-remote-play")
        self.assertFalse(self.sd.is_armed(path))
        self.sd.arm(path)
        self.assertTrue(self.sd.is_armed(path))
        self.sd.disarm(path)
        self.sd.disarm(path)
        self.assertFalse(self.sd.is_armed(path))


class ContainerfileTests(unittest.TestCase):
    def test_the_package_ships_the_zone_and_doors_and_the_image_turns_it_on(self):
        text = open(os.path.join(ROOT, "Containerfile"), encoding="utf-8").read()
        self.assertIn("cosmos-firewall-enable", text)
        manifest = json.load(open(os.path.join(ROOT, "packaging", "rpm", "packages.json"), encoding="utf-8"))
        package = [p for p in manifest["packages"] if p["name"] == "cosmos-firewall"][0]
        shipped = {e.get("dest") or e.get("dest_dir", "") + "/" + os.path.basename(e.get("glob", "")) for e in package["files"]}
        for path in ("/usr/lib/firewalld/zones/cosmos.xml", "/usr/lib/firewalld/services/cosmos-webtop.xml", "/usr/lib/firewalld/services/cosmos-play-together.xml",
                     "/usr/lib/firewalld/services/cosmos-steam-remote-play.xml", "/usr/lib/firewalld/services/cosmos-steam-lan-transfer.xml",
                     "/usr/lib/cosmos/cosmos_firewall.py", "/usr/lib/cosmos/cosmos_steam_door.py", "/usr/bin/cosmos-firewall-enable"):
            self.assertIn(path, shipped)
        script = open(os.path.join(ROOT, "packaging", "rpm", "data", "files", "usr", "bin", "cosmos-firewall-enable"), encoding="utf-8").read()
        for needle in ("firewall-offline-cmd --set-default-zone", "systemctl enable firewalld.service"):
            self.assertIn(needle, script)
        body = re.search(r"# >>> bootc-studio:dnf-packages >>>\n(.*?)# <<< bootc-studio:dnf-packages <<<", text, re.S).group(1)
        self.assertIn("firewalld", {t.strip("\\ ") for t in body.replace("RUN dnf install -y", "").split()})


if __name__ == "__main__":
    unittest.main()
