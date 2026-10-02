import os
import struct
import sys
import tempfile
import unittest
import zlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cosmos_webapp_edit as we


def png_bytes(w, h, interlace=0, corrupt=None):
    raw = b"".join(b"\x00" + b"\xff\x00\x90\xff" * w for _ in range(h))

    def chunk(kind, body, bad_crc=False):
        crc = zlib.crc32(kind + body) & 0xFFFFFFFF
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", crc ^ (1 if bad_crc else 0))
    idat = zlib.compress(raw)
    if corrupt == "data":
        idat = idat[:10] + b"\x00" * 5 + idat[15:]
    out = we.PNG_SIGNATURE + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, interlace)) + chunk(b"IDAT", idat, corrupt == "crc")
    if corrupt != "no-end":
        out += chunk(b"IEND", b"")
    if corrupt == "truncated":
        out = out[:len(out) // 2]
    return out


DESKTOP = ("[Desktop Entry]\nType=Application\nName=Gmail\nName[de]=Google Mail\nExec=brave-browser --app=https://mail.google.com --profile-directory=Default\n"
           "Icon=brave-browser\nCategories=Network;\nTerminal=false\nNoDisplay=true\n")


class Base(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.apps, self.icons = os.path.join(self.d, "apps"), os.path.join(self.d, "icons")
        os.makedirs(self.apps)
        self.path = self.make("gmail", DESKTOP)

    def make(self, slug, text):
        p = os.path.join(self.apps, f"brave-{slug}.desktop")
        with open(p, "w") as fh:
            fh.write(text)
        return p

    def image(self, name, data):
        p = os.path.join(self.d, name)
        with open(p, "wb") as fh:
            fh.write(data)
        return p

    def read(self):
        with open(self.path) as fh:
            return fh.read()


class Rename(Base):
    def test_only_the_name_line_changes(self):
        self.assertEqual(we.rename(self.path, "  Mail   (work) ", self.apps), "Mail (work)")
        text = self.read()
        self.assertIn("\nName=Mail (work)\n", text)
        self.assertEqual(text.replace("Name=Mail (work)", "Name=Gmail"), DESKTOP)             # every other line is byte for byte the same
        self.assertIn("Name[de]=Google Mail", text)                                          # translations are left alone

    def test_a_missing_name_line_is_added_and_a_duplicate_dropped(self):
        p = self.make("odd", "[Desktop Entry]\nType=Application\nExec=x\n")
        we.rename(p, "Odd", self.apps)
        self.assertEqual(open(p).read().count("Name=Odd"), 1)
        p2 = self.make("dup", "[Desktop Entry]\nName=A\nName=B\nExec=x\n[Desktop Action x]\nName=Keep me\n")
        we.rename(p2, "New", self.apps)
        text = open(p2).read()
        self.assertEqual(text.count("Name=New"), 1)
        self.assertIn("Name=Keep me", text)                                                  # another group's Name is not the app's name

    def test_bad_names_are_refused_and_change_nothing(self):
        for bad in ("", "   ", "\n\t", "x" * 61, "a\x00b", "a\x07b"):
            with self.assertRaises(we.EditError):
                we.rename(self.path, bad, self.apps)
        self.assertEqual(self.read(), DESKTOP)

    def test_a_newline_cannot_smuggle_in_another_key(self):
        we.rename(self.path, "Mail\nExec=rm -rf ~", self.apps)
        text = self.read()
        self.assertEqual(len([l for l in text.split("\n") if l.startswith("Exec=")]), 1)      # the newline became a space inside the name, not a new line
        self.assertIn("Name=Mail Exec=rm -rf ~\n", text)

    def test_only_web_app_shortcuts_can_be_edited(self):
        other = os.path.join(self.apps, "firefox.desktop")
        open(other, "w").write(DESKTOP)
        outside = os.path.join(self.d, "brave-x.desktop")
        open(outside, "w").write(DESKTOP)
        for bad in (other, outside, os.path.join(self.apps, "brave-missing.desktop"), os.path.join(self.apps, "brave-..x.desktop")):
            with self.assertRaises(we.EditError):
                we.rename(bad, "X", self.apps)


class Icons(Base):
    def test_a_128_png_is_used_and_the_desktop_file_points_at_it(self):
        target = we.set_icon(self.path, self.image("good.png", png_bytes(128, 128)), self.apps, self.icons)
        self.assertEqual(target, os.path.join(self.icons, "brave-gmail.png"))
        self.assertEqual(open(target, "rb").read(), png_bytes(128, 128))
        self.assertIn("\nIcon=brave-gmail\n", self.read())
        self.assertEqual(self.read().count("Icon="), 1)

    def test_wrong_sizes_are_refused_with_the_actual_size_and_nothing_changes(self):
        we.set_icon(self.path, self.image("good.png", png_bytes(128, 128)), self.apps, self.icons)
        before, icon_before = self.read(), open(os.path.join(self.icons, "brave-gmail.png"), "rb").read()
        for w, h in ((64, 64), (127, 128), (256, 256), (128, 96), (512, 128), (1, 1)):
            with self.assertRaises(we.EditError) as ctx:
                we.set_icon(self.path, self.image("bad.png", png_bytes(w, h)), self.apps, self.icons)
            self.assertIn(f"{w} x {h}", str(ctx.exception))
        self.assertEqual(self.read(), before)
        self.assertEqual(open(os.path.join(self.icons, "brave-gmail.png"), "rb").read(), icon_before)

    def test_things_that_are_not_sound_pngs_are_refused(self):
        cases = {"jpeg": b"\xff\xd8\xff\xe0" + b"0" * 100, "svg": b"<svg xmlns='http://www.w3.org/2000/svg' width='128' height='128'/>",
                 "empty": b"", "text": b"hello", "crc": png_bytes(128, 128, corrupt="crc"), "data": png_bytes(128, 128, corrupt="data"),
                 "truncated": png_bytes(128, 128, corrupt="truncated"), "no-end": png_bytes(128, 128, corrupt="no-end")}
        for label, data in cases.items():
            with self.assertRaises(we.EditError, msg=label):
                we.set_icon(self.path, self.image(label + ".png", data), self.apps, self.icons)       # (a wrong extension doesn't matter either way)
        self.assertFalse(os.path.exists(os.path.join(self.icons, "brave-gmail.png")))
        self.assertEqual(self.read(), DESKTOP)

    def test_a_png_named_jpg_is_judged_by_what_it_is(self):
        we.set_icon(self.path, self.image("photo.jpg", png_bytes(128, 128)), self.apps, self.icons)
        self.assertTrue(os.path.exists(os.path.join(self.icons, "brave-gmail.png")))

    def test_huge_files_and_missing_files_are_refused(self):
        with self.assertRaises(we.EditError):
            we.set_icon(self.path, self.image("huge.png", png_bytes(128, 128) + b"0" * (we.MAX_ICON_BYTES + 1)), self.apps, self.icons)
        with self.assertRaises(we.EditError):
            we.set_icon(self.path, os.path.join(self.d, "nope.png"), self.apps, self.icons)
        with self.assertRaises(we.EditError):
            we.set_icon(self.path, self.d, self.apps, self.icons)                                     # a folder

    def test_interlaced_pngs_of_the_right_size_are_fine(self):
        we.set_icon(self.path, self.image("i.png", png_bytes(128, 128, interlace=1)), self.apps, self.icons)


class Removing(Base):
    def test_removes_the_shortcut_and_its_icon(self):
        we.set_icon(self.path, self.image("good.png", png_bytes(128, 128)), self.apps, self.icons)
        we.remove(self.path, self.apps, self.icons)
        self.assertFalse(os.path.exists(self.path))
        self.assertFalse(os.path.exists(os.path.join(self.icons, "brave-gmail.png")))

    def test_a_missing_icon_is_fine(self):
        we.remove(self.path, self.apps, self.icons)
        self.assertFalse(os.path.exists(self.path))

    def test_built_in_defaults_cannot_be_removed_but_can_be_edited(self):
        p = self.make("youtube", DESKTOP + "X-CosmOS-Default=true\n")
        self.assertTrue(we.is_default(p))
        with self.assertRaises(we.EditError):
            we.remove(p, self.apps, self.icons)
        self.assertTrue(os.path.exists(p))
        we.rename(p, "YT", self.apps)
        we.set_icon(p, self.image("good.png", png_bytes(128, 128)), self.apps, self.icons)
        self.assertIn("X-CosmOS-Default=true", open(p).read())

    def test_it_cannot_delete_anything_that_is_not_a_web_app_shortcut(self):
        precious = os.path.join(self.apps, "notes.desktop")
        open(precious, "w").write("keep")
        for bad in (precious, self.apps, os.path.join(self.d, "brave-outside.desktop")):
            with self.assertRaises(we.EditError):
                we.remove(bad, self.apps, self.icons)
        self.assertTrue(os.path.exists(precious))


if __name__ == "__main__":
    unittest.main()
