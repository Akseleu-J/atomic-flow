"""Short traceback admission preserves the full protected pytest7168 suite."""
import copy
import importlib.util
import json
import shlex
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
SPEC = importlib.util.spec_from_file_location("p07_pytest7168_calibrate", ROOT / "tools/p07_pytest7168_calibrate.py")
adapter = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(adapter)


def task_and_control():
    task = next(t for t in json.loads((ROOT / "benchmarks/stage4/corpus-p07-v2.json").read_text(encoding="utf-8"))["tasks"]
                if t["instance_id"] == adapter.TASK)
    control = json.loads((ROOT / "benchmarks/stage4/p07-controls-v5.json").read_text(encoding="utf-8"))["tasks"][adapter.TASK]
    return task, control


def test_actual_task_keeps_all_eleven_f2p_and_exact_existing_wrong_control():
    task, control = task_and_control()
    before = copy.deepcopy(task)
    wrong = adapter.admit_task(task, control)
    assert adapter.oracle.sha(wrong.encode()) == adapter.WRONG_SHA
    assert task == before
    assert len(adapter.REQUIRED) == 11 and task["PASS_TO_PASS"] == []


@pytest.mark.parametrize("field,value", [
    ("instance_id", "pytest-dev__pytest-6116"), ("repo", "other/pytest"),
    ("base_commit", "0" * 40), ("version", "5.5"),
    ("FAIL_TO_PASS", list(adapter.REQUIRED[:-1])),
    ("PASS_TO_PASS", [adapter.REQUIRED[0]]),
    ("patch", ""), ("test_patch", ""),
])
def test_task_identity_or_requirement_narrowing_is_refused(field, value):
    task, control = task_and_control()
    task[field] = value
    with pytest.raises(ValueError, match="reviewed pytest7168"):
        adapter.admit_task(task, control)


@pytest.mark.parametrize("field,value", [("gold_sha256", "0" * 64), ("old", ""),
                                         ("new", "different"), ("rationale", "")])
def test_wrong_patch_substitution_is_refused(field, value):
    task, control = task_and_control()
    control[field] = value
    with pytest.raises(ValueError):
        adapter.admit_task(task, control)


def test_spec_copy_changes_only_exact_full_file_reporting_flag():
    original = SimpleNamespace(eval_script_list=["prepare", adapter.NATIVE, "restore"], token=object())
    result = adapter.reviewed_spec(original)
    assert result is not original
    assert result.eval_script_list == ["prepare", adapter.REVIEWED, "restore"]
    assert original.eval_script_list == ["prepare", adapter.NATIVE, "restore"]
    assert result.token is original.token


@pytest.mark.parametrize("commands", [[], [adapter.NATIVE] * 2,
                                     [adapter.NATIVE, "pytest other.py"],
                                     [adapter.NATIVE + " -k broken"], [adapter.REVIEWED]])
def test_changed_native_command_is_not_silently_rewritten(commands):
    with pytest.raises(ValueError, match="native harness command"):
        adapter.reviewed_spec(SimpleNamespace(eval_script_list=commands))


def test_source_admission_precedes_the_same_complete_native_test_command():
    spec = SimpleNamespace(eval_script_list=["prepare", ": '>>>>> Start Test Output'",
                                            adapter.REVIEWED, ": '>>>>> End Test Output'", "restore"])
    script = adapter.evaluation_script(spec, None)
    lines = script.splitlines()
    command_index = lines.index(adapter.REVIEWED)
    check = shlex.split(lines[command_index - 1])
    assert check[:2] == ["python", "-c"]
    assert check[-3:] == ["||", "exit", "91"]
    assert "P07_REVIEWED_SOURCE_SHA256=" in check[2]
    assert adapter.SOURCE_HASHES[adapter.TEST_FILE] in check[2]
    assert "sys.exit('pinned runner/protected source mismatch')" in check[2]
    assert "assert " not in check[2]
    assert lines.count(adapter.REVIEWED) == 1
    assert "p07_test_exit=$?" in lines[command_index + 1]
    assert lines[-1] == "restore"
    assert len(adapter.SOURCE_HASHES) == 7


def test_exact_image_admission_rejects_another_digest_or_platform():
    info = {"Id": adapter.IMAGE_ID, "RepoDigests": [adapter.IMAGE], "Os": "linux", "Architecture": "amd64"}
    adapter.admit_image(info)
    for key, value in (("Id", "sha256:" + "0" * 64), ("RepoDigests", []),
                       ("Os", "windows"), ("Architecture", "arm64")):
        with pytest.raises(ValueError, match="image mismatch"):
            adapter.admit_image({**info, key: value})


def test_internal_error_is_still_not_a_calibrated_negative():
    task, _ = task_and_control()
    base = adapter.oracle.classify(task, {}, completed=True, exit_code=3)
    assert base == {"status": "infrastructure_error", "verified_success": False}
    assert not adapter.oracle.calibration_decision([
        base, {"verified_success": True}, {"status": "requirement_failed", "assertion_failures": [adapter.REQUIRED[-1]],
                                           "regressions_passed": True}])


def test_fresh_output_refusal_happens_before_any_evaluator_import(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["p07_pytest7168_calibrate", "--output", str(tmp_path)])
    with pytest.raises(ValueError, match="fresh output"):
        adapter.main()
