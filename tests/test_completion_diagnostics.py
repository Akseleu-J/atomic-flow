from __future__ import annotations

from types import SimpleNamespace

import pytest

from atomic_flow.agents.changeset import ChangeSet
from atomic_flow.agents.completion import assess_completion
from atomic_flow.agents.context import ToolContext
from atomic_flow.agents.diagnostics import quality_diagnostics
from atomic_flow.agents.execution import CommandResult, ExecutionUnavailable, TaskCancelled
from atomic_flow.agents.planner import Plan, PlanStep
from atomic_flow.agents.source import digest
from atomic_flow.agents.task import Step, Task, load
from atomic_flow.agents.task_loop import TaskRunner
from atomic_flow.agents.tools.run_command import RunCommandArgs, RunCommandTool
from atomic_flow.agents.verify import verify_edit
from atomic_flow.config import AtomicConfig


def completed_task(root, *, verifier="none"):
    task = Task.new("Inspect source", root, [Step(0, "Inspect source", verify_with=verifier, status="done")])
    task.current_index = 1
    return task


def test_syntax_diagnostic_is_revision_bound_and_compile_does_not_execute(tmp_path):
    path = tmp_path / "a.py"
    data = b"def broken(:\n    pass\n"
    path.write_bytes(data)
    result = verify_edit(tmp_path, "a.py", run_tests=False)
    assert not result.ok and result.diagnostics[0]["line"] == 1
    assert result.diagnostics[0]["content_hash"] == digest(data)
    path.write_bytes(b"raise RuntimeError('must not execute')\n")
    assert verify_edit(tmp_path, "a.py", run_tests=False).ok
    assert not (tmp_path / "__pycache__").exists()


def test_quality_diagnostics_exclude_outside_private_and_stale_locations(tmp_path):
    path = tmp_path / "a.py"
    path.write_text("A = 1\n")
    hashes = {"a.py": digest(path.read_bytes())}
    output = "/workspace/a.py:1:2: F401 unused import\n../outside.py:1:1: E999 bad\n.env:1:1: F401 hidden\n"
    result = quality_diagnostics("ruff", output, tmp_path, hashes)
    assert len(result["diagnostics"]) == 1 and result["diagnostics"][0]["code"] == "F401"
    path.write_text("A = 2\n")
    result = quality_diagnostics("ruff", output, tmp_path, hashes)
    assert not result["diagnostics"] and result["stale_diagnostic_paths"] == ["a.py"]


def test_run_command_exposes_typed_diagnostics_even_on_failure(tmp_path, monkeypatch):
    (tmp_path / "a.py").write_text("A = 1\n")
    context = ToolContext(tmp_path, {}, file_hashes={"a.py": digest((tmp_path / "a.py").read_bytes())})
    monkeypatch.setattr("atomic_flow.agents.tools.run_command.run_python", lambda *a, **kw: CommandResult(1, "a.py:1:3: F401 unused\n", ""))
    result = RunCommandTool().run(RunCommandArgs(command="ruff"), context)
    assert result["exit_code"] == 1 and result["diagnostics"][0]["column"] == 3


def test_completion_rejects_empty_and_unfinished_task(tmp_path):
    changeset = ChangeSet.create(tmp_path)
    task = Task.new("empty", tmp_path, [])
    assert not assess_completion(task, changeset, AtomicConfig())["ready_to_publish"]
    task.steps = [Step(0, "not done", status="pending")]
    task.current_index = 1
    assert not assess_completion(task, changeset, AtomicConfig())["ready_to_publish"]


def test_completion_rechecks_approved_final_syntax_and_original_freshness(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    (root / "a.py").write_text("A = 1\n")
    change = ChangeSet.create(root)
    (change.workspace / "a.py").write_text("def broken(:\n")
    change.track("a.py", digest((change.workspace / "a.py").read_bytes()))
    report = assess_completion(completed_task(root), change, AtomicConfig())
    assert not report["ready_to_publish"] and report["diagnostics"][0]["path"] == "a.py"
    (change.workspace / "a.py").write_text("A = 2\n")
    change.track("a.py", digest((change.workspace / "a.py").read_bytes()))
    assert assess_completion(completed_task(root), change, AtomicConfig())["ready_to_publish"]
    (root / "a.py").write_text("A = 9\n")
    assert not assess_completion(completed_task(root), change, AtomicConfig())["ready_to_publish"]


def test_final_pytest_failure_is_not_published_and_report_survives_reload(tmp_path, monkeypatch):
    root = tmp_path / "project"
    root.mkdir()
    (root / "a.py").write_text("A = 1\n")

    class FakeOrch:
        def __init__(self, *a, **kw):
            pass

        def answer(self, *a, **kw):
            return SimpleNamespace(tool_calls=[{"tool": "run_command", "args": {"command": "pytest"},
                "result_summary": {"outcome": "success", "exit_code": 0}}], ledger_id="req_fake", verdict="ok")

    monkeypatch.setattr("atomic_flow.agents.task_loop.Orchestrator", FakeOrch)
    seen = []
    def run(args, project, **kwargs):
        seen.append(args)
        return CommandResult(1, "final state failed", "")
    monkeypatch.setattr("atomic_flow.agents.completion.run_python", run)
    task = TaskRunner(AtomicConfig(), root, None).run_plan("run checks", Plan(steps=[PlanStep(description="Run tests", verify_with="pytest")]))
    assert task.status == "paused" and seen
    assert load(task.id).completion_report["tests"] == "failed"
    assert (root / "a.py").read_text() == "A = 1\n"
    assert task.completion_report["model_calls"] == 0


def test_completion_detects_test_process_changes_after_its_success(tmp_path, monkeypatch):
    (tmp_path / "a.py").write_text("A = 1\n")
    change = ChangeSet.create(tmp_path)
    def modified(*a, **kw):
        (change.workspace / "a.py").write_text("A = 999\n")
        return CommandResult(0, "passed", "")
    monkeypatch.setattr("atomic_flow.agents.completion.run_python", modified)
    report = assess_completion(completed_task(tmp_path, verifier="pytest"), change, AtomicConfig())
    assert report["tests"] == "passed" and not report["ready_to_publish"]
    assert report["checks"][-1]["status"] == "failed"


def test_unavailable_final_executor_cannot_pass_and_cancellation_propagates(tmp_path, monkeypatch):
    change = ChangeSet.create(tmp_path)
    def unavailable(*a, **kw):
        raise ExecutionUnavailable("no Docker")
    monkeypatch.setattr("atomic_flow.agents.completion.run_python", unavailable)
    report = assess_completion(completed_task(tmp_path, verifier="pytest"), change, AtomicConfig())
    assert report["tests"] == "unavailable" and not report["ready_to_publish"]
    def cancelled():
        raise TaskCancelled("cancelled")
    with pytest.raises(TaskCancelled):
        assess_completion(completed_task(tmp_path), change, AtomicConfig(), cancelled)
