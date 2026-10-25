"""Replacement generations preserve original authority and spent goal allowance."""
import copy

import pytest
from test_repair_budget import MESSAGES, Responses

from atomic_flow.agents.planner import Plan, PlanGoal, PlanStep
from atomic_flow.agents.task import Task, load, save
from atomic_flow.agents.task_loop import TaskRunner
from atomic_flow.config import AtomicConfig


def staged(root, *, calls=1, budget=1.0):
    (root / "a.py").write_text("A = 1\n", encoding="utf-8")
    goal = PlanGoal(id="root", description="Inspect source safely", acceptance_criteria=["Report the constant"],
                    scope_paths=["a.py"], max_model_calls=calls, max_budget_usd=budget)
    step = PlanStep(description="Read the source", parent_goal="root", acceptance_criteria=goal.acceptance_criteria,
                    tools_needed=["read_file"], verify_with="none", scope_paths=["a.py"],
                    linked_test_nodes=["a.py"])
    plan = Plan(goals=[goal], steps=[step])
    task = Task.new("inspect safely", root, TaskRunner._steps(plan))
    task.goals = TaskRunner._goals(plan)
    task.status = "paused"
    save(task)
    backend = Responses()
    runner = TaskRunner(AtomicConfig(), root, backend)
    review = runner.review(task, allowed_paths=["a.py"], allow_internal_replan=True)
    task = runner.approve(task, review["digest"], allowed_paths=["a.py"], allow_internal_replan=True)
    return task, runner, backend, plan


@pytest.mark.parametrize("flat", [False, True])
@pytest.mark.parametrize("limit", ["calls", "usd"])
def test_original_goal_cap_survives_replan_and_restart(tmp_path, monkeypatch, flat, limit):
    task, runner, backend, plan = staged(tmp_path, calls=2 if limit == "calls" else 20,
                                        budget=0.0025 if limit == "usd" else 1.0)
    replacement = copy.deepcopy(plan)
    replacement.steps[0].description = "Read the source another way"
    if flat:
        replacement.goals = []
        replacement.steps[0].parent_goal = ""

    def execute(task_, step):
        runner.task_backend.chat(MESSAGES)
        if step.index == 0:
            raise runner._check_failure(step, "fixture pytest failure", "pytest", 1)
        pytest.fail("replacement received a fresh goal call allowance")

    monkeypatch.setattr(runner, "_execute_step", execute)
    def replan(*args, **kwargs):
        kwargs["backend"].chat(MESSAGES)  # The real planner reservation also consumes the original goal cap.
        return replacement

    monkeypatch.setattr("atomic_flow.agents.task_loop.replan_task", replan)
    result = runner.resume(task)
    assert result.status == "paused" and "goal p0:root" in result.error
    assert ("model-call cap" if limit == "calls" else "budget cannot cover reservation") in result.error
    assert len(backend.calls) == 2 and result.steps[-1].parent_goal == "p0:root"
    assert len(result.goals) == 1 and all(e["goal_ids"] == ["p0:root"] for e in result.budget_events)
    assert result.budget_spent == 0.001
    resumed = TaskRunner(runner.config, tmp_path, backend).resume(load(task.id))
    assert resumed.status == "paused" and len(backend.calls) == 2
    assert not resumed.completion_report.get("ready_to_publish")


@pytest.mark.parametrize("change", ["cap", "scope", "identity", "test_link", "step_scope"])
def test_replacement_cannot_silently_relax_original_constraints(tmp_path, monkeypatch, change):
    task, runner, backend, plan = staged(tmp_path, calls=20)
    replacement = copy.deepcopy(plan)
    replacement.steps[0].description = "Read the source another way"
    if change == "cap":
        replacement.goals[0].max_model_calls = 21
    elif change == "scope":
        replacement.goals[0].scope_paths = None
    elif change == "identity":
        replacement.goals[0].id = "fresh"
        replacement.steps[0].parent_goal = "fresh"
    elif change == "test_link":
        replacement.steps[0].linked_test_nodes = []
    else:
        replacement.steps[0].scope_paths = ["b.py"]
    seen = []

    def execute(task_, step):
        seen.append(step.index)
        raise runner._check_failure(step, "fixture pytest failure", "pytest", 1)

    monkeypatch.setattr(runner, "_execute_step", execute)
    monkeypatch.setattr("atomic_flow.agents.task_loop.replan_task", lambda *a, **kw: replacement)
    result = runner.resume(task)
    assert result.status == "paused" and not result.plan_approval
    assert seen == [0] and not backend.calls
    assert not result.completion_report.get("ready_to_publish")
    assert (tmp_path / "a.py").read_text() == "A = 1\n"


@pytest.mark.parametrize("drift", ["source", "policy", "capabilities", "executor_profile"])
def test_opted_in_runtime_drift_pauses_before_any_call_or_effect(tmp_path, monkeypatch, drift):
    task, runner, backend, _plan = staged(tmp_path)
    if drift == "source":
        (tmp_path / "a.py").write_text("A = 9\n", encoding="utf-8")
    elif drift == "policy":
        from atomic_flow.config import user_dir
        path = user_dir() / "policy.yaml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# Host policy changed after approval\n", encoding="utf-8")
    elif drift == "capabilities":
        runner.capability_factory = lambda *_args: pytest.fail("unreviewed capability was instantiated")
    else:
        runner.config.execution.profile = "read-only"
    monkeypatch.setattr(runner, "_execute_step", lambda *_args: pytest.fail("drift executed a step"))
    monkeypatch.setattr("atomic_flow.agents.task_loop.replan_task", lambda *a, **kw: pytest.fail("drift replanned"))
    result = runner.resume(task)
    assert result.status == "paused" and not backend.calls
    assert not result.completion_report.get("ready_to_publish")
