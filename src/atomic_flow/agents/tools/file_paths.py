"""Shared admission rules for bounded source-file tools."""
from __future__ import annotations

import fnmatch
import stat
from pathlib import PurePosixPath

from atomic_flow.agents.source import SourceConflict, confined_path
from atomic_flow.config import user_dir
from atomic_flow.tree.builder import _Ignore
from atomic_flow.tree.defaults import DEFAULT_IGNORE, MAX_FILE_BYTES, SOURCE_SUFFIXES


def admitted_path(ctx, rel: str, *, existing: bool = False):
    # Reject OS aliases, alternate data streams, hidden/private files and paths
    # which the project's index intentionally excludes.
    if not rel or "\\" in rel or ":" in rel or rel != PurePosixPath(rel).as_posix():
        raise SourceConflict("use a normalized project-relative POSIX path")
    path = confined_path(ctx.project_root, rel)
    if path.resolve().is_relative_to(user_dir().resolve()) and not ctx.private_workspace:
        raise SourceConflict("private application state is not source")
    ignore = _Ignore(ctx.project_root, list(DEFAULT_IGNORE) + list(ctx.tree_ignore or []), ctx.respect_gitignore)
    parts = PurePosixPath(rel).parts
    if any((part.startswith(".") and part.casefold() != ".github") or part.endswith((".", " "))
           or part.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
                                             *(f"LPT{i}" for i in range(1, 10))} for part in parts):
        raise SourceConflict("hidden/private or ambiguous source path")
    if any(ignore("/".join(parts[:i + 1]), i < len(parts) - 1) for i in range(len(parts))):
        raise SourceConflict("path is excluded by project source rules")
    if any(fnmatch.fnmatch(part.lower(), pattern) for part in parts
           for pattern in ("credentials*", "secrets*", "*private_key*", "*.pem", "*.key", "*.enc", "*.p12", "*.pfx")):
        raise SourceConflict("credential/private paths are not source")
    if path.suffix.lower() not in SOURCE_SUFFIXES:
        raise SourceConflict("file type is not supported by the source index")
    if path.exists():
        info = path.stat()
        if not stat.S_ISREG(info.st_mode):
            raise SourceConflict("source path is not a regular file")
        if info.st_size > MAX_FILE_BYTES:
            raise SourceConflict("source file exceeds the size limit")
    elif existing:
        raise SourceConflict("source file does not exist")
    return path


def text_bytes(text: str) -> bytes:
    data = text.encode("utf-8")
    if b"\x00" in data or len(data) > MAX_FILE_BYTES:
        raise SourceConflict("source must be bounded UTF-8 text without NUL bytes")
    return data
