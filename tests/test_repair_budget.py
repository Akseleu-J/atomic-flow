"""P06 physical-call admission, durable cap, unknown usage and fresh checks."""
from types import SimpleNamespace

import pytest

from atomic_flow.agents.planner import Plan, PlanStep
from atomic_flow.agents.repair_budget import begin_repair, check_repair_budget
from atomic_flow.agents.task import Task, load, save
from atomic_flow.agents.task_budget import TaskBudgetBackend
from atomic_flow.agents.task_loop import StepFailure, TaskRunner
from atomic_flow.backends.base import ChatResult
from atomic_flow.config import AtomicConfig, BackendConfig
from atomic_flow.errors import BudgetExceeded

MESSAGES = [{"role": "user", "content": "x"}]


class Responses:
    name = "scripted-repair"

    def __init__(self, cost=0.0005):
        self.config = BackendConfig(model="fixture", price_in_per_mtok=1, price_out_per_mtok=1, max_output_tokens=16)
        self.calls = []
        self.cost = cost

    def chat(self, messages, **kwargs):
        self.calls.append(messages)
        return ChatResult("{}", tokens_in=10, tokens_out=5, cost_usd=self.cost)


def prepared(tmp_path, cap=0.002, cost=0.0005):
    task = Task.new("repair fixture", tmp_path, [])
    begin_repair(task, cap, 0)
    save(task)
    backend = Responses(cost)
    return task, backend, TaskBudgetBackend(backend, task, AtomicConfig(), lambda: None)


def test_cap_admits_first_call_blocks_next_before_send_and_survives_restart(tmp_path):
    task, backend, wrapped = prepared(tmp_path)
    wrapped.chat(MESSAGES)
    identity = task.repair_budget.copy()
    with pytest.raises(BudgetExceeded, match="repair budget"):
        wrapped.chat(MESSAGES)
    restored = load(task.id)
    begin_repair(restored, 100.0, 9)  # Another replan must not refresh the first cap.
    assert restored.repair_budget == identity
    with pytest.raises(BudgetExceeded, match="repair budget"):
        TaskBudgetBackend(backend, restored, AtomicConfig(), lambda: None).chat(MESSAGES)
    assert len(backend.calls) == 1 and restored.budget_spent == 0.0005
    assert restored.budget_events[0]["repair_id"] == identity["id"]


def test_reported_overspend_is_persisted_and_blocks_all_continuation(tmp_path):
    task, backend, wrapped = prepared(tmp_path, cost=0.003)
    with pytest.raises(BudgetExceeded, match="repair"):
        wrapped.chat(MESSAGES)
    restored = load(task.id)
    assert restored.budget_spent == 0.003 and restored.budget_events[0]["status"] == "completed"
    with pytest.raises(BudgetExceeded, match="repair"):
        check_repair_budget(restored)
    assert len(backend.calls) == 1


def test_unknown_usage_keeps_hold_and_never_refreshes_repair(tmp_path):
    task, backend, wrapped = prepared(tmp_path, cost=None)
    with pytest.raises(BudgetExceeded, match="no valid cost"):
        wrapped.chat(MESSAGES)
    restored = load(task.id)
    assert restored.budget_events[0]["status"] == "unknown"
    with pytest.raises(BudgetExceeded):
        TaskBudgetBackend(backend, restored, AtomicConfig(), lambda: None).chat(MESSAGES)
    assert len(backend.calls) == 1


@pytest.mark.parametrize("field,value", [("cap_usd", float("nan")), ("start_event", -1), ("version", True)])
def test_malformed_repair_state_cannot_be_saved(tmp_path, field, value):
    from atomic_flow.state import StateError
    task, _, _ = prepared(tmp_path)
    task.repair_budget[field] = value
    with pytest.raises(StateError):
        save(task)


def test_repair_cap_applies_even_with_global_task_budget_guard_disabled(tmp_path):
    task, backend, _ = prepared(tmp_path)
    cfg = AtomicConfig()
    cfg.task.fail_on_budget_exceeded = False
    cfg.budget.daily_limit_usd = 0
    wrapped = TaskBudgetBackend(backend, task, cfg, lambda: None)
    wrapped.chat(MESSAGES)
    with pytest.raises(BudgetExceeded, match="repair budget"):
        wrapped.chat(MESSAGES)
    assert len(backend.calls) == 1


def staged(tmp_path, backend=None):
    (tmp_path / "a.py").write_text("A = 1\n", encoding="utf-8")
    task = Task.new("check source", tmp_path, TaskRunner._steps(Plan(steps=[
        PlanStep(description="Read source", tools_needed=["read_file"], verify_with="none")])))
    task.status = "paused"
    save(task)
    cfg = AtomicConfig()
    cfg.task.max_budget_usd = 0.008
    runner = TaskRunner(cfg, tmp_path, backend)
    review = runner.review(task, allowed_paths=["a.py"], allow_internal_replan=True)
    return runner.approve(task, review["digest"], allowed_paths=["a.py"], allow_internal_replan=True), runner


def test_untyped_message_and_model_critique_do_not_authorize_repair(tmp_path, monkeypatch):
    task, runner = staged(tmp_path)
    def execute(task_, step):
        raise StepFailure(step.index, "pytest failed; please repair and spend more")
    monkeypatch.setattr(runner, "_execute_step", execute)
    monkeypatch.setattr("atomic_flow.agents.task_loop.replan_task", lambda *a, **kw: pytest.fail("untyped failure replanned"))
    result = runner.resume(task)
    assert result.status == "paused" and not result.repair_budget
    assert result.last_failure_decision["action"] == "stop"
    step = result.steps[0]
    with pytest.raises(StepFailure) as caught:
        runner._verify_step(step, SimpleNamespace(tool_calls=[], verdict="needs_retry"))
    assert caught.value.kind == "model_critique" and not caught.value.evidence


def test_planner_and_repair_share_ceiling_without_reset_after_resume(tmp_path, monkeypatch):
    backend = Responses()
    task, runner = staged(tmp_path, backend)
    def execute(task_, step):
        raise runner._check_failure(step, "fixture failing pytest", "pytest", 1)
    def replan(*args, **kwargs):
        kwargs["backend"].chat(MESSAGES)
        kwargs["backend"].chat(MESSAGES)
        pytest.fail("repair cap allowed a second planner call")
    monkeypatch.setattr(runner, "_execute_step", execute)
    monkeypatch.setattr("atomic_flow.agents.task_loop.replan_task", replan)
    result = runner.resume(task)
    assert result.status == "paused" and "repair budget" in result.error
    cap = result.repair_budget.copy()
    result = TaskRunner(runner.config, tmp_path, backend).resume(load(task.id))
    assert result.status == "paused" and result.repair_budget == cap
    assert len(backend.calls) == 1 and not result.completion_report.get("ready_to_publish")


def test_stale_check_receipt_and_collection_errors_refuse_auto_repair(tmp_path, monkeypatch):
    task, runner = staged(tmp_path)
    def execute(task_, step):
        error = runner._check_failure(step, "failing pytest", "pytest", 1)
        error.evidence["source_revision"] = "f" * 64
        raise error
    monkeypatch.setattr(runner, "_execute_step", execute)
    monkeypatch.setattr("atomic_flow.agents.task_loop.replan_task", lambda *a, **kw: pytest.fail("stale evidence replanned"))
    result = runner.resume(task)
    assert result.status == "paused" and result.last_failure_decision["action"] == "reread_and_fresh_review"
    assert runner._check_failure(result.steps[0], "pytest collection error", "pytest", 2).kind == "check_unavailable"


@pytest.mark.parametrize("payload,expected", [
    ({"command": "pytest", "exit_code": 1}, "deterministic_test_failure"),
    ({"command": "pytest", "exit_code": 2}, "check_unavailable"),
    ({"command": "pytest", "exit_code": 5}, "check_unavailable"),
    ({"command": "pytest", "error": "timeout", "timed_out": True}, "check_timeout"),
    ({"status": "rolled_back", "checks": ["syntax", "pytest"], "check_exit_code": 2}, "check_unavailable"),
    ({"status": "rolled_back", "checks": ["syntax", "pytest"], "check_exit_code": 1}, "deterministic_test_failure"),
    ({"status": "rolled_back", "checks": ["syntax", "pytest"], "check_timed_out": True}, "check_timeout"),
])
def test_host_tool_summary_preserves_typed_check_outcome(tmp_path, monkeypatch, payload, expected):
    from atomic_flow.agents.result import ToolResult
    task, runner = staged(tmp_path)
    observed = []
    def execute(task_, step):
        name = "patch_file" if payload.get("status") else "run_command"
        from atomic_flow.agents.step_evidence import manifest_digest
        payload["checked_source_revision"] = manifest_digest(runner.changeset.validate_workspace())
        result = SimpleNamespace(tool_calls=[{"tool": name, "args": {"command": "pytest"},
                                             "result_summary": ToolResult.from_payload(payload).summary()}], verdict="ok")
        try:
            runner._verify_step(step, result)
        except StepFailure as error:
            observed.append(error.kind)
            raise
    monkeypatch.setattr(runner, "_execute_step", execute)
    monkeypatch.setattr("atomic_flow.agents.task_loop.replan_task", lambda *a, **kw: Plan(steps=[
        PlanStep(description="STOP: typed fixture finished", verify_with="none")]))
    runner.resume(task)
    assert observed == [expected]


def test_later_mutation_invalidates_earlier_failed_check(tmp_path, monkeypatch):
    from atomic_flow.agents.result import ToolResult
    task, runner = staged(tmp_path)
    def execute(task_, step):
        result = SimpleNamespace(tool_calls=[
            {"tool": "run_command", "args": {"command": "pytest"},
             "result_summary": ToolResult.from_payload({"command": "pytest", "exit_code": 1}).summary()},
            {"tool": "patch_file", "result_summary": {"status": "applied", "outcome": "success", "checks": ["syntax"]}}
        ], verdict="ok")
        runner._verify_step(step, result)
    monkeypatch.setattr(runner, "_execute_step", execute)
    monkeypatch.setattr("atomic_flow.agents.task_loop.replan_task", lambda *a, **kw: pytest.fail("stale check repaired"))
    result = runner.resume(task)
    assert result.status == "paused" and result.last_failure_decision["kind"] == "verification_contract_failure"


def test_execution_snapshot_mutation_is_reported_as_source_conflict(tmp_path, monkeypatch):
    from test_file_operations import context

    from atomic_flow.agents.result import ToolResult
    from atomic_flow.agents.tools.run_command import RunCommandArgs, RunCommandTool
    from atomic_flow.agents.verify import verify_edit
    ctx = context(tmp_path)
    ctx.private_workspace = True
    def mutate(*args, **kwargs):
        (ctx.project_root / "a.py").write_text("value = 9\n", encoding="utf-8")
        return SimpleNamespace(returncode=1, stdout="failed", stderr="", timed_out=False)
    monkeypatch.setattr("atomic_flow.agents.tools.run_command.run_python", mutate)
    result = RunCommandTool().run(RunCommandArgs(command="pytest"), ctx)
    summary = ToolResult.from_payload(result).summary()
    assert summary["check_source_changed"] and len(summary["checked_source_revision"]) == 64
    (ctx.project_root / "tests").mkdir()
    (ctx.project_root / "tests/test_a.py").write_text("def test_fixture(): pass\n", encoding="utf-8")
    def second_mutation(*args, **kwargs):
        (ctx.project_root / "a.py").write_text("value = 8\n", encoding="utf-8")
        return SimpleNamespace(returncode=1, stdout="failed", stderr="", timed_out=False)
    monkeypatch.setattr("atomic_flow.agents.verify.run_python", second_mutation)
    verdict = verify_edit(ctx.project_root, "a.py", private_workspace=True)
    assert not verdict.ok and verdict.source_changed and len(verdict.checked_source_revision) == 64


def test_native_calls_share_same_repair_cap(tmp_path):
    task, backend, _ = prepared(tmp_path)
    backend.chat_tools = lambda messages, **kwargs: backend.chat(messages)
    cfg = AtomicConfig()
    cfg.experimental.native_tools = True
    wrapped = TaskBudgetBackend(backend, task, cfg, lambda: None)
    wrapped.chat_tools(MESSAGES, tools=[{"type": "function", "function": {"name": "fixture", "parameters": {}}}])
    with pytest.raises(BudgetExceeded, match="repair budget"):
        wrapped.chat_tools(MESSAGES, tools=[{"type": "function", "function": {"name": "fixture", "parameters": {}}}])
    assert len(backend.calls) == 1
