"""Tests for disc_content's play.txt/m3u/folder browsing (no manifest to author)."""
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import disc_content as dc

SCRATCH = os.path.expanduser("~/.placements/tests/disc-content")


class Disc(unittest.TestCase):
    def setUp(self):
        shutil.rmtree(SCRATCH, ignore_errors=True)
        os.makedirs(SCRATCH)
        self.addCleanup(shutil.rmtree, SCRATCH, True)
        self.root = Path(tempfile.mkdtemp(dir=SCRATCH))

    def write(self, rel, content=""):
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        return path

    # -- play.txt --------------------------------------------------------
    def test_the_content_is_just_the_platform_id(self):
        self.write("play.txt", "nes\n")
        self.assertEqual(dc.read_play_file(self.root), "nes")

    def test_ignores_blank_lines_and_comments_and_trailing_whitespace(self):
        self.write("play.txt", "\n# a comment\n  snes  \n")
        self.assertEqual(dc.read_play_file(self.root), "snes")

    def test_no_play_file_is_none(self):
        self.assertIsNone(dc.read_play_file(self.root))

    def test_a_play_file_with_only_comments_is_none(self):
        self.write("play.txt", "# nothing useful here\n\n")
        self.assertIsNone(dc.read_play_file(self.root))

    # -- a single game at a level -----------------------------------------
    def test_a_single_rom_is_found(self):
        self.write("play.txt", "nes\n")
        rom = self.write("game.nes", "ROM")
        entries = dc.browse(self.root)
        self.assertEqual(len(entries), 1)
        self.assertEqual((entries[0].kind, entries[0].system, entries[0].rom), ("game", "nes", rom))

    def test_boxart_is_auto_detected_and_not_mistaken_for_the_rom(self):
        self.write("play.txt", "nes\n")
        rom = self.write("game.nes", "ROM")
        art = self.write("cover.png", "PNG")
        entry = dc.browse(self.root)[0]
        self.assertEqual(entry.rom, rom)
        self.assertEqual(entry.boxart, art)

    def test_more_than_one_possible_rom_is_refused_not_guessed(self):
        self.write("play.txt", "nes\n")
        self.write("game.nes", "ROM")
        self.write("other.nes", "ROM2")
        self.assertEqual(dc.browse(self.root), [])

    def test_an_m3u_wins_and_everything_else_is_ignored(self):
        self.write("play.txt", "ps1\n")
        m3u = self.write("game.m3u", "disc1.cue\ndisc2.cue\n")
        self.write("disc1.cue", "cue1")
        self.write("disc2.cue", "cue2")
        self.write("readme.txt", "junk")
        entry = dc.browse(self.root)[0]
        self.assertEqual(entry.rom, m3u)

    def test_more_than_one_m3u_is_refused(self):
        self.write("play.txt", "ps1\n")
        self.write("a.m3u", "")
        self.write("b.m3u", "")
        self.assertEqual(dc.browse(self.root), [])

    # -- browsable collections --------------------------------------------
    def test_no_play_file_but_folders_is_a_browsable_collection(self):
        self.write("Game One/play.txt", "nes\n")
        self.write("Game One/one.nes", "x")
        self.write("Game Two/play.txt", "snes\n")
        self.write("Game Two/two.sfc", "x")
        entries = dc.browse(self.root)
        self.assertEqual(sorted(e.name for e in entries), ["Game One", "Game Two"])
        self.assertTrue(all(e.kind == "folder" for e in entries))

    def test_opening_a_folder_applies_the_same_rules_again(self):
        self.write("Game One/play.txt", "nes\n")
        rom = self.write("Game One/one.nes", "x")
        folder_entry = dc.browse(self.root)[0]
        inner = dc.browse(folder_entry.path)
        self.assertEqual((inner[0].kind, inner[0].rom), ("game", rom))

    def test_collections_can_nest(self):
        self.write("Consoles/NES/play.txt", "nes\n")
        self.write("Consoles/NES/g.nes", "x")
        top = dc.browse(self.root)
        self.assertEqual([e.name for e in top], ["Consoles"])
        second = dc.browse(top[0].path)
        self.assertEqual([e.name for e in second], ["NES"])
        third = dc.browse(second[0].path)
        self.assertEqual(third[0].kind, "game")

    def test_nothing_playable_is_an_empty_list(self):
        self.write("readme.txt", "just a stray file, no play.txt, no folders")
        self.assertEqual(dc.browse(self.root), [])

    def test_an_unreadable_folder_is_empty_not_a_crash(self):
        self.assertEqual(dc.browse(self.root / "does-not-exist"), [])

    # -- install ------------------------------------------------------------
    def test_install_a_single_file_game(self):
        self.write("play.txt", "nes\n")
        self.write("game.nes", "ROMDATA")
        entry = dc.browse(self.root)[0]
        dest = self.root.parent / "dest1"
        ok, message = dc.install_game(entry, dest)
        self.assertTrue(ok, message)
        self.assertEqual((dest / "game.nes").read_text(), "ROMDATA")

    def test_install_skips_if_already_there(self):
        self.write("play.txt", "nes\n")
        self.write("game.nes", "ROMDATA")
        entry = dc.browse(self.root)[0]
        dest = self.root.parent / "dest2"
        dest.mkdir()
        (dest / "game.nes").write_text("already here")
        ok, message = dc.install_game(entry, dest)
        self.assertFalse(ok)
        self.assertEqual((dest / "game.nes").read_text(), "already here")

    def test_install_a_multi_disc_game_copies_every_disc_the_m3u_names(self):
        self.write("play.txt", "ps1\n")
        self.write("game.m3u", "# a comment\ndisc1.cue\ndisc2.cue\n")
        self.write("disc1.cue", "CUE1")
        self.write("disc2.cue", "CUE2")
        entry = dc.browse(self.root)[0]
        dest = self.root.parent / "dest3"
        ok, message = dc.install_game(entry, dest)
        self.assertTrue(ok, message)
        installed = dest / "Game One" if (dest / "Game One").is_dir() else next(dest.iterdir())
        names = sorted(p.name for p in installed.iterdir())
        self.assertEqual(names, ["disc1.cue", "disc2.cue", "game.m3u"])
        self.assertEqual((installed / "disc1.cue").read_text(), "CUE1")


if __name__ == "__main__":
    unittest.main()
