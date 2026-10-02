#!/usr/bin/env python3
"""Builds a tiny but valid XDVDFS (Xbox disc) image for tests: files={name: bytes}, the directory a balanced binary tree like the real thing.
base is where the volume starts (0 for a plain XISO; the others are the game-partition offsets of full dumps)."""
import struct

SECTOR = 2048
MAGIC = b"MICROSOFT*XBOX*MEDIA"


def build(files, base=0):
    names = sorted(files, key=str.lower)
    root_sector = 33
    sector = root_sector + 1
    entries = []                                   # (name, first sector, size), sorted by name
    for name in names:
        entries.append((name, sector, len(files[name])))
        sector += max(1, (len(files[name]) + SECTOR - 1) // SECTOR)

    nodes = {}                                     # index -> (left index or None, right index or None)

    def place(lo, hi):
        if lo > hi:
            return None
        mid = (lo + hi) // 2
        nodes[mid] = (place(lo, mid - 1), place(mid + 1, hi))
        return mid

    root = place(0, len(entries) - 1)
    offsets, at = {}, 0
    for index in sorted(nodes, key=lambda i: (i != root, i)):      # the root first: the walk starts at offset 0
        offsets[index] = at
        at += (14 + len(entries[index][0]) + 3) & ~3
    assert at <= SECTOR, "directory too big for this helper"
    directory = bytearray(b"\xff" * SECTOR)
    for index, (left, right) in nodes.items():
        name, first, size = entries[index]
        at = offsets[index]
        struct.pack_into("<HHIIBB", directory, at, 0 if left is None else offsets[left] // 4, 0 if right is None else offsets[right] // 4,
                         first, size, 0x20, len(name))
        directory[at + 14:at + 14 + len(name)] = name.encode("ascii")
    volume = bytearray(SECTOR)
    volume[0:20] = MAGIC
    struct.pack_into("<II", volume, 20, root_sector, SECTOR)
    volume[0x7EC:0x7EC + 20] = MAGIC
    image = bytearray(base + sector * SECTOR)
    image[base + 32 * SECTOR:base + 33 * SECTOR] = volume
    image[base + root_sector * SECTOR:base + (root_sector + 1) * SECTOR] = directory
    for name, first, size in entries:
        image[base + first * SECTOR:base + first * SECTOR + size] = files[name]
    return bytes(image)


def write(path, files, base=0):
    """Writes the image to `path` as a sparse file: only the volume itself is stored, so a fake full dump (whose volume starts 400 MB in) costs
    kilobytes of disk instead of 400 MB."""
    volume = build(files, 0)
    with open(path, "wb") as fh:
        fh.seek(base)
        fh.write(volume)
