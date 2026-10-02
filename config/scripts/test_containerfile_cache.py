"""The Containerfile's package steps use the build cache mounts (so a rebuild doesn't download everything again) and nothing in them empties it."""
import os
import re
import unittest

CF = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "Containerfile"), encoding="utf-8").read()
DNF_MOUNT = "--mount=type=cache,target=/var/cache/libdnf5,sharing=locked"


def run_steps(text):
    """[(first line number, full text)] of each RUN instruction (continued lines joined)."""
    steps, lines, i = [], text.split("\n"), 0
    while i < len(lines):
        if lines[i].startswith("RUN "):
            start, chunk = i + 1, [lines[i]]
            while chunk[-1].rstrip().endswith("\\") and i + 1 < len(lines):
                i += 1
                chunk.append(lines[i])
            steps.append((start, "\n".join(chunk)))
        i += 1
    return steps


class Cache(unittest.TestCase):
    def test_every_step_that_installs_or_upgrades_packages_uses_the_dnf_cache(self):
        bad = []
        for n, step in run_steps(CF):
            if re.search(r"\bdnf\s+(install|upgrade|group\s+install|makecache)\b", step) and "copr" not in step.split("\n")[0] and "dnf5-command" not in step:
                if DNF_MOUNT not in step.split("\n")[0] and "--mount=type=cache,target=/var/cache/libdnf5" not in step:
                    bad.append(n)
                if re.search(r"\bdnf\s+(install|upgrade|group\s+install)\b", step):
                    for m in re.finditer(r"\bdnf\s+(?:install|upgrade|group\s+install)\b[^\n&|;]*", step):
                        if "keepcache=1" not in m.group(0):
                            bad.append(n)
        self.assertEqual(sorted(set(bad)), [], "these RUN steps download packages without the cache mount / keepcache")

    def test_no_step_that_mounts_the_cache_empties_it(self):
        for n, step in run_steps(CF):
            if "type=cache" in step:
                self.assertNotRegex(step, r"dnf\s+clean\s+all", f"line {n}")
                self.assertNotRegex(step, r"rm\s+-rf?\s+/var/cache/libdnf5", f"line {n}")

    def test_pip_and_npm_use_their_caches_and_pip_is_not_told_to_skip_it(self):
        steps = dict(run_steps(CF))
        pip = [s for s in steps.values() if s.startswith("RUN") and "pip3 install" in s][0]
        self.assertIn("target=/var/cache/pip", pip)       # not /root: on the bootc base /root is a dangling symlink and the mount fails
        self.assertNotIn("--no-cache-dir", pip)
        npm = [s for s in steps.values() if "npm install -g" in s][0]
        self.assertIn("target=/var/cache/npm", npm)

    def test_the_managed_package_block_carries_the_mount(self):
        block = re.search(r"# >>> bootc-studio:dnf-packages >>>\n(RUN [^\n]*)", CF).group(1)
        self.assertIn(DNF_MOUNT, block)
        self.assertIn("--setopt=keepcache=1", block)


if __name__ == "__main__":
    unittest.main()
