"""The path diagnostic changes only native Python startup for admitted7114."""
import importlib.util
import shlex
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("evaluation_stage4", ROOT / "tools/evaluation_stage4.py")
stage4 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stage4)
sys.modules.setdefault("evaluation_stage4", stage4)
spec = importlib.util.spec_from_file_location("p07_pylint_launcher", ROOT / "tools/evaluation_p07_isolated.py")
p07 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p07)


def source_spec(command="pytest -rA tests/checkers/unittest_imports.py tests/lint/unittest_lint.py"):
    return SimpleNamespace(eval_script_list=["conda activate testbed", "cd /testbed", "reviewed-install",
        ": '>>>>> Start Test Output'", command, ": '>>>>> End Test Output'", "git checkout fixed-base tests/checkers/unittest_imports.py"])


def test_explicit_console_is_byte_identical_to_existing_default_script():
    spec = source_spec()
    assert p07.evaluation_script(spec, "reviewed-install", "pylint-dev/pylint") == p07.evaluation_script(
        spec, "reviewed-install", "pylint-dev/pylint", pytest_launcher="console")


def test_diagnostic_preserves_all_native_arguments_script_policy_and_exit_receipt():
    spec = source_spec("pytest -rA --color=no 'tests/path with spaces.py' tests/lint/unittest_lint.py")
    before = p07.evaluation_script(spec, "reviewed-install", "pylint-dev/pylint")
    after = p07.evaluation_script(spec, "reviewed-install", "pylint-dev/pylint", pytest_launcher="python-c")
    original = next(line for line in before.splitlines() if line.startswith("pytest "))
    diagnostic = next(line for line in after.splitlines() if line.startswith("python -c "))
    assert shlex.split(original)[1:] == shlex.split(diagnostic)[3:]
    assert shlex.split(diagnostic)[:3] == ["python", "-c", "import sys,pytest; raise SystemExit(pytest.main(sys.argv[1:]))"]
    assert after.replace(p07.PYTEST_PYTHON_C, "pytest") == before
    assert "unset PYTHONPATH\nexport PYTEST_DISABLE_PLUGIN_AUTOLOAD=1" in after
    assert "sys.path" not in after and "astroid_cache" not in after
    assert "P07_TEST_EXIT=%s" in after and "reviewed-install" not in after


@pytest.mark.parametrize("task,dependency", [("pylint-dev__pylint-7080", {"image_id": "sha256:" + "a" * 64}),
                                            ("pylint-dev__pylint-7114", None),
                                            ("sphinx-doc__sphinx-8713", {"image_id": "sha256:" + "a" * 64})])
def test_python_c_requires_exact_task_and_already_admitted_overlay(task, dependency):
    with pytest.raises(ValueError, match="admitted dependency"):
        p07.validate_pytest_launcher("python-c", task, dependency)


def test_console_needs_no_overlay_and_diagnostic_accepts_admitted7114():
    p07.validate_pytest_launcher("console", "pylint-dev__pylint-7080", None)
    p07.validate_pytest_launcher("python-c", "pylint-dev__pylint-7114", {"image_id": "sha256:" + "a" * 64,
        "pins": {"astroid": "2.12.2"}, "profile_sha256": "b" * 64, "build_receipt_sha256": "c" * 64})
    with pytest.raises(ValueError, match="admitted dependency"):
        p07.validate_pytest_launcher("python-c", "pylint-dev__pylint-7114", {})


@pytest.mark.parametrize("repo,commands", [("psf/requests", ["pytest -rA tests.py"]),
                                         ("pylint-dev/pylint", ["python -m pytest -rA tests.py"]),
                                         ("pylint-dev/pylint", ["pytest tests/one.py", "pytest tests/two.py"])])
def test_ambiguous_or_non_pylint_native_script_is_not_rewritten(repo, commands):
    spec = source_spec()
    spec.eval_script_list = [": '>>>>> Start Test Output'", *commands, ": '>>>>> End Test Output'"]
    with pytest.raises(ValueError, match="exactly one native Pylint"):
        p07.evaluation_script(spec, None, repo, pytest_launcher="python-c")


def test_unknown_launcher_is_refused_by_admission_and_script_generation():
    with pytest.raises(ValueError, match="unreviewed"):
        p07.validate_pytest_launcher("custom", "pylint-dev__pylint-7114", {})
    with pytest.raises(ValueError, match="unreviewed"):
        p07.evaluation_script(source_spec(), None, "pylint-dev/pylint", pytest_launcher="custom")


@pytest.mark.parametrize("extra", [[], ["--dependency-image", "sha256:" + "a" * 64,
    "--dependency-receipt", "unused-receipt", "--dependency-profile", "unused-profile"]])
def test_cli_refuses_unadmitted_diagnostic_before_output_or_worker_access(tmp_path, monkeypatch, extra):
    output = tmp_path / "output"
    task = "pylint-dev__pylint-7114" if not extra else "pylint-dev__pylint-7080"
    monkeypatch.setattr(sys, "argv", ["evaluation_p07_isolated.py", "--task", task,
        "--output", str(output), "--pytest-launcher", "python-c", *extra])
    monkeypatch.setattr(p07, "command", lambda *a, **k: pytest.fail("worker access must not occur"))
    with pytest.raises(ValueError):
        p07.main()
    assert not output.exists()
