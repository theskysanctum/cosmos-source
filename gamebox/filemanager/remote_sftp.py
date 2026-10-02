#!/usr/bin/env python3
"""SFTP backend for File Manager's remote sources (see filemanager/
remote_connections.py for saved connection storage, filemanager/
remote_ftp.py and remote_webdav.py for the other two protocols) - a
thin wrapper over paramiko's SFTPClient. Same "fresh connection per
call, never raises past this module's own boundary" shape as
remote_ftp.py (see that module's own docstring for why a short-lived
per-call connection beats trying to keep one open across unrelated
GTK-driven UI actions)."""
import stat
import sys
from pathlib import Path

import paramiko

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import debuglog  # noqa: E402

log = debuglog.setup("filemanager.remote_sftp")


def _connect(connection):
	transport = paramiko.Transport((connection["host"], int(connection.get("port") or 22)))
	transport.connect(username=connection.get("username") or "", password=connection.get("password") or "")
	return transport, paramiko.SFTPClient.from_transport(transport)


def _close(transport, sftp):
	try:
		sftp.close()
	finally:
		transport.close()


def list_dir(connection, path):
	"""Returns (dirs, files, error) - same shape as remote_ftp.list_dir()
	and filemanager/files.py's own list_dir()."""
	path = path or "/"
	try:
		transport, sftp = _connect(connection)
	except (paramiko.SSHException, OSError) as e:
		log.warning(f"list_dir: connect to {connection.get('host')} failed: {e}")
		return [], [], f"Couldn't connect: {e}"
	try:
		dirs, files = [], []
		for entry in sftp.listdir_attr(path):
			entry_path = f"{path.rstrip('/')}/{entry.filename}"
			if stat.S_ISDIR(entry.st_mode):
				dirs.append({"name": entry.filename, "path": entry_path})
			else:
				files.append({"name": entry.filename, "path": entry_path, "size": entry.st_size or 0})
		dirs.sort(key=lambda d: d["name"].lower())
		files.sort(key=lambda f: f["name"].lower())
		return dirs, files, None
	except (paramiko.SSHException, OSError) as e:
		log.warning(f"list_dir: listing {path} on {connection.get('host')} failed: {e}")
		return [], [], f"Couldn't list this directory: {e}"
	finally:
		_close(transport, sftp)


def download(connection, remote_path, local_dest_dir):
	name = remote_path.rstrip("/").split("/")[-1]
	dest = Path(local_dest_dir) / name
	try:
		transport, sftp = _connect(connection)
	except (paramiko.SSHException, OSError) as e:
		return f"Couldn't connect: {e}"
	try:
		sftp.get(remote_path, str(dest))
		log.info(f"downloaded {connection.get('host')}:{remote_path} -> {dest}")
		return None
	except (paramiko.SSHException, OSError) as e:
		log.warning(f"download of {remote_path} failed: {e}")
		return f"Download failed: {e}"
	finally:
		_close(transport, sftp)


def upload(connection, local_path, remote_dest_dir):
	local_path = Path(local_path)
	remote_path = f"{remote_dest_dir.rstrip('/')}/{local_path.name}"
	try:
		transport, sftp = _connect(connection)
	except (paramiko.SSHException, OSError) as e:
		return f"Couldn't connect: {e}"
	try:
		sftp.put(str(local_path), remote_path)
		log.info(f"uploaded {local_path} -> {connection.get('host')}:{remote_path}")
		return None
	except (paramiko.SSHException, OSError) as e:
		log.warning(f"upload of {local_path} failed: {e}")
		return f"Upload failed: {e}"
	finally:
		_close(transport, sftp)


def delete(connection, path, is_dir):
	try:
		transport, sftp = _connect(connection)
	except (paramiko.SSHException, OSError) as e:
		return f"Couldn't connect: {e}"
	try:
		if is_dir:
			sftp.rmdir(path)
		else:
			sftp.remove(path)
		return None
	except (paramiko.SSHException, OSError) as e:
		log.warning(f"delete of {path} failed: {e}")
		return f"Delete failed: {e}"
	finally:
		_close(transport, sftp)


def rename(connection, path, new_name):
	parent = path.rsplit("/", 1)[0] or "/"
	new_path = f"{parent.rstrip('/')}/{new_name}"
	try:
		transport, sftp = _connect(connection)
	except (paramiko.SSHException, OSError) as e:
		return f"Couldn't connect: {e}"
	try:
		sftp.rename(path, new_path)
		return None
	except (paramiko.SSHException, OSError) as e:
		log.warning(f"rename of {path} failed: {e}")
		return f"Rename failed: {e}"
	finally:
		_close(transport, sftp)


def make_dir(connection, parent, name):
	new_path = f"{parent.rstrip('/')}/{name}"
	try:
		transport, sftp = _connect(connection)
	except (paramiko.SSHException, OSError) as e:
		return f"Couldn't connect: {e}"
	try:
		sftp.mkdir(new_path)
		return None
	except (paramiko.SSHException, OSError) as e:
		log.warning(f"make_dir {new_path} failed: {e}")
		return f"Couldn't create folder: {e}"
	finally:
		_close(transport, sftp)
