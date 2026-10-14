"""Persistent filesystem backups plus a fast in-memory undo stack."""
from __future__ import annotations

import hashlib
import json
import shutil
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass
class EditRecord:
    edit_id: str
    rel_path: str
    node_id: str
    old_source: str
    new_source: str
    timestamp: float
    backup: Path | None = None


class UndoStack:
    def __init__(self, storage_dir: Path):
        self.storage_dir = storage_dir
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self._memory: list[EditRecord] = []

    def _new_id(self, rel_path: str) -> str:
        digest = hashlib.sha1(f"{rel_path}:{time.time_ns()}".encode()).hexdigest()[:12]
        return f"edit_{digest}"

    def push(self, rel_path: str, node_id: str, old_source: str,
             new_source: str, *, project_root: Path) -> EditRecord:
        edit_id = self._new_id(rel_path)
        backup_dir = self.storage_dir / edit_id
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup = backup_dir / "original.py"
        try:
            shutil.copy2(project_root / rel_path, backup)
        except OSError:
            backup = None
        record = EditRecord(edit_id, rel_path, node_id, old_source, new_source, time.time(), backup)
        (backup_dir / "meta.json").write_text(json.dumps({
            "edit_id": edit_id, "rel_path": rel_path, "node_id": node_id,
            "timestamp": record.timestamp,
        }, indent=2), encoding="utf-8")
        self._memory.append(record)
        return record

    def _record_from_dir(self, path: Path) -> EditRecord | None:
        try:
            meta = json.loads((path / "meta.json").read_text(encoding="utf-8"))
            backup = path / "original.py"
            if not backup.exists():
                return None
            return EditRecord(meta["edit_id"], meta["rel_path"], meta["node_id"],
                              backup.read_text(encoding="utf-8"), "", meta["timestamp"], backup)
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            return None

    def undo(self, project_root: Path, edit_id: str = "last") -> EditRecord | None:
        if edit_id == "last" and self._memory:
            record = self._memory.pop()
        else:
            if edit_id == "last":
                candidates = []
                for path in self.storage_dir.iterdir():
                    if path.is_dir() and (path / "meta.json").exists():
                        record = self._record_from_dir(path)
                        if record is not None:
                            candidates.append(record)
                record = max(candidates, key=lambda item: item.timestamp, default=None)
                if record is None:
                    return None
            else:
                path = self.storage_dir / edit_id
                record = self._record_from_dir(path)
            if record is None:
                return None
        if record.backup is None or not record.backup.exists():
            return None
        target = project_root / record.rel_path
        temp = target.with_suffix(target.suffix + ".undo.tmp")
        shutil.copyfile(record.backup, temp)
        temp.replace(target)
        return record

    def list(self) -> list[EditRecord]:
        return list(self._memory)
