"""Read-only, bounded rolling horizon over the existing task DAG.

This projection explains scheduling, not permission or semantic correctness.
Request IDs link existing host ledgers; a legacy done step may have no ledger.
"""
from __future__ import annotations

from atomic_flow.agents.goals import goal_context, goal_progress, validate_goals
from atomic_flow.agents.task import Task, validate_dependencies


def task_frontier(task: Task, *, horizon: int = 3) -> dict:
    """Return all runnable IDs and at most three detailed next steps.

    Ordering follows the approved plan's list order, exactly as TaskRunner did.
    No mutation, model calls, read authority or automatic replay is introduced.
    """
    if type(horizon) is not int or not 1 <= horizon <= 3:
        raise ValueError("frontier horizon must be an integer between 1 and 3")
    validate_dependencies(task.steps)
    validate_goals(task.goals, task.steps)
    by_id = {step.index: step for step in task.steps}
    ready, blocked, running, failed = [], [], [], []
    counts = {}
    for step in task.steps:
        counts[step.status] = counts.get(step.status, 0) + 1
        if step.status in {"pending", "blocked"}:
            waiting = [d for d in step.depends_on if by_id[d].status != "done"]
            if waiting:
                blocked.append({"index": step.index, "waiting_for": waiting})
            else:
                ready.append(step.index)
        elif step.status == "running":
            running.append(step.index)
        elif step.status == "failed":
            failed.append(step.index)
    next_steps = []
    for index in ready[:horizon]:
        step = by_id[index]
        next_steps.append({
            "id": f"{task.id}:{index}", "index": index,
            "description": step.description[:500],
            "expected_outcome": step.expected_outcome[:500],
            "verify_with": step.verify_with[:80],
            "parent_goal": step.parent_goal,
            "acceptance_criteria": list(step.acceptance_criteria),
            "ancestor_goals": goal_context(task.goals, step),
            "prerequisites": [
                {"index": d, "request_id": by_id[d].request_id[:128],
                 "verification": by_id[d].verify_with[:80],
                 **({"request_id_truncated": True} if len(by_id[d].request_id) > 128 else {})}
                for d in step.depends_on[:20]
            ],
            "omitted_prerequisites": max(0, len(step.depends_on) - 20),
        })
    if running:
        decision = "reconcile_running"
    elif ready:
        decision = "execute_ready"
    elif failed:
        decision = "replan_failed"
    elif blocked:
        decision = "review_blocked"
    else:
        decision = "no_remaining_steps"
    return {"schema_version": 1, "task_id": task.id, "task_status": task.status, "decision": decision,
            "ready": ready, "blocked": blocked, "running": running, "failed": failed,
            "counts": counts, "next_steps": next_steps,
            "goals": goal_progress(task.goals, task.steps),
            "horizon_steps": rolling_horizon(task, horizon),
            "known_failures": [{"index": s.index, "summary": s.result_summary[:500]}
                               for s in task.steps if s.status == "failed"][:8],
            "omitted_ready": max(0, len(ready) - horizon)}


def rolling_horizon(task: Task, horizon: int) -> list[dict]:
    """Project the next dependency chain without changing states or doing work."""
    completed = {s.index for s in task.steps if s.status == "done"}
    candidates = [s for s in task.steps if s.status in {"pending", "blocked", "running"}]
    result = []
    for _ in range(horizon):
        step = next((s for s in candidates if s.status == "running"), None)
        if step is None:
            step = next((s for s in candidates if all(d in completed for d in s.depends_on)), None)
        if step is None:
            break
        result.append({"id": f"{task.id}:{step.index}", "index": step.index,
                       "description": step.description[:500], "verify_with": step.verify_with[:80],
                       "parent_goal": step.parent_goal, "scope_paths": step.scope_paths,
                       "linked_code_nodes": step.linked_code_nodes, "linked_test_nodes": step.linked_test_nodes,
                       "acceptance_criteria": step.acceptance_criteria, "depends_on": list(step.depends_on),
                       "projection_only": step.status != "running" and any(
                           next(p for p in task.steps if p.index == d).status != "done" for d in step.depends_on)})
        completed.add(step.index)
        candidates.remove(step)
    return result


def frontier_context(task: Task) -> str:
    """Bound model-visible context without implying upcoming work is approved."""
    import json

    view = task_frontier(task)
    payload = {"decision": "current_step_running" if view["running"] else view["decision"],
               "running": view["running"][:3], "next_steps": view["next_steps"],
               "omitted_ready": view["omitted_ready"],
               "blocked_count": len(view["blocked"]), "failed_count": len(view["failed"])}
    payload["horizon_steps"] = view["horizon_steps"]
    text = json.dumps(payload, ensure_ascii=False)
    if len(text) > 5000:
        # Keep valid JSON; do not cut a request ID or dependency halfway through.
        payload["next_steps"] = [{"id": step["id"], "index": step["index"],
                                  "verify_with": step["verify_with"]}
                                 for step in view["next_steps"]]
        payload["details_omitted"] = True
        payload["horizon_steps"] = [{"index": s["index"], "projection_only": s["projection_only"]}
                                    for s in view["horizon_steps"]]
        text = json.dumps(payload, ensure_ascii=False)
    return ("Host task frontier (advisory; execute only the current step; ledger links "
            "are observations, not proof that acceptance criteria are satisfied):\n" + text)
