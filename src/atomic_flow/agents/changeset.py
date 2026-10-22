"""Task workspaces and journaled, conflict-checked publication of approved paths.

Multi-file publication is recoverable, not an OS-level atomic transaction. This is
filesystem staging, not a sandbox for executing untrusted project tests.
"""
from __future__ import annotations

import fnmatch
import json
import os
import re
import shutil
import stat
import uuid
from pathlib import Path

from atomic_flow.agents.source import SourceConflict, confined_path, digest, replace_checked
from atomic_flow.config import user_dir
from atomic_flow.tree.defaults import DEFAULT_IGNORE


class ChangeSetConflict(SourceConflict):
    pass


_UNSPECIFIED_HASH = object()


def _files(root: Path, excluded: Path | None = None) -> dict[str, str]:
    files = {}
    def ignored(name):
        return any(fnmatch.fnmatch(name, pattern) for pattern in DEFAULT_IGNORE)
    for current, dirs, names in os.walk(root, followlinks=False):
        folder = Path(current)
        dirs[:] = sorted(name for name in dirs if not ignored(name)
                         and (not name.startswith(".") or name == ".github")
                         and (excluded is None or (folder / name).resolve() != excluded))
        for name in sorted(names):
            if ignored(name):
                continue
            if name.startswith(".") and name not in {".gitignore", ".coveragerc", ".python-version"}:
                continue
            if name.endswith((".pyc", ".atomic-tmp")):
                continue
            path = folder / name
            rel = path.relative_to(root).as_posix()
            files[rel] = digest(confined_path(root, rel).read_bytes())
        # os.walk would silently ignore symlink directories unless checked here.
        for name in dirs:
            confined_path(root, (folder / name).relative_to(root).as_posix())
    return files


class ChangeSet:
    def __init__(self, project: Path, directory: Path, state: dict):
        self.project = project.resolve()
        self.directory = directory
        self.workspace = directory / "workspace"
        self.id = directory.name
        self.state = state

    @classmethod
    def create(cls, project: Path) -> ChangeSet:
        project = project.resolve()
        if not project.is_dir():
            raise ChangeSetConflict("project directory does not exist")
        directory = user_dir() / "changesets" / ("change_" + uuid.uuid4().hex[:12])
        directory.mkdir(parents=True)
        baseline = _files(project, user_dir().resolve())
        change = cls(project, directory, {"project": str(project), "status": "active", "baseline": baseline,
                                         "approved": [], "approved_hashes": {}, "changes": {}, "excluded": str(user_dir().resolve())})
        change.workspace.mkdir()
        for rel, expected in baseline.items():
            source = confined_path(project, rel)
            target = confined_path(change.workspace, rel)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            if digest(target.read_bytes()) != expected:
                raise ChangeSetConflict(f"source changed while copying: {rel}")
        change.check_original()
        change._save()
        return change

    @classmethod
    def open(cls, project: Path, change_id: str) -> ChangeSet:
        if not re.fullmatch(r"change_[a-f0-9]{12}", change_id):
            raise ChangeSetConflict("invalid changeset id")
        directory = user_dir() / "changesets" / change_id
        state = json.loads((directory / "journal.json").read_text(encoding="utf-8"))
        if state["project"] != str(project.resolve()):
            raise ChangeSetConflict("changeset belongs to a different project")
        return cls(project, directory, state)

    def _save(self):
        target = self.directory / "journal.json"
        temp = target.with_suffix(".json.tmp")
        temp.write_text(json.dumps(self.state, indent=2) + "\n", encoding="utf-8")
        temp.replace(target)

    def check_original(self):
        if _files(self.project, Path(self.state["excluded"])) != self.state["baseline"]:
            raise ChangeSetConflict("original project changed since task snapshot; refusing publication/resume")

    def track(self, rel: str, expected_hash: str | None | object = _UNSPECIFIED_HASH):
        path = confined_path(self.workspace, rel)
        current = digest(path.read_bytes()) if path.exists() else None
        # An explicit None is proof of deletion, rather than an omitted check.
        if expected_hash is not _UNSPECIFIED_HASH and current != expected_hash:
            raise ChangeSetConflict(f"workspace no longer matches verified edit: {rel}")
        if self.state["status"] != "active":
            raise ChangeSetConflict("changeset is not active")
        self.state["approved"] = sorted(set(self.state["approved"]) | {rel})
        self.state["approved_hashes"][rel] = current
        self._save()

    def validate_workspace(self) -> dict[str, str]:
        staged = _files(self.workspace)
        baseline = self.state["baseline"]
        changed = {rel for rel in baseline.keys() | staged.keys() if baseline.get(rel) != staged.get(rel)}
        unapproved = changed - set(self.state["approved"])
        if unapproved:
            raise ChangeSetConflict(f"unapproved workspace modifications: {sorted(unapproved)}")
        for rel in changed:
            if staged.get(rel) != self.state["approved_hashes"].get(rel):
                raise ChangeSetConflict(f"workspace changed after verification: {rel}")
        return staged

    def abort(self):
        if self.state["status"] == "active":
            self.state["status"] = "aborted"
            self._save()

    def checkpoint(self) -> dict:
        folder = self.directory / "checkpoints" / uuid.uuid4().hex
        files = self.validate_workspace()
        for rel, expected in files.items():
            target = confined_path(folder, rel)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(confined_path(self.workspace, rel), target)
            if digest(target.read_bytes()) != expected:
                raise ChangeSetConflict(f"workspace changed during checkpoint: {rel}")
        return {"folder": folder, "files": files, "approved": list(self.state["approved"]),
                "approved_hashes": dict(self.state["approved_hashes"])}

    def restore_checkpoint(self, checkpoint: dict):
        # Workspace is task-owned. Original project paths are never touched here.
        current = _files(self.workspace)
        previous = checkpoint["files"]
        for rel in current.keys() | previous.keys():
            if current.get(rel) == previous.get(rel):
                continue
            if rel in previous:
                data = confined_path(checkpoint["folder"], rel).read_bytes()
                if digest(data) != previous[rel]:
                    raise ChangeSetConflict(f"checkpoint damaged: {rel}")
                replace_checked(self.workspace, rel, data, current.get(rel))
                confined_path(self.workspace, rel).chmod(stat.S_IMODE(confined_path(checkpoint["folder"], rel).stat().st_mode))
            else:
                target = confined_path(self.workspace, rel)
                if digest(target.read_bytes()) != current[rel]:
                    raise ChangeSetConflict(f"workspace recovery conflict: {rel}")
                target.unlink()
        self.state["approved"] = checkpoint["approved"]
        self.state["approved_hashes"] = checkpoint["approved_hashes"]
        self._save()

    def _write_target(self, rel: str, data: bytes | None):
        expected = self.state["baseline"].get(rel)
        if data is None:
            target = confined_path(self.project, rel)
            if digest(target.read_bytes()) != expected:
                raise ChangeSetConflict(f"conflict deleting {rel}")
            target.unlink()
        else:
            replace_checked(self.project, rel, data, expected)
            # Newly created/moved source inherits its verified workspace mode.
            # Existing targets keep replace_checked's permission-preserving rule.
            mode = self.state["changes"].get(rel, {}).get("after_mode")
            if expected is None and mode is not None:
                confined_path(self.project, rel).chmod(mode)

    def publish(self, finalize=None, cancel_check=lambda: None):
        cancel_check()
        if self.state["status"] != "active":
            raise ChangeSetConflict("changeset is not active")
        self.check_original()
        baseline = self.state["baseline"]
        staged = self.validate_workspace()
        changed = sorted(rel for rel in baseline.keys() | staged.keys() if baseline.get(rel) != staged.get(rel))
        backup_root = self.directory / "before"
        self.state["changes"] = {
            rel: {"before": baseline.get(rel), "after": staged.get(rel),
                  "before_mode": stat.S_IMODE(confined_path(self.project, rel).stat().st_mode) if rel in baseline else None,
                  "after_mode": stat.S_IMODE(confined_path(self.workspace, rel).stat().st_mode) if rel in staged else None}
            for rel in changed
        }
        self.state["published_manifest"] = staged
        for rel in changed:
            if rel in baseline:
                data = confined_path(self.project, rel).read_bytes()
                if digest(data) != baseline[rel]:
                    raise ChangeSetConflict(f"original changed while preparing: {rel}")
                backup = confined_path(backup_root, rel)
                backup.parent.mkdir(parents=True, exist_ok=True)
                backup.write_bytes(data)
        self.state["status"] = "publishing"
        self._save()  # all intents/backups durable before the first target write
        try:
            for rel in changed:
                cancel_check()
                data = confined_path(self.workspace, rel).read_bytes() if rel in staged else None
                if data is not None and digest(data) != staged[rel]:
                    raise ChangeSetConflict(f"workspace changed while publishing: {rel}")
                self._write_target(rel, data)
            if _files(self.project, Path(self.state["excluded"])) != staged:
                raise ChangeSetConflict("project changed during publication")
            if finalize is not None:
                finalize()
            if _files(self.project, Path(self.state["excluded"])) != staged:
                raise ChangeSetConflict("project changed during finalization")
            self.state["status"] = "published"
            cancel_check()
            self._save()
        except Exception:
            self.state["status"] = "publishing"
            self.recover()
            raise

    def recover(self):
        """Restore only bytes written by this publication; never overwrite a third version."""
        if self.state["status"] not in {"publishing", "recovery_conflict"}:
            raise ChangeSetConflict("changeset does not need publication recovery")
        conflicts = []
        for rel, hashes in self.state["changes"].items():
            try:
                target = confined_path(self.project, rel)
                current = digest(target.read_bytes()) if target.exists() else None
                if current == hashes["before"]:
                    continue
                if current != hashes["after"]:
                    conflicts.append(rel)
                    continue
                if hashes["before"] is None:
                    # Recheck immediately before removing our newly created file.
                    if digest(target.read_bytes()) != hashes["after"]:
                        raise ChangeSetConflict(f"recovery conflict: {rel}")
                    target.unlink()
                else:
                    data = confined_path(self.directory / "before", rel).read_bytes()
                    if digest(data) != hashes["before"]:
                        raise ChangeSetConflict(f"backup damaged: {rel}")
                    replace_checked(self.project, rel, data, hashes["after"])
                    if hashes.get("before_mode") is not None:
                        confined_path(self.project, rel).chmod(hashes["before_mode"])
            except OSError:
                conflicts.append(rel)
        self.state["status"] = "recovery_conflict" if conflicts else "rolled_back"
        self.state["conflicts"] = conflicts
        self._save()
        if conflicts:
            raise ChangeSetConflict(f"recovery needs review; preserved conflicting files: {conflicts}")

    def verify_published(self):
        if _files(self.project, Path(self.state["excluded"])) != self.state.get("published_manifest"):
            raise ChangeSetConflict("published files changed before task completion was recorded")
