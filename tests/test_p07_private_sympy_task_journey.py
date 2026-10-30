"""Pure acceptance checks; no backend, TaskRunner or target process starts."""
import importlib.util
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest


@pytest.fixture
def journey(monkeypatch):
    tools = Path(__file__).resolve().parents[1] / "tools"
    monkeypatch.syspath_prepend(str(tools))
    calibration = ModuleType("p07_sympy13971_calibrate")
    calibration.RUNNER_SOURCES = {}
    calibration.runtime_admission = lambda *args: None
    monkeypatch.setitem(__import__("sys").modules, "p07_sympy13971_calibrate", calibration)
    module_spec = importlib.util.spec_from_file_location("p07_private_journey_test", tools / "p07_private_sympy_task_journey.py")
    module = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(module)
    return module


def admitted(journey):
    revision, final_revision = "a" * 64, "b" * 64
    spec = SimpleNamespace(source_revision=revision, digest="c" * 64)
    obligation = SimpleNamespace(digest="d" * 64)
    failure = {"kind": "deterministic_test_failure", "evidence": {
        "version": 2, "check": "ordinary_test", "exit_code": 1, "step_index": 0, "started_at": 1.0,
        "outcome": "failed", "source_revision": revision, "checked_source_revision": revision,
        "runner": "sympy-native", "check_completed": True, "check_failed_completed": True,
        "check_obligation_digest": obligation.digest, "check_spec_digest": spec.digest, "check_receipt_digest": "e" * 64}}
    completion = {"ready_to_publish": True, "check_runner": "sympy-native", "check_passed": True,
                  "check_completed": True, "check_result_exit_code": 0,
                  "checked_source_revision": final_revision, "check_obligation_digest": obligation.digest}
    task = SimpleNamespace(status="done", replan_count=1, completion_report=completion,
                           graph_sync={"failed": False, "coverage_complete": True, "coverage_omissions": []})
    return task, dict(changed=[journey.MODULE_PATH], negative=[{"checked_source_revision": revision}],
                      positive=[{"checked_source_revision": final_revision}], failures=[failure], spec=spec,
                      obligation=obligation, final_revision=final_revision, runner_unchanged=True,
                      final_module=b"value = 42\n", final_test=journey.ORDINARY_TEST.encode())


def test_actual_public_journey_accepts_consistent_complete_or_explicit_omitted_graph_coverage(journey):
    task, kwargs = admitted(journey)
    assert journey.validate_journey(task, **kwargs)
    task.graph_sync = {"failed": False, "coverage_complete": False, "coverage_omissions": [{"path": "large.py"}]}
    assert journey.validate_journey(task, **kwargs)


@pytest.mark.parametrize("field,value", [
    ("version", 1), ("runner", "pytest"), ("source_revision", "0" * 64),
    ("checked_source_revision", "0" * 64), ("check_spec_digest", "0" * 64),
    ("check_obligation_digest", "0" * 64), ("check_completed", False), ("check_receipt_digest", "bad"),
])
def test_failure_v2_must_keep_native_runner_exact_source_and_obligation(journey, field, value):
    task, kwargs = admitted(journey)
    kwargs["failures"][0]["evidence"][field] = value
    assert not journey.validate_journey(task, **kwargs)


@pytest.mark.parametrize("changed", [[], ["unapproved.py"], ["sympy/p07_probe.py", "sympy/core/tests/test_p07_native_check.py"]])
def test_publication_must_change_only_admitted_probe(journey, changed):
    task, kwargs = admitted(journey)
    kwargs["changed"] = changed
    assert not journey.validate_journey(task, **kwargs)


@pytest.mark.parametrize("graph", [
    {"failed": True, "coverage_complete": True, "coverage_omissions": []},
    {"coverage_complete": True, "coverage_omissions": []},
    {"failed": False, "coverage_complete": False, "coverage_omissions": []},
    {"failed": False, "coverage_complete": True, "coverage_omissions": [{"path": "large.py"}]},
])
def test_graph_failure_or_ambiguous_coverage_cannot_qualify(journey, graph):
    task, kwargs = admitted(journey)
    task.graph_sync = graph
    assert not journey.validate_journey(task, **kwargs)


def test_invalid_label_rejected_before_provisioning(journey, tmp_path):
    with pytest.raises(ValueError, match="verification method"):
        journey.journey(tmp_path, tmp_path, tmp_path / "out", "shell")
