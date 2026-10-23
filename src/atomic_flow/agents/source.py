"""Confined, revision-checked source I/O shared by tools and undo."""
from __future__ import annotations

import hashlib
import stat
import uuid
from pathlib import Path


class SourceConflict(OSError):
    pass


class SourcePreconditionFailed(SourceConflict):
    """Trusted tool preflight failed before every mutation/execution boundary.

    Only raise in a host-owned preflight block, never from action/rollback code.
    Remains a SourceConflict for callers of the direct source tool API.
    """


def digest(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


def confined_path(root: Path, rel: str) -> Path:
    root = root.resolve()
    part = Path(rel)
    if part.is_absolute() or part.drive or not part.parts or ".." in part.parts:
        raise SourceConflict("source path escapes project root")
    target = root / part
    if not target.resolve().is_relative_to(root):
        raise SourceConflict("source path escapes project root")
    cursor = root
    for item in part.parts:
        cursor /= item
        if cursor.is_symlink() or getattr(cursor, "is_junction", lambda: False)():
            raise SourceConflict("symlink/junction source paths are not permitted")
    return target


def read_checked(root: Path, rel: str, expected: str) -> bytes:
    data = confined_path(root, rel).read_bytes()
    if not expected or digest(data) != expected:
        raise SourceConflict(f"stale source: {rel}; refresh the index before editing or reading")
    return data


def replace_checked(root: Path, rel: str, data: bytes, expected: str | None) -> None:
    target = confined_path(root, rel)

    def check():
        current = digest(target.read_bytes()) if target.exists() else None
        if current != expected:
            raise SourceConflict(f"stale source: {rel}; refusing to overwrite concurrent changes")

    check()
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(target.name + f".{uuid.uuid4().hex}.atomic-tmp")
    try:
        temp.write_bytes(data)
        if target.exists():
            temp.chmod(stat.S_IMODE(target.stat().st_mode))
        check()
        confined_path(root, rel)
        temp.replace(target)
    finally:
        temp.unlink(missing_ok=True)
