# Kronos - the privilege split for CosmOS (design)

Status: built and tested end to end without root (`config/kronos/`, `config/scripts/cosmos_kronos.py`, `kronos-prompt.py`, `cosmos-admin.py`, `cosmos_recovery.py`): the admin passphrase, the typed actions, the decision engine, the audit log, the socket daemon `kronosd`, the root helper `act` (re-validates every action), the client and `kronos` command, the passphrase prompt, the Admin activity window and the recovery app's logic. The image cut-over is in the Containerfile (no wheel, no sudoers for the desktop account, kronos account/units/tmpfiles) and every root-using program now asks kronos - but **none of it has been booted**: that is a rebuild (section 10, step 4). The recovery boot entry is not created (see section 7). The "no terminal" enforcement (section 11) is built.

## 1. Why

Today the desktop account `user` is in `wheel` with `NOPASSWD: ALL` (Containerfile, `/etc/sudoers.d/wheel`).
Every program that runs as that account - a browser tab exploit, an AppImage, a game, a Flatpak with a hole -
is one `sudo` away from root, silently. The "never type sudo" layer (`cosmos-priv`) makes that convenient on
purpose. The split keeps the convenience for *you* but stops it being ambient for *everything running as you*:

- the desktop account has **no** root path of its own (no wheel, no sudoers, no polkit admin);
- one system account, **`kronos`**, with no shell and no login, is the only route to admin work;
- what it will do is a **short list of typed actions**, each validated, most of them without a prompt, the
  dangerous ones behind an approval, and **every request is written to an audit log**;
- anything not on the list is refused, not "run as root because it asked".

Non-goal: defending against an attacker who already controls the whole desktop session *and* can inject
input (see section 8). This is a blast-radius and accident reducer with a real log, not a vault.

## 2. Where root is used now (the migration surface)

| Today | How it gets root | Replacement |
|---|---|---|
| `dnf install/remove/upgrade` typed in a task window / Shop | `cosmos-priv` -> `sudo rpm-ostree ...` | action `pkg.*` |
| `flatpak install --system`, updates | polkit (wheel) | action `flatpak.*` |
| CosmOS Updates "apply" (bootc / rpm-ostree upgrade) | `sudo` in `cosmos-task` | action `os.upgrade` |
| mount / unmount internal drives (cosmos-files) | polkit rule for wheel (`51-cosmos-files-udisks.rules`) | **not a kronos action**: the same polkit rule, now for the `user` account's active session with no `wheel` (done) - a root mount would land in root's own directory and be unusable |
| controller LEDs on Sony/Nintendo pads | udev makes files `input`-group writable; `sudo -n tee` fallback in `cosmos_pads.write_attr` | keep udev; drop the sudo fallback |
| `systemctl`, `journalctl`, `mount`, `modprobe`, `useradd`... typed by hand | `cosmos-priv` ALWAYS list | `kronos run` (passphrase-gated, section 5) |
| read/write of root-owned files (`cat /etc/shadow`, `nano /etc/hosts`) | `cosmos-priv` READ/WRITE/EDITORS classes | `file.read` / `file.write` on an allowlist, else `kronos run` |
| GameBox deploy/install scripts (5 `sudo` uses in the launcher, packaging/) | `sudo` | `kronos run` for now, then typed actions |
| power (reboot/shutdown), NetworkManager, Bluetooth | logind/NM polkit for the *active local session* (no wheel needed) | unchanged |
| greeter Restart/Shutdown | `50-cosmos-greeter-power.rules` | unchanged |

Nothing else needs root at runtime. The image is immutable, so most "admin" is one of: change packages,
upgrade the OS, manage a few services, mount disks.

## 3. Architecture

```
 desktop apps / cosmos-task / cosmos-priv shims          (uid user, no root path)
        |  unix socket /run/kronos/kronos.sock  (0660 root:kronos-clients, SO_PEERCRED checked)
        v
   kronosd  (uid kronos, no shell, no home except /var/lib/kronos)
        |  validate -> decide (automatic / passphrase / refuse) -> audit-log -> execute
        |  exec: sudo -n /usr/libexec/kronos/act <action>  (sudoers: kronos may run ONLY that helper)
        v
   /usr/libexec/kronos/act  (root, tiny, argument-validating)  ->  rpm-ostree / flatpak / systemctl / udisksctl ...
```

- **Accounts.** `kronos` (system uid < 1000, `/usr/sbin/nologin`, locked password, group `kronos`).
  `user` joins the group `kronos-clients` (only to reach the socket). `user` leaves `wheel`; the
  `%wheel NOPASSWD` sudoers file is deleted; `/etc/sudoers.d/kronos` allows exactly
  `kronos ALL=(root) NOPASSWD: /usr/libexec/kronos/act *` and nothing else.
- **Why not run kronosd as root?** A bug in the request parser, the prompt code or the audit writer would
  then be root. As `kronos` it can only do what the one helper allows, and the helper is small enough to
  read in one sitting (validation + a table of argv templates).
- **Why not sudoers rules per command?** Argument validation (which package, which unit, which path) doesn't
  fit in sudoers; it belongs in code that is tested.
- **Transport.** systemd socket activation (`kronos.socket` -> `kronos.service`); JSON lines. The peer's
  uid/pid come from `SO_PEERCRED`; the daemon reads `/proc/<pid>/exe` and `cmdline` for the audit record.
  Only uids listed in `/etc/kronos/clients` are served (today: `user`).
- **Client.** `kronos ACTION ARGS...` (and a Python module `cosmos_kronos.py`). Streams stdout/stderr and
  the exit status back so a `cosmos-task` window still shows live output, and stdin is forwarded for the
  few interactive cases.
- **Hardening of kronosd's unit:** `NoNewPrivileges=no` is unavoidable (it calls sudo), so the unit instead
  uses `ProtectHome=yes`, `PrivateTmp=yes`, `ProtectKernelModules`, `RestrictAddressFamilies=AF_UNIX`, a
  resource limit and a request size cap. The *helper* is what runs outside those limits, per request.
  (Phase 3 option: replace sudo with one systemd oneshot unit per action, started through polkit, each with
  its own `ProtectSystem`/`ReadWritePaths`; better isolation, more moving parts.) **Correction to the paragraph above:** the helper is a
  *child* of kronosd's unit, so it inherits that unit's sandbox - no network for `flatpak`, no `/home`, no modules for `modprobe`. The shipped
  unit therefore sets only `PrivateTmp` and restart limits, not the hardening list; the per-action transient unit is the way to get more.

## 4. Actions (v1 catalogue)

Every action has: a schema for its arguments, a tier (section 5; **P** = needs the admin passphrase), and an argv template the helper builds -
never a shell string. Unknown action or a failed schema check = refused and logged.

| Action | Arguments | Tier | Runs |
|---|---|---|---|
| `pkg.install` | package names (checked against `cosmos_app_policy` and a name regex) | 0 auto | `rpm-ostree install --idempotent [--apply-live]` |
| `pkg.remove` | names | 0 | `rpm-ostree uninstall` |
| `pkg.upgrade` | - | 0 auto | `rpm-ostree upgrade` |
| `os.upgrade` / `os.rollback` / `os.switch REF` | ref must match `registry/name:tag` | 0 / **P** / **P** | `bootc upgrade|rollback|switch` |
| `flatpak.install` / `.update` / `.remove` | app id (policy + scope checks), remote | 0 / 0 / 0 | `flatpak ... --system` |
| `service.control` | unit from an allowlist, verb start/stop/restart/enable/disable | 0 (allowlisted units), else **P** | `systemctl` |
| `firewall.door` | door name (`ssh`, `webtop`) and `open` true/false; nothing else | close: 0 auto / open: **P** | `firewall-cmd` (running + permanent, zone `cosmos`) |
| `file.read` / `file.write` | path from an allowlist (`/etc/hosts`, `/etc/cosmos/*` ...), size cap | 0 read / **P** write | helper opens with `O_NOFOLLOW` |
| `log.read` | journal unit / boot | 0 | `journalctl` |
| `run` | argv (no shell), cwd, optional stdin | **P** | the generic escape hatch, see below |

`run` exists for the few things that are not (yet) typed actions - a GameBox install script, an odd system command -
and because it is the most powerful action it always needs the admin passphrase. Nothing routine uses it; over
time each thing that does gets its own typed action and `run` shrinks. With no terminal on the system, the only
callers are programs, never a person typing.

## 5. The admin passphrase, and what is automatic

**What it is, plainly.** A short passphrase you choose. It is **not** a login, it is **never asked at boot, at
sign-in, or for everyday things** (installing an app from the Shop, updating, mounting a USB stick, the profile
picker - none of those ever ask). It exists for the handful of actions that could damage or take over the machine,
so that a program can't do them just by asking. You are asked **once when you first do such a thing** (that is also
when you create it: "Choose an admin passphrase"), and then it is remembered for the rest of that piece of work.

Two tiers, decided by kronosd from the action, never by the client:

- **Automatic (no prompt, always logged).** Everything routine: `pkg.*` (the Shop is the vetted path, and the policy
  checks - one packaging per app, no terminals - run first), `flatpak.*`, `os.upgrade`, allowlisted
  `service.control`, `log.read`, allowlisted `file.read`.
- **Passphrase (**P** in the table).** Rare and powerful: `run`, `os.switch` (moving to another image),
  `os.rollback`, `file.write`, non-allowlisted `service.control`, editing the policy, changing the passphrase.
  the passphrase window says what is being asked, by which program (as the kernel reports it), and takes the passphrase. **The daemon asks,
  not the program that asked:** `kronos-agent` runs in the desktop session, keeps a connection to the daemon, and the daemon sends it the
  question; the passphrase goes back to the daemon only. A program that could be anything therefore never sees the passphrase, can't
  put its own words in the window, and can't swap in a fake prompt. Over SSH (no agent) the client asks in the terminal. No agent on the
  desktop = refused, not asked some weaker way.
- **Refused.** Anything that fails its schema, isn't an action, or trips the policy (a terminal, a duplicate app, a
  system-disk mount). Logged with the reason. A passphrase can't unlock these.

**Admin session.** A correct passphrase opens a session for the asking *process* (its pid and start time from the kernel - not its name, which
a program can rewrite), 15 minutes, sliding while it keeps working, held in the daemon's memory only (a restart or `kronos lock` ends it). No
other process is unlocked by it, and a `kronos exec` on the command line asks each time it is started. So a task window that needs several admin steps asks once.

**Safeguards** (built and tested in `config/kronos/kronos_passphrase.py`): stored only as a salted PBKDF2-SHA256
hash in a file only `kronos` can read; at least 6 characters, not trivially guessable; five wrong tries in a row
lock it for 10 minutes (longer each time it happens again) and the lock survives a restart; a damaged file is never
treated as "not set" (so it can't be used to slip in a new one) until the recovery app resets it; changing it needs
the old one.

**Forgotten it?** The passwordless recovery app (section 7) can reset it; the next passphrase-gated action then asks
for a new one. That is deliberate: it matches "no root password" (someone with the machine in their hands controls
the disk anyway), and it means a forgotten passphrase is a 30-second inconvenience, not a reinstall.

**Policy file:** `/etc/kronos/policy.json` (default in `/usr/share/kronos/policy.json`): service and file
allowlists, which actions are **P**. Editing it needs the passphrase.

Honest limit: a program that can watch you type could learn the passphrase - Wayland stops that for ordinary
apps, and nothing else on this system reads other apps' keys. And a program that can inject input can't answer the
prompt without knowing the passphrase, which is the whole point of not using a plain Approve button.

## 6. Audit log

- Every request, whatever the outcome, is one JSON line in `/var/log/kronos/audit.log`, and also sent to the
  journal with `SYSLOG_IDENTIFIER=kronos`:
  `{"ts", "seq", "id", "peer": {"uid","pid","exe","cmdline"}, "action", "args" (secrets redacted),
  "tier", "decision": "auto|passphrase-ok|passphrase-denied|refused", "approver", "exit", "ms", "prev": "<sha256 of the previous line>"}`.
- The `prev` hash chains the lines, so a deleted or edited line is detectable (`kronos audit verify`).
- The file is owned by `kronos`, mode 0640, `chattr +a` set by the helper at creation (append-only even for
  the daemon); rotation is done by the helper at a size cap, keeping the chain across files (the first line of a
  new file carries the last hash of the old one).
- The **write must not be optional:** if the log can't be written the request is refused (fail closed) - an
  unlogged admin action is worse than a failed one.
- "Admin activity" viewer (Settings): reads the log through `log.read`-style access for `user` (group read
  ACL), newest first, filters by outcome; a denied/refused entry shows why.
- Retention: 30 days or 50 MB by default, whichever is larger, configurable in the policy file.

## 7. Bootstrap, recovery, upgrades

- **Image build.** `useradd -r -s /usr/sbin/nologin kronos`; `groupadd kronos-clients`; the desktop user is
  created *without* `wheel`; install the helper (root:root 0755, in `/usr/libexec/kronos`), `sudoers.d/kronos`,
  the socket/service units, `/etc/kronos/clients`. `/var/lib/kronos` and `/var/log/kronos` come from a
  tmpfiles.d entry (bootc's `/var` is only seeded on first boot).
- **First boot.** Nothing new to enter. The passphrase is created lazily, the first time a passphrase-gated action is
  requested, so the first-boot flow stays exactly as immersive as today.
- **Locked root, passwordless recovery (decision 1).** `root` stays locked and there is no root password to
  remember. Recovery is a boot-menu entry, "CosmOS recovery" (`cosmos.recovery=1`), that starts a small
  controller-friendly **recovery app - not a shell**: roll back to the previous OS deployment, restart or reset
  kronos (policy, socket, audit log kept), clear a profile PIN, or factory-reset user data. Reaching it needs no
  password (physical access to the machine already means control of the disk), and it keeps the no-terminal
  rule even when things are broken.
- **Upgrades.** The daemon, helper and default policy ship in the image, so `bootc upgrade` updates them
  together; `/etc/kronos/policy.json` is a 3-way-merged `/etc` file like any other.
- **Fail closed.** kronosd down / socket missing -> clients print "admin service unavailable" and do nothing.
  The desktop, launcher, GameBox and normal apps never need it to start.

## 8. What this does not protect against

- Malware running as `user` that can **inject input** (this session gives `user` `/dev/uinput` for the
  controller layer) can click through a plain Approve button - which is why there isn't one. **The passphrase is the real gate**, because
  it cannot be clicked through; the automatic tier is guard rails and a log.
  If that matters, move more actions to **P** (cost: more passphrase entries).
- A fully compromised session: same-account malware that installs its own user units or rewrites the account's files can impersonate any
  user-level program (including a look-alike prompt agent). The passphrase, the kernel-reported asker in the window and the audit log make
  it visible and stop the casual cases; they are not a vault.
- A compromised kernel, a physical-access attacker with the disk, or a flaw in the helper's validation.
- Anything that never needed root anyway: the browser, games and Flatpaks still see everything `user` can.
  The Flatpak work (sandboxes + the scoped file portal) is what narrows that side; kronos narrows the root side.

## 9. Decisions

Settled:

1. **Break-glass: passwordless.** No root password; the recovery app above (rollback / reset, no shell).
2. **There IS an admin passphrase** (first answer was a misunderstanding - it is not asked at boot or for everyday
   things; section 5 explains exactly when). Created lazily on first need; `run` returns as a passphrase-gated action.
3. **Package changes run automatically**, because the Shop replaces the terminal and the policy checks run first.
   Plus: **no terminal can be installed** in any form - see section 11.

Still open:

4. **`/dev/uinput` and the `input` group (DONE, in part).** The real hole was the `input` group: it let any program of the desktop account
   read every keystroke, the admin passphrase included. The account is no longer in it. Game controllers are covered by logind's uaccess (plus
   `72-cosmos-controller-input.rules` for the motion sensors), the controller LED files and DualSense hidraw nodes by the harmless
   `cosmos-pads` group, and passing a host keyboard/mouse to a virtual machine is a kronos action (`input.grant`, admin passphrase, on that
   one device, taken back when the VM stops). Two more holes of the same kind are closed: `kernel.yama.ptrace_scope=1` (a program can't read
   another's memory) and the prompt itself (below). **`/dev/uinput` stays open** to the session on purpose: restricting who may inject input
   can't be enforced between programs of one account (every program can rewrite its own command line, so "this is the OSK" is not something a
   broker could verify), it would put the controller layer - the way into the OS with no keyboard - behind a new service that has never been
   booted, and injected input can't reveal the passphrase. If that ever matters, the answer is running the input layer as another account.
5. **GameBox's `sudo` calls (DONE).** Update/rollback/steam/host-package installs ask kronos (`kronos exec --`, the typed `os.upgrade` /
   `os.rollback` / `pkg.install` actions where they fit) and fall back to `sudo` only where kronos isn't installed (a dev machine); restart and
   shut down need no admin at all (logind lets the active session do it). GameBox's own dev-machine scripts (`atomic/`, `packaging/deploy_gui`,
   `image/grow_userdata.sh`) still use sudo: they run on a build machine or on GameBox's own OS, not on CosmOS. `rpm-fusion` release RPMs by URL
   can't be a typed package action, so that one step is the passphrase-gated `run`.
6. **(Settled) IDEs with a terminal panel.** They are Expansion Packs (opt-in) and the Webtop is the development environment; SSH and the Webtop are deliberate exceptions to "no terminal" (section 11).
   Original question: **IDEs with a terminal panel.** The image has VS Code, Eclipse, Sublime and neovim (`:terminal`), and
   Flatpak/RPM apps like them. They are not terminal emulators, so the checks don't touch them, but each gives a
   shell. "Zero way" is only true if they go (or their terminal is disabled where an app allows it).

## 10. Build plan

1. **Core (headless-testable) - DONE:** action schemas, validation, policy, the argv builders, audit writer with the
   hash chain - unit tests only, no root needed (fake helper), like `test_cosmos_priv.sh`.
2. **Daemon + client (DONE):** socket activation, `SO_PEERCRED`, streaming, `cosmos_kronos.py`, `kronos`; integration test (`test_kronosd.py`) with the real daemon, helper and client and fake tools on PATH. `cosmos-priv`, the Shop, Updates, packs, Remote Access, the firewall doors and `cosmos_pads` are switched to the client.
3. **Prompt + viewer + recovery app (DONE, except the boot entry):** `kronos-agent` (the daemon-driven prompt; `kronos-prompt` remains for the Admin activity app and `kronos passphrase`), `cosmos-admin` (Admin activity), `cosmos-recovery` + `cosmos_recovery.py` (tested), `cosmos-recovery.service` (starts only with `cosmos.recovery=1`). Still to do by hand: a GRUB entry that adds `cosmos.recovery=1` (until then: at the GRUB menu press `e` and append it to the `linux` line). The prompt has no on-screen keyboard yet.
4. **Image cut-over:** remove `wheel` from the desktop user and delete the wheel sudoers; new units, tmpfiles,
   polkit rules trimmed (the udisks rule no longer mentions `wheel` (done)). The one step that needs a rebuild to prove.
5. **Convert leftovers** off `run` into typed actions (GameBox scripts first).

Phases 1-3 can be built and tested here without touching the image; phase 4 is yours to rebuild.

## 11. No terminal, in every packaging (built)

"Zero way" cannot be literally true of a general-purpose computer, so the aim is: **no terminal emulator or
remote-shell client can be installed through any path CosmOS controls, and one that sneaks in is found and removed.**
Layers, all fed by `config/cosmos-apps/app-policy.json` (`blocked`) and `find-terminals`:

| Layer | What it stops |
|---|---|
| dnf `excludepkgs` in the image | the RPM of any listed terminal, by dnf or rpm-ostree |
| Flathub remote filter (`deny ...`, exact ids plus `*Terminal*`, `*.Konsole`, `*.Console`) | the Flatpak of those, by `flatpak install` or the Shop (tested against real Flathub) |
| Shop, `cosmos-priv`, kronos `pkg.*`/`flatpak.*` | the same list, with a plain explanation, before anything is downloaded |
| `find-terminals --purge-flatpaks` after every Flatpak install (Shop and `cosmos-priv`) | a terminal whose id nobody listed: found by its desktop category `TerminalEmulator` and uninstalled |
| build gate: `find-terminals` in the Containerfile | fails the image build if any terminal ended up in the image (dependency, base package, new entry) |

**Deliberate exceptions (your decision):** SSH access is on by default with keys only, and gives a normal shell as `user` from another computer - that
terminal is on *your* computer, not this one, so it isn't a terminal emulator on CosmOS. Over SSH, admin work goes through the `kronos` client (the
passphrase-gated `run`, and the typed actions), never a root shell; `sshd` itself is on kronos's automatic service allowlist. The **Webtop** contains
a terminal and IDEs, but inside a rootless container isolated from the system. `claude` (Claude Code) stays in the base image, for use over SSH or in the Webtop.

Not covered, said plainly: an AppImage installed under an unlisted name (AM has no category to check before
installing), a program that embeds a terminal (section 9, item 6), and anything a person builds by hand.
`find-terminals --root <image>` re-checks any image.
