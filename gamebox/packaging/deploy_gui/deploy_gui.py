#!/usr/bin/env python3
"""CosmOS Deploy - a small GUI wrapping the stage/test/install/rollback/
push-live cycle this project's own dev-machine install model needs (see
packaging/cosmos_install.py and packaging/stage_build.sh), plus the
real bootc/Atomic image pipeline (atomic/), so none of it has to be
driven by hand from a terminal each time.

CosmOS is no longer packaged as an AppImage at all, per direct
request - there's no separate build artifact to bundle GTK3/PyGObject/
pip deps into any more; every real dependency installs straight onto
whatever Python is running CosmOS (packaging/host_deps.py, or
atomic/Containerfile's own dnf/pip RUN step for the bootc image).

Tkinter, not GTK3, deliberately: this is a standalone maintenance tool for
whoever's building/deploying CosmOS, not part of CosmOS's own
gamepad-first "Nebula Engine" UI - it has no reason to share that stack,
and Tkinter (stdlib, no PyGObject/GObject-Introspection dependency) is
simpler to package as its own separate AppImage (see packaging/
build_deploy_gui_appimage.sh - this tool's own packaging is a separate,
unrelated concern from CosmOS's, left as-is).

"Clone Repos..." (see on_clone_repos()) is the one button here that
doesn't assume a real git checkout already exists - every other button
operates on REPO_ROOT, which packaging/stage_build.sh's own guard
explicitly refuses to treat as a real source of truth when it's actually
the AppImage's own bundled, .git-less copy (see that script's own error
message). Cloning any of the account's real repos - not just this one -
straight from inside the AppImage is what actually makes a single
downloaded file self-sufficient, per direct request."""
import json
import os
import queue
import subprocess
import sys
import tempfile
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

GITHUB_OWNER = "theskysanctum"

sys.path.insert(0, str(REPO_ROOT / "packaging"))
import cosmos_install  # noqa: E402
import host_deps  # noqa: E402
import steam_native  # noqa: E402

sys.path.insert(0, str(REPO_ROOT / "emulators"))
import assets  # noqa: E402
import cores  # noqa: E402
import engines  # noqa: E402

sys.path.insert(0, str(REPO_ROOT / "webshell"))
import electron_runtime  # noqa: E402

STAGE_SCRIPT = REPO_ROOT / "packaging" / "stage_build.sh"
STAGED_BUILD_DIR = REPO_ROOT / "build" / "deploy-stage"

# The Fedora Atomic/bootc pipeline this project runs on (see atomic/
# Containerfile) - a separate concern from the app-layer stage/install/
# rollback buttons above, but the same GUI: this tool's whole job is
# "wrap the stage/test/install cycle so it isn't driven by hand from a
# terminal", and that includes the OS image itself, not just the app
# tree. The older Debian rootfs/live-ISO/USB-image pipeline this one
# superseded has been retired outright (per direct request, along with
# the AppImage it depended on) rather than kept side by side.
BOOTC_BUILD_SCRIPT = REPO_ROOT / "atomic" / "build_bootc_image.sh"
BOOTC_BOOT_TEST_SCRIPT = REPO_ROOT / "atomic" / "test_bootc_boot.sh"
BOOTC_UPGRADE_TEST_SCRIPT = REPO_ROOT / "atomic" / "test_bootc_upgrade_rollback.sh"
BOOTC_VIEW_LIVE_SCRIPT = REPO_ROOT / "atomic" / "view_live_qemu.sh"
PRUNE_BUILD_CACHE_SCRIPT = REPO_ROOT / "atomic" / "prune_build_cache.sh"
BOOTC_PUSH_SCRIPT = REPO_ROOT / "atomic" / "push_bootc_image.sh"


class DeployGUI(tk.Tk):
	def __init__(self):
		super().__init__()
		self.title("CosmOS Deploy")
		self.geometry("900x600")
		self.log_queue = queue.Queue()

		buttons = tk.Frame(self)
		buttons.pack(fill=tk.X, padx=10, pady=10)

		self.clone_btn = tk.Button(buttons, text="Clone Repos...", command=self.on_clone_repos)
		self.clone_btn.pack(side=tk.LEFT, padx=4)

		self.deps_btn = tk.Button(buttons, text="Check / Install Dependencies", command=self.on_check_deps)
		self.deps_btn.pack(side=tk.LEFT, padx=4)

		self.build_btn = tk.Button(buttons, text="Stage Build", command=self.on_build)
		self.build_btn.pack(side=tk.LEFT, padx=4)

		self.test_btn = tk.Button(buttons, text="Test in Scratch Session", command=self.on_test)
		self.test_btn.pack(side=tk.LEFT, padx=4)

		self.install_btn = tk.Button(buttons, text="Install / Update", command=self.on_install)
		self.install_btn.pack(side=tk.LEFT, padx=4)

		self.rollback_btn = tk.Button(buttons, text="Rollback", command=self.on_rollback)
		self.rollback_btn.pack(side=tk.LEFT, padx=4)

		self.push_btn = tk.Button(buttons, text="Kill tty2", command=self.on_push_live, fg="#b00020")
		self.push_btn.pack(side=tk.LEFT, padx=4)

		# A second row for the Fedora Atomic/bootc pipeline - the real OS
		# image this project ships (see atomic/Containerfile), a separate
		# concern from the app-layer stage/install/rollback buttons above
		# (a bootable OS image vs. this machine's own already-running
		# CosmOS).
		bootc_buttons = tk.Frame(self)
		bootc_buttons.pack(fill=tk.X, padx=10)

		tk.Label(bootc_buttons, text="Bootc image:", anchor="w").pack(side=tk.LEFT, padx=(0, 8))

		self.bootc_build_btn = tk.Button(bootc_buttons, text="Build Bootc Image", command=self.on_build_bootc_image)
		self.bootc_build_btn.pack(side=tk.LEFT, padx=4)

		self.bootc_push_btn = tk.Button(bootc_buttons, text="Push to GHCR", command=self.on_push_bootc_ghcr)
		self.bootc_push_btn.pack(side=tk.LEFT, padx=4)

		self.bootc_boot_test_btn = tk.Button(
			bootc_buttons, text="Test Install & Boot (local image)", command=self.on_test_bootc_boot
		)
		self.bootc_boot_test_btn.pack(side=tk.LEFT, padx=4)

		self.bootc_upgrade_test_btn = tk.Button(
			bootc_buttons, text="Test Upgrade/Rollback (GHCR)", command=self.on_test_bootc_upgrade_rollback
		)
		self.bootc_upgrade_test_btn.pack(side=tk.LEFT, padx=4)

		self.bootc_view_live_btn = tk.Button(
			bootc_buttons, text="View Live (qemu window)", command=self.on_view_live_qemu
		)
		self.bootc_view_live_btn.pack(side=tk.LEFT, padx=4)

		self.prune_btn = tk.Button(bootc_buttons, text="Prune Build Cache", command=self.on_prune_build_cache)
		self.prune_btn.pack(side=tk.LEFT, padx=4)

		self.status_label = tk.Label(self, text="", anchor="w", justify="left")
		self.status_label.pack(fill=tk.X, padx=10)

		self.log = scrolledtext.ScrolledText(self, state="disabled", bg="black", fg="#7CFC7C", font=("monospace", 10))
		self.log.pack(fill=tk.BOTH, expand=True, padx=10, pady=(4, 10))

		self.refresh_status()
		self.after(100, self._poll_log_queue)

	# -- logging / status --------------------------------------------------

	def log_line(self, text):
		self.log_queue.put(text)

	def _poll_log_queue(self):
		while True:
			try:
				line = self.log_queue.get_nowait()
			except queue.Empty:
				break
			self.log.configure(state="normal")
			self.log.insert(tk.END, line + "\n")
			self.log.see(tk.END)
			self.log.configure(state="disabled")
		self.after(100, self._poll_log_queue)

	def refresh_status(self):
		bits = []
		if cosmos_install.APP_ROOT.exists():
			bits.append(f"Installed: {cosmos_install.APP_ROOT}")
		else:
			bits.append("Not installed yet")
		if cosmos_install.BACKUP_ROOT.exists():
			bits.append("backup available")
		if STAGED_BUILD_DIR.exists():
			bits.append(f"staged: {STAGED_BUILD_DIR}")
		else:
			bits.append("no build staged yet")
		self.status_label.configure(text="  |  ".join(bits))
		self.rollback_btn.configure(state=tk.NORMAL if cosmos_install.BACKUP_ROOT.exists() else tk.DISABLED)

	# -- background work -----------------------------------------------------

	def _set_buttons_enabled(self, enabled):
		state = tk.NORMAL if enabled else tk.DISABLED
		for b in (
			self.clone_btn,
			self.deps_btn,
			self.build_btn,
			self.test_btn,
			self.install_btn,
			self.push_btn,
			self.bootc_build_btn,
			self.bootc_push_btn,
			self.bootc_boot_test_btn,
			self.bootc_upgrade_test_btn,
			self.bootc_view_live_btn,
			self.prune_btn,
		):
			b.configure(state=state)
		if enabled:
			self.rollback_btn.configure(state=tk.NORMAL if cosmos_install.BACKUP_ROOT.exists() else tk.DISABLED)
		else:
			self.rollback_btn.configure(state=tk.DISABLED)

	def run_in_background(self, work):
		self._set_buttons_enabled(False)

		def target():
			try:
				work()
			except Exception as e:
				self.log_line(f"ERROR: {e}")
			finally:
				self.after(0, lambda: (self._set_buttons_enabled(True), self.refresh_status()))

		threading.Thread(target=target, daemon=True).start()

	def _run_streamed(self, cmd, **kwargs):
		"""Runs cmd, streaming stdout+stderr into the log line by line as it
		happens (rather than waiting for it to finish and dumping it all at
		once) - the bootc image build in particular takes long enough that
		seeing it progress matters. Returns the process's exit code."""
		proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1, **kwargs)
		for line in proc.stdout:
			self.log_line(line.rstrip())
		proc.wait()
		return proc.returncode

	def _gh_env(self):
		"""Environment for every `gh` call and the bootc push: the process's own,
		with GH_TOKEN filled from the manual `gh auth login` session (`gh auth
		token`) when it isn't already set, because atomic/push_bootc_image.sh
		needs GH_TOKEN to log in to ghcr.io. No token is embedded anywhere -
		if nobody has signed in, GH_TOKEN stays unset and the script's own
		guard fails with a clear message."""
		env = dict(os.environ)
		if not env.get("GH_TOKEN"):
			try:
				done = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, timeout=10)
				if done.returncode == 0 and done.stdout.strip():
					env["GH_TOKEN"] = done.stdout.strip()
			except (OSError, subprocess.SubprocessError):
				pass
		return env

	# -- actions ---------------------------------------------------------

	def on_clone_repos(self):
		"""Lists every repo on the GITHUB_OWNER account (gh repo list) and
		lets you pick any number of them to clone locally, each into its
		own <dest>/<repo-name> - not just CosmOS itself. Uses `gh repo
		clone` rather than a bare `git clone` URL specifically so private
		repos (this one included) clone with zero extra auth setup, via
		whatever account the machine's own `gh` is already logged into.

		The real reason this exists on the FIRST button, before even
		Check/Install Dependencies: every other button here operates
		against REPO_ROOT, and packaging/stage_build.sh explicitly refuses
		to run against it when REPO_ROOT is the AppImage's own bundled,
		.git-less copy of the source tree (see that script's own error
		message) - so a plain "download the AppImage and go" flow only
		gets you as far as this button without a real `git clone`
		somewhere first. Once you're cloning one repo by hand there's no
		reason this tool couldn't clone any/all of the account's repos
		instead of just this one, per direct request."""
		def fetch():
			self.log_line(f"== Fetching your repo list (gh repo list {GITHUB_OWNER}) ==")
			result = subprocess.run(
				["gh", "repo", "list", GITHUB_OWNER, "--limit", "300", "--json", "name,description,isPrivate,updatedAt"],
				capture_output=True, text=True, env=self._gh_env(),
			)
			if result.returncode != 0:
				self.log_line(f"FAILED to list repos (exit {result.returncode}): {result.stderr.strip()}")
				return
			try:
				repos = json.loads(result.stdout)
			except json.JSONDecodeError as e:
				self.log_line(f"FAILED to parse repo list: {e}")
				return
			repos.sort(key=lambda r: r["name"].lower())
			self.log_line(f"Found {len(repos)} repos.")
			self.after(0, lambda: self._show_clone_picker(repos))

		self.run_in_background(fetch)

	def _show_clone_picker(self, repos):
		"""Runs on the main thread (see on_clone_repos's own self.after
		hop) - Tk widgets can't be built off it. A plain Listbox with
		selectmode=EXTENDED rather than a checklist - picking several
		repos with shift/ctrl-click is the same gesture anyone already
		knows from a real file manager, no custom widget needed."""
		dialog = tk.Toplevel(self)
		dialog.title("Clone Repos")
		dialog.geometry("520x520")

		tk.Label(dialog, text="Select one or more repos to clone (ctrl/shift-click for more than one):", anchor="w").pack(
			fill=tk.X, padx=10, pady=(10, 4)
		)

		list_frame = tk.Frame(dialog)
		list_frame.pack(fill=tk.BOTH, expand=True, padx=10)
		scrollbar = tk.Scrollbar(list_frame)
		scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
		listbox = tk.Listbox(list_frame, selectmode=tk.EXTENDED, yscrollcommand=scrollbar.set)
		listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
		scrollbar.configure(command=listbox.yview)

		for repo in repos:
			label = repo["name"]
			if repo.get("isPrivate"):
				label += "  (private)"
			if repo.get("description"):
				label += f"  - {repo['description']}"
			listbox.insert(tk.END, label)

		dest_frame = tk.Frame(dialog)
		dest_frame.pack(fill=tk.X, padx=10, pady=(8, 4))
		tk.Label(dest_frame, text="Clone into:", anchor="w").pack(side=tk.LEFT)
		# Defaults next to REPO_ROOT's own parent when this is already a
		# real checkout (the common "clone a sibling repo alongside this
		# one" case) - the AppImage's own bundled copy has no .git, so
		# that case instead falls back to $HOME.
		default_dest = str(REPO_ROOT.parent) if (REPO_ROOT / ".git").exists() else str(Path.home())
		dest_var = tk.StringVar(value=default_dest)
		dest_entry = tk.Entry(dest_frame, textvariable=dest_var)
		dest_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=4)

		def browse():
			chosen = filedialog.askdirectory(initialdir=dest_var.get() or str(Path.home()), parent=dialog)
			if chosen:
				dest_var.set(chosen)

		tk.Button(dest_frame, text="Browse...", command=browse).pack(side=tk.LEFT)

		btn_frame = tk.Frame(dialog)
		btn_frame.pack(fill=tk.X, padx=10, pady=(4, 10))

		def do_clone():
			selected = [repos[i]["name"] for i in listbox.curselection()]
			if not selected:
				messagebox.showwarning("No repos selected", "Select at least one repo first.", parent=dialog)
				return
			dest = dest_var.get().strip()
			if not dest:
				messagebox.showwarning("No destination", "Pick a destination directory first.", parent=dialog)
				return
			dialog.destroy()
			self._clone_repos(selected, dest)

		tk.Button(btn_frame, text="Clone Selected", command=do_clone).pack(side=tk.LEFT, padx=4)
		tk.Button(btn_frame, text="Cancel", command=dialog.destroy).pack(side=tk.LEFT, padx=4)

	def _clone_repos(self, names, dest_dir):
		def work():
			dest = Path(dest_dir)
			dest.mkdir(parents=True, exist_ok=True)
			for name in names:
				target = dest / name
				if target.exists():
					self.log_line(f"== Skipping {name} - {target} already exists ==")
					continue
				self.log_line(f"== Cloning {GITHUB_OWNER}/{name} into {target} ==")
				code = self._run_streamed(["gh", "repo", "clone", f"{GITHUB_OWNER}/{name}", str(target)], env=self._gh_env())
				self.log_line(f"== {name}: {'cloned' if code == 0 else f'FAILED (exit {code})'} ==")
			self.log_line("== Clone batch done ==")

		self.run_in_background(work)

	def on_check_deps(self):
		"""Host runtime/build-time packages (dnf/apt/pip) stay a single
		all-or-nothing yes/no - there's no real reason anyone would want
		only some of those. There's no Flatpak checklist any more -
		Brave (this project's one and only Flatpak dependency, via the
		since-deleted packaging/flatpak_apps.py) is gone, replaced by
		webshell/ (see electron_runtime.fetch_electron() below, the same
		unconditional "just fetch it, it's load-bearing" treatment
		cores/assets/engines already get, not a checklist choice)."""
		def work():
			pm = host_deps.PACKAGE_MANAGER
			self.log_line(f"== Checking host dependencies (package manager: {pm or 'none detected'}) ==")
			missing_dnf, missing_pip, missing_libs = host_deps.check()
			missing_build = host_deps.check_build_deps()
			missing_bin = missing_dnf + missing_pip + missing_libs

			if pm is None and (missing_bin or missing_build):
				self.log_line(
					"No supported package manager (dnf or apt) found - can't install "
					"automatically. Missing: " + ", ".join(missing_bin + missing_build)
				)
			elif not missing_bin and not missing_build:
				self.log_line("Every host runtime/build-time dependency is already installed.")
			else:
				if missing_bin:
					self.log_line(f"Missing runtime dependencies: {', '.join(missing_bin)}")
				if missing_build:
					self.log_line(f"Missing build-time devel packages: {', '.join(missing_build)}")
				if self._ask_yes_no(
					"Install missing dependencies",
					"Install everything listed above now? This runs real sudo dnf/apt install / pip install commands.",
				):
					ok = True
					if missing_bin:
						ok = host_deps.install(missing_bin, log_fn=self.log_line) and ok
					if missing_build:
						ok = host_deps.install_build_deps(log_fn=self.log_line) and ok
					self.log_line("Host dependency install succeeded." if ok else "Host dependency install FAILED - see above.")
				else:
					self.log_line("Skipped host dependencies - nothing installed.")

			# Unconditional, same "just fetch/install it, it's load-bearing"
			# treatment as cores/assets/engines/Electron below - CosmOS
			# is no longer an AppImage, so this is the one thing that makes
			# a bare checkout actually runnable (PyGObject/pycairo included,
			# built from source against the host devel headers just
			# installed above if the user said yes).
			self.log_line("Installing this project's own Python dependencies (packaging/requirements.txt)...")
			pydeps_ok = host_deps.install_python_deps(log_fn=self.log_line)
			self.log_line("Python dependencies installed." if pydeps_ok else "FAILED to install Python dependencies - see above.")

			self.log_line("Fetching every RetroArch core (this is a real, one-time multi-hundred-MB download)...")
			failed_cores = cores.fetch_all_cores(log_fn=self.log_line)
			self.log_line("All cores fetched." if not failed_cores else f"FAILED to fetch: {', '.join(failed_cores)}")

			self.log_line("Fetching RetroArch's own assets (autoconfig/cheats/database/overlays/shaders/info)...")
			failed_assets = assets.fetch_all_assets(log_fn=self.log_line)
			self.log_line("All assets fetched." if not failed_assets else f"FAILED to fetch: {', '.join(failed_assets)}")

			self.log_line("Fetching indie game-engine runtimes (NW.js/LÖVE/Ren'Py/TIC-80/AGS)...")
			failed_engines = engines.fetch_all_engines(log_fn=self.log_line)
			self.log_line("All engines fetched." if not failed_engines else f"FAILED to fetch: {', '.join(failed_engines)}")

			# Also unrelated to the Flatpak checklist below - a required,
			# native (RPM Fusion) dependency now, not a Flatpak. See
			# packaging/steam_native.py's own docstring for why Steam
			# moved off Flatpak specifically (Millennium can't theme a
			# sandboxed Steam at all).
			self.log_line("Checking Steam (native RPM Fusion package)...")
			steam_ok = steam_native.install(log_fn=self.log_line)
			self.log_line("Steam install succeeded." if steam_ok else "Steam install FAILED - see above.")

			self.log_line("Fetching the Electron runtime (this is a real, one-time ~280MB download)...")
			electron_ok = electron_runtime.fetch_electron(log_fn=self.log_line)
			self.log_line("Electron fetched." if electron_ok else "FAILED to fetch Electron - see above.")

		self.run_in_background(work)

	def _ask_yes_no(self, title, message):
		"""messagebox must run on the main thread - callers run on a
		background one (see run_in_background), so this hops back via a
		one-shot Event/holder rather than blocking a background thread on
		a Tk call directly."""
		answer = {}
		done = threading.Event()

		def ask():
			answer["yes"] = messagebox.askyesno(title, message)
			done.set()

		self.after(0, ask)
		done.wait()
		return answer.get("yes", False)

	def on_build(self):
		def work():
			self.log_line("== Staging a fresh CosmOS build (packaging/stage_build.sh) ==")
			code = self._run_streamed(["bash", str(STAGE_SCRIPT), str(STAGED_BUILD_DIR)])
			self.log_line(f"== Stage {'succeeded' if code == 0 else 'FAILED'} (exit {code}) ==")

		self.run_in_background(work)

	def on_build_bootc_image(self):
		def work():
			self.log_line("== Building the bootc/Atomic image (atomic/build_bootc_image.sh) - this takes a while ==")
			code = self._run_streamed(["bash", str(BOOTC_BUILD_SCRIPT)])
			self.log_line(f"== Bootc image build {'succeeded' if code == 0 else 'FAILED'} (exit {code}) ==")

		self.run_in_background(work)

	def on_push_bootc_ghcr(self):
		"""Publishes the local bootc image to ghcr.io/theskysanctum/cosmos
		(see atomic/push_bootc_image.sh - the same registry/tag convention
		.github/workflows/build-image.yml uses) - previously CI-only, since
		that workflow authenticates with its own ephemeral
		secrets.GITHUB_TOKEN and nothing local ever had an equivalent real
		credential. Now real here too, via the signed-in `gh` session's token passed
		in as GH_TOKEN (see this class's own _gh_env() docstring) - a real, direct request to make this tool
		self-sufficient rather than needing to push through GitHub Actions
		for every image update."""
		if not messagebox.askyesno(
			"Push to GHCR",
			"Pushes the local bootc image to ghcr.io/theskysanctum/cosmos "
			"(:latest and today's date tag) - a real, public-to-this-account "
			"registry push. Continue?",
		):
			return

		def work():
			self.log_line("== Pushing the local bootc image to GHCR (atomic/push_bootc_image.sh) ==")
			code = self._run_streamed(["bash", str(BOOTC_PUSH_SCRIPT)], env=self._gh_env())
			self.log_line(f"== GHCR push {'succeeded' if code == 0 else 'FAILED'} (exit {code}) ==")

		self.run_in_background(work)

	def on_test_bootc_boot(self):
		"""Simpler, faster sibling of Test Upgrade/Rollback below: installs
		the LOCAL bootc image (no GHCR push needed) onto a scratch disk and
		confirms it boots to a real login prompt, then greps dmesg/
		journalctl over serial for the nvidia module's own load status and
		any other boot-time errors. The right check after a Containerfile
		change (new firmware, the nvidia driver build, native Steam, ...)
		when you just want to confirm it still boots, not run a full
		upgrade/rollback cycle against GHCR."""
		if not messagebox.askyesno(
			"Test Install & Boot",
			"Installs the local bootc image onto a scratch disk under build/ and boots "
			"it in qemu to confirm it comes up cleanly. Takes a few real minutes. Continue?",
		):
			return

		def work():
			self.log_line("== Testing install+boot of the local bootc image (this takes a few minutes) ==")
			code = self._run_streamed(["bash", str(BOOTC_BOOT_TEST_SCRIPT)])
			self.log_line(f"== Install+boot test {'PASSED' if code == 0 else 'FAILED'} (exit {code}) - see the log above ==")

		self.run_in_background(work)

	def on_test_bootc_upgrade_rollback(self):
		"""Real, automated end-to-end verification of the update ritual
		UI's own `bootc upgrade --apply`/`bootc rollback --apply` commands
		(see launcher/app.py) - installs a real image from GHCR onto a
		scratch disk, boots it in qemu, and confirms a real upgrade then
		rollback both actually change (and restore) the booted image
		digest. Takes real wall-clock time (each boot+reboot cycle is
		several real minutes) and needs network access to pull from GHCR -
		warned about explicitly here since every other button in this
		row finishes in seconds to a couple minutes."""
		if not messagebox.askyesno(
			"Test Upgrade/Rollback",
			"Installs a real image from ghcr.io/theskysanctum/cosmos onto a scratch "
			"disk under build/, boots it in qemu, and runs a real bootc upgrade then "
			"rollback end to end. Takes real wall-clock time (several minutes per "
			"reboot cycle) and needs network access. Continue?",
		):
			return

		def work():
			self.log_line("== Testing bootc upgrade/rollback against a real GHCR image (this takes a while) ==")
			code = self._run_streamed(["bash", str(BOOTC_UPGRADE_TEST_SCRIPT)])
			self.log_line(f"== Upgrade/rollback test {'PASSED' if code == 0 else 'FAILED'} (exit {code}) - see the log above ==")

		self.run_in_background(work)

	def on_view_live_qemu(self):
		"""Boots the already-installed bootc raw disk (built by Test
		Install & Boot, above) in a REAL, visible qemu window instead of
		headless - for actually watching CosmOS run live rather than
		just checking a serial-console pass/fail. Fire-and-forget: the
		script launches qemu in the background and returns right away, so
		this doesn't block the log thread waiting for a window close - the
		user closes the qemu window itself whenever they're done."""
		def work():
			self.log_line("== Launching a live qemu window for the installed bootc disk ==")
			code = self._run_streamed(["bash", str(BOOTC_VIEW_LIVE_SCRIPT)])
			if code != 0:
				self.log_line(f"== Failed to launch (exit {code}) - see the log above ==")

		self.run_in_background(work)

	def on_prune_build_cache(self):
		"""Every Build Bootc Image rebuild leaves the previous image's
		now-unreferenced layers behind as dangling podman images -
		confirmed live this adds up to tens of GB after a normal session
		of fix-rebuild iteration. Only removes genuinely dangling
		(<none>) images, never a currently-tagged one, so it's always safe
		to run."""
		def work():
			self.log_line("== Pruning dangling podman images from previous builds ==")
			code = self._run_streamed(["bash", str(PRUNE_BUILD_CACHE_SCRIPT)])
			self.log_line(f"== Prune {'done' if code == 0 else 'FAILED'} (exit {code}) ==")

		self.run_in_background(work)

	def on_test(self):
		if not STAGED_BUILD_DIR.exists():
			messagebox.showwarning("No build", "Stage a build first.")
			return

		def work():
			self.log_line("== Testing the staged build in an isolated scratch X session ==")
			_boot_test(STAGED_BUILD_DIR, self.log_line)

		self.run_in_background(work)

	def on_install(self):
		if not STAGED_BUILD_DIR.exists():
			messagebox.showwarning("No build", "Stage a build first.")
			return

		def work():
			if cosmos_install.APP_ROOT.exists():
				self.log_line("== Updating the installed build ==")
				ok = cosmos_install.update(str(STAGED_BUILD_DIR))
			else:
				self.log_line("== Installing for the first time ==")
				ok = cosmos_install.install(str(STAGED_BUILD_DIR))
			self.log_line("Done." if ok else "FAILED - see the debug log for details.")

		self.run_in_background(work)

	def on_rollback(self):
		if not messagebox.askyesno("Rollback", "Roll back to the previously installed build?"):
			return

		def work():
			self.log_line("== Rolling back ==")
			ok = cosmos_install.rollback()
			self.log_line("Rolled back." if ok else "FAILED - no backup to roll back to.")

		self.run_in_background(work)

	def on_push_live(self):
		"""Kills whatever real, currently-running session is bound to tty2
		(Xorg, consolewm, the launcher - everything under it), so a freshly
		installed build actually gets picked up on the next login there.

		Used to just be `sudo systemctl restart getty@tty2.service` - looked
		right (KillMode=control-group, so restarting a unit normally does
		take its whole cgroup down with it) but never actually killed
		anything real: confirmed live that a session's login process gets
		migrated OUT of getty@tty2.service's own cgroup and into its own
		systemd-logind session scope (session-N.scope, under user-*.slice)
		the moment PAM opens the session - `cat /proc/<login-pid>/cgroup`
		showed this directly, even for the PID `systemctl status
		getty@tty2.service` still lists as that unit's own MainPID. So
		"restart" was only ever killing an already-empty cgroup and
		spawning a second, redundant getty fighting the first still-live
		one over vt2 - the real, running Xorg/launcher never went away,
		matching the report that this button didn't actually kill it.

		The real fix has to go through systemd-logind directly, which
		tracks the session's real (migrated) scope regardless of how PAM
		reparented it: find whichever session is actually on tty2 (`loginctl
		show-session <id> -p TTY --value`, not parsed off `list-sessions`'
		own table columns - confirmed those differ across systemd versions,
		an id-then-query approach doesn't care), then `loginctl
		terminate-session` it. getty@tty2.service's own Restart=always
		(confirmed via `systemctl show`) means killing that session's leader
		is all it takes for a fresh getty to come back on its own - no
		separate restart call needed at all."""
		if not messagebox.askyesno(
			"Kill tty2",
			"Terminates the real, currently-running tty2 session (Xorg, "
			"CosmOS, everything under it), replacing whatever's currently "
			"on screen there. Continue?",
		):
			return

		def work():
			self.log_line("== Finding the live session on tty2 ==")
			result = subprocess.run(["loginctl", "list-sessions", "--no-legend"], capture_output=True, text=True)
			session_id = None
			for line in result.stdout.splitlines():
				fields = line.split()
				if not fields:
					continue
				candidate = fields[0]
				tty = subprocess.run(
					["loginctl", "show-session", candidate, "-p", "TTY", "--value"], capture_output=True, text=True
				).stdout.strip()
				if tty == "tty2":
					session_id = candidate
					break
			if session_id is None:
				self.log_line("No live session found on tty2 - nothing to kill.")
				return
			self.log_line(f"== Killing session {session_id} (tty2) ==")
			code = self._run_streamed(["sudo", "loginctl", "terminate-session", session_id])
			self.log_line(
				"Killed - getty@tty2.service will respawn a fresh session automatically."
				if code == 0
				else f"FAILED (exit {code})."
			)

		self.run_in_background(work)


def _boot_test(staged_dir, log_fn):
	"""Boots the staged build (see packaging/stage_build.sh) directly,
	with the real system Python, on a throwaway Xephyr display isolated
	from any real CosmOS session - never touches /cosmos/app/current
	or the real tty2 session. Blocks until the test window is closed.

	No mounting/extraction/GTK-bundling-hook needed any more - CosmOS
	is no longer packaged as an AppImage at all, so this is just "run the
	staged tree's own launcher/run.py". COSMOS_PACKAGED=1 is still set
	(matching what launcher/startx.sh sets for a real boot) so this test
	reflects real, no-hot-reload deployed behavior rather than the
	always-on dev-loop reload."""
	scratch = Path(tempfile.mkdtemp(prefix="cosmos-deploy-test-"))
	driver = scratch / "driver.py"
	driver.write_text(
		"import sys\n"
		f'sys.path.insert(0, "{staged_dir}/launcher")\n'
		"import run\n"
		'run.main(display=":90", x_binary="Xephyr", use_vt_args=False, start_wm=True, vt=1, screen_size="1280x720")\n'
	)

	log_fn("Launching on a throwaway display (:90) - close the test window when you're done looking.")
	test_proc = subprocess.Popen([sys.executable, "-sE", str(driver)], env={**os.environ, "COSMOS_PACKAGED": "1"})
	test_proc.wait()
	log_fn(f"Test session exited (code {test_proc.returncode})")


def main():
	app = DeployGUI()
	app.mainloop()


if __name__ == "__main__":
	main()
