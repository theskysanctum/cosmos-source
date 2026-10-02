#!/bin/bash
# Tests for gamebox-dir: precedence, first-use copy, refresh keeping runtime files.
HERE=$(cd "$(dirname "$0")" && pwd)
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
fail=0
ok() { echo "ok   - $1"; }
bad() { echo "FAIL - $1 (got: $2)"; fail=1; }
check() { [ "$2" = "$3" ] && ok "$1" || bad "$1" "$2"; }

export HOME="$T/home"; mkdir -p "$HOME"
export COSMOS_GAMEBOX_SEED="$T/seed" COSMOS_GAMEBOX_LIVE="$T/live"
run() { "$HERE/gamebox-dir"; }

check "nothing anywhere: names the writable location" "$(run)" "$T/live"

mkdir -p "$T/seed/launcher"; echo "v1" > "$T/seed/launcher/run.py"; echo "stamp1" > "$T/seed/.cosmos-image-stamp"; echo "IGDB_CLIENT_ID=abc" > "$T/seed/.env"
check "baked copy is used via the writable copy" "$(run)" "$T/live"
check "the copy has the files" "$(cat "$T/live/launcher/run.py")" "v1"
check "the copy has the stamp" "$(cat "$T/live/.cosmos-image-stamp")" "stamp1"

mkdir -p "$T/live/settings"; echo secret > "$T/live/settings/igdb_credentials.json"
echo "edited by hot reload" > "$T/live/launcher/run.py"
run >/dev/null
check "dotfiles such as .env are copied too" "$(cat "$T/live/.env")" "IGDB_CLIENT_ID=abc"
check "same stamp: nothing is overwritten" "$(cat "$T/live/launcher/run.py")" "edited by hot reload"

echo "v2" > "$T/seed/launcher/run.py"; echo "stamp2" > "$T/seed/.cosmos-image-stamp"
touch -d "+1 hour" "$T/seed/launcher/run.py"
run >/dev/null
check "new image: code is refreshed" "$(cat "$T/live/launcher/run.py")" "v2"
check "new image: runtime files are kept" "$(cat "$T/live/settings/igdb_credentials.json")" "secret"
check "new image: stamp updated" "$(cat "$T/live/.cosmos-image-stamp")" "stamp2"

mkdir -p "$HOME/repos/gamebox/launcher"; touch "$HOME/repos/gamebox/launcher/run.py"
check "a checkout under ~/repos is no longer looked for" "$(run)" "$T/live"
check "GAMEBOX_REPO_DIR beats everything" "$(GAMEBOX_REPO_DIR=/x/y run)" "/x/y"

rm -rf "$HOME/repos" "$T/live"; mkdir -p "$T/live-ro"; chmod 555 "$T/live-ro"
out=$(COSMOS_GAMEBOX_LIVE="$T/live-ro/sub" run 2>/dev/null)
check "unwritable target falls back to the image copy" "$out" "$T/seed"
chmod 755 "$T/live-ro"

if [ ! -w /cosmos/app ]; then     # a machine without the image's data folder: the copy goes in the user's own data folder
	out=$(env -u COSMOS_GAMEBOX_LIVE XDG_DATA_HOME="$T/xdg" "$HERE/gamebox-dir")
	check "no /cosmos/app: the copy lives under the user's data folder" "$out" "$T/xdg/cosmos/gamebox"
fi
exit $fail
