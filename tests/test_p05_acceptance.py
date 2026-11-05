"""Headless P05 acceptance uses only host checks and scripted model responses."""
import json

import pytest

from atomic_flow.agents.changeset import ChangeSet
from atomic_flow.agents.control import request_cancel
from atomic_flow.agents.frontier import task_frontier
from atomic_flow.agents.goals import Goal
from atomic_flow.agents.planner import Plan, PlanGoal, PlanStep
from atomic_flow.agents.source import digest
from atomic_flow.agents.step_evidence import receipt_matches
from atomic_flow.agents.task import Task, load, save
from atomic_flow.agents.task_budget import TaskBudgetBackend
from atomic_flow.agents.task_loop import TaskRunner
from atomic_flow.backends.base import ChatResult
from atomic_flow.config import AtomicConfig, BackendConfig
from atomic_flow.errors import BudgetExceeded


class Backend:
    name = "p05-scripted"

    def __init__(self, cost=0.001):
        self.config = BackendConfig(model="test", price_in_per_mtok=1, price_out_per_mtok=1)
        self.calls = []
        self.cost = cost

    def chat(self, *args, **kwargs):
        self.calls.append(1)
        return ChatResult("{}", tokens_in=1, tokens_out=1, cost_usd=self.cost)


def job(tmp_path):
    (tmp_path / "a.py").write_text("A = 1\n")
    (tmp_path / "b.py").write_text("B = 1\n")
    plan = Plan(goals=[PlanGoal(id="root", description="Inspect source", acceptance_criteria=["Report facts"],
                               max_model_calls=1, max_budget_usd=1.0)],
                steps=[PlanStep(description="Inspect a.py", verify_with="none", parent_goal="root"),
                       PlanStep(description="Inspect b.py", verify_with="none", parent_goal="root")])
    task = Task.new("inspect", tmp_path, TaskRunner._steps(plan))
    task.goals = TaskRunner._goals(plan)
    task.status = "paused"
    save(task)
    return task


def test_goal_call_cap_shared_across_steps_and_resume(tmp_path):
    task = job(tmp_path)
    backend = Backend()
    wrapped = TaskBudgetBackend(backend, task, AtomicConfig(), lambda: None)
    wrapped.bind_steps([0])
    wrapped.chat([{"role": "user", "content": "report"}])
    assert task.budget_events[0]["goal_ids"] == ["p0:root"]
    restored = load(task.id)
    again = TaskBudgetBackend(backend, restored, AtomicConfig(), lambda: None)
    again.bind_steps([1])
    with pytest.raises(BudgetExceeded, match="goal .*call"):
        again.chat([{"role": "user", "content": "report"}])
    assert len(backend.calls) == 1


@pytest.mark.parametrize("case", ["parent", "unknown_prices", "reported_overspend", "zero"])
def test_goal_usd_caps_independent_of_global_enforcement(tmp_path, case):
    task = job(tmp_path)
    task.goals[0].max_model_calls = None
    config = AtomicConfig()
    config.task.fail_on_budget_exceeded = False
    config.budget.daily_limit_usd = 0
    backend = Backend(cost=0.5 if case == "reported_overspend" else 0.001)
    if case == "parent":
        task.goals.append(Goal("p0:child", "Child requirement", "p0:root", ["Report facts"]))
        task.steps[0].parent_goal = "p0:child"
        task.goals[0].max_budget_usd = 0.00001
    elif case == "unknown_prices":
        backend.config.price_in_per_mtok = None
    elif case == "zero":
        task.goals[0].max_budget_usd = 0
    else:
        task.goals[0].max_budget_usd = 0.1
    wrapped = TaskBudgetBackend(backend, task, config, lambda: None)
    wrapped.bind_steps([0])
    with pytest.raises(BudgetExceeded, match="goal"):
        wrapped.chat([{"role": "user", "content": "report"}])
    assert len(backend.calls) == (1 if case == "reported_overspend" else 0)
    if case == "reported_overspend":
        assert load(task.id).budget_spent == 0.5


def test_nested_scope_catches_unscoped_host_command_delta_and_rolls_back(tmp_path, monkeypatch):
    task = job(tmp_path)
    task.goals[0].scope_paths = ["a.py"]
    save(task)
    runner = TaskRunner(None, tmp_path, None)
    review = runner.review(task, allowed_paths=["a.py", "b.py"])
    task = runner.approve(task, review["digest"], allowed_paths=["a.py", "b.py"])

    def execute(task, step):
        path = runner.changeset.workspace / "b.py"
        path.write_text("B = 999\n")
        runner.changeset.track("b.py", digest(path.read_bytes()))

    monkeypatch.setattr(runner, "_execute_step", execute)
    result = runner.resume(task)
    assert result.status == "failed" and "goal scope" in result.error
    assert (tmp_path / "b.py").read_text() == "B = 1\n"


def test_empty_goal_scope_denies_writes_and_is_bound_to_review(tmp_path):
    task = job(tmp_path)
    task.goals[0].scope_paths = []
    save(task)
    runner = TaskRunner(None, tmp_path, None)
    restricted = runner.review(task)
    task.goals[0].scope_paths = None
    save(task)
    assert runner.review(task)["digest"] != restricted["digest"]


def test_expand_public_api_preserves_goals_requires_new_review(tmp_path, monkeypatch):
    task = job(tmp_path)
    runner = TaskRunner(None, tmp_path, None)
    approved = runner.approve(task, runner.review(task)["digest"])
    expanded = runner.expand_plan(approved, 0, Plan(steps=[PlanStep(description="Inspect first fact", verify_with="none"),
                                                        PlanStep(description="Inspect second fact", verify_with="none")]))
    assert expanded.status == "paused" and not expanded.plan_approval
    assert expanded.expansion_count == 1 and expanded.steps[1].depends_on == [3]
    monkeypatch.setattr(runner, "_execute_step", lambda *a: pytest.fail("unapproved expansion executed"))
    assert runner.resume(expanded).status == "paused"
    horizon = task_frontier(expanded)["horizon_steps"]
    assert [s["index"] for s in horizon] == [2, 3, 1]
    assert horizon[1]["projection_only"]


def test_done_step_receipt_survives_resume_and_stale_premise_never_replays(tmp_path, monkeypatch):
    task = job(tmp_path)
    runner = TaskRunner(None, tmp_path, None)
    task = runner.approve(task, runner.review(task)["digest"])
    change = ChangeSet.create(tmp_path)
    task.changeset_id = change.id
    task.steps[0].status = "done"
    task.steps[0].request_id = "req_observation"
    runner.changeset = change
    before = change.validate_workspace()
    runner._record_step_evidence(task, task.steps[0], before, before, ["a.py", "b.py"])
    path = change.workspace / "a.py"
    path.write_text("A = 2\n")
    change.track("a.py", digest(path.read_bytes()))
    save(task)
    monkeypatch.setattr(runner, "_execute_step", lambda *a: pytest.fail("stale premise executed"))
    result = runner.resume(task)
    assert result.status == "paused" and "evidence" in result.error
    assert result.steps[0].status == "done"
    assert result.steps[1].status == "pending"


def test_cancelled_task_is_terminal_without_replay(tmp_path):
    task = job(tmp_path)
    request_cancel(task.id)
    runner = TaskRunner(None, tmp_path, None)
    result = runner.resume(task)
    assert result.status == "cancelled"
    assert all(s.status == "cancelled" for s in result.steps)
    assert runner.resume(result).status == "cancelled"


def test_retrieval_and_auto_pytest_premises_are_not_narrowed_by_tool_paths(tmp_path):
    task = job(tmp_path)
    task.steps[0].verify_with = "pytest"
    runner = TaskRunner(None, tmp_path, None)
    runner._step_tool_calls[0] = [
        {"tool": "read_file", "result_summary": {"path": "a.py", "outcome": "success"}},
        {"tool": "edit_node", "result_summary": {"path": "a.py", "outcome": "success", "status": "applied", "checks": ["syntax"]}}]
    source = {"a.py": "a" * 40, "b.py": "b" * 40}
    runner._record_step_evidence(task, task.steps[0], source, source, ["a.py"])
    modified = {**source, "b.py": "c" * 40}
    assert not receipt_matches(task.steps[0].evidence, task.steps[0], modified, task_id=task.id)[0]


def test_changed_step_scope_cannot_reuse_a_current_receipt(tmp_path):
    task = job(tmp_path)
    runner = TaskRunner(None, tmp_path, None)
    source = {"a.py": "a" * 40, "b.py": "b" * 40}
    runner._record_step_evidence(task, task.steps[0], source, source, ["a.py", "b.py"])
    task.steps[0].scope_paths = ["a.py"]
    assert not receipt_matches(task.steps[0].evidence, task.steps[0], source, task_id=task.id)[0]


def test_cli_expansion_and_explanation_are_offline_and_never_approve(tmp_path, monkeypatch, capsys):
    from atomic_flow.cli import main

    task = job(tmp_path)
    proposal = tmp_path / "expansion.json"
    proposal.write_text(Plan(steps=[PlanStep(description="Inspect first detail", verify_with="none"),
                                    PlanStep(description="Inspect second detail", verify_with="none")]).model_dump_json())
    monkeypatch.setattr("atomic_flow.cli.make_backend", lambda *a, **kw: pytest.fail("offline operation constructed a provider"))
    assert main(["task", "--expand-step", "0", "--task-id", task.id, "--plan-file", str(proposal)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["approval_required"] and not result["execution_started"]
    assert not load(task.id).plan_approval
    assert main(["task", "--explain-next", task.id]) == 0
    view = json.loads(capsys.readouterr().out)
    assert view["ready"] == [2] and view["horizon_steps"][1]["projection_only"]
