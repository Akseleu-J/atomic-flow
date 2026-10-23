"""Explicit host-approved roots and root-qualified source evidence.

Read evidence and patch previews are session-local. Publication uses one root's
existing recoverable ChangeSet; there is no cross-root atomic transaction.
"""
from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import re
import stat
import time
import uuid
from pathlib import Path

from atomic_flow.activation.redact import redact
from atomic_flow.agents.changeset import ChangeSet
from atomic_flow.agents.context import ToolContext
from atomic_flow.agents.source import SourceConflict, digest, replace_checked
from atomic_flow.agents.tools.file_paths import admitted_path, text_bytes
from atomic_flow.config import user_dir
from atomic_flow.locking import exclusive_file
from atomic_flow.tree.defaults import DEFAULT_IGNORE, SOURCE_SUFFIXES


class WorkspaceError(SourceConflict):
    pass


class WorkspaceScanLimit(WorkspaceError):
    pass


_SCAN_ENTRIES = 20_000
_SCAN_SECONDS = 5.0
_MANIFEST_FILE_BYTES = 8 * 1024 * 1024
_MANIFEST_TOTAL_BYTES = 32 * 1024 * 1024


def _scan_guard(cancel_check):
    deadline = time.monotonic() + _SCAN_SECONDS

    def check():
        cancel_check()
        if time.monotonic() >= deadline:
            raise WorkspaceScanLimit("workspace scan deadline exceeded")

    return check


def _scan_files(root: Path, check):
    """Stream entries with bounded traversal, matching ChangeSet snapshot filters."""
    stack, visited = [root], 0
    while stack:
        check()
        folder = stack.pop()
        _plain(folder)
        with os.scandir(folder) as entries:
            for entry in entries:
                check()
                visited += 1
                if visited > _SCAN_ENTRIES:
                    raise WorkspaceScanLimit("workspace scan entry limit exceeded")
                name = entry.name
                if any(fnmatch.fnmatch(name, pattern) for pattern in DEFAULT_IGNORE):
                    continue
                path = Path(entry.path)
                is_dir = entry.is_dir(follow_symlinks=False)
                if name.startswith(".") and name not in ({".github"} if is_dir else
                                                          {".gitignore", ".coveragerc", ".python-version"}):
                    continue
                _plain(path)
                if is_dir:
                    stack.append(path)
                elif not name.endswith((".pyc", ".atomic-tmp")):
                    if not stat.S_ISREG(path.stat().st_mode):
                        raise WorkspaceError("workspace snapshot requires regular source files")
                    yield path


def _manifest(root: Path, cancel_check):
    check = _scan_guard(cancel_check)
    result, total = {}, 0
    for path in _scan_files(root, check):
        check()
        size = path.stat().st_size
        if size > _MANIFEST_FILE_BYTES:
            raise WorkspaceScanLimit("workspace snapshot file exceeds its byte cap")
        hasher, observed = hashlib.sha1(), 0
        with path.open("rb") as handle:
            while True:
                check()
                chunk = handle.read(1_000_000)
                if not chunk:
                    break
                observed += len(chunk)
                total += len(chunk)
                if observed > _MANIFEST_FILE_BYTES or total > _MANIFEST_TOTAL_BYTES:
                    raise WorkspaceScanLimit("workspace snapshot exceeds its byte cap")
                hasher.update(chunk)
        result[path.relative_to(root).as_posix()] = hasher.hexdigest()
    check()
    return result


def _identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,47}", value):
        raise WorkspaceError("workspace/root ID must start with a letter and contain at most 48 safe characters")
    return value


def _plain(path):
    for item in (path, *path.parents):
        if item.is_symlink() or getattr(item, "is_junction", lambda: False)():
            raise WorkspaceError("workspace paths may not contain links or junctions")


def _encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _sha(data):
    return hashlib.sha256(data).hexdigest()


class WorkspaceRegistry:
    """Trusted-host registry; source/config/model content never creates grants."""

    def __init__(self, workspace_id: str = "default"):
        self.id = _identifier(workspace_id)
        self.directory = user_dir().expanduser().absolute() / "workspaces" / self.id
        self.path = self.directory / "roots.json"
        self.lock_path = self.directory / "roots.lock"
        self._paths()

    def _paths(self):
        for path in (self.directory, self.path, self.lock_path):
            _plain(path)
        for path in (self.path, self.lock_path):
            if path.exists() and not stat.S_ISREG(path.stat().st_mode):
                raise WorkspaceError("workspace registry requires regular files")

    def _lease(self):
        self._paths()
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._paths()
        return exclusive_file(self.lock_path, blocking=True, busy_message="workspace registry is being updated")

    def _root_path(self, raw):
        if not isinstance(raw, str) or not Path(raw).is_absolute():
            raise WorkspaceError("root must be an explicit absolute directory")
        path = Path(raw)
        _plain(path)
        try:
            canonical = path.resolve(strict=True)
        except OSError as exc:
            raise WorkspaceError("root is unavailable") from exc
        if not canonical.is_dir():
            raise WorkspaceError("root must be a directory")
        home = user_dir().expanduser().absolute().resolve()
        if canonical.is_relative_to(home) or home.is_relative_to(canonical):
            raise WorkspaceError("application state and source roots must be disjoint")
        if any(part.casefold() == ".git" for part in canonical.parts):
            raise WorkspaceError("Git metadata cannot be a source root")
        return canonical

    def _load(self, *, validate_roots=True):
        self._paths()
        if not self.path.exists():
            return {"schema_version": 1, "workspace_id": self.id, "roots": {}}
        if self.path.stat().st_size > 65536:
            raise WorkspaceError("workspace registry exceeds its size limit")
        try:
            state = json.loads(self.path.read_text(encoding="utf-8"))
            if (set(state) != {"schema_version", "workspace_id", "roots"}
                    or type(state["schema_version"]) is not int or state["schema_version"] != 1
                    or state["workspace_id"] != self.id or not isinstance(state["roots"], dict)
                    or len(state["roots"]) > 8):
                raise ValueError()
            canonical = []
            for root_id, item in state["roots"].items():
                _identifier(root_id)
                if (set(item) != {"root_id", "path", "generation", "write_paths", "root_identity"}
                        or item["root_id"] != root_id
                        or not re.fullmatch(r"[a-f0-9]{32}", item["generation"])
                        or not isinstance(item["write_paths"], list) or len(item["write_paths"]) > 64
                        or len(set(item["write_paths"])) != len(item["write_paths"])
                        or not isinstance(item["root_identity"], list) or len(item["root_identity"]) != 2
                        or any(type(value) is not int for value in item["root_identity"])):
                    raise ValueError()
                if not validate_roots:
                    if not isinstance(item["path"], str) or not Path(item["path"]).is_absolute():
                        raise ValueError()
                    continue
                root = self._root_path(item["path"])
                info = root.stat()
                if [info.st_dev, info.st_ino] != item["root_identity"]:
                    raise ValueError()
                if str(root) != item["path"] or any(root.is_relative_to(other) or other.is_relative_to(root)
                                                       for other in canonical):
                    raise ValueError()
                canonical.append(root)
                ctx = ToolContext(root, {})
                for rel in item["write_paths"]:
                    admitted_path(ctx, rel)
            return state
        except (ValueError, TypeError, KeyError, OSError) as exc:
            raise WorkspaceError("invalid or unavailable workspace registry") from exc

    def _save(self, state):
        self._paths()
        temp = self.directory / (uuid.uuid4().hex + ".tmp")
        try:
            with temp.open("xb") as handle:
                handle.write(_encode(state))
                handle.flush()
                os.fsync(handle.fileno())
            self._paths()
            temp.replace(self.path)
        finally:
            temp.unlink(missing_ok=True)

    def grant(self, root_id: str, path: Path, *, write_paths=(), approved: bool = False):
        if approved is not True:
            raise WorkspaceError("root access needs explicit host approval")
        root_id = _identifier(root_id)
        root = self._root_path(str(path))
        if not isinstance(write_paths, (list, tuple)) or len(write_paths) > 64:
            raise WorkspaceError("write scope must contain at most 64 exact source paths")
        ctx = ToolContext(root, {})
        for rel in write_paths:
            admitted_path(ctx, rel)
        with self._lease():
            state = self._load()
            others = [self._root_path(item["path"]) for key, item in state["roots"].items() if key != root_id]
            if any(root.is_relative_to(other) or other.is_relative_to(root) for other in others):
                raise WorkspaceError("overlapping or aliased roots are not permitted")
            if root_id not in state["roots"] and len(state["roots"]) >= 8:
                raise WorkspaceError("workspace has reached its root limit")
            info = root.stat()
            item = {"root_id": root_id, "path": str(root), "generation": uuid.uuid4().hex,
                    "write_paths": sorted(set(write_paths)), "root_identity": [info.st_dev, info.st_ino]}
            state["roots"][root_id] = item
            self._save(state)
            return dict(item)

    def revoke(self, root_id: str, *, approved: bool = False):
        if approved is not True:
            raise WorkspaceError("revocation needs explicit host approval")
        _identifier(root_id)
        with self._lease():
            state = self._load(validate_roots=False)
            existed = state["roots"].pop(root_id, None) is not None
            self._save(state)
            return {"root_id": root_id, "revoked": existed}

    def list_roots(self):
        with self._lease():
            return list(self._load()["roots"].values())

    def _get(self, root_id):
        _identifier(root_id)
        with self._lease():
            item = self._load()["roots"].get(root_id)
            if item is None:
                raise WorkspaceError("root access is not granted")
            return item

    def session(self):
        return WorkspaceSession(self)


class WorkspaceSession:
    """Fresh read proofs and opaque single-root previews, never restored from text."""

    def __init__(self, registry: WorkspaceRegistry):
        self.registry = registry
        self._reads = {}
        self._previews = {}

    def _source(self, root_id, rel):
        item = self.registry._get(root_id)
        ctx = ToolContext(Path(item["path"]), {})
        path = admitted_path(ctx, rel)
        if path.exists() and path.stat().st_nlink != 1:
            raise WorkspaceError("hard-linked source aliases are not permitted")
        data = None
        if path.exists():
            with path.open("rb") as handle:
                data = handle.read(1_000_001)
        if data is not None and (b"\0" in data or len(data) > 1_000_000):
            raise WorkspaceError("workspace source must be bounded UTF-8 text")
        current = _sha(data) if data is not None else None
        return item, path, data, current

    def read(self, root_id: str, path: str, *, offset: int = 0, limit: int = 6000, record_proof: bool = True):
        if type(record_proof) is not bool:
            raise WorkspaceError("invalid host read-proof mode")
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 12000:
            raise WorkspaceError("invalid source page")
        item, _, data, current = self._source(root_id, path)
        try:
            rendered = redact(data.decode("utf-8-sig")) if data is not None else ""
        except UnicodeError as exc:
            raise WorkspaceError("workspace source is not UTF-8 text") from exc
        key = (root_id, item["generation"], path, current)
        if record_proof and key not in self._reads and len(self._reads) >= 256:
            raise WorkspaceError("session read-evidence limit reached")
        start, stop = min(offset, len(rendered)), min(offset + limit, len(rendered))
        ranges = sorted([*(self._reads.get(key, []) if record_proof else []), [start, stop]])
        merged = []
        for left, right in ranges:
            if merged and left <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], right)
            else:
                merged.append([left, right])
        # Recheck access and bytes after rendering, before handing out evidence.
        after, _, _, fresh = self._source(root_id, path)
        if after != item or fresh != current:
            raise WorkspaceError("root grant/source changed during read")
        if record_proof:
            self._reads[key] = merged
        return {"workspace_id": self.registry.id, "root_id": root_id, "path": path,
                "source": rendered[start:stop], "exists": data is not None,
                "content_hash": current, "hash_algorithm": "sha256", "offset": start,
                "next_offset": stop if stop < len(rendered) else None, "total_chars": len(rendered),
                "read_complete": len(rendered) == 0 or merged == [[0, len(rendered)]],
                "representation": "redacted", "data_only": True}

    def list_files(self, root_id: str, *, limit: int = 200, cancel_check=lambda: None):
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise WorkspaceError("invalid source-list limit")
        item = self.registry._get(root_id)
        root = Path(item["path"])
        ctx = ToolContext(root, {})
        files, truncated, reason = [], False, None
        check = _scan_guard(cancel_check)
        try:
            for path in _scan_files(root, check):
                if path.suffix.lower() not in SOURCE_SUFFIXES:
                    continue
                rel = path.relative_to(root).as_posix()
                try:
                    admitted_path(ctx, rel, existing=True)
                except SourceConflict:
                    continue
                if len(files) >= limit:
                    truncated, reason = True, "result_limit"
                    break
                files.append(rel)
        except WorkspaceScanLimit as exc:
            truncated, reason = True, str(exc)
        if self.registry._get(root_id) != item:
            raise WorkspaceError("root grant changed during enumeration")
        return {"workspace_id": self.registry.id, "root_id": root_id, "files": sorted(files),
                "truncated": truncated, "limit_reason": reason, "data_only": True}

    def preview(self, root_id: str, changes: dict[str, str | None], *, cancel_check=lambda: None):
        if not isinstance(changes, dict) or not 1 <= len(changes) <= 32 or len(self._previews) >= 16:
            raise WorkspaceError("preview requires 1-32 paths and at most 16 pending previews")
        item = self.registry._get(root_id)
        prepared, total = {}, 0
        for rel, text in changes.items():
            cancel_check()
            if rel not in item["write_paths"]:
                raise WorkspaceError("path is outside the exact approved root write scope")
            before, _, data, current = self._source(root_id, rel)
            if before != item:
                raise WorkspaceError("root grant changed")
            key = (root_id, item["generation"], rel, current)
            rendered = redact(data.decode("utf-8-sig")) if data is not None else ""
            if self._reads.get(key) != [[0, len(rendered)]]:
                raise WorkspaceError("read the complete current root-qualified source before preview")
            after = text_bytes(text) if isinstance(text, str) else None
            if text is not None and not isinstance(text, str):
                raise WorkspaceError("replacement must be text or None for deletion")
            total += len(after or b"")
            if total > 1_000_000:
                raise WorkspaceError("preview exceeds its content cap")
            if after is not None and Path(rel).suffix in {".py", ".pyi"}:
                try:
                    compile(after, rel, "exec")
                except (SyntaxError, ValueError) as exc:
                    raise WorkspaceError("replacement Python does not compile") from exc
            prepared[rel] = {"before": current, "before_source_hash": digest(data) if data is not None else None,
                             "after": after, "after_hash": _sha(after) if after is not None else None}
        token = uuid.uuid4().hex + uuid.uuid4().hex
        manifest = _manifest(Path(item["path"]), cancel_check)
        if self.registry._get(root_id) != item:
            raise WorkspaceError("root grant changed during preview")
        self._previews[token] = {"grant": item, "changes": prepared, "manifest": manifest}
        return {"workspace_id": self.registry.id, "root_id": root_id, "preview_token": token,
                "changes": [{"path": rel, "before_hash": change["before"], "after_hash": change["after_hash"],
                             "replacement": redact(change["after"].decode())[:12000] if change["after"] is not None else None,
                             "replacement_truncated": len(change["after"] or b"") > 12000}
                            for rel, change in prepared.items()],
                "publication": "single-root recoverable changeset; no cross-root atomicity"}

    def apply(self, preview_token: str, *, approved: bool = False, cancel_check=lambda: None, final_check=None):
        if approved is not True:
            raise WorkspaceError("publication needs explicit host approval of the preview")
        preview = self._previews.pop(preview_token, None)
        if preview is None:
            raise WorkspaceError("unknown, consumed or foreign preview token")
        item = preview["grant"]
        root_id = item["root_id"]

        def authority():
            cancel_check()
            if self.registry._get(root_id) != item:
                raise WorkspaceError("root grant was revoked or changed after preview")

        authority()
        for rel, change in preview["changes"].items():
            _, _, _, current = self._source(root_id, rel)
            if current != change["before"]:
                raise WorkspaceError("root source changed after preview")
        if _manifest(Path(item["path"]), cancel_check) != preview["manifest"]:
            raise WorkspaceError("root source manifest changed after preview")
        staged = ChangeSet.create(Path(item["path"]))
        try:
            if staged.state["baseline"] != preview["manifest"]:
                raise WorkspaceError("root source manifest changed after preview")
            for rel, change in preview["changes"].items():
                authority()
                if staged.state["baseline"].get(rel) != change["before_source_hash"]:
                    raise WorkspaceError("root source changed during staging")
                if change["after"] is None:
                    target = admitted_path(ToolContext(staged.workspace, {}, private_workspace=True), rel)
                    target.unlink(missing_ok=True)
                else:
                    replace_checked(staged.workspace, rel, change["after"], change["before_source_hash"])
                staged.track(rel, digest(change["after"]) if change["after"] is not None else None)
            staged.validate_workspace()
            if final_check is not None:
                if final_check(staged.workspace) is not True:
                    raise WorkspaceError("host final checks did not pass")
            authority()
            staged.publish(finalize=authority, cancel_check=authority)
            staged.verify_published()
        except BaseException:
            staged.abort()
            raise
        return {"workspace_id": self.registry.id, "root_id": root_id,
                "changeset_id": staged.id, "status": staged.state["status"],
                "paths": sorted(preview["changes"]), "cross_root_atomic": False}
