#!/usr/bin/env python3
"""FTP backend for File Manager's remote sources (see filemanager/
remote_connections.py for saved connection storage, filemanager/
remote_sftp.py and remote_webdav.py for the other two protocols) - a
thin wrapper over stdlib ftplib (no new dependency). Every function
takes a `connection` dict (as returned by remote_connections.list_
connections()/get_connection()) and opens a fresh, short-lived FTP
session per call - FTP has no useful "keep this handle open across
several unrelated GTK-driven UI actions" story once idle-timeout and
NAT/passive-mode reconnects are accounted for, so a plain connect-do-
one-thing-disconnect per call is both simpler and more robust than
trying to cache a live connection.

Never raises past this module's own boundary - every function returns
its real result plus a trailing None-or-error-string, exactly the
"never crash, log and return a sentinel" contract already established
this project for every other network-backed module (Wi-Fi, Dropbox,
GitHub, ScreenScraper); a bad host/port/credential is an everyday user
mistake here, not a program bug."""
import sys
from ftplib import FTP, error_perm
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import debuglog  # noqa: E402

log = debuglog.setup("filemanager.remote_ftp")


def _connect(connection):
	ftp = FTP()
	ftp.connect(connection["host"], int(connection.get("port") or 21), timeout=10)
	ftp.login(connection.get("username") or "anonymous", connection.get("password") or "")
	return ftp


def list_dir(connection, path):
	"""Returns (dirs, files, error) - same {"name","path"}/{"name","path",
	"size"} shape filemanager/files.py's own list_dir() uses, so the File
	Manager UI can build rows identically regardless of source kind."""
	path = path or "/"
	try:
		ftp = _connect(connection)
	except (OSError, error_perm) as e:
		log.warning(f"list_dir: connect/login to {connection.get('host')} failed: {e}")
		return [], [], f"Couldn't connect: {e}"
	try:
		ftp.cwd(path)
		dirs, files = [], []
		entries = list(ftp.mlsd()) if _supports_mlsd(ftp) else _list_dir_fallback(ftp)
		for name, facts in entries:
			if name in (".", ".."):
				continue
			entry_path = f"{path.rstrip('/')}/{name}"
			if facts.get("type") == "dir":
				dirs.append({"name": name, "path": entry_path})
			else:
				try:
					size = int(facts.get("size", 0))
				except (TypeError, ValueError):
					size = 0
				files.append({"name": name, "path": entry_path, "size": size})
		dirs.sort(key=lambda d: d["name"].lower())
		files.sort(key=lambda f: f["name"].lower())
		return dirs, files, None
	except (OSError, error_perm) as e:
		log.warning(f"list_dir: listing {path} on {connection.get('host')} failed: {e}")
		return [], [], f"Couldn't list this directory: {e}"
	finally:
		_quit(ftp)


def _supports_mlsd(ftp):
	try:
		next(iter(ftp.mlsd()), None)
		return True
	except Exception:
		return False


def _list_dir_fallback(ftp):
	"""Some real FTP servers still don't support MLSD (RFC 3659) - fall
	back to a plain NLST + a per-entry type probe via a failed CWD,
	slower but works against a genuinely old/minimal server."""
	entries = []
	for name in ftp.nlst():
		name = name.split("/")[-1]
		is_dir = False
		cwd_before = ftp.pwd()
		try:
			ftp.cwd(name)
			is_dir = True
			ftp.cwd(cwd_before)
		except error_perm:
			is_dir = False
		size = 0
		if not is_dir:
			try:
				size = ftp.size(name) or 0
			except error_perm:
				size = 0
		entries.append((name, {"type": "dir" if is_dir else "file", "size": size}))
	return entries


def _quit(ftp):
	try:
		ftp.quit()
	except OSError:
		ftp.close()


def download(connection, remote_path, local_dest_dir):
	"""Downloads one remote file into local_dest_dir (a real local
	directory) under its own name. Returns None on success, else an
	error string - directories aren't recursed into here (the File
	Manager UI only ever offers this action on a "file" row)."""
	name = remote_path.rstrip("/").split("/")[-1]
	dest = Path(local_dest_dir) / name
	try:
		ftp = _connect(connection)
	except (OSError, error_perm) as e:
		return f"Couldn't connect: {e}"
	try:
		with open(dest, "wb") as f:
			ftp.retrbinary(f"RETR {remote_path}", f.write)
		log.info(f"downloaded {connection.get('host')}:{remote_path} -> {dest}")
		return None
	except (OSError, error_perm) as e:
		log.warning(f"download of {remote_path} failed: {e}")
		return f"Download failed: {e}"
	finally:
		_quit(ftp)


def upload(connection, local_path, remote_dest_dir):
	"""Uploads one local file into remote_dest_dir under its own name.
	Returns None on success, else an error string."""
	local_path = Path(local_path)
	remote_path = f"{remote_dest_dir.rstrip('/')}/{local_path.name}"
	try:
		ftp = _connect(connection)
	except (OSError, error_perm) as e:
		return f"Couldn't connect: {e}"
	try:
		with open(local_path, "rb") as f:
			ftp.storbinary(f"STOR {remote_path}", f)
		log.info(f"uploaded {local_path} -> {connection.get('host')}:{remote_path}")
		return None
	except (OSError, error_perm) as e:
		log.warning(f"upload of {local_path} failed: {e}")
		return f"Upload failed: {e}"
	finally:
		_quit(ftp)


def delete(connection, path, is_dir):
	try:
		ftp = _connect(connection)
	except (OSError, error_perm) as e:
		return f"Couldn't connect: {e}"
	try:
		if is_dir:
			ftp.rmd(path)
		else:
			ftp.delete(path)
		return None
	except (OSError, error_perm) as e:
		log.warning(f"delete of {path} failed: {e}")
		return f"Delete failed: {e}"
	finally:
		_quit(ftp)


def rename(connection, path, new_name):
	parent = path.rsplit("/", 1)[0] or "/"
	new_path = f"{parent.rstrip('/')}/{new_name}"
	try:
		ftp = _connect(connection)
	except (OSError, error_perm) as e:
		return f"Couldn't connect: {e}"
	try:
		ftp.rename(path, new_path)
		return None
	except (OSError, error_perm) as e:
		log.warning(f"rename of {path} failed: {e}")
		return f"Rename failed: {e}"
	finally:
		_quit(ftp)


def make_dir(connection, parent, name):
	new_path = f"{parent.rstrip('/')}/{name}"
	try:
		ftp = _connect(connection)
	except (OSError, error_perm) as e:
		return f"Couldn't connect: {e}"
	try:
		ftp.mkd(new_path)
		return None
	except (OSError, error_perm) as e:
		log.warning(f"make_dir {new_path} failed: {e}")
		return f"Couldn't create folder: {e}"
	finally:
		_quit(ftp)
