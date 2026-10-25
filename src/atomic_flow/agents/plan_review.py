"""Host plan approval bound to reviewed source bytes and exact path authority."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath

from atomic_flow.agents.changeset import ChangeSet, ChangeSetConflict, _files
from atomic_flow.agents.goals import step_scope, validate_goals
from atomic_flow.agents.source import confined_path
from atomic_flow.agents.task import validate_dependencies
from atomic_flow.config import user_dir


def canonical_plan(task):
    validate_dependencies(task.steps)
    validate_goals(task.goals, task.steps)
    result = {"query": task.query, "rationale": task.rationale, "steps": [
        {"index": s.index, "description": s.description, "tools_needed": list(s.tools_needed),
         "expected_outcome": s.expected_outcome, "verify_with": s.verify_with,
         "depends_on": list(s.depends_on), "superseded": s.status == "superseded",
         **({"parent_goal": s.parent_goal} if s.parent_goal else {}),
         **({"scope_paths": list(s.scope_paths)} if s.scope_paths else {}),
         **({"linked_code_nodes": list(s.linked_code_nodes)} if s.linked_code_nodes else {}),
         **({"linked_test_nodes": list(s.linked_test_nodes)} if s.linked_test_nodes else {}),
         **({"acceptance_criteria": list(s.acceptance_criteria)} if s.acceptance_criteria else {})}
        for s in task.steps]}
    # Omitting empty optional metadata keeps legacy schema-2 approval digests valid.
    if task.goals:
        from dataclasses import asdict
        result["goals"] = [{k: v for k, v in asdict(g).items()
                            if k not in {"scope_paths", "max_model_calls", "max_budget_usd"} or v is not None}
                           for g in task.goals]
    if task.expansion_count:
        result["expansion_count"] = task.expansion_count
    if task.goals or any(s.acceptance_criteria for s in task.steps):
        result["goal_schema_version"] = 1
    return result


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


def review_payload(task, project, allowed_paths=None, *, internal_replan=None):
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
    for step in task.steps:
        if step.status != "superseded":
            step_scope(task, step, paths)
    if workspace is not None:
        changed = {p for p in source.keys() | workspace.keys() if source.get(p) != workspace.get(p)}
        if changed - set(paths):
            raise ChangeSetConflict("review scope excludes existing staged changes")
    payload = {"schema_version": 1, "task_id": task.id, "project": str(project.resolve()), "plan": canonical_plan(task),
               "source_manifest": source, "allowed_paths": paths,
               "scope_hashes": {p: source.get(p) for p in paths}, "workspace_manifest": workspace}
    if internal_replan is not None:
        from atomic_flow.agents.envelope import validate_envelope
        validate_envelope(internal_replan)
        payload.update(schema_version=2, internal_replan=internal_replan)
    return {**payload, "digest": fingerprint(payload)}


def require_approval(task, project):
    approval = task.plan_approval
    if not approval:
        raise ChangeSetConflict("plan approval required; review and approve its current digest before execution")
    if (approval.get("schema_version") not in {1, 2} or not isinstance(approval.get("allowed_paths"), list)
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
