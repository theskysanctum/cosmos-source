#!/usr/bin/env python3
"""Tiny, dependency-free .env reader shared by every module that needs a
real embedded credential (settings/igdb.py, packaging/dropbox_token.py) -
one parser, not a copy per module. Reads the repo root's own .env file
(gitignored, never committed - see .gitignore's own comment on why),
KEY=VALUE per line, '#' comments and blank lines skipped, values
optionally quoted with matching single/double quotes.

Not the real python-dotenv package - this project has no other reason
to take on that dependency, and the actual parsing need here (one flat
file, no variable expansion, no export keyword) is small enough that a
real, dedicated third-party library would be more dependency than
value."""
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = REPO_ROOT / ".env"

_cache = None


def _parse(path):
	values = {}
	if not path.exists():
		return values
	for line in path.read_text().splitlines():
		line = line.strip()
		if not line or line.startswith("#") or "=" not in line:
			continue
		key, _, value = line.partition("=")
		key = key.strip()
		value = value.strip()
		if value and value[0] in "\"'" and value[-1] == value[0] and len(value) >= 2:
			value = value[1:-1]
		if key:
			values[key] = value
	return values


def get(key, env_path=ENV_PATH):
	"""Returns the value of `key` from .env, or None if the file or the
	key is missing - callers decide what a missing credential means (a
	logged warning + degraded feature, never a raised exception - see
	igdb.py's/dropbox_token.py's own docstrings). Parses once and caches
	(module-level, keyed by nothing but the default path - fine, this
	project only ever has one real .env) rather than re-reading the file
	on every lookup."""
	global _cache
	if env_path == ENV_PATH:
		if _cache is None:
			_cache = _parse(env_path)
		return _cache.get(key)
	return _parse(env_path).get(key)
