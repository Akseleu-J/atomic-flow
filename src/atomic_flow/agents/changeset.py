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
import threading
import uuid
from dataclasses import asdict
from io import BytesIO
from pathlib import Path

from atomic_flow.agents.source import SourceConflict, confined_path, digest, replace_checked
from atomic_flow.config import user_dir
from atomic_flow.durability import atomic_bytes, write_bytes
from atomic_flow.execution_inputs import private_credential_path
from atomic_flow.project_resources import ProjectLimits, ResourceLimitError, require_disk_space, source_files, stream_source
from atomic_flow.source_lock import mutation_guard
from atomic_flow.tree.defaults import DEFAULT_IGNORE


class ChangeSetConflict(SourceConflict):
    pass


_UNSPECIFIED_HASH = object()


def _git(directory: Path, *args: str, data: bytes | None = None, output_limit: int | None = None) -> bytes:
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
        if output_limit is not None:
            if data is not None:
                raise ChangeSetConflict("bounded Git inventory does not accept input")
            with subprocess.Popen(command, cwd=directory, env=env, stdin=subprocess.DEVNULL,
                                  stdout=subprocess.PIPE, stderr=subprocess.DEVNULL) as process:
                result_bytes = []
                read_errors = []

                def receive():
                    try:
                        result_bytes.append(process.stdout.read(output_limit + 1))
                        if len(result_bytes[0]) > output_limit:
                            process.kill()
                    except OSError as exc:
                        read_errors.append(exc)

                reader = threading.Thread(target=receive, daemon=True)
                reader.start()
                try:
                    process.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                    raise
                finally:
                    reader.join(timeout=1)
                if reader.is_alive() or read_errors or not result_bytes:
                    raise ChangeSetConflict("Git inventory unavailable")
                if len(result_bytes[0]) > output_limit:
                    raise ResourceLimitError("Git source inventory output limit exceeded")
                if process.returncode:
                    raise ChangeSetConflict("Git workspace operation failed")
                return result_bytes[0]
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


def _snapshot_tree(metadata: Path, workspace: Path, manifest: dict[str, str], limits: ProjectLimits,
                   cancel_check) -> str:
    """Create a private snapshot commit without checkout, Git attributes or source config."""
    tree: dict = {}
    for rel, expected in manifest.items():
        cancel_check()
        source = confined_path(workspace, rel)
        data = _bounded_bytes(source, limits, cancel_check)
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
        cancel_check()
        records = []
        for name, value in sorted(node.items()):
            cancel_check()
            if isinstance(value, dict):
                mode, kind, oid = b"040000", b"tree", store(value)
            else:
                mode, oid = value
                kind = b"blob"
            records.append(mode + b" " + kind + b" " + oid + b"\t" + name.encode("utf-8") + b"\0")
        return _git(metadata.parent, "--git-dir=" + str(metadata), "mktree", "-z", data=b"".join(records)).strip()

    return _git(metadata.parent, "--git-dir=" + str(metadata), "commit-tree", store(tree).decode(),
                "-m", "Managed source snapshot; no original Git history or credentials").decode().strip()


def _bounded_bytes(path, limits, cancel_check):
    sink = BytesIO()
    stream_source(path, limits, cancel_check, sink=sink)
    return sink.getvalue()


def _copy_source(source, target, expected, limits, cancel_check, *, remaining_bytes=None):
    cancel_check()
    require_disk_space(target.parent, source.stat().st_size, limits)
    target.parent.mkdir(parents=True, exist_ok=True)
    created = False
    try:
        with target.open("xb") as sink:
            created = True
            actual, size = stream_source(source, limits, cancel_check, sink=sink, remaining_bytes=remaining_bytes)
            sink.flush()
            os.fsync(sink.fileno())
        if actual != expected:
            raise ChangeSetConflict("source changed while copying")
        shutil.copystat(source, target, follow_symlinks=False)
        return size
    except BaseException:
        if created:
            target.unlink(missing_ok=True)
        raise


def _files(root: Path, excluded: Path | None = None, *, limits: ProjectLimits | None = None,
           cancel_check=lambda: None, sizes: dict | None = None) -> dict[str, str]:
    limits = limits or ProjectLimits()
    files, total_bytes = {}, 0

    def skip(rel, is_dir):
        name = Path(rel).name
        if any(fnmatch.fnmatch(name, pattern) for pattern in DEFAULT_IGNORE) or private_credential_path(rel):
            return True
        if name == ".github" and (root / rel).is_symlink():
            confined_path(root, rel)
        if name.startswith(".") and (name != ".github" if is_dir else name not in {".gitignore", ".coveragerc", ".python-version"}):
            return True
        if not is_dir and name.endswith((".pyc", ".atomic-tmp")):
            return True
        if excluded is not None and (root / rel).resolve() == excluded:
            return True
        confined_path(root, rel)  # Do not silently accept links omitted by traversal.
        return False

    for rel, _ in source_files(root, skip, limits, cancel_check):
        if len(files) >= limits.max_files:
            raise ResourceLimitError("project snapshot file count limit exceeded")
        fingerprint, size = stream_source(confined_path(root, rel), limits, cancel_check,
                                          remaining_bytes=limits.max_total_bytes - total_bytes)
        total_bytes += size
        files[rel] = fingerprint
        if sizes is not None:
            sizes[rel] = size
    return files


class ChangeSet:
    def __init__(self, project: Path, directory: Path, state: dict, *, limits=None, cancel_check=lambda: None):
        self.project = project.resolve()
        self.directory = directory
        self.workspace = directory / "workspace"
        self.id = directory.name
        self.state = state
        self.limits = limits or ProjectLimits(**state.get("resource_limits", {}))
        self.cancel_check = cancel_check

    @classmethod
    def create(cls, project: Path, *, backend: str = "copy", limits: ProjectLimits | None = None,
               cancel_check=lambda: None) -> ChangeSet:
        project = project.resolve()
        if not project.is_dir():
            raise ChangeSetConflict("project directory does not exist")
        if backend not in {"copy", "git-worktree"}:
            raise ChangeSetConflict("unknown workspace backend")
        _plain_path(user_dir().absolute())
        limits = limits or ProjectLimits()
        sizes = {}
        baseline = _files(project, user_dir().resolve(), limits=limits, cancel_check=cancel_check, sizes=sizes)
        # Git objects and metadata need extra headroom beyond the staged copy.
        reserve = sum(sizes.values()) * (3 if backend == "git-worktree" else 1)
        reserve += 1024 * 1024 + len(baseline) * 1024
        require_disk_space(user_dir(), reserve, limits)
        directory = user_dir() / "changesets" / ("change_" + uuid.uuid4().hex[:12])
        directory.mkdir(parents=True)
        change = cls(project, directory, {"project": str(project), "status": "active", "baseline": baseline,
                                         "approved": [], "approved_hashes": {}, "changes": {},
                                         "backend": backend, "excluded": str(user_dir().resolve()),
                                         "resource_limits": asdict(limits)}, limits=limits, cancel_check=cancel_check)
        change._save()
        try:
            if backend == "git-worktree":
                change._create_worktree()
            else:
                change.workspace.mkdir()
            copied = 0
            for rel, expected in baseline.items():
                source = confined_path(project, rel)
                target = confined_path(change.workspace, rel)
                copied += _copy_source(source, target, expected, limits, cancel_check,
                                       remaining_bytes=limits.max_total_bytes - copied)
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
        index = _git(self.project, "ls-files", "--stage", "-z",
                     output_limit=min(self.limits.max_total_bytes, 4 * 1024 * 1024))
        if any(record.startswith(b"160000 ") for record in index.split(b"\0")):
            raise ChangeSetConflict("submodules are unsupported by managed worktrees")
        metadata = self.directory / "git-private"
        _git(self.directory, "init", "--bare", "--template=", str(metadata))
        commit = _snapshot_tree(metadata, self.project, self.state["baseline"], self.limits, self.cancel_check)
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
        atomic_bytes(target, (json.dumps(self.state, indent=2) + "\n").encode("utf-8"))

    def check_original(self):
        self._validate_ownership()
        if self._manifest(self.project, Path(self.state["excluded"])) != self.state["baseline"]:
            raise ChangeSetConflict("original project changed since task snapshot; refusing publication/resume")

    def track(self, rel: str, expected_hash: str | None | object = _UNSPECIFIED_HASH):
        path = confined_path(self.workspace, rel)
        current = stream_source(path, self.limits, self.cancel_check)[0] if path.exists() else None
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
        staged = self._manifest(self.workspace)
        baseline = self.state["baseline"]
        changed = {rel for rel in baseline.keys() | staged.keys() if baseline.get(rel) != staged.get(rel)}
        unapproved = changed - set(self.state["approved"])
        if unapproved:
            raise ChangeSetConflict(f"unapproved workspace modifications: {sorted(unapproved)}")
        for rel in changed:
            if staged.get(rel) != self.state["approved_hashes"].get(rel):
                raise ChangeSetConflict(f"workspace changed after verification: {rel}")
        return staged

    def _manifest(self, root, excluded=None, *, sizes=None):
        return _files(root, excluded, limits=self.limits, cancel_check=self.cancel_check, sizes=sizes)

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
        sizes = {}
        if self._manifest(self.workspace, sizes=sizes) != files:
            raise ChangeSetConflict("workspace changed before checkpoint")
        require_disk_space(self.directory, sum(sizes.values()) + len(files) * 1024, self.limits)
        copied = 0
        for rel, expected in files.items():
            target = confined_path(folder, rel)
            copied += _copy_source(confined_path(self.workspace, rel), target, expected, self.limits,
                                   self.cancel_check, remaining_bytes=self.limits.max_total_bytes - copied)
        return {"folder": folder, "files": files, "approved": list(self.state["approved"]),
                "approved_hashes": dict(self.state["approved_hashes"])}

    @mutation_guard("workspace")
    def restore_checkpoint(self, checkpoint: dict):
        # Workspace is task-owned. Original project paths are never touched here.
        self._validate_ownership()
        current = self._manifest(self.workspace)
        previous = checkpoint["files"]
        for rel in current.keys() | previous.keys():
            if current.get(rel) == previous.get(rel):
                continue
            if rel in previous:
                data = _bounded_bytes(confined_path(checkpoint["folder"], rel), self.limits, self.cancel_check)
                if digest(data) != previous[rel]:
                    raise ChangeSetConflict(f"checkpoint damaged: {rel}")
                replace_checked(self.workspace, rel, data, current.get(rel))
                confined_path(self.workspace, rel).chmod(stat.S_IMODE(confined_path(checkpoint["folder"], rel).stat().st_mode))
            else:
                target = confined_path(self.workspace, rel)
                if stream_source(target, self.limits, self.cancel_check)[0] != current[rel]:
                    raise ChangeSetConflict(f"workspace recovery conflict: {rel}")
                target.unlink()
        self.state["approved"] = checkpoint["approved"]
        self.state["approved_hashes"] = checkpoint["approved_hashes"]
        self._save()

    def _write_target(self, rel: str, data: bytes | None):
        expected = self.state["baseline"].get(rel)
        if data is None:
            target = confined_path(self.project, rel)
            if stream_source(target, self.limits, self.cancel_check)[0] != expected:
                raise ChangeSetConflict(f"conflict deleting {rel}")
            target.unlink()
        else:
            replace_checked(self.project, rel, data, expected)
            # Newly created/moved source inherits its verified workspace mode.
            # Existing targets keep replace_checked's permission-preserving rule.
            mode = self.state["changes"].get(rel, {}).get("after_mode")
            if expected is None and mode is not None:
                confined_path(self.project, rel).chmod(mode)

    @mutation_guard("project", publication=True)
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
        require_disk_space(self.directory, sum(confined_path(self.project, rel).stat().st_size
                                              for rel in changed if rel in baseline) + len(changed) * 1024,
                           self.limits)
        for rel in changed:
            if rel in baseline:
                data = _bounded_bytes(confined_path(self.project, rel), self.limits, self.cancel_check)
                if digest(data) != baseline[rel]:
                    raise ChangeSetConflict(f"original changed while preparing: {rel}")
                backup = confined_path(backup_root, rel)
                backup.parent.mkdir(parents=True, exist_ok=True)
                write_bytes(backup, data)
        self.state["status"] = "publishing"
        self._save()  # Flushed intents/backups precede source writes; see durability contract.
        try:
            for rel in changed:
                cancel_check()
                data = (_bounded_bytes(confined_path(self.workspace, rel), self.limits, self.cancel_check)
                        if rel in staged else None)
                if data is not None and digest(data) != staged[rel]:
                    raise ChangeSetConflict(f"workspace changed while publishing: {rel}")
                self._write_target(rel, data)
            if self._manifest(self.project, Path(self.state["excluded"])) != staged:
                raise ChangeSetConflict("project changed during publication")
            if finalize is not None:
                finalize()
            if self._manifest(self.project, Path(self.state["excluded"])) != staged:
                raise ChangeSetConflict("project changed during finalization")
            self.state["status"] = "published"
            cancel_check()
            self._save()
        except Exception:
            self.state["status"] = "publishing"
            self._recover()  # the publication lease is already held; recovery runs under it
            raise

    @mutation_guard("project", publication=True)
    def recover(self):
        """Restore only bytes written by this publication; never overwrite a third version."""
        self._recover()

    def _recover(self):
        self._validate_ownership()
        if self.state["status"] not in {"publishing", "recovery_conflict"}:
            raise ChangeSetConflict("changeset does not need publication recovery")
        conflicts = []
        for rel, hashes in self.state["changes"].items():
            try:
                target = confined_path(self.project, rel)
                current = stream_source(target, self.limits, lambda: None)[0] if target.exists() else None
                if current == hashes["before"]:
                    continue
                if current != hashes["after"]:
                    conflicts.append(rel)
                    continue
                if hashes["before"] is None:
                    # Recheck immediately before removing our newly created file.
                    if stream_source(target, self.limits, lambda: None)[0] != hashes["after"]:
                        raise ChangeSetConflict(f"recovery conflict: {rel}")
                    target.unlink()
                else:
                    data = _bounded_bytes(confined_path(self.directory / "before", rel), self.limits, lambda: None)
                    if digest(data) != hashes["before"]:
                        raise ChangeSetConflict(f"backup damaged: {rel}")
                    replace_checked(self.project, rel, data, hashes["after"])
                    if hashes.get("before_mode") is not None:
                        confined_path(self.project, rel).chmod(hashes["before_mode"])
            except (OSError, ResourceLimitError):
                conflicts.append(rel)
        self.state["status"] = "recovery_conflict" if conflicts else "rolled_back"
        self.state["conflicts"] = conflicts
        self._save()
        if conflicts:
            raise ChangeSetConflict(f"recovery needs review; preserved conflicting files: {conflicts}")

    def verify_published(self):
        if self._manifest(self.project, Path(self.state["excluded"])) != self.state.get("published_manifest"):
            raise ChangeSetConflict("published files changed before task completion was recorded")
