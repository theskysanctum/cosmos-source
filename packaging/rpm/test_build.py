"""Tests for build.py: the manifest is sound, its checks catch mistakes, and staging and spec generation are right (no rpmbuild needed)."""
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
SCRATCH = Path.home() / ".placements" / "tests"
SCRATCH.mkdir(parents=True, exist_ok=True)
tempfile.tempdir = str(SCRATCH)            # scratch files live in ~/.placements, not /tmp
import build  # noqa: E402


class Tree(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        for name in ("COPYING", "a.py", "b.py", "test_a.py", "run"):
            (self.root / name).write_text("x = 1\n")

    def manifest(self, *packages):
        return {"version": "1.0", "packages": list(packages)}


class RealManifest(unittest.TestCase):
    def test_the_real_manifest_has_no_problems(self):
        self.assertEqual(build.problems(build.load()), [])

    def test_every_package_is_installable_on_its_own_terms(self):
        manifest = build.load()
        names = {p["name"] for p in manifest["packages"]}
        for package in manifest["packages"]:
            self.assertTrue(package["summary"] and package.get("description"), package["name"])
            for dep in package.get("requires", []):
                if dep.startswith("cosmos-"):
                    self.assertIn(dep, names)
            for link in package.get("links", []):                     # every symlink points at a file some package installs
                owned = {d for p in manifest["packages"] for _s, d, _m, _c in build.expand_files(p)}
                self.assertIn(link["target"], owned, f"{link['dest']} -> {link['target']}")


class Completeness(unittest.TestCase):
    """The mistake a split invites: a package whose program imports a module that some other package ships. Checked by reading the code."""

    @staticmethod
    def imported_names(path):
        import ast
        try:
            tree = ast.parse(Path(path).read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError, OSError):
            return set()
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                names.add(node.module.split(".")[0])
        return names

    def test_every_repo_module_a_package_imports_is_shipped_by_it_or_a_dependency(self):
        manifest = build.load()
        packages = {p["name"]: p for p in manifest["packages"]}
        repo_modules = {}                                       # module name -> the repo file, for the modules that live beside the programs
        for folder in ("config/scripts", "config/kronos", "config/input"):
            for f in (build.ROOT / folder).glob("*.py"):
                repo_modules.setdefault(f.stem.replace("-", "_"), f)

        def provided(name, seen=None):
            seen = seen if seen is not None else set()
            if name in seen or name not in packages:
                return set()
            seen.add(name)
            mods = {Path(d).stem.replace("-", "_") for _s, d, _m, _c in build.expand_files(packages[name]) if d.endswith(".py")}
            for dep in packages[name].get("requires", []):
                mods |= provided(dep.split()[0], seen)
            return mods

        missing = []
        for name, package in packages.items():
            have = provided(name)
            for source, dest, _mode, _config in build.expand_files(package):
                if not dest.endswith(".py") and "/usr/lib/cosmos/" not in dest:
                    continue
                if not (source.suffix == ".py" or dest.startswith("/usr/lib/cosmos/")):
                    continue
                for module in self.imported_names(source):
                    if module in repo_modules and module not in have and module != Path(dest).stem.replace("-", "_"):
                        missing.append(f"{name}: {Path(dest).name} imports {module}, which no package it depends on ships")
        self.assertEqual(sorted(set(missing)), [])


class DesktopEntries(unittest.TestCase):
    """A menu entry that runs nothing is invisible to a smoke test that only checks files exist."""

    def test_every_desktop_entry_runs_a_command_some_package_installs(self):
        import configparser
        manifest = build.load()
        commands = {Path(link["dest"]).name for p in manifest["packages"] for link in p.get("links", []) if link["dest"].startswith("/usr/bin/")}
        commands |= {Path(d).name for p in manifest["packages"] for _s, d, _m, _c in build.expand_files(p) if d.startswith("/usr/bin/")}
        system = {"python3", "flatpak", "env", "sh", "bash", "xdg-open"}                          # provided by Fedora itself
        bad = []
        for package in manifest["packages"]:
            for source, dest, _mode, _config in build.expand_files(package):
                if not dest.endswith(".desktop"):
                    continue
                parser = configparser.RawConfigParser(strict=False)
                parser.read_string(build.transformed(source, dest, package).decode("utf-8"))          # as staged, with the layout replacements applied
                for key in ("Exec", "TryExec"):
                    value = parser.get("Desktop Entry", key, fallback="")
                    if not value:
                        continue
                    if "/usr/local" in value:
                        bad.append(f"{dest}: {key}={value} points into /usr/local, which a package does not install to")
                    words = [w for w in value.split() if not w.startswith("-") and "=" not in w]
                    first = Path(words[0]).name if words else ""
                    # TryExec often names an optional program (waydroid, qemu) so the entry hides until it is installed; only Exec must be ours
                    if key == "Exec" and first and first not in commands and first not in system and not first.startswith("%"):
                        bad.append(f"{dest}: {key} runs {first}, which no package installs")
        self.assertEqual(bad, [])


class Checks(Tree):
    def test_mistakes_are_reported(self):
        bad = self.manifest(
            {"name": "one", "summary": "s", "files": [{"src": "a.py", "dest": "/usr/lib/x/a.py"}, {"src": "missing.py", "dest": "/usr/lib/x/m.py"}]},
            {"name": "two", "summary": "s", "requires": ["cosmos-nothere"], "files": [{"src": "b.py", "dest": "/usr/lib/x/a.py"}]},
            {"name": "Bad Name", "summary": "s", "files": [{"src": "a.py", "dest": "/usr/x", "mode": "755"}]},
            {"name": "one", "summary": "s"})
        text = "\n".join(build.problems(bad, self.root))
        for expected in ("missing source", "is in both one and two", "needs cosmos-nothere", "not a valid package name", "bad mode 755", "one: listed twice"):
            self.assertIn(expected, text)

    def test_a_correct_manifest_passes(self):
        good = self.manifest({"name": "one", "summary": "s", "dirs": ["/usr/lib/x"], "files": [{"src": "a.py", "dest": "/usr/lib/x/a.py"}],
                              "links": [{"dest": "/usr/bin/a", "target": "/usr/lib/x/a.py"}]})
        self.assertEqual(build.problems(good, self.root), [])


class Expansion(Tree):
    def test_globs_expand_sorted_and_honour_excludes(self):
        package = {"files": [{"glob": "*.py", "exclude": ["test_*"], "dest_dir": "/usr/lib/x/"}]}
        self.assertEqual([d for _s, d, _m, _c in build.expand_files(package, self.root)], ["/usr/lib/x/a.py", "/usr/lib/x/b.py"])


class Spec(Tree):
    PACKAGE = {"name": "cosmos-demo", "summary": "A demo", "description": "Longer.", "requires": ["python3", "cosmos-common"], "recommends": ["flatpak"],
               "dirs": ["/usr/lib/cosmos"], "post": "echo hi",
               "files": [{"src": "a.py", "dest": "/usr/lib/cosmos/a.py", "mode": "0755"}, {"src": "b.py", "dest": "/etc/cosmos/b.conf", "config": True}],
               "links": [{"dest": "/usr/bin/a", "target": "/usr/lib/cosmos/a.py"}]}

    def test_the_spec_lists_dependencies_files_modes_and_config(self):
        text = build.spec_text(self.PACKAGE, "1.2.3", "4", self.root)
        for line in ("Name:           cosmos-demo", "Version:        1.2.3", "BuildArch:      noarch", "Requires:       cosmos-common", "Recommends:     flatpak",
                     '%dir "/usr/lib/cosmos"', '%attr(755,root,root) "/usr/lib/cosmos/a.py"', '%config(noreplace) %attr(644,root,root) "/etc/cosmos/b.conf"',
                     '"/usr/bin/a"', "%post", "echo hi"):
            self.assertIn(line, text)

    def test_a_native_package_is_not_marked_noarch(self):
        self.assertNotIn("BuildArch", build.spec_text({**self.PACKAGE, "arch": "x86_64"}, "1", "1", self.root))


class Releases(Tree):
    def test_package_names_are_read_from_rpm_file_names(self):
        self.assertEqual(build.package_name("cosmos-gamebox-music-1-0.4.0-1.20260926011200.fc44.noarch.rpm"), "cosmos-gamebox-music-1")
        self.assertEqual(build.package_name("cosmos-wm-0.4.0-1.fc44.x86_64.rpm"), "cosmos-wm")

    def test_only_the_newest_build_of_a_package_is_published(self):
        old = self.root / "noarch"
        old.mkdir()
        first, second, other = old / "cosmos-a-1-1.1.fc44.noarch.rpm", old / "cosmos-a-1-1.2.fc44.noarch.rpm", old / "cosmos-b-1-1.1.fc44.noarch.rpm"
        for i, path in enumerate((first, second, other)):
            path.write_text("x")
            os.utime(path, (1000 + i, 1000 + i))
        self.assertEqual(build.newest_per_package([first, second, other]), [second, other])

    def test_the_repository_is_not_taken_from_dnf_s_cache(self):
        """The Containerfile must refresh the local repository's metadata, or a cached copy of an older build is installed."""
        text = open(os.path.join(build.ROOT, "Containerfile"), encoding="utf-8").read()
        start = text.index("--repofrompath=cosmos")
        install = text[start - 120:start + 400]
        self.assertIn("--refresh", install)
        self.assertIn("cosmos.metadata_expire=0", install)


class Replacements(Tree):
    def test_replacements_are_applied_while_staging_and_a_missing_text_is_an_error(self):
        (self.root / "unit.service").write_text("ExecStart=/usr/local/lib/kronos/kronosd.py\nExecStop=/usr/local/lib/kronos/stop\n")
        package = {"name": "cosmos-demo", "files": [{"src": "unit.service", "dest": "/usr/lib/systemd/system/demo.service",
                                                    "replace": [["/usr/local/lib/kronos/", "/usr/lib/kronos/"]]}]}
        work = self.root / "work"
        build.stage(package, "1.0", work, self.root)
        staged = (work / "stage" / "cosmos-demo-1.0" / "root" / "usr/lib/systemd/system/demo.service").read_text()
        self.assertEqual(staged, "ExecStart=/usr/lib/kronos/kronosd.py\nExecStop=/usr/lib/kronos/stop\n")      # every occurrence
        drifted = {"name": "cosmos-demo", "files": [{"src": "unit.service", "dest": "/x/demo.service", "replace": [["/opt/nowhere/", "/y/"]]}]}
        self.assertIn("to replace is not in the source file", "\n".join(build.problems({"packages": [drifted]}, self.root)))
        with self.assertRaises(ValueError):
            build.stage(drifted, "1.0", work, self.root)


class Staging(Tree):
    def test_files_get_their_modes_and_links_are_symlinks(self):
        work = self.root / "work"
        package = {"name": "cosmos-demo", "files": [{"src": "a.py", "dest": "/usr/lib/cosmos/a.py", "mode": "0755"}],
                   "links": [{"dest": "/usr/bin/a", "target": "/usr/lib/cosmos/a.py"}], "dirs": ["/usr/share/cosmos"]}
        build.stage(package, "1.0", work, self.root)
        top = work / "stage" / "cosmos-demo-1.0" / "root"
        self.assertEqual(oct((top / "usr/lib/cosmos/a.py").stat().st_mode & 0o777), "0o755")
        self.assertEqual(os.readlink(top / "usr/bin/a"), "/usr/lib/cosmos/a.py")
        self.assertTrue((top / "usr/share/cosmos").is_dir())
        self.assertTrue((work / "SOURCES" / "cosmos-demo-1.0.tar.gz").is_file())


if __name__ == "__main__":
    unittest.main()
