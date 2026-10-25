"""Bounded goal hierarchy metadata; never an execution permission."""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from atomic_flow.state import StateError


@dataclass
class Goal:
    id: str
    description: str
    parent_id: str = ""
    acceptance_criteria: list[str] = field(default_factory=list)
    scope_paths: list[str] | None = None
    max_model_calls: int | None = None
    max_budget_usd: float | None = None


def validate_paths(paths):
    if not isinstance(paths, list) or len(paths) > 64 or any(not isinstance(p, str) for p in paths):
        raise StateError("invalid goal/step scope paths")
    for path in paths:
        pure = PurePosixPath(path)
        if (not path or len(path) > 512 or any(c in path for c in "\\:*?[]\x00")
                or pure.is_absolute() or pure.as_posix() != path or not pure.parts
                or any(part in {".", ".."} for part in pure.parts)):
            raise StateError("scope requires exact project-relative POSIX file paths")
    if len(paths) != len(set(paths)):
        raise StateError("duplicate goal/step scope path")


def validate_links(links):
    if (not isinstance(links, list) or len(links) > 64
            or any(not isinstance(node, str) or not node or len(node.encode("utf-8")) > 256 for node in links)):
        raise StateError("invalid linked code/test nodes")
    validate_paths(list(dict.fromkeys(node.split("::", 1)[0] for node in links)))
    if len(set(links)) != len(links):
        raise StateError("duplicate linked code/test node")


def step_scope(task, step, reviewed_paths):
    """Intersect reviewed authority with every ancestor and current step scope."""
    allowed = set(reviewed_paths)
    for item in goal_context(task.goals, step):
        scope = item["scope_paths"]
        if scope is not None:
            if set(scope) - allowed:
                raise StateError("goal scope exceeds its reviewed parent scope")
            allowed.intersection_update(scope)
    if step.scope_paths:
        if set(step.scope_paths) - allowed:
            raise StateError("step scope exceeds its reviewed goal scope")
        allowed.intersection_update(step.scope_paths)
    return frozenset(allowed)


def validate_criteria(criteria):
    if (not isinstance(criteria, list) or len(criteria) > 8
            or any(not isinstance(item, str) or not item.strip() or len(item) > 300 for item in criteria)
            or len(set(criteria)) != len(criteria)):
        raise StateError("invalid acceptance criteria: at most 8 unique nonempty texts of 300 characters")


def validate_goals(goals, steps):
    """Historical plans share a bounded task-local namespace, max depth four."""
    if len(goals) > 128:
        raise StateError("task goal history exceeds 128 nodes")
    by_id = {}
    generations = {}
    for goal in goals:
        if (not isinstance(goal.id, str) or not re.fullmatch(r"p[0-9]+:[A-Za-z][A-Za-z0-9_-]{0,31}", goal.id)
                or len(goal.id) > 64 or goal.id in by_id):
            raise StateError("invalid or duplicate task goal id")
        if (not isinstance(goal.description, str) or not goal.description.strip() or not 5 <= len(goal.description) <= 500
                or not isinstance(goal.parent_id, str)):
            raise StateError("invalid task goal description/parent")
        validate_criteria(goal.acceptance_criteria)
        if goal.scope_paths is not None:
            validate_paths(goal.scope_paths)
        if goal.max_model_calls is not None and (type(goal.max_model_calls) is not int or not 1 <= goal.max_model_calls <= 1000):
            raise StateError("invalid goal model-call cap")
        if goal.max_budget_usd is not None and (type(goal.max_budget_usd) not in (int, float)
                or not math.isfinite(goal.max_budget_usd) or not 0 <= goal.max_budget_usd <= 1_000_000):
            raise StateError("invalid goal budget cap")
        if not goal.acceptance_criteria:
            raise StateError("goal requires acceptance criteria")
        generation = goal.id.split(":", 1)[0]
        generations[generation] = generations.get(generation, 0) + 1
        if generations[generation] > 32:
            raise StateError("plan goal count exceeds 32 nodes")
        by_id[goal.id] = goal
    for goal in goals:
        seen = set()
        current = goal.id
        while current:
            if current in seen:
                raise StateError("cyclic task goal hierarchy")
            if current not in by_id:
                raise StateError("missing task parent goal")
            seen.add(current)
            if len(seen) > 4:
                raise StateError("task goal hierarchy exceeds depth 4")
            parent = by_id[current].parent_id
            if parent and parent.split(":", 1)[0] != goal.id.split(":", 1)[0]:
                raise StateError("goal parent crosses plan generation")
            current = parent
    covered = set()
    for step in steps:
        validate_criteria(step.acceptance_criteria)
        validate_paths(step.scope_paths)
        validate_links(step.linked_code_nodes)
        validate_links(step.linked_test_nodes)
        if not isinstance(step.parent_goal, str) or (step.parent_goal and step.parent_goal not in by_id):
            raise StateError("missing or invalid step parent goal")
        current = step.parent_goal
        while current:
            covered.add(current)
            current = by_id[current].parent_id
    if set(by_id) - covered:
        raise StateError("every goal needs an executable descendant step")


def goal_context(goals, step):
    """Ordered ancestor requirements for one executable step."""
    by_id = {goal.id: goal for goal in goals}
    chain = []
    current = step.parent_goal
    while current:
        goal = by_id[current]
        chain.append({"id": goal.id, "description": goal.description,
                      "acceptance_criteria": list(goal.acceptance_criteria), "scope_paths": goal.scope_paths,
                      "max_model_calls": goal.max_model_calls, "max_budget_usd": goal.max_budget_usd})
        current = goal.parent_id
    return list(reversed(chain))


def goal_progress(goals, steps):
    """Aggregate observations without treating completed steps as semantic proof."""
    by_id = {goal.id: goal for goal in goals}
    counts = {goal.id: {} for goal in goals}
    for step in steps:
        if step.status == "superseded":
            continue
        current = step.parent_goal
        while current:
            counts[current][step.status] = counts[current].get(step.status, 0) + 1
            current = by_id[current].parent_id
    return [{"id": goal.id, "parent_id": goal.parent_id, "step_counts": counts[goal.id],
             "all_active_steps_done": bool(counts[goal.id]) and set(counts[goal.id]) == {"done"},
             "criteria_status": "not_assessed"} for goal in goals]


def replan_requirements(goals, steps):
    """Remaining-step requirements and completed goal observations, bounded."""
    import json

    active = [step for step in steps if step.status not in {"done", "superseded"}]
    needed = {item["id"] for step in active for item in goal_context(goals, step)}
    observations = {item["id"]: item for item in goal_progress(goals, steps)}
    # Done steps are not permission to claim their criteria independently met.
    # Preserve those goal requirements too, without requesting action replay.
    needed.update(key for key, item in observations.items() if item["step_counts"])
    payload = {"goals": [{"id": goal.id, "parent_id": goal.parent_id,
                           "description": goal.description, "acceptance_criteria": goal.acceptance_criteria,
                           "observations": observations[goal.id]}
                          for goal in goals if goal.id in needed],
               "steps": [{"index": step.index, "parent_goal": step.parent_goal,
                          "acceptance_criteria": step.acceptance_criteria} for step in active
                         if step.parent_goal or step.acceptance_criteria]}
    text = json.dumps(payload, ensure_ascii=False)
    if len(text) > 32000:
        raise StateError("reviewed requirement context exceeds 32000 characters; narrow the remaining plan")
    return text
