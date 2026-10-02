"""Android on CosmOS (Waydroid): setting it up once and tuning it for games. The logic behind the Android app (cosmos-android); no window here.

Waydroid runs Android in a container on the host's own kernel and shows it as a Wayland window. It ships in the Virtualization Expansion Pack.

  setup()          enables the Waydroid container service, starts it and downloads the Android image (`waydroid init`, about 1 GB) - all through kronos
                   in ONE process, so the admin passphrase is asked once
  gaming_props()   the Waydroid properties for a gaming setup, from the settings below (every one is in Waydroid's own documentation,
                   docs.waydro.id/usage/waydroid-prop-options):
                     persist.waydroid.multi_windows=false   one full-screen Android window (no desktop window integration)
                     persist.waydroid.suspend=false         the container never sleeps between apps, so a game resumes without a stall
                     persist.waydroid.uevent=true           Android sees hotplugged controllers directly, so games with gamepad support just work
                     persist.waydroid.width / height        a resolution override: native, 1080p or 720p (lower is faster)
                     persist.waydroid.fake_touch            packages whose mouse input is treated as touch
                     persist.waydroid.fake_wifi             packages that insist on Wi-Fi
                   and optionally Android's animation scales set to 0 (`settings put global ...`, standard Android settings).
  apply_gaming()   sets them. Waydroid applies most of them at the next session start, so restart Android afterwards.

Everything that runs a program goes through `run` (injectable), so this is tested without Waydroid.
"""
import json
import os
import re
import subprocess

CONFIG_PATH = os.path.join(os.path.expanduser("~/.config/cosmos"), "android.json")
WAYDROID_CFG = "/var/lib/waydroid/waydroid.cfg"
SERVICE = "waydroid-container.service"
RESOLUTIONS = {"native": None, "1080p": (1920, 1080), "720p": (1280, 720)}
PACKAGE_LIST = re.compile(r"^[A-Za-z0-9_.*]+(,[A-Za-z0-9_.*]+)*$")
PROP_LIMIT = 91                                             # Waydroid's own limit on the two package-list properties
ANIMATION_KEYS = ("window_animation_scale", "transition_animation_scale", "animator_duration_scale")
DEFAULTS = {"resolution": "native", "keep_awake": True, "gamepads": True, "animations_off": True, "fake_touch": "", "fake_wifi": ""}


class AndroidError(Exception):
    """Something to tell the person in a sentence."""


# ---- settings ----------------------------------------------------------------------------------------------------------------------

def clean_settings(raw):
    """A validated copy of `raw` on top of DEFAULTS; raises AndroidError for anything that isn't acceptable."""
    s = dict(DEFAULTS)
    s.update({k: v for k, v in (raw or {}).items() if k in DEFAULTS})
    if s["resolution"] not in RESOLUTIONS:
        raise AndroidError("The resolution must be native, 1080p or 720p.")
    for key in ("keep_awake", "gamepads", "animations_off"):
        if not isinstance(s[key], bool):
            raise AndroidError(f"{key} must be on or off.")
    for key in ("fake_touch", "fake_wifi"):
        text = s[key].strip() if isinstance(s[key], str) else None
        if text is None or len(text) > PROP_LIMIT or (text and not PACKAGE_LIST.match(text)):
            raise AndroidError(f"{key.replace('_', ' ')}: package names separated by commas (letters, digits, dots, * as a wildcard), at most {PROP_LIMIT} characters.")
        s[key] = text
    return s


def load_settings(path=CONFIG_PATH):
    try:
        with open(path, encoding="utf-8") as fh:
            return clean_settings(json.load(fh))
    except (OSError, ValueError, AndroidError):
        return dict(DEFAULTS)


def save_settings(settings, path=CONFIG_PATH):
    clean = clean_settings(settings)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(clean, fh, indent=2)
    return clean


def gaming_props(settings):
    """{property: value} to set with `waydroid prop set`. An empty value clears the property (Waydroid's own way to unset one)."""
    s = clean_settings(settings)
    props = {"persist.waydroid.multi_windows": "false",
             "persist.waydroid.suspend": "false" if s["keep_awake"] else "true",
             "persist.waydroid.uevent": "true" if s["gamepads"] else "false",
             "persist.waydroid.fake_touch": s["fake_touch"],
             "persist.waydroid.fake_wifi": s["fake_wifi"]}
    size = RESOLUTIONS[s["resolution"]]
    props["persist.waydroid.width"] = str(size[0]) if size else ""
    props["persist.waydroid.height"] = str(size[1]) if size else ""
    return props


def animation_commands(settings):
    """The `waydroid shell` argv lists that switch Android's animations off (scale 0) or back to normal (1)."""
    value = "0" if clean_settings(settings)["animations_off"] else "1"
    return [["waydroid", "shell", "settings", "put", "global", key, value] for key in ANIMATION_KEYS]


# ---- running things ----------------------------------------------------------------------------------------------------------------

def default_run(argv, timeout=60):
    try:
        done = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        return 127, str(exc)
    return done.returncode, (done.stdout + done.stderr)


def installed(which=None):
    import shutil
    return (which or shutil.which)("waydroid") is not None


def initialized(cfg=WAYDROID_CFG):
    return os.path.isfile(cfg)


def status(run=default_run):
    """{"installed", "initialized", "session", "container"} - the last two are Waydroid's own words (RUNNING, STOPPED, FROZEN) or ""."""
    out = {"installed": installed(), "initialized": initialized(), "session": "", "container": ""}
    if out["installed"]:
        rc, text = run(["waydroid", "status"], 15)
        for line in text.splitlines():
            key, _, value = line.partition(":")
            if key.strip().lower() in ("session", "container"):
                out[key.strip().lower()] = value.strip().upper()
    return out


def apply_gaming(settings, run=default_run, log=lambda text: None):
    """Set the gaming properties (and the animation scales). Returns [(what, ok, message)]. Nothing is fatal: each is reported so a person can see
    which took. Needs Android to have been started at least once (the properties live in the running container)."""
    results = []
    for prop, value in gaming_props(settings).items():
        rc, text = run(["waydroid", "prop", "set", prop, value], 30)
        results.append((prop, rc == 0, text.strip().splitlines()[-1] if text.strip() and rc != 0 else ""))
        log(f"{prop} = {value or '(cleared)'}: {'ok' if rc == 0 else 'failed'}")
    for argv in animation_commands(settings):
        rc, text = run(argv, 30)
        results.append((argv[-2], rc == 0, text.strip().splitlines()[-1] if text.strip() and rc != 0 else ""))
    return results


def setup(ask, log=lambda text: None, is_initialized=initialized, on_output=None):
    """Turn Android on for the first time. `ask(action, args, on_output=...)` is cosmos_kronos.ask - one process, so one passphrase. Returns True
    when Android is ready; raises AndroidError with the reason if a step is refused or fails."""
    def step(action, args, what):
        log(what)
        result = ask(action, args, on_output=on_output) if on_output else ask(action, args)
        if result.get("status") != "ok":
            raise AndroidError(result.get("reason") or (result.get("output") or "").strip()[-300:] or f"{what} didn't work.")
    step("service.control", {"unit": SERVICE, "verb": "enable"}, "Turning the Android container service on ...")
    step("service.control", {"unit": SERVICE, "verb": "start"}, "Starting it ...")
    if not is_initialized():
        step("run", {"argv": ["waydroid", "init"]}, "Downloading Android (about 1 GB, this takes a while) ...")
    else:
        log("Android is already set up.")
    log("Done. Open Android from the grid.")
    return True
