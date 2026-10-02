"""What can be updated on CosmOS, from all three places software comes from.

One list, three providers, all optional (a provider whose tool is missing just isn't shown):

  Flatpak     apps installed with flatpak, per-user and system-wide
  AppImages   apps installed with AM (the AppImage manager: `am`)
  System      the DNF/bootc side. CosmOS is an image-based system: its packages are baked into
              the image, so `dnf upgrade` cannot change /usr on a running machine. What can be
              done is (a) tell you which packages the repositories now have newer than the
              image's (`dnf check-update`) and (b) upgrade the image itself (`bootc upgrade`)
              when it comes from a registry - otherwise the fix is rebuilding the image.

Checking never changes anything. Each provider can also give the shell command that applies its
updates; the applet runs those in a cosmos-task window so the output and any question they ask
are visible. No GTK here: commands are run through an injectable `run` so the parsing and the
decisions are testable with canned output.
"""
import json
import re
import shlex
import shutil
import subprocess
from dataclasses import dataclass, field

ARCHES = ("x86_64", "noarch", "i686", "aarch64", "src")


@dataclass
class Update:
    name: str
    current: str = ""
    new: str = ""
    source: str = ""


@dataclass
class Result:
    provider: str            # "flatpak" | "appimage" | "system"
    title: str
    available: bool          # the tool exists on this machine
    updates: list = field(default_factory=list)
    note: str = ""           # one line for the panel: what was found, or why it can't be checked
    apply_command: str = ""  # shell command that applies them ("" = nothing to run)
    error: str = ""

    @property
    def count(self):
        return len(self.updates)


def default_run(command, timeout=180):
    """(returncode, stdout+stderr) of a shell command; never raises."""
    try:
        proc = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=timeout)
        return proc.returncode, proc.stdout + proc.stderr
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 124, str(exc)


def have(tool):
    return shutil.which(tool) is not None


# ---------------------------------------------------------------------------- flatpak
def parse_flatpak_updates(text, origin):
    """`flatpak remote-ls --updates --columns=name,application,version` (tab separated)."""
    updates = []
    for line in text.splitlines():
        parts = [p.strip() for p in line.split("\t")]
        if len(parts) >= 2 and parts[0] and "." in parts[1] and not line.lower().startswith("name"):
            updates.append(Update(name=parts[0], current="", new=parts[2] if len(parts) > 2 else "", source=origin))
    return updates


def check_flatpak(run=default_run, tool_present=have):
    result = Result("flatpak", "Flatpak apps", tool_present("flatpak"))
    if not result.available:
        result.note = "Flatpak is not installed."
        return result
    for scope in ("user", "system"):
        rc, out = run(f"flatpak remote-ls --{scope} --updates --columns=name,application,version 2>&1")
        if rc == 0:
            result.updates += parse_flatpak_updates(out, scope)
        elif scope == "user" and "not" not in out.lower():
            result.error = out.strip().splitlines()[-1] if out.strip() else "flatpak failed"
    result.note = f"{result.count} update(s) available" if result.count else "Everything is up to date."
    if result.count:
        result.apply_command = ("flatpak update --user -y --noninteractive; "
                                "kronos exec -- flatpak update --system -y --noninteractive")
    return result


# ---------------------------------------------------------------------------- AppImages (AM)
def parse_am_installed(text):
    """Names of installed apps from `am -f` (one per line, possibly with sizes/decoration)."""
    names = []
    for line in text.splitlines():
        line = re.sub(r"\x1b\[[0-9;]*m", "", line).strip()
        match = re.match(r"^[\W_]*([A-Za-z0-9][A-Za-z0-9._+-]+)\b", line)
        if match and not line.lower().startswith(("installed", "total", "list", "usage")):
            names.append(match.group(1))
    return names


def check_appimages(run=default_run, tool_present=have):
    result = Result("appimage", "AppImages (AM)", tool_present("am"))
    if not result.available:
        result.note = "AM (the AppImage manager) is not installed."
        return result
    rc, out = run("am -f 2>&1", timeout=60)
    installed = parse_am_installed(out) if rc == 0 else []
    result.updates = []                     # AM checks and updates in one step; it has no dry run
    result.note = (f"{len(installed)} AppImage(s) installed - AM checks each one and updates it in place."
                   if installed else "No AppImages installed with AM.")
    if installed:
        result.apply_command = "am -u"
    return result


# ---------------------------------------------------------------------------- system: dnf + bootc
def parse_dnf_check_update(text):
    """`dnf check-update` (dnf4: `name.arch version repo`; dnf5: `name arch version repo size`)."""
    updates = []
    for line in text.splitlines():
        tokens = line.split()
        if len(tokens) < 3 or line.startswith(("Last metadata", "Obsoleting", "Repositories", "Updating", "Package ", "Name ", "Upgrading")):
            continue
        first = tokens[0]
        if "." in first and first.rsplit(".", 1)[1] in ARCHES:                    # dnf4
            updates.append(Update(name=first.rsplit(".", 1)[0], new=tokens[1], source=tokens[2]))
        elif len(tokens) >= 4 and tokens[1] in ARCHES:                            # dnf5
            updates.append(Update(name=first, new=tokens[2], source=tokens[3]))
    return updates


def parse_image_reference(reference):
    """('registry', 'ghcr.io/x/y:tag') for an image that can be pulled, ('local', ref) for one built on this machine, (None, '') for anything else. `reference` is
    rpm-ostree's container-image-reference: ostree-unverified-registry:REF, ostree-remote-registry:REMOTE:REF, ostree-image-signed:docker://REF, or
    ostree-unverified-image:containers-storage:REF / oci:PATH for local ones."""
    text = (reference or "").strip()
    if not text:
        return None, ""
    if text.startswith(("ostree-unverified-registry:", "ostree-image-signed:docker://", "ostree-unverified-image:docker://")):
        ref = text.split(":", 1)[1].removeprefix("docker://") if not text.startswith("ostree-unverified-registry:") else text[len("ostree-unverified-registry:"):]
        if text.startswith(("ostree-image-signed:", "ostree-unverified-image:")):
            ref = text.split("docker://", 1)[1]
    elif text.startswith("ostree-remote-registry:"):
        ref = text.split(":", 2)[2] if text.count(":") >= 2 else ""
    elif text.startswith(("ostree-unverified-image:containers-storage:", "ostree-image-signed:containers-storage:")) or "oci:" in text or "oci-archive:" in text:
        return "local", text.split("containers-storage:", 1)[-1]
    else:
        return None, ""
    if not ref:
        return None, ""
    return ("local" if ref.startswith("localhost/") else "registry"), ref


def bootc_source(run=default_run):
    """('registry', 'quay.io/x/y:tag') for an image that can be pulled, ('local', ref) for one
    built on this machine, or (None, '') when this isn't a bootc system. `bootc status` needs root,
    so an ordinary user is answered from rpm-ostree, whose status anyone may read."""
    rc, out = run("bootc status --json 2>/dev/null", timeout=30)
    if rc == 0:
        try:
            image = json.loads(out)["spec"]["image"]
        except (ValueError, KeyError, TypeError):
            image = None
        if image:
            ref = image.get("image", "")
            if image.get("transport", "") == "registry" and not ref.startswith("localhost/"):
                return "registry", ref
            return "local", ref
    rc, out = run("rpm-ostree status --json 2>/dev/null", timeout=30)
    if rc != 0:
        return None, ""
    try:
        deployments = json.loads(out)["deployments"]
        booted = next((d for d in deployments if d.get("booted")), deployments[0])
        return parse_image_reference(booted.get("container-image-reference", ""))
    except (ValueError, KeyError, IndexError, TypeError, StopIteration):
        return None, ""


def layered_packages(run=default_run):
    """Packages added on top of the image with rpm-ostree (`dnf install` does that here)."""
    rc, out = run("rpm-ostree status --json 2>/dev/null", timeout=30)
    if rc != 0:
        return []
    try:
        deployments = json.loads(out)["deployments"]
        return list(deployments[0].get("requested-packages", []))
    except (ValueError, KeyError, IndexError, TypeError):
        return []


def check_system(run=default_run, tool_present=have):
    result = Result("system", "System (DNF / bootc)", tool_present("dnf") or tool_present("bootc"))
    if not result.available:
        result.note = "Neither dnf nor bootc is installed."
        return result
    if tool_present("dnf"):
        rc, out = run("dnf check-update --quiet 2>&1", timeout=240)
        if rc == 100:
            result.updates = parse_dnf_check_update(out)
        elif rc != 0:
            result.error = out.strip().splitlines()[-1] if out.strip() else "dnf check-update failed"
    kind, ref = bootc_source(run) if tool_present("bootc") else (None, "")
    pending = len(result.updates)
    layered = layered_packages(run) if tool_present("rpm-ostree") else []
    upgrade = "kronos exec -- rpm-ostree upgrade" if tool_present("rpm-ostree") else "kronos exec -- bootc upgrade"
    if kind == "registry":
        result.apply_command = "kronos exec -- bootc upgrade"
        result.note = (f"{pending} package(s) newer in the repositories; the image {ref} can be upgraded."
                       if pending else f"Repositories match the image; the image {ref} can be checked for a new build.")
    elif kind == "local" and layered:
        result.apply_command = upgrade          # the packages added with `dnf install` can be upgraded
        result.note = (f"{pending} package(s) newer in the repositories. {len(layered)} package(s) were added with dnf "
                       "install and can be upgraded here; the image's own packages need a rebuild.")
    elif kind == "local":
        result.note = (f"{pending} package(s) are newer in the repositories than in this image. The image was built "
                       "on this machine, so rebuild it to include them." if pending
                       else "Up to date with the repositories. (Built locally: updates come from rebuilding.)")
    else:
        result.note = f"{pending} package(s) newer in the repositories." if pending else "Up to date."
    return result


# ---------------------------------------------------------------------------- everything
PROVIDERS = (check_flatpak, check_appimages, check_system)


def check_all(run=default_run, tool_present=have):
    return [provider(run=run, tool_present=tool_present) for provider in PROVIDERS]


def apply_all_command(results):
    """One shell command that applies every provider's updates in turn."""
    steps = []
    for result in results:
        if result.apply_command:
            steps.append(f"echo; echo '=== {result.title} ==='; {result.apply_command}")
    return "; ".join(steps)


def summary(results):
    """(number of updates found, short text for a tooltip/notification)."""
    total = sum(r.count for r in results if r.provider != "appimage")
    parts = [f"{r.count} {r.title.split(' (')[0].lower()}" for r in results if r.count]
    return total, (", ".join(parts) if parts else "Everything is up to date")


def signature(results):
    """Stable text identifying the update set, so a notification is sent once per new set."""
    return "|".join(sorted(f"{r.provider}:{u.name}:{u.new}" for r in results for u in r.updates))
