"""Journaled file-tool writes and compare-before-restore multi-path undo.

This is recoverable source I/O, not an OS atomic multi-file transaction.
"""
from __future__ import annotations

import json
import re
import stat
import time
import uuid
from pathlib import Path

from atomic_flow.agents.source import SourceConflict, confined_path, digest, replace_checked


def _current(root: Path, rel: str) -> str | None:
    path = confined_path(root, rel)
    return digest(path.read_bytes()) if path.exists() else None


def _write(root: Path, rel: str, data: bytes | None, expected: str | None):
    if data is not None:
        replace_checked(root, rel, data, expected)
    else:
        path = confined_path(root, rel)
        if _current(root, rel) != expected:
            raise SourceConflict(f"concurrent source change: {rel}")
        if path.exists():
            path.unlink()


def _save(folder: Path, state: dict):
    target = folder / "file-transaction.json"
    temp = target.with_suffix(".json.tmp")
    temp.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    temp.replace(target)


def begin(storage: Path, root: Path, before: dict[str, bytes | None], after: dict[str, bytes | None]) -> tuple[str, dict]:
    """Persist intents and verified backups before the first source mutation."""
    if not before or before.keys() != after.keys():
        raise SourceConflict("invalid transaction paths")
    edit_id = "fileedit_" + uuid.uuid4().hex[:12]
    folder = storage / edit_id
    folder.mkdir(parents=True)
    changes = {}
    for index, (rel, data) in enumerate(before.items()):
        expected = digest(data) if data is not None else None
        if _current(root, rel) != expected:
            raise SourceConflict(f"source changed before transaction: {rel}")
        backup = f"before-{index}"
        if data is not None:
            (folder / backup).write_bytes(data)
        changes[rel] = {"before": expected, "after": digest(after[rel]) if after[rel] is not None else None, "backup": backup,
                        "before_mode": stat.S_IMODE(confined_path(root, rel).stat().st_mode) if data is not None else None}
    state = {"edit_id": edit_id, "project": str(root.resolve()), "timestamp": time.time(),
             "status": "writing", "changes": changes}
    _save(folder, state)
    return edit_id, state


def apply(storage: Path, root: Path, edit_id: str, state: dict, after: dict[str, bytes | None], cancel_check,
          after_modes: dict[str, int] | None = None):
    for rel, data in after.items():
        cancel_check()
        _write(root, rel, data, state["changes"][rel]["before"])
        if data is not None and after_modes and rel in after_modes:
            confined_path(root, rel).chmod(after_modes[rel])
    state["status"] = "verifying"
    _save(storage / edit_id, state)


def finish(storage: Path, edit_id: str, state: dict):
    state["status"] = "applied"
    _save(storage / edit_id, state)


def undo(storage: Path, root: Path, edit_id: str, *, interrupted: bool = False) -> dict | None:
    """Undo own bytes, preserving third-party versions. Supports interrupted writes."""
    if not re.fullmatch(r"fileedit_[a-f0-9]{12}", edit_id):
        return None
    folder = storage / edit_id
    try:
        state = json.loads((folder / "file-transaction.json").read_text(encoding="utf-8"))
        if state["project"] != str(root.resolve()) or state["status"] == "undone":
            return None
        changes = state["changes"]
        # Preflight all paths and backups before any restore. A conflict does not
        # partially undo a move or remove a user's replacement file.
        restore = {}
        for rel, hashes in changes.items():
            current = _current(root, rel)
            if interrupted and current == hashes["before"]:
                continue
            if current != hashes["after"]:
                return None
            data = (folder / hashes["backup"]).read_bytes() if hashes["before"] is not None else None
            if data is not None and digest(data) != hashes["before"]:
                return None
            restore[rel] = data
        for rel, data in reversed(list(restore.items())):
            _write(root, rel, data, changes[rel]["after"])
            if data is not None and changes[rel].get("before_mode") is not None:
                confined_path(root, rel).chmod(changes[rel]["before_mode"])
        state["status"] = "undone"
        _save(folder, state)
        return state
    except (OSError, KeyError, TypeError, ValueError):
        return None
