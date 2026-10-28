"""Host-only pytest-self wiring; synthetic receipts are not Docker evidence."""
import hashlib
import json
import time
from dataclasses import replace
from types import SimpleNamespace

import pytest

from atomic_flow import check_execution
from atomic_flow.agents.changeset import ChangeSet
from atomic_flow.agents.completion import assess_completion
from atomic_flow.agents.context import ToolContext
from atomic_flow.agents.execution import CommandResult, ExecutionUnavailable
from atomic_flow.agents.failure_policy import CONTINUE_REPAIR, classify
from atomic_flow.agents.result import ToolResult
from atomic_flow.agents.task import Step, Task
from atomic_flow.agents.task_loop import TaskRunner
from atomic_flow.agents.tools.run_command import RunCommandArgs, RunCommandTool
from atomic_flow.agents.verify import check_source_revision, check_source_snapshot, verify_edit
from atomic_flow.check_execution import CheckReceipt, _runner_receipt, completed_check_receipt
from atomic_flow.check_obligation import (
    CheckObligation,
    CheckObligationError,
    check_observation,
    failed_observation_matches,
    obligation_binding,
)
from atomic_flow.check_pytest_self import MARKER, PytestSelfIdentity
from atomic_flow.check_spec import CheckSpec
from atomic_flow.config import AtomicConfig, ExecutionConfig


@pytest.fixture
def workspace(tmp_path):
    sources = {"src/pytest.py": "__version__ = '5.4.0'\n", "src/_pytest/__init__.py": "# entrypoint\n",
               "src/_pytest/python.py": "VALUE = 1\n", "testing/test_value.py": "def test_value():\n    assert True\n"}
    for path, text in sources.items():
        file = tmp_path / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(text.encode())
    anchors = tuple((path, hashlib.sha256(sources[path].encode()).hexdigest())
                    for path in ("src/_pytest/__init__.py", "src/pytest.py"))
    identity = PytestSelfIdentity((3, 11, 9), "5.4.0", anchors)
    spec = CheckSpec("pytest-dev__pytest-7168", "a" * 40, "b" * 40, check_source_revision(tmp_path),
                     "example/checks@sha256:" + "c" * 64, "/usr/local/bin/python", "pytest-self",
                     ("testing/test_value.py",), pytest_self_identity=identity)
    bound = CheckObligation.seal(spec, tmp_path, environment_digest="d" * 64, deadline_at=time.time() + 600)
    return tmp_path, bound


def receipt(spec, *, passed=True, **changes):
    identity = spec.pytest_self_identity
    prefix = spec.target_root + ("/" + identity.source_root if identity.source_root else "")
    code = 0 if passed else 1
    data = {"nonce": "e" * 32, "root": spec.target_root, "runner": "pytest-self", "interpreter": spec.interpreter,
            "interpreter_resolved": "/usr/local/bin/python3.11", "python_version": list(identity.python_version),
            "pytest_version": identity.pytest_version, "pytest_module": spec.target_root + "/" + identity.pytest_entrypoint,
            "_pytest_module": prefix + "/_pytest/__init__.py", "pytest_self_identity": identity.as_dict(),
            "argv": list(spec.argv[3:]), "files": sorted(spec.test_paths), "finished": True,
            "collection_complete": True, "collected": 2, "completed": 2, "collection_errors": 0,
            "skipped": 0, "failed": int(not passed), "call_failed": int(not passed),
            "setup_failed": 0, "teardown_failed": 0, "exit_code": code}
    data.update(changes)
    result = CommandResult(data["exit_code"], MARKER + json.dumps(data), "")
    validated = _runner_receipt(result, spec, "e" * 32)
    return CheckReceipt(spec.digest, spec.source_revision, spec.source_revision,
                        passed and validated is not None, validated, result)


def config(bound):
    value = AtomicConfig()
    value.execution.mode = "docker"
    value.execution.profile = "checks"
    value.execution.image = bound.template.image
    return value


def mock_executor(monkeypatch, bound, *, passed=True, **changes):
    calls = []
    def execute(spec, root, *, cancel_check=None):
        assert spec.runner == "pytest-self"
        assert spec.pytest_self_identity == bound.template.pytest_self_identity
        assert spec.test_paths == bound.template.test_paths
        assert spec.source_revision == check_source_revision(root)
        calls.append(spec)
        return receipt(spec, passed=passed, **changes)
    monkeypatch.setattr(check_execution, "run_check_spec", execute)
    return calls


def test_default_serialization_remains_without_optional_identity(workspace):
    _, bound = workspace
    ordinary = replace(bound.template, task_id="ordinary", runner="pytest", pytest_self_identity=None)
    assert "pytest_self_identity" not in ordinary.as_dict()
    assert "sympy_identity" not in ordinary.as_dict()
    assert ordinary.argv[1:6] == ("-m", "pytest", "-q", "--no-header", "-x")


def test_argv_bootstrap_and_short_profile_are_fixed(workspace):
    _, bound = workspace
    spec = bound.template
    assert spec.argv[1:] == ("-m", "pytest", "-rA", *spec.test_paths)
    spec = replace(spec, pytest_self_identity=replace(spec.pytest_self_identity, traceback="short"))
    assert spec.argv[1:] == ("-m", "pytest", "-rA", "--tb=short", *spec.test_paths)
    assert check_execution.checker_args(spec, "e" * 32)[:2] == ["-I", "-c"]


def test_admission_refuses_installed_fallback_and_missing_identity(workspace):
    root, bound = workspace
    for spec in (replace(bound.template, pytest_self_identity=None),
                 replace(bound.template, runner="pytest", pytest_self_identity=None)):
        with pytest.raises(ExecutionUnavailable, match="self-checks"):
            check_execution.admit_check_spec(spec, root, config(bound).execution, 180, list(spec.argv[1:]))


def test_admission_protects_anchor_bytes_and_requires_docker(workspace):
    root, bound = workspace
    spec = bound.template
    assert check_execution.admit_check_spec(spec, root, config(bound).execution, 180, list(spec.argv[1:])) == spec.source_revision
    with pytest.raises(ExecutionUnavailable, match="Executor"):
        check_execution.admit_check_spec(spec, root, ExecutionConfig(mode="local"), 180, list(spec.argv[1:]))
    (root / "src/pytest.py").write_bytes(b"# replaced\n")
    spec = replace(spec, source_revision=check_source_revision(root))
    with pytest.raises(ExecutionUnavailable, match="runner source"):
        check_execution.admit_check_spec(spec, root, config(bound).execution, 180, list(spec.argv[1:]))


def test_seal_and_constructor_automatically_protect_anchors(workspace):
    root, bound = workspace
    assert set(bound.template.pytest_self_identity.source_sha256) <= set(bound.protected_files)
    with pytest.raises(CheckObligationError, match="entrypoint"):
        replace(bound, protected_files=tuple(pair for pair in bound.protected_files if pair[0] != "src/pytest.py"))
    (root / "src/pytest.py").write_bytes(b"# replaced\n")
    with pytest.raises(CheckObligationError, match="protected"):
        bound.derive(root)


def test_editable_implementation_rebinds_revision_without_weakening_obligation(workspace):
    root, bound = workspace
    (root / "src/_pytest/python.py").write_bytes(b"VALUE = 2\n")
    spec = bound.derive(root)
    assert spec.source_revision != bound.template.source_revision
    assert spec.pytest_self_identity == bound.template.pytest_self_identity
    assert spec.test_paths == bound.template.test_paths


def test_complete_negative_proof_survives_summary_and_failure_policy(workspace):
    root, bound = workspace
    spec = bound.template
    checked = receipt(spec, passed=False)
    assert completed_check_receipt(spec, checked, negative=True)
    observation = check_observation(bound, spec, checked)
    summary = ToolResult.from_payload({"command": "check", "exit_code": 1, **observation}).summary()
    assert summary["check_runner"] == "pytest-self"
    assert failed_observation_matches(bound, summary, spec.source_revision)
    host = SimpleNamespace(check_obligation=bound,
                           changeset=SimpleNamespace(validate_workspace=lambda: check_source_snapshot(root)))
    failure = TaskRunner._check_failure(host, SimpleNamespace(index=0, started_at=1.0),
                                      "failed", "check", 1, checked_revision=spec.source_revision,
                                      check_observation=summary)
    assert failure.evidence["runner"] == "pytest-self"
    decision = classify({"kind": failure.kind, "evidence": failure.evidence, "remaining_budget_usd": 1})
    assert decision["action"] == CONTINUE_REPAIR


@pytest.mark.parametrize("changes", [{"completed": 1}, {"collection_errors": 1}, {"skipped": 1},
                                    {"setup_failed": 1, "call_failed": 0}, {"finished": False}])
def test_incomplete_and_infrastructure_receipts_never_authorize_repair(workspace, changes):
    _, bound = workspace
    checked = receipt(bound.template, passed=False, **changes)
    assert not completed_check_receipt(bound.template, checked, negative=True)
    observation = check_observation(bound, bound.template, checked)
    assert not failed_observation_matches(bound, observation, bound.template.source_revision)


@pytest.mark.parametrize("command", ["check", "pytest"])
def test_run_command_uses_actual_self_obligation(workspace, monkeypatch, command):
    root, bound = workspace
    calls = mock_executor(monkeypatch, bound)
    payload = RunCommandTool().run(RunCommandArgs(command=command),
                                  ToolContext(root, {}, private_workspace=True, check_obligation=bound))
    if command == "check":
        assert payload["check_passed"] is True and payload["check_runner"] == "pytest-self"
        assert len(calls) == 1
    else:
        assert "requires command check" in payload["error"] and calls == []


def test_post_edit_and_step_verification_keep_self_identity(workspace, monkeypatch):
    root, bound = workspace
    calls = mock_executor(monkeypatch, bound)
    (root / "src/_pytest/python.py").write_bytes(b"VALUE = 2\n")
    verdict = verify_edit(root, "src/_pytest/python.py", private_workspace=True, check_obligation=bound)
    assert verdict.ok and verdict.check_observation["check_runner"] == "pytest-self"
    runner = TaskRunner(config(bound), root, object(), check_obligation=bound)
    step = Step(0, "Check source", verify_with="pytest")
    runner._verify_step(step, SimpleNamespace(tool_calls=[], verdict="unverified"))
    assert len(calls) == 2
    assert all(spec.source_revision != bound.template.source_revision for spec in calls)


@pytest.mark.parametrize("passed", [True, False])
def test_final_gate_uses_same_full_self_obligation(workspace, monkeypatch, passed):
    root, bound = workspace
    calls = mock_executor(monkeypatch, bound, passed=passed)
    changeset = ChangeSet.create(root)
    task = Task.new("Check", root, [Step(0, "Check", verify_with="none", status="done")])
    task.current_index = 1
    task.plan_approval["check_obligation"] = obligation_binding(bound)
    report = assess_completion(task, changeset, config(bound), check_obligation=bound)
    assert report["ready_to_publish"] is passed
    assert report["tests"] == ("passed" if passed else "failed")
    assert len(calls) == 1
