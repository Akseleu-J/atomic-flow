"""Oracle infrastructure errors must never calibrate an agent task."""
import importlib.util
import json
import re
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


def _summary_parser(body, unused):
    return {node: state for state, node in re.findall(r"^(PASSED|FAILED|ERROR) (\S+)", body, re.MULTILINE)}


def test_nested_pytest_self_test_failures_are_diagnostics_not_outer_cases():
    body = "\n".join(["FAILED nested/test_fake.py::test_bad", "=== short test summary info ===",
        "FAILED nested/test_fake.py::test_bad", "=== 1 failed in 0.1s ===", "captured subprocess output ends",
        "=== short test summary info ===", "PASSED testing/test_outer.py::test_regression",
        "FAILED testing/test_outer.py::test_required", "=== 1 failed, 1 passed in 1.0s ===", "P07_TEST_EXIT=1"])
    observed, diagnostics = p07.parse_observations({"repo": "pytest-dev/pytest"}, body, _summary_parser, None)
    assert observed == {"testing/test_outer.py::test_regression": "PASSED", "testing/test_outer.py::test_required": "FAILED"}
    assert diagnostics["discarded_nested_or_alias_observations"] == {"nested/test_fake.py::test_bad": "FAILED"}
    assert diagnostics["scope"] == "final_outer_pytest_short_summary"


def test_outer_collection_error_survives_summary_selection():
    body = "=== short test summary info ===\nERROR testing/test_outer.py::test_required\n=== 1 error in 0.1s ===\n"
    observed, diagnostics = p07.parse_observations({"repo": "pytest-dev/pytest"}, body, _summary_parser, None)
    assert observed["testing/test_outer.py::test_required"] == "ERROR"
    assert p07.classify({"FAIL_TO_PASS": ["testing/test_outer.py::test_required"], "PASS_TO_PASS": []},
                        observed, completed=True, exit_code=2)["status"] == "infrastructure_error"


def test_color_codes_do_not_corrupt_pytest_node_identity():
    body = "\x1b[31mFAILED\x1b[0m tests/test_a.py::\x1b[1mtest_required\x1b[0m\n"
    observed, diagnostics = p07.parse_observations({"repo": "sphinx-doc/sphinx"}, body, _summary_parser, None)
    assert observed == {"tests/test_a.py::test_required": "FAILED"}
    assert diagnostics["ansi_removed"]


def test_sphinx_verbose_progress_preserves_passes_outside_failure_only_summary():
    body = "\n".join(["tests/test_a.py::test_regression PASSED [ 50%]",
        "tests/test_a.py::test_required FAILED [100%]", "=== short test summary info ===",
        "FAILED tests/test_a.py::test_required", "=== 1 failed, 1 passed in 1.0s ==="])
    observed, diagnostics = p07.parse_observations({"repo": "sphinx-doc/sphinx"}, body, _summary_parser, None)
    assert observed == {"tests/test_a.py::test_regression": "PASSED", "tests/test_a.py::test_required": "FAILED"}
    assert diagnostics["scope"] == "complete_test_output"
    assert diagnostics["verbose_status_normalized"]


def test_pylint_subprocesses_do_not_receive_forced_source_pythonpath():
    spec = SimpleNamespace(eval_script_list=[": '>>>>> Start Test Output'", "pytest -rA tests", ": '>>>>> End Test Output'"])
    assert "unset PYTHONPATH" in p07.evaluation_script(spec, None, "pylint-dev/pylint")
    assert "export PYTHONPATH=/testbed:/testbed/src" in p07.evaluation_script(spec, None, "pytest-dev/pytest")
    assert "export PYTHONPATH=/testbed:/testbed/src" in p07.evaluation_script(spec, None, "psf/requests")


def test_sphinx_only_adds_reporting_flags_without_changing_selectors():
    command = "tox --current-env -epy39 -v -- tests/test_build_html.py"
    spec = SimpleNamespace(eval_script_list=[": '>>>>> Start Test Output'", command, ": '>>>>> End Test Output'"])
    assert command + " -rA -vv --color=no" in p07.evaluation_script(spec, None, "sphinx-doc/sphinx")
    assert command + " -rA -vv --color=no" not in p07.evaluation_script(spec, None, "psf/requests")


@pytest.mark.parametrize("repo,module", [("psf/requests", "requests"), ("pylint-dev/pylint", "pylint"),
    ("sphinx-doc/sphinx", "sphinx"), ("sympy/sympy", "sympy")])
def test_runner_identity_records_historical_target_package_path(repo, module):
    assert "print(" + module + ".__file__)" in p07.runner_identity_command(repo)
    if repo == "pylint-dev/pylint":
        assert "PYTHONPATH=" not in p07.runner_identity_command(repo)


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


def test_v2_controls_are_prospective_exact_gold_mutants_with_v1_provenance():
    v1 = (ROOT / "benchmarks/stage4/p07-controls.json").read_bytes()
    v2 = json.loads((ROOT / "benchmarks/stage4/p07-controls-v2.json").read_text())
    assert v2["version"] == 2 and v2["previous_controls_sha256"] == p07.sha(v1)
    old = json.loads(v1)["tasks"]
    assert {task_id for task_id, value in v2["tasks"].items() if value != old[task_id]} == {
        "pylint-dev__pylint-5859", "pylint-dev__pylint-7993"}
    for task in stage4.load_manifest(ROOT / "benchmarks/stage4/corpus.json")["tasks"]:
        control = v2["tasks"][task["instance_id"]]
        assert task["patch"].count(control["old"]) == 1
        assert p07.sha(task["patch"].encode()) == control["gold_sha256"]


def test_v2_punctuation_control_preserves_word_notes_but_rejects_bug_requirement():
    row = json.loads((ROOT / "benchmarks/stage4/p07-controls-v2.json").read_text())["tasks"]["pylint-dev__pylint-5859"]
    pattern = re.search(r'rf"(.*)"', row["new"])[1]
    assert re.search(pattern.replace("{notes}", "TODO"), "# TODO: message")
    assert re.search(pattern.replace("{notes}", "TODO"), "# TODO")
    assert not re.search(pattern.replace("{notes}", "TODO"), "# TODOist")
    assert not re.search(pattern.replace("{notes}", re.escape("???")), "# ???: message")


def test_v2_template_control_keeps_unknown_names_but_misparses_escaped_header():
    row = json.loads((ROOT / "benchmarks/stage4/p07-controls-v2.json").read_text())["tasks"]["pylint-dev__pylint-7993"]
    pattern = re.search(r're.findall\(r"(.*)", template\)', row["new"])[1]
    ordinary = [match[0] for match in re.findall(pattern, "{path}:{line}:{categ}:({a_second_new_option:03d})")]
    assert ordinary == ["path", "line", "categ", "a_second_new_option"]
    header = [match[0] for match in re.findall(pattern, '{{ "Category": "{category}" }}')]
    assert header != ["category"]
