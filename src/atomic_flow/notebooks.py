"""Bounded notebook source views and reviewed, recoverable cell mutations.

Notebook output, attachments and metadata are never included in source views.
No operation executes notebook code. Source edits discard all saved code output
and trust markers because reordered or dependent cells may otherwise look fresh.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from atomic_flow.activation.redact import redact
from atomic_flow.agents import file_transaction
from atomic_flow.agents.source import SourceConflict, digest
from atomic_flow.agents.tools.file_paths import admitted_path
from atomic_flow.tree.defaults import MAX_FILE_BYTES

MAX_CELLS = 2048
_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise SourceConflict("duplicate notebook JSON key")
        result[key] = value
    return result


def _source(cell: dict) -> str:
    value = cell.get("source")
    if isinstance(value, str):
        return value
    if isinstance(value, list) and all(isinstance(line, str) for line in value):
        return "".join(value)
    raise SourceConflict("cell source must be a string or a string list")


def _validate(value: Any) -> dict:
    def bounded_json(item, depth=0):
        if depth > 32:
            raise SourceConflict("notebook JSON nesting exceeds the limit")
        if isinstance(item, str):
            if "\x00" in item:
                raise SourceConflict("notebook contains NUL")
            try:
                item.encode("utf-8")
            except UnicodeError as exc:
                raise SourceConflict("notebook contains invalid Unicode") from exc
        elif isinstance(item, dict):
            for key, child in item.items():
                bounded_json(key, depth + 1)
                bounded_json(child, depth + 1)
        elif isinstance(item, list):
            for child in item:
                bounded_json(child, depth + 1)

    bounded_json(value)
    if not isinstance(value, dict) or type(value.get("nbformat")) is not int or value["nbformat"] != 4:
        raise SourceConflict("only nbformat 4 notebooks are supported")
    if type(value.get("nbformat_minor")) is not int or value["nbformat_minor"] < 0:
        raise SourceConflict("invalid notebook minor version")
    if not isinstance(value.get("metadata"), dict):
        raise SourceConflict("notebook metadata must be an object")
    cells = value.get("cells")
    if not isinstance(cells, list) or len(cells) > MAX_CELLS:
        raise SourceConflict("notebook cell limit exceeded or cells are invalid")
    ids = set()
    for cell in cells:
        if not isinstance(cell, dict) or cell.get("cell_type") not in {"code", "markdown", "raw"}:
            raise SourceConflict("invalid notebook cell")
        if not isinstance(cell.get("metadata"), dict):
            raise SourceConflict("cell metadata must be an object")
        if "\x00" in _source(cell):
            raise SourceConflict("notebook source contains NUL")
        cell_id = cell.get("id")
        if cell_id is not None:
            if not isinstance(cell_id, str) or not _ID.fullmatch(cell_id) or cell_id in ids:
                raise SourceConflict("invalid or duplicate notebook cell ID")
            ids.add(cell_id)
        elif "id" in cell:
            raise SourceConflict("cell ID cannot be null")
        if cell["cell_type"] == "code":
            if not isinstance(cell.get("outputs"), list):
                raise SourceConflict("code outputs must be a list")
            for output in cell["outputs"]:
                if not isinstance(output, dict):
                    raise SourceConflict("invalid notebook output")
                kind = output.get("output_type")
                if kind == "stream":
                    if output.get("name") not in {"stdout", "stderr"}:
                        raise SourceConflict("invalid notebook stream output")
                    _source({"source": output.get("text")})
                elif kind == "error":
                    if (not isinstance(output.get("ename"), str) or not isinstance(output.get("evalue"), str)
                            or not isinstance(output.get("traceback"), list)
                            or not all(isinstance(line, str) for line in output["traceback"])):
                        raise SourceConflict("invalid notebook error output")
                elif kind in {"display_data", "execute_result"}:
                    if not isinstance(output.get("data"), dict) or not isinstance(output.get("metadata"), dict):
                        raise SourceConflict("invalid notebook display output")
                    if kind == "execute_result" and type(output.get("execution_count")) is not int:
                        raise SourceConflict("invalid result execution count")
                else:
                    raise SourceConflict("unsupported notebook output type")
            count = cell.get("execution_count")
            if count is not None and (type(count) is not int or count < 0):
                raise SourceConflict("invalid code execution count")
    return value


def _encode(value: dict) -> bytes:
    try:
        data = (json.dumps(_validate(value), ensure_ascii=False, allow_nan=False, indent=1) + "\n").encode("utf-8")
    except (ValueError, TypeError, RecursionError) as exc:
        raise SourceConflict("invalid notebook JSON") from exc
    if len(data) > MAX_FILE_BYTES or b"\x00" in data:
        raise SourceConflict("notebook exceeds the source size limit")
    return data


def _strip_trust(value: dict) -> None:
    for owner in [value, *value["cells"]]:
        for name in ("trusted", "signature", "orig_nbformat"):
            owner["metadata"].pop(name, None)


@dataclass(frozen=True)
class NotebookPreview:
    path: str
    before_hash: str
    after_hash: str
    preview_token: str
    operation: dict
    _after: bytes

    def payload(self) -> dict:
        """Expose only host facts; never echo source literals or saved output."""
        return {"status": "preview", "path": self.path, "content_hash": self.before_hash,
                "after_hash": self.after_hash, "preview_token": self.preview_token,
                "operation": self.operation["action"], "cell_id": redact(self.operation.get("cell_id") or "") or None,
                "outputs": "discard_all_code_outputs", "notebook_execution": "not_run"}


class NotebookDocument:
    def __init__(self, project: Path, path: str, data: bytes, value: dict):
        self.project = project.resolve()
        self.path = path
        self.content_hash = digest(data)
        self._data = data
        self._value = value
        self._policy = {"tree_ignore": None, "respect_gitignore": True, "private_workspace": False}

    @classmethod
    def load(cls, project: Path, path: str, *, expected_hash: str | None = None,
             tree_ignore: list[str] | None = None, respect_gitignore: bool = True,
             private_workspace: bool = False) -> NotebookDocument:
        from types import SimpleNamespace

        context = SimpleNamespace(project_root=project, tree_ignore=tree_ignore,
                                  respect_gitignore=respect_gitignore, private_workspace=private_workspace)
        target = admitted_path(context, path, existing=True, notebook=True)
        data = target.read_bytes()
        if len(data) > MAX_FILE_BYTES:
            raise SourceConflict("notebook exceeds the source size limit")
        if expected_hash is not None and digest(data) != expected_hash:
            raise SourceConflict("notebook changed; read the current revision first")
        try:
            value = json.loads(data.decode("utf-8-sig"), object_pairs_hook=_pairs,
                               parse_constant=lambda _: (_ for _ in ()).throw(ValueError("non-finite JSON")))
            _validate(value)
        except (ValueError, UnicodeError, TypeError, RecursionError) as exc:
            raise SourceConflict("invalid notebook JSON") from exc
        document = cls(project, path, data, value)
        document._policy = {"tree_ignore": list(tree_ignore or []), "respect_gitignore": respect_gitignore,
                            "private_workspace": private_workspace}
        return document

    def assert_current(self) -> None:
        """Recheck source admission and bytes; loaded objects confer no freshness."""
        from types import SimpleNamespace

        context = SimpleNamespace(project_root=self.project, **self._policy)
        target = admitted_path(context, self.path, existing=True, notebook=True)
        with target.open("rb") as handle:
            current = handle.read(MAX_FILE_BYTES + 1)
        if len(current) > MAX_FILE_BYTES or digest(current) != self.content_hash:
            raise SourceConflict("notebook changed; read the current revision first")

    def read(self, *, offset: int = 0, limit: int = 6000) -> dict:
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 256 <= limit <= 12000:
            raise SourceConflict("invalid notebook page range")
        self.assert_current()
        # The transcript includes every source byte after redaction; output and
        # metadata do not confer read proof and are deliberately absent.
        text = redact("\n".join(f"Cell {index} id={cell.get('id', '[missing: explicit normalization required]')} "
                                f"type={cell['cell_type']}\n{_source(cell)}"
                                for index, cell in enumerate(self._value["cells"])))
        start = min(offset, len(text))
        end = min(start + limit, len(text))
        self.assert_current()
        return {"path": self.path, "content_hash": self.content_hash, "source": text[start:end],
                "offset": start, "end_offset": end, "next_offset": end if end < len(text) else None,
                "total_chars": len(text), "total_cells": len(self._value["cells"]),
                "missing_ids": sum("id" not in cell for cell in self._value["cells"]),
                "outputs": "omitted", "metadata": "omitted", "representation": "redacted_notebook_source",
                "source_complete": True, "read_complete": start == 0 and end == len(text),
                "provenance": {"kind": "notebook_source", "content_hash": self.content_hash,
                               "scope": "current project file; no execution or permission proof"}}

    def preview_normalize_ids(self) -> NotebookPreview:
        """Host-only explicit migration: reads never mint or modify legacy IDs."""
        return self._preview({"action": "normalize_ids"}, allow_normalization=True)

    def preview(self, operation: dict) -> NotebookPreview:
        return self._preview(operation, allow_normalization=False)

    def _preview(self, operation: dict, *, allow_normalization: bool) -> NotebookPreview:
        self.assert_current()
        if not isinstance(operation, dict):
            raise SourceConflict("notebook operation must be an object")
        action = operation.get("action")
        fields = {"set_source": {"action", "cell_id", "source"}, "delete": {"action", "cell_id"},
                  "move": {"action", "cell_id", "before_id"},
                  "insert": {"action", "cell_id", "before_id", "cell_type", "source"},
                  "normalize_ids": {"action"}}
        if not isinstance(action, str) or action not in fields or set(operation) - fields[action]:
            raise SourceConflict("unsupported notebook operation or fields")
        value = copy.deepcopy(self._value)
        cells = value["cells"]
        ids = {cell.get("id") for cell in cells if "id" in cell}
        if action == "normalize_ids":
            if not allow_normalization:
                raise SourceConflict("legacy ID normalization requires explicit host approval")
            if all("id" in cell for cell in cells):
                raise SourceConflict("all cells already have IDs")
            for index, cell in enumerate(cells):
                if "id" not in cell:
                    cell_id = "af_" + hashlib.sha256(f"{self.content_hash}:{index}".encode()).hexdigest()[:24]
                    while cell_id in ids:
                        cell_id += "x"
                    cell["id"] = cell_id
                    ids.add(cell_id)
            value["nbformat_minor"] = max(value["nbformat_minor"], 5)
        else:
            if any("id" not in cell for cell in cells):
                raise SourceConflict("normalize legacy missing cell IDs explicitly before editing")
            cell_id = operation.get("cell_id")
            if not isinstance(cell_id, str) or not _ID.fullmatch(cell_id):
                raise SourceConflict("a valid stable cell ID is required")
            before_id = operation.get("before_id")
            if before_id is not None and (not isinstance(before_id, str) or before_id not in ids or before_id == cell_id):
                raise SourceConflict("before_id must identify another existing cell")
            if action in {"insert", "set_source"}:
                source = operation.get("source")
                if not isinstance(source, str) or "\x00" in source or len(source.encode("utf-8")) > MAX_FILE_BYTES:
                    raise SourceConflict("cell source must be bounded text without NUL")
            if action == "insert":
                kind = operation.get("cell_type")
                if cell_id in ids or kind not in {"code", "markdown", "raw"}:
                    raise SourceConflict("insert requires a new cell ID and supported cell type")
                cell = {"id": cell_id, "cell_type": kind, "metadata": {}, "source": source}
                if kind == "code":
                    cell.update(outputs=[], execution_count=None)
                cells.insert(next((i for i, c in enumerate(cells) if c["id"] == before_id), len(cells)), cell)
                value["nbformat_minor"] = max(value["nbformat_minor"], 5)
            else:
                index = next((i for i, c in enumerate(cells) if c["id"] == cell_id), None)
                if index is None:
                    raise SourceConflict("cell ID does not exist in the current revision")
                if action == "set_source":
                    cells[index]["source"] = source
                else:
                    cell = cells.pop(index)
                    if action == "move":
                        cells.insert(next((i for i, c in enumerate(cells) if c["id"] == before_id), len(cells)), cell)
        for cell in cells:
            if cell["cell_type"] == "code":
                cell["outputs"] = []
                cell["execution_count"] = None
        _strip_trust(value)
        after = _encode(value)
        canonical = json.dumps({"project": str(self.project), "path": self.path, "before": self.content_hash,
                                "after": digest(after), "operation": operation}, sort_keys=True, ensure_ascii=False)
        token = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        self.assert_current()
        return NotebookPreview(self.path, self.content_hash, digest(after), token, copy.deepcopy(operation), after)

    def apply(self, preview: NotebookPreview, *, storage: Path, approved: bool = False,
              preview_token: str, allow_normalization: bool = False,
              cancel_check: Callable[[], None] = lambda: None,
              after_write: Callable[[], None] | None = None) -> dict:
        if approved is not True:
            raise SourceConflict("notebook mutation requires explicit approval")
        fresh = NotebookDocument.load(self.project, self.path, expected_hash=self.content_hash, **self._policy)
        checked = fresh._preview(preview.operation, allow_normalization=allow_normalization)
        if (preview != checked or preview_token != checked.preview_token):
            raise SourceConflict("notebook preview token or content does not match this operation")
        cancel_check()
        before, after = {self.path: fresh._data}, {self.path: checked._after}
        edit_id, state = file_transaction.begin(storage, self.project, before, after)
        try:
            file_transaction.apply(storage, self.project, edit_id, state, after, cancel_check)
            NotebookDocument.load(self.project, self.path, expected_hash=checked.after_hash, **self._policy)
            cancel_check()
            if after_write:
                after_write()
            NotebookDocument.load(self.project, self.path, expected_hash=checked.after_hash, **self._policy)
            file_transaction.finish(storage, edit_id, state)
        except Exception as exc:
            restored = file_transaction.undo(storage, self.project, edit_id, interrupted=True)
            return {"status": "rolled_back" if restored else "rollback_failed", "error": str(exc),
                    "path": self.path, "edit_id": edit_id,
                    "side_effects_may_have_occurred": restored is None,
                    "verification": {"notebook_schema": "not_run", "notebook_execution": "not_run"}}
        return {"status": "applied", "path": self.path, "edit_id": edit_id,
                "checks": ["notebook_schema"],
                "content_hash": checked.after_hash, "changes": [{"path": self.path,
                "before_hash": self.content_hash, "after_hash": checked.after_hash}],
                "verification": {"notebook_schema": "passed", "notebook_execution": "not_run"},
                "outputs": "discarded_all_code_outputs", "undo_hint": f"atomic undo {edit_id}"}
