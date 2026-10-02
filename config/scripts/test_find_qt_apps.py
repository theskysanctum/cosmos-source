"""Tests for find-qt-apps against a fake root filesystem."""
import importlib.machinery
import importlib.util
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
loader = importlib.machinery.SourceFileLoader("find_qt_apps", os.path.join(HERE, "find-qt-apps"))
spec = importlib.util.spec_from_loader("find_qt_apps", loader)
fq = importlib.util.module_from_spec(spec)
loader.exec_module(fq)


def find_qt_elf():
    """Any real ELF file on this machine that links Qt (a PyQt module will do)."""
    for candidate in __import__("glob").glob("/usr/lib64/python3*/site-packages/PyQt6/QtWidgets*.so") + \
            __import__("glob").glob("/usr/lib64/python3*/site-packages/PyQt5/QtWidgets*.so"):
        if fq.elf_needed(candidate) and any(fq.QT_LIB.match(n) for n in fq.elf_needed(candidate)):
            return candidate
    return None


QT_ELF = find_qt_elf()
PLAIN_ELF = shutil.which("true")


class CommandTests(unittest.TestCase):
    def test_exec_lines(self):
        self.assertEqual(fq.command_of("krita %F"), ("krita", False))
        self.assertEqual(fq.command_of("env QT_QPA_PLATFORM=xcb FOO=1 /usr/bin/app --flag %U"), ("/usr/bin/app", False))
        self.assertEqual(fq.command_of('sh -c "exec mytool %u"'), ("mytool", False))
        self.assertEqual(fq.command_of("/usr/bin/flatpak run --branch=stable org.kde.krita @@u %U @@"), ("flatpak", True))
        self.assertEqual(fq.command_of(""), (None, False))


@unittest.skipUnless(QT_ELF and PLAIN_ELF, "needs a Qt-linked ELF (PyQt) and /usr/bin/true")
class ScanTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        for d in ("usr/bin", "usr/lib/app", "usr/share/applications", "usr/share/cosmos", "var/lib/flatpak/exports/share/applications"):
            os.makedirs(os.path.join(self.root, d))

    def put(self, rel, src=None, text=None, mode=0o755):
        path = os.path.join(self.root, rel)
        if src:
            shutil.copy(src, path)
        else:
            with open(path, "w") as fh:
                fh.write(text)
        os.chmod(path, mode)

    def desktop(self, name, exec_line, extra="", where="usr/share/applications"):
        self.put(f"{where}/{name}.desktop", text=f"[Desktop Entry]\nType=Application\nName={name}\nExec={exec_line}\n{extra}")

    def rows(self, **kw):
        return {r["desktop"]: r for r in fq.scan(self.root, **kw)}

    def test_elf_reader(self):
        self.assertTrue(any(fq.QT_LIB.match(n) for n in fq.elf_needed(QT_ELF)))
        self.assertFalse(any(fq.QT_LIB.match(n) for n in fq.elf_needed(PLAIN_ELF) or []))
        self.assertIsNone(fq.elf_needed(__file__))                              # not an ELF

    def test_qt_apps_are_problems_and_others_are_not_listed(self):
        self.put("usr/bin/qtapp", src=QT_ELF)
        self.put("usr/bin/plain", src=PLAIN_ELF)
        self.desktop("qtapp", "qtapp %U")
        self.desktop("plain", "plain")
        rows = self.rows()
        self.assertEqual(rows["qtapp.desktop"]["status"], "PROBLEM")
        self.assertTrue(rows["qtapp.desktop"]["qt"])
        self.assertNotIn("plain.desktop", rows)

    def test_scripts_and_symlinks_are_followed_inside_the_root(self):
        self.put("usr/lib/app/real", src=QT_ELF)
        self.put("usr/bin/wrapped", text="#!/bin/sh\nexec /usr/lib/app/real \"$@\"\n")
        os.symlink("/usr/lib/app/real", os.path.join(self.root, "usr/bin/abslink"))       # absolute: must resolve inside the root
        os.symlink("../lib/app/real", os.path.join(self.root, "usr/bin/rellink"))
        for n in ("wrapped", "abslink", "rellink"):
            self.desktop(n, n)
        rows = self.rows()
        for n in ("wrapped", "abslink", "rellink"):
            self.assertEqual(rows[n + ".desktop"]["status"], "PROBLEM", n)

    def test_flatpaks_exceptions_hidden_and_unresolved(self):
        self.put("usr/bin/pcsx2-qt", src=QT_ELF)
        self.put("usr/bin/secret", src=QT_ELF)
        self.desktop("org.kde.krita", "/usr/bin/flatpak run --command=krita org.kde.krita", where="var/lib/flatpak/exports/share/applications")
        self.desktop("pcsx2", "pcsx2-qt")
        self.desktop("hidden", "secret", extra="NoDisplay=true\n")
        self.desktop("ghost", "not-installed")
        self.put("usr/share/cosmos/qt-app-exceptions.txt", text="# hand built\npcsx2-qt\n")
        rows = self.rows()
        self.assertEqual(rows["org.kde.krita.desktop"]["status"], "flatpak")
        self.assertEqual(rows["pcsx2.desktop"]["status"], "allowed")
        self.assertNotIn("hidden.desktop", rows)
        self.assertEqual(rows["ghost.desktop"]["status"], "unresolved")
        self.assertEqual(self.rows(include_hidden=True)["hidden.desktop"]["status"], "PROBLEM")
        self.assertEqual(self.rows(allow=["secret"], include_hidden=True)["hidden.desktop"]["status"], "allowed")

    def test_exit_status_gates(self):
        self.put("usr/bin/qtapp", src=QT_ELF)
        self.desktop("qtapp", "qtapp")
        self.assertEqual(fq.main(["--root", self.root]), 1)
        self.assertEqual(fq.main(["--root", self.root, "--allow", "qtapp"]), 0)

    def test_the_repos_own_exception_list_covers_the_two_emulators(self):
        with open(os.path.join(HERE, "..", "cosmos-apps", "qt-app-exceptions.txt")) as fh:
            names = {l.split("#")[0].strip() for l in fh if l.split("#")[0].strip()}
        self.assertTrue({"pcsx2-qt", "dolphin-emu-nogui"} <= names)


if __name__ == "__main__":
    unittest.main()
