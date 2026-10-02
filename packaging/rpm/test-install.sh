#!/bin/bash
# Installs packages from the local repository (build.py's output) into a CLEAN Fedora container and smoke-tests them, so "works on a normal Fedora" is
# checked, not assumed:   packaging/rpm/test-install.sh cosmos-shop [cosmos-... ]
set -euo pipefail
REPO=${COSMOS_RPM_WORK:-$HOME/.placements/rpm}/repo
[ -d "$REPO/repodata" ] || { echo "no repository at $REPO: run packaging/rpm/build.py first" >&2; exit 1; }
[ $# -gt 0 ] || { echo "usage: test-install.sh PACKAGE..." >&2; exit 2; }
podman run --rm -v "$REPO:/repo:ro,Z" -v "$(cd "$(dirname "$0")" && pwd)/../repo-site/RPM-GPG-KEY-cosmos:/key:ro,Z" quay.io/fedora/fedora:44 bash -c '
set -e
dnf install -y -q --repofrompath=cosmos,/repo --setopt=cosmos.gpgcheck=1 --setopt=cosmos.repo_gpgcheck=1 --setopt=cosmos.gpgkey=file:///key '"$*"' 2>&1 | tail -3
for pkg in $(rpm -qa --qf "%{NAME}-%{VERSION}\\n" "cosmos-*" | sort); do echo "installed: $pkg"; done
echo "--- files under /usr/lib/cosmos that no package owns (none is right):"
find /usr/lib/cosmos /usr/lib/kronos /usr/libexec/kronos /usr/libexec/cosmos-sudo -type f -not -name "*.pyc" 2>/dev/null | while read f; do rpm -qf "$f" >/dev/null 2>&1 || echo "  UNOWNED $f"; done
echo "(checked)"
echo "--- every command on PATH resolves to an executable file:"
for link in $(rpm -qa "cosmos-*" -l | grep "^/usr/bin/"); do
  target=$(readlink -f "$link"); [ -x "$target" ] || echo "  BROKEN $link -> $target"
done
echo "(checked)"
echo "--- every Python module compiles and imports:"
cd /usr/lib/cosmos
for f in $(ls *.py cosmos-*.py 2>/dev/null | sort -u); do
  python3 -m py_compile "$f" || echo "  DOES NOT COMPILE $f"
done
for f in $(ls *.py | grep -v "^cosmos-\|-"); do
  m=${f%.py}; python3 -c "import sys; sys.path.insert(0, \"/usr/lib/cosmos\"); import $m" 2>/tmp/err || echo "  IMPORT FAILS $m: $(tail -1 /tmp/err)"
done
if [ -d /usr/lib/kronos ]; then
  cd /usr/lib/kronos
  for f in kronos_*.py; do m=${f%.py}; python3 -c "import sys; sys.path[:0] = [\"/usr/lib/kronos\", \"/usr/lib/cosmos\"]; import $m" 2>/tmp/err || echo "  IMPORT FAILS $m: $(tail -1 /tmp/err)"; done
fi
echo "(checked)"
if [ -d /usr/share/gamebox ]; then
  echo "--- GameBox:"
  find /usr/share/gamebox /opt/cosmos-emulators -type f -not -name "*.pyc" 2>/dev/null | while read f; do rpm -qf "$f" >/dev/null 2>&1 || echo "  UNOWNED $f"; done
  for f in $(find /usr/share/gamebox /opt/cosmos-emulators -type f -perm /111 2>/dev/null); do
    head -c4 "$f" | grep -q ELF || continue
    missing=$(LD_LIBRARY_PATH=$(dirname "$f"):$(dirname "$f")/lib64 ldd "$f" 2>/dev/null | grep "not found" | tr -s " " | tr "\n" ";")
    [ -z "$missing" ] || echo "  LIBRARIES MISSING for $f: $missing"
  done
  python3 -m py_compile /usr/share/gamebox/launcher/run.py && echo "  launcher compiles"
  dir=$(HOME=/root gamebox-dir) && test -f "$dir/launcher/run.py" && echo "  gamebox-dir: $dir (has the launcher)"
  echo "(checked)"
fi
if [ -x /usr/bin/cosmos-wm ]; then echo "--- cosmos-wm libraries:"; ldd /usr/bin/cosmos-wm | grep "not found" || echo "  all found"; fi
for pkg in $(rpm -qa --qf "%{NAME}\n" "cosmos-*" | sort); do
  case $pkg in
    cosmos-common) python3 /usr/lib/cosmos/cosmos_catalog.py validate && echo "cosmos-common: catalog valid"
                   python3 -c "import sys; sys.path.insert(0, \"/usr/lib/cosmos\"); import cosmos_app_policy, cosmos_packs, cosmos_modules, cosmos_updates; print(\"cosmos-common: modules import\")"
                   test -x /usr/bin/find-terminals && echo "cosmos-common: find-terminals on PATH" ;;
    cosmos-shop)   python3 -c "
import importlib.machinery, importlib.util, sys
sys.path.insert(0, \"/usr/lib/cosmos\")
loader = importlib.machinery.SourceFileLoader(\"shop\", \"/usr/bin/cosmos-shop\")
spec = importlib.util.spec_from_loader(\"shop\", loader); m = importlib.util.module_from_spec(spec); loader.exec_module(m)
import cosmos_gamegrubber as g
print(\"cosmos-shop: loads; sources\", m.SOURCE_ORDER, \"| gamebox files:\", g.gamebox_dir())" ;;
  esac
done'
