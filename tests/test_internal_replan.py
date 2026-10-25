"""Opt-in internal replanning: host authority survives adaptation, never widens."""
import json
from types import SimpleNamespace

import pytest
from conftest import ScriptedBackend

from atomic_flow.agents.changeset import ChangeSetConflict
from atomic_flow.agents.planner import Plan, PlanStep
from atomic_flow.agents.task import Task, load, save
from atomic_flow.agents.task_loop import StepFailure, TaskRunner
from atomic_flow.config import AtomicConfig, BackendConfig, user_dir


def staged(root, plan=None):
    (root / "a.py").write_text("A = 1\n", encoding="utf-8")
    plan = plan or Plan(steps=[PlanStep(description="Inspect current source", tools_needed=["read_file"],
                                       verify_with="none")])
    task = Task.new("Inspect source without changing its requirement", root, TaskRunner._steps(plan))
    task.status = "paused"
    save(task)
    return task, TaskRunner(AtomicConfig(), root, None)


def authorize(runner, task, *, internal=True):
    review = runner.review(task, allowed_paths=["a.py"], allow_internal_replan=internal)
    return runner.approve(task, review["digest"], allowed_paths=["a.py"], allow_internal_replan=internal)


def repaired_plan(*args, **kwargs):
    return Plan(steps=[PlanStep(description="Inspect source by an alternative read", tools_needed=["read_file"],
                               verify_with="none")])


def test_internal_replan_is_explicit_and_bound_to_review_digest(tmp_path):
    task, runner = staged(tmp_path)
    legacy = runner.review(task, allowed_paths=["a.py"])
    opted_in = runner.review(task, allowed_paths=["a.py"], allow_internal_replan=True)
    assert legacy["schema_version"] == 1 and "internal_replan" not in legacy
    assert opted_in["schema_version"] == 2 and "internal_replan" in opted_in
    assert opted_in["digest"] != legacy["digest"]
    with pytest.raises(ChangeSetConflict, match="digest"):
        runner.approve(task, opted_in["digest"], allowed_paths=["a.py"])
    approved = authorize(runner, task)
    assert load(approved.id).plan_approval == approved.plan_approval


def test_authorized_repair_continues_in_same_resume_and_preserves_scope(tmp_path, monkeypatch):
    task, runner = staged(tmp_path)
    seen = []

    def execute(task, step):
        seen.append(step.index)
        if len(seen) == 1:
            raise StepFailure(step.index, "first approach failed")
        step.result_summary = "independent read completed"

    monkeypatch.setattr(runner, "_execute_step", execute)
    monkeypatch.setattr("atomic_flow.agents.task_loop.replan_task", repaired_plan)
    task = authorize(runner, task)
    before_source = (tmp_path / "a.py").read_bytes()
    result = runner.resume(task)
    assert result.status == "done", result.error
    assert len(seen) == 2 and result.replan_count == 1
    persisted = load(result.id)
    assert [step.status for step in persisted.steps] == ["superseded", "done"]
    assert persisted.plan_approval["allowed_paths"] == ["a.py"]
    assert len(persisted.plan_approval["internal_replan"]["replan_failures"]) == 1
    assert persisted.completion_report["ready_to_publish"]
    assert (tmp_path / "a.py").read_bytes() == before_source


def test_legacy_approval_still_pauses_before_replanned_execution(tmp_path, monkeypatch):
    task, runner = staged(tmp_path)
    seen = []

    def execute(task, step):
        seen.append(step.index)
        raise StepFailure(step.index, "first approach failed")

    monkeypatch.setattr(runner, "_execute_step", execute)
    monkeypatch.setattr("atomic_flow.agents.task_loop.replan_task", repaired_plan)
    result = runner.resume(authorize(runner, task, internal=False))
    assert result.status == "paused" and seen == [0]
    assert not result.plan_approval
    assert runner.resume(result).status == "paused" and seen == [0]


def test_replan_cannot_drop_pytest_obligation_from_mixed_plan(tmp_path, monkeypatch):
    plan = Plan(steps=[
        PlanStep(description="Verify behavior with tests", tools_needed=["run_command"], verify_with="pytest", depends_on=[]),
        PlanStep(description="Read the source independently", tools_needed=["read_file"], verify_with="none", depends_on=[]),
    ])
    task, runner = staged(tmp_path, plan)
    seen = []

    def execute(task, step):
        seen.append(step.index)
        if step.index == 0:
            raise StepFailure(step.index, "pytest requirement failed")

    monkeypatch.setattr(runner, "_execute_step", execute)
    monkeypatch.setattr("atomic_flow.agents.task_loop.replan_task", repaired_plan)
    result = runner.resume(authorize(runner, task))
    assert result.status == "paused", result.error
    assert seen == [0, 1]  # Independent work drains, but the downgraded replacement never runs.
    assert "verif" in result.error.lower() or "pytest" in result.error.lower()
    assert not result.completion_report.get("ready_to_publish")


def test_replan_cannot_introduce_new_execution_tool(tmp_path, monkeypatch):
    task, runner = staged(tmp_path)
    seen = []

    def execute(task, step):
        seen.append(step.index)
        raise StepFailure(step.index, "read failed")

    monkeypatch.setattr(runner, "_execute_step", execute)
    monkeypatch.setattr("atomic_flow.agents.task_loop.replan_task", lambda *a, **kw: Plan(steps=[
        PlanStep(description="Execute a different approach", tools_needed=["run_command"], verify_with="none")]))
    result = runner.resume(authorize(runner, task))
    assert result.status == "paused" and seen == [0]
    assert "run_command" in result.error


def test_runtime_change_after_opt_in_pauses_before_any_step(tmp_path, monkeypatch):
    task, runner = staged(tmp_path)
    task = authorize(runner, task)
    runner.config.execution.mode = "docker"
    monkeypatch.setattr(runner, "_execute_step", lambda *a: pytest.fail("runtime drift executed a step"))
    monkeypatch.setattr("atomic_flow.agents.task_loop.replan_task", lambda *a, **kw: pytest.fail("runtime drift called planner"))
    result = runner.resume(task)
    assert result.status == "paused", result.error
    assert any(word in result.error.lower() for word in ("runtime", "configuration", "execution", "context"))
    assert not result.completion_report.get("ready_to_publish")


@pytest.mark.parametrize("field,new_value", [("model", "other-model"), ("base_url", "https://other.invalid/v1")])
def test_actual_backend_change_with_same_runner_config_pauses_before_inference(tmp_path, monkeypatch, field, new_value):
    task, runner = staged(tmp_path)
    backend = SimpleNamespace(name="reviewed", config=BackendConfig(model="reviewed-model", base_url="https://reviewed.invalid/v1"))
    runner.backend = backend
    task = authorize(runner, task)
    config_before = runner.config.model_dump()
    setattr(backend.config, field, new_value)
    monkeypatch.setattr(runner, "_execute_step", lambda *a: pytest.fail("changed provider executed a step"))
    monkeypatch.setattr("atomic_flow.agents.task_loop.replan_task", lambda *a, **kw: pytest.fail("changed provider invoked planner"))
    result = runner.resume(task)
    assert runner.config.model_dump() == config_before
    assert result.status == "paused", result.error
    assert "runtime" in result.error.lower()


def test_backend_added_after_approval_requires_fresh_review(tmp_path, monkeypatch):
    task, runner = staged(tmp_path)
    task = authorize(runner, task)
    runner.backend = SimpleNamespace(name="late-provider", config=BackendConfig(model="late-model"))
    monkeypatch.setattr(runner, "_execute_step", lambda *a: pytest.fail("unreviewed provider executed a step"))
    result = runner.resume(task)
    assert result.status == "paused", result.error
    assert "runtime" in result.error.lower()


def test_repeated_failure_pauses_before_global_replan_cap(tmp_path, monkeypatch):
    task, runner = staged(tmp_path)
    runner.config.task.max_replans = 5
    seen = []

    def execute(task, step):
        seen.append(step.index)
        raise StepFailure(step.index, "the same failing precondition")

    monkeypatch.setattr(runner, "_execute_step", execute)
    monkeypatch.setattr("atomic_flow.agents.task_loop.replan_task", repaired_plan)
    result = runner.resume(authorize(runner, task))
    assert result.status == "paused", result.error
    assert "no-progress" in result.error.lower()
    assert len(seen) <= 3 and result.replan_count < 5
    assert not result.completion_report.get("ready_to_publish")


def test_opt_in_enforces_effective_tool_allowlist_in_orchestrator(tmp_path, monkeypatch):
    task, runner = staged(tmp_path)
    captured = []

    class CapturingOrchestrator:
        def __init__(self, *args, **kwargs):
            captured.append(kwargs["role_tools"])

        def answer(self, *args, **kwargs):
            return SimpleNamespace(tool_calls=[], ledger_id="req_allowlist", verdict="unverified")

    monkeypatch.setattr("atomic_flow.agents.task_loop.Orchestrator", CapturingOrchestrator)
    result = runner.resume(authorize(runner, task))
    assert result.status == "done", result.error
    assert captured and captured[0] is not None
    allowed = {tool for tools in captured[0].values() for tool in tools}
    assert "read_file" in allowed
    assert not {"run_command", "edit_node", "patch_file", "create_file", "docs_fetch"} & allowed


def test_real_dispatcher_denies_undeclared_execution_before_tool_implementation(tmp_path, monkeypatch):
    task, runner = staged(tmp_path)
    runner.backend = ScriptedBackend({
        "parser": {"task_type": "explain", "focus_terms": ["A"]},
        "answerer": [{"tool": "run_command", "args": {"command": "pytest"}},
                     {"text": "Source inspected.", "confidence": "low"}],
        "critic": {"verdict": "ok", "citation_coverage": 1},
    })
    monkeypatch.setattr("atomic_flow.agents.tools.run_command.RunCommandTool.run",
                        lambda *a, **kw: pytest.fail("undeclared execution tool ran"))
    monkeypatch.setattr("atomic_flow.agents.task_loop.replan_task", lambda *a, **kw: Plan(steps=[
        PlanStep(description="STOP: tool request was unauthorized", verify_with="none")]))
    source_before = (tmp_path / "a.py").read_bytes()
    result = runner.resume(authorize(runner, task))
    assert result.status == "failed", result.error
    assert (tmp_path / "a.py").read_bytes() == source_before
    request = result.steps[0].request_id
    assert request
    meta = json.loads((user_dir() / "runs" / request / "meta.json").read_text(encoding="utf-8"))
    denied = next(call for call in meta["tool_calls"] if call["tool"] == "run_command")
    assert denied["result_summary"]["outcome"] == "denied"
    assert "not allowed" in denied["result_summary"]["error"]


@pytest.mark.parametrize("change", ["unlimited_budget", "disabled_budget_guard", "no_replans"])
def test_opt_in_requires_enforceable_finite_task_limits(tmp_path, change):
    task, runner = staged(tmp_path)
    if change == "unlimited_budget":
        runner.config.task.max_budget_usd = 0
    elif change == "disabled_budget_guard":
        runner.config.task.fail_on_budget_exceeded = False
    else:
        runner.config.task.max_replans = 0
    with pytest.raises((ValueError, ChangeSetConflict)):
        runner.review(task, allowed_paths=["a.py"], allow_internal_replan=True)
