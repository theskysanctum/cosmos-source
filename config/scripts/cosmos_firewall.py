"""The CosmOS firewall: firewalld with one zone ("cosmos", config/firewalld/zones/cosmos.xml) that rejects everything coming in from the
network except a short list of named doors. Each door is a firewalld service:

  ssh            SSH sign-in (22/tcp)                open by default - key-only, and no key is installed until you add one
  webtop         the Webtop over HTTPS (3001/tcp)    closed until "Webtop from other computers" is switched on, then open
  play-together  hosting a Play Together session     closed; GameBox opens it while it hosts and closes it when it stops
                 (Sunshine's streaming ports)         (a "temporary" door - see below)
  steam-remote-play  streaming from Steam here       closed; once switched on in Remote Access it is open only while Steam is running
                 (Valve's Remote Play ports)          (cosmos_steam_door.py watches Steam and renews a one-hour timeout)
  steam-lan-transfer  Steam local network game       the same, as its own switch: other computers copying an installed game from
                 transfers                            this one over the LAN (Steam's "Local Network Game Transfers")

Of the game stores, only Steam has inbound needs (Remote Play, LAN transfers) and Sunshine hosting has its own; Epic, GOG, Battle.net, EA,
Ubisoft, itch and the rest only make outgoing connections, which the firewall never touches, so they need no door.

A temporary door is never saved: it is added to the running firewall only, with a timeout (six hours), so it can't survive a reboot or a
crash of whatever opened it, and it is closed explicitly when the feature stops.

Nothing else listens for the network by default: no file sharing, no discovery, no remote desktop, no game-streaming host. The Remote
Access app opens and closes doors together with the feature they belong to (SSH on/off, Webtop network access on/off), so a door is
never open for something that is switched off. Opening a door changes both the running firewall and the saved configuration.

Changing the firewall needs root: through kronos's `firewall.door` action (opening a door then needs
the admin passphrase; closing one never does). This module only builds the commands and reads the state.
"""
import os
import subprocess

ZONE = "cosmos"
TEMPORARY_TIMEOUT = "6h"          # a door's own "timeout" overrides this
DOORS = {
    "ssh": {"service": "ssh", "label": "SSH sign-in", "port": "22/tcp", "ports": [22]},
    "webtop": {"service": "cosmos-webtop", "label": "Webtop over HTTPS", "port": "3001/tcp", "ports": [3001]},
    "play-together": {"service": "cosmos-play-together", "label": "Play Together hosting", "port": "streaming ports 47984-48010",
                      "ports": [47984, 47989, 48010, 47998, 47999, 48000, 48002], "temporary": True},
    "steam-remote-play": {"service": "cosmos-steam-remote-play", "label": "Steam Remote Play", "port": "27031-27037 (Steam's list)",
                          "ports": [27036, 27037, 27031], "temporary": True, "timeout": "1h"},
    "steam-lan-transfer": {"service": "cosmos-steam-lan-transfer", "label": "Steam LAN game transfers", "port": "27040/tcp, 27036/udp",
                           "ports": [27040, 27036], "temporary": True, "timeout": "1h"},
}
# Services that are part of the zone for reasons that aren't a door someone can switch.
ALWAYS = ("dhcpv6-client",)


class DoorError(ValueError):
    pass


def door_commands(name, open_):
    """The argv lists that open or close a door (what kronos's firewall.door action runs as root; kept here so the two can be compared): the running firewall first, then the saved configuration (a temporary door has no
    saved part)."""
    if name not in DOORS:
        raise DoorError(f"There is no door called {name!r}.")
    verb = "--add-service" if open_ else "--remove-service"
    service = DOORS[name]["service"]
    prefix = []
    if DOORS[name].get("temporary"):
        # running firewall only, and an opened door expires by itself
        timeout = DOORS[name].get("timeout", TEMPORARY_TIMEOUT)
        return [prefix + ["firewall-cmd", f"--zone={ZONE}", f"{verb}={service}"] + ([f"--timeout={timeout}"] if open_ else [])]
    return [prefix + ["firewall-cmd", f"--zone={ZONE}", f"{verb}={service}"],
            prefix + ["firewall-cmd", "--permanent", f"--zone={ZONE}", f"{verb}={service}"]]


def _failure(result, fallback):
    """The sentence to show for a kronos answer that wasn't ok."""
    return (result.get("reason") or (result.get("output") or "").strip() or fallback)


def set_door(name, open_, ask=None):
    """(ok, message). Goes through kronos's firewall.door action (closing and the temporary doors are automatic; opening ssh or the
    Webtop asks for the admin passphrase). `ask` is cosmos_kronos.ask, replaceable for tests."""
    if name not in DOORS:
        raise DoorError(f"There is no door called {name!r}.")
    if ask is None:
        import cosmos_kronos
        ask = cosmos_kronos.ask
    result = ask("firewall.door", {"door": name, "open": bool(open_)})
    if result.get("status") == "ok":
        return True, ""
    return False, _failure(result, "The firewall couldn't be changed.")


def _permanent_services(zone_dirs=("/etc/firewalld/zones", "/usr/lib/firewalld/zones")):
    """The services in the cosmos zone's configuration files (readable by everyone; the running firewall itself may only be asked by an administrator)."""
    import xml.etree.ElementTree as ET
    for folder in zone_dirs:
        path = os.path.join(folder, f"{ZONE}.xml")
        try:
            return [e.get("name") for e in ET.parse(path).getroot().findall("service") if e.get("name")]
        except (OSError, ET.ParseError):
            continue
    return None


def status(run=subprocess.run, zone_dirs=("/etc/firewalld/zones", "/usr/lib/firewalld/zones")):
    """{"running": bool, "zone_ok": bool, "open": [service names], "doors": {name: bool}, "unexpected": [services no door explains]}
    firewall-cmd may refuse an ordinary user, so when it does the service state comes from systemd and the doors from the zone's configuration
    (which cannot show a door opened only until it expires)."""
    nothing = {"running": False, "zone_ok": False, "open": [], "doors": {n: False for n in DOORS}, "unexpected": []}
    services, asked = [], False
    try:
        state = run(["firewall-cmd", "--state"], capture_output=True, text=True, timeout=10)
        if state.returncode == 0:
            listed = run(["firewall-cmd", f"--zone={ZONE}", "--list-services"], capture_output=True, text=True, timeout=10)
            if listed.returncode == 0:
                services, asked = listed.stdout.split(), True
            active = True
        else:
            active = run(["systemctl", "is-active", "--quiet", "firewalld.service"], capture_output=True, text=True, timeout=10).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return nothing
    if not active:
        return nothing
    if not asked:
        configured = _permanent_services(zone_dirs)
        services = configured or []
        asked = configured is not None
    known = {d["service"] for d in DOORS.values()} | set(ALWAYS)
    return {"running": True, "zone_ok": asked, "open": services,
            "doors": {n: d["service"] in services for n, d in DOORS.items()},
            "unexpected": [s for s in services if s not in known]}


def listeners(run=subprocess.run):
    """[(protocol, address, port)] for everything listening on a non-loopback address (`ss`); these are the things the firewall
    decides about. Loopback listeners (the Webtop's plain port, local services) can't be reached from the network anyway."""
    try:
        out = run(["ss", "-H", "-l", "-n", "-t", "-u"], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    found = []
    for line in out.splitlines():
        cols = line.split()
        if len(cols) < 5 or ":" not in cols[4]:
            continue
        addr, _, port = cols[4].rpartition(":")
        addr = addr.strip("[]")
        if addr.startswith("127.") or addr in ("::1", "localhost") or addr.startswith("fe80") or not port.isdigit():
            continue
        found.append((cols[0], addr, int(port)))
    return sorted(set(found))


def audit(run=subprocess.run):
    """Human-readable check: what is listening for the network, and whether each is let in or rejected."""
    st = status(run)
    open_ports = {port for n, d in DOORS.items() if st["doors"].get(n) for port in d["ports"]}
    lines = [describe(st)]
    for proto, addr, port in listeners(run):
        if not st["running"]:
            verdict = "NOT protected - the firewall is not running"
        else:
            verdict = "let in (a door is open)" if port in open_ports else "rejected by the firewall"
        lines.append(f"  {proto:4} {addr}:{port}  {verdict}")
    if len(lines) == 1:
        lines.append("  nothing is listening on the network")
    return "\n".join(lines)


def describe(st):
    """One line for the Remote Access window."""
    if not st["running"]:
        return "The firewall is not running."
    doors = [DOORS[n]["label"] + " (" + DOORS[n]["port"] + ")" for n, is_open in st["doors"].items() if is_open]
    text = "Firewall on. Open to the network: " + (", ".join(doors) if doors else "nothing") + "."
    if st["unexpected"]:
        text += " Also open (not set up by CosmOS): " + ", ".join(st["unexpected"]) + "."
    return text


if __name__ == "__main__":
    import sys
    if sys.argv[1:] == ["check"]:
        print(audit())
    else:
        print("usage: cosmos_firewall.py check", file=sys.stderr)
        sys.exit(2)
