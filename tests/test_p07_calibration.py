"""Oracle infrastructure errors must never calibrate an agent task."""
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("evaluation_stage4", ROOT / "tools/evaluation_stage4.py")
stage4 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stage4)
sys.modules.setdefault("evaluation_stage4", stage4)
spec = importlib.util.spec_from_file_location("p07_isolated", ROOT / "tools/evaluation_p07_isolated.py")
p07 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p07)
TASK = {"FAIL_TO_PASS": ["required"], "PASS_TO_PASS": ["regression"]}


def test_sympy_uses_its_own_runner_without_requiring_host_pytest():
    assert "import pytest" not in p07.runner_identity_command("sympy/sympy")


def test_sympy_named_failure_alias_requires_matching_bare_test():
    raw = {"test_Derivative_kind": "FAILED", "test_other": "PASSED",
           "sympy/core/tests/test_kind.py:test_Derivative_kind": "FAILED",
           "sympy/core/tests/test_kind.py:test_unmatched": "FAILED",
           "sympy/core/tests/test_kind.py:test_other": "FAILED"}
    observed, diagnostics = p07.normalize_observations({"repo": "sympy/sympy"}, raw)
    assert diagnostics == {"sympy/core/tests/test_kind.py:test_Derivative_kind": "FAILED"}
    assert observed["sympy/core/tests/test_kind.py:test_unmatched"] == "FAILED"
    assert observed["sympy/core/tests/test_kind.py:test_other"] == "FAILED"
    assert "import pytest,_pytest" in p07.runner_identity_command("pytest-dev/pytest")


def test_framework_source_paths_are_not_mistaken_for_oracle_files():
    for path in ["src/_pytest/main.py", "src/_pytest/assertion/rewrite.py", "src/pytest/__init__.py"]:
        assert not stage4.protected_test_path(path)
    for path in ["tests/test_kind.py", "testing/acceptance_test.py", "pkg/test_a.py", "pkg/a_test.py", "conftest.py", "pytest.ini"]:
        assert stage4.protected_test_path(path)


@pytest.mark.parametrize("observed,exit_code", [
    ({}, 0), ({"required": "ERROR", "regression": "PASSED"}, 1),
    ({"required": "SKIPPED", "regression": "PASSED"}, 0),
    ({"required": "FAILED"}, 1), ({"required": "FAILED", "regression": "PASSED"}, 2),
])
def test_environment_missing_error_skip_and_collection_are_not_requirement_fail(observed, exit_code):
    assert p07.classify(TASK, observed, completed=True, exit_code=exit_code)["status"] == "infrastructure_error"


def test_calibration_requires_real_assertion_failures_and_preserved_regressions():
    bad = p07.classify(TASK, {"required": "FAILED", "regression": "PASSED"}, completed=True, exit_code=1)
    good = p07.classify(TASK, {"required": "PASSED", "regression": "PASSED"}, completed=True, exit_code=0)
    assert p07.calibration_decision([bad, good, bad])
    assert not p07.calibration_decision([good, good, bad])
    assert not p07.calibration_decision([bad, good, {**bad, "regressions_passed": False}])


def test_gold_cannot_pass_with_unexpected_regression_failure():
    result = p07.classify(TASK, {"required": "PASSED", "regression": "PASSED", "extra": "FAILED"}, completed=True, exit_code=0)
    assert not result["verified_success"]
    assert not result["regressions_passed"]


def test_unrelated_fixture_error_cannot_masquerade_as_valid_negative_control():
    result = p07.classify(TASK, {"required": "FAILED", "regression": "PASSED", "extra": "ERROR"}, completed=True, exit_code=1)
    assert result["status"] == "infrastructure_error"


def test_sympy_failure_header_diagnostics_do_not_invent_extra_tests():
    raw = {"required": "FAILED", "regression": "PASSED", "sympy/core/tests/test_kind.py:62": "FAILED"}
    observed, diagnostics = p07.normalize_observations({"repo": "sympy/sympy"}, raw)
    assert observed == {"required": "FAILED", "regression": "PASSED"}
    assert diagnostics == {"sympy/core/tests/test_kind.py:62": "FAILED"}


def test_historical_test_command_and_exit_receipt_survive_without_reinstall():
    spec = SimpleNamespace(eval_script_list=["conda activate testbed", "pip install .", ": '>>>>> Start Test Output'", "pytest -rA tests", ": '>>>>> End Test Output'"])
    script = p07.evaluation_script(spec, "pip install .")
    assert "pip install ." not in script
    assert script.index("pytest -rA tests") < script.index("p07_test_exit=$?") < script.index(": '>>>>> End Test Output'")
    assert "PYTEST_DISABLE_PLUGIN_AUTOLOAD=1" in script
    assert script.index("exec 2>&1") < script.index("set -uxo pipefail")


def test_all_thirty_wrong_controls_are_unique_source_changes():
    corpus = stage4.load_manifest(ROOT / "benchmarks/stage4/corpus.json")
    controls = json.loads((ROOT / "benchmarks/stage4/p07-controls.json").read_text())["tasks"]
    assert set(controls) == {t["instance_id"] for t in corpus["tasks"]}
    for task in corpus["tasks"]:
        control = controls[task["instance_id"]]
        assert task["patch"].count(control["old"]) == 1
        wrong = task["patch"].replace(control["old"], control["new"])
        assert wrong != task["patch"] and control["rationale"]
        assert stage4.patch_paths(wrong) == stage4.patch_paths(task["patch"])
