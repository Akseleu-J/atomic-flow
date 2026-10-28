"""Repair authority requires a completed assertion failure at current source."""
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from atomic_flow.agents.execution import CommandResult
from atomic_flow.agents.result import ToolResult
from atomic_flow.agents.step_evidence import manifest_digest
from atomic_flow.agents.task_loop import TaskRunner
from atomic_flow.check_execution import _MARKER, CheckReceipt, _runner_receipt, completed_check_receipt
from atomic_flow.check_obligation import CheckObligation, check_observation, failed_observation_matches
from atomic_flow.check_spec import CheckSpec


@pytest.fixture
def bound(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests/test_value.py").write_text("def test_value():\n    assert False\n")
    from atomic_flow.agents.verify import check_source_revision
    spec = CheckSpec("example", "a" * 40, "b" * 40, check_source_revision(tmp_path),
                     "example/checks@sha256:" + "c" * 64, "/usr/local/bin/python", "pytest",
                     ("tests/test_value.py",))
    obligation = CheckObligation.seal(spec, tmp_path, environment_digest="d" * 64,
                                      deadline_at=400, now=lambda: 100)
    return obligation, spec


def failed_receipt(spec, **changes):
    data = {"nonce": "e" * 32, "root": spec.target_root, "interpreter": spec.interpreter,
            "pytest_module": "/usr/local/lib/python3.11/site-packages/pytest/__init__.py",
            "pytest_version": "8.0", "collected": 2, "files": sorted(spec.test_paths),
            "finished": True, "skipped": 0, "failed": 1, "exit_code": 1,
            "call_failed": 1, "setup_failed": 0, "teardown_failed": 0}
    data.update(changes)
    result = CommandResult(data["exit_code"], _MARKER + json.dumps(data), "")
    validated = _runner_receipt(result, spec, "e" * 32)
    return CheckReceipt(spec.digest, spec.source_revision, spec.source_revision, False, validated, result)


def test_completed_assertion_failure_survives_bounded_summary(bound):
    obligation, spec = bound
    receipt = failed_receipt(spec)
    assert completed_check_receipt(spec, receipt, negative=True)
    observation = check_observation(obligation, spec, receipt)
    summary = ToolResult.from_payload({"command": "pytest", "exit_code": 1, **observation}).summary()
    assert "check_spec" not in summary and "stdout" not in summary
    assert failed_observation_matches(obligation, summary, spec.source_revision)


@pytest.mark.parametrize("changes", [
    {"setup_failed": 1, "call_failed": 0}, {"teardown_failed": 1, "call_failed": 0},
    {"skipped": 1}, {"collected": 0}, {"finished": False}, {"exit_code": 2},
    {"exit_code": 125}, {"call_failed": True}, {"failed": 0}, {"files": []},
    {"setup_failed": 1, "failed": 2},
])
def test_infrastructure_collection_fixture_and_partial_checks_cannot_authorize_repair(bound, changes):
    obligation, spec = bound
    receipt = failed_receipt(spec, **changes)
    assert receipt.runner_receipt is None
    observation = check_observation(obligation, spec, receipt)
    assert observation["check_completed"] is False
    assert not failed_observation_matches(obligation, observation, spec.source_revision)


@pytest.mark.parametrize("changes", [
    {"spec_digest": "f" * 64}, {"source_before": "f" * 64}, {"source_after": "f" * 64},
    {"runner_receipt": None}, {"check_passed": True},
])
def test_receipt_binding_cannot_be_replaced_by_flags(bound, changes):
    _, spec = bound
    assert not completed_check_receipt(spec, replace(failed_receipt(spec), **changes), negative=True)


@pytest.mark.parametrize("field,value", [
    ("check_spec_digest", "f" * 64), ("check_obligation_digest", "f" * 64),
    ("check_runner", "sympy-native"), ("check_completed", False),
    ("check_failed_completed", False), ("check_result_exit_code", True),
    ("check_receipt_digest", "not-a-digest"), ("check_timeout_seconds", True),
    ("checked_source_revision", "f" * 64),
])
def test_stale_or_changed_summary_refuses_repair(bound, field, value):
    obligation, spec = bound
    observation = check_observation(obligation, spec, failed_receipt(spec))
    observation[field] = value
    assert not failed_observation_matches(obligation, observation, spec.source_revision)


def test_stream_is_revalidated_and_truncation_never_authorizes_repair(bound):
    _, spec = bound
    receipt = failed_receipt(spec)
    receipt.result.stdout = "bootstrap failed"
    assert not completed_check_receipt(spec, receipt, negative=True)
    receipt = failed_receipt(spec)
    receipt.result.stdout_truncated = True
    assert not completed_check_receipt(spec, receipt, negative=True)


def test_task_failure_requires_host_proof_and_current_revision(bound):
    obligation, spec = bound
    manifest = {"a.py": "f" * 64}
    current = manifest_digest(manifest)
    spec = replace(spec, source_revision=current)
    observation = check_observation(obligation, spec, failed_receipt(spec))
    host = SimpleNamespace(check_obligation=obligation,
                           changeset=SimpleNamespace(validate_workspace=lambda: manifest))
    step = SimpleNamespace(index=0, started_at="2026-10-28T00:00:00Z")
    failure = TaskRunner._check_failure(host, step, "failed", "pytest", 1,
                                      checked_revision=current, check_observation=observation)
    assert failure.kind == "deterministic_test_failure"
    for proof, revision in ((None, current), (observation, "0" * 64)):
        failure = TaskRunner._check_failure(host, step, "failed", "pytest", 1,
                                          checked_revision=revision, check_observation=proof)
        assert failure.kind == "check_unavailable"
