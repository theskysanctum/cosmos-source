#!/usr/bin/env python3
"""WebDAV backend for File Manager's remote sources (see filemanager/
remote_connections.py for saved connection storage, filemanager/
remote_ftp.py and remote_sftp.py for the other two protocols) - a thin
wrapper over the webdavclient3 package's Client. Same "fresh client per
call, never raises past this module's own boundary" shape as
remote_ftp.py/remote_sftp.py (see remote_ftp.py's own docstring for why
a short-lived per-call connection beats trying to keep one open across
unrelated GTK-driven UI actions - WebDAV is stateless over HTTP anyway,
so there's no real session cost to opening a fresh Client every time)."""
import sys
from pathlib import Path

from webdav3.client import Client
from webdav3.exceptions import WebDavException

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import debuglog  # noqa: E402

log = debuglog.setup("filemanager.remote_webdav")


def _client(connection):
	scheme = "https" if int(connection.get("port") or 443) == 443 else "http"
	hostname = f"{scheme}://{connection['host']}:{connection.get('port') or 443}"
	return Client(
		{
			"webdav_hostname": hostname,
			"webdav_login": connection.get("username") or "",
			"webdav_password": connection.get("password") or "",
			"webdav_timeout": 10,
		}
	)


def list_dir(connection, path):
	"""Returns (dirs, files, error) - same shape as remote_ftp.list_dir()/
	remote_sftp.list_dir() and filemanager/files.py's own list_dir()."""
	path = path or "/"
	try:
		client = _client(connection)
		entries = client.list(path, get_info=True)
		dirs, files = [], []
		for entry in entries:
			name = (entry.get("name") or "").rstrip("/")
			if not name:
				continue
			entry_path = f"{path.rstrip('/')}/{name}"
			if entry.get("isdir"):
				dirs.append({"name": name, "path": entry_path})
			else:
				try:
					size = int(entry.get("size") or 0)
				except (TypeError, ValueError):
					size = 0
				files.append({"name": name, "path": entry_path, "size": size})
		dirs.sort(key=lambda d: d["name"].lower())
		files.sort(key=lambda f: f["name"].lower())
		return dirs, files, None
	except (WebDavException, OSError) as e:
		log.warning(f"list_dir: listing {path} on {connection.get('host')} failed: {e}")
		return [], [], f"Couldn't list this directory: {e}"


def download(connection, remote_path, local_dest_dir):
	name = remote_path.rstrip("/").split("/")[-1]
	dest = Path(local_dest_dir) / name
	try:
		client = _client(connection)
		client.download_sync(remote_path=remote_path, local_path=str(dest))
		log.info(f"downloaded {connection.get('host')}:{remote_path} -> {dest}")
		return None
	except (WebDavException, OSError) as e:
		log.warning(f"download of {remote_path} failed: {e}")
		return f"Download failed: {e}"


def upload(connection, local_path, remote_dest_dir):
	local_path = Path(local_path)
	remote_path = f"{remote_dest_dir.rstrip('/')}/{local_path.name}"
	try:
		client = _client(connection)
		client.upload_sync(remote_path=remote_path, local_path=str(local_path))
		log.info(f"uploaded {local_path} -> {connection.get('host')}:{remote_path}")
		return None
	except (WebDavException, OSError) as e:
		log.warning(f"upload of {local_path} failed: {e}")
		return f"Upload failed: {e}"


def delete(connection, path, is_dir):
	try:
		client = _client(connection)
		client.clean(path)
		return None
	except (WebDavException, OSError) as e:
		log.warning(f"delete of {path} failed: {e}")
		return f"Delete failed: {e}"


def rename(connection, path, new_name):
	parent = path.rsplit("/", 1)[0] or "/"
	new_path = f"{parent.rstrip('/')}/{new_name}"
	try:
		client = _client(connection)
		client.move(remote_path_from=path, remote_path_to=new_path)
		return None
	except (WebDavException, OSError) as e:
		log.warning(f"rename of {path} failed: {e}")
		return f"Rename failed: {e}"


def make_dir(connection, parent, name):
	new_path = f"{parent.rstrip('/')}/{name}"
	try:
		client = _client(connection)
		client.mkdir(new_path)
		return None
	except (WebDavException, OSError) as e:
		log.warning(f"make_dir {new_path} failed: {e}")
		return f"Couldn't create folder: {e}"
