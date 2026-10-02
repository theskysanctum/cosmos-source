"""ProtonDB and the Proton fix databases, so a Windows game pack can be set up from what is already known about that game.

Where each fact comes from (all fetched over HTTPS, cached for a day, and used from the cache when offline):

  the game's Steam id     steam_appid.txt next to the game if it ships one, else a search of the Steam store by name
  how well it runs        ProtonDB's own summary  https://www.protondb.com/api/v1/reports/summaries/<id>.json  (tier, score, confidence, count)
  what people did         the community mirror of ProtonDB's report data  https://protondb.max-p.me/games/<id>/reports  (a SNAPSHOT of old reports
                          - every result says how old the newest one is). From the report notes this pulls the launch options and the Proton
                          versions that worked or didn't.
  automatic game fixes    umu-protonfixes  https://github.com/Open-Wine-Components/umu-protonfixes  - a per-game script that GE-Proton / umu apply by
                          themselves when the game's id is set. The pack sets the id, so a fix that exists is used with no action from you.
                          umu-database maps other stores' games (GOG, Epic, ...) to those ids.

Report notes are written by strangers, so they are never run or pasted anywhere: a launch option is accepted only if it is a well-formed
KEY=VALUE for a short list of Wine/Proton/graphics settings, one of two known wrappers, or a short command-line switch; everything else is
dropped (see sanitize). Suggestions that enough good reports agree on are switched on automatically; the rest are offered.
"""
import csv
import io
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter

CACHE = os.path.join(os.path.expanduser("~"), ".cache/cosmos/protondb")
TTL_S = 24 * 3600
USER_AGENT = "cosmos-winpack (+https://github.com/theskysanctum/cosmos)"
SUMMARY_URL = "https://www.protondb.com/api/v1/reports/summaries/{appid}.json"
REPORTS_URL = "https://protondb.max-p.me/games/{appid}/reports"
STEAM_SEARCH_URL = "https://store.steampowered.com/api/storesearch/?term={term}&cc=us&l=en"
FIX_URL = "https://raw.githubusercontent.com/Open-Wine-Components/umu-protonfixes/master/gamefixes-steam/{appid}.py"
UMU_DB_URL = "https://raw.githubusercontent.com/Open-Wine-Components/umu-database/main/umu-database.csv"
AUTO_MIN_GOOD = 3                      # a suggestion is switched on by itself when this many Gold/Platinum reports agree on it

GOOD, OK, BAD = {"platinum", "gold"}, {"silver"}, {"bronze", "borked"}
ENV_PREFIXES = ("PROTON_", "WINE", "DXVK_", "VKD3D_", "MESA_", "RADV_", "__GL_", "__NV_", "SDL_", "ENABLE_", "STAGING_", "MANGOHUD")
ENV_DENIED = {"WINEPREFIX", "WINELOADER", "WINESERVER", "WINEDLLPATH", "WINEBOOTSTRAPPER", "PROTON_LOG_DIR", "PROTON_CRASH_REPORT_DIR",
              "PROTON_DUMP_DEBUG_COMMANDS", "PROTON_ENABLE_WAYLAND_DEBUG", "MANGOHUD_CONFIGFILE", "MANGOHUD_CONFIG", "SDL_DYNAMIC_API",
              "WINE_MONO_OVERRIDES_PATH"}
ENV_TOKEN = re.compile(r"""(?<![A-Za-z0-9_])((?:__)?[A-Z][A-Z0-9_]{2,45})=("[^"\n]{0,120}"|'[^'\n]{0,120}'|[^\s"'`;|&$<>()\\]{1,120}(?=\s|$))""")
ENV_VALUE = re.compile(r"^[A-Za-z0-9_.,:=;+-]{0,120}$")
ARG_TOKEN = re.compile(r"^(-{1,2}|\+)[A-Za-z][A-Za-z0-9_.-]{0,40}(=[A-Za-z0-9_.,:+-]{0,40})?$")
WRAPPERS = ("gamemoderun", "mangohud")


class ProtonDBError(Exception):
    pass


def http_get(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json, text/plain"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


# ---- cache --------------------------------------------------------------------------------------------------------------------------

def _cache_path(key, cache):
    return os.path.join(cache, re.sub(r"[^A-Za-z0-9._-]", "_", key) + ".cache")


def cached_get(url, key, fetch=http_get, cache=CACHE, ttl=TTL_S, now=time.time, allow_missing=False):
    """(bytes or None, note). Fresh cache first, then the network (which refreshes it), then a stale cache if the network is down."""
    path = _cache_path(key, cache)
    fresh = stale = None
    try:
        age = now() - os.path.getmtime(path)
        with open(path, "rb") as fh:
            data = fh.read()
        stale = data
        if age < ttl:
            fresh = data
    except OSError:
        pass
    if fresh is not None:
        return fresh, ""
    try:
        data = fetch(url)
    except urllib.error.HTTPError as exc:
        if exc.code == 404 and allow_missing:
            return None, ""
        data = None
        error = f"{url} answered {exc.code}"
    except (OSError, ValueError) as exc:
        data, error = None, str(exc)
    if data is not None:
        try:
            os.makedirs(cache, exist_ok=True)
            with open(path, "wb") as fh:
                fh.write(data)
        except OSError:
            pass
        return data, ""
    if stale is not None:
        return stale, "offline - using what was saved earlier"
    raise ProtonDBError(f"Couldn't reach {urllib.parse.urlparse(url).netloc} ({error}).")


def _json(url, key, **kw):
    data, note = cached_get(url, key, **kw)
    if data is None:
        return None, note
    try:
        return json.loads(data), note
    except ValueError:
        raise ProtonDBError(f"{urllib.parse.urlparse(url).netloc} sent something that isn't JSON.")


# ---- finding the game ---------------------------------------------------------------------------------------------------------------

def appid_from_folder(folder, max_depth=2):
    """The Steam app id in a steam_appid.txt inside the game's folder (many Steam games ship one), or None."""
    root = os.path.abspath(folder)
    for dirpath, dirnames, filenames in os.walk(root):
        if dirpath[len(root):].count(os.sep) >= max_depth:
            dirnames[:] = []
        for name in filenames:
            if name.lower() == "steam_appid.txt":
                try:
                    with open(os.path.join(dirpath, name), encoding="utf-8", errors="replace") as fh:
                        text = fh.read(32).strip()
                except OSError:
                    continue
                if text.isdigit() and 0 < int(text) < 10 ** 10:
                    return int(text)
    return None


def search_steam(name, fetch=http_get, cache=CACHE, **kw):
    """[{"appid", "name"}] - Steam store matches for a game name."""
    term = " ".join((name or "").split())[:80]
    if not term:
        return []
    data, _ = _json(STEAM_SEARCH_URL.format(term=urllib.parse.quote(term)), "search-" + term.lower(), fetch=fetch, cache=cache, **kw)
    return [{"appid": int(i["id"]), "name": str(i.get("name", ""))} for i in (data or {}).get("items", [])
            if isinstance(i, dict) and str(i.get("id", "")).isdigit() and i.get("type", "app") == "app"]


def umu_lookup(title, fetch=http_get, cache=CACHE, **kw):
    """[{"title", "store", "umu_id", "note"}] rows of umu-database whose title matches (games from GOG/Epic/etc. that have a fix id)."""
    data, _ = cached_get(UMU_DB_URL, "umu-database", fetch=fetch, cache=cache, **kw)
    wanted = re.sub(r"[^a-z0-9]", "", (title or "").lower())
    if not wanted or data is None:
        return []
    rows = []
    for row in csv.DictReader(io.StringIO(data.decode("utf-8", "replace"))):
        have = re.sub(r"[^a-z0-9]", "", (row.get("TITLE") or "").lower())
        if have and have == wanted:
            rows.append({"title": row["TITLE"], "store": row.get("STORE", ""), "umu_id": row.get("UMU_ID", ""), "note": row.get("NOTE (Optional)", "")})
    return rows


# ---- the facts ----------------------------------------------------------------------------------------------------------------------

def summary(appid, **kw):
    """ProtonDB's current verdict: {"tier", "best", "trending", "score", "confidence", "total"} or None if it has none."""
    data, note = _json(SUMMARY_URL.format(appid=int(appid)), f"summary-{int(appid)}", allow_missing=True, **kw)
    if not isinstance(data, dict):
        return None
    return {"tier": data.get("tier"), "best": data.get("bestReportedTier"), "trending": data.get("trendingTier"), "score": data.get("score"),
            "confidence": data.get("confidence"), "total": data.get("total"), "note": note}


def reports(appid, **kw):
    data, note = _json(REPORTS_URL.format(appid=int(appid)), f"reports-{int(appid)}", allow_missing=True, **kw)
    return ([r for r in data if isinstance(r, dict)] if isinstance(data, list) else []), note


def protonfix(appid, **kw):
    """The umu-protonfixes script for a Steam game: {"url", "doc", "lines"} or None when there isn't one. GE-Proton and umu run it by themselves
    when the game's id is set (the pack does that), so its existence is the news - nothing has to be applied by hand."""
    data, _ = cached_get(FIX_URL.format(appid=int(appid)), f"fix-{int(appid)}", allow_missing=True, **kw)
    if data is None:
        return None
    text = data.decode("utf-8", "replace")
    match = re.match(r'\s*"""(.*?)"""', text, re.S)
    return {"url": FIX_URL.format(appid=int(appid)), "doc": (match.group(1).strip() if match else "")[:400], "lines": text.count("\n")}


# ---- reading the report notes -------------------------------------------------------------------------------------------------------

def rating_class(rating):
    r = (rating or "").strip().lower()
    return "good" if r in GOOD else "ok" if r in OK else "bad" if r in BAD else "unknown"


def sanitize_env(key, value):
    """(key, value) if this is an environment setting that is safe to put in front of a game, else None."""
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1]
    if key in ENV_DENIED or not key.startswith(ENV_PREFIXES) or not ENV_VALUE.match(value) or ".." in value:
        return None
    return key, value


def parse_launch_options(text):
    """{"env": {K: V}, "wrappers": [...], "args": [...]} from free text (a report note or a Steam launch-options line). Only what passes
    the checks above survives: nothing is ever run from it, and unknown words are dropped."""
    env, wrappers, args = {}, [], []
    for line in (text or "").splitlines():
        before, marker, after = line.partition("%command%")
        for m in ENV_TOKEN.finditer(before if marker else line):
            clean = sanitize_env(m.group(1), m.group(2))
            if clean:
                env.setdefault(*clean)
        if marker:
            for word in re.findall(r"[A-Za-z0-9_.-]+", before):
                if word in WRAPPERS and word not in wrappers:
                    wrappers.append(word)
            for token in after.split()[:6]:
                if not ARG_TOKEN.match(token):
                    break                                                  # the switches end at the first word that isn't one
                if token not in args:
                    args.append(token)
    return {"env": env, "wrappers": wrappers, "args": args}


def normalize_version(text):
    return re.sub(r"[^a-z0-9.]+", "-", (text or "").strip().lower()).strip("-")


def analyze(report_list, installed=(), auto_min=AUTO_MIN_GOOD):
    """Turn a list of ProtonDB reports into what to do about them.

    {"reports": n, "newest": unix time or None,
     "suggestions": [{"kind": "env"|"arg"|"wrapper", "key", "value", "text", "good", "total", "auto"}]  (best first; `auto` = switch on by default),
     "versions": [{"version", "good", "bad", "installed": Build name or None}]  (best first),
     "problems": [short texts of what went wrong in the bad reports]}"""
    counts, good_counts = Counter(), Counter()
    versions = {}
    problems = []
    newest = None
    for r in report_list:
        cls = rating_class(r.get("rating"))
        ts = r.get("timestamp")
        if isinstance(ts, (int, float)):
            newest = max(newest or 0, ts)
        version = normalize_version(r.get("protonVersion"))
        if version and re.search(r"\d", version) and cls != "unknown":
            v = versions.setdefault(version, {"good": 0, "bad": 0, "ok": 0})
            v[cls] += 1
        notes = r.get("notes") if isinstance(r.get("notes"), str) else ""
        if cls == "bad" and notes and len(problems) < 5:
            problems.append(" ".join(notes.split())[:240])
        parsed = parse_launch_options(notes)
        items = [("env", k, v, f"{k}={v}") for k, v in parsed["env"].items()] + [("wrapper", w, "", w) for w in parsed["wrappers"]] \
            + [("arg", a, "", a) for a in parsed["args"]]
        for item in items:
            counts[item] += 1
            if cls == "good":
                good_counts[item] += 1
    suggestions = []
    chosen_env = {}
    for item, total in sorted(counts.items(), key=lambda kv: (-good_counts[kv[0]], -kv[1], kv[0][3])):
        kind, key, value, text = item
        good = good_counts[item]
        if good == 0 and total < 2:
            continue                                                       # a single report from someone who then still had trouble
        auto = good >= auto_min
        if kind == "env":
            if key in chosen_env:                                          # two values for one setting: only the best-supported is automatic
                auto = False
            elif auto:
                chosen_env[key] = value
        suggestions.append({"kind": kind, "key": key, "value": value, "text": text, "good": good, "total": total, "auto": auto})
    have = {i.name.lower(): i.name for i in installed}
    out_versions = []
    for version, c in versions.items():
        match = next((name for low, name in have.items() if normalize_version(low).endswith(version) or version.endswith(normalize_version(low))
                      or version in normalize_version(low)), None)
        out_versions.append({"version": version, "good": c["good"], "bad": c["bad"], "installed": match})
    out_versions.sort(key=lambda v: (-(v["good"] - 2 * v["bad"]), -v["good"], v["version"]))
    return {"reports": len(report_list), "newest": newest, "suggestions": suggestions[:20], "versions": out_versions[:10], "problems": problems}


def info(appid, installed=(), fetch=http_get, cache=CACHE, **kw):
    """Everything known about a game, in one dict (for the pack creator and `cosmos-winpack info`). Parts that couldn't be fetched are
    left out with a message in "warnings" rather than failing the whole thing."""
    result = {"appid": int(appid), "summary": None, "fix": None, "analysis": None, "warnings": []}
    common = {"fetch": fetch, "cache": cache, **kw}
    for name, call in (("summary", lambda: summary(appid, **common)), ("fix", lambda: protonfix(appid, **common))):
        try:
            result[name] = call()
        except ProtonDBError as exc:
            result["warnings"].append(str(exc))
    try:
        rows, note = reports(appid, **common)
        result["analysis"] = analyze(rows, installed)
        if note:
            result["warnings"].append(note)
    except ProtonDBError as exc:
        result["warnings"].append(str(exc))
    return result


def choose_version(analysis, installed):
    """The installed Proton build the reports favour (most Gold/Platinum, fewest Borked), or None if none of the favoured ones are installed."""
    for v in (analysis or {}).get("versions", []):
        if v["installed"] and v["good"] > v["bad"]:
            return next((b for b in installed if b.name == v["installed"]), None)
    return None
