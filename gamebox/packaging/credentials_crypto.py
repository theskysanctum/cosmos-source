#!/usr/bin/env python3
"""Shared at-rest encryption for every real credential/token file this
project stores (API keys, remote-connection passwords, ...) - per
direct instruction ("encrypted files would be better... than py or text
files for everything API, Token, etc related"). Fernet (symmetric,
authenticated - the cryptography package's own recommended "just
encrypt this blob safely" primitive, not something hand-rolled) with a
real, randomly-generated key - generated once, on first real use, and
stored OUTSIDE the repo entirely (paths.DATA_ROOT, not settings/ or any
other tracked directory) so even a fully misconfigured .gitignore could
never leak it alongside the credential files it protects.

The real threat model here is accidental exposure - a stray `git add
-A`, a screenshot, a copied backup of just the repo checkout - not a
fully compromised running system (which could always just call
decrypt_json() itself, the same way it could read any other secret a
running process has access to); this still adds a real layer defense-
in-depth wasn't providing before, matching the same "never accidentally
leak a token" concern this whole project's own .env/.gitignore handling
already reflects.

Every credential module should store data via encrypt_json()/
decrypt_json() instead of raw json.dump()/json.load() - same read/write
shape (a plain dict in, a plain dict out), so migrating an existing
plain-JSON credential module is a small, mechanical change. Never
raises - a missing, corrupt, or wrong-key-encrypted file all just mean
"no credentials yet", the same "missing means empty" contract every
credentials module in this project already follows."""
import json
import os
import sys
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import debuglog  # noqa: E402
import paths  # noqa: E402

log = debuglog.setup("credentials_crypto")

# Outside the repo entirely (unlike the encrypted *.json files
# themselves, which stay in their existing settings/ locations, still
# gitignored too - defense in depth, not a replacement for it) - see
# this module's own docstring on why that separation is the actual
# point.
KEY_PATH = paths.DATA_ROOT / ".credentials.key"

_fernet_cache = None


def _get_fernet():
	global _fernet_cache
	if _fernet_cache is not None:
		return _fernet_cache
	if KEY_PATH.is_file():
		key = KEY_PATH.read_bytes()
	else:
		key = Fernet.generate_key()
		KEY_PATH.parent.mkdir(parents=True, exist_ok=True)
		KEY_PATH.write_bytes(key)
		os.chmod(KEY_PATH, 0o600)
		log.info(f"generated a new credentials encryption key at {KEY_PATH}")
	_fernet_cache = Fernet(key)
	return _fernet_cache


def encrypt_json(data, dest_path):
	"""Encrypts `data` (a real JSON-serializable dict) and writes it to
	dest_path as opaque encrypted bytes. Never raises - logs and returns
	False on any failure (a caller can't do much about a failed write
	anyway; every real caller this session just fires-and-forgets a
	save() the same way the old plain-JSON versions did)."""
	try:
		payload = json.dumps(data).encode()
		token = _get_fernet().encrypt(payload)
		dest_path = Path(dest_path)
		dest_path.parent.mkdir(parents=True, exist_ok=True)
		dest_path.write_bytes(token)
		return True
	except OSError:
		log.exception(f"failed to write encrypted credentials to {dest_path}")
		return False


def decrypt_json(src_path):
	"""The real dict previously written by encrypt_json(), or {} if the
	file doesn't exist, is corrupt, or was encrypted with a different/
	since-regenerated key - never raises, "missing/unreadable means
	empty" matches every other credentials module's own established
	contract, so callers don't need a special case for "never saved
	anything yet" vs. "can't read what's there"."""
	src_path = Path(src_path)
	if not src_path.is_file():
		return {}
	try:
		token = src_path.read_bytes()
		payload = _get_fernet().decrypt(token)
		return json.loads(payload)
	except (InvalidToken, ValueError, OSError):
		log.exception(f"failed to read encrypted credentials from {src_path}")
		return {}
