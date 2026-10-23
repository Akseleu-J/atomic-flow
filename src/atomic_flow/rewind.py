"""Explicit, preview-bound source snapshots and recoverable multi-file restores.

No code or tool action is replayed. Multi-file restoration is journaled and
recoverable, not an OS atomic transaction. Concurrent foreign bytes are refused.
"""
from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
import stat
import uuid
from pathlib import Path

from atomic_flow.activation.redact import redact
from atomic_flow.agents.source import SourceConflict, digest, replace_checked
from atomic_flow.config import user_dir
from atomic_flow.dialogue import _path
from atomic_flow.locking import exclusive_file
from atomic_flow.sessions import SessionJournal
from atomic_flow.state import StateError

MAX_PATHS = 64
MAX_SNAPSHOT_BYTES = 16 * 1024 * 1024
MAX_DIFF_CHARS = 24000
MAX_DIFF_INPUT_BYTES = 64 * 1024


def _canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()


def _no_links(path: Path):
    for part in (path, *path.parents):
        if part.is_symlink() or getattr(part, "is_junction", lambda: False)():
            raise StateError("rewind state paths cannot contain symlinks/junctions")


def _storage() -> Path:
    path = (user_dir() / "rewind").absolute()
    _no_links(path)
    return path


def _save(path: Path, state: dict):
    _no_links(path)
    temp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temp.open("xb") as handle:
            handle.write(_canonical(state))
            handle.flush()
            os.fsync(handle.fileno())
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


def _folder(identity: str, kind: str) -> Path:
    if not isinstance(identity, str) or not re.fullmatch(kind + r"_[a-f0-9]{32}", identity):
        raise StateError(f"invalid {kind} id")
    folder = _storage() / identity
    _no_links(folder)
    return folder


def _current(project: Path, rel: str) -> tuple[bytes | None, dict]:
    path = _path(project, rel)
    data = path.read_bytes() if path.exists() else None
    return data, {"hash": digest(data) if data is not None else None,
                  "mode": stat.S_IMODE(path.stat().st_mode) if data is not None else None}


def _snapshot(project: Path, snapshot_id: str) -> tuple[dict, dict]:
    folder = _folder(snapshot_id, "snapshot")
    manifest = folder / "snapshot.json"
    _no_links(manifest)
    if manifest.stat().st_size > 64 * 1024:
        raise StateError("snapshot manifest exceeds limit")
    state = json.loads(manifest.read_text(encoding="utf-8"))
    if (set(state) != {"schema_version", "id", "name", "project", "session_id", "files"}
            or type(state["schema_version"]) is not int or state["schema_version"] != 1
            or state["id"] != snapshot_id or state["project"] != str(project.resolve())
            or not isinstance(state["files"], dict) or not 1 <= len(state["files"]) <= MAX_PATHS):
        raise StateError("unsupported, corrupt or wrong-project snapshot")
    data, total = {}, 0
    for index, (rel, record) in enumerate(state["files"].items()):
        _path(project, rel)
        if not isinstance(record, dict) or set(record) != {"hash", "mode", "blob"} or record["blob"] != str(index):
            raise StateError("invalid snapshot file record")
        blob = folder / record["blob"]
        _no_links(blob)
        if record["hash"] is None:
            if record["mode"] is not None:
                raise StateError("invalid absent-file mode")
            content = None
        else:
            if (not isinstance(record["hash"], str) or not re.fullmatch(r"[a-f0-9]{40}", record["hash"])
                    or type(record["mode"]) is not int or not 0 <= record["mode"] <= 0o777):
                raise StateError("invalid snapshot hash/mode")
            if blob.stat().st_size > MAX_SNAPSHOT_BYTES - total:
                raise StateError("snapshot backup exceeds limit")
            with blob.open("rb") as handle:
                content = handle.read(MAX_SNAPSHOT_BYTES - total + 1)
            if len(content) > MAX_SNAPSHOT_BYTES - total:
                raise StateError("snapshot backup exceeds limit")
            if digest(content) != record["hash"]:
                raise StateError("snapshot backup hash mismatch")
            total += len(content)
        data[rel] = content
    if total > MAX_SNAPSHOT_BYTES:
        raise StateError("snapshot exceeds total byte limit")
    return state, data


def create_snapshot(project: Path, name: str, paths, *, session_id: str | None = None) -> dict:
    """Capture named explicit paths, including absent destinations for creations/moves."""
    project = Path(project).resolve()
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", name):
        raise StateError("snapshot name must use 1-64 letters/digits/underscore/hyphen")
    if isinstance(paths, (str, bytes)):
        raise StateError("snapshot paths must be a list")
    paths = sorted(set(paths))
    if not 1 <= len(paths) <= MAX_PATHS:
        raise StateError("snapshot requires 1-64 explicit paths")
    if session_id:
        journal = SessionJournal.open(session_id)
        snapshot = journal.read()
        if snapshot.events[0]["payload"].get("project") != str(project) or snapshot.incomplete_tail:
            raise StateError("snapshot session/project mismatch or interrupted tail")
    captured = {rel: _current(project, rel) for rel in paths}
    if sum(len(data or b"") for data, _ in captured.values()) > MAX_SNAPSHOT_BYTES:
        raise StateError("snapshot exceeds total byte limit")
    identity = "snapshot_" + uuid.uuid4().hex
    folder = _folder(identity, "snapshot")
    folder.mkdir(parents=True)
    records = {}
    for index, (rel, (content, record)) in enumerate(captured.items()):
        if content is not None:
            with (folder / str(index)).open("xb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
        records[rel] = {**record, "blob": str(index)}
    # Recheck the whole selection before publishing a valid checkpoint.
    if any(_current(project, rel)[1] != record for rel, (_, record) in captured.items()):
        raise SourceConflict("source changed while snapshotting")
    state = {"schema_version": 1, "id": identity, "name": name, "project": str(project),
             "session_id": session_id, "files": records}
    _save(folder / "snapshot.json", state)
    if session_id:
        journal.append("snapshot_created", {"snapshot_id": identity, "paths": paths})
    return {"snapshot_id": identity, "name": name, "paths": paths}


def preview_rewind(project: Path, snapshot_id: str) -> dict:
    project = Path(project).resolve()
    state, desired = _snapshot(project, snapshot_id)
    current, changes, diffs = {}, [], []
    rendered, truncated = 0, False

    def add_diff(text):
        nonlocal rendered, truncated
        remaining = MAX_DIFF_CHARS - rendered
        if len(text) > remaining:
            truncated = True
        if remaining > 0:
            diffs.append(text[:remaining])
            rendered += min(len(text), remaining)
    for rel, wanted in desired.items():
        content, observed = _current(project, rel)
        current[rel] = observed
        target = {key: state["files"][rel][key] for key in ("hash", "mode")}
        if observed == target:
            continue
        changes.append({"path": rel, "current": observed, "restore": target})
        if rendered >= MAX_DIFF_CHARS:
            truncated = True
            continue
        if len(content or b"") + len(wanted or b"") > MAX_DIFF_INPUT_BYTES:
            truncated = True
            add_diff(f"[diff omitted for {rel}: input exceeds {MAX_DIFF_INPUT_BYTES} bytes; "
                     f"current={observed['hash']} restore={target['hash']}; inspect files before confirmation]\n")
            continue
        before = redact((content or b"").decode("utf-8", errors="replace")).splitlines(keepends=True)
        after = redact((wanted or b"").decode("utf-8", errors="replace")).splitlines(keepends=True)
        for line in difflib.unified_diff(before, after, fromfile=rel + " (current)", tofile=rel + " (snapshot)"):
            add_diff(line)
            if rendered >= MAX_DIFF_CHARS:
                truncated = True
                break
    binding = {"schema_version": 1, "snapshot_id": snapshot_id, "project": str(project),
               "snapshot_digest": hashlib.sha256(_canonical(state)).hexdigest(), "current": current}
    diff = "".join(diffs)
    return {**binding, "preview_token": hashlib.sha256(_canonical(binding)).hexdigest(), "files": changes,
            "diff": diff, "diff_truncated": truncated,
            "atomic_multi_file": False}


def _write(project: Path, rel: str, content: bytes | None, expected: dict, wanted: dict):
    if _current(project, rel)[1] != expected:
        raise SourceConflict(f"rewind conflict: {rel}")
    path = _path(project, rel)
    if content is None:
        path.unlink(missing_ok=True)
    else:
        replace_checked(project, rel, content, expected["hash"])
        path.chmod(wanted["mode"])


def _restore(project: Path, folder: Path, state: dict, desired: dict, snapshot: dict) -> dict:
    # Full preflight before the next mutation, including files already restored.
    for rel, expected in state["expected"].items():
        observed = _current(project, rel)[1]
        wanted = {key: snapshot["files"][rel][key] for key in ("hash", "mode")}
        if observed != expected and observed != wanted:
            raise SourceConflict(f"rewind recovery conflict: {rel}; preserve foreign changes")
    for rel, expected in state["expected"].items():
        wanted = {key: snapshot["files"][rel][key] for key in ("hash", "mode")}
        observed = _current(project, rel)[1]
        if observed != wanted:
            _write(project, rel, desired[rel], expected, wanted)
        state["restored"] = sorted(set(state["restored"]) | {rel})
        _save(folder / "restore.json", state)
    # An editor may modify an earlier path while later files are restored.
    # Retain the durable restoring intent and preserve that foreign version.
    for rel in state["expected"]:
        wanted = {key: snapshot["files"][rel][key] for key in ("hash", "mode")}
        if _current(project, rel)[1] != wanted:
            raise SourceConflict(f"rewind final verification conflict: {rel}; preserve foreign changes")
    state["status"] = "applied"
    _save(folder / "restore.json", state)
    return {"restore_id": state["id"], "snapshot_id": state["snapshot_id"], "status": "applied",
            "paths": list(state["expected"])}


def apply_rewind(project: Path, snapshot_id: str, preview_token: str) -> dict:
    project = Path(project).resolve()
    storage = _storage()
    _no_links(storage / "restore.lock")
    with exclusive_file(storage / "restore.lock", busy_message="another rewind is active"):
        # Any interrupted restore must be explicitly recovered before a new one.
        for path in storage.glob("restore_*/restore.json"):
            _no_links(path)
            old = json.loads(path.read_text(encoding="utf-8"))
            if old.get("project") == str(project) and old.get("status") != "applied":
                raise StateError("interrupted rewind exists; recover its restore id before a new restore")
        preview = preview_rewind(project, snapshot_id)
        if not isinstance(preview_token, str) or preview["preview_token"] != preview_token:
            raise SourceConflict("rewind preview is stale or unconfirmed; show a new diff")
        snapshot, desired = _snapshot(project, snapshot_id)
        identity = "restore_" + uuid.uuid4().hex
        folder = _folder(identity, "restore")
        folder.mkdir(parents=True)
        state = {"schema_version": 1, "id": identity, "snapshot_id": snapshot_id, "project": str(project),
                 "preview_token": preview_token, "snapshot_digest": preview["snapshot_digest"],
                 "expected": preview["current"], "restored": [], "status": "restoring"}
        _save(folder / "restore.json", state)  # Durable intent precedes the first write.
        return _restore(project, folder, state, desired, snapshot)


def recover_rewind(project: Path, restore_id: str, preview_token: str) -> dict:
    project = Path(project).resolve()
    folder = _folder(restore_id, "restore")
    lock = _storage() / "restore.lock"
    _no_links(lock)
    with exclusive_file(lock, busy_message="another rewind is active"):
        path = folder / "restore.json"
        _no_links(path)
        if path.stat().st_size > 64 * 1024:
            raise StateError("restore manifest exceeds limit")
        state = json.loads(path.read_text(encoding="utf-8"))
        if (set(state) != {"schema_version", "id", "snapshot_id", "project", "preview_token", "snapshot_digest",
                          "expected", "restored", "status"}
                or type(state["schema_version"]) is not int or state["schema_version"] != 1
                or state["id"] != restore_id or state["project"] != str(project)
                or state["preview_token"] != preview_token or state["status"] not in {"restoring", "applied"}):
            raise StateError("unsupported, wrong-project or unconfirmed recovery state")
        snapshot, desired = _snapshot(project, state["snapshot_id"])
        if (hashlib.sha256(_canonical(snapshot)).hexdigest() != state["snapshot_digest"]
                or not isinstance(state["expected"], dict) or set(state["expected"]) != set(desired)
                or not isinstance(state["restored"], list) or not set(state["restored"]).issubset(desired)):
            raise StateError("rewind recovery state does not match snapshot")
        binding = {"schema_version": 1, "snapshot_id": state["snapshot_id"], "project": str(project),
                   "snapshot_digest": state["snapshot_digest"], "current": state["expected"]}
        if hashlib.sha256(_canonical(binding)).hexdigest() != preview_token:
            raise StateError("rewind recovery preview binding is corrupt")
        return _restore(project, folder, state, desired, snapshot)
