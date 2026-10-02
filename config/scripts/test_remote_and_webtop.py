"""Checks for the SSH and Webtop wiring: the quadlet is valid and local-only, the launcher script and desktop entries are well
formed, sshd is key-only, and the Containerfile installs and enables it all."""
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
GENERATOR = "/usr/lib/systemd/user-generators/podman-user-generator"


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as fh:
        return fh.read()


class WebtopTests(unittest.TestCase):
    def test_the_quadlet_generates_a_local_only_service(self):
        if not os.path.exists(GENERATOR):
            self.skipTest("podman's quadlet generator isn't installed")
        d = tempfile.mkdtemp()
        shutil.copy(os.path.join(ROOT, "config", "webtop", "cosmos-webtop.container"), d)
        out = subprocess.run([GENERATOR, "--user", "--dryrun"], env=dict(os.environ, QUADLET_UNIT_DIRS=d), capture_output=True, text=True).stdout
        self.assertIn("---cosmos-webtop.service---", out)
        self.assertIn("ExecStart=", out)
        self.assertRegex(out, r"--publish 127\.0\.0\.1:3000:3000")                     # never 0.0.0.0
        self.assertNotRegex(out, r"--publish (0\.0\.0\.0|:)?\d*:?3000")
        self.assertIn("--name cosmos-webtop", out)
        self.assertNotIn("--privileged", out)
        self.assertIn("ExecStartPre=/usr/bin/mkdir -p", out)                          # the config folder exists before the mount

    def test_it_is_started_on_demand_not_at_boot(self):
        text = read("config", "webtop", "cosmos-webtop.container")
        self.assertNotIn("[Install]", text)
        self.assertNotIn("WantedBy", text)

    def test_the_launcher_script(self):
        path = os.path.join(HERE, "cosmos-webtop")
        self.assertEqual(subprocess.run(["bash", "-n", path]).returncode, 0)
        text = open(path).read()
        for word in ("start_it", "systemctl --user start", "cosmos-task", "--app=", "podman image exists", "stop)", "status)"):
            self.assertIn(word, text)
        self.assertEqual(subprocess.run(["bash", path, "bogus"], capture_output=True, text=True).returncode, 2)

    def test_desktop_entries_are_valid(self):
        if not shutil.which("desktop-file-validate"):
            self.skipTest("desktop-file-validate isn't installed")
        for name in ("cosmos-webtop.desktop",):
            done = subprocess.run(["desktop-file-validate", os.path.join(ROOT, "config", "webtop", name)], capture_output=True, text=True)
            self.assertEqual(done.returncode, 0, done.stdout + done.stderr)

    def test_the_webtop_is_not_a_terminal_emulator(self):
        text = read("config", "webtop", "cosmos-webtop.desktop")
        self.assertNotIn("TerminalEmulator", text)


class SshTests(unittest.TestCase):
    def test_sshd_is_key_only_for_one_account(self):
        text = read("config", "ssh", "50-cosmos.conf")
        for line in ("PasswordAuthentication no", "KbdInteractiveAuthentication no", "PermitRootLogin no", "PubkeyAuthentication yes",
                     "AllowUsers user", "AllowTcpForwarding yes"):
            self.assertIn(line, text)
        self.assertNotRegex(text, r"(?m)^PasswordAuthentication yes|^PermitRootLogin (yes|prohibit)|^PermitEmptyPasswords yes")

    def test_the_shipped_authorized_keys_has_no_keys_or_secrets(self):
        lines = [l for l in read("config", "ssh", "authorized_keys").splitlines() if l.strip() and not l.startswith("#")]
        self.assertEqual(lines, [])                      # the file in the repo is only instructions; real keys are added on the machine
        self.assertNotIn("PRIVATE KEY", read("config", "ssh", "authorized_keys"))

    def test_sshd_config_passes_sshd_when_available(self):
        sshd = shutil.which("sshd") or ("/usr/sbin/sshd" if os.path.exists("/usr/sbin/sshd") else None)
        if not sshd:
            self.skipTest("sshd isn't installed here")
        d = tempfile.mkdtemp()
        cfg = os.path.join(d, "sshd_config")
        open(cfg, "w").write(read("config", "ssh", "50-cosmos.conf"))
        done = subprocess.run([sshd, "-t", "-f", cfg], capture_output=True, text=True)
        self.assertNotIn("Bad configuration option", done.stderr)
        self.assertNotIn("Unsupported option", done.stderr)


class ContainerfileTests(unittest.TestCase):
    def setUp(self):
        self.text = read("Containerfile")

    def test_the_image_enables_ssh_for_the_desktop_account_and_the_package_ships_everything(self):
        for needle in ("/home/user/.ssh/authorized_keys", "cosmos-remote-enable user", "chmod 0700 /home/user/.ssh", "chmod 0600 /home/user/.ssh/authorized_keys"):
            self.assertIn(needle, self.text)
        manifest = json.loads(read("packaging/rpm/packages.json"))
        remote = [p for p in manifest["packages"] if p["name"] == "cosmos-remote"][0]
        shipped = {e.get("dest") or e.get("dest_dir", "") + "/" + os.path.basename(e.get("glob", "")) for e in remote["files"]}
        for path in ("/usr/share/cosmos/remote/sshd-50-cosmos.conf", "/etc/containers/systemd/users/cosmos-webtop.container",
                     "/usr/share/applications/cosmos-webtop.desktop", "/usr/share/applications/cosmos-remote.desktop", "/usr/lib/cosmos/cosmos_webtop_access.py",
                     "/usr/bin/cosmos-remote-enable"):
            self.assertIn(path, shipped)
        self.assertIn("cosmos-firewall", remote["requires"])
        self.assertIn("systemctl enable sshd.service", read("packaging/rpm/data/files/usr/bin/cosmos-remote-enable"))

    def test_the_packages_are_in_the_managed_list(self):
        body = re.search(r"# >>> bootc-studio:dnf-packages >>>\n(.*?)# <<< bootc-studio:dnf-packages <<<", self.text, re.S).group(1)
        packages = {t.strip("\\ ") for t in body.replace("RUN dnf install -y", "").split()}
        for name in ("openssh-server", "nodejs22-bin", "nodejs22-npm-bin", "podman"):
            self.assertIn(name, packages)
        self.assertIn("npm install -g @anthropic-ai/claude-code", self.text)               # Claude Code stays

    def test_the_terminal_gate_still_runs_last(self):
        self.assertGreater(self.text.index("find-terminals\n") if "find-terminals\n" in self.text else self.text.rindex("find-terminals"),
                           self.text.index("cosmos-remote-enable user"))


if __name__ == "__main__":
    unittest.main()
