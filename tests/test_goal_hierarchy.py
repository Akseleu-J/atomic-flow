"""P05 bounded goal metadata stays inside existing review authority."""
import json
from dataclasses import asdict
from types import SimpleNamespace

import pytest

from atomic_flow.agents.changeset import ChangeSetConflict
from atomic_flow.agents.frontier import task_frontier
from atomic_flow.agents.goals import Goal, replan_requirements
from atomic_flow.agents.plan_review import canonical_plan, require_approval
from atomic_flow.agents.planner import Plan, PlanGoal, PlanStep
from atomic_flow.agents.task import Step, Task, load, save
from atomic_flow.agents.task_loop import TaskRunner
from atomic_flow.state import StateError


def plan():
    return Plan(goals=[PlanGoal(id="root", description="Overall objective", acceptance_criteria=["Report source facts"]),
                       PlanGoal(id="inspect", parent_id="root", description="Inspect current source",
                                acceptance_criteria=["Identify the current value"])],
                steps=[PlanStep(description="Inspect a.py source", verify_with="none", tools_needed=["read_file"],
                                parent_goal="inspect", acceptance_criteria=["Cite a.py"])])


def staged(tmp_path):
    (tmp_path / "a.py").write_text("A = 1\n", encoding="utf-8")
    proposal = plan()
    task = Task.new("inspect", tmp_path, TaskRunner._steps(proposal))
    task.goals = TaskRunner._goals(proposal)
    task.status = "paused"
    save(task)
    return task, TaskRunner(None, tmp_path, None)


def test_roundtrip_preserves_stable_goal_ids_and_ancestor_requirements(tmp_path):
    task, _ = staged(tmp_path)
    restored = load(task.id)
    assert restored == task
    next_step = task_frontier(restored)["next_steps"][0]
    assert next_step["id"] == f"{task.id}:0"
    assert next_step["parent_goal"] == "p0:inspect"
    assert [g["id"] for g in next_step["ancestor_goals"]] == ["p0:root", "p0:inspect"]
    assert next_step["acceptance_criteria"] == ["Cite a.py"]
    restored.steps[0].status = "done"
    progress = task_frontier(restored)["goals"]
    assert all(g["all_active_steps_done"] for g in progress)
    assert all(g["criteria_status"] == "not_assessed" for g in progress)


@pytest.mark.parametrize("change", ["goal_criterion", "step_criterion", "parent"])
def test_goal_and_step_requirement_changes_stale_approval(tmp_path, change):
    task, runner = staged(tmp_path)
    review = runner.review(task, allowed_paths=["a.py"])
    task = runner.approve(task, review["digest"], allowed_paths=["a.py"])
    if change == "goal_criterion":
        task.goals[0].acceptance_criteria = ["Different requirement"]
    elif change == "step_criterion":
        task.steps[0].acceptance_criteria = ["Different step requirement"]
    else:
        # Swap the two levels while keeping every goal covered. An orphaned
        # hierarchy is rejected structurally before stale-approval comparison.
        task.goals[0].parent_id = "p0:inspect"
        task.goals[1].parent_id = ""
        task.steps[0].parent_goal = "p0:root"
    with pytest.raises(ChangeSetConflict, match="stale"):
        require_approval(task, tmp_path)


def test_legacy_canonical_plan_is_byte_compatible_and_migration_grants_nothing(tmp_path):
    task = Task.new("legacy", tmp_path, [Step(0, "Legacy source check", verify_with="none")])
    expected = {"query": "legacy", "rationale": "", "steps": [
        {"index": 0, "description": "Legacy source check", "tools_needed": [], "expected_outcome": "",
         "verify_with": "none", "depends_on": [], "superseded": False}]}
    assert canonical_plan(task) == expected
    path = save(task)
    raw = asdict(task)
    raw.pop("goals")
    for step in raw["steps"]:
        step.pop("parent_goal")
        step.pop("acceptance_criteria")
    raw["schema_version"] = 1
    raw["plan_approval"] = {"digest": "must not grant authority"}
    path.write_text(json.dumps(raw), encoding="utf-8")
    restored = load(task.id)
    assert not restored.plan_approval and restored.goals == []
    assert canonical_plan(restored) == expected


@pytest.mark.parametrize("kind", ["cycle", "missing_parent", "uncovered", "depth", "count", "duplicate", "criteria"])
def test_invalid_decomposition_is_rejected(kind):
    raw = plan().model_dump()
    if kind == "cycle":
        raw["goals"][0]["parent_id"] = "inspect"
    elif kind == "missing_parent":
        raw["steps"][0]["parent_goal"] = "absent"
    elif kind == "uncovered":
        raw["goals"].append({"id": "extra", "description": "Uncovered goal", "acceptance_criteria": ["Missing"]})
    elif kind == "depth":
        raw["goals"] = [{"id": f"g{i}", "parent_id": f"g{i-1}" if i else "",
                         "description": "Deep goal", "acceptance_criteria": ["Requirement"]} for i in range(5)]
        raw["steps"][0]["parent_goal"] = "g4"
    elif kind == "count":
        raw["goals"] *= 17
    elif kind == "duplicate":
        raw["goals"].append(raw["goals"][0])
    else:
        raw["goals"][0]["acceptance_criteria"] = ["x" * 301]
    with pytest.raises(ValueError):
        Plan.model_validate(raw)


def test_persisted_goal_validation_rejects_cross_generation_and_unknown_fields(tmp_path):
    task, _ = staged(tmp_path)
    task.goals[1].parent_id = "p1:root"
    with pytest.raises(StateError, match="parent|generation"):
        save(task)
    task.goals[1].parent_id = "p0:root"
    path = save(task)
    raw = asdict(task)
    raw["goals"][0]["permission"] = "execute anything"
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(StateError, match="goal fields"):
        load(task.id)


def test_revise_after_completed_work_keeps_history_and_namespaces_new_goals(tmp_path):
    task, runner = staged(tmp_path)
    task.steps[0].status = "done"
    save(task)
    revised = runner.revise(task, plan())
    assert [g.id for g in revised.goals] == ["p0:root", "p0:inspect", "p1:root", "p1:inspect"]
    assert revised.steps[0].status == "done" and revised.steps[1].parent_goal == "p1:inspect"
    assert not revised.plan_approval


def test_hierarchical_internal_replan_cannot_drop_original_requirements(tmp_path, monkeypatch):
    task, runner = staged(tmp_path)
    review = runner.review(task, allowed_paths=["a.py"], allow_internal_replan=True)
    task = runner.approve(task, review["digest"], allowed_paths=["a.py"], allow_internal_replan=True)
    calls = []
    planning = {}

    def execute(task, step):
        calls.append(step.index)
        raise runner._check_failure(step, "first approach failed", "pytest", 1)

    monkeypatch.setattr(runner, "_execute_step", execute)
    def replan(*args, **kwargs):
        planning.update(json.loads(kwargs["requirements_context"]))
        return Plan(steps=[PlanStep(description="Alternative source reading", verify_with="none",
                                    tools_needed=["read_file"])])

    monkeypatch.setattr("atomic_flow.agents.task_loop.replan_task", replan)
    result = runner.resume(task)
    # Lineage gate names every dropped obligation (stronger than the old
    # blanket "goal/acceptance obligations" message).
    assert result.status == "paused"
    assert all(f"criterion lost by replan: {criterion!r}" in result.error
               for criterion in ("Report source facts", "Cite a.py", "Identify the current value"))
    assert calls == [0] and not result.plan_approval
    assert result.goals[0].acceptance_criteria == ["Report source facts"]
    assert planning["goals"][0]["acceptance_criteria"] == ["Report source facts"]
    assert planning["steps"][0]["acceptance_criteria"] == ["Cite a.py"]


def test_task_goal_history_is_bounded(tmp_path):
    task = Task.new("history", tmp_path, [])
    task.goals = [Goal(f"p{i}:root", "History goal", acceptance_criteria=["Requirement"]) for i in range(129)]
    with pytest.raises(StateError, match="goals"):
        save(task)


def test_requirements_reach_step_context_without_claiming_semantic_acceptance(tmp_path, monkeypatch):
    task, runner = staged(tmp_path)
    captured = {}

    class Orchestrator:
        def __init__(self, *args, **kwargs):
            pass

        def answer(self, query, **kwargs):
            captured["query"] = query
            captured["retrieval_query"] = kwargs["retrieval_query"]
            return SimpleNamespace(tool_calls=[], ledger_id="req_observation")

    monkeypatch.setattr("atomic_flow.agents.task_loop.Orchestrator", Orchestrator)
    report = runner.run_plan(task.query, plan(), allowed_paths=["a.py"])
    assert report.status == "done", report.error
    assert report.completion_report["declared_criteria"]["status"] == "not_assessed"
    assert "Report source facts" in captured["query"] and "Cite a.py" in captured["query"]
    assert "Reviewed requirements" in captured["query"]
    assert captured["retrieval_query"] == "[Step 1/1] Inspect a.py source"
    assert all(goal["criteria_status"] == "not_assessed" for goal in task_frontier(report)["goals"])


def test_replan_requirement_context_refuses_overflow_instead_of_truncating():
    goals = [Goal(f"p0:g{i}", "Large requirement set", acceptance_criteria=[str(i) + str(j) + "x" * 290
                                                                           for j in range(8)])
             for i in range(20)]
    steps = [Step(i, "Inspect source", parent_goal=g.id) for i, g in enumerate(goals)]
    with pytest.raises(StateError, match="32000"):
        replan_requirements(goals, steps)


def test_completed_goal_criteria_remain_observations_in_replan_context(tmp_path):
    task, _ = staged(tmp_path)
    task.steps[0].status = "done"
    context = json.loads(replan_requirements(task.goals, task.steps))
    assert context["steps"] == []
    assert len(context["goals"]) == 2
    assert context["goals"][0]["observations"]["criteria_status"] == "not_assessed"
    assert context["goals"][0]["observations"]["all_active_steps_done"]


@pytest.mark.parametrize("kind", ["uncovered", "generation_count"])
def test_saved_state_enforces_decomposition_limits_too(tmp_path, kind):
    task, _ = staged(tmp_path)
    if kind == "uncovered":
        task.goals.append(Goal("p0:extra", "Uncovered goal", acceptance_criteria=["Requirement"]))
    else:
        task.goals = [Goal(f"p0:g{i}", "Another goal", acceptance_criteria=["Requirement"]) for i in range(33)]
        task.steps = [Step(i, "Source inspection", parent_goal=g.id) for i, g in enumerate(task.goals)]
    with pytest.raises(StateError, match="descendant|count"):
        save(task)
