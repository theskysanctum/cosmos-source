#!/usr/bin/env python3
"""Cosmochat's background message relay - the systemd --user service
(cosmochat.service) that keeps a Telethon connection open for whichever
CosmOS profile is currently active, and appends every incoming
Telegram message to that profile's own cosmochat_messages.json (see
profiles.cosmochat_messages_path()) for the dashboard widget (see
launcher/app.py's build_cosmochat_page()) to read.

Tracks the active profile, not a fixed one - app.py restarts this
service on every profile switch (see activate_profile()) so it always
matches whoever's actually using the console; this loop is also
resilient on its own to "no session yet"/"profile has no Telegram
login" by polling rather than crash-looping, as a defensive backstop
against a missed restart."""
import asyncio
import json
import os
import sys
import time
from pathlib import Path

try:
	from telethon import TelegramClient, events
except ImportError:
	# optional: CosmOS doesn't ship telethon by default (main() below just idles without it)
	TelegramClient = events = None

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import debuglog  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "launcher"))
import profiles  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
import telegram_credentials  # noqa: E402

log = debuglog.setup("telegram_listener")

MAX_MESSAGES = 50
POLL_SECONDS = 10


def _load(path):
	if not path.exists():
		return []
	try:
		with open(path) as f:
			return json.load(f)
	except (json.JSONDecodeError, OSError):
		log.warning(f"could not read {path}, starting fresh", exc_info=True)
		return []


def _save_atomic(path, records):
	path.parent.mkdir(parents=True, exist_ok=True)
	tmp_path = path.with_suffix(".tmp")
	with open(tmp_path, "w") as f:
		json.dump(records, f)
	os.replace(tmp_path, path)


def list_recent():
	"""What the Cosmochat dashboard widget reads (see launcher/app.py's
	on_cosmochat_done()) - never raises, matching every other widget fetch
	function in this project: an empty list if the active profile has no
	messages recorded yet (or none logged in at all), most-recent first."""
	messages_path = profiles.cosmochat_messages_path()
	return _load(messages_path) if messages_path else []


def record_message(messages_path, sender, text):
	records = _load(messages_path)
	records.insert(0, {"sender": sender, "text": text, "timestamp": time.time()})
	del records[MAX_MESSAGES:]
	_save_atomic(messages_path, records)
	log.info(f"recorded message from {sender!r}")


async def run_for_profile(name):
	session_path = profiles.cosmochat_session_path()
	messages_path = profiles.cosmochat_messages_path()
	credentials = telegram_credentials.load()
	client = TelegramClient(str(session_path), int(credentials["api_id"]), credentials["api_hash"])
	await client.connect()
	if not await client.is_user_authorized():
		log.info(f"profile {name!r} has no Telegram login yet, idling")
		await client.disconnect()
		return False

	@client.on(events.NewMessage(incoming=True))
	async def handler(event):
		sender = await event.get_sender()
		sender_name = getattr(sender, "first_name", None) or getattr(sender, "title", None) or "Unknown"
		record_message(messages_path, sender_name, event.raw_text)

	log.info(f"listening for Telegram messages for profile {name!r}")
	await client.run_until_disconnected()
	return True


def main():
	log.info("starting cosmochat telegram_listener")
	if TelegramClient is None:
		log.warning("telethon isn't installed, so there is nothing to listen with - idling")
		while True:
			time.sleep(3600)          # (idle rather than exit, so a service manager doesn't restart it in a loop)
	while True:
		name = profiles.get_active_profile()
		credentials = telegram_credentials.load()
		if name is None or not credentials["api_id"]:
			time.sleep(POLL_SECONDS)
			continue
		try:
			ran = asyncio.run(run_for_profile(name))
		except Exception:
			log.exception(f"listener for profile {name!r} crashed, retrying")
			ran = False
		if not ran:
			time.sleep(POLL_SECONDS)


if __name__ == "__main__":
	main()
