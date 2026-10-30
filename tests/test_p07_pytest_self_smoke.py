"""Pure smoke-result admission tests; no target imports or Docker processes."""
import importlib.util
import json
from dataclasses import replace
from pathlib import Path

import pytest

from atomic_flow.agents.execution import CommandResult
from atomic_flow.check_execution import CheckReceipt
from atomic_flow.check_pytest_self import PytestSelfIdentity
from atomic_flow.check_spec import CheckSpec

_module_spec = importlib.util.spec_from_file_location(
    "p07_pytest_self_check_smoke", Path(__file__).resolve().parents[1] / "tools/p07_pytest_self_check_smoke.py")
smoke = importlib.util.module_from_spec(_module_spec)
_module_spec.loader.exec_module(smoke)


def observed_case(label):
    identity = PytestSelfIdentity((3, 9, 20), "5.3.0.dev0",
                                 (("src/_pytest/__init__.py", "a" * 64), ("src/pytest.py", "b" * 64)))
    revision = "c" * 64
    spec = CheckSpec(task_id="p07_pytest_self_synthetic_" + label, task_commit="d" * 40,
                     base_commit=smoke.BASE_COMMIT, source_revision=revision, image=smoke.IMAGE,
                     interpreter=smoke.INTERPRETER, runner="pytest-self", test_paths=(smoke.TEST_PATH,),
                     target_root="/testbed", pytest_self_identity=identity)
    ordinary_exit = int(label == "base_fail")
    observed = {"nonce": "e" * 32, "root": "/testbed", "runner": "pytest-self",
                "interpreter": smoke.INTERPRETER,
                "interpreter_resolved": "/opt/miniconda3/envs/testbed/bin/python3.9",
                "python_version": [3, 9, 20], "pytest_version": identity.pytest_version,
                "pytest_module": "/testbed/src/pytest.py", "_pytest_module": "/testbed/src/_pytest/__init__.py",
                "pytest_self_identity": identity.as_dict(), "argv": ["-rA", smoke.TEST_PATH],
                "collected": 2, "completed": 2, "collection_errors": 0, "files": [smoke.TEST_PATH],
                "finished": True, "collection_complete": True, "skipped": int(label == "required_skip"),
                "failed": ordinary_exit, "call_failed": ordinary_exit, "setup_failed": 0, "teardown_failed": 0,
                "exit_code": ordinary_exit}
    result = CommandResult(returncode=5 if label == "required_skip" else ordinary_exit,
                           stdout=smoke.MARKER + json.dumps(observed), stderr="")
    receipt = CheckReceipt(spec.digest, revision, revision, label == "edited_pass",
                           None if label == "required_skip" else observed, result)
    return receipt, spec, identity, revision, observed


@pytest.mark.parametrize("label", ["base_fail", "edited_pass", "required_skip"])
def test_actual_executor_outcome_is_required_for_each_boundary_case(label):
    assert smoke.validate_case(label, *observed_case(label))


@pytest.mark.parametrize("change", [
    {"source_after": "f" * 64}, {"spec_digest": "f" * 64}, {"verified_success": True},
])
def test_source_contract_or_quality_claim_substitution_refused(change):
    receipt, spec, identity, revision, observed = observed_case("edited_pass")
    assert not smoke.validate_case("edited_pass", replace(receipt, **change), spec, identity, revision, observed)


@pytest.mark.parametrize("change", [
    {"collected": 1}, {"completed": 1}, {"collection_errors": 1}, {"setup_failed": 1},
    {"teardown_failed": 1}, {"pytest_module": "/usr/local/lib/python3.9/site-packages/pytest.py"},
    {"_pytest_module": "/usr/local/lib/python3.9/site-packages/_pytest/__init__.py"},
    {"python_version": [3, 9, 19]}, {"files": ["testing/unrelated.py"]},
    {"call_failed": True}, {"collection_complete": False},
])
def test_incomplete_or_different_runner_observation_refused(change):
    receipt, spec, identity, revision, observed = observed_case("base_fail")
    observed = observed | change
    assert not smoke.validate_case("base_fail", receipt, spec, identity, revision, observed)


@pytest.mark.parametrize("field", ["timed_out", "stdout_truncated", "stderr_truncated"])
def test_incomplete_process_output_refused(field):
    receipt, spec, identity, revision, observed = observed_case("edited_pass")
    result = replace(receipt.result, **{field: True})
    assert not smoke.validate_case("edited_pass", replace(receipt, result=result), spec, identity, revision, observed)


def test_required_skip_is_refused_despite_pytest_internal_zero_exit():
    receipt, spec, identity, revision, observed = observed_case("required_skip")
    result = replace(receipt.result, returncode=0)
    assert not smoke.validate_case("required_skip", replace(receipt, result=result), spec, identity, revision, observed)
