"""Tests for cosmos_places / cosmos_fileops: what the scoped file manager may show, and that
nothing can lead out of it."""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cosmos_fileops as fo
import cosmos_places as cp

LSBLK = {"blockdevices": [
    {"name": "zram0", "path": "/dev/zram0", "type": "disk", "rm": False, "hotplug": False, "tran": None, "fstype": "swap",
     "label": None, "size": "8G", "mountpoints": ["[SWAP]"]},
    {"name": "nvme0n1", "path": "/dev/nvme0n1", "type": "disk", "rm": False, "hotplug": False, "tran": "nvme", "size": "1T",
     "model": "WD Black", "children": [
         {"name": "nvme0n1p1", "path": "/dev/nvme0n1p1", "type": "part", "fstype": "vfat", "size": "512M", "mountpoints": ["/boot/efi"]},
         {"name": "nvme0n1p2", "path": "/dev/nvme0n1p2", "type": "part", "fstype": "ext4", "size": "1G", "mountpoints": ["/boot"]},
         {"name": "nvme0n1p3", "path": "/dev/nvme0n1p3", "type": "part", "fstype": "btrfs", "size": "900G", "mountpoints": ["/home", "/"]}]},
    {"name": "sda", "path": "/dev/sda", "type": "disk", "rm": False, "hotplug": False, "tran": "sata", "size": "2T",
     "model": "ST2000", "vendor": "Seagate", "children": [
         {"name": "sda1", "path": "/dev/sda1", "type": "part", "fstype": "ntfs", "label": "Games", "size": "2T", "mountpoints": ["/var/mnt/games"]}]},
    {"name": "sdb", "path": "/dev/sdb", "type": "disk", "rm": True, "hotplug": True, "tran": "usb", "size": "64G",
     "model": "Ultra", "vendor": "SanDisk", "children": [
         {"name": "sdb1", "path": "/dev/sdb1", "type": "part", "fstype": "exfat", "label": "STICK", "size": "64G", "mountpoints": ["/run/media/user/STICK"]}]},
    {"name": "sdc", "path": "/dev/sdc", "type": "disk", "rm": False, "hotplug": False, "tran": "sata", "size": "500G",
     "model": "EVO", "children": [
         {"name": "sdc1", "path": "/dev/sdc1", "type": "part", "fstype": "ext4", "label": "Backup", "size": "250G", "mountpoints": []},
         {"name": "sdc2", "path": "/dev/sdc2", "type": "part", "fstype": "crypto_LUKS", "size": "250G", "mountpoints": []}]},
    {"name": "sr0", "path": "/dev/sr0", "type": "rom", "rm": True, "hotplug": False, "tran": "sata", "fstype": "iso9660",
     "label": "MOVIE_DISC", "size": "4G", "mountpoints": []},
    {"name": "sdd", "path": "/dev/sdd", "type": "disk", "rm": False, "hotplug": False, "tran": "sata", "size": "100G", "children": [
        {"name": "sdd1", "path": "/dev/sdd1", "type": "part", "fstype": "swap", "size": "8G", "mountpoints": ["[SWAP]"]},
        {"name": "sdd2", "path": "/dev/sdd2", "type": "part", "fstype": "ext4", "size": "92G", "mountpoints": []}]},
]}


def places():
    return cp.find_places(cp.parse_disks(json.dumps(LSBLK)))


class PlaceTests(unittest.TestCase):
    def test_the_system_disk_is_never_listed(self):
        devices = [p.device for p in places()]
        self.assertFalse(any(d.startswith("/dev/nvme0n1") for d in devices))
        self.assertNotIn("/", [p.path for p in places()])
        self.assertNotIn("/home", [p.path for p in places()])

    def test_usb_and_other_internal_drives_are_listed_with_kinds(self):
        by_dev = {p.device: p for p in places()}
        self.assertEqual(by_dev["/dev/sdb1"].kind, "usb")
        self.assertEqual(by_dev["/dev/sdb1"].path, "/run/media/user/STICK")
        self.assertEqual(by_dev["/dev/sda1"].kind, "drive")
        self.assertEqual(by_dev["/dev/sda1"].title, "Games")
        self.assertEqual(by_dev["/dev/sr0"].kind, "disc")
        self.assertEqual([p.kind for p in places()][0], "usb")           # usb first

    def test_unmounted_partitions_are_offered_but_have_no_path(self):
        backup = {p.device: p for p in places()}["/dev/sdc1"]
        self.assertFalse(backup.mounted)

    def test_locked_luks_and_swap_are_not_browsable(self):
        devices = [p.device for p in places()]
        self.assertNotIn("/dev/sdc2", devices)
        self.assertNotIn("/dev/zram0", devices)

    def test_a_disk_holding_swap_counts_as_the_systems(self):
        self.assertNotIn("/dev/sdd2", [p.device for p in places()])

    def test_a_place_can_never_be_rooted_at_a_system_path(self):
        odd = {"blockdevices": [{"name": "sde", "path": "/dev/sde", "type": "disk", "size": "1T", "children": [
            {"name": "sde1", "path": "/dev/sde1", "type": "part", "fstype": "ext4", "size": "1T", "mountpoints": ["/mnt"]}]}]}
        self.assertEqual(cp.find_places(cp.parse_disks(json.dumps(odd))), [])

    def test_bad_input_gives_nothing(self):
        self.assertEqual(cp.parse_disks(""), [])
        self.assertEqual(cp.parse_disks("not json"), [])
        self.assertEqual(cp.find_places([]), [])

    def test_lsblk_override_file(self):
        f = os.path.join(tempfile.mkdtemp(), "l.json")
        open(f, "w").write(json.dumps(LSBLK))
        os.environ["COSMOS_LSBLK_JSON"] = f
        try:
            self.assertEqual(len(cp.find_places(cp.parse_disks(cp.read_lsblk()))), len(places()))
        finally:
            del os.environ["COSMOS_LSBLK_JSON"]


class ScopeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.profile = os.path.join(self.tmp, "profile", "files")
        self.usb = os.path.join(self.tmp, "usb")
        os.makedirs(os.path.join(self.usb, "Holiday"))
        open(os.path.join(self.usb, "Holiday", "a.txt"), "w").write("a")
        self.scope = cp.Scope([cp.profile_place("Skye", self.profile), cp.Place("dev:/dev/sdb1", "STICK", "usb", self.usb)])
        self.secret = os.path.join(self.tmp, "profile", "profile.json")      # beside the files folder, like the real one
        open(self.secret, "w").write("{pin}")

    def test_inside_paths_resolve_and_outside_ones_do_not(self):
        self.assertIsNotNone(self.scope.locate(self.usb))
        self.assertIsNotNone(self.scope.locate(os.path.join(self.usb, "Holiday")))
        for outside in ("/", "/etc", "/home", self.tmp, os.path.dirname(self.profile), self.secret):
            self.assertIsNone(self.scope.locate(outside), outside)
            with self.assertRaises(cp.OutOfScope):
                self.scope.check(outside)

    def test_dotdot_and_symlinks_cannot_escape(self):
        self.assertIsNone(self.scope.locate(os.path.join(self.usb, "..", "profile", "profile.json")))
        os.symlink("/", os.path.join(self.usb, "rootlink"))
        os.symlink(self.secret, os.path.join(self.profile, "pinlink"))
        self.assertIsNone(self.scope.locate(os.path.join(self.usb, "rootlink")))
        self.assertIsNone(self.scope.locate(os.path.join(self.usb, "rootlink", "etc")))
        self.assertIsNone(self.scope.locate(os.path.join(self.profile, "pinlink")))

    def test_prefix_lookalikes_are_not_inside(self):
        lookalike = self.usb + "-evil"
        os.makedirs(lookalike)
        self.assertIsNone(self.scope.locate(lookalike))

    def test_titles_never_show_absolute_paths(self):
        self.assertEqual(self.scope.relative_title(os.path.join(self.usb, "Holiday")), "STICK / Holiday")
        self.assertEqual(self.scope.relative_title(self.usb), "STICK")
        self.assertEqual(self.scope.relative_title("/etc"), "")

    def test_the_profile_place_is_only_its_files_folder(self):
        self.assertTrue(os.path.isdir(self.profile))


class FileOpsTests(ScopeTests):
    def test_listing_hides_escaping_links_dotfiles_and_sorts_folders_first(self):
        os.symlink("/", os.path.join(self.usb, "rootlink"))
        open(os.path.join(self.usb, ".hidden"), "w").write("x")
        open(os.path.join(self.usb, "b.txt"), "w").write("bb")
        names = [r[0] for r in fo.list_dir(self.scope, self.usb)]
        self.assertEqual(names, ["Holiday", "b.txt"])
        self.assertIn(".hidden", [r[0] for r in fo.list_dir(self.scope, self.usb, show_hidden=True)])

    def test_listing_outside_the_scope_is_refused(self):
        with self.assertRaises(cp.OutOfScope):
            fo.list_dir(self.scope, "/")

    def test_copy_keeps_symlinks_as_symlinks_and_names_collisions(self):
        os.symlink("/etc", os.path.join(self.usb, "Holiday", "link"))
        target = fo.copy_into(self.scope, os.path.join(self.usb, "Holiday"), self.profile)
        self.assertTrue(os.path.islink(os.path.join(target, "link")))
        again = fo.copy_into(self.scope, os.path.join(self.usb, "Holiday"), self.profile)
        self.assertNotEqual(target, again)
        self.assertTrue(again.endswith("Holiday (2)"))

    def test_copy_or_move_out_of_scope_is_refused(self):
        with self.assertRaises(cp.OutOfScope):
            fo.copy_into(self.scope, "/etc/hostname", self.profile)
        with self.assertRaises(cp.OutOfScope):
            fo.copy_into(self.scope, os.path.join(self.usb, "Holiday", "a.txt"), "/tmp")
        with self.assertRaises(cp.OutOfScope):
            fo.move_into(self.scope, self.secret, self.profile)

    def test_a_folder_cannot_go_into_itself(self):
        with self.assertRaises(ValueError):
            fo.copy_into(self.scope, os.path.join(self.usb, "Holiday"), os.path.join(self.usb, "Holiday"))

    def test_move_new_folder_rename_delete(self):
        moved = fo.move_into(self.scope, os.path.join(self.usb, "Holiday", "a.txt"), self.profile)
        self.assertTrue(os.path.exists(moved))
        folder = fo.new_folder(self.scope, self.profile)
        self.assertTrue(folder.endswith("New folder"))
        self.assertTrue(fo.new_folder(self.scope, self.profile).endswith("New folder (2)"))
        renamed = fo.rename(self.scope, folder, "Pictures")
        self.assertTrue(os.path.isdir(renamed))
        for bad in ("", "a/b", "..", "."):
            with self.assertRaises(ValueError):
                fo.rename(self.scope, renamed, bad)
        fo.delete(self.scope, renamed)
        fo.delete(self.scope, moved)
        self.assertFalse(os.path.exists(renamed))

    def test_deleting_a_symlink_removes_only_the_link(self):
        link = os.path.join(self.profile, "l")
        os.symlink(self.usb, link)
        fo.delete(self.scope, link)
        self.assertFalse(os.path.lexists(link))
        self.assertTrue(os.path.isdir(self.usb))

    def test_human_sizes(self):
        self.assertEqual(fo.human_size(999), "999 B")
        self.assertEqual(fo.human_size(1500), "1.5 KB")


if __name__ == "__main__":
    unittest.main()
