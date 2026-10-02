"""DualSense-only features the kernel driver does not expose: adaptive triggers and haptics.

Adaptive triggers are part of the controller's ordinary HID output report, so they are set by
writing that report to the controller's /dev/hidraw node ourselves (the same thing dualsensectl and
pydualsense do). The report is "flagged": only the fields whose valid-flag bit is set are changed, so
this coexists with hid-playstation, which keeps sending its own reports for rumble, the lightbar
and the player lights without those flags.

  USB        report 0x02, 63 bytes:   id | common(47) | reserved(15)
  Bluetooth  report 0x31, 78 bytes:   id | seq<<4 | tag 0x10 | common(47) | reserved(24) | crc32(4)
             (the CRC is a zlib crc32 over 0xA2 + everything before it, little-endian)

In `common`: byte 0 valid_flag0 (bit 2 = right trigger effect, bit 3 = left), the right trigger's
11 bytes at offset 10 and the left one's at offset 21.

The effect encodings (mode byte + zone bit-fields) are the ones the community documented from
Sony's own effect definitions. They have not been checked against hardware from this repository:
if a mode misbehaves, that is where to look (build_effect).

Haptics are audio: over USB the controller is a 4-channel sound card and channels 3-4 drive the
voice coils. Over Bluetooth they use a compressed stream nothing on Linux implements, so haptics
are USB-only here.
"""
import glob
import math
import os
import struct
import subprocess
import tempfile
import wave
import zlib

USB_REPORT_ID = 0x02
BT_REPORT_ID = 0x31
BT_TAG = 0x10
COMMON_SIZE = 47
RIGHT_OFFSET = 10
LEFT_OFFSET = 21
FLAG_RIGHT = 0x04
FLAG_LEFT = 0x08

MODES = ("off", "feedback", "weapon", "vibration")
DEFAULT_TRIGGER = {"mode": "off", "start": 3, "end": 6, "strength": 5, "amplitude": 5, "frequency": 30}


def _clamp(v, lo, hi):
    return max(lo, min(hi, int(v)))


def build_effect(t):
    """The 11 bytes for one trigger from settings `t` (see DEFAULT_TRIGGER)."""
    mode = t.get("mode", "off")
    out = bytearray(11)
    if mode == "feedback":          # constant resistance from `start` to the end of the travel
        start, strength = _clamp(t.get("start", 3), 0, 9), _clamp(t.get("strength", 5), 1, 8)
        zones = active = 0
        for i in range(start, 10):
            zones |= 1 << i
            active |= (strength - 1) << (3 * i)
        out[0] = 0x21
        out[1], out[2] = zones & 0xFF, zones >> 8
        out[3:7] = struct.pack("<I", active)
    elif mode == "weapon":          # resistance from `start`, then a click at `end`
        start = _clamp(t.get("start", 3), 2, 7)
        end = _clamp(t.get("end", 6), start + 1, 8)
        strength = _clamp(t.get("strength", 5), 1, 8)
        mask = (1 << start) | (1 << end)
        out[0] = 0x25
        out[1], out[2] = mask & 0xFF, mask >> 8
        out[3] = strength - 1
    elif mode == "vibration":       # a buzz of `frequency` Hz from `start` onwards
        start, amp = _clamp(t.get("start", 3), 0, 9), _clamp(t.get("amplitude", 5), 1, 8)
        freq = _clamp(t.get("frequency", 30), 1, 255)
        zones = active = 0
        for i in range(start, 10):
            zones |= 1 << i
            active |= (amp - 1) << (3 * i)
        out[0] = 0x26
        out[1], out[2] = zones & 0xFF, zones >> 8
        out[3:7] = struct.pack("<I", active)
        out[9] = freq
    else:
        out[0] = 0x05               # off
    return bytes(out)


def build_report(bluetooth, left=None, right=None, seq=0):
    """The full output report setting the triggers (None = leave that trigger alone)."""
    common = bytearray(COMMON_SIZE)
    if right is not None:
        common[0] |= FLAG_RIGHT
        common[RIGHT_OFFSET:RIGHT_OFFSET + 11] = build_effect(right)
    if left is not None:
        common[0] |= FLAG_LEFT
        common[LEFT_OFFSET:LEFT_OFFSET + 11] = build_effect(left)
    if not bluetooth:
        return bytes([USB_REPORT_ID]) + bytes(common) + bytes(15)
    body = bytes([BT_REPORT_ID, (seq & 0x0F) << 4, BT_TAG]) + bytes(common) + bytes(24)
    crc = zlib.crc32(b"\xa2" + body) & 0xFFFFFFFF
    return body + struct.pack("<I", crc)


def is_bluetooth(pad):
    return os.path.basename(pad.hid_dir or "").startswith("0005:")


def hidraw_path(pad):
    for d in glob.glob(os.path.join(pad.hid_dir or "/nonexistent", "hidraw", "hidraw*")):
        return "/dev/" + os.path.basename(d)
    return None


_seq = [0]


def send_triggers(pad, left, right):
    """Write the trigger settings to the controller. Returns True if the write went through."""
    path = hidraw_path(pad)
    if not path:
        return False
    _seq[0] = (_seq[0] + 1) & 0x0F
    report = build_report(is_bluetooth(pad), left, right, _seq[0])
    try:
        fd = os.open(path, os.O_WRONLY)
    except OSError:
        return False
    try:
        return os.write(fd, report) == len(report)
    except OSError:
        return False
    finally:
        os.close(fd)


def triggers_from_settings(s):
    """(left, right) trigger dicts from a controller's saved settings, or (None, None) when the
    user never touched them (then games and other tools keep control of the triggers)."""
    data = s.get("triggers")
    if not isinstance(data, dict):
        return None, None
    out = []
    for side in ("left", "right"):
        t = dict(DEFAULT_TRIGGER)
        if isinstance(data.get(side), dict):
            t.update({k: v for k, v in data[side].items() if k in DEFAULT_TRIGGER})
        if t["mode"] not in MODES:
            t["mode"] = "off"
        out.append(t)
    return out[0], out[1]


# --------------------------------------------------------------------------- haptics

def haptics_wav(path, seconds=1.2, rate=48000):
    """A 4-channel test clip: silence on the speaker channels (1-2), a swelling 80 Hz thump on the
    haptic channels (3-4)."""
    frames = bytearray()
    for i in range(int(seconds * rate)):
        t = i / rate
        env = min(1.0, t * 6) * max(0.0, 1.0 - t / seconds) * (0.6 + 0.4 * math.sin(2 * math.pi * 5 * t))
        v = int(20000 * env * math.sin(2 * math.pi * 80 * t))
        frames += struct.pack("<hhhh", 0, 0, v, v)
    with wave.open(path, "wb") as w:
        w.setnchannels(4)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(bytes(frames))


def usb_device_dir(pad):
    """The USB device the controller hangs off (…/1-2), or None when it is not on USB."""
    if not pad.hid_dir or is_bluetooth(pad) or not os.path.basename(pad.hid_dir).startswith("0003:"):
        return None
    return os.path.dirname(os.path.dirname(pad.hid_dir))


def sound_sink(pad, run=subprocess.run):
    """The PulseAudio/PipeWire sink of this controller's USB audio interface, or None."""
    if usb_device_dir(pad) is None:
        return None
    try:
        out = run(["pactl", "list", "short", "sinks"], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    names = [line.split("\t")[1] for line in out.splitlines() if "\t" in line]
    named = [n for n in names if "sony" in n.lower() and ("dualsense" in n.lower() or "wireless_controller" in n.lower())]
    surround = [n for n in named if "surround-40" in n or "40" in n]
    return (surround or named or [None])[0]


def play_haptics(pad, run=subprocess.run):
    """Play the test clip on the haptic channels. Returns (ok, message)."""
    if usb_device_dir(pad) is None:
        return False, "Haptics need the controller connected by USB cable; over Bluetooth they are not available on Linux."
    sink = sound_sink(pad, run)
    if not sink:
        return False, "The controller's audio device was not found (is it plugged in by USB, and is sound running?)."
    fd, path = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    try:
        haptics_wav(path)
        done = run(["paplay", "--device", sink, path], capture_output=True, timeout=15)
        if done.returncode != 0:
            return False, "Playing on the controller failed - its audio profile may be stereo only (no rear channels)."
        return True, "Played the haptic test."
    except (OSError, subprocess.SubprocessError):
        return False, "paplay is not available."
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
