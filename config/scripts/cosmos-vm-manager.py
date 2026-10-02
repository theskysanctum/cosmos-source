#!/usr/bin/env python3
"""CosmOS's own QEMU front end - a real GTK3 app (not another
bar widget; opened from the Applications menu like Nautilus/GIMP) that
wraps the qemu-system-x86_64/edk2-ovmf combo already baked into this
image for GameBox's/this repo's own test_bootc_boot.sh runs
(see Containerfile's own comment on why those are installed at all),
so running an ad-hoc VM doesn't mean hand-typing the same -machine/
-drive/-netdev flags every time.

Same visual language as cosmos-bar's own power menu/Add Web App dialog
on purpose (identical BG/FG/ACCENT/BORDER, same rofi-matching
border-color) - this should read as part of the same desktop, not a
bolted-on separate app with its own theme. Deliberately its own
process/file rather than another window cosmos-bar.py itself builds:
this has real, persistent per-VM state (config/scripts/cosmos-bar.py
has none beyond a Brave-app .desktop scan), a file chooser, and a
lifetime (stays open while a VM runs) that don't fit the bar's own
"build once at startup, live for the X session" model.

VM configs persist as plain JSON at ~/.config/cosmos/vms.json -
one file, no database, matching this project's own "no more machinery
than the actual need" convention (see packaging/dotenv_config.py's own
docstring in the os repo for the same reasoning applied to a different
file). Each UEFI VM gets its own persistent OVMF_VARS.fd copy under
~/.config/cosmos/vm-firmware/ - shared vars across VMs would
mean one VM's boot-order/Secure-Boot-state changes silently leak into
every other VM's firmware, the same real bug class
install_in_disposable_vm.sh's own docstring documents for the actual
host's UEFI NVRAM (just scoped to QEMU's virtual firmware instead of
real hardware here, so it's an annoyance rather than a real incident).
"""
import json
import os
import shutil
import subprocess
import threading
import uuid

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GLib, Gtk, Pango  # noqa: E402
import cosmos_display  # noqa: E402
import cosmos_settings_link  # noqa: E402

import vm_passthrough

BG = "#0a0e16"
FG = "#e8ecf4"
ACCENT = "#ff0090"
# Same literal border_muted value as config/rofi/config.rasi and
# cosmos-bar.py's own power menu - this should look like it belongs to
# the same desktop, not a differently-themed dialog that happens to
# also be dark.
BORDER = "#2a3a56"

CONFIG_DIR = os.path.expanduser("~/.config/cosmos")
VMS_PATH = os.path.join(CONFIG_DIR, "vms.json")
FIRMWARE_DIR = os.path.join(CONFIG_DIR, "vm-firmware")

OVMF_CODE = "/usr/share/edk2/ovmf/OVMF_CODE.fd"
OVMF_VARS_TEMPLATE = "/usr/share/edk2/ovmf/OVMF_VARS.fd"

CSS = f"""
window.vm-manager {{
    background-color: {BG};
    border: 2px solid {BORDER};
}}
/* GtkListBox/GtkScrolledWindow/GtkViewport all keep GTK's stock white
background by default - .vm-manager-row alone (the manually-classed
inner box each row builds) only covers the row's own content area, not
the "list" (the ListBox itself) or the scrolledwindow/viewport wrapping
it, which is what was actually showing through as a real white block
below/around the rows (confirmed live via a real screenshot, not
assumed) until these were added. */
window.vm-manager list,
window.vm-manager scrolledwindow,
window.vm-manager viewport,
window.vm-manager row {{
    background-color: {BG};
}}
.vm-manager-label {{
    color: {FG};
}}
.vm-manager-row {{
    background-color: {BG};
    border-bottom: 1px solid {BORDER};
    padding: 6px;
}}
.vm-manager-row:hover {{
    background-color: alpha({ACCENT}, 0.15);
}}
.vm-manager-entry {{
    background-color: {BG};
    background-image: none;
    color: {FG};
    caret-color: {FG};
    border: 1px solid {BORDER};
    box-shadow: none;
}}
.vm-manager-entry:focus {{
    border-color: {ACCENT};
    box-shadow: none;
}}
/* background-image: none is load-bearing, not decoration - Adwaita's
stock GtkButton CSS node ships its own background-image gradient that
otherwise wins over a plain background-color every time (confirmed
live via a real screenshot: only border-color was actually changing,
every button still showed Adwaita's own light gradient fill
underneath). */
.vm-manager-button {{
    background-color: {BG};
    background-image: none;
    color: {FG};
    border: 1px solid {BORDER};
    box-shadow: none;
}}
.vm-manager-button:hover {{
    background-color: {ACCENT};
    background-image: none;
    color: {BG};
    border-color: {ACCENT};
}}
.vm-manager-button-accent {{
    background-color: {ACCENT};
    background-image: none;
    color: {BG};
    border: 1px solid {ACCENT};
    box-shadow: none;
}}
.vm-manager-button-accent:hover {{
    background-color: {FG};
    background-image: none;
    border-color: {FG};
}}
""".encode()


def load_vms():
    try:
        with open(VMS_PATH) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def save_vms(vms):
    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(VMS_PATH, "w") as f:
        json.dump(vms, f, indent=2)


def vm_vars_path(vm):
    return os.path.join(FIRMWARE_DIR, f"{vm['id']}.ovmf_vars.fd")


def start_vm(vm):
    """Launches qemu-system-x86_64 with a real on-screen window
    (-display gtk, not the headless -vnc setup this repo's own boot
    tests use - a GUI VM manager wants the VM's own window to just
    appear like any other application, not need a separate viewer).
    Fire-and-forget subprocess.Popen: the manager window stays open and
    responsive for starting/managing other VMs while this one runs, the
    same reason the bar itself never blocks on a launched app.

    .get() with a default on every field added after the first version
    of this dict shape (disk_bus/boot_order/network_mode/bridge_name) -
    a VM saved before this file knew about them still has a real
    vms.json entry missing those keys, and this should keep launching
    it exactly as before rather than raising a KeyError the next time
    cosmos-vm-manager itself gets a feature added."""
    if not shutil.which("qemu-system-x86_64"):
        raise vm_passthrough.PassthroughError("QEMU isn't installed. Install the Virtualization pack from the Shop.")
    disk_bus = vm.get("disk_bus", "virtio")
    boot_order = vm.get("boot_order", "disk")
    network_mode = vm.get("network_mode", "nat")

    cmd = [
        "qemu-system-x86_64",
        "-machine", "q35,accel=kvm",
        "-cpu", "host",
        "-m", str(vm["ram_mb"]),
        "-smp", str(vm["cpus"]),
        "-vga", "std",
        "-display", "gtk",
        # order=c is "hard disk first", order=d is "CD-ROM first" -
        # qemu's own classic BIOS boot-device letters (a=floppy,
        # c=disk, d=cdrom, n=network), still honored under OVMF/UEFI
        # too (confirmed live: this is what actually lets an ISO
        # install onto a blank disk instead of the VM trying - and
        # failing - to boot the still-empty disk first).
        "-boot", f"order={'d' if boot_order == 'cdrom' else 'c'}",
    ]

    if network_mode == "bridge":
        bridge_name = vm.get("bridge_name") or "br0"
        # Needs a real bridge interface already up on the host (this
        # doesn't create one) and qemu-bridge-helper's usual setuid-root
        # install - genuinely more setup than NAT, which is why NAT
        # stays the default. Visible-on-LAN is the whole point of
        # choosing this over NAT, so it's worth that one-time host-side
        # setup when it's actually needed.
        cmd += ["-netdev", f"bridge,id=net0,br={bridge_name}"]
    else:
        cmd += ["-netdev", "user,id=net0"]
    cmd += ["-device", "virtio-net-pci,netdev=net0"]

    if vm.get("uefi", True):
        os.makedirs(FIRMWARE_DIR, exist_ok=True)
        vars_path = vm_vars_path(vm)
        if not os.path.exists(vars_path):
            shutil.copyfile(OVMF_VARS_TEMPLATE, vars_path)
        cmd += [
            "-drive", f"if=pflash,format=raw,readonly=on,file={OVMF_CODE}",
            "-drive", f"if=pflash,format=raw,file={vars_path}",
        ]

    # ide, not sata: qemu's own `if=` values don't include a distinct
    # "sata" option (AHCI/SATA disks are wired up via a separate
    # -device ahci + -device ide-hd pair instead) - `if=ide` is the
    # actual broadly-compatible choice every "old installer with no
    # VirtIO driver" scenario wants, and matches what "SATA/IDE" in the
    # dialog's own combo box is really offering.
    cmd += ["-drive", f"if={disk_bus},format=raw,file={vm['disk_path']}"]
    if vm.get("iso_path"):
        cmd += ["-cdrom", vm["iso_path"]]

    # Host-device passthrough (the dialog's "Pass through host devices" box). Refuses to
    # start, with the reason, rather than launching a VM that cannot get its devices.
    # Host keyboards/mice: this account can't read them (it isn't in the `input` group), so ask kronos for the chosen ones - admin passphrase -
    # for as long as the VM runs.
    granted, grant_errors = vm_passthrough.grant_inputs(vm)
    errors, warnings = (grant_errors, []) if grant_errors else vm_passthrough.preflight(vm)
    if errors:
        vm_passthrough.revoke_inputs(granted)
        raise vm_passthrough.PassthroughError("\n\n".join(errors))
    cmd = vm_passthrough.apply_passthrough(cmd, vm)
    process = subprocess.Popen(cmd)
    if granted:
        threading.Thread(target=lambda: (process.wait(), vm_passthrough.revoke_inputs(granted)), daemon=True).start()
    return warnings, process


def show_vm_dialog(parent, existing=None, on_save=None):
    """Add/Edit VM - same centered-floating-DIALOG treatment as
    cosmos-bar.py's own show_add_web_app_dialog(), same reasoning: real
    text entry needs a real Gtk.Window, not a Gtk.Menu trying to embed
    one."""
    window = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
    window.set_title("cosmos-vm-manager-edit")
    window.set_type_hint(Gdk.WindowTypeHint.DIALOG)
    window.set_decorated(False)
    window.set_skip_taskbar_hint(True)
    window.set_skip_pager_hint(True)
    window.set_keep_above(True)
    window.set_transient_for(parent)
    window.set_position(Gtk.WindowPosition.CENTER_ALWAYS)
    window.get_style_context().add_class("vm-manager")

    screen_width = Gdk.Screen.get_default().get_width()
    window.set_size_request(int(screen_width * 0.35), -1)

    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    box.set_border_width(14)
    window.add(box)

    def labeled_row(label_text, widget):
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        label = Gtk.Label(label=label_text)
        label.set_width_chars(10)
        label.set_xalign(0)
        label.get_style_context().add_class("vm-manager-label")
        row.pack_start(label, False, False, 0)
        row.pack_start(widget, True, True, 0)
        box.pack_start(row, False, False, 0)
        return row

    name_entry = Gtk.Entry()
    name_entry.set_placeholder_text("Name (e.g. Windows 11 Testing)")
    name_entry.get_style_context().add_class("vm-manager-entry")
    labeled_row("Name", name_entry)

    def path_row(placeholder, chooser_title, filters=()):
        entry_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        entry = Gtk.Entry()
        entry.set_placeholder_text(placeholder)
        entry.get_style_context().add_class("vm-manager-entry")
        entry_box.pack_start(entry, True, True, 0)

        def on_browse(_b):
            dialog = Gtk.FileChooserDialog(
                title=chooser_title,
                parent=window,
                action=Gtk.FileChooserAction.OPEN,
            )
            dialog.add_buttons(
                "Cancel", Gtk.ResponseType.CANCEL, "Select", Gtk.ResponseType.OK
            )
            for name, pattern in filters:
                f = Gtk.FileFilter()
                f.set_name(name)
                f.add_pattern(pattern)
                dialog.add_filter(f)
            if dialog.run() == Gtk.ResponseType.OK:
                entry.set_text(dialog.get_filename())
            dialog.destroy()

        browse = Gtk.Button(label="Browse...")
        browse.get_style_context().add_class("vm-manager-button")
        browse.connect("clicked", on_browse)
        entry_box.pack_start(browse, False, False, 0)
        return entry, entry_box

    disk_entry, disk_box = path_row(
        "Disk image path (.raw/.qcow2)", "Select disk image",
        (("Disk images", "*.raw"), ("Disk images", "*.qcow2"), ("All files", "*")),
    )
    labeled_row("Disk", disk_box)

    iso_entry, iso_box = path_row(
        "ISO to boot (optional)", "Select ISO", (("ISO images", "*.iso"), ("All files", "*")),
    )
    labeled_row("ISO", iso_box)

    ram_adjustment = Gtk.Adjustment(value=4096, lower=512, upper=131072, step_increment=512)
    ram_spin = Gtk.SpinButton(adjustment=ram_adjustment)
    ram_spin.get_style_context().add_class("vm-manager-entry")
    labeled_row("RAM (MB)", ram_spin)

    cpu_adjustment = Gtk.Adjustment(value=2, lower=1, upper=32, step_increment=1)
    cpu_spin = Gtk.SpinButton(adjustment=cpu_adjustment)
    cpu_spin.get_style_context().add_class("vm-manager-entry")
    labeled_row("CPU cores", cpu_spin)

    uefi_check = Gtk.CheckButton(label="UEFI firmware (uncheck for legacy BIOS)")
    uefi_check.set_active(True)
    uefi_check.get_style_context().add_class("vm-manager-label")
    box.pack_start(uefi_check, False, False, 0)

    boot_combo = Gtk.ComboBoxText()
    boot_combo.append("disk", "Disk first")
    boot_combo.append("cdrom", "CD-ROM first (installing onto a blank disk)")
    boot_combo.set_active_id("disk")
    boot_combo.get_style_context().add_class("vm-manager-entry")
    labeled_row("Boot order", boot_combo)

    disk_bus_combo = Gtk.ComboBoxText()
    disk_bus_combo.append("virtio", "VirtIO (faster, needs a modern OS/driver)")
    disk_bus_combo.append("ide", "SATA/IDE (compatible, no driver needed)")
    disk_bus_combo.set_active_id("virtio")
    disk_bus_combo.get_style_context().add_class("vm-manager-entry")
    labeled_row("Disk bus", disk_bus_combo)

    network_combo = Gtk.ComboBoxText()
    network_combo.append("nat", "NAT (outbound only, no setup needed)")
    network_combo.append("bridge", "Bridged (visible on your LAN)")
    network_combo.set_active_id("nat")
    network_combo.get_style_context().add_class("vm-manager-entry")
    labeled_row("Network", network_combo)

    bridge_entry = Gtk.Entry()
    bridge_entry.set_placeholder_text("Bridge interface name (e.g. br0)")
    bridge_entry.set_text("br0")
    bridge_entry.set_sensitive(False)
    bridge_entry.get_style_context().add_class("vm-manager-entry")
    bridge_row = labeled_row("Bridge", bridge_entry)
    network_combo.connect(
        "changed",
        lambda c: bridge_entry.set_sensitive(c.get_active_id() == "bridge"),
    )

    # ---- host-device passthrough -----------------------------------------------
    pt_check = Gtk.CheckButton(label="Pass through host devices (audio, USB, input, GPU...)")
    pt_check.get_style_context().add_class("vm-manager-label")
    box.pack_start(pt_check, False, False, 0)

    pt_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
    pt_box.set_margin_start(18)
    pt_scroll = Gtk.ScrolledWindow()
    pt_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
    pt_scroll.set_min_content_height(160)
    pt_scroll.set_max_content_height(260)
    pt_scroll.set_propagate_natural_height(True)
    pt_scroll.add(pt_box)
    pt_scroll.set_no_show_all(True)
    box.pack_start(pt_scroll, False, False, 0)

    def pt_section(title, note=None):
        label = Gtk.Label(label=title)
        label.set_xalign(0)
        label.get_style_context().add_class("vm-manager-label")
        label.set_markup(f"<b>{GLib.markup_escape_text(title)}</b>")
        pt_box.pack_start(label, False, False, 6)
        if note:
            hint = Gtk.Label(label=note)
            hint.set_xalign(0)
            hint.set_line_wrap(True)
            hint.set_opacity(0.6)
            hint.get_style_context().add_class("vm-manager-label")
            pt_box.pack_start(hint, False, False, 0)

    def pt_checks(devices, label_of, key_of, chosen, tooltip_of=None):
        checks = []
        if not devices:
            none = Gtk.Label(label="(none found)")
            none.set_xalign(0)
            none.set_opacity(0.5)
            none.get_style_context().add_class("vm-manager-label")
            pt_box.pack_start(none, False, False, 0)
        for device in devices:
            check = Gtk.CheckButton(label=label_of(device))
            check.get_style_context().add_class("vm-manager-label")
            check._key = key_of(device)
            check.set_active(check._key in chosen)
            if tooltip_of:
                check.set_tooltip_text(tooltip_of(device))
            pt_box.pack_start(check, False, False, 0)
            checks.append(check)
        return checks

    chosen = vm_passthrough.settings_of(existing or {})
    pt_section("Audio")
    audio_check = Gtk.CheckButton(label="Host audio (PipeWire / PulseAudio / ALSA)")
    audio_check.set_active(chosen["audio"])
    audio_check.get_style_context().add_class("vm-manager-label")
    pt_box.pack_start(audio_check, False, False, 0)

    pt_section("USB devices",
               "Controllers, keyboards, headsets, capture cards... The host loses a device while the VM runs.")
    usb_checks = pt_checks(vm_passthrough.list_usb_devices(), lambda d: f"{d['label']}  [{d['id']}]",
                           lambda d: d["id"], chosen["usb"])

    pt_section("Host keyboard / mouse (evdev)",
               "Captured whole while the VM runs; press both Ctrl keys to hand them back to the host. "
               "Asks for the admin passphrase when the VM starts, for those devices only.")
    input_checks = pt_checks(vm_passthrough.list_input_devices(), lambda d: d["label"], lambda d: d["path"],
                             chosen["input"])

    pci_devices = vm_passthrough.list_pci_devices()
    pt_section("PCI devices (VFIO) - GPU, network, NVMe...",
               "Whole devices. Needs the IOMMU on and the device already bound to vfio-pci - this app never "
               "unbinds a host device (it could take your display away); the start button explains anything "
               "missing. Devices in one IOMMU group are ticked together.")
    pci_checks = pt_checks(
        pci_devices, lambda d: d["label"] + (f"  [{d['driver']}]" if d["driver"] else ""), lambda d: d["address"],
        chosen["pci"], tooltip_of=lambda d: f"IOMMU group {d['group'] or '?'}; driver {d['driver'] or 'none'}",
    )

    def on_pci_toggled(check):
        # ticking one device ticks the rest of its IOMMU group (VFIO needs the whole group)
        for peer in vm_passthrough.group_endpoints(check._key, pci_devices):
            for other in pci_checks:
                if other._key == peer["address"] and other.get_active() != check.get_active():
                    other.handler_block_by_func(on_pci_toggled)
                    other.set_active(check.get_active())
                    other.handler_unblock_by_func(on_pci_toggled)

    for check in pci_checks:
        check.connect("toggled", on_pci_toggled)

    gpu_only_check = Gtk.CheckButton(label="GPU output only (no emulated display or VM window)")
    gpu_only_check.set_active(chosen["gpu_only"])
    gpu_only_check.get_style_context().add_class("vm-manager-label")
    pt_box.pack_start(gpu_only_check, False, False, 6)
    hide_kvm_check = Gtk.CheckButton(label="Hide KVM from the guest (NVIDIA driver in Windows guests)")
    hide_kvm_check.set_active(chosen["hide_kvm"])
    hide_kvm_check.get_style_context().add_class("vm-manager-label")
    pt_box.pack_start(hide_kvm_check, False, False, 0)

    def on_pt_toggled(check):
        pt_scroll.set_visible(check.get_active())

    pt_check.connect("toggled", on_pt_toggled)
    pt_check.set_active(chosen["enabled"])
    # no_show_all keeps the window's own show_all() from revealing the section before the
    # box is ticked - which also means it must be shown explicitly here.
    pt_box.show_all()
    pt_scroll.get_child().show()
    pt_scroll.set_visible(chosen["enabled"])

    if existing:
        name_entry.set_text(existing["name"])
        disk_entry.set_text(existing["disk_path"])
        iso_entry.set_text(existing.get("iso_path") or "")
        ram_spin.set_value(existing["ram_mb"])
        cpu_spin.set_value(existing["cpus"])
        uefi_check.set_active(existing.get("uefi", True))
        boot_combo.set_active_id(existing.get("boot_order", "disk"))
        disk_bus_combo.set_active_id(existing.get("disk_bus", "virtio"))
        network_combo.set_active_id(existing.get("network_mode", "nat"))
        bridge_entry.set_text(existing.get("bridge_name") or "br0")
        bridge_entry.set_sensitive(network_combo.get_active_id() == "bridge")

    button_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    button_row.set_halign(Gtk.Align.END)
    box.pack_start(button_row, False, False, 12)

    cancel_button = Gtk.Button(label="Cancel")
    cancel_button.get_style_context().add_class("vm-manager-button")
    cancel_button.connect("clicked", lambda _b: window.destroy())
    button_row.pack_start(cancel_button, False, False, 0)

    save_button = Gtk.Button(label="Save")
    save_button.get_style_context().add_class("vm-manager-button-accent")

    def on_save_clicked(_b):
        name = name_entry.get_text().strip()
        disk_path = disk_entry.get_text().strip()
        if not name or not disk_path:
            return
        vm = {
            "id": existing["id"] if existing else uuid.uuid4().hex,
            "name": name,
            "disk_path": disk_path,
            "iso_path": iso_entry.get_text().strip() or None,
            "ram_mb": int(ram_spin.get_value()),
            "cpus": int(cpu_spin.get_value()),
            "uefi": uefi_check.get_active(),
            "boot_order": boot_combo.get_active_id(),
            "disk_bus": disk_bus_combo.get_active_id(),
            "network_mode": network_combo.get_active_id(),
            "bridge_name": bridge_entry.get_text().strip() or "br0",
            "passthrough": {
                "enabled": pt_check.get_active(),
                "audio": audio_check.get_active(),
                "usb": [c._key for c in usb_checks if c.get_active()],
                "input": [c._key for c in input_checks if c.get_active()],
                "pci": [c._key for c in pci_checks if c.get_active()],
                "gpu_only": gpu_only_check.get_active(),
                "hide_kvm": hide_kvm_check.get_active(),
            },
        }
        on_save(vm)
        window.destroy()

    save_button.connect("clicked", on_save_clicked)
    button_row.pack_start(save_button, False, False, 0)

    window.connect(
        "key-press-event",
        lambda _w, event: window.destroy() if event.keyval == Gdk.KEY_Escape else None,
    )

    window.show_all()
    window.present()
    name_entry.grab_focus()


class VMManagerWindow(Gtk.Window):
    def __init__(self):
        super().__init__(title="Virtual Machines")
        self.set_title("cosmos-vm-manager")
        cosmos_display.size_window(self, 560, 420)
        self.set_position(Gtk.WindowPosition.CENTER)
        self.get_style_context().add_class("vm-manager")
        self.connect("destroy", Gtk.main_quit)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        outer.set_border_width(14)
        self.add(outer)

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        title_label = Gtk.Label(label="Virtual Machines")
        title_label.get_style_context().add_class("vm-manager-label")
        header.pack_start(title_label, False, False, 0)

        new_button = Gtk.Button(label="New VM...")
        new_button.get_style_context().add_class("vm-manager-button-accent")
        new_button.connect("clicked", self.on_new_vm)
        header.pack_end(new_button, False, False, 0)
        outer.pack_start(header, False, False, 0)

        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.list_box = Gtk.ListBox()
        scroller.add(self.list_box)
        outer.pack_start(scroller, True, True, 0)

        self.refresh()

    def refresh(self):
        for child in self.list_box.get_children():
            self.list_box.remove(child)
        vms = load_vms()
        if not vms:
            placeholder = Gtk.Label(label="No VMs yet - click \"New VM...\" to add one.")
            placeholder.get_style_context().add_class("vm-manager-label")
            placeholder.set_margin_top(24)
            self.list_box.add(placeholder)
        for vm in vms:
            self.list_box.add(self.build_row(vm))
        self.list_box.show_all()

    def build_row(self, vm):
        row = Gtk.ListBoxRow()
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        box.get_style_context().add_class("vm-manager-row")
        row.add(box)

        info = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        name_label = Gtk.Label(label=vm["name"])
        name_label.set_xalign(0)
        name_label.get_style_context().add_class("vm-manager-label")
        info.pack_start(name_label, False, False, 0)

        bus_label = "VirtIO" if vm.get("disk_bus", "virtio") == "virtio" else "SATA/IDE"
        net_label = "bridged" if vm.get("network_mode", "nat") == "bridge" else "NAT"
        detail_text = (
            f"{vm['ram_mb']} MB - {vm['cpus']} CPU - {bus_label} - {net_label} - {vm['disk_path']}"
        )
        passthrough_text = vm_passthrough.summary(vm)
        if passthrough_text:
            detail_text = f"{vm['ram_mb']} MB - {vm['cpus']} CPU - passthrough: {passthrough_text} - {vm['disk_path']}"
        detail_label = Gtk.Label(label=detail_text)
        detail_label.set_xalign(0)
        # Ellipsize rather than let a long absolute disk path force the
        # whole window wider than its set_default_size() - confirmed
        # live this really happens otherwise (GTK treats default_size
        # as an initial hint, not a max; an unwrapped/unellipsized
        # label's natural width still wins).
        detail_label.set_ellipsize(Pango.EllipsizeMode.END)
        detail_label.set_max_width_chars(50)
        detail_label.get_style_context().add_class("vm-manager-label")
        detail_label.set_opacity(0.6)
        info.pack_start(detail_label, False, False, 0)
        box.pack_start(info, True, True, 0)

        start_button = Gtk.Button(label="Start")
        start_button.get_style_context().add_class("vm-manager-button-accent")
        start_button.connect("clicked", lambda _b, v=vm: self.on_start_vm(v))
        box.pack_start(start_button, False, False, 0)

        edit_button = Gtk.Button(label="Edit")
        edit_button.get_style_context().add_class("vm-manager-button")
        edit_button.connect("clicked", lambda _b, v=vm: self.on_edit_vm(v))
        box.pack_start(edit_button, False, False, 0)

        delete_button = Gtk.Button(label="Delete")
        delete_button.get_style_context().add_class("vm-manager-button")
        delete_button.connect("clicked", lambda _b, v=vm: self.on_delete_vm(v))
        box.pack_start(delete_button, False, False, 0)

        return row

    def on_start_vm(self, vm):
        try:
            warnings, _process = start_vm(vm)
        except vm_passthrough.PassthroughError as exc:
            self.message("Can't start with these passthrough devices", str(exc), Gtk.MessageType.ERROR)
            return
        if warnings:
            self.message("Started, with warnings", "\n\n".join(warnings), Gtk.MessageType.WARNING)

    def message(self, title, text, kind):
        dialog = Gtk.MessageDialog(transient_for=self, modal=True, message_type=kind,
                                   buttons=Gtk.ButtonsType.OK, text=title)
        dialog.format_secondary_text(text)
        dialog.run()
        dialog.destroy()

    def on_new_vm(self, _button):
        def on_save(vm):
            vms = load_vms()
            vms.append(vm)
            save_vms(vms)
            self.refresh()

        show_vm_dialog(self, on_save=on_save)

    def on_edit_vm(self, existing):
        def on_save(vm):
            vms = [v if v["id"] != vm["id"] else vm for v in load_vms()]
            save_vms(vms)
            self.refresh()

        show_vm_dialog(self, existing=existing, on_save=on_save)

    def on_delete_vm(self, existing):
        """No confirmation dialog - this only forgets the VM entry
        (name/RAM/CPU/paths in vms.json), it never touches the disk
        image file itself, so it's already a fully reversible action
        (re-add the same disk path back as a new VM) rather than one
        that needs a real are-you-sure gate."""
        vars_path = vm_vars_path(existing)
        if os.path.exists(vars_path):
            os.remove(vars_path)
        vms = [v for v in load_vms() if v["id"] != existing["id"]]
        save_vms(vms)
        self.refresh()


def main():
    cosmos_settings_link.redirect("vm")
    provider = Gtk.CssProvider()
    provider.load_from_data(cosmos_display.scale_css(CSS))
    Gtk.StyleContext.add_provider_for_screen(
        Gdk.Screen.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_USER
    )
    window = VMManagerWindow()
    window.show_all()
    Gtk.main()


if __name__ == "__main__":
    main()
