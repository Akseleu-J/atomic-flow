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
import subprocess
import uuid
from pathlib import Path

from atomic_flow.agents.source import SourceConflict, confined_path, digest, replace_checked
from atomic_flow.config import user_dir
from atomic_flow.execution_inputs import private_credential_path
from atomic_flow.tree.defaults import DEFAULT_IGNORE


class ChangeSetConflict(SourceConflict):
    pass


_UNSPECIFIED_HASH = object()


def _git(directory: Path, *args: str, data: bytes | None = None) -> bytes:
    """Only built-in plumbing, no user environment, global config, hooks or filters."""
    engine = shutil.which("git")
    if engine is None:
        raise ChangeSetConflict("Git is unavailable for git-worktree workspace")
    env = {key: value for key, value in os.environ.items()
           if key.upper() in {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP"}}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
               GIT_TERMINAL_PROMPT="0", GIT_AUTHOR_NAME="atomic-flow workspace",
               GIT_AUTHOR_EMAIL="workspace@localhost", GIT_COMMITTER_NAME="atomic-flow workspace",
               GIT_COMMITTER_EMAIL="workspace@localhost")
    command = [engine, "-c", "core.hooksPath=" + str(directory / "disabled-hooks"),
               "-c", "core.fsmonitor=false", "-c", "core.attributesFile=" + os.devnull,
               "-c", "protocol.file.allow=never", "-c", "maintenance.auto=false",
               "-c", "gc.auto=0", *args]
    try:
        result = subprocess.run(command, cwd=directory, env=env, input=data,
                                capture_output=True, timeout=30, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ChangeSetConflict("Git workspace operation unavailable") from exc
    if result.returncode:
        # Git errors can contain repository config values; keep them out of model/artifact output.
        raise ChangeSetConflict("Git workspace operation failed")
    return result.stdout


def _plain_path(path: Path):
    for item in (path, *path.parents):
        if item.is_symlink() or (hasattr(item, "is_junction") and item.is_junction()):
            raise ChangeSetConflict("workspace ownership path contains a link")


def _snapshot_tree(metadata: Path, workspace: Path, manifest: dict[str, str]) -> str:
    """Create a private snapshot commit without checkout, Git attributes or source config."""
    tree: dict = {}
    for rel, expected in manifest.items():
        source = confined_path(workspace, rel)
        data = source.read_bytes()
        if digest(data) != expected:
            raise ChangeSetConflict(f"workspace changed while snapshotting: {rel}")
        blob = _git(metadata.parent, "--git-dir=" + str(metadata), "hash-object", "-w", "--stdin", data=data).strip()
        node = tree
        parts = rel.split("/")
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        mode = b"100755" if source.stat().st_mode & stat.S_IXUSR else b"100644"
        node[parts[-1]] = (mode, blob)

    def store(node):
        records = []
        for name, value in sorted(node.items()):
            if isinstance(value, dict):
                mode, kind, oid = b"040000", b"tree", store(value)
            else:
                mode, oid = value
                kind = b"blob"
            records.append(mode + b" " + kind + b" " + oid + b"\t" + name.encode("utf-8") + b"\0")
        return _git(metadata.parent, "--git-dir=" + str(metadata), "mktree", "-z", data=b"".join(records)).strip()

    return _git(metadata.parent, "--git-dir=" + str(metadata), "commit-tree", store(tree).decode(),
                "-m", "Managed source snapshot; no original Git history or credentials").decode().strip()


def _files(root: Path, excluded: Path | None = None) -> dict[str, str]:
    files = {}
    def ignored(name):
        return any(fnmatch.fnmatch(name, pattern) for pattern in DEFAULT_IGNORE)
    for current, dirs, names in os.walk(root, followlinks=False):
        folder = Path(current)
        dirs[:] = sorted(name for name in dirs if not ignored(name)
                         and not private_credential_path((folder / name).relative_to(root).as_posix())
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
            if private_credential_path(rel):
                continue
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
    def create(cls, project: Path, *, backend: str = "copy") -> ChangeSet:
        project = project.resolve()
        if not project.is_dir():
            raise ChangeSetConflict("project directory does not exist")
        if backend not in {"copy", "git-worktree"}:
            raise ChangeSetConflict("unknown workspace backend")
        _plain_path(user_dir().absolute())
        baseline = _files(project, user_dir().resolve())
        directory = user_dir() / "changesets" / ("change_" + uuid.uuid4().hex[:12])
        directory.mkdir(parents=True)
        change = cls(project, directory, {"project": str(project), "status": "active", "baseline": baseline,
                                         "approved": [], "approved_hashes": {}, "changes": {},
                                         "backend": backend, "excluded": str(user_dir().resolve())})
        change._save()
        try:
            if backend == "git-worktree":
                change._create_worktree()
            else:
                change.workspace.mkdir()
            for rel, expected in baseline.items():
                source = confined_path(project, rel)
                target = confined_path(change.workspace, rel)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
                if digest(target.read_bytes()) != expected:
                    raise ChangeSetConflict(f"source changed while copying: {rel}")
            change.check_original()
            change._validate_ownership()
            change._save()
        except BaseException:
            change.state["status"] = "creation_failed"
            change._save()  # Keep partial workspace and creation evidence for explicit review.
            raise
        return change

    def _create_worktree(self):
        source_git = self.project / ".git"
        _plain_path(self.project)
        _plain_path(source_git)
        if not source_git.is_dir():
            raise ChangeSetConflict("git-worktree needs a repository root with a local .git directory")
        top = _git(self.project, "rev-parse", "--show-toplevel").decode().strip()
        if Path(top).resolve() != self.project:
            raise ChangeSetConflict("git-worktree project must be the repository root")
        index = _git(self.project, "ls-files", "--stage", "-z")
        if any(record.startswith(b"160000 ") for record in index.split(b"\0")):
            raise ChangeSetConflict("submodules are unsupported by managed worktrees")
        metadata = self.directory / "git-private"
        _git(self.directory, "init", "--bare", "--template=", str(metadata))
        commit = _snapshot_tree(metadata, self.project, self.state["baseline"])
        _git(self.directory, "--git-dir=" + str(metadata), "worktree", "add", "--detach",
             "--no-checkout", str(self.workspace), commit)
        _git(self.workspace, "read-tree", commit)  # Populate only the private index, never checkout/filter source.
        self.state["workspace_commit"] = commit
        self.state["gitdir_marker"] = (self.workspace / ".git").read_text(encoding="utf-8")
        self.state["git_config_hash"] = digest((metadata / "config").read_bytes())
        self._save()

    def _validate_ownership(self):
        _plain_path(self.directory)
        _plain_path(self.workspace)
        if self.directory.resolve() != (user_dir() / "changesets" / self.id).resolve():
            raise ChangeSetConflict("workspace is outside its owned changeset")
        backend = self.state.get("backend", "copy")
        if backend not in {"copy", "git-worktree"}:
            raise ChangeSetConflict("unknown workspace backend")
        if backend != "git-worktree":
            return
        metadata = self.directory / "git-private"
        _plain_path(metadata)
        config = metadata / "config"
        _plain_path(config)
        if not config.is_file() or digest(config.read_bytes()) != self.state.get("git_config_hash"):
            raise ChangeSetConflict("private Git config changed")
        if self.state.get("workspace_cleaned"):
            if self.workspace.exists() or self.state["status"] not in {"aborted", "rolled_back", "published"}:
                raise ChangeSetConflict("cleaned worktree ownership mismatch")
            return
        marker = self.workspace / ".git"
        _plain_path(marker)
        if not marker.is_file() or marker.read_text(encoding="utf-8") != self.state.get("gitdir_marker"):
            raise ChangeSetConflict("worktree Git marker changed")
        pointer = marker.read_text(encoding="utf-8").strip()
        expected = metadata / "worktrees" / "workspace"
        if not pointer.startswith("gitdir: ") or Path(pointer[8:]).resolve() != expected.resolve():
            raise ChangeSetConflict("worktree Git metadata is outside its owned changeset")
        _plain_path(expected)
        for name, target in (("gitdir", marker), ("commondir", metadata)):
            path = expected / name
            _plain_path(path)
            value = Path(path.read_text(encoding="utf-8").strip())
            if not value.is_absolute():
                value = expected / value
            if value.resolve() != target.resolve():
                raise ChangeSetConflict("worktree registration changed")
        head = expected / "HEAD"
        _plain_path(head)
        if head.read_text(encoding="utf-8").strip() != self.state.get("workspace_commit"):
            raise ChangeSetConflict("worktree snapshot HEAD changed")

    @classmethod
    def open(cls, project: Path, change_id: str) -> ChangeSet:
        if not re.fullmatch(r"change_[a-f0-9]{12}", change_id):
            raise ChangeSetConflict("invalid changeset id")
        directory = user_dir() / "changesets" / change_id
        state = json.loads((directory / "journal.json").read_text(encoding="utf-8"))
        if state["project"] != str(project.resolve()):
            raise ChangeSetConflict("changeset belongs to a different project")
        change = cls(project, directory, state)
        change._validate_ownership()
        return change

    def _save(self):
        target = self.directory / "journal.json"
        temp = target.with_suffix(".json.tmp")
        temp.write_text(json.dumps(self.state, indent=2) + "\n", encoding="utf-8")
        temp.replace(target)

    def check_original(self):
        self._validate_ownership()
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
        self._validate_ownership()
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
            self._validate_ownership()
            self.state["status"] = "aborted"
            self._save()
            self.cleanup()

    def cleanup(self):
        """Explicitly remove only a verified owned terminal Git workspace; retain journal/objects."""
        if self.state.get("backend", "copy") != "git-worktree" or self.state.get("workspace_cleaned"):
            return
        if self.state["status"] not in {"aborted", "rolled_back", "published"}:
            raise ChangeSetConflict("active/recovery workspace must be retained")
        self._validate_ownership()
        # Only the private Git registration is touched; no reset, merge or prune of source metadata.
        _git(self.directory, "--git-dir=" + str(self.directory / "git-private"),
             "worktree", "remove", "--force", str(self.workspace))
        self.state["workspace_cleaned"] = True
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
        self._validate_ownership()
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
        self._validate_ownership()
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
