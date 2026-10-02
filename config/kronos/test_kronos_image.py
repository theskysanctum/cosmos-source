"""The image really has the privilege split: the desktop account has no root path, kronos is wired up, and every file the Containerfile
copies for it exists in the repo. (The proof that it boots is a rebuild - this catches the mistakes that don't need one.)"""
import json
import os
import re
import unittest

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
CF = open(os.path.join(ROOT, "Containerfile"), encoding="utf-8").read()
MANIFEST = json.load(open(os.path.join(ROOT, "packaging", "rpm", "packages.json"), encoding="utf-8"))


def shipped(package):
    """Where each package puts its files (globs count as dest_dir/file-name), plus its symlinks."""
    entry = [p for p in MANIFEST["packages"] if p["name"] == package][0]
    paths = {e.get("dest") or e.get("dest_dir", "").rstrip("/") + "/" + os.path.basename(e.get("glob", e.get("tree", ""))) for e in entry["files"]}
    return paths | {link["dest"] for link in entry.get("links", [])}


class Image(unittest.TestCase):
    def test_the_desktop_account_is_not_in_wheel_and_has_no_sudoers(self):
        useradd = re.search(r"useradd -m -G ([\w,-]+) .* user", CF)
        self.assertIsNotNone(useradd)
        groups = useradd.group(1).split(",")
        self.assertNotIn("wheel", groups)
        self.assertIn("kronos-clients", groups)
        self.assertNotIn("NOPASSWD: ALL", CF)
        self.assertIn("rm -f /etc/sudoers.d/wheel", CF)

    def test_sudoers_lets_kronos_run_exactly_the_helper_and_nothing_else(self):
        text = open(os.path.join(ROOT, "config/kronos/kronos-sudoers")).read()
        rules = [l for l in text.splitlines() if l.strip() and not l.startswith("#") and not l.startswith("Defaults")]
        self.assertEqual(rules, ["kronos ALL=(root) NOPASSWD: /usr/libexec/kronos/act"])

    def test_every_file_copied_for_kronos_exists(self):
        for m in re.finditer(r"^COPY (.+?) (/\S+)$", CF, re.M):
            sources = m.group(1).split()
            if "kronos" in m.group(0) or "cosmos-recovery" in m.group(0) or "cosmos-admin" in m.group(0) or "cosmos_recovery" in m.group(0):
                for src in sources:
                    self.assertTrue(os.path.exists(os.path.join(ROOT, src)), f"{src} is copied by the Containerfile but is missing")

    def test_the_socket_is_for_the_client_group_only_and_the_service_is_not_root(self):
        sock = open(os.path.join(ROOT, "config/kronos/kronos.socket")).read()
        self.assertIn("SocketGroup=kronos-clients", sock)
        self.assertIn("SocketMode=0660", sock)
        svc = open(os.path.join(ROOT, "config/kronos/kronos.service")).read()
        self.assertIn("User=kronos", svc)
        self.assertIn("Requires=kronos.socket", svc)

    def test_state_and_log_belong_to_kronos_and_are_private(self):
        conf = open(os.path.join(ROOT, "config/tmpfiles/kronos.conf")).read()
        for path in ("/var/log/kronos", "/var/lib/kronos"):
            self.assertRegex(conf, rf"(?m)^d {re.escape(path)} 0750 kronos kronos")
        self.assertRegex(conf, r"(?m)^f /var/log/kronos/audit.log 0640 kronos kronos")

    def test_the_client_list_names_the_desktop_account(self):
        lines = [l.strip() for l in open(os.path.join(ROOT, "config/kronos/clients")) if l.strip() and not l.startswith("#")]
        self.assertEqual(lines, ["user"])

    def test_the_desktop_account_cannot_read_keyboards(self):
        """No `input` group (it would let any program read every keystroke, the admin passphrase included); nothing else hands it out."""
        useradd = re.search(r"useradd -m -G ([\w,-]+) .* user", CF).group(1).split(",")
        self.assertNotIn("input", useradd)
        rules = "".join(open(os.path.join(ROOT, "packaging", "rpm", "data", "files", "usr", "lib", "udev", "rules.d", name), encoding="utf-8").read()
                        for name in ("71-cosmos-controller-leds.rules", "72-cosmos-controller-input.rules"))
        self.assertNotRegex(CF + rules, r'GROUP="input"')
        self.assertNotIn("chgrp input", CF + rules)
        self.assertIn("chgrp cosmos-pads", rules)
        self.assertIn("cosmos-pads", useradd)
        self.assertIn("/usr/lib/udev/rules.d/72-cosmos-controller-input.rules", shipped("cosmos-controllers"))

    def test_recovery_cannot_take_the_login_screen_away_at_a_normal_boot(self):
        svc = open(os.path.join(ROOT, "config/kronos/cosmos-recovery.service")).read()
        self.assertNotRegex(svc, r"(?m)^Conflicts=")                       # a Conflicts= made systemd drop greetd from every normal boot
        drop = open(os.path.join(ROOT, "config/kronos/greetd-not-in-recovery.conf")).read()
        self.assertIn("ConditionKernelCommandLine=!cosmos.recovery=1", drop)
        self.assertIn("/usr/lib/systemd/system/greetd.service.d/10-not-in-recovery.conf", shipped("cosmos-recovery"))

    def test_programs_cannot_read_each_others_memory(self):
        conf = open(os.path.join(ROOT, "config/kronos/50-cosmos-ptrace.conf")).read()
        self.assertRegex(conf, r"(?m)^kernel\.yama\.ptrace_scope = 1$")
        self.assertIn("/usr/lib/sysctl.d/50-cosmos-ptrace.conf", shipped("cosmos-kronos"))

    def test_the_passphrase_is_asked_by_the_daemon_through_the_session_agent(self):
        for path in ("/usr/lib/cosmos/kronos-agent.py", "/usr/lib/cosmos/kronos_prompt_ui.py", "/usr/bin/kronos-agent"):
            self.assertIn(path, shipped("cosmos-kronos"))
        apps = open(os.path.join(ROOT, "config/scripts/cosmos-session-apps")).read()
        self.assertRegex(apps, r"(?m)^kronos-agent &$")
        self.assertIn("kronos-agent &", open(os.path.join(ROOT, "config/scripts/gamebox-session")).read())
        self.assertIn("AmbientCapabilities=CAP_SYS_PTRACE", open(os.path.join(ROOT, "config/kronos/kronos.service")).read())

    def test_the_client_never_opens_its_own_passphrase_window(self):
        """`ask` (what every program uses) prompts only in a terminal; the window belongs to the agent."""
        src = open(os.path.join(ROOT, "config/scripts/cosmos_kronos.py")).read()
        self.assertIn("prompt = prompt or tty_prompt", src)
        self.assertNotIn("prompt = prompt or default_prompt", src)

    def test_no_program_still_calls_sudo(self):
        """Programs that run on CosmOS ask kronos; sudo appears only as the fallback for machines without it."""
        import glob
        offenders = []
        for path in glob.glob(os.path.join(ROOT, "config/scripts/*.py")) + glob.glob(os.path.join(ROOT, "config/scripts/cosmos-*")):
            if os.path.basename(path).startswith("test_") or os.path.isdir(path):
                continue
            for i, line in enumerate(open(path, encoding="utf-8", errors="replace"), 1):
                code = line.split("#", 1)[0]
                if re.search(r"""["']sudo["']|\bsudo -n\b|\bsudo rpm-ostree\b""", code):
                    offenders.append(f"{os.path.basename(path)}:{i}")
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
