"""Prospective pytest7168 oracle with reporting-only short tracebacks.

Only the frozen base/gold/wrong triple is admitted. The generic oracle and
historical results remain unchanged; no target code executes on this host.
"""
from __future__ import annotations

import argparse
import copy
import importlib.metadata
import json
import shlex
from pathlib import Path

import evaluation_p07_isolated as oracle
from evaluation_stage4 import load_manifest

ROOT = Path(__file__).resolve().parents[1]
TASK = "pytest-dev__pytest-7168"
BASE = "4787fd64a4ca0dba5528b5651bddd254102fe9f3"
IMAGE = ("swebench/sweb.eval.x86_64.pytest-dev_1776_pytest-7168@sha256:"
         "9eef4ab057f209925aaf40a4f644f82c2aa3b525e02e341d800a9794225fa68e")
IMAGE_ID = "sha256:fa2614449afb464c272c3bd0c5ebc66d7cd6a046b4a98ac65ee1b0df2594a9ff"
GOLD_SHA = "6461134e72ddd5997decb656090500402441f32d12bc9f583a9bce0e2f3e806b"
TEST_PATCH_SHA = "ae6288ce2b11af1e23717c3234930c64a88181491124fa236253fdd7634ccc18"
WRONG_SHA = "8958ffdd23b5bb39ad49f70f4f4b0266d9cbcbde747e6f11464ffc0cde65a834"
TEST_FILE = "testing/io/test_saferepr.py"
NATIVE = "pytest -rA " + TEST_FILE
REVIEWED = "pytest -rA --tb=short " + TEST_FILE
REQUIRED = tuple(TEST_FILE + "::" + name for name in (
    "test_simple_repr", "test_maxsize", "test_maxsize_error_on_instance",
    "test_exceptions", "test_baseexception", "test_buggy_builtin_repr",
    "test_big_repr", "test_repr_on_newstyle", "test_unicode",
    "test_pformat_dispatch", "test_broken_getattribute"))
SOURCE_HASHES = {
    "src/_pytest/_code/code.py": "ab7fc5f6ca95cc0eb2be4baac41fd3e0ac2ab56ad9c5ecffe4796040cf944db5",
    "src/_pytest/python.py": "d86b91fd968ee9630e67047d6147757c73fd9eaca50479d2921e381158467f67",
    "src/_pytest/nodes.py": "a2bf4e47edaa8c5384eef9e555c95edc689fe5a3c3c73be797b266ad13bf4a45",
    "src/_pytest/reports.py": "ac53786a3208cc10cb4232382090dc55b0730976fdc380415759eeada01f3da5",
    "tox.ini": "1826cce79332a3b0f503d730516351c99616718a8e14f203a3af0447095f2356",
    "testing/conftest.py": "cdec2ef20a07011572da652e5aa08c74b1f518cd54c73824c4ac6753d4335d55",
    TEST_FILE: "15c33fbfe55c121caa7b9aa749c04fd0c772f91de9b8d6508e4bb9a290d0bf36",
}


def admit_task(task, control):
    if (task.get("instance_id") != TASK or task.get("repo") != "pytest-dev/pytest"
            or task.get("version") != "5.4" or task.get("base_commit") != BASE
            or tuple(task.get("FAIL_TO_PASS", ())) != REQUIRED or task.get("PASS_TO_PASS") != []
            or oracle.sha(task.get("patch", "").encode()) != GOLD_SHA
            or oracle.sha(task.get("test_patch", "").encode()) != TEST_PATCH_SHA):
        raise ValueError("task differs from reviewed pytest7168 source and obligations")
    old, new = control.get("old"), control.get("new")
    if (control.get("gold_sha256") != GOLD_SHA or not isinstance(old, str) or not old
            or not isinstance(new, str) or task["patch"].count(old) != 1
            or not control.get("rationale")):
        raise ValueError("wrong control is not reviewed for this exact gold")
    wrong = task["patch"].replace(old, new)
    if oracle.sha(wrong.encode()) != WRONG_SHA:
        raise ValueError("wrong control differs from reviewed patch")
    return wrong


def reviewed_spec(spec):
    """Copy the harness object and replace precisely one complete command."""
    commands = list(spec.eval_script_list)
    if commands.count(NATIVE) != 1 or sum(line.startswith("pytest ") for line in commands) != 1:
        raise ValueError("native harness command differs from reviewed full-file invocation")
    result = copy.copy(spec)
    commands[commands.index(NATIVE)] = REVIEWED
    result.eval_script_list = commands
    return result


def evaluation_script(spec, install):
    script = oracle.evaluation_script(spec, install, "pytest-dev/pytest")
    if script.splitlines().count(REVIEWED) != 1:
        raise ValueError("reviewed command absent or duplicated")
    # This fixed source check executes only inside the isolated target image,
    # after the harness has applied the unchanged protected test patch.
    code = ("import hashlib,json,pathlib,sys; "
            "expected=json.loads(" + repr(json.dumps(SOURCE_HASHES, sort_keys=True)) + "); "
            "actual={p:hashlib.sha256(pathlib.Path(p).read_bytes()).hexdigest() for p in expected}; "
            "actual==expected or sys.exit('pinned runner/protected source mismatch'); "
            "print('P07_REVIEWED_SOURCE_SHA256='+json.dumps(actual,sort_keys=True))")
    check = "python -c " + shlex.quote(code) + " || exit 91"
    return script.replace("\n" + REVIEWED + "\n", "\n" + check + "\n" + REVIEWED + "\n", 1)


def admit_image(info):
    if (info.get("Id") != IMAGE_ID or IMAGE not in info.get("RepoDigests", [])
            or info.get("Os") != "linux" or info.get("Architecture") != "amd64"):
        raise ValueError("immutable pytest7168 image mismatch")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("fresh output required; no implicit retry or report replacement")
    args.output.mkdir(parents=True)
    report = {"instance_id": TASK, "status": "infrastructure_error", "calibrated": False,
              "model_calls": 0, "provider_credentials": "none", "harness": oracle.HARNESS_VERSION,
              "command_profile": "pytest7168-full-file-short-traceback-v1",
              "test_command": REVIEWED, "base_commit": BASE, "image_digest": IMAGE,
              "source_hashes": SOURCE_HASHES, "requirement_ids": list(REQUIRED),
              "requirement_exception_extension": False}
    try:
        if importlib.metadata.version("swebench") != oracle.HARNESS_VERSION:
            raise ValueError("unreviewed external evaluator version")
        from swebench.harness.constants import MAP_REPO_VERSION_TO_SPECS
        from swebench.harness.log_parsers import MAP_REPO_TO_PARSER
        from swebench.harness.test_spec.test_spec import make_test_spec

        manifest = ROOT / "benchmarks/stage4/corpus-p07-v2.json"
        controls = ROOT / "benchmarks/stage4/p07-controls-v5.json"
        task = next(t for t in load_manifest(manifest)["tasks"] if t["instance_id"] == TASK)
        control = json.loads(controls.read_text(encoding="utf-8"))["tasks"][TASK]
        wrong = admit_task(task, control)
        native_spec = make_test_spec(task, namespace="swebench")
        spec = reviewed_spec(native_spec)
        install = MAP_REPO_VERSION_TO_SPECS[task["repo"]][task["version"]].get("install")
        script = evaluation_script(spec, install)
        (args.output / "eval.sh").write_bytes(script.encode())
        report.update(manifest_sha256=oracle.sha(manifest.read_bytes()),
                      controls_sha256=oracle.sha(controls.read_bytes()),
                      eval_script_sha256=oracle.sha(script.encode()),
                      negative_control_rationale=control["rationale"])
        oracle.command(["docker", "pull", IMAGE], timeout=600, log=args.output / "pull.log")
        info = json.loads(oracle.command(["docker", "image", "inspect", IMAGE]))[0]
        admit_image(info)
        report["image_id"] = info["Id"]
        rows = [oracle.grade(task, spec, IMAGE, patch, args.output, label,
                             MAP_REPO_TO_PARSER[task["repo"]], 240)
                for label, patch in (("base", ""), ("gold", task["patch"]), ("wrong", wrong))]
        report.update(rows=rows, calibrated=oracle.calibration_decision(rows), status="completed")
    except Exception as exc:
        report.update(error_type=type(exc).__name__, error=str(exc)[:500])
    (args.output / "calibration.json").write_bytes((json.dumps(report, indent=2) + "\n").encode())
    print(json.dumps({key: report[key] for key in ("instance_id", "status", "calibrated", "model_calls")}))
    return 0 if report["calibrated"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
