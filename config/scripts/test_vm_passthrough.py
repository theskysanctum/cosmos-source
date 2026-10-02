#!/usr/bin/env python3
"""Tests for vm_passthrough against a fake sysfs/devfs tree. Run: python3 test_vm_passthrough.py"""
import os
import resource
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import vm_passthrough as vp  # noqa: E402


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        fh.write(text)


class FakeHost:
    def __init__(self):
        self.root = tempfile.mkdtemp()
        self.sys = os.path.join(self.root, "sys")
        self.dev = os.path.join(self.root, "dev")
        # USB: a hub (skipped), an interface node (skipped), a mouse and a gamepad
        for name, vid, pid, cls, man, prod, bus, dev in (
            ("1-0", "1d6b", "0002", "09", "Linux", "xHCI Host Controller", 1, 1),
            ("1-2", "046d", "c52b", "00", "Logitech", "USB Receiver", 1, 4),
            ("1-3", "045e", "028e", "ff", "Microsoft", "Xbox360 Controller", 1, 7),
        ):
            d = f"{self.sys}/bus/usb/devices/{name}"
            for k, v in (("idVendor", vid), ("idProduct", pid), ("bDeviceClass", cls), ("manufacturer", man),
                         ("product", prod), ("busnum", bus), ("devnum", dev)):
                write(f"{d}/{k}", str(v))
        os.makedirs(f"{self.sys}/bus/usb/devices/1-2:1.0")
        # input by-id
        for name in ("usb-Logitech_K120-event-kbd", "usb-Logitech_M90-event-mouse", "usb-Logitech_K120-event-if01"):
            write(f"{self.dev}/input/by-id/{name}", "")
        # PCI: GPU + its audio function in group 14, an NVMe in group 3, a host bridge
        self.pci("0000:00:00.0", 0x060000, "8086", "1234", None, None)
        self.pci("0000:01:00.0", 0x030000, "10de", "2684", "vfio-pci", "14", boot_vga="0")
        self.pci("0000:01:00.1", 0x040300, "10de", "22ba", "snd_hda_intel", "14")
        self.pci("0000:02:00.0", 0x010802, "144d", "a80a", "nvme", "3")
        os.makedirs(f"{self.sys}/kernel/iommu_groups/14", exist_ok=True)

    def pci(self, address, klass, vendor, device, driver, group, boot_vga=None):
        d = f"{self.sys}/bus/pci/devices/{address}"
        write(f"{d}/class", f"0x{klass:06x}")
        write(f"{d}/vendor", f"0x{vendor}")
        write(f"{d}/device", f"0x{device}")
        if boot_vga is not None:
            write(f"{d}/boot_vga", boot_vga)
        if driver:
            target = f"{self.sys}/bus/pci/drivers/{driver}"
            os.makedirs(target, exist_ok=True)
            os.symlink(target, f"{d}/driver")
        if group:
            target = f"{self.sys}/kernel/iommu_groups/{group}"
            os.makedirs(target, exist_ok=True)
            os.symlink(target, f"{d}/iommu_group")


class EnumerationTests(unittest.TestCase):
    def setUp(self):
        self.h = FakeHost()

    def test_usb_lists_real_devices_only(self):
        ids = [d["id"] for d in vp.list_usb_devices(self.h.sys)]
        self.assertEqual(ids, ["046d:c52b", "045e:028e"])          # no hub, no interface node
        self.assertEqual(vp.list_usb_devices(self.h.sys)[1]["label"], "Microsoft Xbox360 Controller")

    def test_input_devices_are_keyboards_and_mice(self):
        kinds = sorted(d["kind"] for d in vp.list_input_devices(self.h.dev))
        self.assertEqual(kinds, ["kbd", "mouse"])

    def test_pci_lists_endpoints_with_drivers_and_groups(self):
        devices = {d["address"]: d for d in vp.list_pci_devices(self.h.sys, with_names=False)}
        self.assertNotIn("0000:00:00.0", devices)                   # host bridge is not passable
        self.assertTrue(devices["0000:01:00.0"]["gpu"])
        self.assertEqual(devices["0000:01:00.0"]["driver"], "vfio-pci")
        self.assertEqual(devices["0000:01:00.1"]["driver"], "snd_hda_intel")
        self.assertEqual(devices["0000:02:00.0"]["group"], "3")
        peers = vp.group_endpoints("0000:01:00.0", list(devices.values()))
        self.assertEqual(sorted(d["address"] for d in peers), ["0000:01:00.0", "0000:01:00.1"])


class PreflightTests(unittest.TestCase):
    def setUp(self):
        self.h = FakeHost()

    def check(self, pt, **kw):
        vm = {"ram_mb": 4096, "uefi": True, "passthrough": {"enabled": True, **pt}}
        kw.setdefault("is_root", True)
        kw.setdefault("memlock_limit", resource.RLIM_INFINITY)
        return vp.preflight(vm, sysfs=self.h.sys, devfs=self.h.dev, **kw)

    def test_disabled_means_no_checks(self):
        self.assertEqual(vp.preflight({"passthrough": {"enabled": False, "pci": ["9999:99:99.9"]}}), ([], []))

    def test_gpu_group_needs_the_whole_group(self):
        errors, _ = self.check({"pci": ["0000:01:00.0"]})
        self.assertTrue(any("shares IOMMU group 14" in e and "01:00.1" in e for e in errors), errors)
        errors, _ = self.check({"pci": ["0000:01:00.0", "0000:01:00.1"]})
        # the audio function is selected now, but is still bound to a host driver
        self.assertTrue(any("snd_hda_intel" in e and "never unbinds" in e for e in errors), errors)

    def test_bound_device_is_refused_with_the_fix(self):
        errors, _ = self.check({"pci": ["0000:02:00.0"]})
        self.assertTrue(any("nvme" in e and "vfio-pci.ids=144d:a80a" in e for e in errors), errors)

    def test_iommu_off_is_an_error(self):
        import shutil
        shutil.rmtree(f"{self.h.sys}/kernel/iommu_groups")
        errors, _ = self.check({"pci": ["0000:01:00.0"]})
        self.assertTrue(any("IOMMU is off" in e for e in errors), errors)

    def test_locked_memory_limit(self):
        errors, _ = self.check({"pci": ["0000:01:00.0", "0000:01:00.1"]}, memlock_limit=64 * 1024 * 1024)
        self.assertTrue(any("locked-memory limit" in e for e in errors), errors)

    def test_missing_usb_and_unreadable_input(self):
        errors, _ = self.check({"usb": ["dead:beef"], "input": ["/nonexistent/kbd"]})
        self.assertEqual(len(errors), 2)

    def test_usb_needs_device_access_unless_root(self):
        node = f"{self.h.dev}/bus/usb/001/004"
        write(node, "")
        os.chmod(node, 0)
        errors, _ = self.check({"usb": ["046d:c52b"]}, is_root=False)
        self.assertTrue(any("kvm group" in e for e in errors) or os.access(node, os.R_OK), errors)

    def test_clean_config_passes(self):
        errors, warnings = self.check({"audio": True, "usb": ["046d:c52b"]})
        self.assertEqual((errors, warnings), ([], []))

    def test_warnings(self):
        _, warnings = vp.preflight({"ram_mb": 1, "uefi": False, "passthrough": {"enabled": True, "pci": ["0000:01:00.0"]}},
                                    sysfs=self.h.sys, devfs=self.h.dev, is_root=True, memlock_limit=resource.RLIM_INFINITY)
        self.assertTrue(any("legacy BIOS" in w for w in warnings))


class ArgumentTests(unittest.TestCase):
    BASE = ["qemu-system-x86_64", "-machine", "q35,accel=kvm", "-cpu", "host", "-m", "4096", "-vga", "std", "-display", "gtk"]

    def build(self, pt):
        return vp.apply_passthrough(self.BASE, {"passthrough": {"enabled": True, "audio": False, **pt}}, backend="pipewire")

    def test_off_changes_nothing(self):
        self.assertEqual(vp.apply_passthrough(self.BASE, {}), self.BASE)
        self.assertEqual(vp.apply_passthrough(self.BASE, {"passthrough": {"enabled": False, "audio": True}}), self.BASE)

    def test_audio(self):
        cmd = vp.apply_passthrough(self.BASE, {"passthrough": {"enabled": True, "audio": True}}, backend="pipewire")
        self.assertEqual(cmd[len(self.BASE):], ["-audiodev", "pipewire,id=cosmos-snd", "-device", "ich9-intel-hda",
                                                "-device", "hda-duplex,audiodev=cosmos-snd"])

    def test_usb_uses_one_controller(self):
        cmd = self.build({"usb": ["046d:c52b", "045e:028e"]})
        self.assertEqual(cmd.count("qemu-xhci,id=cosmos-xhci"), 1)
        self.assertIn("usb-host,bus=cosmos-xhci.0,vendorid=0x045e,productid=0x028e", cmd)

    def test_input_grabs_keyboards_fully(self):
        cmd = self.build({"input": ["/dev/input/by-id/a-event-kbd", "/dev/input/by-id/b-event-mouse"]})
        self.assertIn("input-linux,id=cosmos-in0,evdev=/dev/input/by-id/a-event-kbd,grab_all=on,repeat=on", cmd)
        self.assertIn("input-linux,id=cosmos-in1,evdev=/dev/input/by-id/b-event-mouse", cmd)

    def test_pci_and_gpu_only(self):
        cmd = self.build({"pci": ["0000:01:00.0", "0000:01:00.1"], "gpu_only": True})
        self.assertIn("vfio-pci,host=0000:01:00.0", cmd)
        self.assertEqual(cmd[cmd.index("-vga") + 1], "none")
        self.assertEqual(cmd[cmd.index("-display") + 1], "none")
        cmd = self.build({"pci": ["0000:01:00.0"]})                # without gpu_only the emulated display stays
        self.assertEqual(cmd[cmd.index("-vga") + 1], "std")

    def test_hide_kvm(self):
        cmd = self.build({"hide_kvm": True})
        self.assertEqual(cmd[cmd.index("-cpu") + 1], "host,kvm=off,hv_vendor_id=cosmos0")
        self.assertEqual(cmd.count("host"), 0)

    def test_summary(self):
        self.assertEqual(vp.summary({}), "")
        self.assertEqual(vp.summary({"passthrough": {"enabled": True, "audio": True, "usb": ["a:b", "c:d"], "pci": ["x"]}}),
                         "audio, 2 USB, 1 PCI")


class InputGrantTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.d = tempfile.mkdtemp()
        self.kbd = os.path.join(self.d, "usb-K-event-kbd")
        open(self.kbd, "w").close()
        os.chmod(self.kbd, 0)                                               # not readable, like a real /dev/input node
        self.vm = {"passthrough": {"enabled": True, "input": [self.kbd]}}

    def test_only_unreadable_chosen_devices_are_asked_for_and_the_ask_is_kronos_input_grant(self):
        if os.access(self.kbd, os.R_OK):
            self.skipTest("running as root: everything is readable")
        seen = []
        granted, errors = vp.grant_inputs(self.vm, lambda action, args: (seen.append((action, args)), {"status": "ok"})[1])
        self.assertEqual((granted, errors), ([self.kbd], []))
        self.assertEqual(seen, [("input.grant", {"path": self.kbd})])
        seen.clear()
        vp.revoke_inputs(granted, lambda action, args: (seen.append((action, args)), {"status": "ok"})[1])
        self.assertEqual(seen, [("input.revoke", {"path": self.kbd})])

    def test_a_refused_or_cancelled_grant_is_an_error_and_stops(self):
        if os.access(self.kbd, os.R_OK):
            self.skipTest("running as root")
        granted, errors = vp.grant_inputs(self.vm, lambda a, args: {"status": "refused", "reason": "Cancelled."})
        self.assertEqual((granted, errors), ([], ["Cancelled."]))

    def test_nothing_is_asked_when_no_input_device_is_chosen(self):
        granted, errors = vp.grant_inputs({"passthrough": {"enabled": True}}, lambda *a: self.fail("asked"))
        self.assertEqual((granted, errors), ([], []))


if __name__ == "__main__":
    unittest.main()
