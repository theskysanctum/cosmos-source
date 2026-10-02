#!/usr/bin/env python3
"""Builds the cosmos-* RPMs from packages.json, and a local dnf repository from them.

packages.json is the one description of what each package contains (where every file comes from and goes), so a package is data, not a hand-written
spec. For each package this stages the files, writes the spec, runs rpmbuild, and at the end runs createrepo_c, leaving a repository laid out exactly
as it would be served from a web server (repo/x86_64/, repo/noarch/, repodata/).

  build.py                      build every package, then the repository
  build.py PACKAGE [PACKAGE...] build only these
  build.py --list               the packages and what they need
  build.py --check              validate packages.json without building (missing sources, files claimed twice, unknown dependencies)

Everything is built under ~/.placements/rpm (override with COSMOS_RPM_WORK): nothing goes in /tmp. The repository is <work>/repo.

packages.json, per package:
  name, summary, description, arch (noarch|x86_64), requires, recommends, suggests, conflicts, provides, buildrequires
  files    [{"src": "config/x.py", "dest": "/usr/lib/cosmos/x.py", "mode": "0755", "config": false}]
           [{"glob": "config/scripts/cosmos_*.py", "exclude": ["test_*"], "dest_dir": "/usr/lib/cosmos", "mode": "0644"}]
  links    [{"dest": "/usr/bin/cosmos-shop", "target": "/usr/lib/cosmos/cosmos-shop.py"}]
  dirs     ["/usr/lib/cosmos"]                 directories this package owns
  build    a shell command run in the source tree before staging (C programs); the files it makes are then listed in `files`, with "@WORK@" in
           a src standing for the work folder (where build scripts put their output)
  post, postun, posttrans   scriptlet bodies
  filetriggers   [{"paths": "/usr/bin /usr/share/applications", "script": "..."}]: run once per transaction after files appear in those folders

A files entry may carry "replace": [["old text", "new text"], ...]: exact replacements applied to the file as it is staged, for the paths inside it
that differ between the image's layout (/usr/local/...) and the packages' (/usr/lib/cosmos, /usr/lib/kronos, ...). Each "old text" must occur in the
file, so a file that changes shape breaks the build instead of shipping a stale path.
"""
import fnmatch
import shlex
import shutil
import glob
import json
import os
import re
import shlex
import subprocess
import time
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
MANIFEST = Path(__file__).resolve().parent / "packages.json"
WORK = Path(os.environ.get("COSMOS_RPM_WORK") or Path.home() / ".placements" / "rpm")
NAME_RE = re.compile(r"^[a-z][a-z0-9.+-]*$")
DEST_RE = re.compile(r"^/[A-Za-z0-9._+@,~()&'!# -][A-Za-z0-9._+@,~()&'!# /-]*$")   # no % * ? [ ] { } " $ \ : the spec would read them as macros or globs
MODE_RE = re.compile(r"^[0-7]{4}$")


def load(path=MANIFEST):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


# ---- expanding a package's file list ------------------------------------------------------------------------------------------------------

def _excluded(rel, patterns):
    """True when the tree-relative path `rel` is one of the patterns, inside one, or matches one (as a path or as a file name)."""
    return any(rel == pat or rel.startswith(pat + "/") or fnmatch.fnmatch(rel, pat) or fnmatch.fnmatch(rel.rsplit("/", 1)[-1], pat) for pat in patterns)


def walk_tree(entry, root=ROOT):
    """(files, links, dirs) of a "tree" entry: a whole folder copied under dest_dir, keeping its layout and each file's executable bit, minus the excludes (paths relative to the folder; __pycache__ and .pyc are always left out). Symlinks stay symlinks."""
    top = root / entry["tree"]
    dest_top = entry["dest_dir"].rstrip("/")
    patterns = list(entry.get("exclude", [])) + ["__pycache__", "*.pyc"]
    files, links, dirs = [], [], [dest_top]
    for folder, subdirs, names in os.walk(top):
        subdirs.sort()
        subdirs[:] = [d for d in subdirs if not _excluded((Path(folder) / d).relative_to(top).as_posix(), patterns)]
        for d in subdirs:
            path = Path(folder) / d
            rel = path.relative_to(top).as_posix()
            if path.is_symlink():
                links.append({"dest": f"{dest_top}/{rel}", "target": os.readlink(path)})
            else:
                dirs.append(f"{dest_top}/{rel}")
        subdirs[:] = [d for d in subdirs if not (Path(folder) / d).is_symlink()]
        for name in sorted(names):
            path = Path(folder) / name
            rel = path.relative_to(top).as_posix()
            if _excluded(rel, patterns):
                continue
            if path.is_symlink():
                links.append({"dest": f"{dest_top}/{rel}", "target": os.readlink(path)})
            elif path.is_file():
                mode = entry.get("mode") or ("0755" if os.access(path, os.X_OK) else "0644")
                files.append((path, f"{dest_top}/{rel}", mode, False))
    return files, links, dirs


def tree_links(package, root=ROOT):
    return [link for entry in package.get("files", []) if "tree" in entry for link in walk_tree(entry, root)[1]]


def tree_dirs(package, root=ROOT):
    return [d for entry in package.get("files", []) if "tree" in entry for d in walk_tree(entry, root)[2]]


def expand_files(package, root=ROOT):
    """[(source path, dest, mode, is_config)] with every glob expanded (sorted, so builds are reproducible)."""
    out = []
    for entry in package.get("files", []):
        mode = entry.get("mode", "0644")
        if "tree" in entry:
            out += walk_tree(entry, root)[0]
        elif "glob" in entry:
            matches = sorted(glob.glob(str(root / entry["glob"])))
            for match in matches:
                base = os.path.basename(match)
                if any(fnmatch.fnmatch(base, pattern) for pattern in entry.get("exclude", [])) or not os.path.isfile(match):
                    continue
                out.append((Path(match), entry["dest_dir"].rstrip("/") + "/" + base, mode, bool(entry.get("config"))))
        else:
            out.append((root / entry["src"].replace("@WORK@", str(WORK)), entry["dest"], mode, bool(entry.get("config"))))
    return out


def replacements(package):
    """{dest: [(old, new), ...]} for the files entries that carry "replace"."""
    out = {}
    for entry in package.get("files", []):
        if entry.get("replace"):
            if "dest" in entry:
                out[entry["dest"]] = [tuple(pair) for pair in entry["replace"]]
    return out


def transformed(source, dest, package):
    """The bytes to stage for a file: its content with the package's replacements applied (each old text must be present)."""
    data = Path(source).read_bytes()
    for old, new in replacements(package).get(dest, []):
        if old.encode() not in data:
            raise ValueError(f"{package['name']}: {dest}: the text {old!r} to replace is not in {source}")
        data = data.replace(old.encode(), new.encode())
    return data


def problems(manifest, root=ROOT, built=()):
    """Mistakes in packages.json: bad names, missing sources, a file in two packages, a dependency on a package that does not exist."""
    found, names, owner = [], set(), {}
    packages = manifest.get("packages", [])
    for package in packages:
        name = package.get("name", "")
        if not NAME_RE.match(name):
            found.append(f"'{name}': not a valid package name")
        if name in names:
            found.append(f"{name}: listed twice")
        names.add(name)
        if package.get("arch", "noarch") not in ("noarch", "x86_64"):
            found.append(f"{name}: arch must be noarch or x86_64")
        for entry in package.get("files", []):
            if not (("src" in entry and "dest" in entry) or ("glob" in entry and "dest_dir" in entry) or ("tree" in entry and "dest_dir" in entry)):
                found.append(f"{name}: a files entry needs src and dest, glob and dest_dir, or tree and dest_dir: {entry}")
            if not MODE_RE.match(entry.get("mode", "0644")):
                found.append(f"{name}: bad mode {entry.get('mode')}")
        for link in package.get("links", []):
            if not (DEST_RE.match(link.get("dest", "")) and DEST_RE.match(link.get("target", ""))):
                found.append(f"{name}: bad link {link}")
        for source, dest, _mode, _config in expand_files(package, root):
            if not DEST_RE.match(dest):
                found.append(f"{name}: bad destination {dest}")
            if name not in built and not package.get("build") and not source.is_file():
                found.append(f"{name}: missing source {source.relative_to(root) if root in source.parents else source}")
            if dest in owner and owner[dest] != name:
                found.append(f"{dest} is in both {owner[dest]} and {name}")
            owner[dest] = name
        for link in package.get("links", []):
            if link["dest"] in owner and owner[link["dest"]] != name:
                found.append(f"{link['dest']} is in both {owner[link['dest']]} and {name}")
            owner[link["dest"]] = name
        for dest, pairs in replacements(package).items():
            for source, d, _mode, _config in expand_files(package, root):
                if d == dest and source.is_file():
                    text = source.read_text(encoding="utf-8", errors="replace")
                    for old, _new in pairs:
                        if old not in text:
                            found.append(f"{name}: {dest}: the text {old!r} to replace is not in the source file")
        if package.get("build") and not package.get("files"):
            found.append(f"{name}: has a build command but no files")
    for package in packages:
        for dep in package.get("requires", []) + package.get("recommends", []) + package.get("suggests", []):
            base = re.split(r"[ <>=]", dep, maxsplit=1)[0]
            if base.startswith("cosmos-") and base not in names and base not in ("cosmos-release",):
                found.append(f"{package['name']}: needs {base}, which is not in packages.json")
    return found


# ---- spec generation ----------------------------------------------------------------------------------------------------------------------

def spec_text(package, version, release="1", root=ROOT):
    files = expand_files(package, root)
    name = package["name"]
    lines = [f"Name:           {name}", f"Version:        {version}", f"Release:        {release}%{{?dist}}",
             f"Summary:        {package['summary']}", "License:        GPL-3.0-or-later", f"Source0:        {name}-{version}.tar.gz"]
    if package.get("arch", "noarch") == "noarch":
        lines.append("BuildArch:      noarch")
    for key, tag in (("buildrequires", "BuildRequires"), ("requires", "Requires"), ("recommends", "Recommends"), ("suggests", "Suggests"),
                     ("conflicts", "Conflicts"), ("provides", "Provides")):
        for item in package.get(key, []):
            lines.append(f"{tag + ':':<16}{item}")
    lines += ["", "%description", package.get("description", package["summary"]), "", "%prep", "%setup -q", "", "%build"]
    lines.append("# nothing to build here" if not package.get("build") else "# built before staging (see build.py)")
    lines += ["", "%install", "cp -a root/. %{buildroot}/"]
    lines += ["", "%files", f"%license /usr/share/licenses/{name}/COPYING"]
    for directory in package.get("dirs", []) + tree_dirs(package, root):
        lines.append(f'%dir "{directory}"')
    for _source, dest, mode, config in files:
        prefix = "%config(noreplace) " if config else ""
        lines.append(f'{prefix}%attr({mode[1:]},root,root) "{dest}"')
    for link in package.get("links", []) + tree_links(package, root):
        lines.append(f'"{link["dest"]}"')
    if package.get("post"):
        lines += ["", "%post", package["post"]]
    if package.get("postun"):
        lines += ["", "%postun", package["postun"]]
    if package.get("posttrans"):
        lines += ["", "%posttrans", package["posttrans"]]
    for trigger in package.get("filetriggers", []):
        lines += ["", f"%transfiletriggerin -- {trigger['paths']}", trigger["script"]]
    lines += ["", "%changelog", f"* Fri Sep 25 2026 Skye Morrison <yummybonbun12@gmail.com> - {version}-{release}", "- Built from packages.json."]
    return "\n".join(lines) + "\n"


# ---- building -----------------------------------------------------------------------------------------------------------------------------

def stage(package, version, work=WORK, root=ROOT):
    """Copies the package's files into <work>/stage/<name>-<version>/root/ and returns the tarball path."""
    name = package["name"]
    top = work / "stage" / f"{name}-{version}"
    if top.exists():
        subprocess.run(["rm", "-rf", str(top)], check=True)
    for source, dest, mode, _config in expand_files(package, root):
        target = top / "root" / dest.lstrip("/")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(transformed(source, dest, package))
        os.chmod(target, int(mode, 8))
    licence = top / "root" / "usr" / "share" / "licenses" / name / "COPYING"
    licence.parent.mkdir(parents=True, exist_ok=True)
    licence.write_bytes((root / "COPYING").read_bytes())
    for link in package.get("links", []) + tree_links(package, root):
        target = top / "root" / link["dest"].lstrip("/")
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.is_symlink() or target.exists():
            target.unlink()
        os.symlink(link["target"], target)
    for directory in package.get("dirs", []) + tree_dirs(package, root):
        (top / "root" / directory.lstrip("/")).mkdir(parents=True, exist_ok=True)
    (work / "SOURCES").mkdir(parents=True, exist_ok=True)
    tarball = work / "SOURCES" / f"{name}-{version}.tar.gz"
    with tarfile.open(tarball, "w:gz") as tar:
        tar.add(top, arcname=f"{name}-{version}")
    return tarball


def build_one(package, version, release, work=WORK, root=ROOT):
    name = package["name"]
    if package.get("build"):
        subprocess.run(package["build"], shell=True, cwd=root, check=True)
    stage(package, version, work, root)
    (work / "SPECS").mkdir(parents=True, exist_ok=True)
    spec = work / "SPECS" / f"{name}.spec"
    spec.write_text(spec_text(package, version, release, root), encoding="utf-8")
    log = work / f"{name}.build.log"
    # 0x0002 (an invalid RUNPATH) is allowed: the prebuilt MKXP-Z binary GameBox ships carries the directory it was built in next to its own $ORIGIN/lib64.
    done = subprocess.run(["rpmbuild", "-bb", "--define", f"_topdir {work}", "--define", "debug_package %{nil}", str(spec)],
                          capture_output=True, text=True, env={**os.environ, "QA_RPATHS": "2"})
    log.write_text(done.stdout + done.stderr, encoding="utf-8")
    if done.returncode != 0:
        print((done.stdout + done.stderr)[-3000:], file=sys.stderr)
        raise SystemExit(f"rpmbuild failed for {name} (log: {log})")
    rpms = sorted((work / "RPMS").glob(f"*/{name}-{version}-{release}*.rpm"))
    return rpms[-1]


def signing_home():
    """The GnuPG home that holds the private signing key (outside every repository), or None when there is none."""
    home = Path(os.environ.get("COSMOS_SIGNING_HOME") or Path.home() / ".config" / "cosmos-signing" / "gnupg")
    return home if (home / "pubring.kbx").exists() else None


def sign_rpms(rpms, home, key="CosmOS Packages (theskysanctum)"):
    """Signs the RPMs in place. Uses rpmsign when it is installed, otherwise runs it in a throwaway Fedora container (nothing is installed on the host)."""
    args = ["--define", f"_gpg_name {key}", "--define", "_gpg_sign_cmd_extra_args --pinentry-mode loopback --passphrase ''"]
    if shutil.which("rpmsign"):
        subprocess.run(["rpmsign", "--addsign", *args, *map(str, rpms)], check=True, env={**os.environ, "GNUPGHOME": str(home)})
        return
    folders = sorted({str(r.parent) for r in rpms})
    mounts = [x for i, f in enumerate(folders) for x in ("-v", f"{f}:/rpms{i}:Z")]
    script = "dnf -y -q install rpm-sign >/dev/null && " + " && ".join(
        f"rpmsign --addsign {shlex.join(args)} /rpms{i}/*.rpm" for i in range(len(folders)))
    subprocess.run(["podman", "run", "--rm", "-v", f"{home}:/gnupg:Z", "-e", "GNUPGHOME=/gnupg", *mounts, "quay.io/fedora/fedora:44", "sh", "-c", script], check=True)


def package_name(path):
    """The package name in an RPM file name (NAME-VERSION-RELEASE.ARCH.rpm)."""
    return re.sub(r"-[^-]+-[^-]+\.[^.]+\.rpm$", "", Path(path).name)


def newest_per_package(rpms):
    """Of the RPMs built over time, only the most recently built one of each package and architecture (older ones stay in RPMS but are not published)."""
    newest = {}
    for rpm in rpms:
        key = (package_name(rpm), rpm.parent.name)
        if key not in newest or rpm.stat().st_mtime >= newest[key].stat().st_mtime:
            newest[key] = rpm
    return sorted(newest.values())


def build_repo(work=WORK):
    """<work>/repo/: every built RPM (by architecture) and its repodata, ready to be served as a dnf repository. When a signing key exists the RPMs and
    the repository metadata (repomd.xml.asc) are signed; without one the repository is left unsigned and says so."""
    repo = work / "repo"
    if repo.exists():
        subprocess.run(["rm", "-rf", str(repo)], check=True)
    for rpm in newest_per_package(sorted((work / "RPMS").glob("*/*.rpm"))):
        target = repo / rpm.parent.name / rpm.name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(rpm.read_bytes())
    home = signing_home()
    if home:
        sign_rpms(sorted(repo.glob("*/*.rpm")), home)
    else:
        print("no signing key found: the repository is NOT signed", file=sys.stderr)
    subprocess.run(["createrepo_c", "--quiet", str(repo)], check=True)
    if home:
        subprocess.run(["gpg", "--batch", "--yes", "--pinentry-mode", "loopback", "--passphrase", "", "--armor", "--detach-sign", "-u",
                        "CosmOS Packages (theskysanctum)", str(repo / "repodata" / "repomd.xml")],
                       check=True, env={**os.environ, "GNUPGHOME": str(home)})
    return repo


def main(argv):
    manifest = load()
    if "--check" in argv:
        bad = problems(manifest)
        for line in bad:
            print("problem:", line)
        print(f"{len(manifest['packages'])} package(s) checked")
        return 1 if bad else 0
    if "--list" in argv:
        for package in manifest["packages"]:
            print(f"{package['name']:24} {package.get('arch', 'noarch'):7} needs: {', '.join(package.get('requires', [])) or '-'}")
        return 0
    bad = problems(manifest)
    if bad:
        for line in bad:
            print("problem:", line, file=sys.stderr)
        return 1
    # Every build gets a release of its own (1.<UTC time>): the same version and release with different contents made dnf serve a stale copy of a package
    # from its cache (an image was built from RPMs a day old), and a newer release is also what lets an installed machine upgrade.
    version, release = manifest.get("version", "0.1.0"), f"{manifest.get('release', '1')}.{time.strftime('%Y%m%d%H%M%S', time.gmtime())}"
    wanted = [a for a in argv if not a.startswith("-")]
    for package in manifest["packages"]:
        if wanted and package["name"] not in wanted:
            continue
        print("building", package["name"], flush=True)
        print(" ", build_one(package, version, release))
    print("repository:", build_repo())
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
