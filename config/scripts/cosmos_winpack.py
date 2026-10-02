"""Windows game packs for CosmOS: a game folder squeezed into one read-only SquashFS image that runs under any Proton version.

  <name>.sqfs         the game's files, compressed (zstd by default), with the pack's settings stored inside as .winpack.json
  <name>.json         the same settings beside the image, so a list of packs doesn't have to open any of them

What lives where (nothing here needs root):
  ~/.local/share/cosmos/winpacks/            the packs (image + settings)
  ~/.local/share/cosmos/winpacks/data/<id>/  the pack's own writable side: prefix/ (the Wine prefix - saves and settings live here),
                                             upper/ + work/ (anything the game writes into its own folder), so the image itself never changes
  $XDG_RUNTIME_DIR/cosmos-winpack/<id>/      where the image is mounted while the game runs (squashfuse; layered with fuse-overlayfs so the game
                                             sees a writable folder)

The settings (pack.json): name, exe (path inside the pack), args, steam_appid, proton ("auto" or a build's name), env, wrappers, protondb (a snapshot
of what was known when it was made). "auto" picks, in order, the build ProtonDB's reports favour, the newest GE-Proton, the newest anything. The
Steam id is what makes GE-Proton/umu apply the game's known fixes by themselves (see cosmos_protondb.py).
"""
import contextlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time

import cosmos_proton as cproton
import cosmos_protondb as pdb

HOME = os.path.expanduser("~")
PACKS_DIR = os.path.join(HOME, ".local/share/cosmos/winpacks")
FORMAT = 1
MANIFEST_NAME = ".winpack.json"
COMPRESSORS = {"zstd": (1, 22, 15), "xz": None, "gzip": (1, 9, 9), "lz4": None, "lzo": None}       # name -> (min, max, default level) if it has one
BLOCK_SIZES = ("128K", "256K", "512K", "1M")
EXE_SKIP = re.compile(r"(unins\d*|uninstall|vc_?redist|vcredist|dxsetup|dotnet|ndp\d|oalinst|crashpad|crashreport|crashhandler|"
                      r"unitycrashhandler|setup_redist|installer|updater|launcher_helper|easyanticheat_setup|eac_launcher|physx|directx)", re.I)
RUNNABLE = (".exe", ".bat", ".cmd")


class PackError(Exception):
    """Something to tell the person in a sentence."""


def slug(name):
    text = re.sub(r"[^A-Za-z0-9]+", "-", (name or "").strip()).strip("-").lower()
    if not text:
        raise PackError("Give the game a name.")
    return text[:60]


# ---- looking inside a game folder ---------------------------------------------------------------------------------------------------

def detect_executables(folder, name="", limit=12, max_depth=4):
    """Likely game executables (relative paths), best first: big, near the top, named like the game; installers, redistributables, crash
    handlers and uninstallers are left out."""
    root = os.path.abspath(folder)
    wanted = re.sub(r"[^a-z0-9]", "", (name or os.path.basename(root)).lower())
    found = []
    for dirpath, dirnames, filenames in os.walk(root):
        depth = dirpath[len(root):].count(os.sep)
        if depth >= max_depth:
            dirnames[:] = []
        dirnames[:] = [d for d in dirnames if not d.startswith(".") and d.lower() not in ("redist", "_commonredist", "__installer", "directx")]
        for f in filenames:
            if not f.lower().endswith(RUNNABLE) or EXE_SKIP.search(f):
                continue
            path = os.path.join(dirpath, f)
            try:
                size = os.path.getsize(path)
            except OSError:
                continue
            base = re.sub(r"[^a-z0-9]", "", os.path.splitext(f)[0].lower())
            score = min(size / 1e6, 200) - depth * 15 + (60 if wanted and (base in wanted or wanted in base) else 0) + (10 if f.lower().endswith(".exe") else 0)
            if "shipping" in f.lower():
                score += 10
            found.append((score, os.path.relpath(path, root)))
    found.sort(key=lambda t: (-t[0], t[1]))
    return [rel for _, rel in found[:limit]]


def folder_size(folder):
    total = 0
    for dirpath, _dirs, files in os.walk(folder):
        for f in files:
            with contextlib.suppress(OSError):
                total += os.lstat(os.path.join(dirpath, f)).st_size
    return total


# ---- settings -----------------------------------------------------------------------------------------------------------------------

def make_manifest(name, exe, appid=None, args=(), proton="auto", env=None, wrappers=(), protondb=None, notes=""):
    """A validated settings dict. Environment settings pass the same checks as ProtonDB suggestions (a hand-typed setting is checked too)."""
    if not isinstance(exe, str) or not exe or exe.startswith("/") or ".." in exe.replace("\\", "/").split("/") or not exe.lower().endswith(RUNNABLE):
        raise PackError("Choose the game's .exe (a path inside the game folder).")
    clean_env = {}
    for key, value in (env or {}).items():
        ok = pdb.sanitize_env(str(key), str(value))
        if ok is None:
            raise PackError(f"'{key}={value}' isn't an allowed setting (Wine/Proton/graphics settings only, short values, no paths).")
        clean_env[ok[0]] = ok[1]
    bad = [w for w in wrappers if w not in pdb.WRAPPERS]
    if bad:
        raise PackError(f"'{bad[0]}' isn't an allowed wrapper (only {', '.join(pdb.WRAPPERS)}).")
    bad_args = [a for a in args if not pdb.ARG_TOKEN.match(a)]
    if bad_args:
        raise PackError(f"'{bad_args[0]}' isn't a plain command-line switch.")
    if appid is not None and not (isinstance(appid, int) and 0 < appid < 10 ** 10):
        raise PackError("The Steam id is a number.")
    if proton != "auto" and not isinstance(proton, str):
        raise PackError("Choose a Proton version or 'auto'.")
    return {"format": FORMAT, "id": slug(name), "name": name.strip(), "exe": exe.replace("\\", "/"), "args": list(args), "steam_appid": appid, "proton": proton,
            "env": clean_env, "wrappers": list(wrappers), "protondb": protondb, "notes": notes[:2000], "created": int(time.time())}


def from_protondb(analysis, installed):
    """(env, wrappers, args, proton_name_or_None) - the parts of a pdb.analyze() result that are switched on automatically."""
    env, wrappers, args = {}, [], []
    for s in (analysis or {}).get("suggestions", []):
        if not s["auto"]:
            continue
        if s["kind"] == "env":
            env[s["key"]] = s["value"]
        elif s["kind"] == "wrapper":
            wrappers.append(s["key"])
        else:
            args.append(s["key"])
    build = pdb.choose_version(analysis, installed)
    return env, wrappers, args, (build.name if build else None)


# ---- working out the settings automatically -----------------------------------------------------------------------------------------

def _norm(text):
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def prepare(folder, name=None, appid=None, builds=None, fetch=pdb.http_get, cache=pdb.CACHE, offline=False):
    """Everything the creator can find out by itself about a game folder, for the person to review:

    {"name", "executables", "appid", "appid_source", "candidates" (other Steam matches), "info" (pdb.info or None), "env", "wrappers", "args",
     "proton" (the favoured installed build's name or None), "warnings"}

    The Steam id comes from steam_appid.txt in the folder, else the one Steam store match whose name is exactly the game's name; if that is
    ambiguous the matches are returned and nothing is guessed. With the id, ProtonDB and the fix database are consulted."""
    folder = os.path.abspath(folder)
    if not os.path.isdir(folder):
        raise PackError("Choose the game's folder.")
    name = (name or os.path.basename(folder.rstrip(os.sep))).strip()
    builds = builds if builds is not None else cproton.discover()
    out = {"name": name, "executables": detect_executables(folder, name), "appid": appid, "appid_source": "you gave it" if appid else "", "candidates": [],
           "info": None, "env": {}, "wrappers": [], "args": [], "proton": None, "warnings": []}
    if offline:
        fetch = _no_network
    if not appid:
        appid = pdb.appid_from_folder(folder)
        if appid:
            out["appid_source"] = "steam_appid.txt in the game folder"
        else:
            try:
                matches = pdb.search_steam(name, fetch=fetch, cache=cache)
            except pdb.ProtonDBError as exc:
                matches = []
                out["warnings"].append(str(exc))
            out["candidates"] = matches[:8]
            exact = [m for m in matches if _norm(m["name"]) == _norm(name)]
            if len(exact) == 1:
                appid, out["appid_source"] = exact[0]["appid"], "the one Steam store match for that name"
    if not appid and not out["candidates"]:
        # not a Steam game as far as the store knows: the umu fix database maps GOG/Epic/... titles to fix ids (numeric ones are Steam ids)
        try:
            ids = {r["umu_id"][4:] for r in pdb.umu_lookup(name, fetch=fetch, cache=cache) if r["umu_id"].startswith("umu-") and r["umu_id"][4:].isdigit()}
        except pdb.ProtonDBError as exc:
            ids = set()
            out["warnings"].append(str(exc))
        if len(ids) == 1:
            appid, out["appid_source"] = int(ids.pop()), "the umu fix database (matched by title)"
    out["appid"] = appid
    if appid:
        info = pdb.info(appid, builds, fetch=fetch, cache=cache)
        out["info"] = info
        out["warnings"] += info["warnings"]
        out["env"], out["wrappers"], out["args"], out["proton"] = from_protondb(info["analysis"], builds)
    return out


def _no_network(url, timeout=20):
    raise OSError("offline mode")


def settings_for(prepared, exe, proton="auto", extra_env=None, extra_args=(), extra_wrappers=(), skip=(), notes=""):
    """Manifest built from prepare()'s automatic choices plus the person's own additions; `skip` is a set of suggestion texts to switch off."""
    env = {k: v for k, v in prepared["env"].items() if f"{k}={v}" not in skip}
    env.update(extra_env or {})
    args = [a for a in prepared["args"] if a not in skip] + [a for a in extra_args if a not in prepared["args"]]
    wrappers = [w for w in prepared["wrappers"] if w not in skip] + [w for w in extra_wrappers if w not in prepared["wrappers"]]
    snapshot = None
    info = prepared.get("info")
    if info:
        snap = info.get("summary") or {}
        snapshot = {"fetched": int(time.time()), "tier": snap.get("tier"), "best": snap.get("best"), "score": snap.get("score"), "total": snap.get("total"),
                    "favoured": prepared.get("proton"), "fix": bool(info.get("fix")), "fix_url": (info.get("fix") or {}).get("url"),
                    "reports_newest": (info.get("analysis") or {}).get("newest")}
    return make_manifest(prepared["name"], exe, prepared.get("appid"), args, proton, env, wrappers, snapshot, notes)


# ---- creating a pack ----------------------------------------------------------------------------------------------------------------

def _mksquashfs_args(src, out, pseudo, comp, level, block):
    if comp not in COMPRESSORS:
        raise PackError(f"Unknown compression '{comp}'.")
    if block not in BLOCK_SIZES:
        raise PackError("Unknown block size.")
    args = ["mksquashfs", src, out, "-comp", comp, "-b", block, "-noappend", "-no-xattrs", "-percentage", "-pf", pseudo]
    limits = COMPRESSORS[comp]
    if limits and comp in ("zstd", "gzip"):
        level = limits[2] if level is None else level
        if not (isinstance(level, int) and limits[0] <= level <= limits[1]):
            raise PackError(f"The {comp} level is {limits[0]}-{limits[1]}.")
        args += ["-Xcompression-level", str(level)]
    return args


def create(src, manifest, out_dir=PACKS_DIR, comp="zstd", level=None, block="1M", progress=None, replace=False, popen=subprocess.Popen, which=shutil.which):
    """Build <out_dir>/<id>.sqfs (+ .json) from the game folder `src`. Returns {"image", "settings", "bytes_in", "bytes_out"}.
    `progress(percent)` gets 0-100 while mksquashfs works."""
    src = os.path.abspath(src)
    if not os.path.isdir(src):
        raise PackError("Choose the game's folder.")
    if not os.path.isfile(os.path.join(src, manifest["exe"])):
        raise PackError(f"{manifest['exe']} isn't inside that folder.")
    if os.path.lexists(os.path.join(src, MANIFEST_NAME)):
        raise PackError(f"The folder already has a {MANIFEST_NAME} - it looks like it was unpacked from a pack.")
    if which("mksquashfs") is None:
        raise PackError("mksquashfs isn't installed (squashfs-tools).")
    pid = manifest["id"]
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,59}", pid):
        raise PackError("Bad pack id.")
    os.makedirs(out_dir, exist_ok=True)
    image, sidecar = os.path.join(out_dir, pid + ".sqfs"), os.path.join(out_dir, pid + ".json")
    if os.path.exists(image) and not replace:
        raise PackError(f"There is already a pack called {manifest['name']}.")
    work = tempfile.mkdtemp(prefix="winpack-")
    try:
        text = json.dumps(manifest, indent=2, sort_keys=True)
        settings_file = os.path.join(work, "pack.json")
        with open(settings_file, "w", encoding="utf-8") as fh:
            fh.write(text)
        pseudo = os.path.join(work, "pseudo")
        with open(pseudo, "w", encoding="utf-8") as fh:
            fh.write(f"{MANIFEST_NAME} f 444 0 0 cat {settings_file}\n")
        partial = image + ".partial"
        cmd = _mksquashfs_args(src, partial, pseudo, comp, level, block)
        proc = popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
        tail = []
        for line in proc.stdout:
            line = line.strip()
            if line.isdigit():
                if progress:
                    progress(min(int(line), 100))
            elif line:
                tail.append(line)
                tail = tail[-8:]
        if proc.wait() != 0:
            with contextlib.suppress(OSError):
                os.unlink(partial)
            raise PackError("mksquashfs failed: " + (tail[-1] if tail else "no message"))
        os.replace(partial, image)
        with open(sidecar, "w", encoding="utf-8") as fh:
            fh.write(text)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    if progress:
        progress(100)
    return {"image": image, "settings": sidecar, "bytes_in": folder_size(src), "bytes_out": os.path.getsize(image)}


# ---- the packs you have -------------------------------------------------------------------------------------------------------------

def read_manifest(image, run=subprocess.run):
    """The settings of a pack: the .json beside it, else read out of the image itself (unsquashfs -cat)."""
    sidecar = os.path.splitext(image)[0] + ".json"
    try:
        with open(sidecar, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        done = run(["unsquashfs", "-cat", image, MANIFEST_NAME], capture_output=True, text=True)
        if done.returncode != 0:
            raise PackError(f"{os.path.basename(image)} isn't a CosmOS game pack.")
        try:
            data = json.loads(done.stdout)
        except ValueError:
            raise PackError(f"{os.path.basename(image)} has damaged settings.")
    if not isinstance(data, dict) or data.get("format") != FORMAT or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,59}", str(data.get("id", ""))):
        raise PackError(f"{os.path.basename(image)} isn't a CosmOS game pack this version understands.")
    return data


def list_packs(packs_dir=PACKS_DIR):
    """[{"image", "size", **settings}] sorted by name."""
    out = []
    try:
        names = sorted(os.listdir(packs_dir))
    except OSError:
        return out
    for n in names:
        if n.endswith(".sqfs"):
            path = os.path.join(packs_dir, n)
            try:
                out.append({"image": path, "size": os.path.getsize(path), **read_manifest(path)})
            except (PackError, OSError):
                continue
    return sorted(out, key=lambda p: p["name"].lower())


def delete_pack(pid, packs_dir=PACKS_DIR, remove_data=False):
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,59}", pid or ""):
        raise PackError("Bad pack id.")
    for ext in (".sqfs", ".json"):
        with contextlib.suppress(FileNotFoundError):
            os.unlink(os.path.join(packs_dir, pid + ext))
    if remove_data:
        shutil.rmtree(os.path.join(packs_dir, "data", pid), ignore_errors=True)


# ---- running one --------------------------------------------------------------------------------------------------------------------

def choose_build(manifest, builds):
    """The Proton build a pack should use, and why: (Build, reason). Raises PackError when there are none at all."""
    if not builds:
        raise PackError("No Proton build is installed. Import one (Proton tab) or download GE-Proton.")
    wanted = manifest.get("proton", "auto")
    if wanted != "auto":
        build = cproton.find(wanted, builds)
        if build is None:
            raise PackError(f"This pack wants {wanted}, which isn't installed. Import it, or set the pack to 'auto'.")
        return build, "pinned to this pack"
    snapshot = (manifest.get("protondb") or {}).get("favoured")
    build = cproton.find(snapshot, builds) if snapshot else None
    if build:
        return build, "what ProtonDB's reports favour"
    build = cproton.newest(builds, "ge") or cproton.newest(builds)
    return build, "the newest installed" if build else "none"


def _runtime_dir():
    base = os.environ.get("XDG_RUNTIME_DIR") or os.path.join(tempfile.gettempdir(), f"cosmos-{os.getuid()}")
    return os.path.join(base, "cosmos-winpack")


def plan_launch(manifest, build, packs_dir=PACKS_DIR, runtime=None, base_env=None, which=shutil.which, has_overlay=None):
    """Everything needed to run a pack, as plain data (so it is testable without mounting anything):
    {"lower", "merged", "upper", "work", "prefix", "cwd", "argv", "env", "notes"}"""
    pid = manifest["id"]
    runtime = runtime or _runtime_dir()
    data = os.path.join(packs_dir, "data", pid)
    overlay = (which("fuse-overlayfs") is not None) if has_overlay is None else has_overlay
    lower = os.path.join(runtime, pid, "lower")
    merged = os.path.join(runtime, pid, "merged") if overlay else lower
    exe = os.path.join(merged, manifest["exe"])
    notes = []
    env = dict(base_env if base_env is not None else os.environ)
    for k in list(env):
        if k.startswith(("STEAM_COMPAT_", "WINEPREFIX", "PROTONPATH", "GAMEID", "SteamAppId", "SteamGameId", "UMU_")):
            del env[k]                                        # nothing left over from another game's launch
    prefix = os.path.join(data, "prefix")
    env["STEAM_COMPAT_DATA_PATH"] = prefix
    env["STEAM_COMPAT_CLIENT_INSTALL_PATH"] = os.path.join(data, "steam")
    env["STEAM_COMPAT_INSTALL_PATH"] = merged
    appid = manifest.get("steam_appid")
    if appid:
        env["SteamAppId"] = env["SteamGameId"] = env["STEAM_COMPAT_APP_ID"] = str(appid)
    env["UMU_ID"] = env["GAMEID"] = f"umu-{appid}" if appid else "umu-0"
    for key, value in (manifest.get("env") or {}).items():
        clean = pdb.sanitize_env(key, str(value))               # checked again here: a settings file can be edited by hand
        if clean:
            env[clean[0]] = clean[1]
    wrappers = []
    for w in manifest.get("wrappers") or []:
        if w in pdb.WRAPPERS and which(w):
            wrappers.append(w)
        else:
            notes.append(f"{w} isn't installed, so it was skipped.")
    args = [a for a in manifest.get("args") or [] if pdb.ARG_TOKEN.match(a)]
    if which("umu-run"):
        env["PROTONPATH"] = build.path
        env["WINEPREFIX"] = os.path.join(prefix, "pfx")
        core = ["umu-run", exe, *args]
    else:
        core = [build.script, "waitforexitandrun", exe, *args]
    return {"lower": lower, "merged": merged, "upper": os.path.join(data, "upper"), "work": os.path.join(data, "work"), "prefix": prefix,
            "cwd": os.path.dirname(exe), "argv": wrappers + core, "env": env, "notes": notes, "overlay": overlay}


def _unmount(path, run=subprocess.run, which=shutil.which):
    tool = which("fusermount3") or which("fusermount")
    if tool:
        run([tool, "-u", "-z", path], capture_output=True)


def _is_mounted(path):
    try:
        with open("/proc/self/mountinfo", encoding="utf-8") as fh:
            return any(f" {path} " in line for line in fh)
    except OSError:
        return False


@contextlib.contextmanager
def mounted(image, plan, run=subprocess.run, which=shutil.which, is_mounted=_is_mounted):
    """Mount the image (and the writable layer) for the duration of the with-block; always unmounted afterwards."""
    if which("squashfuse") is None:
        raise PackError("squashfuse isn't installed, so the pack can't be mounted.")
    for path in (plan["lower"], plan["merged"], plan["upper"], plan["work"], plan["prefix"], os.path.join(os.path.dirname(plan["prefix"]), "steam")):
        os.makedirs(path, exist_ok=True)
    for path in {plan["merged"], plan["lower"]}:                      # a crash last time can leave them mounted
        if is_mounted(path):
            _unmount(path, run, which)
    done = run(["squashfuse", "-o", "ro", image, plan["lower"]], capture_output=True, text=True)
    if done.returncode != 0:
        raise PackError("Couldn't mount the pack: " + (done.stderr.strip().splitlines() or ["squashfuse failed"])[-1])
    try:
        if plan["overlay"]:
            done = run(["fuse-overlayfs", "-o", f"lowerdir={plan['lower']},upperdir={plan['upper']},workdir={plan['work']}", plan["merged"]],
                       capture_output=True, text=True)
            if done.returncode != 0:
                raise PackError("Couldn't make the game folder writable: " + (done.stderr.strip().splitlines() or ["fuse-overlayfs failed"])[-1])
        try:
            yield plan
        finally:
            if plan["overlay"]:
                _unmount(plan["merged"], run, which)
    finally:
        _unmount(plan["lower"], run, which)


def launch(pack, builds=None, packs_dir=PACKS_DIR, run=subprocess.run, popen=subprocess.Popen, which=shutil.which, log=None):
    """Mount and run a pack (a list_packs() entry, or a settings dict + "image"), wait for it to finish, unmount. Returns the exit code."""
    builds = builds if builds is not None else cproton.discover()
    build, why = choose_build(pack, builds)
    plan = plan_launch(pack, build, packs_dir, which=which)
    say = log or (lambda text: None)
    say(f"Using {build.name} ({why}).")
    for note in plan["notes"]:
        say(note)
    with mounted(pack["image"], plan, run=run, which=which):
        if not os.path.isfile(os.path.join(plan["merged"], pack["exe"])):
            raise PackError(f"{pack['exe']} isn't in the mounted pack.")
        proc = popen(plan["argv"], cwd=plan["cwd"], env=plan["env"])
        return proc.wait()
