"""File flush and atomic replacement primitives, without power-loss guarantees.

POSIX directory fsync is requested after rename. Windows Python cannot portably
flush directory metadata; supported recovery is process failure, not power loss.
"""
from __future__ import annotations

import os
import uuid
from pathlib import Path


def sync_directory(directory: Path):
    if os.name == "nt":
        return
    descriptor = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def write_bytes(path: Path, data: bytes):
    with path.open("wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    sync_directory(path.parent)


def atomic_bytes(path: Path, data: bytes):
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        write_bytes(temporary, data)
        temporary.replace(path)
        sync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)
