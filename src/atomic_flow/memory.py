"""Explicitly approved product memory under ATOMIC_FLOW_HOME, scoped to a project.

Memory is advisory data, never tool permission, source coverage or action replay.
No implicit model writes or automatic retrieval are performed here.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import uuid
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from atomic_flow.config import user_dir
from atomic_flow.locking import exclusive_file
from atomic_flow.summaries import _date, clean_context_text, utc_now

_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")


class MemoryError(ValueError):
    """Memory is invalid, unavailable, outside scope or not approved."""


def _encode(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _identifier(value: str) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise MemoryError("memory ID must be 1-64 letters, digits, underscores or hyphens")
    return value


def _safe_path(path: Path, *, regular: bool = False) -> None:
    for item in (path, *path.parents):
        if item.is_symlink() or getattr(item, "is_junction", lambda: False)():
            raise MemoryError("memory paths may not contain symbolic links or junctions")
    if path.exists() and regular and not stat.S_ISREG(path.stat().st_mode):
        raise MemoryError("memory storage requires regular files")


class MemoryEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, hide_input_in_errors=True)
    schema_version: Literal[1] = 1
    id: str
    project_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    scope: Literal["project"] = "project"
    content: str = Field(min_length=1, max_length=8192)
    category: str = Field(default="note", min_length=1, max_length=64)
    origin: str = Field(default="user", min_length=1, max_length=128)
    created_at: str
    updated_at: str
    content_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    data_only: Literal[True] = True

    @field_validator("id")
    @classmethod
    def valid_id(cls, value: str) -> str:
        return _identifier(value)

    @field_validator("created_at", "updated_at")
    @classmethod
    def valid_date(cls, value: str) -> str:
        return _date(value)


class MemoryStore:
    def __init__(self, project: Path, root: Path | None = None, *, max_entries: int = 128,
                 max_item_bytes: int = 8192, max_total_bytes: int = 1048576):
        if (type(max_entries) is not int or not 1 <= max_entries <= 1024
                or type(max_item_bytes) is not int or not 1 <= max_item_bytes <= 8192
                or type(max_total_bytes) is not int or not 256 <= max_total_bytes <= 8388608):
            raise MemoryError("invalid memory storage limits")
        project = Path(project).expanduser().resolve(strict=True)
        if not project.is_dir():
            raise MemoryError("memory project must be an existing directory")
        allowed = user_dir().expanduser().absolute() / "memory"
        self.root = Path(root).expanduser().absolute() if root is not None else allowed
        _safe_path(allowed)
        _safe_path(self.root)
        if not self.root.resolve().is_relative_to(allowed.resolve()) or self.root.resolve().is_relative_to(project):
            raise MemoryError("memory storage must stay under ATOMIC_FLOW_HOME/memory outside the source project")
        self.project_id = _hash(os.path.normcase(str(project)))
        self.directory = self.root / f"project_{self.project_id}"
        self.path = self.directory / "entries.json"
        self.lock_path = self.directory / "entries.lock"
        self.max_entries, self.max_item_bytes, self.max_total_bytes = max_entries, max_item_bytes, max_total_bytes

    def _paths(self):
        _safe_path(self.directory)
        _safe_path(self.path, regular=True)
        _safe_path(self.lock_path, regular=True)

    def _lease(self):
        self._paths()
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._paths()
        return exclusive_file(self.lock_path, blocking=True, busy_message="memory is being updated")

    def _validate(self, raw: dict) -> dict:
        if (not isinstance(raw, dict) or type(raw.get("schema_version")) is not int
                or raw["schema_version"] != 1 or raw.get("data_only") is not True):
            raise MemoryError("invalid memory entry or unsupported schema")
        try:
            entry = MemoryEntry.model_validate(raw).model_dump()
        except ValueError as exc:
            raise MemoryError("invalid memory entry or unsupported schema") from exc
        if (entry != raw or entry["project_id"] != self.project_id or not entry["content"].strip()
                or any(clean_context_text(entry[key]) != entry[key] for key in ("content", "category", "origin"))
                or entry["content_hash"] != _hash(entry["content"])
                or len(entry["content"].encode("utf-8")) > self.max_item_bytes):
            raise MemoryError("memory integrity, redaction or project scope mismatch")
        return entry

    def _read(self) -> list[dict]:
        self._paths()
        if not self.path.exists():
            return []
        try:
            fd = os.open(self.path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
            with os.fdopen(fd, "rb") as handle:
                if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                    raise MemoryError("memory is not a regular file")
                data = handle.read(self.max_total_bytes + 1)
            if len(data) > self.max_total_bytes:
                raise MemoryError("memory store exceeds byte limit")
            def pairs(items):
                out = {}
                for key, value in items:
                    if key in out:
                        raise MemoryError("duplicate memory JSON key")
                    out[key] = value
                return out
            envelope = json.loads(data, object_pairs_hook=pairs)
            if (not isinstance(envelope, dict) or set(envelope) != {"schema_version", "project_id", "entries"}
                    or type(envelope["schema_version"]) is not int or envelope["schema_version"] != 1
                    or envelope["project_id"] != self.project_id or not isinstance(envelope["entries"], list)):
                raise MemoryError("invalid memory store or unsupported schema")
            entries = [self._validate(item) for item in envelope["entries"]]
            if len(entries) > self.max_entries or len({entry["id"] for entry in entries}) != len(entries):
                raise MemoryError("memory count exceeds bounds or IDs are duplicated")
            return entries
        except (OSError, UnicodeError, ValueError, TypeError, KeyError) as exc:
            if isinstance(exc, MemoryError):
                raise
            raise MemoryError("memory store is unavailable or malformed; no entries may be reused") from exc

    def _write(self, entries: list[dict]):
        self._paths()
        if len(entries) > self.max_entries:
            raise MemoryError("memory entry limit exhausted")
        data = _encode({"schema_version": 1, "project_id": self.project_id,
                        "entries": [self._validate(entry) for entry in entries]})
        if len(data) > self.max_total_bytes:
            raise MemoryError("memory total byte limit exhausted")
        temporary = self.directory / f".entries-{uuid.uuid4().hex}.tmp"
        try:
            with temporary.open("xb") as handle:
                if os.name != "nt":
                    os.chmod(temporary, 0o600)
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            self._paths()
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _approval(approved):
        if approved is not True:
            raise MemoryError("memory mutation requires explicit host write approval")

    def _entry(self, content, *, memory_id, origin, category, created_at=None) -> dict:
        if not isinstance(content, str) or not content.strip():
            raise MemoryError("memory content must be nonempty text")
        if len(content.encode("utf-8")) > self.max_item_bytes:
            raise MemoryError("memory content exceeds item byte limit")
        if not isinstance(origin, str) or not isinstance(category, str):
            raise MemoryError("memory origin and category must be text")
        content, origin, category = clean_context_text(content), clean_context_text(origin), clean_context_text(category)
        now = utc_now()
        return self._validate(MemoryEntry(id=_identifier(memory_id), project_id=self.project_id, content=content,
                                         origin=origin, category=category, created_at=created_at or now,
                                         updated_at=now, content_hash=_hash(content)).model_dump())

    def add(self, content: str, *, approved: bool = False, origin: str = "user", category: str = "note",
            memory_id: str | None = None) -> dict:
        self._approval(approved)
        entry = self._entry(content, memory_id=memory_id or uuid.uuid4().hex, origin=origin, category=category)
        with self._lease():
            entries = self._read()
            if any(item["id"] == entry["id"] for item in entries):
                raise MemoryError("memory ID already exists")
            self._write([*entries, entry])
        return entry

    def update(self, memory_id: str, content: str, *, approved: bool = False, origin: str = "user",
               category: str | None = None) -> dict:
        self._approval(approved)
        memory_id = _identifier(memory_id)
        with self._lease():
            entries = self._read()
            old = next((entry for entry in entries if entry["id"] == memory_id), None)
            if old is None:
                raise MemoryError("memory ID does not exist in this project")
            entry = self._entry(content, memory_id=memory_id, origin=origin,
                                category=old["category"] if category is None else category, created_at=old["created_at"])
            self._write([entry if item["id"] == memory_id else item for item in entries])
        return entry

    def delete(self, memory_id: str, *, approved: bool = False) -> None:
        self._approval(approved)
        memory_id = _identifier(memory_id)
        with self._lease():
            entries = self._read()
            if not any(item["id"] == memory_id for item in entries):
                raise MemoryError("memory ID does not exist in this project")
            self._write([item for item in entries if item["id"] != memory_id])

    def list(self, *, limit: int = 100) -> list[dict]:
        if type(limit) is not int or not 1 <= limit <= 1024:
            raise MemoryError("memory list limit must be 1-1024")
        with self._lease():
            return self._read()[:limit]

    def select(self, *, ids: list[str] | None = None, query: str | None = None,
               limit: int = 10, max_bytes: int = 16384) -> list[dict]:
        if type(limit) is not int or not 1 <= limit <= 32 or type(max_bytes) is not int or not 256 <= max_bytes <= 32768:
            raise MemoryError("invalid memory selection limits")
        if ids is None and query is None:
            raise MemoryError("memory selection requires explicit IDs or a query")
        if ids is not None and (not isinstance(ids, list) or len(ids) > 32
                                or any(not isinstance(item, str) for item in ids) or len(set(ids)) != len(ids)):
            raise MemoryError("memory selection IDs must be unique and at most 32")
        requested = [_identifier(item) for item in ids] if ids is not None else None
        if query is not None and (not isinstance(query, str) or not query.strip() or len(query.encode("utf-8")) > 2048):
            raise MemoryError("memory query must be nonempty and at most 2048 bytes")
        with self._lease():
            entries = self._read()
        if requested is not None:
            index = {entry["id"]: entry for entry in entries}
            if any(item not in index for item in requested):
                raise MemoryError("selected memory ID is unavailable in this project")
            entries = [index[item] for item in requested]
        if query is not None:
            terms = query.casefold().split()
            entries = [entry for entry in entries if all(term in (entry["content"] + " " + entry["category"]).casefold() for term in terms)]
        if requested is not None and len(entries) > limit:
            raise MemoryError("explicit memory selection exceeds count limit")
        selected = entries[:limit]
        if len(_encode(selected)) > max_bytes:
            raise MemoryError("selected memory exceeds request byte limit; choose fewer entries")
        return selected
