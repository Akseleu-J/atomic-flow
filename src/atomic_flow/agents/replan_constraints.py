"""Bind provably equivalent replacement goals to their original accounting IDs."""
from __future__ import annotations

from dataclasses import asdict


def inherit_constraints(task, new_goals, new_steps):
    """Return retained proposals and refusal reasons; mutate steps only after proof.

    A new generation is not a fresh allowance. Exact local goal identity and
    metadata permit reuse of the old goal, including its ancestor chain. A flat
    replacement may inherit one unambiguous remaining parent. Other mappings
    require fresh review; no semantic equivalence is inferred from model text.
    """
    active = [s for s in task.steps if s.status not in {"done", "superseded"}]
    by_local = {}
    for goal in task.goals:
        by_local.setdefault(goal.id.split(":", 1)[1], []).append(goal)
    mapping = {}
    reasons = []
    for goal in new_goals:
        matches = by_local.get(goal.id.split(":", 1)[1], [])
        if len(matches) != 1:
            reasons.append("replacement goal identity is new or ambiguous")
            continue
        old = matches[0]
        proposed, original = asdict(goal), asdict(old)
        for value in (proposed, original):
            value.pop("id")
            value["parent_id"] = value["parent_id"].split(":", 1)[-1]
        if proposed != original:
            reasons.append("replacement goal changes reviewed requirements, scope or caps")
        mapping[goal.id] = old.id
    parents = {s.parent_goal for s in active}
    targets = []
    for step in new_steps:
        if step.parent_goal:
            parent = mapping.get(step.parent_goal)
        else:
            parent = next(iter(parents)) if len(parents) == 1 else None
        if parent is None or parent not in parents:
            reasons.append("replacement step has no unambiguous original goal lineage")
            continue
        previous = [s for s in active if s.parent_goal == parent]
        scopes = [set(s.scope_paths) for s in previous if s.scope_paths]
        scope = set.intersection(*scopes) if scopes else None
        if scope is not None and (not scope or (step.scope_paths and set(step.scope_paths) - scope)):
            reasons.append("replacement step widens or ambiguously merges original step scopes")
        targets.append((step, parent, scope))
    for field in ("linked_code_nodes", "linked_test_nodes"):
        for parent in parents:
            required = {node for s in active if s.parent_goal == parent for node in getattr(s, field)}
            covered = {node for s, target, _scope in targets if target == parent for node in getattr(s, field)}
            if required - covered:
                reasons.append(f"replacement loses original {field} obligations")
    if reasons:
        return new_goals, sorted(set(reasons))
    for step, parent, scope in targets:
        step.parent_goal = parent
        if scope is not None and not step.scope_paths:
            step.scope_paths = sorted(scope)
    return [], []
