# /etc/profile.d/cosmos-sudo.sh - never type sudo.
#
# On CosmOS the one account has no sudo - root work goes through kronos - so typing `sudo dnf install x` isn't needed. In an
# interactive shell this puts /usr/libexec/cosmos-sudo/bin first in PATH: every ordinary command
# is answered by cosmos-priv, which runs it as you or as root depending on what it needs
# (see that file's header for the rules). Only interactive shells for the desktop user get it,
# and the real command always runs with PATH restored, so scripts and programs are untouched.
#
#   raw CMD ...           run one command exactly as typed, no privilege games
#   cosmos-autosudo off   turn it off in this shell (on turns it back on)
#   put FILE              write standard input to FILE, as root if needed - the one thing a
#                         shell cannot do for you, because `echo x > /etc/foo` is opened by
#                         your own shell before any command runs:  echo x | put /etc/foo
#                         (put -a appends)
#
# ~/.config/cosmos/autosudo: extra command names (one per line) to always run as root.

case $- in *i*) ;; *) return 0 2>/dev/null || exit 0 ;; esac
[ "$(id -u)" = 0 ] && return 0 2>/dev/null
[ -n "${COSMOS_NO_AUTOSUDO:-}" ] && return 0 2>/dev/null
[ -d /usr/libexec/cosmos-sudo/bin ] || return 0 2>/dev/null

raw() { COSMOS_NO_AUTOSUDO=1 command "$@"; }

put() {
    if [ "${1:-}" = "-a" ]; then shift; tee -a "$@" >/dev/null; else tee "$@" >/dev/null; fi
}

cosmos-autosudo() {
    local dir=/usr/libexec/cosmos-sudo/bin p new=
    case ${1:-status} in
        on)
            case ":$PATH:" in *":$dir:"*) ;; *) PATH=$dir:$PATH ;; esac
            hash -r; echo "cosmos-autosudo: on - commands run as root when they need it" ;;
        off)
            IFS=: read -ra parts <<< "$PATH"
            for p in "${parts[@]}"; do [ "$p" = "$dir" ] || new=${new:+$new:}$p; done
            PATH=$new; hash -r; echo "cosmos-autosudo: off - type sudo yourself" ;;
        *) case ":$PATH:" in *":$dir:"*) echo "cosmos-autosudo: on" ;; *) echo "cosmos-autosudo: off" ;; esac ;;
    esac
}

case ":$PATH:" in *":/usr/libexec/cosmos-sudo/bin:"*) ;; *) PATH=/usr/libexec/cosmos-sudo/bin:$PATH ;; esac
export PATH
