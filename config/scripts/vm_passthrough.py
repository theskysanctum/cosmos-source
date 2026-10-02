"""Host-device passthrough for cosmos-vm-manager: audio, USB devices (controllers,
keyboards, ...), host keyboard/mouse (evdev) and whole PCI devices - GPUs, NICs,
NVMe, capture cards - through VFIO.

No GTK in here: enumerating what the host has, turning a VM's "passthrough" settings
into qemu arguments, and checking beforehand that the launch can actually work, so
that a VM which cannot get its GPU says why instead of failing inside qemu.

    "passthrough": {
        "enabled": true,          # the master checkbox
        "audio": true,            # host audio through PipeWire/PulseAudio/ALSA
        "usb": ["046d:c52b"],     # vendor:product ids of USB devices to hand over
        "input": ["/dev/input/by-id/usb-...-event-kbd"],   # host evdev keyboards/mice
        "pci": ["0000:01:00.0", "0000:01:00.1"],           # VFIO PCI devices
        "gpu_only": false,        # drop the emulated display when a GPU is passed
        "hide_kvm": false,        # hide KVM from the guest (NVIDIA driver in Windows guests)
    }

What this deliberately does NOT do: it never rebinds a host device to vfio-pci. Doing
that automatically can take the display the host is running on away from it. A PCI
device that is still bound to a host driver is refused with the reason and the fix.
"""
import glob
import os
import re
import resource
import shutil
import subprocess

SYSFS = "/sys"
DEVFS = "/dev"

PCI_CLASS_NAMES = {
    0x0300: "GPU (VGA)", 0x0302: "GPU (3D)", 0x0380: "GPU", 0x0403: "Audio", 0x0200: "Network",
    0x0280: "Network", 0x0108: "NVMe", 0x0106: "SATA", 0x0c03: "USB controller", 0x0c80: "Serial bus",
    0x0400: "Multimedia (video)", 0x0480: "Multimedia",
}


class PassthroughError(Exception):
    """The launch cannot work as configured; the message says what to do."""


def _read(path, default=""):
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read().strip()
    except OSError:
        return default


def default_settings():
    return {"enabled": False, "audio": True, "usb": [], "input": [], "pci": [], "gpu_only": False,
            "hide_kvm": False}


def settings_of(vm):
    settings = default_settings()
    settings.update(vm.get("passthrough") or {})
    return settings


def summary(vm):
    """'audio, 2 USB, 1 PCI' for the VM list, or '' when off."""
    pt = settings_of(vm)
    if not pt["enabled"]:
        return ""
    parts = []
    if pt["audio"]:
        parts.append("audio")
    for key, label in (("usb", "USB"), ("input", "input"), ("pci", "PCI")):
        if pt[key]:
            parts.append(f"{len(pt[key])} {label}")
    return ", ".join(parts) or "nothing selected"


# ---------------------------------------------------------------------
# What the host has
# ---------------------------------------------------------------------
def list_usb_devices(sysfs=SYSFS):
    devices = []
    for path in sorted(glob.glob(os.path.join(sysfs, "bus/usb/devices/*"))):
        name = os.path.basename(path)
        if ":" in name or name.startswith("usb"):        # interfaces and root hubs
            continue
        vendor, product = _read(os.path.join(path, "idVendor")), _read(os.path.join(path, "idProduct"))
        if not vendor or not product or _read(os.path.join(path, "bDeviceClass")) == "09":   # hubs
            continue
        label = " ".join(x for x in (_read(os.path.join(path, "manufacturer")), _read(os.path.join(path, "product"))) if x)
        devices.append({
            "id": f"{vendor}:{product}", "label": label or f"USB device {vendor}:{product}",
            "bus": int(_read(os.path.join(path, "busnum"), "0") or 0),
            "dev": int(_read(os.path.join(path, "devnum"), "0") or 0),
        })
    return devices


def list_input_devices(devfs=DEVFS):
    devices = []
    for path in sorted(glob.glob(os.path.join(devfs, "input/by-id/*"))):
        match = re.search(r"-event-(kbd|mouse)$", path)
        if match:
            devices.append({"path": path, "kind": match.group(1),
                            "label": os.path.basename(path).replace("usb-", "").replace(f"-event-{match.group(1)}", "")
                            .replace("_", " ") + f" ({'keyboard' if match.group(1) == 'kbd' else 'mouse'})"})
    return devices


def unreadable_inputs(vm, devfs=DEVFS):
    """The chosen host keyboards/mice this account can't read - which is all of them: it is not in the `input` group, because that would let
    any program read every key typed (the admin passphrase too)."""
    return [p for p in settings_of(vm)["input"] if os.path.exists(p) and not os.access(p, os.R_OK)]


def grant_inputs(vm, ask=None):
    """([granted paths], [error texts]). Asks kronos (admin passphrase) to let this account read each chosen keyboard/mouse, for as long as
    the VM runs - revoke_inputs() gives it back."""
    if ask is None:
        import cosmos_kronos
        ask = cosmos_kronos.ask
    granted, errors = [], []
    for path in unreadable_inputs(vm):
        result = ask("input.grant", {"path": path})
        if result.get("status") == "ok":
            granted.append(path)
        else:
            errors.append(result.get("reason") or (result.get("output") or "").strip() or f"Couldn't get access to {path}.")
            break
    return granted, errors


def revoke_inputs(paths, ask=None):
    if ask is None:
        import cosmos_kronos
        ask = cosmos_kronos.ask
    for path in paths:
        ask("input.revoke", {"path": path})


def _pci_name(address):
    if shutil.which("lspci"):
        try:
            out = subprocess.run(["lspci", "-mm", "-s", address], capture_output=True, text=True, timeout=5).stdout
            fields = re.findall(r'"([^"]*)"', out)
            if len(fields) >= 3:
                return f"{fields[1]} {fields[2]}"
        except (OSError, subprocess.SubprocessError):
            pass
    return ""


def list_pci_devices(sysfs=SYSFS, with_names=True):
    """Endpoint devices only (bridges and host controllers are not passable)."""
    devices = []
    for path in sorted(glob.glob(os.path.join(sysfs, "bus/pci/devices/*"))):
        address = os.path.basename(path)
        try:
            klass = int(_read(os.path.join(path, "class"), "0x0"), 16) >> 8
        except ValueError:
            continue
        if klass >> 8 == 0x06 or klass == 0x0000:                    # bridges, host bridges
            continue
        driver_link = os.path.join(path, "driver")
        driver = os.path.basename(os.path.realpath(driver_link)) if os.path.islink(driver_link) else ""
        group_link = os.path.join(path, "iommu_group")
        group = os.path.basename(os.path.realpath(group_link)) if os.path.islink(group_link) else ""
        vendor, device = _read(os.path.join(path, "vendor"))[2:], _read(os.path.join(path, "device"))[2:]
        kind = PCI_CLASS_NAMES.get(klass, f"class {klass:04x}")
        name = (_pci_name(address) if with_names else "") or f"{vendor}:{device}"
        devices.append({
            "address": address, "class": klass, "kind": kind, "driver": driver, "group": group,
            "gpu": klass >> 8 == 0x03, "boot_vga": _read(os.path.join(path, "boot_vga")) == "1",
            "ids": f"{vendor}:{device}", "label": f"{address}  {kind}: {name}",
        })
    return devices


def iommu_enabled(sysfs=SYSFS):
    return bool(glob.glob(os.path.join(sysfs, "kernel/iommu_groups/*")))


def group_endpoints(address, pci_devices):
    """Every passable device in the same IOMMU group as `address`."""
    mine = next((d for d in pci_devices if d["address"] == address), None)
    return [d for d in pci_devices if mine and mine["group"] and d["group"] == mine["group"]]


def audio_backend(qemu="qemu-system-x86_64"):
    """The best host audio backend this qemu was built with."""
    try:
        out = subprocess.run([qemu, "-audiodev", "help"], capture_output=True, text=True, timeout=10)
        available = set((out.stdout + out.stderr).split())
    except (OSError, subprocess.SubprocessError):
        available = set()
    for backend in ("pipewire", "pa", "alsa", "sdl"):
        if backend in available:
            return backend
    return "pa"


# ---------------------------------------------------------------------
# Checking, then building the command
# ---------------------------------------------------------------------
def preflight(vm, sysfs=SYSFS, devfs=DEVFS, is_root=None, memlock_limit=None, pci_devices=None, usb_devices=None):
    """(errors, warnings): errors mean the launch cannot work; warnings mean it might
    not behave. Everything is read from the given roots so tests can fake a host."""
    pt = settings_of(vm)
    errors, warnings = [], []
    if not pt["enabled"]:
        return errors, warnings
    is_root = (os.geteuid() == 0) if is_root is None else is_root

    if pt["usb"]:
        present = {d["id"]: d for d in (usb_devices if usb_devices is not None else list_usb_devices(sysfs))}
        for vidpid in pt["usb"]:
            device = present.get(vidpid)
            if device is None:
                errors.append(f"USB device {vidpid} is not plugged in.")
                continue
            node = os.path.join(devfs, f"bus/usb/{device['bus']:03d}/{device['dev']:03d}")
            if not is_root and os.path.exists(node) and not os.access(node, os.R_OK | os.W_OK):
                errors.append(f"No access to {device['label']} ({node}). Add yourself to the kvm group and re-plug "
                              "it (the image's udev rule grants that group USB access).")
    for path in pt["input"]:
        if not os.path.exists(path):
            errors.append(f"Input device {path} is not connected.")
        elif not is_root and not os.access(path, os.R_OK):
            errors.append(f"No permission to read {path}. Passing a host keyboard or mouse through needs the admin passphrase (asked when the VM starts).")

    if pt["pci"]:
        pci = pci_devices if pci_devices is not None else list_pci_devices(sysfs)
        by_address = {d["address"]: d for d in pci}
        if not iommu_enabled(sysfs):
            errors.append("The IOMMU is off. Add intel_iommu=on (or amd_iommu=on) and iommu=pt to the kernel "
                          "arguments, enable VT-d/AMD-Vi in the firmware, and reboot.")
        selected = set(pt["pci"])
        for address in pt["pci"]:
            device = by_address.get(address)
            if device is None:
                errors.append(f"PCI device {address} is not present.")
                continue
            if device["driver"] != "vfio-pci":
                errors.append(f"{device['label']} is bound to '{device['driver'] or 'no driver'}', not vfio-pci. "
                              "This app never unbinds a host device (that can take your display away): bind it to "
                              f"vfio-pci first, e.g. add vfio-pci.ids={device['ids']} to the kernel arguments and "
                              "reboot (the host then cannot use it).")
            if device["boot_vga"]:
                warnings.append(f"{address} is the GPU the firmware booted the host on.")
            for peer in group_endpoints(address, pci):
                if peer["address"] not in selected and peer["driver"] != "vfio-pci":
                    errors.append(f"{address} shares IOMMU group {device['group']} with {peer['label']}, which is "
                                  "neither selected nor bound to vfio-pci - VFIO needs the whole group.")
            if not is_root and device["group"] and not os.access(os.path.join(devfs, "vfio", device["group"]), os.R_OK | os.W_OK):
                errors.append(f"No access to /dev/vfio/{device['group']}. Add yourself to the kvm group (the "
                              "image's udev rule grants it).")
        limit = memlock_limit if memlock_limit is not None else resource.getrlimit(resource.RLIMIT_MEMLOCK)[0]
        needed = int(vm.get("ram_mb", 0)) * 1024 * 1024
        if limit != resource.RLIM_INFINITY and limit < needed:
            errors.append(f"The locked-memory limit ({limit // (1024 * 1024)} MB) is below the VM's RAM "
                          f"({vm.get('ram_mb')} MB), which VFIO needs. The kvm group gets an unlimited limit from "
                          "/etc/security/limits.d/90-cosmos-vfio.conf after you log out and in again.")
        if any(by_address.get(a, {}).get("gpu") for a in pt["pci"]) and not vm.get("uefi", True):
            warnings.append("A passed-through GPU normally needs UEFI firmware; this VM uses legacy BIOS.")
    return errors, warnings


def apply_passthrough(cmd, vm, qemu="qemu-system-x86_64", backend=None):
    """The qemu command line with this VM's passthrough devices added."""
    pt = settings_of(vm)
    if not pt["enabled"]:
        return list(cmd)
    cmd = list(cmd)
    if pt["hide_kvm"]:
        cmd = [("host,kvm=off,hv_vendor_id=cosmos0" if prev == "-cpu" and arg == "host" else arg)
               for prev, arg in zip([""] + cmd, cmd)]
    if pt["gpu_only"] and pt["pci"]:
        # The GPU's own output is the display: no emulated VGA, no host window.
        for flag, value in (("-vga", "none"), ("-display", "none")):
            if flag in cmd:
                cmd[cmd.index(flag) + 1] = value
    if pt["audio"]:
        cmd += ["-audiodev", f"{backend or audio_backend(qemu)},id=cosmos-snd",
                "-device", "ich9-intel-hda", "-device", "hda-duplex,audiodev=cosmos-snd"]
    if pt["usb"]:
        cmd += ["-device", "qemu-xhci,id=cosmos-xhci"]
        for vidpid in pt["usb"]:
            vendor, product = vidpid.split(":")
            cmd += ["-device", f"usb-host,bus=cosmos-xhci.0,vendorid=0x{vendor},productid=0x{product}"]
    for number, path in enumerate(pt["input"]):
        extra = ",grab_all=on,repeat=on" if path.endswith("-event-kbd") else ""
        cmd += ["-object", f"input-linux,id=cosmos-in{number},evdev={path}{extra}"]
    for address in pt["pci"]:
        cmd += ["-device", f"vfio-pci,host={address}"]
    return cmd
