#!/usr/bin/env python3
"""Game downloads for the CosmOS Shop: the Games and BIOS sources (GameGrubber, CosmOS's rebrand of RGSX).

The downloader itself is a module (code in the private modules repo, fetched by cosmos_modules.py through the Expansion Pack "game-downloads").
Its `cosmos_bridge.py` is run as a subprocess: `platforms` lists the systems it can fill (remembered in platforms.json), `games` gives one system's
games in GameGrubber's own order, and `download` fetches one. The Shop shows them as GameBox's list did: pick a system, then its games, a page at a
time. The bridge downloads the game lists itself the first time it runs.

Off until switched on (a marker file, see enabled()); the switch and its one-time disclaimer belong in the universal Settings app. Downloads land in
/cosmos/roms/<platform> where GameBox finds them; a full Xbox disc dump is converted to a plain XISO first (see xiso.py), because xemu can't boot one.

  cosmos_gamegrubber.py status                    what is installed and whether it is switched on
  cosmos_gamegrubber.py enable | disable
  cosmos_gamegrubber.py download PLATFORM GAME    one download, as text lines (what the Shop runs in its task window)
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import cosmos_modules  # noqa: E402

MODULE = "gamegrubber"
BIOS_ID = "- BIOS by TMCTV -"
ROMS_FOLDER = "/cosmos/roms"
SEP = "\x1f"                                   # joins a platform id and a game's file name into one Shop item id


def gamebox_dir():
    """The GameBox files the bridge reads: which systems GameBox can run (library/systems.json) and the Real-Debrid key code (settings/). On the image
    that is GameBox's own tree; on an ordinary Fedora the RPM carries just those files (gamebox-lite, beside this file); in a checkout it is the repo's
    gamebox/ folder. COSMOS_GAMEBOX_DIR overrides all three."""
    override = os.environ.get("COSMOS_GAMEBOX_DIR")
    if override:
        return Path(override)
    for candidate in (Path("/usr/share/gamebox"), HERE / "gamebox-lite", HERE.parent.parent / "gamebox"):
        if (candidate / "library" / "systems.json").is_file():
            return candidate
    return Path("/usr/share/gamebox")


def state_dir():
    return Path(os.environ.get("COSMOS_GAMEGRUBBER_DIR") or Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "cosmos" / "gamegrubber")


def config_dir():
    return state_dir() / "config"


def flag_path():
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "cosmos" / "gamegrubber-enabled"


def enabled():
    """Off by default: the Games and BIOS sources stay hidden until this marker exists. The marker is deleted when switched off, so the disclaimer
    is shown again the next time it is switched on."""
    return flag_path().is_file()


def set_enabled(on):
    path = flag_path()
    if on:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    else:
        path.unlink(missing_ok=True)


def bridge_path():
    return cosmos_modules.modules_root() / MODULE / "cosmos_bridge.py"


def installed():
    return bridge_path().is_file()


def visible():
    """Whether the Shop shows Games and BIOS: the module is installed and it is switched on."""
    return installed() and enabled()


def bridge_env():
    return {**os.environ, "COSMOS_ROOT": str(gamebox_dir()), "RGSX_CONFIG_DIR": str(config_dir()),
            "RGSX_PROFILE_HISTORY_PATH": str(state_dir() / "history.json")}


def ensure_settings():
    """The module keeps its own settings; the only one that matters here is where ROMs go."""
    path = config_dir() / "rgsx_settings.json"
    if not path.is_file():
        config_dir().mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"roms_folder": ROMS_FOLDER}), encoding="utf-8")


def run_bridge(args, timeout=120, run=subprocess.run):
    """One bridge call that prints a JSON value; None on any failure."""
    if not installed():
        return None
    ensure_settings()
    try:
        done = run([sys.executable, str(bridge_path()), *args], capture_output=True, text=True, timeout=timeout, env=bridge_env())
    except (OSError, subprocess.SubprocessError):
        return None
    if done.returncode != 0:
        return None
    try:
        return json.loads(done.stdout)
    except ValueError:
        return None


def platforms(run=subprocess.run):
    """[{"id", "name", "folder"}], BIOS excluded, sorted by name: every system the module offers (asks the bridge, then remembers until the module changes)."""
    cache = state_dir() / "platforms.json"
    data = None
    try:
        if cache.stat().st_mtime >= bridge_path().stat().st_mtime:            # a module update (git pull) makes the remembered list stale
            data = json.loads(cache.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    if not data:
        data = run_bridge(["platforms"], run=run) or []
        if data:
            state_dir().mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps(data), encoding="utf-8")
    return sorted((p for p in data if p.get("id") != BIOS_ID), key=lambda p: p["name"].lower())


def _titles():
    """The arcade-name resolver from GameBox (raw MAME/FBNeo set codes -> real titles), or None when it can't be loaded."""
    try:
        for sub in ("library", "."):
            sys.path.insert(0, str(gamebox_dir() / sub))
        import arcade_names
        return arcade_names
    except Exception:
        return None


def read_games(platform_id):
    """[(file name, url, size)] for one platform from the module's cached list; [] when there isn't one yet."""
    path = config_dir() / "games" / f"{platform_id}.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    data = data.get("games", []) if isinstance(data, dict) else data
    return [(g[0], g[1], g[2] if len(g) > 2 else "") for g in data if isinstance(g, (list, tuple)) and len(g) >= 2 and isinstance(g[0], str)]


def display_name(file_name):
    stem, ext = os.path.splitext(file_name)
    return stem if 1 <= len(ext) <= 6 else file_name


def item_id(platform_id, file_name):
    return platform_id + SEP + file_name


def split_item_id(pkg_id):
    platform_id, _, file_name = pkg_id.partition(SEP)
    return platform_id, file_name


GAME_PAGE_SIZE = 200                              # rows per page: real systems have thousands of games (Final Burn Neo alone has 7,500)


def bios_platform():
    """The one BIOS "system": it has its own Shop tab and opens straight onto its files."""
    return {"id": BIOS_ID, "name": "BIOS", "folder": "bios"}


def list_games(platform_id, run=subprocess.run):
    """[{"name", "display_name", "url", "size"}] for one system, in the order GameGrubber itself sorts them (the bridge applies its own sort setting,
    by name by default); what GameBox's own list showed. For an arcade system whose files are raw MAME/FBNeo set codes the display name is the real
    title where one is known. Falls back to the cached list file, sorted by title, if the bridge can't be run."""
    games = run_bridge(["games", platform_id], run=run)
    if not isinstance(games, list):
        games = [{"name": n, "display_name": display_name(n), "url": u, "size": z} for n, u, z in read_games(platform_id)]
        games.sort(key=lambda g: g["display_name"].lower())
    titles = _titles()
    if titles is not None and titles.needs_resolution(platform_id):
        for game in games:
            try:
                game["display_name"] = titles.pretty_name(game["name"], game.get("display_name") or game["name"])
            except Exception:
                pass
    return games


def search_games(games, query):
    """The games whose title has every word of the query in it, in the same order (the full list, not paged); no query means all of them."""
    words = (query or "").strip().lower().split()
    if not words:
        return list(games)
    return [g for g in games if all(w in (g.get("display_name") or g["name"]).lower() for w in words)]


def game_label(game):
    title = game.get("display_name") or game["name"]
    return f"{title}  ({game['size']})" if game.get("size") else title


# Who made each system, keyed by GameGrubber's own folder name. The Shop's Games tab groups systems under these (manufacturer, then system, then games).
MAKER_FOLDERS = {
    "Nintendo": ["nes", "fds", "snes", "satellaview", "sufami", "n64", "n64dd", "gamecube", "wii", "wiiu", "switch", "gb", "gbc", "gba", "nds", "3ds",
                 "virtualboy", "pokemini", "gameandwatch"],
    "Sony": ["psx", "ps2", "ps3", "ps4", "psp", "psvita"],
    "Sega": ["sg1000", "mastersystem", "megadrive", "megacd", "sega32x", "saturn", "dreamcast", "gamegear", "pico", "naomi", "naomi2", "chihiro",
             "lindbergh"],
    "Microsoft": ["xbox", "xbox360"],
    "Atari": ["atari2600", "atari5200", "atari7800", "atarist", "jaguar", "lynx"],
    "Commodore": ["c20", "c64", "cplus4", "amiga500", "amigacd32", "amigacdtv"],
    "NEC": ["pcengine", "pcenginecd", "supergrafx", "pc98"],
    "SNK": ["neogeo", "neogeocd", "ngp", "ngpc"],
    "Bandai": ["wswan", "wswanc"],
    "Acorn": ["archimedes", "atom"],
    "Apple": ["apple2", "apple2gs"],
    "MSX": ["msx1", "MSX2+ (Archive)"],
    "VTech": ["crvision", "vsmile"],
    "Magnavox / Philips": ["o2em", "cdi"],
    "Coleco": ["colecovision"],
    "Mattel": ["intellivision"],
    "3DO": ["3do"],
    "Bally": ["astrocde"],
    "Casio": ["pv1000"],
    "Emerson": ["arcadia"],
    "Entex": ["advision"],
    "Epoch": ["scv"],
    "Fairchild": ["channelf"],
    "Fujitsu": ["fmtowns"],
    "Funtech": ["supracan"],
    "GCE": ["vectrex"],
    "Hartung": ["gmaster"],
    "Sharp": ["x1"],
    "Tiger": ["gamecom"],
    "Watara": ["supervision"],
    "Other handhelds": ["gamate", "megaduck"],
    "PC & Arcade": ["windows", "dos", "scummvm", "openbor", "vpinball", "fbneo", "teknoparrot", "daphne", "namco2x6"],
}
FOLDER_MAKER = {folder: maker for maker, folders in MAKER_FOLDERS.items() for folder in folders}
LAST_MAKERS = ("PC & Arcade", "Other")                # listed after the rest


def maker_of(platform):
    """The manufacturer a system is filed under; "Other" for anything not in MAKER_FOLDERS (a system added to the catalog later)."""
    return FOLDER_MAKER.get(platform.get("folder")) or FOLDER_MAKER.get(platform.get("name")) or "Other"


def makers(platform_list):
    """[(manufacturer, [systems])], manufacturers alphabetical (PC & Arcade and Other last), each one's systems alphabetical."""
    groups = {}
    for platform in platform_list:
        groups.setdefault(maker_of(platform), []).append(platform)
    order = sorted(groups, key=lambda m: (m in LAST_MAKERS, LAST_MAKERS.index(m) if m in LAST_MAKERS else 0, m.lower()))
    return [(m, sorted(groups[m], key=lambda p: p["name"].lower())) for m in order]


def search_makers(groups, query):
    """The manufacturers whose name, or one of whose systems' names, has every word of the query."""
    words = (query or "").strip().lower().split()
    def text(maker, systems):
        return maker + " " + " ".join(f"{p['name']} {p.get('folder', '')}" for p in systems)
    return [(m, ps) for m, ps in groups if all(w in text(m, ps).lower() for w in words)]


def search_systems(platform_list, query):
    words = (query or "").strip().lower().split()
    return [p for p in platform_list if all(w in f"{p['name']} {p.get('folder', '')}".lower() for w in words)]


def download_command(pkg_id, python=None):
    """The shell command the Shop runs (in its task window) to download one item."""
    import shlex
    platform_id, file_name = split_item_id(pkg_id)
    return " ".join(shlex.quote(a) for a in [python or sys.executable, str(Path(__file__).resolve()), "download", platform_id, file_name])


def progress_text(progress):
    downloaded, total = progress.get("downloaded") or 0, progress.get("total") or 0
    speed, status = progress.get("speed"), progress.get("status") or "Downloading"
    text = f"{status}: {downloaded / 1048576:.1f} MB"
    if total > 0:
        text = f"{status}: {int(downloaded / total * 100)}% ({downloaded / 1048576:.1f}/{total / 1048576:.1f} MB)"
    if isinstance(speed, (int, float)) and speed > 0:
        text += f" @ {speed:.1f} MB/s"
    return text


def xbox_roms_dir(platform_id):
    """The ROMs folder of a platform GameBox runs with xemu, or None for every other platform."""
    try:
        with open(gamebox_dir() / "library" / "systems.json", encoding="utf-8") as fh:
            data = json.load(fh)
        for category in data["categories"]:
            for manufacturer in category["manufacturers"]:
                for platform in manufacturer["platforms"]:
                    if platform["id"] == platform_id and platform.get("xemu"):
                        return Path(platform["roms_dir"])
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None


def new_images(roms_dir, game_name, since):
    """The disc image(s) a download produced: the file named for the game if it is there, else any .iso newer than the download's start."""
    named = roms_dir / game_name
    if named.is_file():
        return [named]
    try:
        return [p for p in roms_dir.iterdir() if p.suffix.lower() == ".iso" and p.stat().st_mtime >= since]
    except OSError:
        return []


def download(platform_id, game_name, out=print, popen=subprocess.Popen):
    """Runs the bridge's download and reports it as text lines; returns the exit code. For an Xbox game the finished image is converted before "done"."""
    if not installed():
        out("Game downloads aren't installed. Install the Game Downloads pack from the Shop first.")
        return 1
    ensure_settings()
    started = time.time() - 2
    roms_dir = xbox_roms_dir(platform_id)
    proc = popen([sys.executable, str(bridge_path()), "download", platform_id, game_name, f"shop-{int(started * 1000)}"],
                 stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=bridge_env())
    last_line, last_time, final, noise = "", 0.0, None, []
    for line in proc.stdout:
        try:
            message = json.loads(line)
        except ValueError:
            if line.strip():
                noise.append(line.rstrip())                     # not the bridge's JSON protocol: a warning or a crash; kept so a failure can say why
                del noise[:-12]
            continue
        if message.get("type") == "progress":
            text = progress_text(message)
            if text != last_line and time.time() - last_time >= 1.5:
                out(text)
                last_line, last_time = text, time.time()
        elif message.get("type") == "done":
            final = message
    proc.wait()
    if not final or not final.get("success"):
        out((final or {}).get("message") or "The download failed.")
        if not final and noise:
            out("The downloader said:")
            for line in noise:
                out("  " + line)
        return 1
    if roms_dir is not None:
        import xiso
        for image in new_images(roms_dir, game_name, started):
            if xiso.needs_conversion(image):
                out("Converting to XISO (a few minutes) ...")
                ok, text = xiso.convert(image, log=out)
                if not ok:
                    out(f"Downloaded, but couldn't convert to XISO: {text}")
                    return 1
                out(text)
    out(final.get("message") or "Download complete.")
    return 0


def main(argv):
    if argv == ["status"]:
        print(f"installed: {installed()}   switched on: {enabled()}")
        return 0 if visible() else 1
    if argv in (["enable"], ["disable"]):
        set_enabled(argv[0] == "enable")
        return 0
    if len(argv) == 3 and argv[0] == "download":
        return download(argv[1], argv[2], out=lambda s: print(s, flush=True))
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
