"""File operations for cosmos-files. Every path goes through a Scope first, so nothing here can
read or write outside the places the file manager shows - including through a symlink.

Copies and moves keep symlinks as symlinks (they are never followed), and a symlink whose target
leaves the scope is not listed at all (see list_dir), so it cannot be opened or descended into."""
import os
import shutil
import time

from cosmos_places import OutOfScope


def unique_path(directory, name):
    """directory/name, or "name (2)", "name (3)" ... when that already exists."""
    stem, ext = os.path.splitext(name) if not os.path.isdir(os.path.join(directory, name)) else (name, "")
    candidate, n = os.path.join(directory, name), 1
    while os.path.lexists(candidate):
        n += 1
        candidate = os.path.join(directory, f"{stem} ({n}){ext}")
    return candidate


def list_dir(scope, directory, show_hidden=False):
    """[(name, real path, is_dir, size, mtime)] for what is inside `directory`, folders first.
    Entries that resolve outside the scope (a symlink to /, say) and unreadable ones are left out."""
    real_dir = scope.check(directory)
    rows = []
    try:
        entries = list(os.scandir(real_dir))
    except OSError:
        return []
    for entry in entries:
        if not show_hidden and entry.name.startswith("."):
            continue
        found = scope.locate(entry.path)
        if not found:
            continue
        try:
            st = os.stat(found[1])
        except OSError:
            continue
        is_dir = os.path.isdir(found[1])
        rows.append((entry.name, found[1], is_dir, 0 if is_dir else st.st_size, st.st_mtime))
    rows.sort(key=lambda r: (not r[2], r[0].lower()))
    return rows


def new_folder(scope, directory, name="New folder"):
    path = unique_path(scope.check(directory), name)
    os.mkdir(path)
    return path


def rename(scope, path, new_name):
    real = scope.check(path)
    if not new_name or os.sep in new_name or new_name in (".", ".."):
        raise ValueError("That is not a valid name.")
    target = os.path.join(os.path.dirname(real), new_name)
    scope.check(os.path.dirname(target))
    if os.path.lexists(target):
        raise FileExistsError(new_name)
    os.rename(real, target)
    return target


def copy_into(scope, source, directory):
    src, dest_dir = scope.check(source), scope.check(directory)
    if os.path.isdir(src) and (dest_dir == src or dest_dir.startswith(src + os.sep)):
        raise ValueError("A folder can't be copied into itself.")
    target = unique_path(dest_dir, os.path.basename(src))
    if os.path.isdir(src):
        shutil.copytree(src, target, symlinks=True)
    else:
        shutil.copy2(src, target)
    return target


def move_into(scope, source, directory):
    src, dest_dir = scope.check(source), scope.check(directory)
    if os.path.isdir(src) and (dest_dir == src or dest_dir.startswith(src + os.sep)):
        raise ValueError("A folder can't be moved into itself.")
    if os.path.dirname(src) == dest_dir:
        return src
    target = unique_path(dest_dir, os.path.basename(src))
    shutil.move(src, target)
    return target


def delete(scope, path):
    """Permanently delete (the UI tries the trash first). Symlinks are removed, never followed."""
    real = scope.check(path)
    if os.path.islink(path):
        os.unlink(path)
    elif os.path.isdir(real):
        shutil.rmtree(real)
    else:
        os.unlink(real)


def human_size(n):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1000 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1000.0


def human_time(ts):
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))
