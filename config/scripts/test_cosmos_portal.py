"""Tests for the file chooser portal: option decoding, result shape, and a real D-Bus round trip
against the service (under Xvfb + a private session bus; skipped when those tools are missing)."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)


def client_main():
    """Runs inside `xvfb-run dbus-run-session`: starts the service, makes portal calls, prints JSON."""
    import gi
    from gi.repository import Gio, GLib
    cases = json.loads(os.environ["PORTAL_CASES"])
    service = subprocess.Popen([sys.executable, os.path.join(HERE, "cosmos-portal-filechooser")], env=os.environ)
    conn = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    deadline = time.time() + 20
    while time.time() < deadline:
        owner = conn.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus", "NameHasOwner",
                               GLib.Variant("(s)", ("org.freedesktop.impl.portal.desktop.cosmos",)), None, Gio.DBusCallFlags.NONE, -1, None)
        if owner.unpack()[0]:
            break
        time.sleep(0.2)
    out = []
    for i, case in enumerate(cases):
        opts = {k: GLib.Variant(t, v) for k, (t, v) in case["options"].items()}
        handle = f"/org/freedesktop/portal/desktop/request/test/t{i}"
        loop = GLib.MainLoop()
        result = {}

        def cb(source, res, _u):
            try:
                r = source.call_finish(res).unpack()
                result["response"], result["results"] = r[0], {k: v for k, v in r[1].items()}
            except GLib.Error as exc:
                result["error"] = exc.message
            loop.quit()
        conn.call("org.freedesktop.impl.portal.desktop.cosmos", "/org/freedesktop/portal/desktop",
                  "org.freedesktop.impl.portal.FileChooser", case["method"],
                  GLib.Variant("(osssa{sv})", (handle, "test.app", "", case.get("title", "Pick"), opts)),
                  None, Gio.DBusCallFlags.NONE, 30000, None, cb, None)
        if case.get("close"):
            def close():
                conn.call_sync("org.freedesktop.impl.portal.desktop.cosmos", handle, "org.freedesktop.impl.portal.Request", "Close",
                               None, None, Gio.DBusCallFlags.NONE, 5000, None)
                return False
            GLib.timeout_add(1500, close)
        GLib.timeout_add(25000, loop.quit)
        loop.run()
        out.append(result)
    service.terminate()
    print("RESULT" + json.dumps(out))


class HelperTests(unittest.TestCase):
    def setUp(self):
        import cosmos_portal_filechooser as pf
        self.pf = pf

    def test_paths_arrive_as_nul_terminated_bytes(self):
        self.assertEqual(self.pf.bytes_to_path(b"/tmp/x\0"), "/tmp/x")
        self.assertEqual(self.pf.bytes_to_path([47, 97, 0]), "/a")
        self.assertIsNone(self.pf.bytes_to_path(None))

    def test_open_options(self):
        c = self.pf.chooser_from_call("OpenFile", "Pick", {"multiple": True, "accept_label": "Go",
                                                           "filters": [("Images", [(0, "*.png"), (1, "image/jpeg")]), ("All", [(0, "*")])],
                                                           "current_filter": ("All", [(0, "*")])})
        self.assertEqual((c["kind"], c["multiple"], c["accept_label"], c["current_filter"]), ("open", True, "Go", 1))
        self.assertEqual(c["filters"][0][1], [(0, "*.png"), (1, "image/jpeg")])

    def test_directory_and_save_options(self):
        self.assertEqual(self.pf.chooser_from_call("OpenFile", "", {"directory": True})["kind"], "dir")
        save = self.pf.chooser_from_call("SaveFile", "Save", {"current_name": "a.txt", "current_folder": b"/x\0"})
        self.assertEqual((save["kind"], save["current_name"], save["current_folder"]), ("save", "a.txt", "/x"))
        from_file = self.pf.chooser_from_call("SaveFile", "", {"current_file": b"/y/b.txt\0"})
        self.assertEqual((from_file["current_folder"], from_file["current_name"]), ("/y", "b.txt"))
        files = self.pf.chooser_from_call("SaveFiles", "", {"files": [b"a\0", b"sub/b\0"]})
        self.assertEqual((files["kind"], files["files"]), ("dir", ["a", "sub/b"]))

    def test_result_uris(self):
        self.assertEqual(self.pf.uris_for("OpenFile", {}, ["/tmp/a b.txt"]), ["file:///tmp/a%20b.txt"])
        uris = self.pf.uris_for("SaveFiles", {"files": ["a", "x/../b"]}, ["/usb"])
        self.assertEqual(uris, ["file:///usb/a", "file:///usb/b"])          # only the file name is used, never a path

    def test_filter_matcher(self):
        import cosmos_files_ui as ui
        m = ui.make_matcher([(0, "*.PNG"), (1, "text/plain")])
        self.assertTrue(m("a.png") and m("notes.txt"))
        self.assertFalse(m("song.mp3"))
        self.assertIsNone(ui.make_matcher([]))


@unittest.skipUnless(shutil.which("xvfb-run") and shutil.which("dbus-run-session"), "needs xvfb-run and dbus-run-session")
class RoundTripTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        home = os.path.join(cls.tmp, "home")
        profile_dir = os.path.join(home, ".local", "share", "cosmos", "profiles", "skye")
        os.makedirs(os.path.join(profile_dir, "files"))
        os.makedirs(os.path.join(home, ".config", "cosmos"))
        with open(os.path.join(profile_dir, "profile.json"), "w") as fh:
            json.dump({"name": "Skye", "avatar": None, "pin": None}, fh)
        with open(os.path.join(home, ".config", "cosmos", "active-profile"), "w") as fh:
            fh.write("skye\n")
        cls.files = os.path.realpath(os.path.join(profile_dir, "files"))
        open(os.path.join(cls.files, "doc.txt"), "w").close()
        cls.usb = os.path.realpath(os.path.join(cls.tmp, "usb"))
        os.makedirs(cls.usb)
        open(os.path.join(cls.usb, "photo.jpg"), "w").close()
        lsblk = {"blockdevices": [{"name": "sdb", "path": "/dev/sdb", "type": "disk", "rm": True, "tran": "usb", "size": "8G", "children": [
            {"name": "sdb1", "path": "/dev/sdb1", "type": "part", "fstype": "vfat", "label": "STICK", "size": "8G", "mountpoints": [cls.usb]}]}]}
        cls.lsblk = os.path.join(cls.tmp, "lsblk.json")
        with open(cls.lsblk, "w") as fh:
            json.dump(lsblk, fh)
        cls.home = home

    def run_cases(self, cases, pick=None):
        env = dict(os.environ, HOME=self.home, COSMOS_LSBLK_JSON=self.lsblk, PORTAL_CASES=json.dumps(cases), PYTHONPATH=HERE)
        env.pop("DISPLAY", None)
        env.pop("WAYLAND_DISPLAY", None)
        if pick:
            env["COSMOS_PORTAL_TEST_PICK"] = pick
        else:
            env.pop("COSMOS_PORTAL_TEST_PICK", None)
        done = subprocess.run(["xvfb-run", "-a", "dbus-run-session", "--", sys.executable, os.path.abspath(__file__), "--client"],
                              env=env, capture_output=True, text=True, timeout=120)
        line = next((l for l in done.stdout.splitlines() if l.startswith("RESULT")), None)
        self.assertIsNotNone(line, done.stdout + done.stderr)
        return json.loads(line[len("RESULT"):])

    def test_open_returns_the_picked_file_as_a_uri(self):
        pick = os.path.join(self.usb, "photo.jpg")
        [r] = self.run_cases([{"method": "OpenFile", "options": {}}], pick=pick)
        self.assertEqual(r.get("response"), 0, r)
        self.assertEqual(r["results"]["uris"], ["file://" + pick])

    def test_save_returns_the_new_file_uri_inside_the_scope(self):
        pick = os.path.join(self.files, "saved.txt")
        [r] = self.run_cases([{"method": "SaveFile", "options": {"current_name": ("s", "ignored.txt")}}], pick=pick)
        self.assertEqual(r.get("response"), 0, r)
        self.assertEqual(r["results"]["uris"], ["file://" + pick])

    def test_closing_the_request_cancels_the_dialog(self):
        [r] = self.run_cases([{"method": "OpenFile", "options": {}, "close": True}])
        self.assertEqual(r.get("response"), 1, r)


if __name__ == "__main__":
    if "--client" in sys.argv:
        client_main()
    else:
        unittest.main()
