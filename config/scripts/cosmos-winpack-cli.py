#!/usr/bin/env python3
"""cosmos-winpack-cli - Windows game packs from the command line (the window is `cosmos-winpack`).

  create FOLDER [--name N] [--exe PATH] [--appid ID] [--proton auto|NAME] [--comp zstd|xz|gzip|lz4|lzo] [--level N] [--offline] [--replace]
                        squeeze a game folder into a pack; finds the exe, the Steam id, ProtonDB's tier, launch options and Proton version by itself
  list                  the packs you have
  info NAME_OR_ID       what ProtonDB and the fix database know about a game (a Steam id, or a name to search for)
  run PACK_ID           mount the pack and run it under its Proton version
  delete PACK_ID [--data]   remove a pack (--data also removes its prefix and saves)
  proton list           the Proton builds installed (CosmOS store, Steam, compatibilitytools.d)
  proton import PATH    import a build from a folder or an archive (.tar.gz/.xz/.zst/.bz2)
  proton releases [SOURCE]   what can be downloaded (GE-Proton, Proton-CachyOS, or owner/repo)
  proton download [NAME] [--source SOURCE]   download and import one (default: the newest GE-Proton)
  proton remove NAME    delete a build from the CosmOS store
"""
import argparse
import sys
import time

import cosmos_proton as cproton
import cosmos_protondb as pdb
import cosmos_winpack as wp


def human(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


def show_info(info):
    s = info.get("summary")
    if s:
        print(f"ProtonDB: {s['tier']} (best reported {s['best']}, trending {s['trending']}) - score {s['score']}, {s['total']} reports, confidence {s['confidence']}")
    else:
        print("ProtonDB: no rating for this game")
    fix = info.get("fix")
    print("Automatic fix: " + (f"yes - {fix['doc'] or 'a protonfixes script'}\n    {fix['url']}" if fix else "none (nothing extra to apply)"))
    a = info.get("analysis") or {}
    if a.get("reports"):
        when = time.strftime("%Y-%m", time.localtime(a["newest"])) if a.get("newest") else "?"
        print(f"Reports: {a['reports']} in the community mirror (newest {when} - it is a snapshot, not live)")
        for sug in a["suggestions"][:8]:
            print(f"  {'[auto]' if sug['auto'] else '[    ]'} {sug['text']:36} {sug['good']} good / {sug['total']} reports")
        for v in a["versions"][:5]:
            print(f"  Proton {v['version']:22} {v['good']} good, {v['bad']} bad" + (f"   installed as {v['installed']}" if v["installed"] else ""))
        for problem in a["problems"][:3]:
            print("  problem reported: " + problem)
    for w in info.get("warnings", []):
        print("  note: " + w)


def cmd_create(args):
    builds = cproton.discover()
    prepared = wp.prepare(args.folder, args.name, args.appid, builds, offline=args.offline)
    exe = args.exe or (prepared["executables"][0] if prepared["executables"] else None)
    if not exe:
        print("No .exe found - say which with --exe.", file=sys.stderr)
        return 2
    print(f"Game: {prepared['name']}   exe: {exe}")
    print(f"Steam id: {prepared['appid'] or 'not found'}" + (f" ({prepared['appid_source']})" if prepared["appid"] else ""))
    if not prepared["appid"] and prepared["candidates"]:
        print("  possible matches (use --appid): " + ", ".join(f"{c['name']} = {c['appid']}" for c in prepared["candidates"][:5]))
    if prepared["info"]:
        show_info(prepared["info"])
    for w in prepared["warnings"]:
        print("note: " + w)
    settings = wp.settings_for(prepared, exe, proton=args.proton or "auto")
    print(f"Proton: {settings['proton']}" + (f" (ProtonDB favours {prepared['proton']}, installed)" if prepared["proton"] and settings["proton"] == "auto" else ""))
    print("Settings: " + (", ".join([f"{k}={v}" for k, v in settings["env"].items()] + settings["wrappers"] + settings["args"]) or "none needed"))
    last = [-1]

    def progress(percent):
        if percent // 10 != last[0]:
            last[0] = percent // 10
            print(f"  packing {percent}%", flush=True)
    result = wp.create(args.folder, settings, comp=args.comp, level=args.level, progress=progress, replace=args.replace)
    print(f"Made {result['image']}: {human(result['bytes_in'])} -> {human(result['bytes_out'])}")
    return 0


def cmd_list(_args):
    packs = wp.list_packs()
    for p in packs:
        tier = (p.get("protondb") or {}).get("tier") or "-"
        print(f"{p['id']:28} {p['name']:32} {human(p['size']):>9}  ProtonDB {tier:9} Proton {p['proton']}")
    if not packs:
        print("No packs yet - `cosmos-winpack-cli create FOLDER` makes one.")
    return 0


def cmd_info(args):
    query = args.query
    if query.isdigit():
        appid = int(query)
    else:
        matches = pdb.search_steam(query)
        if not matches:
            print("Steam has no game by that name.", file=sys.stderr)
            return 1
        for m in matches[:5]:
            print(f"  {m['name']} = {m['appid']}")
        appid = matches[0]["appid"]
        print(f"Showing {matches[0]['name']}:")
    show_info(pdb.info(appid, cproton.discover()))
    return 0


def cmd_run(args):
    pack = next((p for p in wp.list_packs() if p["id"] == args.pack), None)
    if pack is None:
        print(f"No pack called {args.pack}.", file=sys.stderr)
        return 1
    return wp.launch(pack, log=lambda t: print(t, flush=True))


def cmd_delete(args):
    wp.delete_pack(args.pack, remove_data=args.data)
    print("Deleted.")
    return 0


def cmd_proton(args):
    if args.action == "list":
        for b in cproton.discover():
            print(f"{b.name:32} {b.source:7} {b.path}")
        return 0
    if args.action == "import":
        import os
        path = os.path.abspath(args.arg or "")
        b = cproton.import_folder(path) if os.path.isdir(path) else cproton.import_archive(path)
        print(f"Imported {b.name}")
        return 0
    if args.action == "releases":
        for r in cproton.list_releases(args.arg or "GE-Proton"):
            print(f"{r['name']:34} {human(r['size']):>9}" + ("  (checksummed)" if r["sha512_url"] else ""))
        return 0
    if args.action == "download":
        releases = cproton.list_releases(args.source)
        chosen = next((r for r in releases if r["name"] == args.arg), None) if args.arg else (releases[0] if releases else None)
        if chosen is None:
            print("No such release.", file=sys.stderr)
            return 1
        b = cproton.download_release(chosen, progress=lambda t: print(t, flush=True))
        print(f"Imported {b.name}")
        return 0
    if args.action == "remove":
        cproton.remove(args.arg or "")
        print("Removed.")
        return 0
    return 2


def main(argv):
    ap = argparse.ArgumentParser(prog="cosmos-winpack-cli", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("create")
    c.add_argument("folder")
    c.add_argument("--name")
    c.add_argument("--exe")
    c.add_argument("--appid", type=int)
    c.add_argument("--proton")
    c.add_argument("--comp", default="zstd", choices=sorted(wp.COMPRESSORS))
    c.add_argument("--level", type=int)
    c.add_argument("--offline", action="store_true")
    c.add_argument("--replace", action="store_true")
    sub.add_parser("list")
    i = sub.add_parser("info")
    i.add_argument("query")
    r = sub.add_parser("run")
    r.add_argument("pack")
    d = sub.add_parser("delete")
    d.add_argument("pack")
    d.add_argument("--data", action="store_true")
    p = sub.add_parser("proton")
    p.add_argument("action", choices=("list", "import", "releases", "download", "remove"))
    p.add_argument("arg", nargs="?")
    p.add_argument("--source", default="GE-Proton")
    args = ap.parse_args(argv)
    try:
        return {"create": cmd_create, "list": cmd_list, "info": cmd_info, "run": cmd_run, "delete": cmd_delete, "proton": cmd_proton}[args.cmd](args)
    except (wp.PackError, cproton.ProtonError, pdb.ProtonDBError) as exc:
        print(f"cosmos-winpack-cli: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
