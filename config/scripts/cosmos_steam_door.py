"""Steam's firewall doors (Remote Play, and local network game transfers), open only while Steam is running.

CosmOS doesn't run Steam, so nothing tells it when streaming or a transfer starts or ends - but Steam is a program, and it can be watched. You
switch a door on once in Settings > Remote Access (that is just a small file per door: ~/.config/cosmos/<door name>). While it is on:

    Steam is running   -> the door (cosmos_firewall's "steam-remote-play" / "steam-lan-transfer") is open
    Steam has quit     -> the door is closed again
    switched off       -> closed, whatever Steam is doing

The door itself is never saved and always carries a short timeout (one hour); this watcher renews it every half hour while Steam runs, so if
the watcher dies the door still closes by itself within the hour. A restarted firewall (which forgets runtime doors) is noticed and the door
put back. Nothing is opened unless you switched it on; nothing is done at all while it is off (no sudo, no firewall calls).

cosmos-steam-door runs this loop, started by the session.
"""
import os
import time

import cosmos_firewall as fw

DOOR = "steam-remote-play"
DOORS = ("steam-remote-play", "steam-lan-transfer")
CONFIG = os.path.join(os.path.expanduser("~"), ".config", "cosmos")


def arm_file(door=DOOR, config=CONFIG):
    return os.path.join(config, door)


ARM_FILE = arm_file(DOOR)
POLL_S = 5
RENEW_S = 1800
VERIFY_S = 60
STEAM_NAMES = ("steam",)


def arm(path=ARM_FILE):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("on\n")


def disarm(path=ARM_FILE):
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass


def is_armed(path=ARM_FILE):
    return os.path.exists(path)


def steam_running(proc="/proc"):
    """True if a process called `steam` exists (the client; its helper processes have other names)."""
    try:
        entries = os.listdir(proc)
    except OSError:
        return False
    for entry in entries:
        if not entry.isdigit():
            continue
        try:
            with open(os.path.join(proc, entry, "comm"), encoding="utf-8") as fh:
                if fh.read().strip() in STEAM_NAMES:
                    return True
        except OSError:
            continue
    return False


class Watcher:
    def __init__(self, door=DOOR, armed=None, running=steam_running, set_door=fw.set_door, status=fw.status, now=time.monotonic,
                 renew_s=RENEW_S, verify_s=VERIFY_S):
        self.door = door
        self.armed = armed or (lambda: is_armed(arm_file(door)))
        self.running, self.set_door, self.status, self.now = running, set_door, status, now
        self.renew_s, self.verify_s = renew_s, verify_s
        self.open = False            # what we believe the door is
        self.opened_at = 0.0
        self.verified_at = 0.0
        self.last_error = ""

    def _apply(self, open_):
        ok, msg = self.set_door(self.door, open_)
        self.last_error = "" if ok else msg
        if ok:
            self.open = open_
            if open_:
                self.opened_at = self.verified_at = self.now()
        return ok

    def tick(self):
        """One look at the world; returns what it did ("opened", "closed", "renewed", "fixed", "" for nothing)."""
        want = bool(self.armed() and self.running())
        t = self.now()
        if want and not self.open:
            return "opened" if self._apply(True) else ""
        if not want and self.open:
            return "closed" if self._apply(False) else ""
        if want and self.open:
            if t - self.opened_at >= self.renew_s:                 # renew: a door added with a timeout can't simply be re-added
                if self._apply(False) and self._apply(True):
                    return "renewed"
                return ""
            if t - self.verified_at >= self.verify_s:              # did the firewall forget it (restart)?
                self.verified_at = t
                st = self.status()
                if st.get("running") and not st.get("doors", {}).get(self.door):
                    return "fixed" if self._apply(True) else ""
        return ""

    def shutdown(self):
        """Close the door if we opened it (called when the watcher is asked to stop)."""
        if self.open:
            self._apply(False)


def main():
    import signal
    import sys
    watchers = [Watcher(door) for door in DOORS]
    stop = []
    signal.signal(signal.SIGTERM, lambda *_: stop.append(1))
    signal.signal(signal.SIGINT, lambda *_: stop.append(1))
    while not stop:
        for w in watchers:
            try:
                w.tick()
            except Exception:                                      # never let a hiccup kill the watcher
                pass
        time.sleep(POLL_S)
    for w in watchers:
        w.shutdown()
    sys.exit(0)


if __name__ == "__main__":
    main()
