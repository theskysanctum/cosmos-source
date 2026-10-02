#!/usr/bin/env python3
"""Interactive Telegram login for Cosmochat - the QR-code dance a user
walks through once per profile (see profiles.cosmochat_session_path())
so notifications/telegram_listener.py's background relay can receive
their real messages. Deliberately separate from telegram_listener.py:
this module drives a short-lived, foreground, UI-facing login flow; that
one is the long-lived, headless background relay.

Meant to run on app.py's own run_async() background thread (see
App.run_async) - its own internal asyncio loop, not GLib's - and reports
back to the GTK thread through GLib.idle_add directly, since a login
flow with a live-refreshing QR code needs more than run_async's single
"start work -> one final result" shape."""
import asyncio
import queue
import sys
from pathlib import Path

import qrcode
try:
	from telethon import TelegramClient
	from telethon.errors import SessionPasswordNeededError
except ImportError:
	# Telegram support is optional: CosmOS doesn't ship telethon by default, and the rest of the launcher must start without it.
	TelegramClient = SessionPasswordNeededError = None
from gi.repository import GLib

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import debuglog  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "launcher"))
import profiles  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
import telegram_credentials  # noqa: E402

log = debuglog.setup("telegram_client")

QR_PATH = Path("/tmp/console-os-logs/cosmochat_qr.png")

# Bridges the 2FA password from the GTK thread (see submit_password(),
# called once the user types it in) into the asyncio coroutine below,
# which is blocked waiting on it on a different thread entirely. A
# module-level singleton is enough - only one login flow can be in
# progress at a time in this single-process app.
_password_queue = queue.Queue()


def is_logged_in():
	"""Cheap, synchronous check for the dashboard/widget to poll - a
	session file existing doesn't strictly guarantee it's still authorized
	(Telegram could revoke it remotely), but that's rare enough that a
	real connect-and-check on every dashboard refresh isn't worth the
	cost. The background listener (telegram_listener.py) does the real
	is_user_authorized() check itself before ever relying on it."""
	session_path = profiles.cosmochat_session_path()
	return session_path is not None and Path(f"{session_path}.session").exists()


def submit_password(password):
	_password_queue.put(password)


def start_qr_login(on_qr_ready, on_password_needed, on_success, on_error):
	"""Blocking - meant to be run via App.run_async on its own thread.
	Drives the whole QR login dance: shows a fresh QR, waits for it to be
	scanned, regenerates it if it expires before that happens, and keeps
	going until the user actually logs in, hits a real error, or (if 2FA
	is enabled) needs a password. Every callback is marshaled onto the
	GTK thread via GLib.idle_add since this itself runs on a background
	thread."""
	if TelegramClient is None:
		GLib.idle_add(on_error, "Telegram support (the telethon package) isn't installed on this system.")
		return
	credentials = telegram_credentials.load()
	if not credentials["api_id"]:
		GLib.idle_add(on_error, "No API credentials set yet - see Cosmochat's setup page.")
		return
	session_path = profiles.cosmochat_session_path()
	if session_path is None:
		GLib.idle_add(on_error, "No active profile.")
		return

	async def run():
		client = TelegramClient(str(session_path), int(credentials["api_id"]), credentials["api_hash"])
		await client.connect()
		try:
			qr_login = await client.qr_login()
			while True:
				QR_PATH.parent.mkdir(parents=True, exist_ok=True)
				qrcode.make(qr_login.url).save(QR_PATH)
				GLib.idle_add(on_qr_ready, str(QR_PATH))
				try:
					await qr_login.wait(timeout=30)
					break
				except asyncio.TimeoutError:
					await qr_login.recreate()
					continue
				except SessionPasswordNeededError:
					GLib.idle_add(on_password_needed)
					password = await asyncio.get_event_loop().run_in_executor(None, _password_queue.get)
					await client.sign_in(password=password)
					break
			GLib.idle_add(on_success)
		except Exception as e:
			log.exception("QR login failed")
			GLib.idle_add(on_error, str(e))
		finally:
			await client.disconnect()

	asyncio.run(run())
