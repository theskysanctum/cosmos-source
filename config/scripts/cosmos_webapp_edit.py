"""Editing a web app's shortcut: rename it, change its icon, remove it. The pure logic behind the launcher's right-click dialog on the Web tab,
kept out of cosmos-launcher.py so it can be tested without a display.

A web app is ~/.local/share/applications/brave-<slug>.desktop with its icon at ~/.local/share/icons/hicolor/128x128/apps/brave-<slug>.png
(Icon=brave-<slug>). Nothing here touches any other file: every function checks the path really is one of those, and the .desktop file is
rewritten line by line so any other key (X-CosmOS-Default, Exec, ...) survives exactly as it was.

Icons are only accepted when they are the size every icon in that folder is: a PNG of exactly ICON_PX x ICON_PX. Anything else is refused with a
sentence saying why (wrong size and its actual size, not a PNG, damaged, too large a file) and the current icon is left untouched - a wrongly
sized image would come out blurred or cropped in the grid and the bar.
"""
import os
import re
import struct
import zlib

ICON_PX = 128
MAX_ICON_BYTES = 2 * 1024 * 1024
MAX_NAME_CHARS = 60
APP_DIR = os.path.expanduser("~/.local/share/applications")
ICON_DIR = os.path.expanduser("~/.local/share/icons/hicolor/128x128/apps")
DESKTOP_NAME = re.compile(r"^brave-([a-z0-9][a-z0-9-]*)\.desktop$")
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


class EditError(Exception):
    """A sentence for the person: why the change wasn't made."""


def slug_of(desktop_path, app_dir=APP_DIR):
    """The slug of a web app's .desktop file, or raise EditError if this isn't one of the web app shortcuts in the user's applications folder."""
    match = DESKTOP_NAME.match(os.path.basename(desktop_path))
    if not match or os.path.realpath(os.path.dirname(desktop_path)) != os.path.realpath(app_dir) or not os.path.isfile(desktop_path):
        raise EditError("That isn't one of your web app shortcuts.")
    return match.group(1)


def icon_path_for(slug, icon_dir=ICON_DIR):
    return os.path.join(icon_dir, f"brave-{slug}.png")


# ---- names ---------------------------------------------------------------------------------------------------------------------------

def clean_name(name):
    name = " ".join((name or "").split())                      # trims and collapses every kind of white space, newlines included
    if not name:
        raise EditError("The name can't be empty.")
    if len(name) > MAX_NAME_CHARS:
        raise EditError(f"The name is too long (at most {MAX_NAME_CHARS} characters).")
    if any(ord(c) < 32 or ord(c) == 127 for c in name):
        raise EditError("The name has characters that can't be used.")
    return name


def _read_lines(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read().split("\n")


def _write_atomic(path, text):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, path)


def _set_key(lines, key, value):
    """The lines with `key=` in [Desktop Entry] set to value (added at the end of that group if missing). Other groups and keys are untouched,
    and so are translated keys like Name[de]=."""
    out, in_main, done = [], False, False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("["):
            if in_main and not done:
                out.append(f"{key}={value}")
                done = True
            in_main = stripped == "[Desktop Entry]"
        elif in_main and line.startswith(key + "="):
            if not done:
                out.append(f"{key}={value}")
                done = True
            continue                                            # a duplicate of the same key is dropped
        out.append(line)
    if not done:
        if out and out[-1] == "":
            out.insert(len(out) - 1, f"{key}={value}")
        else:
            out.append(f"{key}={value}")
    return out


def rename(desktop_path, new_name, app_dir=APP_DIR):
    """Change the name shown for a web app. The file (and so its id, and any favorite) keeps its name. Returns the cleaned name."""
    slug_of(desktop_path, app_dir)
    name = clean_name(new_name)
    _write_atomic(desktop_path, "\n".join(_set_key(_read_lines(desktop_path), "Name", name)))
    return name


# ---- icons ---------------------------------------------------------------------------------------------------------------------------

def png_problem(data, size=ICON_PX):
    """None if `data` is a sound PNG of exactly size x size, else the reason why not, as a sentence."""
    if len(data) > MAX_ICON_BYTES:
        return f"That file is too large ({len(data) // 1024} KB; at most {MAX_ICON_BYTES // 1024} KB)."
    if not data.startswith(PNG_SIGNATURE):
        return f"That isn't a PNG image. Icons must be PNG files, exactly {size} x {size} pixels."
    pos, header, idat, ended = len(PNG_SIGNATURE), None, [], False
    while pos + 8 <= len(data):
        length, kind = struct.unpack(">I4s", data[pos:pos + 8])
        body = data[pos + 8:pos + 8 + length]
        if len(body) < length or pos + 12 + length > len(data):
            return "That PNG is damaged (it ends early)."
        if zlib.crc32(kind + body) & 0xFFFFFFFF != struct.unpack(">I", data[pos + 8 + length:pos + 12 + length])[0]:
            return "That PNG is damaged (a checksum doesn't match)."
        if kind == b"IHDR" and length == 13 and header is None:
            header = struct.unpack(">IIBBBBB", body)
        elif kind == b"IDAT":
            idat.append(body)
        elif kind == b"IEND":
            ended = True
            break
        pos += 12 + length
    if header is None:
        return "That PNG is damaged (it has no header)."
    width, height = header[0], header[1]
    if (width, height) != (size, size):
        shape = "square" if width == height else "not square"
        return f"That image is {width} x {height} pixels ({shape}). Icons must be exactly {size} x {size}."
    if not ended or not idat:
        return "That PNG is damaged (it has no picture data or no end)."
    try:
        zlib.decompress(b"".join(idat))
    except zlib.error:
        return "That PNG is damaged (its picture data can't be read)."
    return None


def check_icon_file(path, size=ICON_PX):
    """Raise EditError unless `path` is a usable icon; returns the file's bytes if it is."""
    try:
        if not os.path.isfile(path):
            raise EditError("That isn't a file.")
        if os.path.getsize(path) > MAX_ICON_BYTES:
            raise EditError(f"That file is too large (at most {MAX_ICON_BYTES // 1024} KB).")
        with open(path, "rb") as fh:
            data = fh.read()
    except OSError as exc:
        raise EditError(f"Couldn't read that file ({exc.strerror or exc}).")
    problem = png_problem(data, size)
    if problem:
        raise EditError(problem)
    return data


def set_icon(desktop_path, image_path, app_dir=APP_DIR, icon_dir=ICON_DIR):
    """Use `image_path` as the web app's icon. The image is checked first (see png_problem) and nothing changes if it fails. The old icon is
    only replaced once the new file is completely written. Returns the path of the icon now in use."""
    slug = slug_of(desktop_path, app_dir)
    data = check_icon_file(image_path)
    os.makedirs(icon_dir, exist_ok=True)
    target = icon_path_for(slug, icon_dir)
    tmp = target + ".tmp"
    with open(tmp, "wb") as fh:
        fh.write(data)
    os.replace(tmp, target)
    _write_atomic(desktop_path, "\n".join(_set_key(_read_lines(desktop_path), "Icon", f"brave-{slug}")))
    return target


# ---- removing ------------------------------------------------------------------------------------------------------------------------

def is_default(desktop_path):
    """True for the built-in web apps (the X-CosmOS-Default key), which can be renamed and re-iconed but not removed."""
    try:
        return any(line.strip() == "X-CosmOS-Default=true" for line in _read_lines(desktop_path))
    except OSError:
        return False


def remove(desktop_path, app_dir=APP_DIR, icon_dir=ICON_DIR):
    """Delete a web app: its shortcut and its icon file. Built-in defaults are refused."""
    slug = slug_of(desktop_path, app_dir)
    if is_default(desktop_path):
        raise EditError("Built-in web apps can't be removed.")
    os.unlink(desktop_path)
    try:
        os.unlink(icon_path_for(slug, icon_dir))
    except FileNotFoundError:
        pass
