"""Host plan approval bound to reviewed source bytes and exact path authority."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath

from atomic_flow.agents.changeset import ChangeSet, ChangeSetConflict, _files
from atomic_flow.agents.source import confined_path
from atomic_flow.agents.task import validate_dependencies
from atomic_flow.config import user_dir


def canonical_plan(task):
    validate_dependencies(task.steps)
    return {"query": task.query, "rationale": task.rationale, "steps": [
        {"index": s.index, "description": s.description, "tools_needed": list(s.tools_needed),
         "expected_outcome": s.expected_outcome, "verify_with": s.verify_with,
         "depends_on": list(s.depends_on), "superseded": s.status == "superseded"}
        for s in task.steps]}


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def path_scope(project, paths):
    result = []
    for path in paths:
        if not isinstance(path, str) or not path or any(char in path for char in "\\*?[]:"):
            raise ValueError("scope paths must be exact project-relative POSIX file paths")
        pure = PurePosixPath(path)
        if not pure.parts or pure.is_absolute() or pure.as_posix() != path or any(p in (".", "..") for p in pure.parts):
            raise ValueError("scope paths must be exact project-relative POSIX file paths")
        target = confined_path(project, path)
        if target.is_dir():
            raise ValueError("scope paths must name files, not directories")
        result.append(path)
    return sorted(set(result))


def review_payload(task, project, allowed_paths=None):
    if Path(task.project).resolve() != project.resolve():
        raise ChangeSetConflict("task belongs to another project")
    source = _files(project, user_dir().resolve())
    workspace = None
    if task.changeset_id:
        change = ChangeSet.open(project, task.changeset_id)
        if change.state["status"] != "active":
            raise ChangeSetConflict("closed changeset cannot receive new plan approval")
        change.check_original()
        workspace = change.validate_workspace()
    paths = path_scope(project, sorted(source) if allowed_paths is None else allowed_paths)
    if workspace is not None:
        changed = {p for p in source.keys() | workspace.keys() if source.get(p) != workspace.get(p)}
        if changed - set(paths):
            raise ChangeSetConflict("review scope excludes existing staged changes")
    payload = {"schema_version": 1, "task_id": task.id, "project": str(project.resolve()), "plan": canonical_plan(task),
               "source_manifest": source, "allowed_paths": paths,
               "scope_hashes": {p: source.get(p) for p in paths}, "workspace_manifest": workspace}
    return {**payload, "digest": fingerprint(payload)}


def require_approval(task, project):
    approval = task.plan_approval
    if not approval:
        raise ChangeSetConflict("plan approval required; review and approve its current digest before execution")
    if (approval.get("schema_version") != 1 or not isinstance(approval.get("allowed_paths"), list)
            or not isinstance(approval.get("source_manifest"), dict)):
        raise ChangeSetConflict("invalid plan approval state; review again")
    payload = {k: v for k, v in approval.items() if k != "digest"}
    if approval.get("digest") != fingerprint(payload):
        raise ChangeSetConflict("plan approval digest is invalid; review again")
    if (approval.get("task_id") != task.id or approval.get("project") != str(project.resolve())
            or approval.get("plan") != canonical_plan(task)):
        raise ChangeSetConflict("plan approval is stale; plan or project changed")
    source = _files(project, user_dir().resolve())
    if approval.get("source_manifest") != source:
        raise ChangeSetConflict("plan approval is stale; project source changed")
    return frozenset(path_scope(project, approval["allowed_paths"]))


def require_scope(task, changeset):
    paths = require_approval(task, changeset.project)
    workspace = changeset.validate_workspace()
    baseline = changeset.state["baseline"]
    changed = {p for p in baseline.keys() | workspace.keys() if baseline.get(p) != workspace.get(p)}
    if changed - paths:
        raise ChangeSetConflict("workspace mutations exceed reviewed plan scope")
