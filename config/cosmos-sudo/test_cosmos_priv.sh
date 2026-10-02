#!/bin/bash
# Tests for cosmos-priv / cosmos-sudo-shims / profile.d/cosmos-sudo.sh in a scratch tree, with a
# stub `sudo` that records the call and runs the command (as you, marked COSMOS_STUB=1).
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
PROFILE="$HERE/../profile.d/cosmos-sudo.sh"
mkdir -p "$HOME/.placements"; T=$(mktemp -d -p "$HOME/.placements"); LOG=$T/sudo.log; fail=0     # scratch lives in ~/.placements, not /tmp
mkdir -p "$T/bin" "$T/fake" "$T/apps" "$T/home/.config/cosmos" "$T/work"
cat > "$T/bin/sudo" <<S
#!/bin/bash
[ "\$1" = "--" ] && shift
echo "SUDO: \${1##*/}" >> "$LOG"
COSMOS_STUB=1 exec "\$@"
S
chmod +x "$T/bin/sudo"
# "installed" commands: real coreutils plus fakes
for c in mv cp cat ls rm mkdir touch chmod tee head grep git; do ln -s "$(command -v $c)" "$T/fake/$c"; done
mk() { printf '#!/bin/bash\n%s\n' "$2" > "$T/fake/$1"; chmod +x "$T/fake/$1"; }
mk dnf 'echo ran-dnf "$@"'
mk rpm-ostree 'echo ran-rpm-ostree "$@"'
mk flatpak 'if [ "$1" = info ]; then case " ${FP_INSTALLED:-} " in *" ${!#} "*) exit 0;; esac; exit 1; fi; echo ran-flatpak "$@"'
mk am 'echo ran-am "$@"'
mk appman 'echo ran-appman "$@"'
mk nano 'echo edited "$@"'
mk needsroot 'if [ -z "${COSMOS_STUB:-}" ]; then echo "needsroot: /x: Permission denied" >&2; exit 1; fi; cat'
mk failother 'echo "boom: something else" >&2; exit 7'
mk runner 'dnf; mv nothing-here /etc/zz 2>/dev/null; true'
mk fakeapp 'echo gui'
printf '[Desktop Entry]\nName=Fake\nExec=env FOO=1 fakeapp --flag %%U\nType=Application\n' > "$T/apps/fake.desktop"

SHIMS=$T/shims BIN_DIRS="$T/fake" APPS="$T/apps" DISPATCHER="$HERE/cosmos-priv" bash "$HERE/cosmos-sudo-shims" >/dev/null
export COSMOS_SUDO_SHIM_DIR=$T/shims

run() {   # run "cmd..." -> output in $OUT, sudo calls in $LOG; interactive-like: shims first in PATH
    : > "$LOG"
    OUT=$( cd "$T/work" && HOME="$T/home" PATH="$T/shims:$T/bin:$T/fake:/usr/bin:/bin" bash --noprofile --norc -c "$1" 2>&1 < /dev/null ); RC=$?
}
expect() {  # expect "name" yes|no [output-substring]
    local got=no; [ -s "$LOG" ] && got=yes
    if [ "$got" = "$2" ] && { [ -z "${3:-}" ] || [[ "$OUT" == *"$3"* ]]; }; then echo "ok   $1"
    else echo "FAIL $1 (sudo: $got, wanted $2; output: $OUT)"; fail=1; fi
}

echo x > "$T/work/a"; echo y > "$T/work/own"
# one packaging per app: a fake policy where Kdenlive is a Flatpak and CopyQ is an RPM
cat > "$T/policy.json" <<'P'
{"apps": [{"name": "Kdenlive", "use": "flatpak", "flatpak": "org.kde.kdenlive", "rpm": ["kdenlive", "kdenlive-*"], "appimage": ["kdenlive"]},
          {"name": "CopyQ", "use": "rpm", "flatpak": "com.github.hluk.copyq", "rpm": ["copyq"], "appimage": ["copyq"]}]}
P
echo '{}' > "$T/empty-catalog.json"        # no curation for the packaging-rule tests below; the catalog tests set their own
export COSMOS_APP_POLICY="$T/policy.json" COSMOS_APP_POLICY_BIN="$HERE/../scripts/cosmos_app_policy.py" COSMOS_CATALOG="$T/empty-catalog.json"
[ -L "$T/shims/flatpak" ] && echo "ok   flatpak gets a (guard-only) shim" || { echo "FAIL no flatpak shim"; fail=1; }
run "dnf install -y kdenlive"               ; expect "dnf install of a Flatpak-only app is refused" no "installed as a Flatpak"
[ "$RC" != 0 ] && echo "ok   ... and exits non-zero" || { echo "FAIL refused install exited 0"; fail=1; }
run "dnf install kdenlive-libs"             ; expect "its sub-packages are refused too" no "installed as a Flatpak"
run "dnf install htop kdenlive"             ; expect "one bad package refuses the whole install" no "installed as a Flatpak"
run "dnf install -y copyq"                  ; expect "an RPM-policy app installs normally" yes "ran-rpm-ostree install"
run "dnf remove kdenlive"                   ; expect "removing is never refused" yes "ran-rpm-ostree uninstall kdenlive"
run "flatpak install flathub com.github.hluk.copyq"; expect "flatpak install of an RPM-policy app is refused" no "system package"
run "flatpak install -y flathub org.kde.kdenlive/x86_64/stable"; expect "the Flatpak of a Flatpak-policy app is allowed (and runs as you)" no "ran-flatpak install"
run "flatpak install --user flathub org.videolan.VLC"; expect "unrelated Flatpaks are untouched" no "ran-flatpak"
FP_INSTALLED="org.kde.krita" ; export FP_INSTALLED
run "flatpak install --user flathub org.kde.krita"; expect "a user copy of a system Flatpak is refused" no "already installed system-wide"
run "flatpak install flathub org.kde.krita"       ; expect "the scope check runs for the default (system) install too" no "already installed user-wide"
unset FP_INSTALLED
run "flatpak list"                          ; expect "other flatpak commands pass through" no "ran-flatpak list"
# the curated catalog: only what it lists can be installed, whatever the way it is asked for
cat > "$T/catalog.json" <<'C'
{"flatpak_families": [], "apps": [
  {"name": "Kdenlive", "category": "Creative", "flatpak": "org.kde.kdenlive"},
  {"name": "Tools", "category": "System", "rpm": ["htop", "copyq"]},
  {"name": "Ventoy", "category": "Utilities", "appimage": ["ventoy"]}]}
C
export COSMOS_CATALOG="$T/catalog.json"
[ -L "$T/shims/am" ] && [ -L "$T/shims/appman" ] && echo "ok   am and appman get shims" || { echo "FAIL no am/appman shim"; fail=1; }
run "dnf install -y htop"                   ; expect "a catalog package installs" yes "ran-rpm-ostree install"
run "dnf install -y htop nope"              ; expect "one package outside the catalog refuses the whole install" no "isn't in the CosmOS catalog"
run "dnf install ./local.rpm"               ; expect "a local rpm can't be checked by name, so it is refused" no "catalog"
run "flatpak install flathub org.kde.kdenlive"; expect "a catalog Flatpak is allowed (and runs as you)" no "ran-flatpak install"
run "flatpak install flathub org.mozilla.firefox"; expect "a Flatpak outside the catalog is refused" no "catalog"
run "flatpak install --from https://example.com/x.flatpakref"; expect "a .flatpakref can't be checked, so it is refused" no "catalog"
run "am -i ventoy"                          ; expect "a catalog AppImage installs (as you, never as root)" no "ran-am -i ventoy"
run "am -i ventoy evil"                     ; expect "one AppImage outside the catalog refuses the whole install" no "catalog"
run "appman install evil"                   ; expect "AppMan is guarded the same way" no "catalog"
run "am -l"                                 ; expect "listing needs no check and no root" no "ran-am -l"
export COSMOS_CATALOG="$T/empty-catalog.json"
run "dnf"                                   ; expect "always-root command uses sudo" yes ran-dnf
run "dnf install -y htop foo"               ; expect "dnf install becomes rpm-ostree install" yes "ran-rpm-ostree install --idempotent --apply-live htop foo"
run "dnf remove foo"                        ; expect "dnf remove becomes rpm-ostree uninstall" yes "ran-rpm-ostree uninstall foo"
run "dnf upgrade -y"                        ; expect "dnf upgrade becomes rpm-ostree upgrade" yes "ran-rpm-ostree upgrade"
run "dnf search foo"                        ; expect "other dnf subcommands stay dnf (as root)" yes "ran-dnf search foo"
run "mv a b"                                ; expect "mv in your own directory stays yours" no
run "mv a /etc/cosmos-test"                 ; expect "mv into /etc uses sudo" yes
run "mkdir newdir"                          ; expect "mkdir here stays yours" no
run "mkdir /usr/cosmos-test"                ; expect "mkdir in /usr uses sudo" yes
run "touch fresh"                           ; expect "touch here stays yours" no
run "chmod 600 own"                         ; expect "chmod on your file stays yours" no
run "chmod 644 /etc/hosts"                  ; expect "chmod on a system file uses sudo" yes
run "cat own"                               ; expect "cat of a readable file stays yours" no y
run "cat /etc/shadow"                       ; expect "cat of an unreadable file uses sudo" yes
run "ls /root"                              ; expect "ls of an unreadable directory uses sudo" yes
run "grep -r y ."                           ; expect "grep with no unreadable path stays yours" no
run "nano own"                              ; expect "editor on your own file stays yours" no "edited own"
run "nano /etc/hosts"                       ; expect "editor on a system file uses sudo" yes
echo hello > "$T/work/input"
run "needsroot < input"                     ; expect "permission error is retried as root, redirected stdin kept" yes hello
run "printf hello | needsroot"              ; expect "a piped command is not retried (stdin cannot be replayed)" no "not run again"
run "failother"                             ; expect "another kind of failure is not retried" no boom
[ "$RC" = 7 ] && echo "ok   exit status is passed through" || { echo "FAIL exit status $RC"; fail=1; }
run "COSMOS_NO_AUTOSUDO=1 dnf"              ; expect "COSMOS_NO_AUTOSUDO skips the wrapper" no
run "runner"                                ; expect "programs a wrapped command starts are not wrapped (its dnf runs as is)" no
for n in git sudo fakeapp; do [ -e "$T/shims/$n" ] && { echo "FAIL $n should have no shim"; fail=1; } || echo "ok   no shim for $n"; done
[ -e "$T/shims/dnf" ] && echo "ok   shim exists for dnf" || { echo "FAIL no dnf shim"; fail=1; }

# the profile script: PATH, raw, put, cosmos-autosudo (needs the real shim dir to exist, so point it at ours)
sed "s#/usr/libexec/cosmos-sudo/bin#$T/shims#g" "$PROFILE" > "$T/profile.sh"
o=$( HOME="$T/home" bash --noprofile --norc -i -c "source $T/profile.sh; case \":\$PATH:\" in *:$T/shims:*) echo path-ok;; esac; cosmos-autosudo off >/dev/null; case \":\$PATH:\" in *:$T/shims:*) echo still-there;; *) echo off-ok;; esac; cosmos-autosudo on >/dev/null; case \":\$PATH:\" in *:$T/shims:*) echo on-ok;; esac; echo hi | put $T/putfile; cat $T/putfile" 2>/dev/null )
: > "$LOG"
o2=$( cd "$T/work" && HOME="$T/home" PATH="$T/bin:$T/fake:/usr/bin:/bin" bash --noprofile --norc -i -c "source $T/profile.sh; raw dnf" 2>/dev/null )
[ ! -s "$LOG" ] && [[ "$o2" == *ran-dnf* ]] && echo "ok   raw runs a command with no privilege games" || { echo "FAIL raw ($o2; $(cat "$LOG"))"; fail=1; }
for want in path-ok off-ok on-ok hi; do [[ "$o" == *"$want"* ]] && echo "ok   profile: $want" || { echo "FAIL profile: $want ($o)"; fail=1; }; done
o=$( HOME="$T/home" bash --noprofile --norc -c "source $T/profile.sh; echo \$PATH" 2>/dev/null )
[[ "$o" == *"$T/shims"* ]] && { echo "FAIL non-interactive shell got the shims"; fail=1; } || echo "ok   non-interactive shells are untouched"

# with kronos installed (CosmOS: no sudo at all) the same decisions go through `kronos exec --`, and power / systemctl --user need nothing
mk systemctl 'echo ran-systemctl "$@"'; mk reboot 'echo ran-reboot'; mk modprobe 'echo ran-modprobe "$@"'
mkdir -p "$T/kbin"
cat > "$T/kbin/kronos" <<S
#!/bin/bash
[ "\$1" = exec ] && shift; [ "\$1" = "--" ] && shift
echo "KRONOS: \$*" >> "$LOG"
COSMOS_STUB=1 exec "\$@"
S
chmod +x "$T/kbin/kronos"
SHIMS=$T/shims2 BIN_DIRS="$T/fake" APPS="$T/apps" DISPATCHER="$HERE/cosmos-priv" bash "$HERE/cosmos-sudo-shims" >/dev/null
krun() { : > "$LOG"; OUT=$( cd "$T/work" && HOME="$T/home" COSMOS_SUDO_SHIM_DIR=$T/shims2 PATH="$T/shims2:$T/kbin:$T/fake:/usr/bin:/bin" bash --noprofile --norc -c "$1" 2>&1 < /dev/null ); RC=$?; }
kexpect() { if [[ "$(cat "$LOG")" == *"$2"* ]] && [[ "$OUT" == *"${3:-}"* ]]; then echo "ok   $1"; else echo "FAIL $1 (log: $(cat "$LOG"); output: $OUT)"; fail=1; fi; }
knot() { if [ ! -s "$LOG" ]; then echo "ok   $1"; else echo "FAIL $1 (log: $(cat "$LOG"))"; fail=1; fi; }
krun "dnf install -y copyq"     ; kexpect "kronos: dnf install goes through kronos exec as rpm-ostree" "KRONOS: rpm-ostree install --idempotent --apply-live copyq" "ran-rpm-ostree"
krun "dnf remove copyq"         ; kexpect "kronos: dnf remove" "KRONOS: rpm-ostree uninstall copyq"
krun "modprobe -r foo"          ; kexpect "kronos: anything else root goes to kronos too" "KRONOS: $T/fake/modprobe -r foo"
krun "systemctl restart sshd.service"; kexpect "kronos: systemctl" "KRONOS: $T/fake/systemctl restart sshd.service"
krun "systemctl --user status x"; knot "systemctl --user needs no admin"
krun "systemctl status sshd.service"; knot "systemctl status (looking) needs no admin"
krun "systemctl is-enabled greetd"; knot "systemctl is-enabled needs no admin"
krun "systemctl -l status sshd"; knot "systemctl with an option before the verb, still looking only"
krun "reboot"                   ; knot "reboot needs no admin (the login manager allows it)"
krun "dnf install kdenlive"     ; knot "the app policy still refuses first"
rm -rf "$T"; exit $fail
