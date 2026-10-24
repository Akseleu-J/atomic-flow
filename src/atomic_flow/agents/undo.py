"""Project-bound backups with compare-before-restore semantics."""
from __future__ import annotations

import json
import re
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from atomic_flow.agents.source import confined_path, digest, replace_checked
from atomic_flow.durability import atomic_bytes, write_bytes
from atomic_flow.source_lock import mutation_guard


@dataclass
class EditRecord:
    edit_id: str
    rel_path: str
    node_id: str
    old_source: str
    new_source: str
    timestamp: float
    backup: Path
    project: str
    before_hash: str
    after_hash: str


class UndoStack:
    def __init__(self, storage_dir: Path):
        self.storage_dir = storage_dir
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self._memory: list[EditRecord] = []

    def push(self, rel_path: str, node_id: str, old_source: str, new_source: str, *,
             project_root: Path, before: bytes, after: bytes) -> EditRecord:
        confined_path(project_root, rel_path)
        edit_id = "edit_" + uuid.uuid4().hex[:12]
        folder = self.storage_dir / edit_id
        folder.mkdir()
        backup = folder / "original.py"
        write_bytes(backup, before)
        record = EditRecord(edit_id, rel_path, node_id, old_source, new_source, time.time(), backup,
                            str(project_root.resolve()), digest(before), digest(after))
        self._write_meta(record)
        self._memory.append(record)
        return record

    def _write_meta(self, record: EditRecord, *, undone: bool = False):
        meta = {key: getattr(record, key) for key in (
            "edit_id", "rel_path", "node_id", "timestamp", "project", "before_hash", "after_hash",
        )}
        target = record.backup.parent / "meta.json"
        atomic_bytes(target, json.dumps({**meta, "undone": undone}, indent=2).encode("utf-8"))

    def _record_from_dir(self, path: Path) -> EditRecord | None:
        try:
            meta = json.loads((path / "meta.json").read_text(encoding="utf-8"))
            if meta.get("undone") or meta["edit_id"] != path.name:
                return None
            backup = path / "original.py"
            if digest(backup.read_bytes()) != meta["before_hash"]:
                return None
            return EditRecord(meta["edit_id"], meta["rel_path"], meta["node_id"], "", "", meta["timestamp"],
                              backup, meta["project"], meta["before_hash"], meta["after_hash"])
        except (OSError, KeyError, TypeError, ValueError):
            return None

    @mutation_guard("project_root")
    def undo(self, project_root: Path, edit_id: str = "last") -> EditRecord | None:
        if edit_id.startswith("fileedit_"):
            return self._undo_files(project_root, edit_id)
        if edit_id != "last" and not re.fullmatch(r"edit_[a-f0-9]{12}", edit_id):
            return None
        root = str(project_root.resolve())
        candidates = [self._record_from_dir(path) for path in (
            self.storage_dir.iterdir() if edit_id == "last" else [self.storage_dir / edit_id]
        ) if path.is_dir()]
        records = [record for record in candidates if record is not None and record.project == root]
        record = max(records, key=lambda item: item.timestamp, default=None)
        if edit_id == "last":
            file_records = []
            for folder in self.storage_dir.glob("fileedit_*"):
                try:
                    data = json.loads((folder / "file-transaction.json").read_text(encoding="utf-8"))
                    if data["project"] == root and data["status"] == "applied":
                        file_records.append((float(data["timestamp"]), folder.name))
                except (OSError, ValueError, KeyError, TypeError):
                    continue
            latest = max(file_records, default=None)
            if latest and (record is None or latest[0] > record.timestamp):
                return self._undo_files(project_root, latest[1])
        if record is None:
            return None
        try:
            replace_checked(project_root, record.rel_path, record.backup.read_bytes(), record.after_hash)
        except OSError:
            return None
        self._write_meta(record, undone=True)
        self._memory = [item for item in self._memory if item.edit_id != record.edit_id]
        return record

    def _undo_files(self, project_root: Path, edit_id: str) -> EditRecord | None:
        from atomic_flow.agents.file_transaction import undo

        state = undo(self.storage_dir, project_root, edit_id, interrupted=True)
        if state is None:
            return None
        paths = list(state["changes"])
        first = state["changes"][paths[0]]
        return EditRecord(edit_id, ", ".join(paths), "", "", "", state["timestamp"],
                          self.storage_dir / edit_id, str(project_root.resolve()),
                          first["before"] or "", first["after"] or "")

    def list(self) -> list[EditRecord]:
        return list(self._memory)
