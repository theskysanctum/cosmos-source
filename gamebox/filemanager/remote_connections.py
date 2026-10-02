#!/usr/bin/env python3
"""Saved FTP/SFTP/WebDAV connections for File Manager's own remote
sources (see filemanager/local_scope.py for the Local/USB side of the
same source-picker) - name/protocol/host/port/username/password, one
real per-install list, encrypted at rest via packaging/
credentials_crypto.py the same way settings/realdebrid.py and friends
already store a personal credential (per direct instruction -
"encrypted files would be better... for everything API, Token, etc
related"), not a project-shared default (there's no such thing as a
CosmOS-owned FTP server).

Stored as {"connections": [{"id", "name", "protocol", "host", "port",
"username", "password"}, ...]} - a plain list, not keyed by name, since
a user could reasonably want two saved connections to the same host
(different accounts, different starting paths later) and a name is
just a display label, not a real identity. "id" is a small integer
CosmOS assigns itself (max existing id + 1) so callers (the File
Manager UI) have a stable handle that survives a rename."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import debuglog  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "packaging"))
import credentials_crypto  # noqa: E402

log = debuglog.setup("filemanager.remote_connections")

CREDENTIALS_PATH = Path(__file__).parent / "remote_connections.json"

PROTOCOLS = ("ftp", "sftp", "webdav")

DEFAULT_PORTS = {"ftp": 21, "sftp": 22, "webdav": 443}


def list_connections(protocol=None):
	"""Every saved connection, optionally filtered to one protocol - []
	if none have ever been saved yet. Never raises."""
	connections = credentials_crypto.decrypt_json(CREDENTIALS_PATH).get("connections", [])
	if protocol:
		connections = [c for c in connections if c.get("protocol") == protocol]
	return connections


def get_connection(connection_id):
	for c in list_connections():
		if c.get("id") == connection_id:
			return c
	return None


def save_connection(protocol, name, host, port, username, password, connection_id=None):
	"""Adds a new connection, or updates one in place if connection_id
	matches an existing saved entry. Returns the connection's id."""
	data = credentials_crypto.decrypt_json(CREDENTIALS_PATH)
	connections = data.get("connections", [])
	entry = {
		"protocol": protocol,
		"name": name,
		"host": host,
		"port": port,
		"username": username,
		"password": password,
	}
	if connection_id is not None:
		for i, c in enumerate(connections):
			if c.get("id") == connection_id:
				entry["id"] = connection_id
				connections[i] = entry
				break
		else:
			connection_id = None  # fall through to "add new" below - the id no longer exists
	if connection_id is None:
		connection_id = max((c.get("id", 0) for c in connections), default=0) + 1
		entry["id"] = connection_id
		connections.append(entry)
	data["connections"] = connections
	credentials_crypto.encrypt_json(data, CREDENTIALS_PATH)
	log.info(f"saved connection id={connection_id} protocol={protocol} name={name!r} host={host!r}")
	return connection_id


def delete_connection(connection_id):
	data = credentials_crypto.decrypt_json(CREDENTIALS_PATH)
	connections = data.get("connections", [])
	remaining = [c for c in connections if c.get("id") != connection_id]
	data["connections"] = remaining
	credentials_crypto.encrypt_json(data, CREDENTIALS_PATH)
	log.info(f"deleted connection id={connection_id}")
