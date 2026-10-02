#!/usr/bin/env python3
"""Dispatches to the right protocol backend (remote_ftp.py/remote_sftp.py/
remote_webdav.py) by a connection's own "protocol" field, so callers
(launcher/app.py's File Manager) never need to branch on protocol
themselves - one call site per operation regardless of which of the
three a given pane's connection actually uses."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import remote_ftp  # noqa: E402
import remote_sftp  # noqa: E402
import remote_webdav  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import debuglog  # noqa: E402

log = debuglog.setup("filemanager.remote")

_BACKENDS = {"ftp": remote_ftp, "sftp": remote_sftp, "webdav": remote_webdav}


def _backend(connection):
	return _BACKENDS[connection["protocol"]]


def list_dir(connection, path):
	return _backend(connection).list_dir(connection, path)


def download(connection, remote_path, local_dest_dir):
	return _backend(connection).download(connection, remote_path, local_dest_dir)


def upload(connection, local_path, remote_dest_dir):
	return _backend(connection).upload(connection, local_path, remote_dest_dir)


def delete(connection, path, is_dir):
	return _backend(connection).delete(connection, path, is_dir)


def rename(connection, path, new_name):
	return _backend(connection).rename(connection, path, new_name)


def make_dir(connection, parent, name):
	return _backend(connection).make_dir(connection, parent, name)
