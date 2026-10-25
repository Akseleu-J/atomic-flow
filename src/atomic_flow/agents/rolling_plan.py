"""Pure, bounded proposals to refine one pending step; never execution approval."""
from __future__ import annotations

from copy import deepcopy

from atomic_flow.agents.goals import validate_goals
from atomic_flow.agents.planner import Plan
from atomic_flow.agents.task import Step, Task, validate_dependencies
from atomic_flow.state import StateError

MAX_STEPS = 128
MAX_EXPANSIONS = 8
_VERIFY = {"none": 0, "py_compile": 1, "pytest": 2}


def propose_expansion(task: Task, index: int, plan: Plan) -> Task:
    """Replace a pending placeholder with 1–3 steps in an unapproved deep copy.

    Plan dependency indexes are local to the expansion. Roots retain the original
    prerequisites; outgoing edges wait for every new terminal. Existing indexes,
    results and goal requirements stay stable. Descriptions remain review input,
    not a proof that their meaning stays inside the original objective.

    Per-step spending caps cannot currently be shared across children, so capped
    placeholders are refused rather than multiplying their spending allowance.
    Goal/task budgets and source permissions remain enforced by the host.
    """
    if not isinstance(plan, Plan):
        raise ValueError("expansion requires a Plan")
    plan = Plan.model_validate(plan.model_dump())
    if plan.goals:
        raise ValueError("expansion cannot rewrite goals")
    if not 1 <= len(plan.steps) <= 3:
        raise ValueError("expansion requires 1–3 concrete steps")
    if len(task.steps) + len(plan.steps) > MAX_STEPS:
        raise ValueError("expansion exceeds 128 historical steps")
    count = getattr(task, "expansion_count", 0)
    if type(count) is not int or not 0 <= count < MAX_EXPANSIONS:
        raise ValueError("expansion limit is 8")
    if type(index) is not int or index < 0:
        raise ValueError("invalid placeholder index")
    if task.status not in {"running", "paused"}:
        raise ValueError("only active tasks may be expanded")
    validate_dependencies(task.steps)
    validate_goals(task.goals, task.steps)
    if any(type(s.index) is not int or s.index < 0 for s in task.steps):
        raise StateError("invalid task step index")
    by_id = {step.index: step for step in task.steps}
    if index not in by_id:
        raise ValueError("missing expansion placeholder")
    original = by_id[index]
    if (original.status != "pending" or original.request_id or original.started_at is not None
            or original.finished_at is not None or original.result_summary):
        raise ValueError("expansion requires an unexecuted pending placeholder")
    if any(step.status == "running" for step in task.steps):
        raise ValueError("reconcile running work before expansion")
    if any(getattr(original, name, None) is not None for name in ("max_model_calls", "max_budget_usd")):
        raise ValueError("cannot expand a placeholder with per-step spending caps")
    if any(index in step.depends_on and step.status not in {"pending", "blocked", "superseded"}
           for step in task.steps):
        raise ValueError("cannot rewrite dependencies of executed work")
    if original.verify_with not in _VERIFY:
        raise ValueError("unknown placeholder verification obligation")

    children: list[Step] = []
    first_index = max(by_id, default=-1) + 1
    local_dependencies = []
    for offset, proposed in enumerate(plan.steps):
        if proposed.parent_goal and proposed.parent_goal != original.parent_goal:
            raise ValueError("expansion cannot change parent goal")
        if not set(proposed.acceptance_criteria).issubset(original.acceptance_criteria):
            raise ValueError("expansion cannot rewrite acceptance criteria")
        if set(proposed.tools_needed) != set(original.tools_needed):
            raise ValueError("expansion must preserve tool obligations")
        if _VERIFY[proposed.verify_with] < _VERIFY[original.verify_with]:
            raise ValueError("expansion cannot weaken verification")
        for name in ("max_model_calls", "max_budget_usd"):
            if getattr(proposed, name, None) is not None:
                raise ValueError("expansion cannot introduce per-step spending caps")
        proposed_scope = getattr(proposed, "scope_paths", [])
        if proposed_scope and not set(proposed_scope).issubset(getattr(original, "scope_paths", [])):
            raise ValueError("expansion cannot widen source scope")
        dependencies = proposed.depends_on
        if dependencies is None:
            dependencies = [offset - 1] if offset else []
        local_dependencies.append(dependencies)
        child = deepcopy(original)
        child.index = first_index + offset
        child.description = proposed.description
        child.expected_outcome = proposed.expected_outcome
        child.verify_with = proposed.verify_with
        child.depends_on = ([first_index + d for d in dependencies] if dependencies
                            else list(original.depends_on))
        child.blocked_reason = ""
        child.invalidated_reason = ""
        if proposed_scope:
            child.scope_paths = list(proposed_scope)
        for name in ("linked_code_nodes", "linked_test_nodes"):
            if hasattr(child, name):
                links = list(dict.fromkeys([*getattr(original, name), *getattr(proposed, name, [])]))
                if len(links) > 64:
                    raise ValueError("expanded evidence links exceed 64 nodes")
                setattr(child, name, links)
        if hasattr(child, "evidence"):
            child.evidence = {}
        children.append(child)

    depended_on = {dependency for dependencies in local_dependencies for dependency in dependencies}
    terminals = [child.index for offset, child in enumerate(children) if offset not in depended_on]
    result = deepcopy(task)
    for step in result.steps:
        if step.index == index:
            step.status = "superseded"
        elif index in step.depends_on:
            step.depends_on = list(dict.fromkeys(
                dependency for old in step.depends_on for dependency in (terminals if old == index else [old])
            ))
    result.steps.extend(children)
    result.expansion_count = count + 1
    result.plan_approval = {}
    result.completion_report = {}
    result.status = "paused"
    result.error = "Expanded plan requires fresh source review and approval."
    result.finished_at = None
    validate_dependencies(result.steps)
    validate_goals(result.goals, result.steps)
    return result
