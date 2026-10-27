"""Legacy oracle admission remains fail-closed across source, runner and results."""
import ast
import copy
import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
SPEC = importlib.util.spec_from_file_location("p07_sympy13971_calibrate", ROOT / "tools/p07_sympy13971_calibrate.py")
cal = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cal)


def identity():
    return {"version_info": [3, 6, 15], "executable": cal.transplant.PREFIX + "/bin/python3.6",
            "prefix": cal.transplant.PREFIX, "isolated": 1, "sympy_module": "/testbed/sympy/__init__.py",
            "mpmath_module": cal.transplant.PREFIX + "/lib/python3.6/site-packages/mpmath/__init__.py",
            "mpmath_version": "1.2.1", "mpmath_module_sha256": "a" * 64,
            "native_source_sha256": cal.RUNNER_SOURCES, "native_argv": list(cal.NATIVE_ARGV),
            "warning_policy": ["ignore::UserWarning", "ignore::SyntaxWarning"], "child_RV_exit": 127,
            "child_RV_output": "loader unavailable", "child_RV_output_sha256": cal.sha(b"loader unavailable"),
            "child_RV_output_truncated": False, "subprocess_hash_randomization": "unavailable_native_fallback"}


def native_parser(body, spec):
    statuses = {"ok": "PASSED", "F": "FAILED", "E": "ERROR", "s": "SKIPPED"}
    return {test: statuses[value] for test, value in re.findall(r"^(test\w+) (ok|F|E|s)$", body, re.MULTILINE)}


def native_log(lines="test_requirement ok\ntest_regression ok", exit_code=0):
    return (cal.IDENTITY_PREFIX + json.dumps(identity()) + "\n" + cal.START + "\n" + lines
            + "\ntests finished: 2 passed in 0.01 seconds\nP07_TEST_EXIT=" + str(exit_code) + "\n" + cal.END + "\n")


TASK = {"repo": "sympy/sympy", "FAIL_TO_PASS": ["test_requirement"], "PASS_TO_PASS": ["test_regression"],
        "base_commit": cal.transplant.COMMIT, "patch": "", "test_patch": ""}
ADMITTED = {"mpmath_module_sha256": "a" * 64}


def test_complete_native_gold_is_graded_and_negative_requirement_keeps_regressions():
    gold = cal.classify_native(TASK, native_log(), 0, native_parser, None, ADMITTED)
    assert gold["verified_success"] and gold["regressions_passed"]
    bad = cal.classify_native(TASK, native_log("test_requirement F\ntest_regression ok", 1), 1,
                              native_parser, None, ADMITTED)
    assert bad["status"] == "requirement_failed" and bad["regressions_passed"]
    assert not bad["verified_success"] and bad["assertion_failures"] == ["test_requirement"]


@pytest.mark.parametrize("lines", ["test_requirement ok", "test_requirement ok\ntest_regression s",
    "test_requirement ok\ntest_regression E", "test_requirement ok\ntest_regression F"])
def test_missing_skipped_error_or_regressing_tests_cannot_be_success(lines):
    result = cal.classify_native(TASK, native_log(lines), 0, native_parser, None, ADMITTED)
    assert result["verified_success"] is False


@pytest.mark.parametrize("mutation", ["no_end", "duplicate_start", "no_footer", "wrong_exit", "no_identity", "late_identity"])
def test_incomplete_or_ambiguous_native_transcripts_are_refused(mutation):
    text = native_log()
    if mutation == "no_end":
        text = text.replace(cal.END, "")
    elif mutation == "duplicate_start":
        text += cal.START
    elif mutation == "no_footer":
        text = text.replace("tests finished:", "interrupted:")
    elif mutation == "wrong_exit":
        text = text.replace("P07_TEST_EXIT=0", "P07_TEST_EXIT=1")
    elif mutation == "no_identity":
        text = text.split("\n", 1)[1]
    else:
        first, rest = text.split("\n", 1)
        text = rest + first + "\n"
    with pytest.raises(ValueError):
        cal.classify_native(TASK, text, 0, native_parser, None, ADMITTED)


@pytest.mark.parametrize("field,value", [("isolated", 0), ("native_argv", ["bin/test", "--quick"]),
    ("mpmath_module_sha256", "b" * 64), ("child_RV_output", "changed"), ("child_RV_output_truncated", True),
    ("subprocess_hash_randomization", "native_child_startup_available_execution_not_attested")])
def test_runtime_or_capability_claim_cannot_drift(field, value):
    proof = identity()
    proof[field] = value
    with pytest.raises(ValueError):
        cal.validate_identity(proof, ADMITTED)


def test_host_bootstrap_preserves_native_flags_and_fallback_without_running_target():
    source = cal.bootstrap_source("a" * 64)
    ast.parse(source)
    assert "runpy.run_path('/testbed/bin/test',run_name='__main__')" in source
    assert repr(list(cal.NATIVE_ARGV)) in source
    assert "sys.executable=" not in source and "PYTHONHASHSEED" not in source
    assert "subprocess.Popen([sys.executable,'-RV']" in source
    assert "warnings.filterwarnings('ignore',category=UserWarning)" in source
    with pytest.raises(ValueError):
        cal.bootstrap_source("unbound")


def test_profile_freezes_all_103_obligations_and_protected_patch():
    profile = json.loads(cal.PROFILE.read_bytes())
    tasks = cal.load_manifest(ROOT / "benchmarks/stage4/corpus-p07-v2.json")["tasks"]
    task = next(item for item in tasks if item["instance_id"] == cal.TASK)
    assert profile["oracle"]["FAIL_TO_PASS"] == task["FAIL_TO_PASS"]
    assert profile["oracle"]["PASS_TO_PASS"] == task["PASS_TO_PASS"]
    assert len(set(task["FAIL_TO_PASS"] + task["PASS_TO_PASS"])) == 103
    assert profile["oracle"]["test_patch_sha256"] == cal.sha(task["test_patch"].encode())


@pytest.fixture
def qualified(tmp_path):
    profile = json.loads(cal.PROFILE.read_bytes())
    digest = cal.sha(cal.PROFILE.read_bytes().replace(b"\r\n", b"\n"))
    probe = {"version_info": [3, 6, 15], "prefix": cal.transplant.PREFIX, "base_prefix": cal.transplant.PREFIX,
        "stdlib": cal.transplant.PREFIX + "/lib/python3.6", "executable": cal.transplant.PREFIX + "/bin/python3.6",
        "imports": {name: {"status": "imported", "path": cal.transplant.PREFIX + "/lib/python3.6/" + name + ".py"}
                    for name in ["ssl", "ctypes", "sqlite3", "bz2", "lzma", "encodings"]},
        "mapped_libraries": [cal.transplant.PREFIX + "/lib/libpython3.6m.so", "/opt/p07-donor/lib/libc-2.28.so"],
        "sys_path": [cal.transplant.PREFIX + "/lib/python3.6"]}
    source = {"head": "c" * 40, "diff": "", "status": ""}
    base = {"Id": "sha256:" + "1" * 64, "RootFS": {"Layers": ["base"]}, "Architecture": "amd64", "Os": "linux"}
    donor = {"Id": "sha256:" + "2" * 64, "RootFS": {"Layers": ["donor"]}, "Architecture": "amd64", "Os": "linux"}
    parent = {"status": "transplant_stdlib_qualified", "probe_compatible": True, "observed": probe,
        "base_image": cal.transplant.BASE, "donor_image": cal.transplant.DONOR, "base_commit": cal.transplant.COMMIT,
        "image_id": "sha256:" + "3" * 64, "image_inspect": {"RootFS": {"Layers": ["base", "runtime"]}},
        "base_image_inspect": base, "donor_image_inspect": donor, "before": source, "after": source,
        "launcher_sha256": cal.sha(cal.transplant.LAUNCHER_SOURCE.encode()), "probe_sha256": cal.sha(cal.transplant.PROBE.encode()),
        "cleanup_confirmed": True, "cleanup": [{"confirmed": True}, {"confirmed": True}]}
    image = {"Id": "sha256:" + "4" * 64, "RootFS": {"Layers": ["base", "runtime", "mpmath"]},
        "Architecture": "amd64", "Os": "linux", "Config": {"Labels": {
            "atomic.p07.legacy.base": cal.transplant.BASE, "atomic.p07.legacy.donor": cal.transplant.DONOR,
            "atomic.p07.legacy.launcher": parent["launcher_sha256"], "atomic.p07.sympy.base": cal.transplant.BASE,
            "atomic.p07.sympy.parent": parent["image_id"], "atomic.p07.sympy.profile": digest}}}
    wheel = {key: profile["dependency"][key] for key in ["name", "version", "filename", "url", "sha256", "wheel_metadata_sha256"]}
    wheel["module_member_sha256"] = "a" * 64
    report = {"status": "runtime_dependencies_qualified", "task": cal.TASK, "base_image": cal.transplant.BASE,
        "donor_image": cal.transplant.DONOR, "base_commit": cal.transplant.COMMIT, "profile_sha256": digest,
        "image_id": image["Id"], "image_inspect": image, "parent_image_id": parent["image_id"],
        "parent_transplant_report": parent, "parent_transplant_report_sha256": cal.sha((json.dumps(parent, sort_keys=True, indent=2) + "\n").encode()),
        "model_calls": 0, "provider_credentials": "none", "target_setup_or_tests_executed": False,
        "cleanup_confirmed": True, "cleanup_exit": 0, "cleanup": [{"confirmed": True}, {"confirmed": True}],
        "launcher_sha256": parent["launcher_sha256"], "runtime_probe": probe, "wheel": wheel,
        "before": source, "after": source, "mpmath_probe": {"version": "1.2.1", "module_sha256": "a" * 64,
            "prefix": cal.transplant.PREFIX, "executable": cal.transplant.PREFIX + "/bin/python3.6",
            "module_path": cal.transplant.PREFIX + "/lib/python3.6/site-packages/mpmath/__init__.py"}}
    return tmp_path, report, image, base, donor


def admit(fixture):
    directory, report, image, base, donor = fixture
    cal.save(directory / "sympy-runtime.json", report)
    return cal.runtime_admission(image["Id"], directory, cal.PROFILE, image, base, donor)


def test_complete_runtime_proof_is_admitted(qualified):
    assert admit(qualified)["mpmath_module_sha256"] == "a" * 64


@pytest.mark.parametrize("mutation", ["ancestry", "cleanup", "wheel", "source", "parent_hash", "mapped_library", "profile_label"])
def test_altered_runtime_proof_is_refused(qualified, mutation):
    directory, report, image, base, donor = copy.deepcopy(qualified)
    if mutation == "ancestry":
        image["RootFS"]["Layers"][0] = "foreign-base"
    elif mutation == "cleanup":
        report["cleanup"][0]["confirmed"] = False
    elif mutation == "wheel":
        report["wheel"]["sha256"] = "0" * 64
    elif mutation == "source":
        report["after"]["diff"] = "changed target"
    elif mutation == "parent_hash":
        report["parent_transplant_report_sha256"] = "0" * 64
    elif mutation == "mapped_library":
        report["runtime_probe"]["mapped_libraries"].append("/usr/lib/foreign.so")
    else:
        image["Config"]["Labels"]["atomic.p07.sympy.profile"] = "0" * 64
    with pytest.raises(ValueError):
        admit((directory, report, image, base, donor))
