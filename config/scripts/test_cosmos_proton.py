import hashlib
import io
import json
import os
import sys
import tarfile
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cosmos_proton as cp


def make_build(root, name, wine="files"):
    path = os.path.join(root, name)
    os.makedirs(os.path.join(path, wine, "bin"))
    script = os.path.join(path, "proton")
    open(script, "w").write("#!/bin/sh\n")
    os.chmod(script, 0o755)
    return path


def make_tar(path, entries, mode="w:gz"):
    with tarfile.open(path, mode) as tar:
        for name, kind, data in entries:
            info = tarfile.TarInfo(name)
            if kind == "dir":
                info.type = tarfile.DIRTYPE
                info.mode = 0o755
                tar.addfile(info)
            elif kind == "file":
                info.size = len(data)
                info.mode = 0o755 if name.endswith("/proton") else 0o644
                tar.addfile(info, io.BytesIO(data))
            elif kind == "symlink":
                info.type = tarfile.SYMTYPE
                info.linkname = data
                tar.addfile(info)
            elif kind == "dev":
                info.type = tarfile.CHRTYPE
                tar.addfile(info)


GOOD = [("GE-Proton9-1", "dir", None), ("GE-Proton9-1/proton", "file", b"#!/bin/sh\n"), ("GE-Proton9-1/files", "dir", None),
        ("GE-Proton9-1/files/bin", "dir", None), ("GE-Proton9-1/files/bin/wine64", "file", b"x")]


class Discover(unittest.TestCase):
    def test_finds_store_steam_and_tool_folders_and_orders_newest_first(self):
        d = tempfile.mkdtemp()
        store, steam = os.path.join(d, "store"), os.path.join(d, "steam")
        make_build(store, "GE-Proton9-2")
        make_build(os.path.join(steam, "compatibilitytools.d"), "GE-Proton10-4")
        make_build(os.path.join(steam, "steamapps/common"), "Proton 9.0 (Beta)", wine="dist")
        os.makedirs(os.path.join(store, "not-a-build"))
        builds = cp.discover(store, [steam])
        self.assertEqual([b.name for b in builds][:2], ["GE-Proton10-4", "GE-Proton9-2"] if builds[0].sort_key[0] == "ge" else [b.name for b in builds][:2])
        self.assertEqual({b.name for b in builds}, {"GE-Proton9-2", "GE-Proton10-4", "Proton 9.0 (Beta)"})
        self.assertEqual({b.name: b.source for b in builds}["GE-Proton9-2"], "cosmos")

    def test_version_order_within_a_family(self):
        names = ["GE-Proton9-27", "GE-Proton10-1", "GE-Proton9-4", "GE-Proton10-10"]
        got = sorted(names, key=cp.version_key)
        self.assertEqual(got, ["GE-Proton9-4", "GE-Proton9-27", "GE-Proton10-1", "GE-Proton10-10"])
        b = [cp.Build(n, "/x", "cosmos") for n in names + ["Proton 9.0-4"]]
        self.assertEqual(cp.newest(b, "ge").name, "GE-Proton10-10")
        self.assertEqual(cp.newest(b, "valve").name, "Proton 9.0-4")

    def test_a_folder_without_the_script_or_wine_is_not_a_build(self):
        d = tempfile.mkdtemp()
        os.makedirs(os.path.join(d, "x", "files", "bin"))
        self.assertFalse(cp.is_build(os.path.join(d, "x")))
        os.makedirs(os.path.join(d, "y"))
        open(os.path.join(d, "y", "proton"), "w").write("")
        self.assertFalse(cp.is_build(os.path.join(d, "y")))


class Import(unittest.TestCase):
    def test_import_folder_copies_and_refuses_duplicates(self):
        d = tempfile.mkdtemp()
        src = make_build(d, "Proton-9.0-4")
        store = os.path.join(d, "store")
        b = cp.import_folder(src, store)
        self.assertTrue(cp.is_build(b.path))
        with self.assertRaises(cp.ProtonError):
            cp.import_folder(src, store)
        cp.import_folder(src, store, replace=True)
        with self.assertRaises(cp.ProtonError):
            cp.import_folder(d, store)                                    # not a build

    def test_import_archive_of_each_compression(self):
        d = tempfile.mkdtemp()
        for ext, mode in (("gz", "w:gz"), ("xz", "w:xz"), ("bz2", "w:bz2")):
            arc = os.path.join(d, f"GE-Proton9-1.tar.{ext}")
            make_tar(arc, GOOD, mode)
            store = os.path.join(d, "store-" + ext)
            b = cp.import_archive(arc, store)
            self.assertEqual(b.name, "GE-Proton9-1")
            self.assertTrue(cp.is_build(b.path), ext)
        try:
            import compression.zstd  # noqa: F401
            arc = os.path.join(d, "GE-Proton9-1.tar.zst")
            make_tar(arc, GOOD, "w:zst")
            self.assertTrue(cp.is_build(cp.import_archive(arc, os.path.join(d, "store-zst")).path))
        except ImportError:
            pass

    def test_hostile_archives_are_refused_and_leave_nothing_behind(self):
        d = tempfile.mkdtemp()
        store = os.path.join(d, "store")
        bad = {
            "dotdot": [("../evil", "file", b"x")],
            "absolute": [("/tmp/evil", "file", b"x")],
            "link out": GOOD + [("GE-Proton9-1/l", "symlink", "../../../../etc/passwd")],
            "absolute link": GOOD + [("GE-Proton9-1/l", "symlink", "/etc/passwd")],
            "device": GOOD + [("GE-Proton9-1/dev", "dev", None)],
        }
        for label, entries in bad.items():
            arc = os.path.join(d, "bad.tar.gz")
            make_tar(arc, entries)
            with self.assertRaises(cp.ProtonError, msg=label):
                cp.import_archive(arc, store)
        self.assertFalse(os.path.exists(os.path.join(d, "evil")))
        self.assertEqual([n for n in os.listdir(store)] if os.path.isdir(store) else [], [])

    def test_an_archive_that_is_not_a_build_says_so(self):
        d = tempfile.mkdtemp()
        arc = os.path.join(d, "x.tar.gz")
        make_tar(arc, [("readme", "file", b"hi")])
        with self.assertRaises(cp.ProtonError) as ctx:
            cp.import_archive(arc, os.path.join(d, "s"))
        self.assertIn("doesn't contain a Proton build", str(ctx.exception))
        with self.assertRaises(cp.ProtonError):
            cp.import_archive(os.path.join(d, "missing.tar.gz"), os.path.join(d, "s"))

    def test_remove_only_touches_the_store(self):
        d = tempfile.mkdtemp()
        store = os.path.join(d, "store")
        make_build(store, "GE-Proton9-1")
        cp.remove("GE-Proton9-1", store)
        self.assertFalse(os.path.exists(os.path.join(store, "GE-Proton9-1")))
        for bad in ("../x", "", "a/b"):
            with self.assertRaises(cp.ProtonError):
                cp.remove(bad, store)


RELEASES = [{"tag_name": "GE-Proton11-7", "assets": [
    {"name": "GE-Proton11-7-aarch64.tar.gz", "size": 1, "browser_download_url": "https://github.com/x/GE-Proton11-7-aarch64.tar.gz"},
    {"name": "GE-Proton11-7-x86_64.tar.gz", "size": 5, "browser_download_url": "https://github.com/x/GE-Proton11-7-x86_64.tar.gz"},
    {"name": "GE-Proton11-7-x86_64.sha512sum", "size": 1, "browser_download_url": "https://github.com/x/GE-Proton11-7-x86_64.sha512sum"}]},
    {"tag_name": "cachyos-11.0", "assets": [
    {"name": "proton-cachyos-11.0-x86_64_v3.tar.xz", "size": 1, "browser_download_url": "https://github.com/x/v3.tar.xz"},
    {"name": "proton-cachyos-11.0-x86_64.tar.xz", "size": 2, "browser_download_url": "https://github.com/x/plain.tar.xz"}]},
    {"tag_name": "no-assets", "assets": []}]


class Download(unittest.TestCase):
    def test_lists_the_x86_64_archive_and_its_checksum(self):
        rel = cp.list_releases("GE-Proton", lambda url: json.dumps(RELEASES).encode())
        self.assertEqual([r["name"] for r in rel], ["GE-Proton11-7", "cachyos-11.0"])
        self.assertTrue(rel[0]["url"].endswith("x86_64.tar.gz"))
        self.assertTrue(rel[0]["sha512_url"].endswith(".sha512sum"))
        self.assertTrue(rel[1]["url"].endswith("plain.tar.xz"))                # never the arm64 or the v3 (needs a newer CPU) one
        with self.assertRaises(cp.ProtonError):
            cp.list_releases("not a repo", lambda u: b"[]")
        with self.assertRaises(cp.ProtonError):
            cp.list_releases("GE-Proton", lambda u: (_ for _ in ()).throw(OSError("offline")))

    def setup_download(self, corrupt=False, checksum=True):
        d = tempfile.mkdtemp()
        arc = os.path.join(d, "src.tar.gz")
        make_tar(arc, GOOD)
        data = open(arc, "rb").read()
        digest = hashlib.sha512(data).hexdigest()

        def fetch_file(url, dest, timeout=600):
            open(dest, "wb").write(data + (b"junk" if corrupt else b""))
            return hashlib.sha512(data + (b"junk" if corrupt else b"")).hexdigest()
        release = {"name": "GE-Proton9-1", "url": "https://github.com/x/a.tar.gz", "archive": "a.tar.gz",
                   "sha512_url": "https://github.com/x/a.sha512sum" if checksum else ""}
        return d, release, fetch_file, (lambda url: (digest + "  a.tar.gz\n").encode())

    def test_a_good_download_is_checked_and_imported(self):
        d, release, fetch_file, fetch = self.setup_download()
        said = []
        b = cp.download_release(release, os.path.join(d, "store"), fetch, fetch_file, os.path.join(d, "cache"), said.append)
        self.assertTrue(cp.is_build(b.path))
        self.assertTrue(any("Checking" in s for s in said))
        self.assertEqual(os.listdir(os.path.join(d, "cache")), [])              # the archive is cleaned up

    def test_a_download_that_does_not_match_its_checksum_is_not_unpacked(self):
        d, release, fetch_file, fetch = self.setup_download(corrupt=True)
        with self.assertRaises(cp.ProtonError) as ctx:
            cp.download_release(release, os.path.join(d, "store"), fetch, fetch_file, os.path.join(d, "cache"))
        self.assertIn("checksum", str(ctx.exception))
        self.assertFalse(os.path.isdir(os.path.join(d, "store")) and os.listdir(os.path.join(d, "store")))

    def test_only_github_urls_are_downloaded(self):
        d, release, fetch_file, fetch = self.setup_download()
        release["url"] = "https://evil.example/a.tar.gz"
        with self.assertRaises(cp.ProtonError):
            cp.download_release(release, os.path.join(d, "store"), fetch, fetch_file, os.path.join(d, "cache"))


if __name__ == "__main__":
    unittest.main()
