#!/usr/bin/env python3
"""Supervises a running game: watches for the pause hotkey - the gamepad's
Guide/Mode button (BTN_MODE, a button essentially no game itself binds
to) or the keyboard's Home key, whichever's actually connected - the
whole time it's running, and is meant to be the *only* way to exit an
emulator, per plan2.txt/user direction - not one option alongside
whatever quit mechanism each emulator happens to have. For the
emulators that do ship their own gamepad quit hotkey (RetroArch chief
among them), that hotkey needs to be disabled in their own config so it
can't bypass this - see the per-emulator config notes (e.g.
retroarch.cfg's input_exit_emulator) rather than relying on this module
alone to guarantee it.

F12 was the first choice for the keyboard equivalent, but that's a
built-in Chromium DevTools shortcut - meaningless for emulators, but a
real collision the moment the browser is what's being supervised (see
graphical.run_graphical(), which runs pause_menu.supervise() for every
graphical launch, browser included). Home doesn't collide with anything
either context actually binds.

On pause: SIGSTOPs the game's entire real process tree (found via
psutil - the same "wrapper script vs. real binary pid" problem
TeknoParrot/Wine has applies to emulators too, so the root pid alone
isn't enough) and shows pause_overlay.py's Resume/
Quit window. Resume SIGCONTs everything back; Quit SIGKILLs the whole
tree outright rather than trying a graceful SIGTERM first - a stopped
process can't run a SIGTERM handler anyway (it would just sit queued
until resumed), and "the only way to exit" is a reliability requirement
before it's a nicety.
"""
import os
import select
import signal
import subprocess
import sys
import tempfile
from pathlib import Path

import psutil
from evdev import ecodes

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "input"))
from gamepad import find_gamepads, find_keyboards  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "launcher"))
import graphical  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import debuglog  # noqa: E402

log = debuglog.setup("pause_menu")

PAUSE_OVERLAY = str(Path(__file__).parent / "pause_overlay.py")
PAUSE_BUTTON = ecodes.BTN_MODE
PAUSE_KEY = ecodes.KEY_HOME  # keyboard equivalent - useful with no gamepad connected (dev/testing) too


def process_tree_pids(root_pid):
	try:
		root = psutil.Process(root_pid)
	except psutil.NoSuchProcess:
		log.warning(f"pid {root_pid} no longer exists when building process tree")
		return []
	pids = [root_pid] + [p.pid for p in root.children(recursive=True)]
	log.debug(f"process tree for {root_pid}: {pids}")
	return pids


def send_signal(pids, sig):
	log.info(f"sending {signal.Signals(sig).name} to {pids}")
	for pid in pids:
		try:
			os.kill(pid, sig)
		except ProcessLookupError:
			log.debug(f"pid {pid} already gone")


def wait_for_pause_press(devices, game_proc, poll_timeout=0.3):
	device_by_fd = {d.fd: d for d in devices}
	while game_proc.poll() is None:
		ready, _, _ = select.select(device_by_fd, [], [], poll_timeout)
		for fd in ready:
			for event in device_by_fd[fd].read():
				if event.type == ecodes.EV_KEY and event.code in (PAUSE_BUTTON, PAUSE_KEY) and event.value == 1:
					log.info("pause button pressed")
					return True
	return False


def show_pause_overlay(env):
	fd, result_path = tempfile.mkstemp(prefix="pause-result-")
	os.close(fd)
	try:
		proc = subprocess.Popen(["python3", PAUSE_OVERLAY, result_path], env=env)
		window_id = graphical.find_window_for_pid(proc.pid, env)
		if window_id:
			graphical.focus_window(window_id, env)
		proc.wait()
		with open(result_path) as f:
			choice = f.read().strip() or "resume"
		log.info(f"pause overlay choice: {choice}")
		return choice
	finally:
		if os.path.exists(result_path):
			os.remove(result_path)


def supervise(game_proc, env):
	devices = find_gamepads() + find_keyboards()
	if not devices:
		log.warning("no gamepad or keyboard found, pause menu unavailable for this session - waiting on process exit only")
		game_proc.wait()
		return
	log.debug(f"supervising pid {game_proc.pid} with {len(devices)} input device(s)")

	while True:
		pressed = wait_for_pause_press(devices, game_proc)
		if game_proc.poll() is not None:
			log.debug(f"game process {game_proc.pid} exited on its own (code {game_proc.returncode})")
			return
		if not pressed:
			continue

		pids = process_tree_pids(game_proc.pid)
		send_signal(pids, signal.SIGSTOP)
		choice = show_pause_overlay(env)
		if choice == "quit":
			send_signal(pids, signal.SIGKILL)
			game_proc.wait()
			return
		send_signal(pids, signal.SIGCONT)
