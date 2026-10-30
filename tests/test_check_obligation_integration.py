"""Four ordinary-test consumers retain one host obligation and fresh source identity."""
import time
from types import SimpleNamespace

import pytest

from atomic_flow import check_execution
from atomic_flow.agents.changeset import ChangeSet
from atomic_flow.agents.completion import assess_completion
from atomic_flow.agents.context import ToolContext
from atomic_flow.agents.task import Step, Task
from atomic_flow.agents.task_loop import TaskRunner
from atomic_flow.agents.tools.run_command import RunCommandArgs, RunCommandTool
from atomic_flow.agents.verify import check_source_revision, verify_edit
from atomic_flow.check_obligation import CheckObligation, CheckObligationError, obligation_binding, require_obligation_binding
from atomic_flow.check_spec import CheckSpec
from atomic_flow.config import AtomicConfig


@pytest.fixture
def workspace(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests/test_value.py").write_text("def test_value():\n    assert True\n")
    (tmp_path / "value.py").write_text("VALUE = 1\n")
    spec = CheckSpec(task_id="host-example", task_commit="a" * 40, base_commit="b" * 40,
                     source_revision=check_source_revision(tmp_path), image="example/checks@sha256:" + "c" * 64,
                     interpreter="/usr/local/bin/python", runner="pytest", test_paths=("tests/test_value.py",))
    bound = CheckObligation.seal(spec, tmp_path, environment_digest="d" * 64, deadline_at=time.time() + 600)
    return tmp_path, bound


def fake_executor(monkeypatch, *, passed=True):
    calls = []
    def execute(spec, root, *, cancel_check=None):
        if cancel_check:
            cancel_check()
        assert spec.source_revision == check_source_revision(root)
        assert spec.test_paths == ("tests/test_value.py",)
        calls.append((spec, root))
        result = SimpleNamespace(returncode=0 if passed else 1, timed_out=False, stdout="host checker", stderr="")
        return SimpleNamespace(check_passed=passed, result=result, source_before=spec.source_revision, source_after=spec.source_revision)
    monkeypatch.setattr(check_execution, "run_check_spec", execute)
    return calls


def config(bound):
    value = AtomicConfig()
    value.execution.mode = "docker"
    value.execution.profile = "checks"
    value.execution.image = bound.template.image
    return value


def test_run_command_and_post_edit_use_fresh_complete_same_obligation(workspace, monkeypatch):
    root, bound = workspace
    calls = fake_executor(monkeypatch)
    (root / "value.py").write_text("VALUE = 2\n")
    ctx = ToolContext(root, {}, private_workspace=True, check_obligation=bound)
    result = RunCommandTool().run(RunCommandArgs(command="pytest"), ctx)
    assert result["check_passed"] and result["check_obligation_digest"] == bound.digest
    verdict = verify_edit(root, "value.py", private_workspace=True, check_obligation=bound)
    assert verdict.ok and verdict.check_observation["check_obligation_digest"] == bound.digest
    assert len(calls) == 2 and all(spec.source_revision != bound.template.source_revision for spec, _ in calls)


def test_model_cannot_narrow_the_host_inventory(workspace, monkeypatch):
    root, bound = workspace
    calls = fake_executor(monkeypatch)
    result = RunCommandTool().run(RunCommandArgs(command="pytest", target="tests/test_value.py"),
                                  ToolContext(root, {}, private_workspace=True, check_obligation=bound))
    assert "cannot be narrowed" in result["error"] and calls == []


def test_protected_test_edit_is_refused_before_execution(workspace, monkeypatch):
    root, bound = workspace
    calls = fake_executor(monkeypatch)
    (root / "tests/test_value.py").write_text("def test_value():\n    pass\n")
    with pytest.raises(CheckObligationError):
        verify_edit(root, "value.py", private_workspace=True, check_obligation=bound)
    assert calls == []


def test_step_verification_uses_host_obligation(workspace, monkeypatch):
    root, bound = workspace
    calls = fake_executor(monkeypatch)
    runner = TaskRunner(config(bound), root, object(), check_obligation=bound)
    step = Step(0, "Inspect and check", verify_with="pytest")
    runner._verify_step(step, SimpleNamespace(tool_calls=[], verdict="unverified"))
    assert len(calls) == 1 and "source-bound" in step.result_summary


@pytest.mark.parametrize("passed", [True, False])
def test_final_gate_always_checks_full_bound_obligation(workspace, monkeypatch, passed):
    root, bound = workspace
    calls = fake_executor(monkeypatch, passed=passed)
    changeset = ChangeSet.create(root)
    task = Task.new("Inspect", root, [Step(0, "Inspect", verify_with="none", status="done")])
    task.current_index = 1
    task.plan_approval["check_obligation"] = obligation_binding(bound)
    report = assess_completion(task, changeset, config(bound), check_obligation=bound)
    assert len(calls) == 1 and report["ready_to_publish"] is passed
    assert report["check_obligation_digest"] == bound.digest
    assert report["tests"] == ("passed" if passed else "failed")


@pytest.mark.parametrize("passed", [True, False])
def test_oversized_graph_omission_preserves_complete_host_obligation(workspace, monkeypatch, passed):
    root, bound = workspace
    (root / "large.py").write_bytes(b"#" + b"x" * 1_000_000 + b"\n")
    calls = fake_executor(monkeypatch, passed=passed)
    changeset = ChangeSet.create(root)
    edited = changeset.workspace / "value.py"
    edited.write_bytes(b"VALUE = 2\n")
    from atomic_flow.agents.source import digest
    changeset.track("value.py", digest(edited.read_bytes()))
    task = Task.new("Inspect", root, [Step(0, "Inspect", verify_with="none", status="done")])
    task.current_index = 1
    task.plan_approval["check_obligation"] = obligation_binding(bound)
    report = assess_completion(task, changeset, config(bound), check_obligation=bound)
    assert len(calls) == 1 and report["ready_to_publish"] is passed
    assert calls[0][0].test_paths == bound.template.test_paths
    assert report["impact_check_plan"]["omitted_oversized"] == ["large.py"]
    assert report["impact_check_plan"]["impact_status"] == "unknown"
    assert report["check_obligation_digest"] == bound.digest


def test_resume_cannot_drop_or_replace_bound_obligation(workspace):
    _, bound = workspace
    approval = {"check_obligation": obligation_binding(bound)}
    require_obligation_binding(approval, bound)
    with pytest.raises(CheckObligationError):
        require_obligation_binding(approval, None)
    with pytest.raises(CheckObligationError):
        require_obligation_binding({}, bound)
